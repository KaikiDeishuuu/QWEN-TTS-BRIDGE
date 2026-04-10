import json
import asyncio
import logging
import time
import uuid
import threading
from collections import deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .auth import auth_dependency, validate_bearer_token
from .audio_utils import pcm_to_encoded
from .channel_routing import build_send_plan, convert_to_feishu_opus, log_tts_event, resolve_session_voice
from .config import Settings, load_settings
from .delivery_planner import DeliveryPlan, DeliveryPlanError, decide_audio_delivery, get_next_fallback, get_provider_registry
from .feishu_client import FeishuClient
from .qwen_client import QwenRealtimeTTSClient, QwenSynthesisConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tts_bridge")


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice_prompt: str | None = Field(default=None, max_length=1000)
    format: str = Field(default="wav")
    channel: str | None = Field(default=None, max_length=64)
    sender_type: str = Field(default="bot", max_length=16)
    voice_profile: str | None = Field(default=None, max_length=64)
    tts_engine: str = Field(default="qwen", max_length=32)


def media_type_for(fmt: str) -> str:
    mapping = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg"}
    if fmt in mapping:
        return mapping[fmt]
    raise ValueError(f"Unsupported format {fmt}")


def _estimate_duration_seconds(pcm_audio: bytes, sample_rate: int, channels: int = 1, sample_width: int = 2) -> float:
    frame_size = sample_width * channels
    if frame_size <= 0 or sample_rate <= 0:
        return 0.0
    return len(pcm_audio) / float(sample_rate * frame_size)


def _validate_encoded_audio(media_type: str, encoded_audio: bytes, *, min_audio_bytes: int = 512) -> tuple[bool, str]:
    if len(encoded_audio) < min_audio_bytes:
        return False, "audio_too_small"

    if media_type == "audio/ogg" and not encoded_audio.startswith(b"OggS"):
        return False, "invalid_ogg_header"
    if media_type == "audio/mpeg" and not (encoded_audio.startswith(b"ID3") or encoded_audio.startswith(b"\xff\xfb") or encoded_audio.startswith(b"\xff\xf3") or encoded_audio.startswith(b"\xff\xf2")):
        return False, "invalid_mp3_header"
    if media_type == "audio/wav" and not (encoded_audio.startswith(b"RIFF") and encoded_audio[8:12] == b"WAVE"):
        return False, "invalid_wav_header"

    return True, "ok"


def _fallback_response(reason: str, request_id: str, *, plan: DeliveryPlan | None = None, status_code: int = 200) -> Response:
    payload = {
        "fallback_to_text": True,
        "reason": reason,
        "reason_code": reason,
        "request_id": request_id,
    }
    if plan is not None:
        payload["resolved_type"] = plan.resolved_type
        payload["fallback_chain"] = list(plan.fallback_chain)
        payload["plan_reason_codes"] = list(plan.reason_codes)
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        headers={"X-Request-Id": request_id, "X-Reason-Code": reason},
        status_code=status_code,
    )


def _log_delivery_state(step: str, request_id: str, **fields: object) -> None:
    logger.info("tts_delivery_state", extra={"step": step, "request_id": request_id, **fields})


def _split_long_text(text: str, threshold: int) -> list[str]:
    if len(text) <= threshold:
        return [text]
    chunks: list[str] = []
    current = ""
    sentences = [s for s in __import__('re').split(r'(?<=[。！？!?；;])\s*', text) if s]
    for sentence in sentences:
        if not current:
            current = sentence
            continue
        if len(current) + len(sentence) <= threshold:
            current += sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks or [text]


async def _synthesize_with_plan(plan: DeliveryPlan, cfg: QwenSynthesisConfig, text: str, voice_prompt: str | None) -> bytes:
    registry = get_provider_registry()
    _log_delivery_state("provider_selected", plan.request_id, provider=plan.tts_provider, bridge_url=plan.bridge_url)
    try:
        if plan.tts_provider == "bridge":
            async with QwenRealtimeTTSClient(
                cfg,
                timeout_seconds=_SETTINGS.ws_timeout_seconds,
                handshake_retries=_SETTINGS.ws_handshake_retries,
            ) as client:
                pcm_audio = await client.synthesize(text, voice_prompt)
            registry.report_success("bridge")
            return pcm_audio
        if plan.tts_provider == "native":
            raise NotImplementedError("native_provider_not_implemented")
        raise RuntimeError("no_tts_provider")
    except TimeoutError as exc:
        if plan.tts_provider == "bridge" and len(text) > _SETTINGS.long_text_split_threshold:
            chunks = _split_long_text(text, _SETTINGS.long_text_split_threshold)
            if len(chunks) > 1:
                logger.warning(
                    "bridge_timeout_retrying_with_split_chunks",
                    extra={"request_id": plan.request_id, "chunks": len(chunks), "threshold": _SETTINGS.long_text_split_threshold},
                )
                combined: list[bytes] = []
                for idx, chunk in enumerate(chunks, start=1):
                    async with QwenRealtimeTTSClient(
                        cfg,
                        timeout_seconds=_SETTINGS.ws_timeout_seconds,
                        handshake_retries=_SETTINGS.ws_handshake_retries,
                    ) as client:
                        part = await client.synthesize(chunk, voice_prompt)
                    combined.append(part)
                    _log_delivery_state("split_chunk_synthesized", plan.request_id, chunk_index=idx, chunk_total=len(chunks), pcm_bytes=len(part))
                pcm_audio = b"".join(combined)
                registry.report_success("bridge")
                return pcm_audio
        if plan.tts_provider == "bridge":
            registry.report_failure("bridge", error_type=type(exc).__name__)
        raise
    except Exception as exc:
        if plan.tts_provider == "bridge":
            registry.report_failure("bridge", error_type=type(exc).__name__)
        raise


def _telegram_transport_for(plan: DeliveryPlan, duration_s: float | None, media_type: str) -> dict[str, object]:
    use_voice = plan.resolved_type == "voice_bubble"
    return build_send_plan(plan.channel, use_voice, duration_s=duration_s, mime_type=media_type)


_SETTINGS: Settings
_SEMAPHORE: asyncio.Semaphore

# Replay buffer for deterministic diagnostics
_REPLAY_LOCK = threading.RLock()
_REPLAY_ORDER: deque[str] = deque(maxlen=500)
_REPLAY_STORE: dict[str, dict[str, object]] = {}


def _record_replay(request_id: str, **fields: object) -> None:
    with _REPLAY_LOCK:
        if request_id not in _REPLAY_STORE:
            if len(_REPLAY_ORDER) >= _REPLAY_ORDER.maxlen:
                oldest = _REPLAY_ORDER.popleft()
                _REPLAY_STORE.pop(oldest, None)
            _REPLAY_ORDER.append(request_id)
            _REPLAY_STORE[request_id] = {}
        _REPLAY_STORE[request_id].update(fields)
        _REPLAY_STORE[request_id]["updated_at"] = time.time()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _SETTINGS, _SEMAPHORE
    _SETTINGS = load_settings()
    _SEMAPHORE = asyncio.Semaphore(_SETTINGS.max_concurrent_requests)

    registry = get_provider_registry()
    registry.clear_bridge()
    registry.register_bridge(url=f"http://{_SETTINGS.tts_host}:{_SETTINGS.tts_port}/tts", healthy=True)
    registry.set_native_allowed(allowed=_SETTINGS.enable_native_fallback)

    logger.info(
        "tts_bridge_started_deterministic_mode",
        extra={
            "model": _SETTINGS.tts_model,
            "max_concurrent": _SETTINGS.max_concurrent_requests,
            "provider_registry": registry.snapshot(),
        },
    )
    yield


app = FastAPI(title="OpenClaw TTS Bridge", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "provider_registry": get_provider_registry().snapshot()}


@app.get("/replay/{request_id}")
async def replay(request_id: str) -> Response:
    with _REPLAY_LOCK:
        payload = _REPLAY_STORE.get(request_id)
    if not payload:
        return Response(
            content=json.dumps({"error": "request_id_not_found", "request_id": request_id}),
            media_type="application/json",
            headers={"X-Request-Id": request_id, "X-Reason-Code": "request_id_not_found"},
            status_code=404,
        )
    body = {"request_id": request_id, **payload}
    return Response(
        content=json.dumps(body),
        media_type="application/json",
        headers={"X-Request-Id": request_id},
        status_code=200,
    )


@app.post("/tts")
async def tts(
    body: TTSRequest,
    authorization: str | None = Depends(auth_dependency),
) -> Response:
    request_id = str(uuid.uuid4())
    t_start = time.monotonic()
    validate_bearer_token(authorization, _SETTINGS.internal_tts_token)
    normalized_channel = (body.channel or "other").lower()
    normalized_sender = (body.sender_type or "bot").lower()
    _log_delivery_state("request_received", request_id, channel=normalized_channel, sender_type=normalized_sender)
    _record_replay(
        request_id,
        normalized_input={
            "channel": normalized_channel,
            "sender_type": normalized_sender,
            "text_length": len(body.text),
            "format": body.format,
            "tts_engine": body.tts_engine,
            "voice_profile": body.voice_profile,
        },
        terminal_status="in_progress",
    )

    try:
        plan = decide_audio_delivery(
            request_id=request_id,
            channel=normalized_channel,
            sender_type=normalized_sender,  # type: ignore[arg-type]
            text_length=len(body.text),
        )
        _log_delivery_state("policy_resolved", request_id, plan=plan.to_log_dict())
        _record_replay(request_id, plan=plan.to_log_dict())
    except DeliveryPlanError as exc:
        logger.error("delivery_planning_rejected", extra={"request_id": request_id, "error_type": type(exc).__name__, "reason": str(exc)})
        _record_replay(request_id, terminal_status="fallback_text", reason_code="capability_blocked")
        return _fallback_response("capability_blocked", request_id, status_code=status.HTTP_409_CONFLICT)
    except Exception as exc:
        logger.exception("delivery_planning_failed", extra={"request_id": request_id, "error_type": type(exc).__name__})
        _record_replay(request_id, terminal_status="fallback_text", reason_code="planning_failed")
        return _fallback_response("planning_failed", request_id)

    if plan.status == "fallback_text":
        _log_delivery_state("fallback", request_id, reason=plan.reason_codes[0], fallback_chain=plan.fallback_chain)
        _record_replay(request_id, terminal_status="fallback_text", reason_code=plan.reason_codes[0])
        return _fallback_response(plan.reason_codes[0], request_id, plan=plan)

    voice_info = resolve_session_voice(session_id=plan.channel, text=body.text, tts_engine=body.tts_engine)
    env_default_voice = (_SETTINGS.tts_voice or "").strip()
    final_voice = body.voice_profile or env_default_voice or voice_info["tts_voice_id"]
    cfg = QwenSynthesisConfig(
        ws_base=_SETTINGS.qwen_ws_base,
        model=_SETTINGS.tts_model,
        api_key=_SETTINGS.dashscope_api_key,
        voice=final_voice,
    )

    logger.info(
        "tts_request_received",
        extra={
            "request_id": request_id,
            "channel": plan.channel,
            "sender_type": plan.sender_type,
            "tts_provider": plan.tts_provider,
            "message_type_requested": plan.requested_type,
            "message_type_resolved": plan.resolved_type,
            "voice_profile": body.voice_profile or voice_info["voice_profile"],
            "reason_codes": plan.reason_codes,
        },
    )

    if _SEMAPHORE.locked():
        logger.warning("tts_concurrency_limit_reached", extra={"request_id": request_id})

    try:
        async with _SEMAPHORE:
            _log_delivery_state("synthesis_started", request_id, provider=plan.tts_provider)
            pcm_audio = await _synthesize_with_plan(plan, cfg, body.text, body.voice_prompt)
            duration_s = _estimate_duration_seconds(pcm_audio, cfg.sample_rate)
            _log_delivery_state("synthesis_result", request_id, pcm_bytes=len(pcm_audio), duration_s=duration_s)

            if len(pcm_audio) < _SETTINGS.min_pcm_bytes:
                logger.error("invalid_pcm_audio", extra={"request_id": request_id, "pcm_bytes": len(pcm_audio)})
                _record_replay(request_id, terminal_status="fallback_text", reason_code="invalid_audio")
                return _fallback_response("invalid_audio", request_id, plan=plan)

            encoded_audio = await pcm_to_encoded(
                pcm_audio,
                sample_rate=cfg.sample_rate,
                channels=1,
                output_format=plan.audio_format,
                ffmpeg_timeout=_SETTINGS.ffmpeg_timeout,
            )
            media_type = media_type_for(plan.audio_format)
            ok_audio, audio_reason = _validate_encoded_audio(media_type, encoded_audio)
            if not ok_audio:
                logger.error(
                    "invalid_encoded_audio",
                    extra={"request_id": request_id, "audio_bytes": len(encoded_audio), "media_type": media_type, "reason_code": audio_reason},
                )
                _record_replay(request_id, terminal_status="fallback_text", reason_code="invalid_audio")
                return _fallback_response("invalid_audio", request_id, plan=plan)

            latency_ms = int((time.monotonic() - t_start) * 1000)
            _log_delivery_state(
                "transport_selected",
                request_id,
                resolved_type=plan.resolved_type,
                audio_format=plan.audio_format,
                audio_bytes=len(encoded_audio),
                duration_s=duration_s,
            )

            if plan.channel == "feishu" and plan.resolved_type == "voice_bubble":
                if not _SETTINGS.feishu_app_id or not _SETTINGS.feishu_app_secret:
                    logger.error("feishu_credentials_missing", extra={"request_id": request_id})
                    _record_replay(request_id, terminal_status="fallback_text", reason_code="feishu_credentials_missing")
                    return _fallback_response("feishu_credentials_missing", request_id, plan=plan)
                try:
                    opus_path = convert_to_feishu_opus(encoded_audio, plan.audio_format)
                    fs_client = FeishuClient(_SETTINGS.feishu_app_id, _SETTINGS.feishu_app_secret, upload_timeout=_SETTINGS.feishu_upload_timeout)
                    file_key = await fs_client.upload_audio(str(opus_path))
                    _log_delivery_state("delivery_result", request_id, upstream_status="ok", file_key_prefix=file_key[:6])
                    _record_replay(request_id, terminal_status="success", reason_code="ok", transport_api="im/v1/files+im/v1/messages")
                    return Response(
                        content=f'{{"msg_type": "audio", "content": {{"file_key": "{file_key}"}}}}',
                        media_type="application/json",
                        headers={
                            "X-Request-Id": request_id,
                            "X-Sender-Type": plan.sender_type,
                            "X-TTS-Provider": plan.tts_provider,
                            "X-Resolved-Type": plan.resolved_type,
                            "X-Latency-Ms": str(latency_ms),
                        },
                    )
                except Exception as exc:
                    logger.exception("feishu_delivery_failed", extra={"request_id": request_id, "error_type": type(exc).__name__})
                    next_fallback = get_next_fallback(plan, plan.resolved_type)
                    if next_fallback == "text":
                        _record_replay(request_id, terminal_status="fallback_text", reason_code="feishu_upload_failed")
                        return _fallback_response("feishu_upload_failed", request_id, plan=plan)
                    raise

            telegram_send_plan = _telegram_transport_for(plan, duration_s, media_type)
            log_tts_event(request_id, plan.channel, {**plan.to_log_dict(), "transport": telegram_send_plan}, len(encoded_audio), latency_ms)
            _log_delivery_state("delivery_result", request_id, upstream_status="ok", transport=telegram_send_plan)
            _record_replay(
                request_id,
                terminal_status="success",
                reason_code="ok",
                transport_api=str(telegram_send_plan.get("transport_api", "")),
            )
            return Response(
                content=encoded_audio,
                media_type=media_type,
                headers={
                    "X-Request-Id": request_id,
                    "X-Sender-Type": plan.sender_type,
                    "X-TTS-Provider": plan.tts_provider,
                    "X-Resolved-Type": plan.resolved_type,
                    "X-Telegram-Transport": str(telegram_send_plan.get("transport_api", "")),
                    "X-Latency-Ms": str(latency_ms),
                },
            )

    except NotImplementedError as exc:
        logger.exception("tts_provider_not_implemented", extra={"request_id": request_id, "error_type": type(exc).__name__})
        _record_replay(request_id, terminal_status="fallback_text", reason_code=str(exc))
        return _fallback_response(str(exc), request_id, plan=plan)
    except Exception as exc:
        logger.exception("final_delivery_failed", extra={"request_id": request_id, "error_type": type(exc).__name__})
        _record_replay(request_id, terminal_status="fallback_text", reason_code="delivery_failed")
        return _fallback_response("delivery_failed", request_id, plan=plan)

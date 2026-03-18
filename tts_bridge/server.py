import asyncio
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .auth import auth_dependency, validate_bearer_token
from .audio_utils import SUPPORTED_FORMATS, pcm_to_encoded
from .channel_routing import (
    choose_bridge_format,
    convert_to_feishu_opus,
    resolve_session_voice,
    validate_bridge_response,
)
from .config import Settings, load_settings
from .feishu_client import FeishuClient
from .qwen_client import QwenRealtimeTTSClient, QwenSynthesisConfig


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("tts_bridge")


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice_prompt: str | None = Field(default=None, max_length=1000)
    format: str = Field(default="wav")
    channel: str | None = Field(default=None, max_length=64)
    audio_as_voice: bool | None = Field(default=None)
    ptt: bool | None = Field(default=None)
    voice_profile: str | None = Field(default=None, max_length=64)
    tts_engine: str = Field(default="qwen", max_length=32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def media_type_for(fmt: str) -> str:
    mapping = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg"}
    if fmt in mapping:
        return mapping[fmt]
    raise ValueError(f"Unsupported format {fmt}")


def _fallback_response(reason: str, request_id: str) -> Response:
    return Response(
        content=f'{{"fallback_to_text": true, "reason": "{reason}"}}',
        media_type="application/json",
        headers={"X-Request-Id": request_id},
    )


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

_SETTINGS: Settings
_SEMAPHORE: asyncio.Semaphore


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _SETTINGS, _SEMAPHORE
    _SETTINGS = load_settings()
    _SEMAPHORE = asyncio.Semaphore(_SETTINGS.max_concurrent_requests)
    logger.info(
        "TTS bridge started",
        extra={
            "model": _SETTINGS.tts_model,
            "host": _SETTINGS.tts_host,
            "port": _SETTINGS.tts_port,
            "max_concurrent": _SETTINGS.max_concurrent_requests,
        },
    )
    yield


app = FastAPI(title="OpenClaw TTS Bridge", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tts")
async def tts(
    body: TTSRequest,
    authorization: str | None = Depends(auth_dependency),
) -> Response:
    request_id = str(uuid.uuid4())
    t_start = time.monotonic()

    # --- Auth ---
    validate_bearer_token(authorization, _SETTINGS.internal_tts_token)

    channel = (body.channel or "unknown").lower()
    effective_format = choose_bridge_format(channel, body.format)

    if effective_format not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"format must be one of {sorted(SUPPORTED_FORMATS)}",
        )

    # --- Voice resolution ---
    voice_info = resolve_session_voice(
        session_id=channel,
        text=body.text,
        tts_engine=body.tts_engine,
    )
    env_default_voice = (_SETTINGS.tts_voice or "").strip()
    final_voice = body.voice_profile or env_default_voice or voice_info["tts_voice_id"]

    logger.info(
        "TTS request received",
        extra={
            "request_id": request_id,
            "channel": channel,
            "voice_profile": body.voice_profile or voice_info["voice_profile"],
            "tts_engine": body.tts_engine,
            "requested_format": body.format,
            "effective_format": effective_format,
            "text_len": len(body.text),
        },
    )

    cfg = QwenSynthesisConfig(
        ws_base=_SETTINGS.qwen_ws_base,
        model=_SETTINGS.tts_model,
        api_key=_SETTINGS.dashscope_api_key,
        voice=final_voice,
    )

    # --- Concurrency gate ---
    if _SEMAPHORE.locked():
        logger.warning("Concurrency limit reached, queuing request", extra={"request_id": request_id})

    try:
        async with _SEMAPHORE:
            # ── 1. Synthesize ────────────────────────────────────────────
            try:
                async with QwenRealtimeTTSClient(cfg, timeout_seconds=_SETTINGS.ws_timeout_seconds) as client:
                    pcm_audio = await client.synthesize(body.text, body.voice_prompt)
            except TimeoutError:
                logger.error("TTS synthesis timed out", extra={"request_id": request_id, "channel": channel})
                return _fallback_response("timeout", request_id)
            except Exception as exc:
                logger.exception("TTS synthesis failed", extra={"request_id": request_id, "channel": channel})
                return _fallback_response("synthesis_failed", request_id)

            # ── 2. Validate PCM output ───────────────────────────────────
            if len(pcm_audio) < _SETTINGS.min_pcm_bytes:
                logger.warning(
                    "PCM output too small, rejecting",
                    extra={"request_id": request_id, "pcm_bytes": len(pcm_audio), "min": _SETTINGS.min_pcm_bytes},
                )
                return _fallback_response("invalid_audio", request_id)

            is_valid, reason = validate_bridge_response("audio/wav", pcm_audio)
            if not is_valid:
                logger.warning(
                    "TTS validation failed",
                    extra={"request_id": request_id, "channel": channel, "reason": reason},
                )
                return _fallback_response("invalid_audio", request_id)

            # ── 3. Encode to target format ───────────────────────────────
            try:
                encoded_audio = await pcm_to_encoded(
                    pcm_audio,
                    sample_rate=cfg.sample_rate,
                    channels=1,
                    output_format=effective_format,
                    ffmpeg_timeout=_SETTINGS.ffmpeg_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("FFmpeg encoding timed out", extra={"request_id": request_id})
                return _fallback_response("encoding_timeout", request_id)
            except RuntimeError as exc:
                logger.error("FFmpeg encoding failed", extra={"request_id": request_id, "error": str(exc)})
                return _fallback_response("encoding_failed", request_id)

            output_size = len(encoded_audio)
            media_type = media_type_for(effective_format)
            latency_ms = int((time.monotonic() - t_start) * 1000)

            # ── 4. Feishu pipeline ───────────────────────────────────────
            if channel == "feishu":
                try:
                    opus_path = convert_to_feishu_opus(encoded_audio, effective_format)
                    fs_client = FeishuClient(
                        _SETTINGS.feishu_app_id,
                        _SETTINGS.feishu_app_secret,
                        upload_timeout=_SETTINGS.feishu_upload_timeout,
                    )
                    file_key = await fs_client.upload_audio(str(opus_path))

                    logger.info(
                        "Feishu voice bubble ready",
                        extra={
                            "request_id": request_id,
                            "channel": channel,
                            "output_format": "opus",
                            "audio_size": output_size,
                            "upload_status": "success",
                            "final_msg_type": "audio",
                            "latency_ms": latency_ms,
                        },
                    )
                    return Response(
                        content=f'{{"msg_type": "audio", "content": {{"file_key": "{file_key}"}}}}',
                        media_type="application/json",
                        headers={"X-Request-Id": request_id},
                    )
                except Exception as exc:
                    logger.error(
                        "Feishu pipeline failed",
                        extra={"request_id": request_id, "error": str(exc)},
                        exc_info=True,
                    )
                    return _fallback_response("feishu_upload_failed", request_id)

            # ── 5. Standard binary response (Telegram / other) ───────────
            logger.info(
                "TTS response ready",
                extra={
                    "request_id": request_id,
                    "channel": channel,
                    "content_type": media_type,
                    "effective_format": effective_format,
                    "audio_bytes": output_size,
                    "final_msg_type": "binary",
                    "latency_ms": latency_ms,
                },
            )
            return Response(
                content=encoded_audio,
                media_type=media_type,
                headers={"X-Request-Id": request_id},
            )

    except Exception as exc:
        logger.exception("Unhandled error in /tts", extra={"request_id": request_id})
        return _fallback_response("internal_error", request_id)

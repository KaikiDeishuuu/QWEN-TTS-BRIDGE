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
from .audio_utils import pcm_to_encoded
from .channel_routing import (
    convert_to_feishu_opus,
    resolve_session_voice,
)
from .config import Settings, load_settings
from .feishu_client import FeishuClient
from .qwen_client import QwenRealtimeTTSClient, QwenSynthesisConfig
from .delivery_planner import decide_audio_delivery, get_provider_registry

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
    sender_type: str = Field(default="bot", max_length=16) # "bot" | "user"
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
    
    # Initialize provider registry
    get_provider_registry().register_bridge(url="local", healthy=True)
    get_provider_registry().set_native_allowed(allowed=False) # Only bridge for now
    
    logger.info(
        "TTS bridge started deterministic mode",
        extra={
            "model": _SETTINGS.tts_model,
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

    # --- 1. Deterministic Planning ---
    try:
        # Cast sender_type to the expected Literal for the planner
        request_sender = body.sender_type.lower() if body.sender_type else "bot"
        
        plan = decide_audio_delivery(
            request_id=request_id,
            channel=body.channel or "other",
            sender_type=request_sender, # type: ignore
            text_length=len(body.text),
        )
    except Exception as exc:
        logger.error("Delivery planning failed", extra={"request_id": request_id, "error": str(exc)})
        return _fallback_response("planning_failed", request_id)

    if plan.status == "fallback_text":
        return _fallback_response(plan.reason_codes[0], request_id)

    # --- 2. Voice resolution ---
    voice_info = resolve_session_voice(
        session_id=plan.channel,
        text=body.text,
        tts_engine=body.tts_engine,
    )
    env_default_voice = (_SETTINGS.tts_voice or "").strip()
    final_voice = body.voice_profile or env_default_voice or voice_info["tts_voice_id"]

    logger.info(
        "TTS request received",
        extra={
            "request_id": request_id,
            "plan": plan.to_log_dict(),
            "voice_profile": body.voice_profile or voice_info["voice_profile"],
        },
    )

    cfg = QwenSynthesisConfig(
        ws_base=_SETTINGS.qwen_ws_base,
        model=_SETTINGS.tts_model,
        api_key=_SETTINGS.dashscope_api_key,
        voice=final_voice,
    )

    # --- 3. Concurrency gate ---
    if _SEMAPHORE.locked():
        logger.warning("Concurrency limit reached", extra={"request_id": request_id})

    try:
        async with _SEMAPHORE:
            # --- 4. Synthesize ---
            async with QwenRealtimeTTSClient(cfg, timeout_seconds=_SETTINGS.ws_timeout_seconds) as client:
                pcm_audio = await client.synthesize(body.text, body.voice_prompt)

            # --- 5. Validate & Encode ---
            if len(pcm_audio) < _SETTINGS.min_pcm_bytes:
                return _fallback_response("invalid_audio", request_id)

            encoded_audio = await pcm_to_encoded(
                pcm_audio,
                sample_rate=cfg.sample_rate,
                channels=1,
                output_format=plan.audio_format,
                ffmpeg_timeout=_SETTINGS.ffmpeg_timeout,
            )

            latency_ms = int((time.monotonic() - t_start) * 1000)

            # --- 6. Channel Specific Pipelines ---
            if plan.channel == "feishu" and plan.resolved_type == "voice_bubble":
                opus_path = convert_to_feishu_opus(encoded_audio, plan.audio_format)
                fs_client = FeishuClient(
                    _SETTINGS.feishu_app_id,
                    _SETTINGS.feishu_app_secret,
                )
                file_key = await fs_client.upload_audio(str(opus_path))

                return Response(
                    content=f'{{"msg_type": "audio", "content": {{"file_key": "{file_key}"}}}}',
                    media_type="application/json",
                    headers={"X-Request-Id": request_id},
                )

            # --- 7. Standard Binary Response ---
            return Response(
                content=encoded_audio,
                media_type=media_type_for(plan.audio_format),
                headers={
                    "X-Request-Id": request_id,
                    "X-Resolved-Type": plan.resolved_type,
                    "X-Latency-Ms": str(latency_ms),
                },
            )

    except Exception as exc:
        logger.exception("Final delivery failed", extra={"request_id": request_id})
        return _fallback_response("delivery_failed", request_id)

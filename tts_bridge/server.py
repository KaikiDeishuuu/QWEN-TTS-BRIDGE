import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .auth import auth_dependency, validate_bearer_token
from .audio_utils import SUPPORTED_FORMATS, pcm_to_encoded
from .config import Settings, load_settings
from .qwen_client import QwenRealtimeTTSClient, QwenSynthesisConfig


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("tts_bridge")


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice_prompt: str | None = Field(default=None, max_length=1000)
    format: str = Field(default="wav")
    channel: str | None = Field(default=None, max_length=64)
    audio_as_voice: bool | None = Field(default=None)
    ptt: bool | None = Field(default=None)
    voice_profile: str | None = Field(default=None, max_length=64)
    tts_engine: str = Field(default="qwen", max_length=32)


def media_type_for(fmt: str) -> str:
    if fmt == "wav":
        return "audio/wav"
    if fmt == "mp3":
        return "audio/mpeg"
    if fmt == "ogg":
        return "audio/ogg"
    raise ValueError(f"Unsupported format {fmt}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global SETTINGS
    SETTINGS = load_settings()
    logger.info("TTS bridge settings loaded", extra={"model": SETTINGS.tts_model})
    yield


app = FastAPI(title="OpenClaw TTS Bridge", lifespan=lifespan)
SETTINGS: Settings


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tts")
async def tts(
    body: TTSRequest,
    authorization: str | None = Depends(auth_dependency),
) -> Response:
    validate_bearer_token(authorization, SETTINGS.internal_tts_token)

    channel = (body.channel or "unknown").lower()
    requested_format = body.format.lower()
    effective_format = requested_format

    if channel == "telegram" and requested_format == "wav":
        logger.info(
            "Overriding default wav format to ogg for Telegram compatibility",
            extra={"channel": channel, "requested_format": requested_format, "effective_format": "ogg"},
        )
        effective_format = "ogg"

    if effective_format not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"format must be one of {sorted(SUPPORTED_FORMATS)}",
        )

    logger.info(
        "TTS request received",
        extra={
            "channel": channel,
            "voice_profile": body.voice_profile,
            "tts_engine": body.tts_engine,
            "tts_path": "qwen_bridge",
            "bridge_url": SETTINGS.qwen_ws_base,
            "bridge_auth_mode": "bearer",
            "requested_format": requested_format,
            "effective_format": effective_format,
            "voice_note_flag": bool(body.audio_as_voice) or bool(body.ptt),
            "voice_flags": {"audio_as_voice": body.audio_as_voice, "ptt": body.ptt},
        },
    )

    cfg = QwenSynthesisConfig(
        ws_base=SETTINGS.qwen_ws_base,
        model=SETTINGS.tts_model,
        api_key=SETTINGS.dashscope_api_key,
        voice=SETTINGS.tts_voice,
    )

    try:
        async with QwenRealtimeTTSClient(cfg, timeout_seconds=SETTINGS.ws_timeout_seconds) as client:
            pcm_audio = await client.synthesize(body.text, body.voice_prompt)

        encoded_audio = await pcm_to_encoded(
            pcm_audio,
            sample_rate=cfg.sample_rate,
            channels=1,
            output_format=effective_format,
        )
        media_type = media_type_for(effective_format)
        output_size = len(encoded_audio)

        logger.info(
            "TTS response ready",
            extra={
                "channel": channel,
                "voice_profile": body.voice_profile,
                "tts_engine": body.tts_engine,
                "tts_path": "qwen_bridge",
                "response_content_type": media_type,
                "requested_format": requested_format,
                "effective_format": effective_format,
                "audio_bytes": output_size,
                "voice_note_flag": bool(body.audio_as_voice) or bool(body.ptt),
                "voice_flags": {"audio_as_voice": body.audio_as_voice, "ptt": body.ptt},
            },
        )
        return Response(content=encoded_audio, media_type=media_type)
    except TimeoutError as exc:
        logger.exception("TTS timeout", extra={"channel": channel, "tts_path": "qwen_bridge"})
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("TTS synthesis failed", extra={"channel": channel, "tts_path": "qwen_bridge"})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="TTS synthesis failed") from exc

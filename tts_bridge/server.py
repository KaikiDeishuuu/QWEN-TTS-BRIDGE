import logging
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
    effective_format = choose_bridge_format(channel, body.format)

    if effective_format not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"format must be one of {sorted(SUPPORTED_FORMATS)}",
        )

    # Resolve voice profile (using channel as a simple session key for now)
    voice_info = resolve_session_voice(
        session_id=channel,
        text=body.text,
        tts_engine=body.tts_engine,
    )
    # Allow manual override if provided in request
    final_voice = body.voice_profile or voice_info["tts_voice_id"]

    logger.info(
        "TTS request received",
        extra={
            "channel": channel,
            "voice_profile": body.voice_profile or voice_info["voice_profile"],
            "tts_engine": body.tts_engine,
            "tts_path": "qwen_bridge",
            "bridge_url": SETTINGS.qwen_ws_base,
            "bridge_auth_mode": "bearer",
            "requested_format": body.format,
            "effective_format": effective_format,
            "voice_note_flag": bool(body.audio_as_voice) or bool(body.ptt),
            "voice_flags": {"audio_as_voice": body.audio_as_voice, "ptt": body.ptt},
        },
    )

    cfg = QwenSynthesisConfig(
        ws_base=SETTINGS.qwen_ws_base,
        model=SETTINGS.tts_model,
        api_key=SETTINGS.dashscope_api_key,
        voice=final_voice,
    )

    try:
        async with QwenRealtimeTTSClient(cfg, timeout_seconds=SETTINGS.ws_timeout_seconds) as client:
            pcm_audio = await client.synthesize(body.text, body.voice_prompt)

        # 1. Validate TTS output
        is_valid, reason = validate_bridge_response("audio/wav", pcm_audio)
        if not is_valid:
            logger.warning(
                "TTS validation failed, falling back to text",
                extra={"channel": channel, "reason": reason}
            )
            return Response(
                content='{"fallback_to_text": true, "reason": "invalid_audio"}',
                media_type="application/json"
            )

        # 2. Convert or Encode
        encoded_audio = await pcm_to_encoded(
            pcm_audio,
            sample_rate=cfg.sample_rate,
            channels=1,
            output_format=effective_format,
        )

        media_type = media_type_for(effective_format)
        output_size = len(encoded_audio)

        # 3. Handle Feishu Pipeline
        if channel == "feishu":
            try:
                # Convert to Opus specifically for Feishu bubble
                opus_path = convert_to_feishu_opus(encoded_audio, effective_format)
                
                # Upload to Feishu
                fs_client = FeishuClient(SETTINGS.feishu_app_id, SETTINGS.feishu_app_secret)
                file_key = await fs_client.upload_audio(str(opus_path))
                
                logger.info(
                    "Feishu voice bubble ready",
                    extra={
                        "channel": channel,
                        "tts_engine": body.tts_engine,
                        "input_format": body.format,
                        "output_format": "opus",
                        "audio_size": output_size,
                        "upload_status": "success",
                        "file_key_presence": True,
                        "file_key": file_key,
                        "final_msg_type": "audio"
                    }
                )
                return Response(
                    content=f'{{"msg_type": "audio", "content": {{"file_key": "{file_key}"}}}}',
                    media_type="application/json"
                )
            except Exception as e:
                logger.error(f"Feishu pipeline failed: {e}", exc_info=True)
                return Response(
                    content='{"fallback_to_text": true, "reason": "feishu_upload_failed"}',
                    media_type="application/json"
                )

        # 4. Standard binary response for other channels (like Telegram)
        output_size = len(encoded_audio)
        logger.info(
            "TTS response ready",
            extra={
                "channel": channel,
                "voice_profile": body.voice_profile or voice_info["voice_profile"],
                "tts_engine": body.tts_engine,
                "tts_path": "qwen_bridge",
                "response_content_type": media_type,
                "requested_format": body.format,
                "effective_format": effective_format,
                "audio_bytes": output_size,
                "upload_status": "not_required",
                "file_key_presence": False,
                "final_msg_type": "binary",
                "voice_note_flag": bool(body.audio_as_voice) or bool(body.ptt),
                "voice_flags": {"audio_as_voice": body.audio_as_voice, "ptt": body.ptt},
            },
        )
        return Response(content=encoded_audio, media_type=media_type)

    except TimeoutError as exc:
        logger.exception("TTS timeout", extra={"channel": channel, "tts_path": "qwen_bridge"})
        return Response(
            content='{"fallback_to_text": true, "reason": "timeout"}',
            media_type="application/json"
        )
    except Exception as exc:
        logger.exception("TTS synthesis failed", extra={"channel": channel, "tts_path": "qwen_bridge"})
        return Response(
            content='{"fallback_to_text": true, "reason": "synthesis_failed"}',
            media_type="application/json"
        )

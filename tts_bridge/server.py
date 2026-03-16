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

    output_format = body.format.lower()
    if output_format not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"format must be one of {sorted(SUPPORTED_FORMATS)}",
        )

    cfg = QwenSynthesisConfig(
        ws_base=SETTINGS.qwen_ws_base,
        model=SETTINGS.tts_model,
        api_key=SETTINGS.dashscope_api_key,
    )

    try:
        async with QwenRealtimeTTSClient(cfg, timeout_seconds=SETTINGS.ws_timeout_seconds) as client:
            pcm_audio = await client.synthesize(body.text, body.voice_prompt)

        encoded_audio = await pcm_to_encoded(
            pcm_audio,
            sample_rate=cfg.sample_rate,
            channels=1,
            output_format=output_format,
        )
        return Response(content=encoded_audio, media_type=media_type_for(output_format))
    except TimeoutError as exc:
        logger.exception("TTS timeout")
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("TTS synthesis failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"TTS synthesis failed: {exc}") from exc

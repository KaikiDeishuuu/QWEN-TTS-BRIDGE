import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    dashscope_api_key: str
    tts_model: str = "qwen3-tts-instruct-flash-realtime"
    tts_host: str = "127.0.0.1"
    tts_port: int = 5200
    tts_voice: str = "Maia"
    internal_tts_token: str = ""
    qwen_ws_base: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    ws_timeout_seconds: float = 45.0
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    max_concurrent_requests: int = 8
    feishu_upload_timeout: float = 20.0
    min_pcm_bytes: int = 4800
    ffmpeg_timeout: float = 30.0
    enable_native_fallback: bool = False


def _int_env(name: str, default: str) -> int:
    value = os.getenv(name, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer, got: {value!r}") from exc


def _float_env(name: str, default: str) -> float:
    value = os.getenv(name, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a number, got: {value!r}") from exc


def _bool_env(name: str, default: str = "false") -> bool:
    value = (os.getenv(name, default) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    load_dotenv(override=False)

    api_key = (os.getenv("DASHSCOPE_API_KEY") or "").strip()
    internal_token = (os.getenv("INTERNAL_TTS_TOKEN") or "").strip()

    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required")
    if not internal_token:
        raise RuntimeError("INTERNAL_TTS_TOKEN is required")

    host = os.getenv("TTS_HOST", os.getenv("HOST", "127.0.0.1"))
    port = os.getenv("TTS_PORT", os.getenv("PORT", "5200"))

    return Settings(
        dashscope_api_key=api_key,
        tts_model=os.getenv("TTS_MODEL", "qwen3-tts-instruct-flash-realtime"),
        tts_host=host,
        tts_port=_int_env("TTS_PORT", port),
        internal_tts_token=internal_token,
        tts_voice=os.getenv("TTS_VOICE", "Maia"),
        qwen_ws_base=os.getenv("QWEN_WS_BASE", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"),
        ws_timeout_seconds=_float_env("WS_TIMEOUT_SECONDS", "45"),
        feishu_app_id=(os.getenv("FEISHU_APP_ID") or "").strip(),
        feishu_app_secret=(os.getenv("FEISHU_APP_SECRET") or "").strip(),
        max_concurrent_requests=_int_env("MAX_CONCURRENT_REQUESTS", "8"),
        feishu_upload_timeout=_float_env("FEISHU_UPLOAD_TIMEOUT", "20"),
        min_pcm_bytes=_int_env("MIN_PCM_BYTES", "4800"),
        ffmpeg_timeout=_float_env("FFMPEG_TIMEOUT", "30"),
        enable_native_fallback=_bool_env("ENABLE_NATIVE_FALLBACK"),
    )

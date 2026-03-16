import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    dashscope_api_key: str
    tts_model: str = "qwen3-tts-instruct-flash-realtime"
    tts_host: str = "127.0.0.1"
    tts_port: int = 8000
    tts_voice: str = "Maia"
    internal_tts_token: str = ""
    qwen_ws_base: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    ws_timeout_seconds: float = 45.0
    feishu_app_id: str = ""
    feishu_app_secret: str = ""


def load_settings() -> Settings:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    internal_token = os.getenv("INTERNAL_TTS_TOKEN", "").strip()

    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required")
    if not internal_token:
        raise RuntimeError("INTERNAL_TTS_TOKEN is required")

    return Settings(
        dashscope_api_key=api_key,
        tts_model=os.getenv("TTS_MODEL", "qwen3-tts-instruct-flash-realtime"),
        tts_host=os.getenv("TTS_HOST", "0.0.0.0"),
        tts_port=int(os.getenv("TTS_PORT", "8000")),
        internal_tts_token=internal_token,
        tts_voice=os.getenv("TTS_VOICE", "Maia"),
        qwen_ws_base=os.getenv("QWEN_WS_BASE", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"),
        ws_timeout_seconds=float(os.getenv("WS_TIMEOUT_SECONDS", "45")),
    )

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .voice_personality import (
    choose_profile_for_text,
    extract_voice_override,
    looks_technical,
    session_voice_cache,
    voice_id_for_engine,
)

logger = logging.getLogger(__name__)

TTSMode = Literal["off", "always", "inbound", "tagged"]
Channel = Literal["telegram", "feishu", "other"]

EXPRESSIVE_PATTERNS = (
    "❤️",
    "💖",
    "💕",
    "😊",
    "😌",
    "🥰",
    "hug",
    "good morning",
    "good night",
    "miss you",
    "proud of you",
    "you got this",
    "love",
    "lyric",
    "hello",
    "hi",
    "hey",
)


@dataclass(frozen=True)
class VoiceDecisionConfig:
    mode: TTSMode = "tagged"
    max_chars: int = 160
    max_sentences: int = 2
    min_expressive_hits: int = 1


def _sentence_count(text: str) -> int:
    items = [x for x in re.split(r"[.!?。！？]+", text.strip()) if x.strip()]
    return max(len(items), 1 if text.strip() else 0)


def _count_matches(text: str, patterns: tuple[str, ...]) -> int:
    low = text.lower()
    return sum(1 for p in patterns if p in low)


def _is_clearly_expressive(text: str, min_hits: int) -> bool:
    return _count_matches(text, EXPRESSIVE_PATTERNS) >= min_hits


def parse_tts_overrides(text: str) -> dict[str, bool | str | None]:
    low = text.lower()
    return {
        "tts_override": "[[tts:" in low,
        "audio_as_voice": "[[audio_as_voice]]" in low,
        "voice_profile_override": extract_voice_override(text),
    }


def should_use_voice(
    text: str,
    channel: str,
    cfg: VoiceDecisionConfig,
    *,
    inbound_voice_hint: bool = False,
) -> tuple[bool, str]:
    overrides = parse_tts_overrides(text)
    if overrides["tts_override"]:
        return True, "explicit_tts_override"

    if cfg.mode == "off":
        return False, "mode_off"

    is_short_enough = len(text) <= cfg.max_chars and _sentence_count(text) <= cfg.max_sentences
    if not is_short_enough:
        return False, "too_long"

    if looks_technical(text):
        return False, "technical_or_structured"

    expressive = _is_clearly_expressive(text, cfg.min_expressive_hits)

    if cfg.mode == "always":
        return True, "mode_always"
    if cfg.mode == "inbound":
        ok = inbound_voice_hint and expressive
        return ok, "inbound_voice_hint" if ok else "not_inbound_or_not_expressive"

    if cfg.mode == "tagged":
        ok = expressive and channel in {"telegram", "feishu"}
        return ok, "expressive_short_proactive" if ok else "not_expressive"

    return False, "unsupported_mode"


def resolve_session_voice(
    session_id: str,
    text: str,
    *,
    tts_engine: str = "qwen",
) -> dict[str, str]:
    overrides = parse_tts_overrides(text)
    override_profile = overrides.get("voice_profile_override")

    current = session_voice_cache.get_profile_name(session_id)
    if override_profile:
        selected = str(override_profile)
        session_voice_cache.set_profile_name(session_id, selected)
        reason = "manual_override"
    elif current:
        selected = current
        reason = "session_locked"
    else:
        selected = choose_profile_for_text(text)
        session_voice_cache.set_profile_name(session_id, selected)
        reason = "first_selection"

    return {
        "voice_profile": selected,
        "tts_engine": tts_engine,
        "tts_voice_id": voice_id_for_engine(selected, tts_engine),
        "voice_profile_reason": reason,
    }


def choose_bridge_format(channel: str, requested_format: str | None = None) -> str:
    if requested_format:
        return requested_format.lower()
    if channel == "telegram":
        return "ogg"
    if channel == "feishu":
        return "ogg"
    return "wav"


def validate_bridge_response(content_type: str | None, body: bytes, min_bytes: int = 1024) -> tuple[bool, str]:
    if not body:
        return False, "empty_audio"
    ctype = (content_type or "").lower()
    if "application/json" in ctype:
        return False, "bridge_returned_json"
    if len(body) < min_bytes:
        return False, "audio_too_small"
    if ctype and not ctype.startswith("audio/"):
        return False, f"unexpected_content_type:{ctype}"
    return True, "ok"


def convert_to_feishu_opus(audio_bytes: bytes, input_format: str, output_dir: str | None = None) -> Path:
    target_dir = Path(output_dir or tempfile.mkdtemp(prefix="feishu_opus_"))
    target_dir.mkdir(parents=True, exist_ok=True)
    src = target_dir / f"source.{input_format}"
    out = target_dir / "voice.opus"
    src.write_bytes(audio_bytes)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-c:a",
        "libopus",
        "-b:a",
        "32k",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"feishu_opus_conversion_failed: {result.stderr.strip()}")
    logger.info(
        "feishu_audio_converted",
        extra={
            "conversion_result": "ok",
            "pre_conversion_format": input_format,
            "converted_output_path": str(out),
        },
    )
    return out


def masked_file_key(file_key: str) -> str:
    if len(file_key) <= 6:
        return "***"
    return f"{file_key[:3]}***{file_key[-3:]}"


def build_send_plan(channel: str, use_voice: bool) -> dict[str, object]:
    if not use_voice:
        return {"final_send_mode": "text"}

    if channel == "telegram":
        return {
            "final_send_mode": "telegram_voice_note",
            "asVoice": True,
            "ptt": True,
            "audio_as_voice": True,
            "voice_note_flag": True,
        }

    if channel == "feishu":
        return {
            "final_send_mode": "feishu_audio_bubble",
            "msg_type": "audio",
            "requires_file_upload": True,
            "disallow_generic_file": True,
        }

    return {"final_send_mode": "audio_attachment"}


def log_tts_decision(
    channel: str,
    decision: bool,
    reason: str,
    provider: str,
    fmt: str | None,
    size: int | None,
    *,
    voice_profile: str | None = None,
    tts_engine: str = "qwen",
    voice_flags: dict[str, bool] | None = None,
) -> None:
    logger.info(
        "tts_decision",
        extra={
            "channel": channel,
            "voice_profile": voice_profile,
            "tts_engine": tts_engine,
            "tts_decision": decision,
            "tts_reason": reason,
            "tts_provider": provider,
            "requested_format": fmt,
            "effective_format": fmt,
            "audio_bytes": size,
            "voice_flags": voice_flags or {},
        },
    )


def parse_bridge_error_body(body: bytes) -> str | None:
    try:
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict):
            return str(parsed.get("detail") or parsed.get("error") or "json_error")
    except Exception:
        return None
    return None

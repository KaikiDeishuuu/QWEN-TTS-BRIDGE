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


# --- Legacy Routing Logic Removed: Handled by delivery_planner.py ---

def log_tts_event(
    request_id: str,
    channel: str,
    plan: dict,
    audio_size: int | None = None,
    latency_ms: int | None = None,
) -> None:
    """Unified logger for the new DeliveryPlan architecture."""
    logger.info(
        "tts_delivery_event",
        extra={
            "request_id": request_id,
            "channel": channel,
            "plan": plan,
            "audio_bytes": audio_size,
            "latency_ms": latency_ms,
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

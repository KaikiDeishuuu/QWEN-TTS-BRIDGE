"""
delivery_planner.py — Central audio delivery decision layer.

ALL audio routing decisions flow through decide_audio_delivery().
Downstream code MUST consume the returned DeliveryPlan and MUST NOT
make independent routing decisions.

Design principles:
- Deterministic: given same inputs, always produces same plan.
- Fail-fast: unsupported configurations raise DeliveryPlanError.
- Observable: every plan carries a full audit trail (reason_codes).
- Explicit: no implicit defaults, no magic fallbacks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

from .channel_caps import (
    ChannelCapabilityError,
    MessageType,
    SenderType,
    assert_audio_allowed,
    get_capabilities,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

TTSProvider = Literal["bridge", "native", "none"]
DeliveryStatus = Literal["ok", "fallback_text", "blocked"]


class DeliveryPlanError(Exception):
    """Raised when no valid delivery plan can be constructed."""


# ---------------------------------------------------------------------------
# Core plan structure
# ---------------------------------------------------------------------------

@dataclass
class DeliveryPlan:
    """
    Immutable contract produced by decide_audio_delivery().
    Every downstream component reads from this; nothing overrides it.
    """
    request_id: str
    channel: str
    sender_type: SenderType          # "bot" | "user"
    tts_provider: TTSProvider        # "bridge" | "native" | "none"
    bridge_url: str | None           # only set when tts_provider == "bridge"

    # What we want vs what we'll actually send
    requested_type: MessageType
    resolved_type: MessageType       # may differ from requested due to caps

    # Delivery chain: ordered list of attempts before giving up
    # e.g. ["voice_bubble", "audio_file", "text"]
    fallback_chain: list[MessageType] = field(default_factory=list)

    # Audio encoding
    audio_format: str = "ogg"         # as required by channel caps
    require_opus: bool = True

    # Observability
    reason_codes: list[str] = field(default_factory=list)
    status: DeliveryStatus = "ok"     # "ok" | "fallback_text" | "blocked"

    def is_audio(self) -> bool:
        return self.resolved_type in ("voice_bubble", "audio_file")

    def to_log_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "channel": self.channel,
            "sender_type": self.sender_type,
            "tts_provider": self.tts_provider,
            "requested_type": self.requested_type,
            "resolved_type": self.resolved_type,
            "fallback_chain": self.fallback_chain,
            "audio_format": self.audio_format,
            "require_opus": self.require_opus,
            "status": self.status,
            "reason_codes": self.reason_codes,
        }


# ---------------------------------------------------------------------------
# Provider registry state
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """
    Tracks TTS provider availability with a stateful circuit breaker.

    States:
    - CLOSED: Bridge is healthy, all requests go to bridge.
    - OPEN: Bridge is failing, all requests go to native fallback.
    - HALF_OPEN: Testing if bridge has recovered (1 request allowed).
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout_s: float = 60.0) -> None:
        self._bridge_url: str | None = None
        self._native_allowed: bool = False
        
        # Circuit breaker state
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._last_failure_time: float = 0.0
        self._is_open = False

    def register_bridge(self, url: str, healthy: bool = True) -> None:
        self._bridge_url = url
        if healthy:
            self.report_success()
        else:
            self.report_failure()

    def set_native_allowed(self, allowed: bool) -> None:
        self._native_allowed = allowed

    def report_success(self) -> None:
        if self._is_open:
            logger.info("Circuit breaker CLOSED (Bridge recovered)")
        self._failure_count = 0
        self._is_open = False

    def report_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold and not self._is_open:
            self._is_open = True
            logger.error(
                "Circuit breaker OPENED (Bridge failing)",
                extra={"failure_count": self._failure_count, "url": self._bridge_url}
            )

    def select_provider(self) -> tuple[TTSProvider, str | None]:
        """
        Returns (provider, bridge_url).
        Logic:
        1. If CLOSED -> Bridge.
        2. If OPEN:
           - If recovery_timeout passed -> HALF_OPEN -> Bridge (one probe).
           - Otherwise -> Native.
        """
        now = time.monotonic()
        
        # Check for recovery (Half-Open probe)
        if self._is_open and (now - self._last_failure_time) > self._recovery_timeout_s:
            logger.info("Circuit breaker HALF-OPEN (Probing bridge recovery)")
            return "bridge", self._bridge_url

        if not self._is_open and self._bridge_url:
            return "bridge", self._bridge_url

        if self._native_allowed:
            return "native", None
            
        return "none", None


# Singleton registry — initialized at app startup
_provider_registry = ProviderRegistry()


def get_provider_registry() -> ProviderRegistry:
    return _provider_registry


# ---------------------------------------------------------------------------
# Decision function
# ---------------------------------------------------------------------------

def decide_audio_delivery(
    *,
    request_id: str,
    channel: str,
    sender_type: SenderType,
    text_length: int,
    audio_duration_s: float | None = None,
    registry: ProviderRegistry | None = None,
) -> DeliveryPlan:
    """
    Single entry-point for all audio delivery decisions.

    Raises DeliveryPlanError ONLY for hard blocks (e.g. USER audio on Feishu).
    For soft failures (bridge down, unsupported type), returns a plan with
    status="fallback_text" and the reason documented in reason_codes.

    Args:
        request_id: Unique ID for this request (for log correlation).
        channel: "feishu" | "telegram" | "other".
        sender_type: "bot" | "user".
        text_length: Characters in the source text (used for TG voice vs audio).
        audio_duration_s: Known duration if available (for Telegram routing).
        registry: ProviderRegistry to use; defaults to global singleton.
    """
    reg = registry or _provider_registry
    caps = get_capabilities(channel)
    reason_codes: list[str] = []

    # ── Step 1: Hard-block unsupported sender/channel combos ─────────────
    try:
        assert_audio_allowed(channel, sender_type)
    except ChannelCapabilityError as exc:
        # This is a programming error on the caller's side — raise immediately
        logger.error(
            "Audio delivery hard-blocked",
            extra={"request_id": request_id, "channel": channel, "sender": sender_type, "reason": str(exc)},
        )
        raise DeliveryPlanError(str(exc)) from exc

    # ── Step 2: Select TTS provider ──────────────────────────────────────
    provider, bridge_url = reg.select_provider()
    if provider == "none":
        reason_codes.append("no_tts_provider")
        return DeliveryPlan(
            request_id=request_id,
            channel=channel,
            sender_type=sender_type,
            tts_provider="none",
            bridge_url=None,
            requested_type="voice_bubble",
            resolved_type="text",
            fallback_chain=["text"],
            status="fallback_text",
            reason_codes=reason_codes,
        )

    if provider == "native":
        reason_codes.append("bridge_unhealthy_native_fallback")

    # ── Step 3: Resolve message type based on channel capabilities ────────
    requested_type: MessageType = "voice_bubble"
    resolved_type: MessageType
    fallback_chain: list[MessageType]

    if channel == "feishu":
        # Feishu: always voice_bubble via file_key; no audio_file fallback
        if caps.supports_voice_bubble:
            resolved_type = "voice_bubble"
            fallback_chain = ["voice_bubble", "text"]
        else:
            reason_codes.append("feishu_no_voice_bubble")
            resolved_type = "text"
            fallback_chain = ["text"]

    elif channel == "telegram":
        # Telegram: voice_bubble for short audio, audio_file for longer content
        if audio_duration_s is not None and audio_duration_s > 300:
            # > 5 min → use sendAudio
            requested_type = "audio_file"
            resolved_type = "audio_file"
            reason_codes.append("tg_long_audio_use_audio_file")
        elif text_length > 800:
            # Very long text → unlikely to be a voice bubble usecase
            requested_type = "audio_file"
            resolved_type = "audio_file"
            reason_codes.append("tg_long_text_use_audio_file")
        else:
            resolved_type = "voice_bubble"
        fallback_chain = ["voice_bubble", "audio_file", "document", "text"]

    else:
        # Generic channel
        if caps.supports_audio_file:
            resolved_type = "audio_file"
            fallback_chain = ["audio_file", "text"]
        else:
            resolved_type = "text"
            fallback_chain = ["text"]
            reason_codes.append("channel_no_audio_support")

    # ── Step 4: Determine audio format ────────────────────────────────────
    audio_format = caps.preferred_format
    require_opus = caps.audio_must_be_opus

    plan = DeliveryPlan(
        request_id=request_id,
        channel=channel,
        sender_type=sender_type,
        tts_provider=provider,
        bridge_url=bridge_url,
        requested_type=requested_type,
        resolved_type=resolved_type,
        fallback_chain=fallback_chain,
        audio_format=audio_format,
        require_opus=require_opus,
        reason_codes=reason_codes,
        status="ok",
    )

    logger.info("DeliveryPlan constructed", extra=plan.to_log_dict())
    return plan


# ---------------------------------------------------------------------------
# Execution helpers (called by OpenClaw after obtaining a plan)
# ---------------------------------------------------------------------------

def get_next_fallback(plan: DeliveryPlan, failed_type: MessageType) -> MessageType | None:
    """
    Returns the next message type to attempt after *failed_type* fails.
    Returns None if the chain is exhausted (should result in silent log + give up).

    Usage:
        next_type = get_next_fallback(plan, "voice_bubble")
        if next_type is None:
            log_and_give_up()
    """
    try:
        idx = plan.fallback_chain.index(failed_type)
        next_type = plan.fallback_chain[idx + 1]
        logger.warning(
            "Audio delivery fallback triggered",
            extra={
                "request_id": plan.request_id,
                "failed_type": failed_type,
                "next_type": next_type,
                "channel": plan.channel,
            },
        )
        return next_type
    except (ValueError, IndexError):
        logger.error(
            "Fallback chain exhausted — no more delivery options",
            extra={
                "request_id": plan.request_id,
                "failed_type": failed_type,
                "channel": plan.channel,
                "chain": plan.fallback_chain,
            },
        )
        return None

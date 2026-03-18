"""
delivery_planner.py — Central audio delivery decision layer.

ALL audio routing decisions flow through decide_audio_delivery().
Downstream code MUST consume the returned DeliveryPlan and MUST NOT
make independent routing decisions.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .channel_caps import (
    ChannelCapabilityError,
    MessageType,
    SenderType,
    assert_audio_allowed,
    assert_message_type_allowed,
    get_capabilities,
)

logger = logging.getLogger(__name__)

TTSProvider = Literal["bridge", "native", "none"]
DeliveryStatus = Literal["ok", "fallback_text", "blocked"]


class DeliveryPlanError(Exception):
    """Raised when no valid delivery plan can be constructed."""


@dataclass(frozen=True)
class DeliveryPlan:
    request_id: str
    channel: str
    sender_type: SenderType
    requested_type: MessageType
    resolved_type: MessageType
    tts_provider: TTSProvider
    bridge_url: str | None
    fallback_chain: tuple[MessageType, ...] = field(default_factory=tuple)
    constraints: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    audio_format: str = "ogg"
    require_opus: bool = True
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    status: DeliveryStatus = "ok"

    def __post_init__(self) -> None:
        object.__setattr__(self, "fallback_chain", tuple(self.fallback_chain))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        if not isinstance(self.constraints, MappingProxyType):
            object.__setattr__(self, "constraints", MappingProxyType(dict(self.constraints)))

    @property
    def debug_reason_codes(self) -> list[str]:
        return list(self.reason_codes)

    def is_audio(self) -> bool:
        return self.resolved_type in ("voice_bubble", "audio_file", "document")

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "channel": self.channel,
            "sender_type": self.sender_type,
            "requested_type": self.requested_type,
            "resolved_type": self.resolved_type,
            "tts_provider": self.tts_provider,
            "bridge_url": self.bridge_url,
            "fallback_chain": list(self.fallback_chain),
            "constraints": dict(self.constraints),
            "audio_format": self.audio_format,
            "require_opus": self.require_opus,
            "reason_codes": list(self.reason_codes),
            "status": self.status,
        }


class ProviderRegistry:
    """Strongly-registered provider selector with bridge-first priority."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout_s: float = 60.0) -> None:
        self._bridge_url: str | None = None
        self._bridge_registered = False
        self._native_allowed = False
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._last_failure_time = 0.0
        self._circuit_state = "closed"
        self._lock = threading.RLock()

    def register_bridge(self, url: str, healthy: bool = True) -> None:
        with self._lock:
            self._bridge_url = url
            self._bridge_registered = True
        if healthy:
            self.report_success("bridge")
        else:
            self.report_failure("bridge", error_type="registration_unhealthy")

    def clear_bridge(self) -> None:
        with self._lock:
            self._bridge_url = None
            self._bridge_registered = False
            self._failure_count = 0
            self._last_failure_time = 0.0
            self._circuit_state = "closed"

    def set_native_allowed(self, allowed: bool) -> None:
        with self._lock:
            self._native_allowed = allowed

    def report_success(self, provider: TTSProvider) -> None:
        if provider != "bridge":
            return
        with self._lock:
            if self._circuit_state != "closed":
                logger.info("bridge_circuit_closed", extra={"provider": provider, "bridge_url": self._bridge_url})
            self._failure_count = 0
            self._circuit_state = "closed"

    def report_failure(self, provider: TTSProvider, *, error_type: str) -> None:
        if provider != "bridge":
            return
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._failure_threshold:
                self._circuit_state = "open"
            logger.error(
                "bridge_provider_failure",
                extra={
                    "provider": provider,
                    "bridge_url": self._bridge_url,
                    "error_type": error_type,
                    "failure_count": self._failure_count,
                    "circuit_state": self._circuit_state,
                },
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "bridge_registered": self._bridge_registered,
                "bridge_url": self._bridge_url,
                "native_allowed": self._native_allowed,
                "failure_count": self._failure_count,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout_s": self._recovery_timeout_s,
                "circuit_state": self._current_circuit_state_unlocked(),
            }

    def _current_circuit_state(self) -> str:
        with self._lock:
            return self._current_circuit_state_unlocked()

    def _current_circuit_state_unlocked(self) -> str:
        if self._circuit_state != "open":
            return self._circuit_state
        if (time.monotonic() - self._last_failure_time) > self._recovery_timeout_s:
            return "half_open"
        return "open"

    def select_provider(self) -> tuple[TTSProvider, str | None, list[str]]:
        reason_codes: list[str] = []
        with self._lock:
            circuit_state = self._current_circuit_state_unlocked()
            bridge_registered = self._bridge_registered
            bridge_url = self._bridge_url
            native_allowed = self._native_allowed

        if bridge_registered and bridge_url:
            if circuit_state in {"closed", "half_open"}:
                if circuit_state == "half_open":
                    reason_codes.append("bridge_half_open_probe")
                return "bridge", bridge_url, reason_codes
            reason_codes.append("bridge_circuit_open")
        else:
            reason_codes.append("bridge_not_registered")

        if native_allowed:
            reason_codes.append("bridge_unhealthy_native_fallback")
            return "native", None, reason_codes

        reason_codes.append("no_tts_provider")
        return "none", None, reason_codes


_provider_registry = ProviderRegistry()


def get_provider_registry() -> ProviderRegistry:
    return _provider_registry


def decide_audio_delivery(
    *,
    request_id: str,
    channel: str,
    sender_type: SenderType,
    text_length: int,
    audio_duration_s: float | None = None,
    content_kind: Literal["speech", "music", "unknown"] = "speech",
    registry: ProviderRegistry | None = None,
) -> DeliveryPlan:
    reg = registry or _provider_registry
    normalized_channel = (channel or "other").lower()
    caps = get_capabilities(normalized_channel)
    reason_codes: list[str] = []

    try:
        assert_audio_allowed(normalized_channel, sender_type)
    except ChannelCapabilityError as exc:
        logger.error(
            "audio_delivery_hard_blocked",
            extra={
                "request_id": request_id,
                "channel": normalized_channel,
                "sender_type": sender_type,
                "error_type": "channel_capability_error",
                "reason": str(exc),
            },
        )
        raise DeliveryPlanError(str(exc)) from exc

    provider, bridge_url, provider_reasons = reg.select_provider()
    reason_codes.extend(provider_reasons)
    if provider == "none":
        return DeliveryPlan(
            request_id=request_id,
            channel=normalized_channel,
            sender_type=sender_type,
            requested_type="voice_bubble",
            resolved_type="text",
            tts_provider="none",
            bridge_url=None,
            fallback_chain=("text",),
            constraints={"channel_notes": caps.notes},
            audio_format=caps.preferred_format,
            require_opus=caps.audio_must_be_opus,
            reason_codes=reason_codes,
            status="fallback_text",
        )

    requested_type: MessageType = "voice_bubble"
    resolved_type: MessageType = "voice_bubble"
    fallback_chain: tuple[MessageType, ...]

    if normalized_channel == "feishu":
        requested_type = "voice_bubble"
        resolved_type = "voice_bubble"
        fallback_chain = ("voice_bubble", "text")
        reason_codes.append("feishu_bot_audio_only")
    elif normalized_channel == "telegram":
        if content_kind == "music":
            requested_type = "audio_file"
            resolved_type = "audio_file"
            reason_codes.append("tg_music_use_audio_file")
        elif audio_duration_s is not None and audio_duration_s > 300:
            requested_type = "audio_file"
            resolved_type = "audio_file"
            reason_codes.append("tg_long_audio_use_audio_file")
        elif text_length > 800:
            requested_type = "audio_file"
            resolved_type = "audio_file"
            reason_codes.append("tg_long_text_use_audio_file")
        else:
            requested_type = "voice_bubble"
            resolved_type = "voice_bubble"
            reason_codes.append("tg_short_audio_use_voice_bubble")
        fallback_chain = ("voice_bubble", "audio_file", "document", "text")
    else:
        requested_type = "audio_file"
        resolved_type = "audio_file" if caps.supports_audio_file else "text"
        fallback_chain = ("audio_file", "text") if resolved_type == "audio_file" else ("text",)
        if resolved_type == "text":
            reason_codes.append("channel_no_audio_support")

    if resolved_type != "text":
        assert_message_type_allowed(normalized_channel, resolved_type)

    constraints = {
        "supports_bot_audio": caps.supports_bot_audio,
        "supports_user_audio": caps.supports_user_audio,
        "supports_voice_bubble": caps.supports_voice_bubble,
        "supports_audio_file": caps.supports_audio_file,
        "supports_document": caps.supports_document,
        "channel_notes": caps.notes,
        "content_kind": content_kind,
        "telegram_voice_max_duration_s": 300,
        "telegram_voice_text_threshold": 800,
    }

    plan = DeliveryPlan(
        request_id=request_id,
        channel=normalized_channel,
        sender_type=sender_type,
        requested_type=requested_type,
        resolved_type=resolved_type,
        tts_provider=provider,
        bridge_url=bridge_url,
        fallback_chain=fallback_chain,
        constraints=constraints,
        audio_format=caps.preferred_format,
        require_opus=caps.audio_must_be_opus,
        reason_codes=reason_codes,
        status="ok",
    )
    logger.info("delivery_plan_constructed", extra=plan.to_log_dict())
    return plan


def get_next_fallback(plan: DeliveryPlan, failed_type: MessageType) -> MessageType | None:
    try:
        idx = plan.fallback_chain.index(failed_type)
        next_type = plan.fallback_chain[idx + 1]
        logger.warning(
            "audio_delivery_fallback_triggered",
            extra={
                "request_id": plan.request_id,
                "channel": plan.channel,
                "failed_type": failed_type,
                "next_type": next_type,
                "fallback_chain": list(plan.fallback_chain),
            },
        )
        return next_type
    except (ValueError, IndexError):
        logger.error(
            "audio_delivery_fallback_exhausted",
            extra={
                "request_id": plan.request_id,
                "channel": plan.channel,
                "failed_type": failed_type,
                "fallback_chain": list(plan.fallback_chain),
            },
        )
        return None

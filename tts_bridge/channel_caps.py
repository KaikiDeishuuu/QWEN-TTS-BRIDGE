"""
channel_caps.py — Explicit channel capability matrix.

This module implements the strict contract for what each channel
can and cannot do. All delivery logic MUST consult this before
attempting to send audio. Fail-fast semantics: unsupported
operations raise ChannelCapabilityError immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SenderType = Literal["bot", "user"]
MessageType = Literal["voice_bubble", "audio_file", "document", "text"]


class ChannelCapabilityError(Exception):
    """Raised when the requested delivery is unsupported for the channel/sender combination."""


@dataclass(frozen=True)
class ChannelCapabilities:
    channel: str
    # Which sender can send audio at all
    bot_can_send_audio: bool
    user_can_send_audio: bool
    # Supported message types (for BOT sender)
    supports_voice_bubble: bool   # native inline waveform (Feishu audio / TG sendVoice)
    supports_audio_file: bool     # generic downloadable audio
    supports_document: bool       # arbitrary file attachment
    # Constraints
    audio_must_be_opus: bool      # Feishu and TG voice bubbles require Opus
    preferred_format: str         # "ogg" | "mp3" | "wav"
    notes: str = ""


# ---------------------------------------------------------------------------
# Capability registry — the single source of truth
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ChannelCapabilities] = {
    "feishu": ChannelCapabilities(
        channel="feishu",
        bot_can_send_audio=True,
        user_can_send_audio=False,   # USER cannot send audio — HARD BLOCK
        supports_voice_bubble=True,  # msg_type="audio" with file_key
        supports_audio_file=False,   # Do NOT fall back to generic file on Feishu
        supports_document=True,
        audio_must_be_opus=True,
        preferred_format="ogg",
        notes="Feishu audio requires Opus upload via im/v1/files; USER sender NEVER allowed.",
    ),
    "telegram": ChannelCapabilities(
        channel="telegram",
        bot_can_send_audio=True,
        user_can_send_audio=True,   # Userbot mode supported but not recommended
        supports_voice_bubble=True,  # sendVoice (OGG/Opus, short clips)
        supports_audio_file=True,    # sendAudio (longer / music)
        supports_document=True,
        audio_must_be_opus=True,     # sendVoice requires OGG/Opus
        preferred_format="ogg",
        notes="sendVoice for short clips < 5 min; sendAudio for others.",
    ),
    "other": ChannelCapabilities(
        channel="other",
        bot_can_send_audio=True,
        user_can_send_audio=True,
        supports_voice_bubble=False,
        supports_audio_file=True,
        supports_document=True,
        audio_must_be_opus=False,
        preferred_format="wav",
        notes="Generic channel; no voice-bubble support.",
    ),
}

_DEFAULT_CAPS = _REGISTRY["other"]


def get_capabilities(channel: str) -> ChannelCapabilities:
    """Return capabilities for the given channel. Falls back to 'other'."""
    return _REGISTRY.get(channel.lower(), _DEFAULT_CAPS)


def assert_audio_allowed(channel: str, sender: SenderType) -> None:
    """Raise ChannelCapabilityError if sender cannot send audio on this channel.

    This is a HARD BLOCK — must be called before any synthesis attempt.
    """
    caps = get_capabilities(channel)
    if sender == "bot" and not caps.bot_can_send_audio:
        raise ChannelCapabilityError(
            f"BOT cannot send audio on channel '{channel}'"
        )
    if sender == "user" and not caps.user_can_send_audio:
        raise ChannelCapabilityError(
            f"USER sender is FORBIDDEN from sending audio on channel '{channel}'. "
            f"Audio MUST be sent via BOT. {caps.notes}"
        )

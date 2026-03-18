"""
channel_caps.py — Explicit channel capability matrix.

This module is the hard contract for audio delivery. Every audio request
MUST validate channel + sender capabilities here before synthesis starts.
No downstream caller is allowed to assume BOT/USER interchangeability.
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
    # Supported message types
    supports_voice_bubble: bool
    supports_audio_file: bool
    supports_document: bool
    # Constraints
    audio_must_be_opus: bool
    preferred_format: str
    notes: str = ""

    @property
    def supports_bot_audio(self) -> bool:
        return self.bot_can_send_audio

    @property
    def supports_user_audio(self) -> bool:
        return self.user_can_send_audio

    def supports_message_type(self, message_type: MessageType) -> bool:
        return {
            "voice_bubble": self.supports_voice_bubble,
            "audio_file": self.supports_audio_file,
            "document": self.supports_document,
            "text": True,
        }[message_type]


_REGISTRY: dict[str, ChannelCapabilities] = {
    "feishu": ChannelCapabilities(
        channel="feishu",
        bot_can_send_audio=True,
        user_can_send_audio=False,
        supports_voice_bubble=True,
        supports_audio_file=False,
        supports_document=False,
        audio_must_be_opus=True,
        preferred_format="ogg",
        notes="Feishu audio requires BOT sender plus uploaded Opus file_key envelope.",
    ),
    "telegram": ChannelCapabilities(
        channel="telegram",
        bot_can_send_audio=True,
        user_can_send_audio=True,
        supports_voice_bubble=True,
        supports_audio_file=True,
        supports_document=True,
        audio_must_be_opus=True,
        preferred_format="ogg",
        notes="Telegram voice uses sendVoice; long-form audio should use sendAudio.",
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
        notes="Generic channel with file-only audio transport.",
    ),
}
_DEFAULT_CAPS = _REGISTRY["other"]


def get_capabilities(channel: str) -> ChannelCapabilities:
    return _REGISTRY.get((channel or "other").lower(), _DEFAULT_CAPS)


def assert_audio_allowed(channel: str, sender: SenderType) -> None:
    caps = get_capabilities(channel)
    if sender == "bot" and not caps.bot_can_send_audio:
        raise ChannelCapabilityError(f"BOT cannot send audio on channel '{channel}'")
    if sender == "user" and not caps.user_can_send_audio:
        raise ChannelCapabilityError(
            f"USER sender is FORBIDDEN from sending audio on channel '{channel}'. "
            f"Audio MUST be sent via BOT. {caps.notes}"
        )


def assert_message_type_allowed(channel: str, message_type: MessageType) -> None:
    caps = get_capabilities(channel)
    if not caps.supports_message_type(message_type):
        raise ChannelCapabilityError(
            f"Message type '{message_type}' is unsupported on channel '{channel}'. {caps.notes}"
        )

import re
from dataclasses import dataclass
from typing import Literal

Engine = Literal["edge", "qwen"]


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    edge_voice_id: str
    qwen_voice_id: str
    speaking_style: str
    pitch: str
    speed: str
    engine_compatibility: tuple[Engine, ...] = ("edge", "qwen")


VOICE_PROFILES: dict[str, VoiceProfile] = {
    "companion": VoiceProfile(
        name="companion",
        edge_voice_id="zh-CN-XiaoxiaoNeural",
        qwen_voice_id="Chelsie",
        speaking_style="warm, gentle",
        pitch="medium-low",
        speed="slow",
    ),
    "playful": VoiceProfile(
        name="playful",
        edge_voice_id="zh-CN-YunxiNeural",
        qwen_voice_id="Ethan",
        speaking_style="cheerful, lively",
        pitch="medium-high",
        speed="medium-fast",
    ),
    "professional": VoiceProfile(
        name="professional",
        edge_voice_id="zh-CN-YunyangNeural",
        qwen_voice_id="Serena",
        speaking_style="calm, technical",
        pitch="medium",
        speed="medium",
    ),
    "neutral": VoiceProfile(
        name="neutral",
        edge_voice_id="zh-CN-XiaoxiaoNeural",
        qwen_voice_id="Cherry",
        speaking_style="neutral informative",
        pitch="medium",
        speed="medium",
    ),
}


class SessionVoiceCache:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def get_profile_name(self, session_id: str) -> str | None:
        return self._cache.get(session_id)

    def set_profile_name(self, session_id: str, profile_name: str) -> None:
        if profile_name in VOICE_PROFILES:
            self._cache[session_id] = profile_name

    def resolve_profile(self, session_id: str, *, preferred_profile: str | None = None) -> VoiceProfile:
        if preferred_profile and preferred_profile in VOICE_PROFILES:
            self._cache[session_id] = preferred_profile
            return VOICE_PROFILES[preferred_profile]

        profile_name = self._cache.get(session_id, "neutral")
        return VOICE_PROFILES.get(profile_name, VOICE_PROFILES["neutral"])


def detect_expressive_intent(text: str) -> str:
    low = text.lower()
    if any(x in low for x in ("good morning", "good night", "hello", "hi", "hey")):
        return "greeting"
    if any(x in low for x in ("you got this", "proud of you", "keep going", "i believe in you")):
        return "encouragement"
    if any(x in low for x in ("haha", "lol", "yay", "wow", "playful")):
        return "playful"
    if any(x in low for x in ("miss you", "love", "hug", "with you", "companion")):
        return "companionship"
    if any(x in text for x in ("❤️", "💖", "💕", "😊", "🥰", "😌")):
        return "emotional_short"
    return "unknown"


def choose_profile_for_text(text: str) -> str:
    intent = detect_expressive_intent(text)
    if intent in {"encouragement", "companionship", "emotional_short"}:
        return "companion"
    if intent in {"playful", "greeting"}:
        return "playful"
    if looks_technical(text):
        return "professional"
    return "neutral"


def extract_voice_override(text: str) -> str | None:
    m = re.search(r"\[\[tts:voice=([a-zA-Z0-9_\-]+)\]\]", text)
    if not m:
        return None
    voice = m.group(1).strip().lower()
    return voice if voice in VOICE_PROFILES else None


def voice_id_for_engine(profile_name: str, engine: str) -> str:
    profile = VOICE_PROFILES.get(profile_name, VOICE_PROFILES["neutral"])
    if engine == "edge":
        return profile.edge_voice_id
    return profile.qwen_voice_id


def looks_technical(text: str) -> bool:
    low = text.lower()
    technical_keywords = (
        "traceback",
        "exception",
        "error",
        "config",
        "deployment",
        "debug",
        "log",
        "sudo",
        "kubectl",
        "pip install",
        "```",
    )
    return any(k in low for k in technical_keywords)


session_voice_cache = SessionVoiceCache()

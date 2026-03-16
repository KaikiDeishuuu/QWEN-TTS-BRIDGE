import unittest

from tts_bridge.voice_personality import (
    VOICE_PROFILES,
    extract_voice_override,
    session_voice_cache,
    voice_id_for_engine,
)


class VoicePersonalityTests(unittest.TestCase):
    def test_profiles_exist(self):
        for key in ("companion", "playful", "professional", "neutral"):
            self.assertIn(key, VOICE_PROFILES)

    def test_extract_override(self):
        self.assertEqual(extract_voice_override("[[tts:voice=playful]] hi"), "playful")
        self.assertIsNone(extract_voice_override("[[tts:voice=unknown]] hi"))

    def test_voice_id_mapping(self):
        self.assertTrue(voice_id_for_engine("companion", "edge").startswith("zh-CN-"))
        self.assertTrue(len(voice_id_for_engine("companion", "qwen")) > 0)

    def test_cache_roundtrip(self):
        session_voice_cache.set_profile_name("cache-test", "playful")
        self.assertEqual(session_voice_cache.get_profile_name("cache-test"), "playful")


if __name__ == "__main__":
    unittest.main()

import unittest

from tts_bridge.channel_routing import (
    VoiceDecisionConfig,
    build_send_plan,
    choose_bridge_format,
    should_use_voice,
    validate_bridge_response,
)


class RoutingTests(unittest.TestCase):
    def test_expressive_short_goes_voice_for_telegram(self):
        cfg = VoiceDecisionConfig(mode="tagged", max_chars=160, max_sentences=2, min_expressive_hits=1)
        use_voice, reason = should_use_voice("Good night ❤️", "telegram", cfg)
        self.assertTrue(use_voice)
        self.assertEqual(reason, "expressive_short_proactive")

    def test_technical_stays_text(self):
        cfg = VoiceDecisionConfig(mode="tagged")
        use_voice, reason = should_use_voice("Run sudo apt update and inspect ERROR logs.", "telegram", cfg)
        self.assertFalse(use_voice)
        self.assertEqual(reason, "technical_or_structured")

    def test_long_stays_text(self):
        cfg = VoiceDecisionConfig(mode="tagged", max_chars=30)
        use_voice, reason = should_use_voice("I love helping you but this sentence is too long for voice mode.", "telegram", cfg)
        self.assertFalse(use_voice)
        self.assertEqual(reason, "too_long")

    def test_telegram_default_format(self):
        self.assertEqual(choose_bridge_format("telegram"), "ogg")

    def test_feishu_default_format(self):
        self.assertEqual(choose_bridge_format("feishu"), "mp3")

    def test_validate_audio_rejects_json(self):
        ok, reason = validate_bridge_response("application/json", b'{"detail":"nope"}')
        self.assertFalse(ok)
        self.assertEqual(reason, "bridge_returned_json")

    def test_send_plan_channel_aware(self):
        self.assertEqual(build_send_plan("telegram", True)["asVoice"], True)
        self.assertEqual(build_send_plan("feishu", True)["msg_type"], "audio")
        self.assertEqual(build_send_plan("other", False)["final_send_mode"], "text")


if __name__ == "__main__":
    unittest.main()

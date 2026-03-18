import unittest

from tts_bridge.channel_routing import (
    VoiceDecisionConfig,
    build_send_plan,
    choose_bridge_format,
    resolve_session_voice,
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
        self.assertEqual(choose_bridge_format("feishu"), "ogg")

    def test_validate_audio_rejects_json(self):
        ok, reason = validate_bridge_response("application/json", b'{"detail":"nope"}')
        self.assertFalse(ok)
        self.assertEqual(reason, "bridge_returned_json")

    def test_validate_audio_rejects_small_audio(self):
        ok, reason = validate_bridge_response("audio/ogg", b"0" * 800)
        self.assertFalse(ok)
        self.assertEqual(reason, "audio_too_small")

    def test_send_plan_channel_aware(self):
        tg = build_send_plan("telegram", True)
        self.assertEqual(tg["asVoice"], True)
        self.assertEqual(tg["ptt"], True)
        self.assertEqual(tg["audio_as_voice"], True)
        self.assertEqual(build_send_plan("feishu", True)["msg_type"], "audio")
        self.assertEqual(build_send_plan("other", False)["final_send_mode"], "text")

    def test_session_keeps_same_voice_profile(self):
        first = resolve_session_voice("s1", "Good night ❤️", tts_engine="qwen")
        second = resolve_session_voice("s1", "hello again", tts_engine="qwen")
        self.assertEqual(first["voice_profile"], second["voice_profile"])

    def test_manual_voice_override(self):
        data = resolve_session_voice("s2", "[[tts:voice=professional]] hello", tts_engine="qwen")
        self.assertEqual(data["voice_profile"], "professional")


    def test_telegram_long_audio_goes_send_audio(self):
        tg = build_send_plan("telegram", True, duration_s=301, mime_type="audio/ogg")
        self.assertEqual(tg["final_send_mode"], "audio")
        self.assertEqual(tg["transport_api"], "sendAudio")

    def test_telegram_non_audio_payload_goes_document(self):
        tg = build_send_plan("telegram", True, mime_type="application/octet-stream")
        self.assertEqual(tg["final_send_mode"], "document")
        self.assertEqual(tg["transport_api"], "sendDocument")


if __name__ == "__main__":
    unittest.main()

import unittest

from tts_bridge.server import _fallback_response, _validate_encoded_audio


class TestServerRuntimeContract(unittest.TestCase):
    def test_fallback_response_always_has_request_id_header(self):
        resp = _fallback_response("capability_blocked", "req_123", status_code=409)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.headers.get("X-Request-Id"), "req_123")
        self.assertEqual(resp.headers.get("X-Reason-Code"), "capability_blocked")

    def test_validate_encoded_audio_rejects_empty(self):
        ok, reason = _validate_encoded_audio("audio/mpeg", b"")
        self.assertFalse(ok)
        self.assertEqual(reason, "audio_too_small")

    def test_validate_encoded_audio_accepts_minimal_mp3_id3(self):
        # Minimal header-level check contract; real decode handled by ffmpeg path.
        payload = b"ID3" + b"\x00" * 1024
        ok, reason = _validate_encoded_audio("audio/mpeg", payload)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")


if __name__ == "__main__":
    unittest.main()

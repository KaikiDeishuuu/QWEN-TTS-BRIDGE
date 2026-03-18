import asyncio
import json
import unittest

from tts_bridge.server import _fallback_response, _record_replay, replay


class TestReplayRuntime(unittest.TestCase):
    def test_replay_not_found_has_request_id_header(self):
        resp = asyncio.run(replay("req_missing_001"))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.headers.get("X-Request-Id"), "req_missing_001")
        data = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(data["error"], "request_id_not_found")

    def test_replay_returns_recorded_payload(self):
        rid = "req_replay_123"
        _record_replay(rid, normalized_input={"channel": "feishu", "sender_type": "bot"}, terminal_status="success", reason_code="ok")
        resp = asyncio.run(replay(rid))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-Request-Id"), rid)
        data = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(data["request_id"], rid)
        self.assertEqual(data["terminal_status"], "success")
        self.assertEqual(data["reason_code"], "ok")

    def test_fallback_response_with_plan_fields(self):
        class _Plan:
            resolved_type = "text"
            fallback_chain = ("text",)
            reason_codes = ("no_tts_provider",)

        resp = _fallback_response("no_tts_provider", "req_plan_1", plan=_Plan())
        data = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(data["reason_code"], "no_tts_provider")
        self.assertEqual(data["fallback_chain"], ["text"])
        self.assertEqual(data["plan_reason_codes"], ["no_tts_provider"])


if __name__ == "__main__":
    unittest.main()

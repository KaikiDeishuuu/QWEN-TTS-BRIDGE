import unittest
from types import MappingProxyType

from tts_bridge.delivery_planner import (
    DeliveryPlanError,
    ProviderRegistry,
    decide_audio_delivery,
    get_provider_registry,
)


class TestDeliveryPlanner(unittest.TestCase):
    def setUp(self):
        reg = get_provider_registry()
        reg.clear_bridge()
        reg.set_native_allowed(False)

    def test_feishu_bot_allowed(self):
        get_provider_registry().register_bridge("http://test", healthy=True)
        plan = decide_audio_delivery(
            request_id="test_1",
            channel="feishu",
            sender_type="bot",
            text_length=10,
        )
        self.assertEqual(plan.channel, "feishu")
        self.assertEqual(plan.sender_type, "bot")
        self.assertEqual(plan.resolved_type, "voice_bubble")
        self.assertEqual(plan.status, "ok")
        self.assertTrue(plan.constraints["supports_bot_audio"])
        self.assertFalse(plan.constraints["supports_user_audio"])

    def test_feishu_user_blocked(self):
        with self.assertRaises(DeliveryPlanError) as cm:
            decide_audio_delivery(
                request_id="test_2",
                channel="feishu",
                sender_type="user",
                text_length=10,
            )
        self.assertIn("USER sender is FORBIDDEN", str(cm.exception))

    def test_telegram_voice_for_short_text(self):
        get_provider_registry().register_bridge("http://test", healthy=True)
        plan = decide_audio_delivery(
            request_id="test_3",
            channel="telegram",
            sender_type="bot",
            text_length=50,
        )
        self.assertEqual(plan.resolved_type, "voice_bubble")
        self.assertIn("tg_short_audio_use_voice_bubble", plan.reason_codes)

    def test_telegram_audio_for_long_text(self):
        get_provider_registry().register_bridge("http://test", healthy=True)
        plan = decide_audio_delivery(
            request_id="test_4",
            channel="telegram",
            sender_type="bot",
            text_length=1000,
        )
        self.assertEqual(plan.resolved_type, "audio_file")
        self.assertIn("tg_long_text_use_audio_file", plan.reason_codes)

    def test_circuit_breaker_fallback(self):
        reg = ProviderRegistry(failure_threshold=2)
        reg.register_bridge("http://dead-bridge", healthy=False)
        reg.report_failure("bridge", error_type="timeout")
        reg.set_native_allowed(True)

        plan = decide_audio_delivery(
            request_id="test_5",
            channel="telegram",
            sender_type="bot",
            text_length=10,
            registry=reg,
        )

        self.assertEqual(plan.tts_provider, "native")
        self.assertIn("bridge_unhealthy_native_fallback", plan.reason_codes)
        self.assertEqual(plan.fallback_chain[0], "voice_bubble")

    def test_no_provider_fallback(self):
        reg = ProviderRegistry()
        reg.set_native_allowed(False)

        plan = decide_audio_delivery(
            request_id="test_6",
            channel="telegram",
            sender_type="bot",
            text_length=10,
            registry=reg,
        )

        self.assertEqual(plan.status, "fallback_text")
        self.assertEqual(plan.resolved_type, "text")
        self.assertIn("no_tts_provider", plan.reason_codes)


    def test_delivery_plan_is_deeply_immutable(self):
        get_provider_registry().register_bridge("http://test", healthy=True)
        plan = decide_audio_delivery(
            request_id="test_immutable",
            channel="telegram",
            sender_type="bot",
            text_length=50,
        )

        self.assertIsInstance(plan.fallback_chain, tuple)
        self.assertIsInstance(plan.reason_codes, tuple)
        self.assertIsInstance(plan.constraints, MappingProxyType)

        with self.assertRaises(AttributeError):
            plan.fallback_chain.append("text")  # type: ignore[attr-defined]

        with self.assertRaises(TypeError):
            plan.constraints["new_key"] = True  # type: ignore[index]

    def test_provider_registry_snapshot(self):
        reg = ProviderRegistry()
        reg.register_bridge("http://bridge", healthy=True)
        reg.set_native_allowed(True)
        snap = reg.snapshot()
        self.assertTrue(snap["bridge_registered"])
        self.assertEqual(snap["bridge_url"], "http://bridge")
        self.assertTrue(snap["native_allowed"])

    def test_replay_determinism_same_normalized_request(self):
        get_provider_registry().register_bridge("http://test", healthy=True)
        plan1 = decide_audio_delivery(
            request_id="replay_1",
            channel="Feishu",
            sender_type="bot",
            text_length=120,
        )
        plan2 = decide_audio_delivery(
            request_id="replay_2",
            channel="feishu",
            sender_type="bot",
            text_length=120,
        )

        self.assertEqual(plan1.channel, plan2.channel)
        self.assertEqual(plan1.sender_type, plan2.sender_type)
        self.assertEqual(plan1.requested_type, plan2.requested_type)
        self.assertEqual(plan1.resolved_type, plan2.resolved_type)
        self.assertEqual(plan1.tts_provider, plan2.tts_provider)
        self.assertEqual(plan1.fallback_chain, plan2.fallback_chain)
        self.assertEqual(plan1.reason_codes, plan2.reason_codes)


if __name__ == "__main__":
    unittest.main()

import unittest
from tts_bridge.delivery_planner import (
    decide_audio_delivery,
    ProviderRegistry,
    DeliveryPlanError,
    get_provider_registry,
)
from tts_bridge.channel_caps import ChannelCapabilityError

class TestDeliveryPlanner(unittest.TestCase):
    def setUp(self):
        # Reset registry for each test
        reg = get_provider_registry()
        reg._bridge_url = None
        reg._is_open = False
        reg._failure_count = 0

    def test_feishu_bot_allowed(self):
        get_provider_registry().register_bridge("http://test", healthy=True)
        plan = decide_audio_delivery(
            request_id="test_1",
            channel="feishu",
            sender_type="bot",
            text_length=10
        )
        self.assertEqual(plan.channel, "feishu")
        self.assertEqual(plan.sender_type, "bot")
        self.assertEqual(plan.resolved_type, "voice_bubble")
        self.assertEqual(plan.status, "ok")

    def test_feishu_user_blocked(self):
        with self.assertRaises(DeliveryPlanError) as cm:
            decide_audio_delivery(
                request_id="test_2",
                channel="feishu",
                sender_type="user",
                text_length=10
            )
        self.assertIn("USER sender is FORBIDDEN", str(cm.exception))

    def test_telegram_voice_for_short_text(self):
        get_provider_registry().register_bridge("http://test", healthy=True)
        plan = decide_audio_delivery(
            request_id="test_3",
            channel="telegram",
            sender_type="bot",
            text_length=50
        )
        self.assertEqual(plan.resolved_type, "voice_bubble")

    def test_telegram_audio_for_long_text(self):
        get_provider_registry().register_bridge("http://test", healthy=True)
        plan = decide_audio_delivery(
            request_id="test_4",
            channel="telegram",
            sender_type="bot",
            text_length=1000
        )
        self.assertEqual(plan.resolved_type, "audio_file")
        self.assertIn("tg_long_text_use_audio_file", plan.reason_codes)

    def test_circuit_breaker_fallback(self):
        reg = ProviderRegistry(failure_threshold=2)
        reg.register_bridge("http://dead-bridge", healthy=False)
        reg.report_failure() # count = 2 -> OPEN
        reg.set_native_allowed(True)
        
        plan = decide_audio_delivery(
            request_id="test_5",
            channel="telegram",
            sender_type="bot",
            text_length=10,
            registry=reg
        )
        
        self.assertEqual(plan.tts_provider, "native")
        self.assertIn("bridge_unhealthy_native_fallback", plan.reason_codes)

    def test_no_provider_fallback(self):
        reg = ProviderRegistry()
        reg.set_native_allowed(False) # No bridge registered yet
        
        plan = decide_audio_delivery(
            request_id="test_6",
            channel="telegram",
            sender_type="bot",
            text_length=10,
            registry=reg
        )
        
        self.assertEqual(plan.status, "fallback_text")
        self.assertEqual(plan.resolved_type, "text")
        self.assertIn("no_tts_provider", plan.reason_codes)

if __name__ == "__main__":
    unittest.main()

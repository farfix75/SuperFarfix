"""Offline checks for the fast, adaptive FARFIX text router."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core import ai_router


class AiRouterTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "openai_api_key": "legacy-test-key",
            "farfix_ai": {"provider": "openai"},
        }
        self.load = patch.object(ai_router, "_load", return_value=self.config)
        self.load.start()
        ai_router._clear_response_cache()

    def tearDown(self):
        self.load.stop()

    def test_local_env_key_takes_priority_over_legacy_json(self):
        with patch.dict(ai_router.os.environ, {}, clear=True), \
             patch.object(ai_router, "_env_values", return_value={"OPENAI_API_KEY": "local-test-key"}):
            self.assertEqual(ai_router._key("OPENAI_API_KEY"), "local-test-key")

    def test_process_environment_overrides_local_file(self):
        with patch.dict(ai_router.os.environ, {"OPENAI_API_KEY": "system-test-key"}, clear=True), \
             patch.object(ai_router, "_env_values", return_value={"OPENAI_API_KEY": "local-test-key"}):
            self.assertEqual(ai_router._key("OPENAI_API_KEY"), "system-test-key")

    def test_adaptive_profile_uses_fast_or_smart_model(self):
        cfg = ai_router.get_config()
        fast = ai_router._openai_profile("Ciao", [], cfg)
        smart = ai_router._openai_profile("Analizza questo codice e proponi un piano", [], cfg)
        self.assertEqual(fast[0], "gpt-5.6-luna")
        self.assertEqual(fast[1], "none")
        self.assertEqual(smart[0], "gpt-5.6-terra")
        self.assertEqual(smart[1], "medium")

    def test_repeated_non_dynamic_request_uses_memory_cache(self):
        calls = []

        def fake_openai(*args, **kwargs):
            calls.append((args, kwargs))
            return "Risposta pronta"

        with patch.object(ai_router, "_openai", side_effect=fake_openai):
            first = ai_router.generate("Spiega le liste Python", provider="openai")
            second = ai_router.generate("Spiega le liste Python", provider="openai")
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

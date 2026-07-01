from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from opc.core.config import LLMConfig
from opc.llm.provider import LLMProvider


class TestLLMProviderHasCredentials(unittest.TestCase):
    def test_configured_api_key_has_credentials(self) -> None:
        provider = LLMProvider(LLMConfig(default_model="openai/gpt-4o", api_key="sk-real"))
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(provider.has_credentials())

    def test_api_key_env_resolves_to_credentials(self) -> None:
        with patch.dict(os.environ, {"MY_KEY": "sk-env"}, clear=True):
            provider = LLMProvider(LLMConfig(default_model="openai/gpt-4o", api_key_env="MY_KEY"))
            self.assertTrue(provider.has_credentials())

    def test_no_key_anywhere_has_no_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            provider = LLMProvider(LLMConfig(default_model="openai/gpt-4o", api_key=""))
            self.assertFalse(provider.has_credentials())

    def test_well_known_env_var_counts_as_credentials(self) -> None:
        """Users who export OPENAI_API_KEY without putting it in config are not downgraded."""
        provider = LLMProvider(LLMConfig(default_model="openai/gpt-4o", api_key=""))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}, clear=True):
            self.assertTrue(provider.has_credentials())


class TestLLMProviderContextWindow(unittest.TestCase):
    def test_gpt_5_4_override_applies_on_official_openai_base(self) -> None:
        provider = LLMProvider(LLMConfig(default_model="openai/gpt-5.4"))

        with patch("opc.llm.provider.litellm.get_max_tokens", return_value=128000):
            self.assertEqual(provider.get_context_window(), 1_050_000)

    def test_gpt_5_4_override_does_not_apply_on_proxy_base(self) -> None:
        provider = LLMProvider(LLMConfig(
            default_model="openai/gpt-5.4",
            api_base="https://openrouter.ai/api/v1",
        ))

        with patch("opc.llm.provider.litellm.get_max_tokens", return_value=128000):
            self.assertEqual(provider.get_context_window(), 128000)

    def test_poe_claude_sonnet_4_5_model_uses_local_context_window(self) -> None:
        provider = LLMProvider(LLMConfig(
            default_model="claude-sonnet-4.5",
            api_base="https://api.poe.com/v1",
        ))

        with patch("opc.llm.provider.litellm.get_max_tokens") as get_max_tokens:
            self.assertEqual(provider.get_context_window(), 64_000)
            get_max_tokens.assert_not_called()

    def test_poe_openai_compatible_legacy_prefix_uses_same_context_window(self) -> None:
        provider = LLMProvider(LLMConfig(
            default_model="openai/claude-sonnet-4.5",
            api_base="https://api.poe.com/v1",
        ))

        with patch("opc.llm.provider.litellm.get_max_tokens") as get_max_tokens:
            self.assertEqual(provider.get_context_window(), 64_000)
            get_max_tokens.assert_not_called()

    def test_non_overridden_model_still_uses_litellm(self) -> None:
        provider = LLMProvider(LLMConfig(default_model="openai/gpt-4o"))

        with patch("opc.llm.provider.litellm.get_max_tokens", return_value=128000):
            self.assertEqual(provider.get_context_window(), 128000)

    def test_config_scalar_override_supplies_window_for_unmapped_model(self) -> None:
        """Unmapped proxy models (doubao/minimax/…) get a real window from config."""
        provider = LLMProvider(LLMConfig(
            default_model="openai/doubao-seed-2.0-pro",
            api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
            context_window=256000,
        ))

        with patch("opc.llm.provider.litellm.get_max_tokens", return_value=None) as get_max_tokens:
            self.assertEqual(provider.get_context_window(), 256000)
            get_max_tokens.assert_not_called()

    def test_unmapped_model_without_override_returns_none(self) -> None:
        """No override + litellm can't map → None (unchanged fallback)."""
        provider = LLMProvider(LLMConfig(
            default_model="openai/doubao-seed-2.0-pro",
            api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
        ))

        with patch("opc.llm.provider.litellm.get_max_tokens", return_value=None):
            self.assertIsNone(provider.get_context_window())

    def test_config_per_model_override_takes_precedence(self) -> None:
        provider = LLMProvider(LLMConfig(
            default_model="openai/doubao-seed-2.0-pro",
            context_window=200000,
            context_window_overrides={"doubao-seed-2.0-pro": 262144},
        ))

        with patch("opc.llm.provider.litellm.get_max_tokens", return_value=None):
            self.assertEqual(provider.get_context_window(), 262144)

    def test_config_override_wins_over_litellm_for_mapped_model(self) -> None:
        provider = LLMProvider(LLMConfig(default_model="openai/gpt-4o", context_window=50000))

        with patch("opc.llm.provider.litellm.get_max_tokens", return_value=128000) as get_max_tokens:
            self.assertEqual(provider.get_context_window(), 50000)
            get_max_tokens.assert_not_called()


if __name__ == "__main__":
    unittest.main()

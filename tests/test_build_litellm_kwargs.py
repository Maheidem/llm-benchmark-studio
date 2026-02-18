"""Tests for build_litellm_kwargs() — the final kwarg builder for LiteLLM calls."""

import pytest
from dataclasses import dataclass
from typing import Optional

from provider_params import build_litellm_kwargs


@dataclass
class MockTarget:
    """Minimal Target-like object for testing build_litellm_kwargs."""
    model_id: str
    provider_key: Optional[str] = None
    skip_params: Optional[list] = None


# ===========================================================================
# Basic usage
# ===========================================================================


class TestBasicUsage:
    def test_no_params_returns_empty(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(target)
        assert result == {}

    def test_temperature_only(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(target, temperature=0.7)
        assert result["temperature"] == 0.7

    def test_max_tokens_only(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(target, max_tokens=4096)
        assert result["max_tokens"] == 4096

    def test_both_temperature_and_max_tokens(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(target, temperature=0.5, max_tokens=2048)
        assert result["temperature"] == 0.5
        assert result["max_tokens"] == 2048


# ===========================================================================
# Provider params override
# ===========================================================================


class TestProviderParamsOverride:
    def test_provider_params_override_explicit(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(
            target,
            temperature=0.5,
            provider_params={"temperature": 0.9},
        )
        assert result["temperature"] == 0.9

    def test_provider_params_with_tier2(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(
            target,
            provider_params={"temperature": 0.7, "top_p": 0.9},
        )
        assert result["temperature"] == 0.7
        assert result["top_p"] == 0.9

    def test_provider_params_none_values_ignored(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(
            target,
            provider_params={"temperature": 0.7, "top_p": None},
        )
        assert result["temperature"] == 0.7
        assert "top_p" not in result


# ===========================================================================
# Passthrough (Tier 3)
# ===========================================================================


class TestPassthrough:
    def test_passthrough_params_included(self):
        target = MockTarget(model_id="ollama/llama3.1", provider_key="ollama")
        result = build_litellm_kwargs(
            target,
            provider_params={
                "temperature": 0.7,
                "passthrough": {"repeat_penalty": 1.1},
            },
        )
        assert result["repeat_penalty"] == 1.1

    def test_passthrough_none_values_excluded(self):
        target = MockTarget(model_id="ollama/llama3.1", provider_key="ollama")
        result = build_litellm_kwargs(
            target,
            provider_params={
                "temperature": 0.7,
                "passthrough": {"repeat_penalty": None},
            },
        )
        assert "repeat_penalty" not in result

    def test_passthrough_not_dict_ignored(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(
            target,
            provider_params={"temperature": 0.7, "passthrough": "invalid"},
        )
        # Should not crash, passthrough treated as empty
        assert result["temperature"] == 0.7


# ===========================================================================
# Skip params
# ===========================================================================


class TestSkipParams:
    def test_skip_temperature(self):
        target = MockTarget(model_id="gpt-4o", provider_key="openai", skip_params=["temperature"])
        result = build_litellm_kwargs(target, temperature=0.7, max_tokens=4096)
        assert "temperature" not in result
        assert result["max_tokens"] == 4096

    def test_skip_passthrough_param(self):
        target = MockTarget(model_id="ollama/llama3.1", provider_key="ollama", skip_params=["repeat_penalty"])
        result = build_litellm_kwargs(
            target,
            provider_params={
                "temperature": 0.7,
                "passthrough": {"repeat_penalty": 1.1},
            },
        )
        assert "repeat_penalty" not in result
        assert result["temperature"] == 0.7


# ===========================================================================
# Provider-specific behaviour
# ===========================================================================


class TestProviderSpecificBehaviour:
    def test_anthropic_clamping(self):
        """Anthropic max temp is 1.0, so 1.5 should be clamped."""
        target = MockTarget(model_id="anthropic/claude-sonnet-4-6", provider_key="anthropic")
        result = build_litellm_kwargs(target, temperature=1.5)
        assert result["temperature"] == 1.0

    def test_anthropic_drops_top_p_with_temperature(self):
        """Anthropic conflict: using both temp and top_p drops top_p."""
        target = MockTarget(model_id="anthropic/claude-sonnet-4-6", provider_key="anthropic")
        result = build_litellm_kwargs(
            target,
            provider_params={"temperature": 0.7, "top_p": 0.9},
        )
        assert result["temperature"] == 0.7
        assert "top_p" not in result

    def test_o_series_max_tokens_conversion(self):
        """O-series should convert max_tokens to max_completion_tokens."""
        target = MockTarget(model_id="openai/o3-mini", provider_key="openai")
        result = build_litellm_kwargs(target, max_tokens=4096)
        assert "max_tokens" not in result
        assert result["max_completion_tokens"] == 4096

    def test_o_series_temperature_lock(self):
        """O-series locks temperature to 1.0."""
        target = MockTarget(model_id="openai/o3-mini", provider_key="openai")
        result = build_litellm_kwargs(target, temperature=0.5)
        assert result["temperature"] == 1.0

    def test_openai_warns_top_k(self):
        """OpenAI top_k passes through with a warning (warn-not-drop)."""
        target = MockTarget(model_id="gpt-4o", provider_key="openai")
        result = build_litellm_kwargs(
            target,
            provider_params={"temperature": 0.7, "top_k": 20},
        )
        # top_k now passes through (warn, not drop)
        assert result.get("top_k") == 20

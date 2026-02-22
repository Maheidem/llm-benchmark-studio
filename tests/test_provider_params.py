"""Tests for provider_params.py — provider identification, clamping, conflict resolution, validation."""

import pytest

from provider_params import (
    PROVIDER_REGISTRY,
    _PREFIX_MAP,
    _bare_model_name,
    _is_o_series,
    build_litellm_kwargs,
    clamp_temperature,
    identify_provider,
    resolve_conflicts,
    validate_params,
)


# ===========================================================================
# identify_provider
# ===========================================================================


class TestIdentifyProvider:
    """Tests for provider identification from model IDs and keys."""

    def test_explicit_provider_key_openai(self):
        assert identify_provider("gpt-4o", "openai") == "openai"

    def test_explicit_provider_key_anthropic(self):
        assert identify_provider("claude-opus-4-6", "anthropic") == "anthropic"

    def test_explicit_key_unknown_falls_through_to_prefix(self):
        """If provider_key is not in registry, fall through to prefix detection."""
        assert identify_provider("anthropic/claude-opus-4-6", "custom_key") == "anthropic"

    def test_prefix_anthropic(self):
        assert identify_provider("anthropic/claude-sonnet-4-6") == "anthropic"

    def test_prefix_gemini(self):
        assert identify_provider("gemini/gemini-3-pro-preview") == "gemini"

    def test_prefix_vertex_ai_maps_to_gemini(self):
        assert identify_provider("vertex_ai/gemini-2.0-flash") == "gemini"

    def test_prefix_ollama(self):
        assert identify_provider("ollama/llama3.1") == "ollama"

    def test_prefix_ollama_chat(self):
        assert identify_provider("ollama_chat/mistral") == "ollama"

    def test_prefix_lm_studio(self):
        assert identify_provider("lm_studio/qwen3-coder-30b") == "lm_studio"

    def test_prefix_mistral(self):
        assert identify_provider("mistral/mistral-large-latest") == "mistral"

    def test_prefix_deepseek(self):
        assert identify_provider("deepseek/deepseek-r1") == "deepseek"

    def test_prefix_cohere(self):
        assert identify_provider("cohere/command-r-plus") == "cohere"

    def test_prefix_cohere_chat(self):
        assert identify_provider("cohere_chat/command-r") == "cohere"

    def test_prefix_xai(self):
        assert identify_provider("xai/grok-3") == "xai"

    def test_prefix_vllm(self):
        assert identify_provider("vllm/llama-3-70b") == "vllm"

    def test_prefix_openai_explicit(self):
        assert identify_provider("openai/gpt-4o") == "openai"

    def test_unknown_model_no_key(self):
        assert identify_provider("zai/GLM-4.7") == "_unknown"

    def test_bare_model_no_prefix(self):
        """Models without prefix and no provider_key default to _unknown."""
        assert identify_provider("gpt-4o") == "_unknown"

    def test_case_insensitive(self):
        assert identify_provider("ANTHROPIC/Claude-Opus-4-6") == "anthropic"

    def test_empty_string(self):
        assert identify_provider("") == "_unknown"

    def test_explicit_key_takes_priority_over_prefix(self):
        """Explicit provider_key should win over prefix detection."""
        assert identify_provider("openai/gpt-4o", "lm_studio") == "lm_studio"


# ===========================================================================
# _bare_model_name
# ===========================================================================


class TestBareModelName:
    def test_strips_anthropic_prefix(self):
        assert _bare_model_name("anthropic/claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_strips_openai_prefix(self):
        assert _bare_model_name("openai/gpt-4o") == "gpt-4o"

    def test_no_prefix_returns_lowercase(self):
        assert _bare_model_name("GPT-4o") == "gpt-4o"

    def test_empty_string(self):
        assert _bare_model_name("") == ""


# ===========================================================================
# _is_o_series
# ===========================================================================


class TestIsOSeries:
    def test_o1_model(self):
        assert _is_o_series("o1") is True

    def test_o1_preview(self):
        assert _is_o_series("o1-preview") is True

    def test_o3_mini(self):
        assert _is_o_series("o3-mini") is True

    def test_o4_mini(self):
        assert _is_o_series("o4-mini") is True

    def test_with_prefix(self):
        assert _is_o_series("openai/o3-mini") is True

    def test_not_o_series(self):
        assert _is_o_series("gpt-4o") is False

    def test_o2_not_o_series(self):
        """o2 is not in the o-series pattern (o1, o3, o4)."""
        assert _is_o_series("o2") is False

    def test_not_starting_with_o(self):
        assert _is_o_series("model-o3") is False


# ===========================================================================
# clamp_temperature
# ===========================================================================


class TestClampTemperature:
    def test_within_range_openai(self):
        """Temperature within OpenAI range (0-2) should pass through."""
        val, adj = clamp_temperature(1.0, "openai", "gpt-4o")
        assert val == 1.0
        assert adj is None

    def test_above_anthropic_max(self):
        """Anthropic max is 1.0, so 1.5 should clamp to 1.0."""
        val, adj = clamp_temperature(1.5, "anthropic", "claude-sonnet-4-6")
        assert val == 1.0
        assert adj is not None
        assert adj["param"] == "temperature"
        assert adj["original"] == 1.5
        assert adj["adjusted"] == 1.0

    def test_below_min(self):
        """Negative temperature should clamp to 0."""
        val, adj = clamp_temperature(-0.5, "openai", "gpt-4o")
        assert val == 0.0
        assert adj is not None
        assert adj["adjusted"] == 0.0

    def test_gpt5_locks_to_1(self):
        """GPT-5 always locks temperature to 1.0."""
        val, adj = clamp_temperature(0.5, "openai", "gpt-5")
        assert val == 1.0
        assert adj is not None
        assert "GPT-5" in adj["reason"]

    def test_gpt5_already_at_1(self):
        """GPT-5 with temp=1.0 should have no adjustment."""
        val, adj = clamp_temperature(1.0, "openai", "gpt-5")
        assert val == 1.0
        assert adj is None

    def test_o_series_locks_to_1(self):
        """O-series models lock temperature to 1.0."""
        val, adj = clamp_temperature(0.7, "openai", "o3-mini")
        assert val == 1.0
        assert adj is not None
        assert "O-series" in adj["reason"]

    def test_gemini_3_clamp_minimum(self):
        """Gemini 3 models clamp minimum to 1.0."""
        val, adj = clamp_temperature(0.5, "gemini", "gemini-3-pro-preview")
        assert val == 1.0
        assert adj is not None
        assert "Gemini 3" in adj["reason"]

    def test_gemini_3_above_minimum(self):
        """Gemini 3 models above 1.0 should pass through."""
        val, adj = clamp_temperature(1.5, "gemini", "gemini-3-pro-preview")
        assert val == 1.5
        assert adj is None

    def test_unknown_provider_uses_wide_range(self):
        """Unknown provider uses 0-2 range."""
        val, adj = clamp_temperature(1.5, "_unknown", "some-model")
        assert val == 1.5
        assert adj is None

    def test_cohere_max_1(self):
        """Cohere max is 1.0."""
        val, adj = clamp_temperature(1.5, "cohere", "command-r-plus")
        assert val == 1.0
        assert adj is not None


# ===========================================================================
# resolve_conflicts
# ===========================================================================


class TestResolveConflicts:
    def test_anthropic_temp_and_top_p(self):
        """Anthropic: using both temperature and top_p should drop top_p."""
        resolved, adjustments = resolve_conflicts(
            {"temperature": 0.7, "top_p": 0.9}, "anthropic", "claude-sonnet-4-6"
        )
        assert "top_p" not in resolved
        assert "temperature" in resolved
        assert any(a["param"] == "top_p" for a in adjustments)

    def test_anthropic_warns_unsupported_penalties(self):
        """Anthropic: frequency_penalty and presence_penalty pass through with warnings."""
        resolved, adjustments = resolve_conflicts(
            {"frequency_penalty": 0.5, "presence_penalty": 0.3},
            "anthropic",
            "claude-sonnet-4-6",
        )
        # Params pass through (warn, not drop)
        assert resolved["frequency_penalty"] == 0.5
        assert resolved["presence_penalty"] == 0.3
        assert len(adjustments) == 2
        assert all(a["action"] == "warn" for a in adjustments)

    def test_anthropic_seed_warned(self):
        """Anthropic: seed passes through with a warning."""
        resolved, adjustments = resolve_conflicts(
            {"seed": 42}, "anthropic", "claude-sonnet-4-6"
        )
        assert resolved["seed"] == 42
        assert any(a["param"] == "seed" and a["action"] == "warn" for a in adjustments)

    def test_openai_no_conflicts_basic(self):
        """OpenAI with standard params should have no conflicts."""
        resolved, adjustments = resolve_conflicts(
            {"temperature": 0.7, "top_p": 0.9}, "openai", "gpt-4o"
        )
        assert resolved["temperature"] == 0.7
        assert resolved["top_p"] == 0.9
        assert len(adjustments) == 0

    def test_openai_top_k_warned(self):
        """OpenAI top_k passes through with a warning (warn-not-drop)."""
        resolved, adjustments = resolve_conflicts(
            {"top_k": 20}, "openai", "gpt-4o"
        )
        assert resolved["top_k"] == 20
        assert any(a["param"] == "top_k" and a["action"] == "warn" for a in adjustments)

    def test_openai_o_series_max_tokens_conversion(self):
        """O-series should convert max_tokens to max_completion_tokens."""
        resolved, adjustments = resolve_conflicts(
            {"max_tokens": 4096}, "openai", "o3-mini"
        )
        assert "max_tokens" not in resolved
        assert resolved.get("max_completion_tokens") == 4096
        assert any(a["param"] == "max_tokens" for a in adjustments)

    def test_empty_params(self):
        """Empty params should have no conflicts."""
        resolved, adjustments = resolve_conflicts({}, "openai", "gpt-4o")
        assert resolved == {}
        assert adjustments == []

    def test_ollama_no_conflicts(self):
        """Ollama supports most params — no conflicts expected."""
        resolved, adjustments = resolve_conflicts(
            {"temperature": 0.7, "top_p": 0.9, "top_k": 40}, "ollama", "llama3.1"
        )
        assert resolved["top_k"] == 40
        assert len(adjustments) == 0

    def test_gemini_supports_top_k(self):
        """Gemini supports top_k."""
        resolved, adjustments = resolve_conflicts(
            {"top_k": 50}, "gemini", "gemini-2.0-flash"
        )
        assert resolved.get("top_k") == 50


# ===========================================================================
# validate_params
# ===========================================================================


class TestValidateParams:
    def test_valid_openai_params(self):
        result = validate_params("openai", "gpt-4o", {"temperature": 0.7})
        assert result["valid"] is True
        assert len(result["adjustments"]) == 0
        assert result["resolved_params"]["temperature"] == 0.7

    def test_clamped_temperature(self):
        result = validate_params("anthropic", "claude-sonnet-4-6", {"temperature": 1.5})
        assert result["valid"] is False
        assert len(result["adjustments"]) > 0
        assert result["resolved_params"]["temperature"] == 1.0

    def test_gpt5_temperature_lock(self):
        result = validate_params("openai", "gpt-5", {"temperature": 0.5})
        assert result["valid"] is False
        assert result["resolved_params"]["temperature"] == 1.0

    def test_unknown_provider_warning(self):
        result = validate_params("_unknown", "some-model", {"temperature": 0.7})
        assert any("Unknown provider" in w for w in result["warnings"])

    def test_anthropic_warning(self):
        result = validate_params("anthropic", "claude-sonnet-4-6", {"temperature": 0.7})
        assert any("max_tokens" in w for w in result["warnings"])

    def test_removes_none_values(self):
        result = validate_params("openai", "gpt-4o", {"temperature": 0.7, "top_p": None})
        assert "top_p" not in result["resolved_params"]

    def test_custom_param_gets_passthrough_adjustment(self):
        """Phase A: unknown/Tier-3 params produce a 'passthrough' adjustment badge."""
        result = validate_params("openai", "gpt-4o", {"repetition_penalty": 1.1})
        passthrough_adj = [
            a for a in result["adjustments"] if a["action"] == "passthrough"
        ]
        assert len(passthrough_adj) == 1, "Custom param should produce exactly one passthrough adjustment"
        assert passthrough_adj[0]["param"] == "repetition_penalty"
        assert passthrough_adj[0]["original"] == 1.1
        assert "Tier 3" in passthrough_adj[0]["reason"]

    def test_multiple_custom_params_each_get_passthrough_badge(self):
        """Phase A: multiple unknown params each get their own passthrough adjustment."""
        result = validate_params("openai", "gpt-4o", {
            "repetition_penalty": 1.1,
            "min_p": 0.05,
        })
        passthrough_params = {
            a["param"] for a in result["adjustments"] if a["action"] == "passthrough"
        }
        assert "repetition_penalty" in passthrough_params
        assert "min_p" in passthrough_params

    def test_known_param_does_not_get_passthrough_badge(self):
        """Phase A: a known Tier-1/Tier-2 param should not produce a passthrough adjustment."""
        result = validate_params("openai", "gpt-4o", {"temperature": 0.7})
        passthrough_adj = [
            a for a in result["adjustments"] if a["action"] == "passthrough"
        ]
        assert len(passthrough_adj) == 0, "Known param should not be badged as passthrough"


# ===========================================================================
# PROVIDER_REGISTRY structure validation
# ===========================================================================


class TestProviderRegistryStructure:
    """Validate the structure of the provider registry."""

    def test_all_providers_have_tier1(self):
        for key, prov in PROVIDER_REGISTRY.items():
            assert "tier1" in prov, f"Provider {key} missing tier1"

    def test_all_providers_have_temperature(self):
        for key, prov in PROVIDER_REGISTRY.items():
            assert "temperature" in prov["tier1"], f"Provider {key} missing temperature in tier1"

    def test_all_providers_have_display_name(self):
        for key, prov in PROVIDER_REGISTRY.items():
            if key != "_unknown":
                assert "display_name" in prov, f"Provider {key} missing display_name"

    def test_known_providers_exist(self):
        expected = {"openai", "anthropic", "gemini", "ollama", "lm_studio",
                    "mistral", "deepseek", "cohere", "xai", "vllm", "_unknown"}
        actual = set(PROVIDER_REGISTRY.keys())
        assert expected.issubset(actual), f"Missing providers: {expected - actual}"

    def test_prefix_map_covers_all_providers(self):
        """Every non-unknown provider should have at least one prefix mapping."""
        prov_values = set(_PREFIX_MAP.values())
        for key in PROVIDER_REGISTRY:
            if key == "_unknown":
                continue
            assert key in prov_values, f"Provider {key} has no prefix mapping"

    def test_temperature_ranges_are_valid(self):
        for key, prov in PROVIDER_REGISTRY.items():
            temp = prov["tier1"]["temperature"]
            assert temp["min"] < temp["max"], f"Provider {key} has invalid temp range"
            assert temp["min"] >= 0, f"Provider {key} has negative temp min"

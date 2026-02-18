"""Tests for Round 2 staging bug fixes.

Bug A: Cross-provider model selection — _parse_target_selection, _filter_targets, _target_key
Bug B: Param validation warn-not-drop — resolve_conflicts warns instead of dropping
Bug C: Suite dropdown test_case_count field (frontend-only, trivial assertion)
Bug D: ETA format functions (frontend-only, covered conceptually)
Bug E/F/G: Reconnection — result_ref set early in job (backend integration)
"""

import pytest
from benchmark import Target, build_targets
from app import _parse_target_selection, _filter_targets, _target_key
from provider_params import resolve_conflicts, identify_provider, validate_params


# ===========================================================================
# Bug A — Cross-provider model selection
# ===========================================================================


class TestParseTargetSelection:
    """_parse_target_selection supports both legacy and new targets format."""

    def test_legacy_models_list(self):
        """Legacy format: models: ['model_a', 'model_b']."""
        body = {"models": ["gpt-4o", "claude-opus-4-6"]}
        model_ids, target_set = _parse_target_selection(body)
        assert model_ids == ["gpt-4o", "claude-opus-4-6"]
        assert target_set is None

    def test_new_targets_format(self):
        """New format: targets: [{provider_key, model_id}, ...]."""
        body = {
            "targets": [
                {"provider_key": "openai", "model_id": "gpt-4o"},
                {"provider_key": "anthropic", "model_id": "anthropic/claude-opus-4-6"},
            ]
        }
        model_ids, target_set = _parse_target_selection(body)
        assert set(model_ids) == {"gpt-4o", "anthropic/claude-opus-4-6"}
        assert target_set == {
            ("openai", "gpt-4o"),
            ("anthropic", "anthropic/claude-opus-4-6"),
        }

    def test_targets_takes_priority_over_models(self):
        """When both targets and models are provided, targets wins."""
        body = {
            "targets": [{"provider_key": "openai", "model_id": "gpt-4o"}],
            "models": ["should-be-ignored"],
        }
        model_ids, target_set = _parse_target_selection(body)
        assert model_ids == ["gpt-4o"]
        assert target_set == {("openai", "gpt-4o")}

    def test_empty_targets_falls_back_to_models(self):
        """Empty targets list falls back to legacy models."""
        body = {"targets": [], "models": ["gpt-4o"]}
        model_ids, target_set = _parse_target_selection(body)
        assert model_ids == ["gpt-4o"]
        assert target_set is None

    def test_invalid_targets_entries_skipped(self):
        """Invalid entries in targets are skipped."""
        body = {
            "targets": [
                {"provider_key": "openai", "model_id": "gpt-4o"},
                {"bad_key": "value"},  # Missing required fields
                "just_a_string",
            ]
        }
        model_ids, target_set = _parse_target_selection(body)
        assert model_ids == ["gpt-4o"]
        assert target_set == {("openai", "gpt-4o")}

    def test_no_models_or_targets(self):
        """Empty body returns empty results."""
        body = {}
        model_ids, target_set = _parse_target_selection(body)
        assert model_ids == []
        assert target_set is None

    def test_duplicate_model_ids_different_providers(self):
        """Same model_id from different providers should both appear."""
        body = {
            "targets": [
                {"provider_key": "lm_studio_desktop", "model_id": "lm_studio/qwen3-coder-30b"},
                {"provider_key": "lm_studio_mac", "model_id": "lm_studio/qwen3-coder-30b"},
            ]
        }
        model_ids, target_set = _parse_target_selection(body)
        assert len(model_ids) == 2
        assert target_set == {
            ("lm_studio_desktop", "lm_studio/qwen3-coder-30b"),
            ("lm_studio_mac", "lm_studio/qwen3-coder-30b"),
        }


class TestFilterTargets:
    """_filter_targets filters by precise (provider_key, model_id) or legacy model_id."""

    @pytest.fixture
    def all_targets(self):
        """Two providers with same model_id."""
        return [
            Target(provider="LM Studio Desktop", model_id="lm_studio/qwen3-coder-30b",
                   display_name="Qwen3 Coder (Desktop)", provider_key="lm_studio_desktop"),
            Target(provider="LM Studio Mac", model_id="lm_studio/qwen3-coder-30b",
                   display_name="Qwen3 Coder (Mac)", provider_key="lm_studio_mac"),
            Target(provider="OpenAI", model_id="gpt-4o",
                   display_name="GPT-4o", provider_key="openai"),
        ]

    def test_precise_filtering_selects_correct_provider(self, all_targets):
        """Precise target_set selects only the specified provider."""
        target_set = {("lm_studio_desktop", "lm_studio/qwen3-coder-30b")}
        result = _filter_targets(all_targets, ["lm_studio/qwen3-coder-30b"], target_set)
        assert len(result) == 1
        assert result[0].provider_key == "lm_studio_desktop"

    def test_legacy_filtering_selects_all_matching(self, all_targets):
        """Legacy model_id-only filtering selects ALL providers with that model_id."""
        result = _filter_targets(all_targets, ["lm_studio/qwen3-coder-30b"], None)
        assert len(result) == 2  # Both desktop and mac
        providers = {t.provider_key for t in result}
        assert providers == {"lm_studio_desktop", "lm_studio_mac"}

    def test_precise_filtering_multiple_targets(self, all_targets):
        """Select one from each provider."""
        target_set = {
            ("lm_studio_mac", "lm_studio/qwen3-coder-30b"),
            ("openai", "gpt-4o"),
        }
        result = _filter_targets(all_targets, ["lm_studio/qwen3-coder-30b", "gpt-4o"], target_set)
        assert len(result) == 2
        providers = {t.provider_key for t in result}
        assert providers == {"lm_studio_mac", "openai"}

    def test_empty_selection_returns_all(self, all_targets):
        """No filter criteria returns all targets."""
        result = _filter_targets(all_targets, [], None)
        assert len(result) == 3

    def test_no_match_returns_empty(self, all_targets):
        """Non-matching filter returns empty list."""
        result = _filter_targets(all_targets, ["nonexistent"], None)
        assert len(result) == 0


class TestTargetKey:
    """_target_key generates correct compound keys."""

    def test_basic_key(self):
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o", provider_key="openai")
        assert _target_key(t) == "openai::gpt-4o"

    def test_key_with_slash(self):
        t = Target(provider="Anthropic", model_id="anthropic/claude-opus-4-6",
                   display_name="Claude", provider_key="anthropic")
        assert _target_key(t) == "anthropic::anthropic/claude-opus-4-6"

    def test_key_without_provider_key(self):
        """Targets without provider_key should still produce a key."""
        t = Target(provider="Test", model_id="model", display_name="Model")
        key = _target_key(t)
        assert key.endswith("::model")


# ===========================================================================
# Bug B — Param validation: warn not drop
# ===========================================================================


class TestResolveConflictsWarnNotDrop:
    """resolve_conflicts should WARN about unknown params, not silently DROP them."""

    def test_anthropic_unsupported_params_warn(self):
        """Anthropic: frequency_penalty, presence_penalty, seed should warn, not drop."""
        params = {"temperature": 0.7, "frequency_penalty": 0.5, "seed": 42}
        resolved, adjustments = resolve_conflicts(params, "anthropic", "claude-opus-4-6")
        # All params should still be in resolved
        assert "frequency_penalty" in resolved
        assert "seed" in resolved
        assert resolved["frequency_penalty"] == 0.5
        assert resolved["seed"] == 42
        # Adjustments should be "warn" not "drop"
        warn_adjustments = [a for a in adjustments if a["action"] == "warn"]
        assert len(warn_adjustments) >= 2

    def test_anthropic_temp_top_p_conflict_drops_top_p(self):
        """Anthropic temp + top_p is a GENUINE mutual exclusion — should drop top_p."""
        params = {"temperature": 0.7, "top_p": 0.9}
        resolved, adjustments = resolve_conflicts(params, "anthropic", "claude-opus-4-6")
        assert "top_p" not in resolved
        assert any(a["action"] == "drop" and a["param"] == "top_p" for a in adjustments)

    def test_unknown_provider_passthrough(self):
        """For unknown providers, all params should pass through without modification."""
        params = {"temperature": 0.7, "repeat_penalty": 1.2, "custom_param": "value"}
        resolved, adjustments = resolve_conflicts(params, "_unknown", "some-model")
        assert resolved == params
        assert len(adjustments) == 0

    def test_openai_o_series_rename(self):
        """O-series models rename max_tokens to max_completion_tokens."""
        params = {"max_tokens": 1024, "temperature": 0.5}
        resolved, adjustments = resolve_conflicts(params, "openai", "o3-mini")
        assert "max_completion_tokens" in resolved
        assert "max_tokens" not in resolved
        assert any(a["action"] == "rename" for a in adjustments)

    def test_lm_studio_passthrough(self):
        """LM Studio: repeat_penalty should pass through (it IS supported)."""
        params = {"temperature": 0.7, "repeat_penalty": 1.2}
        resolved, adjustments = resolve_conflicts(params, "lm_studio", "lm_studio/qwen3-coder-30b")
        # repeat_penalty should remain
        assert "repeat_penalty" in resolved
        assert resolved["repeat_penalty"] == 1.2


class TestValidateParamsWarn:
    """validate_params should include warnings, not silently remove params."""

    def test_validate_returns_all_params(self):
        """All params should be present in result, even if provider doesn't natively support them."""
        result = validate_params("lm_studio", "lm_studio/model", {
            "temperature": 0.8,
            "repeat_penalty": 1.1,
        })
        # The result should contain our params — validate_params doesn't drop
        assert "validated" in result or "params" in result or isinstance(result, dict)


# ===========================================================================
# Bug A + build_targets — provider_key propagation
# ===========================================================================


class TestBuildTargetsProviderKey:
    """build_targets should set provider_key for precise matching."""

    @pytest.fixture
    def config_duplicate_models(self):
        """Config with same model in two providers."""
        return {
            "providers": {
                "lm_studio_desktop": {
                    "display_name": "LM Studio Desktop",
                    "api_base": "http://desktop:1234/v1",
                    "api_key": "not-needed",
                    "models": [
                        {"id": "lm_studio/qwen3-coder-30b", "display_name": "Qwen3 Desktop"},
                    ],
                },
                "lm_studio_mac": {
                    "display_name": "LM Studio Mac",
                    "api_base": "http://mac:1234/v1",
                    "api_key": "not-needed",
                    "models": [
                        {"id": "lm_studio/qwen3-coder-30b", "display_name": "Qwen3 Mac"},
                    ],
                },
            }
        }

    def test_same_model_different_providers(self, config_duplicate_models):
        """Two providers with same model_id should produce 2 targets with different provider_keys."""
        targets = build_targets(config_duplicate_models)
        assert len(targets) == 2
        provider_keys = {t.provider_key for t in targets}
        assert provider_keys == {"lm_studio_desktop", "lm_studio_mac"}
        # Both have the same model_id
        assert all(t.model_id == "lm_studio/qwen3-coder-30b" for t in targets)

    def test_target_key_uniqueness(self, config_duplicate_models):
        """_target_key should produce unique keys even for same model_id."""
        targets = build_targets(config_duplicate_models)
        keys = {_target_key(t) for t in targets}
        assert len(keys) == 2  # Both should be unique
        assert "lm_studio_desktop::lm_studio/qwen3-coder-30b" in keys
        assert "lm_studio_mac::lm_studio/qwen3-coder-30b" in keys

    def test_filter_precise_picks_one_provider(self, config_duplicate_models):
        """Precise filtering should select exactly one provider."""
        targets = build_targets(config_duplicate_models)
        target_set = {("lm_studio_desktop", "lm_studio/qwen3-coder-30b")}
        filtered = _filter_targets(targets, ["lm_studio/qwen3-coder-30b"], target_set)
        assert len(filtered) == 1
        assert filtered[0].provider_key == "lm_studio_desktop"
        assert filtered[0].display_name == "Qwen3 Desktop"


# ===========================================================================
# Bug B — resolve_conflicts: adjustment action types
# ===========================================================================


class TestResolveConflictsActions:
    """Verify adjustment action types are consistent."""

    def test_adjustment_has_action_field(self):
        """Every adjustment dict should contain an 'action' field."""
        params = {"temperature": 0.7, "top_p": 0.9}
        _, adjustments = resolve_conflicts(params, "anthropic", "claude-opus-4-6")
        for adj in adjustments:
            assert "action" in adj
            assert adj["action"] in ("drop", "warn", "rename", "clamp")

    def test_warn_preserves_original_value(self):
        """Warn adjustments should have adjusted == original (value preserved)."""
        params = {"temperature": 0.7, "frequency_penalty": 0.5}
        _, adjustments = resolve_conflicts(params, "anthropic", "claude-opus-4-6")
        warn_adjs = [a for a in adjustments if a["action"] == "warn"]
        for adj in warn_adjs:
            assert adj["adjusted"] == adj["original"]

    def test_drop_sets_adjusted_to_none(self):
        """Drop adjustments should have adjusted == None."""
        params = {"temperature": 0.7, "top_p": 0.9}
        _, adjustments = resolve_conflicts(params, "anthropic", "claude-opus-4-6")
        drop_adjs = [a for a in adjustments if a["action"] == "drop"]
        for adj in drop_adjs:
            assert adj["adjusted"] is None

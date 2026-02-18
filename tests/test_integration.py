"""Integration tests for provider_key matching in target selection.

These tests verify that the compound key (provider_key::model_id) flow
works correctly end-to-end: from frontend target construction through
backend parsing and filtering.

The root cause these tests guard against:
- /api/config returns providers keyed by DISPLAY_NAME (e.g., "LM Studio (Desktop)")
- build_targets() sets Target.provider_key to the CONFIG KEY (e.g., "lm_studio_desktop")
- If the frontend sends display_name as provider_key, _filter_targets() won't match
"""

import pytest

from app import _parse_target_selection, _filter_targets, _target_key
from benchmark import Target, build_targets


# ── Fixtures ───────────────────────────────────────────────────────


def _make_target(provider_key: str, model_id: str, provider: str = "Test") -> Target:
    """Create a minimal Target for testing."""
    return Target(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        api_base=None,
        api_key="test-key",
        provider_key=provider_key,
    )


SAMPLE_CONFIG = {
    "defaults": {"max_tokens": 4096, "temperature": 0.7},
    "providers": {
        "lm_studio_desktop": {
            "display_name": "LM Studio (Desktop)",
            "api_base": "http://192.168.31.222:1234/v1",
            "api_key": "lm-studio",
            "models": [
                {"id": "qwen3-coder-30b", "display_name": "Qwen3 Coder 30B"},
                {"id": "glm-4.7-9b", "display_name": "GLM 4.7 9B"},
            ],
        },
        "openai": {
            "display_name": "OpenAI",
            "api_key_env": "OPENAI_API_KEY",
            "models": [
                {"id": "gpt-4o", "display_name": "GPT-4o"},
            ],
        },
    },
}


# ── _parse_target_selection ────────────────────────────────────────


class TestParseTargetSelection:
    """Tests for _parse_target_selection()."""

    def test_new_format_with_targets(self):
        """New format: targets array with provider_key + model_id."""
        body = {
            "targets": [
                {"provider_key": "lm_studio_desktop", "model_id": "qwen3-coder-30b"},
                {"provider_key": "openai", "model_id": "gpt-4o"},
            ]
        }
        model_ids, target_set = _parse_target_selection(body)
        assert set(model_ids) == {"qwen3-coder-30b", "gpt-4o"}
        assert target_set == {
            ("lm_studio_desktop", "qwen3-coder-30b"),
            ("openai", "gpt-4o"),
        }

    def test_legacy_format_with_models(self):
        """Legacy format: flat list of model_ids."""
        body = {"models": ["qwen3-coder-30b", "gpt-4o"]}
        model_ids, target_set = _parse_target_selection(body)
        assert set(model_ids) == {"qwen3-coder-30b", "gpt-4o"}
        assert target_set is None

    def test_empty_targets_falls_back_to_legacy(self):
        """Empty targets list should fall back to legacy models."""
        body = {"targets": [], "models": ["gpt-4o"]}
        model_ids, target_set = _parse_target_selection(body)
        assert model_ids == ["gpt-4o"]
        assert target_set is None

    def test_no_targets_or_models(self):
        """No selection at all."""
        model_ids, target_set = _parse_target_selection({})
        assert model_ids == []
        assert target_set is None

    def test_targets_with_invalid_entries_ignored(self):
        """Invalid entries in targets are skipped."""
        body = {
            "targets": [
                {"provider_key": "openai", "model_id": "gpt-4o"},
                {"bad_field": "value"},  # Missing required fields
                "just-a-string",  # Not a dict
            ]
        }
        model_ids, target_set = _parse_target_selection(body)
        assert model_ids == ["gpt-4o"]
        assert target_set == {("openai", "gpt-4o")}


# ── _filter_targets ────────────────────────────────────────────────


class TestFilterTargets:
    """Tests for _filter_targets() — the critical matching function."""

    def setup_method(self):
        """Create a set of targets as build_targets() would produce."""
        self.targets = [
            _make_target("lm_studio_desktop", "qwen3-coder-30b", "LM Studio (Desktop)"),
            _make_target("lm_studio_desktop", "glm-4.7-9b", "LM Studio (Desktop)"),
            _make_target("openai", "gpt-4o", "OpenAI"),
        ]

    def test_filter_with_config_key_matches(self):
        """Using config key (correct) should match targets."""
        target_set = {("lm_studio_desktop", "qwen3-coder-30b")}
        result = _filter_targets(self.targets, ["qwen3-coder-30b"], target_set)
        assert len(result) == 1
        assert result[0].model_id == "qwen3-coder-30b"
        assert result[0].provider_key == "lm_studio_desktop"

    def test_filter_with_display_name_does_not_match(self):
        """Using display name (wrong) should NOT match any targets.

        This is the exact bug: frontend was sending display_name as
        provider_key, but build_targets() uses config key.
        """
        target_set = {("LM Studio (Desktop)", "qwen3-coder-30b")}
        result = _filter_targets(self.targets, ["qwen3-coder-30b"], target_set)
        assert len(result) == 0, (
            "Display name should NOT match — Target.provider_key is the config key"
        )

    def test_filter_multiple_providers(self):
        """Filter targets from multiple providers simultaneously."""
        target_set = {
            ("lm_studio_desktop", "qwen3-coder-30b"),
            ("openai", "gpt-4o"),
        }
        result = _filter_targets(
            self.targets, ["qwen3-coder-30b", "gpt-4o"], target_set
        )
        assert len(result) == 2
        provider_keys = {t.provider_key for t in result}
        assert provider_keys == {"lm_studio_desktop", "openai"}

    def test_filter_legacy_mode_matches_by_model_id(self):
        """Legacy mode (target_set=None) matches by model_id only."""
        result = _filter_targets(self.targets, ["gpt-4o"], None)
        assert len(result) == 1
        assert result[0].model_id == "gpt-4o"

    def test_filter_no_selection_returns_all(self):
        """No model_ids and no target_set returns all targets."""
        result = _filter_targets(self.targets, [], None)
        assert len(result) == 3

    def test_filter_same_model_id_different_providers(self):
        """Two providers with the same model_id — compound key disambiguates."""
        targets = [
            _make_target("provider_a", "shared-model", "Provider A"),
            _make_target("provider_b", "shared-model", "Provider B"),
        ]
        # Select only from provider_a
        target_set = {("provider_a", "shared-model")}
        result = _filter_targets(targets, ["shared-model"], target_set)
        assert len(result) == 1
        assert result[0].provider_key == "provider_a"


# ── _target_key ────────────────────────────────────────────────────


class TestTargetKey:
    """Tests for _target_key() used in validated_target_combos indexing."""

    def test_standard_key(self):
        t = _make_target("openai", "gpt-4o")
        assert _target_key(t) == "openai::gpt-4o"

    def test_none_provider_key(self):
        t = _make_target(None, "gpt-4o")
        assert _target_key(t) == "::gpt-4o"

    def test_empty_provider_key(self):
        t = _make_target("", "gpt-4o")
        assert _target_key(t) == "::gpt-4o"


# ── build_targets → _filter_targets end-to-end ────────────────────


class TestEndToEndTargetMatching:
    """End-to-end: build_targets produces targets, then filter with
    the compound keys that the (fixed) frontend would send."""

    def test_build_targets_sets_config_key_as_provider_key(self):
        """build_targets() must set provider_key to the config key, not display_name."""
        targets = build_targets(SAMPLE_CONFIG)
        for t in targets:
            assert t.provider_key in ("lm_studio_desktop", "openai"), (
                f"Expected config key, got: {t.provider_key}"
            )
            # provider_key should NOT be the display_name
            assert t.provider_key != "LM Studio (Desktop)"

    def test_frontend_correct_compound_key_matches(self):
        """Simulate fixed frontend: send config key in targets."""
        targets = build_targets(SAMPLE_CONFIG)
        # Frontend (fixed) sends: provider_key = provData.provider_key = config key
        body = {
            "targets": [
                {"provider_key": "lm_studio_desktop", "model_id": "qwen3-coder-30b"}
            ]
        }
        model_ids, target_set = _parse_target_selection(body)
        filtered = _filter_targets(targets, model_ids, target_set)
        assert len(filtered) == 1
        assert filtered[0].model_id == "qwen3-coder-30b"
        assert filtered[0].provider_key == "lm_studio_desktop"

    def test_frontend_broken_compound_key_no_match(self):
        """Simulate broken frontend: send display_name in targets.

        This is the bug this fix resolves — should produce 0 matches.
        """
        targets = build_targets(SAMPLE_CONFIG)
        # Frontend (broken) sends: provider_key = display_name from iteration key
        body = {
            "targets": [
                {"provider_key": "LM Studio (Desktop)", "model_id": "qwen3-coder-30b"}
            ]
        }
        model_ids, target_set = _parse_target_selection(body)
        filtered = _filter_targets(targets, model_ids, target_set)
        assert len(filtered) == 0, (
            "Display name as provider_key should NOT match build_targets() output"
        )

    def test_all_models_from_config_match(self):
        """All models built from config should match when using correct keys."""
        targets = build_targets(SAMPLE_CONFIG)
        # Build targets list as fixed frontend would
        frontend_targets = [
            {"provider_key": t.provider_key, "model_id": t.model_id}
            for t in targets
        ]
        body = {"targets": frontend_targets}
        model_ids, target_set = _parse_target_selection(body)
        filtered = _filter_targets(targets, model_ids, target_set)
        assert len(filtered) == len(targets)

    def test_provider_with_matching_display_name_and_key(self):
        """Provider where display_name matches config key (e.g., 'openai')."""
        targets = build_targets(SAMPLE_CONFIG)
        body = {
            "targets": [{"provider_key": "openai", "model_id": "gpt-4o"}]
        }
        model_ids, target_set = _parse_target_selection(body)
        filtered = _filter_targets(targets, model_ids, target_set)
        assert len(filtered) == 1
        assert filtered[0].provider_key == "openai"

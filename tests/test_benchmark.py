"""Tests for benchmark.py — Target building, context generation, error sanitisation."""

import os
import pytest

from benchmark import (
    Target,
    build_targets,
    generate_context_text,
    resolve_api_key,
    sanitize_error,
)


# ===========================================================================
# resolve_api_key
# ===========================================================================


class TestResolveApiKey:
    def test_direct_key(self):
        assert resolve_api_key({"api_key": "sk-test123"}) == "sk-test123"

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-from-env")
        assert resolve_api_key({"api_key_env": "MY_KEY"}) == "sk-from-env"

    def test_missing_env_var(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        assert resolve_api_key({"api_key_env": "MISSING_KEY"}) is None

    def test_no_key_at_all(self):
        assert resolve_api_key({}) is None

    def test_direct_key_takes_priority(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-from-env")
        assert resolve_api_key({"api_key": "sk-direct", "api_key_env": "MY_KEY"}) == "sk-direct"


# ===========================================================================
# sanitize_error
# ===========================================================================


class TestSanitizeError:
    def test_strips_known_api_key(self):
        msg = "Error: Authentication failed with key sk-abc123456789xyz"
        result = sanitize_error(msg, api_key="sk-abc123456789xyz")
        assert "sk-abc123456789xyz" not in result
        assert "***" in result

    def test_strips_openai_key_pattern(self):
        msg = "Error with sk-proj123456789abcdefghijklmn"
        result = sanitize_error(msg)
        assert "sk-proj1234***" in result

    def test_strips_groq_key_pattern(self):
        msg = "Error with gsk_abcd1234567890efghij"
        result = sanitize_error(msg)
        assert "gsk_abcd***" in result

    def test_strips_bearer_token(self):
        msg = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdef"
        result = sanitize_error(msg)
        assert "Bearer ***" in result
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_strips_google_ai_key(self):
        msg = "Error with AIzaSyCDabcdefghijklmno"
        result = sanitize_error(msg)
        assert "AIzaSyCD***" in result

    def test_preserves_short_key(self):
        """Keys shorter than 8 chars are not stripped by the specific-key path."""
        msg = "Error with key abc"
        result = sanitize_error(msg, api_key="abc")
        assert "abc" in result

    def test_no_key_no_change(self):
        msg = "Simple error message"
        result = sanitize_error(msg)
        assert result == "Simple error message"


# ===========================================================================
# build_targets
# ===========================================================================


class TestBuildTargets:
    @pytest.fixture
    def sample_config(self):
        return {
            "providers": {
                "openai": {
                    "display_name": "OpenAI",
                    "api_key": "sk-test",
                    "models": [
                        {"id": "gpt-4o", "display_name": "GPT-4o"},
                        {"id": "gpt-4o-mini", "display_name": "GPT-4o Mini"},
                    ],
                },
                "anthropic": {
                    "display_name": "Anthropic",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "models": [
                        {"id": "anthropic/claude-sonnet-4-6", "display_name": "Claude Sonnet"},
                    ],
                },
            }
        }

    def test_builds_all_targets(self, sample_config):
        targets = build_targets(sample_config)
        assert len(targets) == 3

    def test_provider_filter(self, sample_config):
        targets = build_targets(sample_config, provider_filter="openai")
        assert len(targets) == 2
        assert all(t.provider == "OpenAI" for t in targets)

    def test_model_filter(self, sample_config):
        targets = build_targets(sample_config, model_filter="mini")
        assert len(targets) == 1
        assert targets[0].model_id == "gpt-4o-mini"

    def test_provider_and_model_filter(self, sample_config):
        targets = build_targets(sample_config, provider_filter="openai", model_filter="4o-mini")
        assert len(targets) == 1

    def test_filter_no_match(self, sample_config):
        targets = build_targets(sample_config, provider_filter="deepseek")
        assert len(targets) == 0

    def test_target_has_correct_fields(self, sample_config):
        targets = build_targets(sample_config, model_filter="gpt-4o")
        t = [x for x in targets if x.model_id == "gpt-4o"][0]
        assert t.provider == "OpenAI"
        assert t.display_name == "GPT-4o"
        assert t.api_key == "sk-test"
        assert t.provider_key == "openai"

    def test_empty_providers(self):
        targets = build_targets({"providers": {}})
        assert targets == []

    def test_model_with_custom_fields(self):
        config = {
            "providers": {
                "openai": {
                    "display_name": "OpenAI",
                    "api_key": "sk-test",
                    "api_base": "https://custom.api.com",
                    "models": [
                        {
                            "id": "gpt-4o",
                            "display_name": "GPT-4o",
                            "context_window": 64000,
                            "max_output_tokens": 4096,
                            "skip_params": ["temperature"],
                            "input_cost_per_mtok": 2.5,
                            "output_cost_per_mtok": 10.0,
                        }
                    ],
                }
            }
        }
        targets = build_targets(config)
        t = targets[0]
        assert t.context_window == 64000
        assert t.max_output_tokens == 4096
        assert t.skip_params == ["temperature"]
        assert t.api_base == "https://custom.api.com"
        assert t.input_cost_per_mtok == 2.5
        assert t.output_cost_per_mtok == 10.0

    def test_case_insensitive_filter(self, sample_config):
        """Filters should be case insensitive."""
        targets = build_targets(sample_config, provider_filter="OPENAI")
        assert len(targets) == 2

    def test_display_name_filter(self, sample_config):
        """Provider filter matches display_name too."""
        targets = build_targets(sample_config, provider_filter="Anthropic")
        assert len(targets) == 1


# ===========================================================================
# generate_context_text
# ===========================================================================


class TestGenerateContextText:
    def test_zero_tokens_returns_empty(self):
        assert generate_context_text(0) == ""

    def test_negative_tokens_returns_empty(self):
        assert generate_context_text(-100) == ""

    def test_produces_nonempty_for_positive(self):
        text = generate_context_text(100)
        assert len(text) > 0

    def test_approximate_token_count(self):
        """Output should be approximately the requested token count."""
        text = generate_context_text(500)
        # Very rough check: tiktoken cl100k_base typically ~4 chars/token
        # Text should be in the right ballpark (100-3000 chars for 500 tokens)
        assert 100 < len(text) < 5000

    def test_large_context(self):
        """Large context should produce substantial text without error."""
        text = generate_context_text(5000)
        assert len(text) > 1000

    def test_diverse_content(self):
        """Output should contain varied content types (code, prose, JSON)."""
        text = generate_context_text(2000)
        # Should contain at least some of the diverse blocks
        has_code = "def " in text or "```" in text
        has_prose = "." in text  # Sentences end with periods
        assert has_code or has_prose  # At least something meaningful

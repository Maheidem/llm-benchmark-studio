"""Tests for staging fixes — combo dedup, system_prompt on Target, system_prompt PREPEND strategy, config model update.

Issue #1: Combo count dedup — validate_params() clamps/drops params causing raw combos to collapse.
Issue #8: System prompt on Target — per-model system_prompt in dataclass, build_targets, run_single messages,
          PREPEND strategy in eval, multi-turn eval, async_run_single, and PUT /api/config/model handling.
"""

import pytest

from benchmark import Target, build_targets, RunResult
from provider_params import identify_provider, validate_params
from app import _expand_search_space


# ===========================================================================
# Issue #8 — Target dataclass: system_prompt field
# ===========================================================================


class TestTargetSystemPrompt:
    """Target dataclass should support an optional system_prompt field."""

    def test_default_is_none(self):
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o")
        assert t.system_prompt is None

    def test_set_system_prompt(self):
        t = Target(
            provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o",
            system_prompt="You are a helpful assistant.",
        )
        assert t.system_prompt == "You are a helpful assistant."

    def test_system_prompt_empty_string(self):
        t = Target(
            provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o",
            system_prompt="",
        )
        assert t.system_prompt == ""

    def test_system_prompt_does_not_affect_other_defaults(self):
        t = Target(
            provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o",
            system_prompt="Be concise.",
        )
        assert t.context_window == 128000
        assert t.api_base is None
        assert t.skip_params is None
        assert t.input_cost_per_mtok is None


# ===========================================================================
# Issue #8 — build_targets reads system_prompt from model config
# ===========================================================================


class TestBuildTargetsSystemPrompt:
    """build_targets() should propagate system_prompt from config to Target."""

    @pytest.fixture
    def config_with_system_prompt(self):
        return {
            "providers": {
                "openai": {
                    "display_name": "OpenAI",
                    "api_key": "sk-test",
                    "models": [
                        {
                            "id": "gpt-4o",
                            "display_name": "GPT-4o",
                            "system_prompt": "You are a coding assistant.",
                        },
                    ],
                }
            }
        }

    @pytest.fixture
    def config_without_system_prompt(self):
        return {
            "providers": {
                "openai": {
                    "display_name": "OpenAI",
                    "api_key": "sk-test",
                    "models": [
                        {"id": "gpt-4o", "display_name": "GPT-4o"},
                    ],
                }
            }
        }

    @pytest.fixture
    def config_mixed_models(self):
        return {
            "providers": {
                "openai": {
                    "display_name": "OpenAI",
                    "api_key": "sk-test",
                    "models": [
                        {
                            "id": "gpt-4o",
                            "display_name": "GPT-4o",
                            "system_prompt": "Be helpful.",
                        },
                        {
                            "id": "gpt-4o-mini",
                            "display_name": "GPT-4o Mini",
                        },
                    ],
                }
            }
        }

    def test_system_prompt_present(self, config_with_system_prompt):
        targets = build_targets(config_with_system_prompt)
        assert len(targets) == 1
        assert targets[0].system_prompt == "You are a coding assistant."

    def test_system_prompt_absent_defaults_none(self, config_without_system_prompt):
        targets = build_targets(config_without_system_prompt)
        assert len(targets) == 1
        assert targets[0].system_prompt is None

    def test_mixed_models_some_have_system_prompt(self, config_mixed_models):
        targets = build_targets(config_mixed_models)
        assert len(targets) == 2
        by_id = {t.model_id: t for t in targets}
        assert by_id["gpt-4o"].system_prompt == "Be helpful."
        assert by_id["gpt-4o-mini"].system_prompt is None

    def test_system_prompt_with_other_fields(self):
        config = {
            "providers": {
                "openai": {
                    "display_name": "OpenAI",
                    "api_key": "sk-test",
                    "models": [
                        {
                            "id": "gpt-4o",
                            "display_name": "GPT-4o",
                            "context_window": 64000,
                            "system_prompt": "Follow instructions carefully.",
                            "input_cost_per_mtok": 2.5,
                        },
                    ],
                }
            }
        }
        targets = build_targets(config)
        t = targets[0]
        assert t.system_prompt == "Follow instructions carefully."
        assert t.context_window == 64000
        assert t.input_cost_per_mtok == 2.5


# ===========================================================================
# Issue #8 — run_single message construction with system_prompt
# ===========================================================================


class TestRunSingleMessageConstruction:
    """Verify the message construction logic from run_single().

    We test the algorithm directly rather than calling run_single (which calls LiteLLM).
    The logic is: if system_prompt and context_tokens>0, combine them in one system message;
    if system_prompt only, add system message alone; if context only, system message with context.
    """

    def _build_messages(self, target: Target, prompt: str, context_tokens: int = 0):
        """Replicate the message construction logic from run_single() / async_run_single()."""
        messages = []
        if target.system_prompt:
            if context_tokens > 0:
                # Simulate: would call generate_context_text(context_tokens)
                context_text = f"<context:{context_tokens}>"
                messages.append({"role": "system", "content": target.system_prompt + "\n\n" + context_text})
            else:
                messages.append({"role": "system", "content": target.system_prompt})
        elif context_tokens > 0:
            context_text = f"<context:{context_tokens}>"
            messages.append({"role": "system", "content": context_text})
        messages.append({"role": "user", "content": prompt})
        return messages

    def test_no_system_prompt_no_context(self):
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o")
        msgs = self._build_messages(t, "Hello")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"

    def test_system_prompt_no_context(self):
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o",
                   system_prompt="Be concise.")
        msgs = self._build_messages(t, "Hello")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "Be concise."
        assert msgs[1]["role"] == "user"

    def test_system_prompt_with_context(self):
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o",
                   system_prompt="Be concise.")
        msgs = self._build_messages(t, "Hello", context_tokens=1000)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert "Be concise." in msgs[0]["content"]
        assert "<context:1000>" in msgs[0]["content"]
        # Should be combined with \n\n separator
        assert msgs[0]["content"] == "Be concise.\n\n<context:1000>"

    def test_no_system_prompt_with_context(self):
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o")
        msgs = self._build_messages(t, "Hello", context_tokens=5000)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "<context:5000>"
        assert msgs[1]["role"] == "user"

    def test_empty_string_system_prompt_treated_as_falsy(self):
        """Empty string system_prompt is falsy, so no system message should be added."""
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o",
                   system_prompt="")
        msgs = self._build_messages(t, "Hello")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"


# ===========================================================================
# Issue #8 — PREPEND strategy for run_single_eval
# ===========================================================================


class TestSystemPromptPrependStrategy:
    """The PREPEND strategy in run_single_eval combines per-model system_prompt (from config)
    with an explicit system_prompt (from prompt tuner) by prepending the model one first.
    """

    def _combine_system_prompts(self, model_system_prompt, explicit_system_prompt):
        """Replicate the PREPEND logic from run_single_eval."""
        combined_system = ""
        if model_system_prompt:
            combined_system = model_system_prompt
        if explicit_system_prompt:
            combined_system = (combined_system + "\n\n" + explicit_system_prompt) if combined_system else explicit_system_prompt
        return combined_system

    def test_both_prompts(self):
        result = self._combine_system_prompts("Model instructions.", "Tuner prompt.")
        assert result == "Model instructions.\n\nTuner prompt."

    def test_model_prompt_only(self):
        result = self._combine_system_prompts("Model instructions.", None)
        assert result == "Model instructions."

    def test_explicit_prompt_only(self):
        result = self._combine_system_prompts(None, "Tuner prompt.")
        assert result == "Tuner prompt."

    def test_neither_prompt(self):
        result = self._combine_system_prompts(None, None)
        assert result == ""

    def test_model_prompt_empty_string(self):
        """Empty string model prompt is falsy — only explicit prompt should appear."""
        result = self._combine_system_prompts("", "Tuner prompt.")
        assert result == "Tuner prompt."

    def test_explicit_prompt_empty_string(self):
        """Empty string explicit prompt is falsy — only model prompt should appear."""
        result = self._combine_system_prompts("Model instructions.", "")
        assert result == "Model instructions."

    def test_both_empty(self):
        result = self._combine_system_prompts("", "")
        assert result == ""

    def test_model_prepended_before_explicit(self):
        """Model system_prompt always comes first (PREPEND strategy)."""
        result = self._combine_system_prompts("FIRST", "SECOND")
        assert result.index("FIRST") < result.index("SECOND")

    def test_separator_is_double_newline(self):
        result = self._combine_system_prompts("A", "B")
        assert result == "A\n\nB"

    def test_multiline_model_prompt(self):
        model = "Line 1\nLine 2"
        explicit = "Extra instructions."
        result = self._combine_system_prompts(model, explicit)
        assert result == "Line 1\nLine 2\n\nExtra instructions."


# ===========================================================================
# Issue #8 — run_multi_turn_eval system_prompt prepend
# ===========================================================================


class TestMultiTurnEvalSystemPrompt:
    """In run_multi_turn_eval, system_prompt is prepended as a separate system message."""

    def _build_multi_turn_messages(self, target: Target, user_prompt: str):
        """Replicate the multi-turn message construction logic."""
        messages = []
        if target.system_prompt:
            messages.append({"role": "system", "content": target.system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def test_with_system_prompt(self):
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o",
                   system_prompt="Use tools efficiently.")
        msgs = self._build_multi_turn_messages(t, "Book a flight")
        assert len(msgs) == 2
        assert msgs[0] == {"role": "system", "content": "Use tools efficiently."}
        assert msgs[1] == {"role": "user", "content": "Book a flight"}

    def test_without_system_prompt(self):
        t = Target(provider="OpenAI", model_id="gpt-4o", display_name="GPT-4o")
        msgs = self._build_multi_turn_messages(t, "Book a flight")
        assert len(msgs) == 1
        assert msgs[0] == {"role": "user", "content": "Book a flight"}


# ===========================================================================
# Issue #8 — PUT /api/config/model system_prompt handling logic
# ===========================================================================


class TestConfigModelSystemPromptUpdate:
    """PUT /api/config/model stores non-empty system_prompt, removes empty/null."""

    def _apply_system_prompt_update(self, model: dict, body: dict) -> dict:
        """Replicate the system_prompt handling from PUT /api/config/model.

        If body contains system_prompt:
          - non-empty string -> stores stripped value
          - empty string or None -> removes the key
        """
        model = dict(model)  # Don't mutate input
        if "system_prompt" in body:
            sp_val = body["system_prompt"]
            if sp_val and isinstance(sp_val, str) and sp_val.strip():
                model["system_prompt"] = sp_val.strip()
            else:
                model.pop("system_prompt", None)
        return model

    def test_set_system_prompt(self):
        model = {"id": "gpt-4o", "display_name": "GPT-4o"}
        result = self._apply_system_prompt_update(model, {"system_prompt": "Be helpful."})
        assert result["system_prompt"] == "Be helpful."

    def test_set_system_prompt_strips_whitespace(self):
        model = {"id": "gpt-4o", "display_name": "GPT-4o"}
        result = self._apply_system_prompt_update(model, {"system_prompt": "  Be helpful.  "})
        assert result["system_prompt"] == "Be helpful."

    def test_clear_with_empty_string(self):
        model = {"id": "gpt-4o", "display_name": "GPT-4o", "system_prompt": "Old prompt."}
        result = self._apply_system_prompt_update(model, {"system_prompt": ""})
        assert "system_prompt" not in result

    def test_clear_with_none(self):
        model = {"id": "gpt-4o", "display_name": "GPT-4o", "system_prompt": "Old prompt."}
        result = self._apply_system_prompt_update(model, {"system_prompt": None})
        assert "system_prompt" not in result

    def test_clear_with_whitespace_only(self):
        model = {"id": "gpt-4o", "display_name": "GPT-4o", "system_prompt": "Old prompt."}
        result = self._apply_system_prompt_update(model, {"system_prompt": "   "})
        assert "system_prompt" not in result

    def test_no_system_prompt_in_body_leaves_unchanged(self):
        model = {"id": "gpt-4o", "display_name": "GPT-4o", "system_prompt": "Keep me."}
        result = self._apply_system_prompt_update(model, {"display_name": "GPT-4o Updated"})
        assert result["system_prompt"] == "Keep me."

    def test_no_system_prompt_in_body_no_existing(self):
        model = {"id": "gpt-4o", "display_name": "GPT-4o"}
        result = self._apply_system_prompt_update(model, {"display_name": "GPT-4o Updated"})
        assert "system_prompt" not in result

    def test_replace_existing_prompt(self):
        model = {"id": "gpt-4o", "display_name": "GPT-4o", "system_prompt": "Old."}
        result = self._apply_system_prompt_update(model, {"system_prompt": "New."})
        assert result["system_prompt"] == "New."


# ===========================================================================
# Issue #1 — Combo dedup logic
# ===========================================================================


class TestComboDedupAlgorithm:
    """Pre-validation and dedup of combos per target.

    The algorithm: for each target, expand raw combos, run validate_params() on each,
    then dedup by (tool_choice, sorted resolved params). Two raw combos that resolve to
    the same validated state count as one.
    """

    def _dedup_combos(self, model_id: str, provider_key: str, combos: list[dict]):
        """Replicate the combo dedup logic from _param_tune_handler."""
        prov_key = identify_provider(model_id, provider_key)
        seen: set[tuple] = set()
        unique: list[tuple[dict, dict, list[dict]]] = []
        for combo in combos:
            temp = float(combo.get("temperature", 0.0))
            pp = {k: v for k, v in combo.items() if k not in ("temperature", "tool_choice", "max_tokens")}
            params_to_check = {"temperature": temp, **pp}
            validation = validate_params(prov_key, model_id, params_to_check)
            resolved = validation["resolved_params"]
            adjustments = validation.get("adjustments", [])
            tc = combo.get("tool_choice", "required")
            dedup_key = (tc,) + tuple(sorted(resolved.items()))
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique.append((combo, resolved, adjustments))
        return unique

    def test_no_duplicates_remain_unchanged(self):
        """Distinct combos should all survive dedup."""
        combos = [
            {"temperature": 0.0, "tool_choice": "auto"},
            {"temperature": 0.5, "tool_choice": "auto"},
            {"temperature": 1.0, "tool_choice": "auto"},
        ]
        unique = self._dedup_combos("gpt-4o", "openai", combos)
        assert len(unique) == 3

    def test_identical_combos_deduped(self):
        """Exact duplicate combos should collapse to one."""
        combos = [
            {"temperature": 0.7, "tool_choice": "required"},
            {"temperature": 0.7, "tool_choice": "required"},
            {"temperature": 0.7, "tool_choice": "required"},
        ]
        unique = self._dedup_combos("gpt-4o", "openai", combos)
        assert len(unique) == 1

    def test_anthropic_temp_clamping_causes_dedup(self):
        """Anthropic clamps temp to max 1.0, so 1.0, 1.5, 2.0 all resolve to 1.0."""
        combos = [
            {"temperature": 1.0, "tool_choice": "auto"},
            {"temperature": 1.5, "tool_choice": "auto"},
            {"temperature": 2.0, "tool_choice": "auto"},
        ]
        unique = self._dedup_combos("anthropic/claude-sonnet-4-6", "anthropic", combos)
        # All three should clamp to temp=1.0, so only 1 unique combo
        assert len(unique) == 1
        # The resolved temperature should be 1.0
        assert unique[0][1]["temperature"] == 1.0

    def test_gpt5_temp_lock_causes_dedup(self):
        """GPT-5 locks temperature to 1.0, so all temp values collapse."""
        combos = [
            {"temperature": 0.0, "tool_choice": "required"},
            {"temperature": 0.5, "tool_choice": "required"},
            {"temperature": 1.0, "tool_choice": "required"},
        ]
        unique = self._dedup_combos("gpt-5", "openai", combos)
        assert len(unique) == 1
        assert unique[0][1]["temperature"] == 1.0

    def test_different_tool_choice_not_deduped(self):
        """Same resolved params but different tool_choice should be separate."""
        combos = [
            {"temperature": 0.7, "tool_choice": "auto"},
            {"temperature": 0.7, "tool_choice": "required"},
        ]
        unique = self._dedup_combos("gpt-4o", "openai", combos)
        assert len(unique) == 2

    def test_default_tool_choice_is_required(self):
        """Missing tool_choice defaults to 'required' in dedup key."""
        combos = [
            {"temperature": 0.7},  # defaults to tool_choice="required"
            {"temperature": 0.7, "tool_choice": "required"},  # explicit required
        ]
        unique = self._dedup_combos("gpt-4o", "openai", combos)
        # Both should have same dedup key
        assert len(unique) == 1

    def test_anthropic_warns_unsupported_params(self):
        """Anthropic warns but passes through frequency_penalty (warn-not-drop).
        Combos differing in frequency_penalty remain distinct."""
        combos = [
            {"temperature": 0.7, "frequency_penalty": 0.0, "tool_choice": "auto"},
            {"temperature": 0.7, "frequency_penalty": 0.5, "tool_choice": "auto"},
            {"temperature": 0.7, "frequency_penalty": 1.0, "tool_choice": "auto"},
        ]
        unique = self._dedup_combos("anthropic/claude-sonnet-4-6", "anthropic", combos)
        # frequency_penalty passes through (warn, not drop) so all three stay distinct
        assert len(unique) == 3

    def test_openai_warns_top_k(self):
        """OpenAI warns but passes through top_k (warn-not-drop).
        Combos differing in top_k remain distinct."""
        combos = [
            {"temperature": 0.7, "top_k": 10, "tool_choice": "required"},
            {"temperature": 0.7, "top_k": 20, "tool_choice": "required"},
            {"temperature": 0.7, "top_k": 50, "tool_choice": "required"},
        ]
        unique = self._dedup_combos("gpt-4o", "openai", combos)
        # top_k passes through (warn, not drop) so all three stay distinct
        assert len(unique) == 3

    def test_o_series_temp_lock_plus_max_tokens_conversion(self):
        """O-series models lock temp to 1.0 AND convert max_tokens.
        Since max_tokens is excluded from params_to_check, only temp matters."""
        combos = [
            {"temperature": 0.0, "tool_choice": "required"},
            {"temperature": 0.5, "tool_choice": "required"},
            {"temperature": 1.0, "tool_choice": "required"},
            {"temperature": 1.5, "tool_choice": "required"},
        ]
        unique = self._dedup_combos("o3-mini", "openai", combos)
        # All temps lock to 1.0, so only 1 unique combo
        assert len(unique) == 1

    def test_preserves_original_combo(self):
        """Each unique entry should store the original combo as first element."""
        combos = [
            {"temperature": 1.5, "tool_choice": "auto"},
        ]
        unique = self._dedup_combos("anthropic/claude-sonnet-4-6", "anthropic", combos)
        assert len(unique) == 1
        original, resolved, adjustments = unique[0]
        assert original["temperature"] == 1.5  # original preserved
        assert resolved["temperature"] == 1.0  # resolved was clamped

    def test_adjustments_populated_when_clamped(self):
        """Adjustments list should be non-empty when params are clamped."""
        combos = [
            {"temperature": 1.5, "tool_choice": "auto"},
        ]
        unique = self._dedup_combos("anthropic/claude-sonnet-4-6", "anthropic", combos)
        _, _, adjustments = unique[0]
        assert len(adjustments) > 0
        assert any(a["param"] == "temperature" for a in adjustments)

    def test_adjustments_empty_when_no_clamping(self):
        """Adjustments should be empty when params are within valid range."""
        combos = [
            {"temperature": 0.7, "tool_choice": "auto"},
        ]
        unique = self._dedup_combos("gpt-4o", "openai", combos)
        _, _, adjustments = unique[0]
        assert len(adjustments) == 0


# ===========================================================================
# Issue #1 — Total combo count accuracy
# ===========================================================================


class TestTotalComboCount:
    """total_combos = sum of unique validated combos per target."""

    def _compute_total_combos(self, targets_spec: list[dict], combos: list[dict]):
        """Simulate the total_combos calculation from _param_tune_handler.

        targets_spec: list of {"model_id": str, "provider_key": str}
        """
        validated_target_combos = {}
        for spec in targets_spec:
            model_id = spec["model_id"]
            provider_key = spec["provider_key"]
            prov_key = identify_provider(model_id, provider_key)
            seen = set()
            unique = []
            for combo in combos:
                temp = float(combo.get("temperature", 0.0))
                pp = {k: v for k, v in combo.items() if k not in ("temperature", "tool_choice", "max_tokens")}
                params_to_check = {"temperature": temp, **pp}
                validation = validate_params(prov_key, model_id, params_to_check)
                resolved = validation["resolved_params"]
                tc = combo.get("tool_choice", "required")
                dedup_key = (tc,) + tuple(sorted(resolved.items()))
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    unique.append(combo)
            validated_target_combos[model_id] = unique

        total_combos = sum(
            len(validated_target_combos.get(spec["model_id"], []))
            for spec in targets_spec
        )
        return total_combos

    def test_single_model_no_dedup(self):
        targets = [{"model_id": "gpt-4o", "provider_key": "openai"}]
        combos = [
            {"temperature": 0.0, "tool_choice": "auto"},
            {"temperature": 0.5, "tool_choice": "auto"},
            {"temperature": 1.0, "tool_choice": "auto"},
        ]
        assert self._compute_total_combos(targets, combos) == 3

    def test_single_model_with_dedup(self):
        """GPT-5 locks temp → 3 raw combos → 1 unique."""
        targets = [{"model_id": "gpt-5", "provider_key": "openai"}]
        combos = [
            {"temperature": 0.0, "tool_choice": "required"},
            {"temperature": 0.5, "tool_choice": "required"},
            {"temperature": 1.0, "tool_choice": "required"},
        ]
        assert self._compute_total_combos(targets, combos) == 1

    def test_two_models_different_dedup_rates(self):
        """OpenAI GPT-4o: 3 unique. Anthropic Claude: clamps above 1.0, so 2 unique."""
        targets = [
            {"model_id": "gpt-4o", "provider_key": "openai"},
            {"model_id": "anthropic/claude-sonnet-4-6", "provider_key": "anthropic"},
        ]
        combos = [
            {"temperature": 0.5, "tool_choice": "auto"},
            {"temperature": 1.0, "tool_choice": "auto"},
            {"temperature": 1.5, "tool_choice": "auto"},
        ]
        # OpenAI: 0.5, 1.0, 1.5 all valid -> 3
        # Anthropic: 0.5, 1.0, clamp(1.5)->1.0 -> dedup -> 2
        total = self._compute_total_combos(targets, combos)
        assert total == 5  # 3 + 2

    def test_empty_combos(self):
        targets = [{"model_id": "gpt-4o", "provider_key": "openai"}]
        assert self._compute_total_combos(targets, []) == 0

    def test_empty_targets(self):
        combos = [{"temperature": 0.5, "tool_choice": "auto"}]
        assert self._compute_total_combos([], combos) == 0

    def test_multiple_models_same_provider(self):
        """Two OpenAI models with same combos — no dedup (both accept full range)."""
        targets = [
            {"model_id": "gpt-4o", "provider_key": "openai"},
            {"model_id": "gpt-4o-mini", "provider_key": "openai"},
        ]
        combos = [
            {"temperature": 0.7, "tool_choice": "auto"},
            {"temperature": 1.0, "tool_choice": "required"},
        ]
        assert self._compute_total_combos(targets, combos) == 4  # 2 * 2


# ===========================================================================
# Issue #1 — Integration: expand_search_space + dedup
# ===========================================================================


class TestExpandAndDedup:
    """End-to-end: expand a search space then dedup for a specific provider/model."""

    def _expand_and_dedup(self, search_space: dict, model_id: str, provider_key: str):
        combos = _expand_search_space(search_space)
        prov_key = identify_provider(model_id, provider_key)
        seen = set()
        unique = []
        for combo in combos:
            temp = float(combo.get("temperature", 0.0))
            pp = {k: v for k, v in combo.items() if k not in ("temperature", "tool_choice", "max_tokens")}
            params_to_check = {"temperature": temp, **pp}
            validation = validate_params(prov_key, model_id, params_to_check)
            resolved = validation["resolved_params"]
            tc = combo.get("tool_choice", "required")
            dedup_key = (tc,) + tuple(sorted(resolved.items()))
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique.append(combo)
        return combos, unique

    def test_anthropic_high_temp_range_collapses(self):
        """Anthropic max temp=1.0. Range 0.5-2.0 step 0.5 = [0.5, 1.0, 1.5, 2.0].
        After clamping: [0.5, 1.0, 1.0, 1.0] -> dedup -> 2 unique."""
        space = {"temperature": {"min": 0.5, "max": 2.0, "step": 0.5}}
        raw, unique = self._expand_and_dedup(space, "anthropic/claude-sonnet-4-6", "anthropic")
        assert len(raw) == 4  # 0.5, 1.0, 1.5, 2.0
        assert len(unique) == 2  # 0.5, 1.0 (1.5 and 2.0 clamp to 1.0)

    def test_openai_full_range_no_collapse(self):
        """OpenAI supports 0-2 temp range, so no clamping happens."""
        space = {"temperature": {"min": 0.0, "max": 2.0, "step": 0.5}}
        raw, unique = self._expand_and_dedup(space, "gpt-4o", "openai")
        assert len(raw) == 5  # 0.0, 0.5, 1.0, 1.5, 2.0
        assert len(unique) == 5

    def test_gpt5_all_temps_collapse(self):
        """GPT-5 locks temp to 1.0, so all values collapse to one."""
        space = {
            "temperature": {"min": 0.0, "max": 1.0, "step": 0.25},
            "tool_choice": ["auto", "required"],
        }
        raw, unique = self._expand_and_dedup(space, "gpt-5", "openai")
        assert len(raw) == 10  # 5 temps * 2 tool_choices
        # All temps -> 1.0, but two tool_choices remain distinct
        assert len(unique) == 2

    def test_openai_top_k_warned_stays_distinct(self):
        """OpenAI warns top_k but passes it through (warn-not-drop).
        Combos with different top_k values remain distinct."""
        space = {
            "temperature": [0.7],
            "top_k": [10, 20, 50],
            "tool_choice": ["auto"],
        }
        raw, unique = self._expand_and_dedup(space, "gpt-4o", "openai")
        assert len(raw) == 3  # 1 * 3 * 1
        assert len(unique) == 3  # top_k passes through, all distinct

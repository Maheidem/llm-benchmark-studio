"""Tests for scoring functions in app.py — tool selection, param accuracy, overall score, multi-turn."""

import pytest

from app import (
    score_tool_selection,
    score_params,
    compute_overall_score,
    score_multi_turn,
    _find_best_config,
    _find_best_score,
    BUILTIN_PARAM_PRESETS,
    PHASE10_DEFAULTS,
)


# ===========================================================================
# score_tool_selection
# ===========================================================================


class TestScoreToolSelection:
    def test_exact_match(self):
        assert score_tool_selection("get_weather", "get_weather") == 1.0

    def test_case_insensitive(self):
        assert score_tool_selection("Get_Weather", "get_weather") == 1.0

    def test_mismatch(self):
        assert score_tool_selection("get_weather", "search_web") == 0.0

    def test_expected_none_actual_none(self):
        """No tool expected, no tool called = correct."""
        assert score_tool_selection(None, None) == 1.0

    def test_expected_none_actual_called(self):
        """No tool expected but model called one = wrong."""
        assert score_tool_selection(None, "get_weather") == 0.0

    def test_expected_tool_actual_none(self):
        """Expected a tool but model didn't call one = wrong."""
        assert score_tool_selection("get_weather", None) == 0.0

    def test_expected_list_contains_match(self):
        """Expected tool is a list, actual matches one."""
        assert score_tool_selection(["get_weather", "check_forecast"], "check_forecast") == 1.0

    def test_expected_list_no_match(self):
        assert score_tool_selection(["get_weather", "check_forecast"], "search_web") == 0.0

    def test_expected_list_case_insensitive(self):
        assert score_tool_selection(["Get_Weather"], "get_weather") == 1.0


# ===========================================================================
# score_params
# ===========================================================================


class TestScoreParams:
    def test_none_expected(self):
        """None expected = params not scored."""
        assert score_params(None, {"city": "NYC"}) is None

    def test_empty_expected(self):
        """Empty dict expected = nothing to check = perfect."""
        assert score_params({}, {"city": "NYC"}) == 1.0

    def test_none_actual(self):
        """Expected params but got None = 0.0."""
        assert score_params({"city": "NYC"}, None) == 0.0

    def test_exact_match(self):
        assert score_params({"city": "NYC"}, {"city": "NYC"}) == 1.0

    def test_string_case_insensitive(self):
        assert score_params({"city": "nyc"}, {"city": "NYC"}) == 1.0

    def test_partial_match(self):
        result = score_params(
            {"city": "NYC", "units": "celsius"},
            {"city": "NYC", "units": "fahrenheit"},
        )
        assert result == 0.5  # 1 of 2 correct

    def test_numeric_match(self):
        assert score_params({"lat": 40.7}, {"lat": 40.7}) == 1.0

    def test_numeric_int_float_match(self):
        """int and float should be comparable."""
        assert score_params({"count": 5}, {"count": 5.0}) == 1.0

    def test_missing_key(self):
        result = score_params({"city": "NYC"}, {"state": "NY"})
        assert result == 0.0

    def test_extra_keys_ignored(self):
        """Extra actual params don't affect score."""
        result = score_params({"city": "NYC"}, {"city": "NYC", "state": "NY"})
        assert result == 1.0

    def test_multi_param_scoring(self):
        result = score_params(
            {"city": "NYC", "units": "celsius", "days": 5},
            {"city": "NYC", "units": "celsius", "days": 5},
        )
        assert result == 1.0

    def test_zero_of_many(self):
        result = score_params(
            {"a": 1, "b": 2, "c": 3},
            {"a": 99, "b": 99, "c": 99},
        )
        assert result == 0.0


# ===========================================================================
# compute_overall_score
# ===========================================================================


class TestComputeOverallScore:
    def test_tool_only(self):
        """When param_score is None, overall = tool_score."""
        assert compute_overall_score(1.0, None) == 1.0
        assert compute_overall_score(0.0, None) == 0.0

    def test_weighted(self):
        """0.6 * tool + 0.4 * param."""
        result = compute_overall_score(1.0, 1.0)
        assert result == 1.0

    def test_weighted_partial(self):
        result = compute_overall_score(1.0, 0.5)
        assert result == pytest.approx(0.8)  # 0.6 + 0.2

    def test_zero_tool_nonzero_param(self):
        result = compute_overall_score(0.0, 1.0)
        assert result == pytest.approx(0.4)  # 0.0 + 0.4


# ===========================================================================
# score_multi_turn
# ===========================================================================


class TestScoreMultiTurn:
    def test_empty_chain(self):
        result = score_multi_turn([], "get_weather", None, [], 1)
        assert result["overall_score"] == 0.0

    def test_single_hop_perfect(self):
        chain = [{"tool_name": "get_weather", "params": {"city": "NYC"}}]
        result = score_multi_turn(
            chain,
            expected_tool="get_weather",
            expected_params={"city": "NYC"},
            valid_prerequisites=[],
            optimal_hops=1,
        )
        assert result["completion"] == 1.0
        assert result["efficiency"] == 1.0
        assert result["overall_score"] == 1.0

    def test_two_hop_with_prerequisite(self):
        chain = [
            {"tool_name": "search_location", "params": {"query": "Eiffel Tower"}},
            {"tool_name": "get_weather", "params": {"lat": 48.8, "lon": 2.3}},
        ]
        result = score_multi_turn(
            chain,
            expected_tool="get_weather",
            expected_params={"lat": 48.8, "lon": 2.3},
            valid_prerequisites=["search_location"],
            optimal_hops=2,
        )
        assert result["completion"] == 1.0
        assert result["efficiency"] == 1.0
        assert result["detour_penalty"] == 0.0
        assert result["overall_score"] == 1.0

    def test_redundancy_penalty(self):
        chain = [
            {"tool_name": "get_weather", "params": {}},
            {"tool_name": "get_weather", "params": {"city": "NYC"}},
        ]
        result = score_multi_turn(
            chain,
            expected_tool="get_weather",
            expected_params={"city": "NYC"},
            valid_prerequisites=[],
            optimal_hops=1,
        )
        assert result["redundancy_penalty"] == 0.1

    def test_detour_penalty(self):
        chain = [
            {"tool_name": "irrelevant_tool", "params": {}},
            {"tool_name": "get_weather", "params": {"city": "NYC"}},
        ]
        result = score_multi_turn(
            chain,
            expected_tool="get_weather",
            expected_params={"city": "NYC"},
            valid_prerequisites=[],
            optimal_hops=1,
        )
        assert result["detour_penalty"] == 0.1

    def test_efficiency_penalty(self):
        """3 hops when optimal is 1 = 1/3 efficiency."""
        chain = [
            {"tool_name": "step1", "params": {}},
            {"tool_name": "step2", "params": {}},
            {"tool_name": "get_weather", "params": {"city": "NYC"}},
        ]
        result = score_multi_turn(
            chain,
            expected_tool="get_weather",
            expected_params={"city": "NYC"},
            valid_prerequisites=["step1", "step2"],
            optimal_hops=1,
        )
        assert result["efficiency"] == pytest.approx(1 / 3, abs=0.01)

    def test_wrong_final_tool(self):
        chain = [{"tool_name": "wrong_tool", "params": {}}]
        result = score_multi_turn(
            chain,
            expected_tool="get_weather",
            expected_params=None,
            valid_prerequisites=[],
            optimal_hops=1,
        )
        assert result["completion"] == 0.0

    def test_expected_tool_list(self):
        chain = [{"tool_name": "check_forecast", "params": {"city": "NYC"}}]
        result = score_multi_turn(
            chain,
            expected_tool=["get_weather", "check_forecast"],
            expected_params={"city": "NYC"},
            valid_prerequisites=[],
            optimal_hops=1,
        )
        assert result["completion"] == 1.0


# ===========================================================================
# _find_best_config / _find_best_score
# ===========================================================================


class TestFindBest:
    def test_find_best_config_basic(self):
        results = [
            {"config": {"temperature": 0.5}, "overall_score": 0.7},
            {"config": {"temperature": 0.8}, "overall_score": 0.95},
            {"config": {"temperature": 0.2}, "overall_score": 0.6},
        ]
        assert _find_best_config(results) == {"temperature": 0.8}

    def test_find_best_config_empty(self):
        assert _find_best_config([]) is None

    def test_find_best_score_basic(self):
        results = [
            {"overall_score": 0.7},
            {"overall_score": 0.95},
            {"overall_score": 0.6},
        ]
        assert _find_best_score(results) == 0.95

    def test_find_best_score_empty(self):
        assert _find_best_score([]) == 0.0

    def test_find_best_score_missing_key(self):
        """Results without overall_score should default to 0."""
        results = [{"config": {}}, {"overall_score": 0.5}]
        assert _find_best_score(results) == 0.5


# ===========================================================================
# BUILTIN_PARAM_PRESETS structure validation
# ===========================================================================


class TestBuiltinParamPresets:
    def test_presets_is_list(self):
        assert isinstance(BUILTIN_PARAM_PRESETS, list)

    def test_presets_not_empty(self):
        assert len(BUILTIN_PARAM_PRESETS) >= 2

    def test_each_preset_has_required_fields(self):
        for preset in BUILTIN_PARAM_PRESETS:
            assert "name" in preset
            assert "search_space" in preset
            assert isinstance(preset["name"], str)
            assert isinstance(preset["search_space"], dict)
            assert preset.get("builtin") is True

    def test_qwen3_preset_exists(self):
        names = [p["name"] for p in BUILTIN_PARAM_PRESETS]
        assert any("Qwen3" in n for n in names)

    def test_glm_preset_exists(self):
        names = [p["name"] for p in BUILTIN_PARAM_PRESETS]
        assert any("GLM" in n for n in names)

    def test_search_space_values_are_lists(self):
        """Vendor presets use single-value lists (not ranges)."""
        for preset in BUILTIN_PARAM_PRESETS:
            for param, values in preset["search_space"].items():
                assert isinstance(values, list), f"Preset '{preset['name']}' param '{param}' should be a list"
                assert len(values) >= 1


# ===========================================================================
# PHASE10_DEFAULTS structure validation
# ===========================================================================


class TestPhase10Defaults:
    def test_has_required_sections(self):
        assert "judge" in PHASE10_DEFAULTS
        assert "param_tuner" in PHASE10_DEFAULTS
        assert "prompt_tuner" in PHASE10_DEFAULTS

    def test_judge_defaults(self):
        j = PHASE10_DEFAULTS["judge"]
        assert j["enabled"] is False
        assert isinstance(j["model_id"], str)
        assert isinstance(j["temperature"], (int, float))
        assert isinstance(j["max_tokens"], int)

    def test_param_tuner_defaults(self):
        pt = PHASE10_DEFAULTS["param_tuner"]
        assert "max_combinations" in pt
        assert "presets" in pt
        assert isinstance(pt["presets"], list)
        assert pt["temp_min"] <= pt["temp_max"]
        assert pt["temp_step"] > 0
        assert pt["top_p_min"] <= pt["top_p_max"]

    def test_prompt_tuner_defaults(self):
        pt = PHASE10_DEFAULTS["prompt_tuner"]
        assert pt["mode"] in ("quick", "evolutionary")
        assert pt["generations"] >= 1
        assert pt["population_size"] >= 1

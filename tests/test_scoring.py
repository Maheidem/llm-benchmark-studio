"""Tests for scoring functions — tool selection, param accuracy, schema validation, overall score, multi-turn."""

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
from routers.helpers import score_schema_validation


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


# ===========================================================================
# score_schema_validation — Tier 2
# ===========================================================================

# Reusable schema fixture: a weather tool with required and optional params
WEATHER_SCHEMA = {
    "type": "object",
    "required": ["city", "units"],
    "properties": {
        "city": {"type": "string"},
        "units": {"type": "string", "enum": ["celsius", "fahrenheit"]},
        "days": {"type": "integer"},
        "lat": {"type": "number"},
        "lon": {"type": "number"},
    },
}


class TestScoreSchemaValidation:
    # --- required_present ---

    def test_all_required_present(self):
        result = score_schema_validation(WEATHER_SCHEMA, {"city": "NYC", "units": "celsius"})
        assert result["required_present"] == 1.0

    def test_some_required_missing(self):
        # Only 1 of 2 required params present
        result = score_schema_validation(WEATHER_SCHEMA, {"city": "NYC"})
        assert result["required_present"] == pytest.approx(0.5)

    def test_no_required_in_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        result = score_schema_validation(schema, {"x": "hello"})
        assert result["required_present"] == 1.0

    def test_all_required_missing(self):
        result = score_schema_validation(WEATHER_SCHEMA, {})
        assert result["required_present"] == 0.0

    def test_empty_actual_with_required_fields(self):
        result = score_schema_validation(WEATHER_SCHEMA, None)
        assert result["required_present"] == 0.0

    # --- type_correct ---

    def test_correct_types_for_all_params(self):
        params = {"city": "NYC", "units": "celsius", "days": 3}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        assert result["type_correct"] == 1.0

    def test_wrong_type_string_vs_int(self):
        # days should be integer, not string
        params = {"city": "NYC", "units": "celsius", "days": "three"}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        assert result["type_correct"] < 1.0

    def test_wrong_type_dict_vs_list_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array"},
                "meta": {"type": "object"},
            },
        }
        # tags gets a dict instead of array, meta gets a list instead of object
        params = {"tags": {"a": 1}, "meta": ["item"]}
        result = score_schema_validation(schema, params)
        assert result["type_correct"] == 0.0

    def test_float_accepted_for_number_type(self):
        schema = {"type": "object", "properties": {"score": {"type": "number"}}}
        result = score_schema_validation(schema, {"score": 3.14})
        assert result["type_correct"] == 1.0

    def test_int_accepted_for_number_type(self):
        # JSON Schema "number" accepts both int and float
        schema = {"type": "object", "properties": {"score": {"type": "number"}}}
        result = score_schema_validation(schema, {"score": 5})
        assert result["type_correct"] == 1.0

    def test_no_properties_in_schema_type_score_is_perfect(self):
        schema = {"type": "object"}
        result = score_schema_validation(schema, {"city": "NYC"})
        assert result["type_correct"] == 1.0

    # --- hallucination_free ---

    def test_no_hallucinated_params(self):
        params = {"city": "NYC", "units": "celsius"}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        assert result["hallucination_free"] == 1.0

    def test_some_hallucinated_params(self):
        # "wind_speed" is not in the schema
        params = {"city": "NYC", "units": "celsius", "wind_speed": 10}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        assert result["hallucination_free"] < 1.0
        assert result["hallucination_free"] > 0.0

    def test_all_params_hallucinated(self):
        params = {"fake_param": "x", "another_fake": 99}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        assert result["hallucination_free"] == 0.0

    def test_single_hallucinated_param_penalty(self):
        # 1 extra param out of 3 total = 1/3 hallucinated -> hallucination_free = 2/3
        params = {"city": "NYC", "units": "celsius", "not_in_schema": True}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        assert result["hallucination_free"] == pytest.approx(2 / 3, abs=0.01)

    # --- weighted combination ---

    def test_weighted_formula_perfect(self):
        """0.5*1.0 + 0.3*1.0 + 0.2*1.0 = 1.0."""
        params = {"city": "NYC", "units": "celsius"}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        assert result["schema_score"] == pytest.approx(1.0)

    def test_weighted_formula_partial(self):
        """Manually verify: required=0.5, type=1.0, hallucination=1.0 -> 0.5*0.5+0.3*1.0+0.2*1.0 = 0.75."""
        # Only city present (not units), both are correct types, no hallucination
        params = {"city": "NYC"}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        expected = 0.5 * 0.5 + 0.3 * 1.0 + 0.2 * 1.0
        assert result["schema_score"] == pytest.approx(expected, abs=0.01)

    def test_returns_all_sub_scores(self):
        """Result dict has all required keys."""
        result = score_schema_validation(WEATHER_SCHEMA, {"city": "NYC", "units": "celsius"})
        assert "required_present" in result
        assert "type_correct" in result
        assert "hallucination_free" in result
        assert "schema_score" in result

    # --- edge cases ---

    def test_empty_schema_no_required_no_properties(self):
        """Empty schema ({}) returns None sentinel values — schema is undefined, not validatable."""
        schema = {}
        result = score_schema_validation(schema, {"anything": "goes"})
        # Implementation returns None for all fields when schema is empty/falsy
        assert result["required_present"] is None
        assert result["type_correct"] is None
        assert result["hallucination_free"] is None
        assert result["schema_score"] is None

    def test_schema_no_properties_key(self):
        """Schema with only 'required' but no 'properties' should not crash."""
        schema = {"required": ["city"]}
        result = score_schema_validation(schema, {"city": "NYC"})
        assert result["required_present"] == 1.0

    def test_actual_params_none_handled_gracefully(self):
        """None actual_params should return 0.0 required_present, not crash."""
        result = score_schema_validation(WEATHER_SCHEMA, None)
        assert result["required_present"] == 0.0

    def test_scores_are_bounded_zero_to_one(self):
        """All sub-scores must be in [0.0, 1.0]."""
        params = {"city": "NYC", "units": "celsius", "fake1": 1, "fake2": 2, "fake3": 3}
        result = score_schema_validation(WEATHER_SCHEMA, params)
        for key in ("required_present", "type_correct", "hallucination_free", "schema_score"):
            assert 0.0 <= result[key] <= 1.0, f"{key} out of bounds: {result[key]}"


# ===========================================================================
# compute_overall_score — updated 3-tier behavior
# ===========================================================================


class TestComputeOverallScoreWithSchema:
    """Tests for the updated compute_overall_score with schema_score parameter."""

    def test_with_schema_score_both_perfect(self):
        """0.5*tool + 0.5*schema when schema_score provided."""
        result = compute_overall_score(1.0, None, schema_score=1.0)
        assert result == 1.0

    def test_with_schema_score_mixed(self):
        """0.5 * 1.0 + 0.5 * 0.6 = 0.8."""
        result = compute_overall_score(1.0, None, schema_score=0.6)
        assert result == pytest.approx(0.8)

    def test_without_schema_score_falls_back_to_legacy(self):
        """When schema_score=None: fallback to 0.6 * tool + 0.4 * param."""
        result = compute_overall_score(1.0, 0.5)
        assert result == pytest.approx(0.8)  # 0.6 + 0.2

    def test_schema_score_takes_precedence_over_param_score(self):
        """When both schema_score and param_score provided, schema_score takes precedence."""
        with_schema = compute_overall_score(1.0, 0.0, schema_score=1.0)
        without_schema = compute_overall_score(1.0, 0.0)
        # schema path: 0.5*1.0 + 0.5*1.0 = 1.0
        # legacy path: 0.6*1.0 + 0.4*0.0 = 0.6
        assert with_schema > without_schema

    def test_both_none_returns_tool_score_only(self):
        """When param_score=None and schema_score=None: just tool_score."""
        assert compute_overall_score(0.75, None) == 0.75
        assert compute_overall_score(0.0, None) == 0.0
        assert compute_overall_score(1.0, None) == 1.0

    def test_zero_tool_score_with_perfect_schema(self):
        """0.5 * 0.0 + 0.5 * 1.0 = 0.5."""
        result = compute_overall_score(0.0, None, schema_score=1.0)
        assert result == pytest.approx(0.5)

    def test_schema_score_zero_tool_zero(self):
        """0.5 * 0.0 + 0.5 * 0.0 = 0.0."""
        result = compute_overall_score(0.0, None, schema_score=0.0)
        assert result == 0.0


# ===========================================================================
# Backward compatibility
# ===========================================================================


class TestBackwardCompatibility:
    """Ensure old case_results data (no schema columns) still computes correctly."""

    def test_score_params_still_works_independently(self):
        """score_params() still returns accurate float or None."""
        assert score_params({"city": "NYC"}, {"city": "NYC"}) == 1.0
        assert score_params({"city": "NYC"}, {"city": "London"}) == 0.0
        assert score_params(None, {"city": "NYC"}) is None

    def test_compute_overall_score_legacy_two_arg_form(self):
        """Two-arg form (no schema_score) uses legacy 0.6/0.4 weighting."""
        assert compute_overall_score(1.0, 1.0) == 1.0
        assert compute_overall_score(1.0, None) == 1.0
        assert compute_overall_score(0.0, 1.0) == pytest.approx(0.4)

    def test_compute_overall_score_with_none_schema_is_legacy(self):
        """Explicit schema_score=None triggers legacy path."""
        legacy = compute_overall_score(1.0, 0.5)
        explicit_none = compute_overall_score(1.0, 0.5, schema_score=None)
        assert legacy == explicit_none

    def test_score_tool_selection_unchanged(self):
        """Tier 1 function is unmodified."""
        assert score_tool_selection("get_weather", "get_weather") == 1.0
        assert score_tool_selection("get_weather", "search_web") == 0.0
        assert score_tool_selection(None, None) == 1.0
        assert score_tool_selection("get_weather", None) == 0.0

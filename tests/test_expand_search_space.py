"""Tests for _expand_search_space() — numeric ranges, categorical lists, cartesian products."""

import pytest

from app import _expand_search_space


# ===========================================================================
# Numeric range expansion
# ===========================================================================


class TestNumericRangeExpansion:
    """Numeric specs like {"min": 0.0, "max": 1.0, "step": 0.5} -> [0.0, 0.5, 1.0]."""

    def test_basic_range(self):
        combos = _expand_search_space({"temperature": {"min": 0.0, "max": 1.0, "step": 0.5}})
        assert combos == [
            {"temperature": 0.0},
            {"temperature": 0.5},
            {"temperature": 1.0},
        ]

    def test_single_value_range(self):
        """min == max should produce exactly one combo."""
        combos = _expand_search_space({"temperature": {"min": 0.5, "max": 0.5, "step": 0.1}})
        assert combos == [{"temperature": 0.5}]

    def test_step_larger_than_range(self):
        """Step > (max - min) should still produce the min value."""
        combos = _expand_search_space({"temperature": {"min": 0.0, "max": 0.3, "step": 1.0}})
        assert combos == [{"temperature": 0.0}]

    def test_defaults_for_missing_keys(self):
        """Missing min defaults to 0, max to 1, step to 0.1."""
        combos = _expand_search_space({"temperature": {}})
        # Default: min=0, max=1, step=0.1 -> 11 values
        assert len(combos) == 11
        assert combos[0]["temperature"] == 0.0
        assert combos[-1]["temperature"] == 1.0

    def test_negative_step_skipped(self):
        """Negative step should skip the param entirely."""
        combos = _expand_search_space({"temperature": {"min": 0.0, "max": 1.0, "step": -0.5}})
        assert combos == [{}]

    def test_zero_step_skipped(self):
        """Zero step should skip the param (would cause infinite loop)."""
        combos = _expand_search_space({"temperature": {"min": 0.0, "max": 1.0, "step": 0}})
        assert combos == [{}]

    def test_min_greater_than_max_skipped(self):
        """min > max should skip the param."""
        combos = _expand_search_space({"temperature": {"min": 1.0, "max": 0.0, "step": 0.1}})
        assert combos == [{}]

    def test_float_precision(self):
        """Values should be rounded to avoid floating point drift."""
        combos = _expand_search_space({"temperature": {"min": 0.0, "max": 0.3, "step": 0.1}})
        vals = [c["temperature"] for c in combos]
        assert vals == [0.0, 0.1, 0.2, 0.3]


# ===========================================================================
# Categorical list expansion
# ===========================================================================


class TestCategoricalListExpansion:
    """List specs like ["auto", "required"] produce one combo per value."""

    def test_basic_list(self):
        combos = _expand_search_space({"tool_choice": ["auto", "required"]})
        assert combos == [
            {"tool_choice": "auto"},
            {"tool_choice": "required"},
        ]

    def test_single_element_list(self):
        combos = _expand_search_space({"tool_choice": ["auto"]})
        assert combos == [{"tool_choice": "auto"}]

    def test_empty_list_skipped(self):
        """Empty list should skip the param."""
        combos = _expand_search_space({"tool_choice": []})
        assert combos == [{}]

    def test_numeric_values_in_list(self):
        """Lists can contain numbers too."""
        combos = _expand_search_space({"temperature": [0.5, 0.7, 1.0]})
        assert combos == [
            {"temperature": 0.5},
            {"temperature": 0.7},
            {"temperature": 1.0},
        ]


# ===========================================================================
# Cartesian product (multi-param)
# ===========================================================================


class TestCartesianProduct:
    """Multiple params produce cartesian product of all values."""

    def test_two_params(self, sample_search_space):
        combos = _expand_search_space(sample_search_space)
        # temperature: [0.0, 0.5, 1.0] x tool_choice: ["auto", "required"] = 6
        assert len(combos) == 6
        # All combos should have both keys
        for c in combos:
            assert "temperature" in c
            assert "tool_choice" in c

    def test_three_params(self):
        space = {
            "temperature": [0.0, 1.0],
            "top_p": [0.5, 1.0],
            "tool_choice": ["auto"],
        }
        combos = _expand_search_space(space)
        assert len(combos) == 4  # 2 * 2 * 1
        for c in combos:
            assert set(c.keys()) == {"temperature", "top_p", "tool_choice"}

    def test_mixed_numeric_and_categorical(self):
        space = {
            "temperature": {"min": 0.0, "max": 1.0, "step": 1.0},
            "tool_choice": ["auto", "required"],
        }
        combos = _expand_search_space(space)
        assert len(combos) == 4  # [0.0, 1.0] x ["auto", "required"]


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_empty_search_space(self):
        combos = _expand_search_space({})
        assert combos == [{}]

    def test_all_params_skipped(self):
        """When all params are invalid, return single empty combo."""
        combos = _expand_search_space({
            "a": {"min": 1.0, "max": 0.0, "step": 0.1},  # min > max
            "b": [],                                         # empty list
        })
        assert combos == [{}]

    def test_large_combo_count(self):
        """Verify a moderately large search space produces correct count."""
        space = {
            "temperature": {"min": 0.0, "max": 1.0, "step": 0.25},   # 5 values
            "top_p": {"min": 0.0, "max": 1.0, "step": 0.5},          # 3 values
            "tool_choice": ["auto", "required", "none"],               # 3 values
        }
        combos = _expand_search_space(space)
        assert len(combos) == 5 * 3 * 3  # 45

    def test_each_combo_is_independent_dict(self):
        """Mutating one combo shouldn't affect others."""
        combos = _expand_search_space({"temperature": [0.0, 1.0]})
        combos[0]["temperature"] = 999
        assert combos[1]["temperature"] == 1.0

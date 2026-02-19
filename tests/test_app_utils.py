"""Tests for pure utility functions in app.py.

Covers: _mask_value, _validate_tools, _parse_expected_tool,
_serialize_expected_tool, _tool_matches, _sse, _parse_meta_response,
_parse_judge_json, _build_tools_summary, _build_test_cases_summary,
_build_tool_definitions_text, _check_rate_limit,
_compute_eval_summaries.
"""

import json
import time
import pytest
from unittest.mock import patch, AsyncMock

from app import (
    _mask_value,
    _validate_tools,
    _parse_expected_tool,
    _serialize_expected_tool,
    _tool_matches,
    _sse,
    _parse_meta_response,
    _parse_judge_json,
    _build_tools_summary,
    _build_test_cases_summary,
    _build_tool_definitions_text,
    _check_rate_limit,
    _compute_eval_summaries,
)
from benchmark import Target


# ── _mask_value ─────────────────────────────────────────────────────

class TestMaskValue:
    def test_empty_string(self):
        assert _mask_value("") == "****"

    def test_short_string(self):
        assert _mask_value("abc") == "****"

    def test_exactly_four_chars(self):
        assert _mask_value("abcd") == "****"

    def test_five_chars(self):
        assert _mask_value("abcde") == "****bcde"

    def test_long_api_key(self):
        result = _mask_value("sk-1234567890abcdef")
        assert result.startswith("****")
        assert result.endswith("cdef")

    def test_none_returns_stars(self):
        assert _mask_value(None) == "****"


# ── _validate_tools ─────────────────────────────────────────────────

class TestValidateTools:
    def test_valid_tool(self):
        tools = [{"type": "function", "function": {"name": "get_weather"}}]
        assert _validate_tools(tools) is None

    def test_empty_list(self):
        assert _validate_tools([]) is not None

    def test_not_a_list(self):
        assert _validate_tools("not a list") is not None

    def test_non_dict_entry(self):
        assert _validate_tools(["string"]) is not None

    def test_wrong_type_field(self):
        tools = [{"type": "not_function", "function": {"name": "x"}}]
        err = _validate_tools(tools)
        assert err is not None
        assert "type" in err

    def test_missing_function_name(self):
        tools = [{"type": "function", "function": {}}]
        err = _validate_tools(tools)
        assert err is not None
        assert "name" in err

    def test_multiple_tools_second_invalid(self):
        tools = [
            {"type": "function", "function": {"name": "ok"}},
            {"type": "function", "function": {}},
        ]
        err = _validate_tools(tools)
        assert "tools[1]" in err

    def test_missing_function_key(self):
        tools = [{"type": "function"}]
        err = _validate_tools(tools)
        assert "name" in err


# ── _parse_expected_tool / _serialize_expected_tool ──────────────────

class TestParseExpectedTool:
    def test_none(self):
        assert _parse_expected_tool(None) is None

    def test_plain_string(self):
        assert _parse_expected_tool("get_weather") == "get_weather"

    def test_json_array(self):
        result = _parse_expected_tool('["get_weather", "get_temp"]')
        assert result == ["get_weather", "get_temp"]

    def test_non_json_string(self):
        assert _parse_expected_tool("not-json") == "not-json"

    def test_json_object_treated_as_string(self):
        # JSON object is not a list, should return the string
        result = _parse_expected_tool('{"key": "val"}')
        assert result == '{"key": "val"}'


class TestSerializeExpectedTool:
    def test_none(self):
        assert _serialize_expected_tool(None) is None

    def test_string(self):
        assert _serialize_expected_tool("get_weather") == "get_weather"

    def test_list(self):
        result = _serialize_expected_tool(["a", "b"])
        assert json.loads(result) == ["a", "b"]

    def test_roundtrip(self):
        original = ["get_weather", "get_temp"]
        serialized = _serialize_expected_tool(original)
        parsed = _parse_expected_tool(serialized)
        assert parsed == original


# ── _tool_matches ───────────────────────────────────────────────────

class TestToolMatches:
    def test_exact_match(self):
        assert _tool_matches("get_weather", "get_weather") is True

    def test_case_insensitive(self):
        assert _tool_matches("Get_Weather", "get_weather") is True

    def test_mismatch(self):
        assert _tool_matches("get_temp", "get_weather") is False

    def test_none_actual(self):
        assert _tool_matches(None, "get_weather") is False

    def test_none_expected(self):
        assert _tool_matches("get_weather", None) is False

    def test_both_none(self):
        assert _tool_matches(None, None) is False

    def test_list_expected_match(self):
        assert _tool_matches("get_weather", ["get_weather", "get_temp"]) is True

    def test_list_expected_no_match(self):
        assert _tool_matches("other", ["get_weather", "get_temp"]) is False

    def test_list_case_insensitive(self):
        assert _tool_matches("Get_Weather", ["get_weather"]) is True


# ── _sse ────────────────────────────────────────────────────────────

class TestSSE:
    def test_basic_format(self):
        result = _sse({"type": "ping"})
        assert result.startswith("data: ")
        assert result.endswith("\n\n")

    def test_parseable_json(self):
        result = _sse({"key": "value"})
        payload = result.replace("data: ", "").strip()
        parsed = json.loads(payload)
        assert parsed == {"key": "value"}

    def test_empty_dict(self):
        result = _sse({})
        payload = json.loads(result.replace("data: ", "").strip())
        assert payload == {}


# ── _parse_meta_response ────────────────────────────────────────────

class TestParseMetaResponse:
    def test_direct_json_array(self):
        result = _parse_meta_response('[{"prompt": "test"}]')
        assert result == [{"prompt": "test"}]

    def test_markdown_code_block(self):
        text = '```json\n[{"a": 1}]\n```'
        result = _parse_meta_response(text)
        assert result == [{"a": 1}]

    def test_embedded_array(self):
        text = 'Here is the result:\n[{"x": 2}]\nDone.'
        result = _parse_meta_response(text)
        assert result == [{"x": 2}]

    def test_invalid_json_returns_empty(self):
        result = _parse_meta_response("not json at all")
        assert result == []

    def test_json_object_not_array(self):
        result = _parse_meta_response('{"key": "val"}')
        assert result == []

    def test_empty_string(self):
        result = _parse_meta_response("")
        assert result == []

    def test_whitespace_only(self):
        result = _parse_meta_response("   \n  ")
        assert result == []


# ── _parse_judge_json ───────────────────────────────────────────────

class TestParseJudgeJson:
    def test_direct_json_object(self):
        result = _parse_judge_json('{"score": 0.9}')
        assert result == {"score": 0.9}

    def test_markdown_code_block(self):
        text = '```json\n{"verdict": "pass"}\n```'
        result = _parse_judge_json(text)
        assert result == {"verdict": "pass"}

    def test_embedded_object(self):
        text = 'Analysis:\n{"pass": true}\nEnd.'
        result = _parse_judge_json(text)
        assert result == {"pass": True}

    def test_invalid_json_returns_empty(self):
        result = _parse_judge_json("random text")
        assert result == {}

    def test_json_array_not_object(self):
        result = _parse_judge_json('[1, 2, 3]')
        assert result == {}

    def test_empty_string(self):
        result = _parse_judge_json("")
        assert result == {}


# ── _build_tools_summary ────────────────────────────────────────────

class TestBuildToolsSummary:
    def test_single_tool(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {"properties": {"city": {}, "unit": {}}},
            }
        }]
        result = _build_tools_summary(tools)
        assert "get_weather" in result
        assert "city" in result
        assert "unit" in result

    def test_empty_tools(self):
        assert _build_tools_summary([]) == ""

    def test_truncates_description(self):
        tools = [{
            "function": {
                "name": "tool",
                "description": "A" * 200,
                "parameters": {"properties": {}},
            }
        }]
        result = _build_tools_summary(tools)
        assert len(result) < 200  # description truncated to 100


# ── _build_test_cases_summary ───────────────────────────────────────

class TestBuildTestCasesSummary:
    def test_single_case(self):
        cases = [{"prompt": "What's the weather?", "expected_tool": "get_weather"}]
        result = _build_test_cases_summary(cases)
        assert "weather" in result.lower()
        assert "get_weather" in result

    def test_limits_to_10(self):
        cases = [{"prompt": f"case {i}", "expected_tool": f"tool_{i}"} for i in range(20)]
        result = _build_test_cases_summary(cases)
        lines = [l for l in result.strip().split("\n") if l.strip()]
        assert len(lines) == 10

    def test_empty_cases(self):
        assert _build_test_cases_summary([]) == ""


# ── _build_tool_definitions_text ────────────────────────────────────

class TestBuildToolDefinitionsText:
    def test_single_tool_with_params(self):
        tools = [{
            "function": {
                "name": "search",
                "description": "Search the web",
                "parameters": {
                    "properties": {
                        "query": {"type": "string", "description": "Search terms"},
                        "limit": {"type": "integer", "description": "Max results"},
                    }
                }
            }
        }]
        result = _build_tool_definitions_text(tools)
        assert "search" in result
        assert "query" in result
        assert "string" in result
        assert "limit" in result

    def test_empty_tools(self):
        assert _build_tool_definitions_text([]) == ""


# ── _check_rate_limit (DB-backed) ────────────────────────────────────

class TestRateLimit:
    """Test DB-backed rate limiting via _check_rate_limit."""

    @pytest.mark.asyncio
    async def test_allowed_when_under_limit(self):
        """Should pass when user has no active/recent jobs."""
        with patch("routers.helpers.db") as mock_db:
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)
            mock_db.get_user_active_job_count = AsyncMock(return_value=0)
            mock_db.get_user_recent_job_count = AsyncMock(return_value=5)
            # Should not raise
            await _check_rate_limit("test_user")

    @pytest.mark.asyncio
    async def test_raises_on_concurrent_limit(self):
        """Should raise HTTPException 429 when concurrent jobs >= max_concurrent."""
        from fastapi import HTTPException
        with patch("routers.helpers.db") as mock_db:
            mock_db.get_user_rate_limit = AsyncMock(return_value={"benchmarks_per_hour": 20, "max_concurrent": 1})
            mock_db.get_user_active_job_count = AsyncMock(return_value=1)
            with pytest.raises(HTTPException) as exc_info:
                await _check_rate_limit("test_user")
            assert exc_info.value.status_code == 429
            assert "concurrent" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_raises_on_hourly_limit(self):
        """Should raise HTTPException 429 when recent jobs >= max_per_hour."""
        from fastapi import HTTPException
        with patch("routers.helpers.db") as mock_db:
            mock_db.get_user_rate_limit = AsyncMock(return_value={"benchmarks_per_hour": 10, "max_concurrent": 5})
            mock_db.get_user_active_job_count = AsyncMock(return_value=0)
            mock_db.get_user_recent_job_count = AsyncMock(return_value=10)
            with pytest.raises(HTTPException) as exc_info:
                await _check_rate_limit("test_user")
            assert exc_info.value.status_code == 429
            assert "rate limit" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_uses_defaults_when_no_custom_limits(self):
        """Should use default 20/hour, 1 concurrent when no rate_limits row."""
        from fastapi import HTTPException
        with patch("routers.helpers.db") as mock_db:
            mock_db.get_user_rate_limit = AsyncMock(return_value=None)
            mock_db.get_user_active_job_count = AsyncMock(return_value=0)
            mock_db.get_user_recent_job_count = AsyncMock(return_value=19)
            # 19 < 20 default, should pass
            await _check_rate_limit("test_user")

    @pytest.mark.asyncio
    async def test_respects_custom_limits(self):
        """Should respect custom rate limits from DB."""
        from fastapi import HTTPException
        with patch("routers.helpers.db") as mock_db:
            mock_db.get_user_rate_limit = AsyncMock(return_value={"benchmarks_per_hour": 100, "max_concurrent": 5})
            mock_db.get_user_active_job_count = AsyncMock(return_value=3)
            mock_db.get_user_recent_job_count = AsyncMock(return_value=50)
            # 3 < 5 and 50 < 100, should pass
            await _check_rate_limit("test_user")


# ── _compute_eval_summaries ─────────────────────────────────────────

class TestComputeEvalSummaries:
    def _make_target(self, model_id, provider="test"):
        return Target(
            provider=provider,
            model_id=model_id,
            display_name=model_id,
        )

    def test_single_model_perfect_score(self):
        targets = [self._make_target("m1")]
        results = [{
            "model_id": "m1",
            "success": True,
            "tool_selection_score": 1.0,
            "param_accuracy": 1.0,
            "overall_score": 1.0,
        }]
        summaries = _compute_eval_summaries(results, targets)
        assert len(summaries) == 1
        s = summaries[0]
        assert s["tool_accuracy_pct"] == 100.0
        assert s["param_accuracy_pct"] == 100.0
        assert s["overall_pct"] == 100.0
        assert s["cases_passed"] == 1

    def test_mixed_scores(self):
        targets = [self._make_target("m1")]
        results = [
            {"model_id": "m1", "success": True, "tool_selection_score": 1.0, "param_accuracy": 0.5, "overall_score": 0.75},
            {"model_id": "m1", "success": True, "tool_selection_score": 0.0, "param_accuracy": 1.0, "overall_score": 0.5},
        ]
        summaries = _compute_eval_summaries(results, targets)
        s = summaries[0]
        assert s["tool_accuracy_pct"] == 50.0
        assert s["param_accuracy_pct"] == 75.0
        assert s["cases_run"] == 2

    def test_failed_results_excluded_from_scores(self):
        targets = [self._make_target("m1")]
        results = [
            {"model_id": "m1", "success": True, "tool_selection_score": 1.0, "param_accuracy": 1.0, "overall_score": 1.0},
            {"model_id": "m1", "success": False, "tool_selection_score": 0.0, "param_accuracy": 0.0, "overall_score": 0.0},
        ]
        summaries = _compute_eval_summaries(results, targets)
        s = summaries[0]
        assert s["tool_accuracy_pct"] == 100.0
        assert s["cases_run"] == 2
        assert s["cases_passed"] == 1

    def test_no_param_accuracy(self):
        targets = [self._make_target("m1")]
        results = [
            {"model_id": "m1", "success": True, "tool_selection_score": 1.0, "param_accuracy": None, "overall_score": 1.0},
        ]
        summaries = _compute_eval_summaries(results, targets)
        s = summaries[0]
        assert s["param_accuracy_pct"] == 0.0  # No param scores -> 0

    def test_multiple_models(self):
        targets = [self._make_target("m1"), self._make_target("m2")]
        results = [
            {"model_id": "m1", "success": True, "tool_selection_score": 1.0, "param_accuracy": 1.0, "overall_score": 1.0},
            {"model_id": "m2", "success": True, "tool_selection_score": 0.5, "param_accuracy": 0.5, "overall_score": 0.5},
        ]
        summaries = _compute_eval_summaries(results, targets)
        assert len(summaries) == 2

    def test_empty_results(self):
        summaries = _compute_eval_summaries([], [])
        assert summaries == []

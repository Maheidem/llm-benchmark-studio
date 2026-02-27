"""Tests for T2: Error Taxonomy Classification.

Tests classify_error_type() pure function in routers/helpers.py,
and the error_type field in tool eval results.

Run: uv run pytest tests/test_error_taxonomy.py -v
"""

import pytest
from routers.helpers import classify_error_type

pytestmark = pytest.mark.asyncio(loop_scope="session")

VALID_ERROR_TYPES = {
    "tool_hallucination",
    "argument_hallucination",
    "invalid_invocation",
    "partial_execution",
    "output_hallucination",
    "invalid_reasoning",
    "reentrant_failure",
    "unclassified",
}


# ===========================================================================
# Unit tests — pure function, no DB needed
# ===========================================================================

class TestClassifyErrorType:
    def test_success_returns_none(self):
        """Passing case: no error type assigned."""
        result = classify_error_type(
            success=True,
            actual_tool="get_weather",
            actual_params={"city": "Paris"},
            expected_tool="get_weather",
            expected_params={"city": "Paris"},
            tool_names_in_suite={"get_weather"},
            overall_score=1.0,
        )
        assert result is None

    def test_failure_no_tool_call_returns_invalid_invocation(self):
        """API-level failure (success=False) returns invalid_invocation first."""
        result = classify_error_type(
            success=False,
            actual_tool=None,
            actual_params=None,
            expected_tool="get_weather",
            expected_params={"city": "Paris"},
            tool_names_in_suite={"get_weather"},
            overall_score=0.0,
        )
        assert result == "invalid_invocation"

    def test_params_parse_failed_returns_invalid_invocation(self):
        """params_parse_failed=True returns invalid_invocation."""
        result = classify_error_type(
            success=True,
            actual_tool="get_weather",
            actual_params={},
            expected_tool="get_weather",
            expected_params={"city": "Paris"},
            tool_names_in_suite={"get_weather"},
            overall_score=0.5,
            params_parse_failed=True,
        )
        assert result == "invalid_invocation"

    def test_unknown_tool_returns_tool_hallucination(self):
        """Calling a tool not in the suite is tool_hallucination."""
        result = classify_error_type(
            success=True,
            actual_tool="nonexistent_tool",
            actual_params={},
            expected_tool="get_weather",
            expected_params={"city": "Paris"},
            tool_names_in_suite={"get_weather"},
            overall_score=0.0,
        )
        assert result == "tool_hallucination"

    def test_correct_tool_wrong_params_returns_argument_hallucination(self):
        """Correct tool but wrong argument values returns argument_hallucination."""
        result = classify_error_type(
            success=True,
            actual_tool="get_weather",
            actual_params={"city": "London"},
            expected_tool="get_weather",
            expected_params={"city": "Paris"},
            tool_names_in_suite={"get_weather"},
            overall_score=0.5,
        )
        assert result == "argument_hallucination"

    def test_multi_turn_excessive_rounds_returns_reentrant_failure(self):
        """Multi-turn case exceeding 2*optimal_hops returns reentrant_failure.

        Note: actual_tool must not match expected for this to avoid argument_hallucination.
        We use actual_tool=None (no final tool result) to fall through to multi-turn checks.
        """
        result = classify_error_type(
            success=True,
            actual_tool=None,  # no actual tool result — multi-turn ran but failed
            actual_params=None,
            expected_tool="get_weather",
            expected_params={"city": "Paris"},
            tool_names_in_suite={"get_weather"},
            overall_score=0.5,
            is_multi_turn=True,
            rounds_used=6,
            optimal_hops=2,
        )
        assert result == "reentrant_failure"

    def test_multi_turn_incomplete_returns_partial_execution(self):
        """Multi-turn case that didn't complete all hops returns partial_execution.

        Note: actual_tool must not match expected for this to avoid argument_hallucination.
        We use actual_tool=None to fall through to multi-turn checks.
        """
        result = classify_error_type(
            success=True,
            actual_tool=None,  # no final tool result
            actual_params=None,
            expected_tool="get_weather",
            expected_params={"city": "Paris"},
            tool_names_in_suite={"get_weather"},
            overall_score=0.5,
            is_multi_turn=True,
            rounds_used=1,
            optimal_hops=3,
        )
        assert result == "partial_execution"

    def test_wrong_tool_from_suite_returns_invalid_reasoning(self):
        """Wrong tool selected (but it IS in the suite) returns invalid_reasoning."""
        result = classify_error_type(
            success=True,
            actual_tool="get_forecast",
            actual_params={"city": "Paris"},
            expected_tool="get_weather",
            expected_params={"city": "Paris"},
            tool_names_in_suite={"get_weather", "get_forecast"},
            overall_score=0.0,
        )
        assert result == "invalid_reasoning"

    def test_all_return_values_in_valid_set(self):
        """Every non-None return value must be one of the 8 locked types."""
        cases = [
            dict(success=False, actual_tool=None, actual_params=None,
                 expected_tool="t", expected_params={}, tool_names_in_suite={"t"},
                 overall_score=0.0),
            dict(success=True, actual_tool="bad_tool", actual_params={},
                 expected_tool="good_tool", expected_params={},
                 tool_names_in_suite={"good_tool"}, overall_score=0.0),
            dict(success=True, actual_tool="good_tool", actual_params={"a": "wrong"},
                 expected_tool="good_tool", expected_params={"a": "right"},
                 tool_names_in_suite={"good_tool"}, overall_score=0.5),
            dict(success=True, actual_tool="good_tool", actual_params={},
                 expected_tool="good_tool", expected_params={},
                 tool_names_in_suite={"good_tool"}, overall_score=0.5,
                 is_multi_turn=True, rounds_used=10, optimal_hops=2),
            dict(success=True, actual_tool="good_tool", actual_params={},
                 expected_tool="good_tool", expected_params={},
                 tool_names_in_suite={"good_tool"}, overall_score=0.5,
                 is_multi_turn=True, rounds_used=1, optimal_hops=3),
        ]
        for kwargs in cases:
            result = classify_error_type(**kwargs)
            if result is not None:
                assert result in VALID_ERROR_TYPES, (
                    f"Unexpected error type {result!r} for inputs {kwargs}"
                )

    def test_none_expected_tool_no_actual_tool_returns_none(self):
        """Correct abstention: no tool expected, none called."""
        result = classify_error_type(
            success=True,
            actual_tool=None,
            actual_params=None,
            expected_tool=None,
            expected_params=None,
            tool_names_in_suite=set(),
            overall_score=1.0,
        )
        assert result is None


# ===========================================================================
# API contract tests — error_type appears in eval results
# ===========================================================================

class TestErrorTypeInEvalResult:
    async def _setup_zai_config(self, app_client, auth_headers):
        """Add Zai provider + GLM model to test user config."""
        resp = await app_client.post("/api/config/provider", headers=auth_headers, json={
            "provider_key": "zai",
            "display_name": "Zai",
            "api_base": "https://api.z.ai/api/coding/paas/v4/",
            "api_key_env": "ZAI_API_KEY",
            "model_id_prefix": "",
        })
        assert resp.status_code in (200, 400)
        resp = await app_client.post("/api/config/model", headers=auth_headers, json={
            "provider_key": "zai",
            "id": "GLM-4.5-Air",
            "display_name": "GLM-4.5-Air",
            "context_window": 128000,
        })
        assert resp.status_code in (200, 400)

    async def test_eval_result_has_error_type_field(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Running a tool eval produces results with error_type field."""
        await self._setup_zai_config(app_client, auth_headers)
        from unittest.mock import patch, MagicMock, AsyncMock
        import json

        # Create a minimal suite
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "ET Test Suite",
            "tools": [{"type": "function", "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object",
                               "properties": {"city": {"type": "string"}},
                               "required": ["city"]},
            }}],
            "test_cases": [{"prompt": "Weather in Paris?",
                            "expected_tool": "get_weather",
                            "expected_params": {"city": "Paris"}}],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        # Mock a successful tool call
        mock_msg = MagicMock()
        mock_msg.tool_calls = [MagicMock()]
        mock_msg.tool_calls[0].function.name = "get_weather"
        mock_msg.tool_calls[0].function.arguments = json.dumps({"city": "Paris"})
        mock_msg.content = None
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message = mock_msg
        mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            run_resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
                "suite_id": suite_id,
                "models": ["GLM-4.5-Air"],
            })
        assert run_resp.status_code == 200

        # Check via history detail (ERD v2: results in child table)
        history_resp = await app_client.get("/api/tool-eval/history", headers=auth_headers)
        assert history_resp.status_code == 200
        runs = history_resp.json().get("runs", [])
        if runs:
            detail_resp = await app_client.get(
                f"/api/tool-eval/history/{runs[0]['id']}", headers=auth_headers
            )
            assert detail_resp.status_code == 200
            results = detail_resp.json().get("results", [])
            for r in results:
                assert "error_type" in r, (
                    f"error_type missing from result: {r.keys()}"
                )
                # error_type must be None or one of the 8 valid types
                et = r["error_type"]
                assert et is None or et in VALID_ERROR_TYPES, (
                    f"error_type={et!r} not in valid set"
                )

    async def test_successful_eval_error_type_is_none(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """When eval succeeds with score=1.0, error_type should be None."""
        await self._setup_zai_config(app_client, auth_headers)
        from unittest.mock import patch, MagicMock, AsyncMock
        import json

        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "ET Pass Suite",
            "tools": [{"type": "function", "function": {
                "name": "search",
                "description": "Search",
                "parameters": {"type": "object",
                               "properties": {"query": {"type": "string"}},
                               "required": ["query"]},
            }}],
            "test_cases": [{"prompt": "Search for cats",
                            "expected_tool": "search",
                            "expected_params": {"query": "cats"}}],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        mock_msg = MagicMock()
        mock_msg.tool_calls = [MagicMock()]
        mock_msg.tool_calls[0].function.name = "search"
        mock_msg.tool_calls[0].function.arguments = json.dumps({"query": "cats"})
        mock_msg.content = None
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message = mock_msg
        mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            run_resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
                "suite_id": suite_id,
                "models": ["GLM-4.5-Air"],
            })
        assert run_resp.status_code == 200

        history_resp = await app_client.get("/api/tool-eval/history", headers=auth_headers)
        assert history_resp.status_code == 200
        runs = history_resp.json().get("runs", [])
        if runs:
            detail_resp = await app_client.get(
                f"/api/tool-eval/history/{runs[0]['id']}", headers=auth_headers
            )
            assert detail_resp.status_code == 200
            results = detail_resp.json().get("results", [])
            if results:
                passing = [r for r in results if r.get("overall_score") == 1.0]
                for r in passing:
                    assert r.get("error_type") is None, (
                        f"Passing result should have error_type=None, got {r.get('error_type')!r}"
                    )

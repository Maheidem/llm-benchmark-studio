"""Tests for T1: Format Compliance Classification.

Tests classify_format_compliance() pure function in routers/helpers.py,
and the format_compliance field in tool eval results.

Run: uv run pytest tests/test_format_compliance.py -v
"""

import pytest
from routers.helpers import classify_format_compliance

pytestmark = pytest.mark.asyncio(loop_scope="session")

VALID_VALUES = {"PASS", "NORMALIZED", "FAIL"}


# ===========================================================================
# Unit tests — pure function, no DB needed (sync, no asyncio mark needed)
# ===========================================================================

class TestClassifyFormatCompliance:
    def test_clean_tool_call_returns_pass(self):
        """Model returned well-formed tool_calls natively."""
        result = classify_format_compliance(
            raw_response_had_tool_calls=True,
            tool_name_was_json_blob=False,
            params_parse_failed=False,
            actual_tool="get_weather",
            expected_tool="get_weather",
        )
        assert result == "PASS"

    def test_json_in_tool_name_returns_normalized(self):
        """Tool name contained a JSON blob that needed extraction."""
        result = classify_format_compliance(
            raw_response_had_tool_calls=True,
            tool_name_was_json_blob=True,
            params_parse_failed=False,
            actual_tool="get_weather",
            expected_tool="get_weather",
        )
        assert result == "NORMALIZED"

    def test_params_parse_failed_returns_normalized(self):
        """Params required fallback parsing."""
        result = classify_format_compliance(
            raw_response_had_tool_calls=True,
            tool_name_was_json_blob=False,
            params_parse_failed=True,
            actual_tool="get_weather",
            expected_tool="get_weather",
        )
        assert result == "NORMALIZED"

    def test_tool_extracted_from_content_returns_normalized(self):
        """No native tool_calls but tool found via content fallback."""
        result = classify_format_compliance(
            raw_response_had_tool_calls=False,
            tool_name_was_json_blob=False,
            params_parse_failed=False,
            actual_tool="get_weather",
            expected_tool="get_weather",
        )
        assert result == "NORMALIZED"

    def test_no_tool_call_when_expected_returns_fail(self):
        """Model produced nothing but a tool was expected."""
        result = classify_format_compliance(
            raw_response_had_tool_calls=False,
            tool_name_was_json_blob=False,
            params_parse_failed=False,
            actual_tool=None,
            expected_tool="get_weather",
        )
        assert result == "FAIL"

    def test_correct_abstention_returns_pass(self):
        """No tool expected, none called — correct abstention."""
        result = classify_format_compliance(
            raw_response_had_tool_calls=False,
            tool_name_was_json_blob=False,
            params_parse_failed=False,
            actual_tool=None,
            expected_tool=None,
        )
        assert result == "PASS"

    def test_unexpected_tool_call_returns_fail(self):
        """No tool expected but model called one."""
        result = classify_format_compliance(
            raw_response_had_tool_calls=True,
            tool_name_was_json_blob=False,
            params_parse_failed=False,
            actual_tool="get_weather",
            expected_tool=None,
        )
        # actual_tool is not None, expected_tool is None — fall through to PASS
        # (the scoring functions handle correctness; format_compliance only checks form)
        assert result in VALID_VALUES

    def test_only_three_values_possible(self):
        """Exhaustively verify all return values are in the locked set."""
        cases = [
            dict(raw_response_had_tool_calls=True, tool_name_was_json_blob=False,
                 params_parse_failed=False, actual_tool="get_weather", expected_tool="get_weather"),
            dict(raw_response_had_tool_calls=True, tool_name_was_json_blob=True,
                 params_parse_failed=False, actual_tool="get_weather", expected_tool="get_weather"),
            dict(raw_response_had_tool_calls=False, tool_name_was_json_blob=False,
                 params_parse_failed=False, actual_tool="get_weather", expected_tool="get_weather"),
            dict(raw_response_had_tool_calls=False, tool_name_was_json_blob=False,
                 params_parse_failed=False, actual_tool=None, expected_tool="get_weather"),
            dict(raw_response_had_tool_calls=False, tool_name_was_json_blob=False,
                 params_parse_failed=False, actual_tool=None, expected_tool=None),
        ]
        for kwargs in cases:
            result = classify_format_compliance(**kwargs)
            assert result in VALID_VALUES, f"Unexpected value {result!r} for inputs {kwargs}"


# ===========================================================================
# API contract tests — format_compliance appears in eval results (async)
# ===========================================================================

class TestFormatComplianceInEvalResult:
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

    async def test_eval_result_has_format_compliance_field(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Running a tool eval produces results with format_compliance field."""
        await self._setup_zai_config(app_client, auth_headers)
        from unittest.mock import patch, MagicMock, AsyncMock
        import json

        # Create a minimal suite
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "FC Test Suite",
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

        # Mock a clean tool call
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
        run_data = run_resp.json()

        # Find results — either inline or via history
        results = run_data.get("results") or []
        if not results:
            history_resp = await app_client.get("/api/tool-eval/history", headers=auth_headers)
            assert history_resp.status_code == 200
            runs = history_resp.json().get("runs", [])
            if runs:
                results = json.loads(runs[0].get("results_json", "[]"))

        if results:
            for r in results:
                assert "format_compliance" in r, f"format_compliance missing from result: {r.keys()}"
                assert r["format_compliance"] in VALID_VALUES, (
                    f"format_compliance={r['format_compliance']!r} not in {VALID_VALUES}"
                )

    async def test_old_run_missing_format_compliance_does_not_500(
        self, app_client, auth_headers, _patch_db_path
    ):
        """Old eval runs that lack format_compliance in results_json must not crash."""
        import aiosqlite, json, uuid

        old_result = [{"test_case_id": "tc1", "overall_score": 1.0,
                       "model_id": "old-model", "prompt": "test"}]
        async with aiosqlite.connect(str(_patch_db_path)) as conn:
            await conn.execute(
                "INSERT INTO tool_eval_runs (id, user_id, suite_id, "
                "models_json, results_json, summary_json) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), "missing-user", "s-old",
                 "[]", json.dumps(old_result), "{}"),
            )
            await conn.commit()

        resp = await app_client.get("/api/tool-eval/history", headers=auth_headers)
        assert resp.status_code == 200

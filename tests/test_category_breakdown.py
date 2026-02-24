"""Tests for T3: Category Tagging and Breakdown.

Tests category field in eval results, category_breakdown in summaries,
and category import/export roundtrip.

Run: uv run pytest tests/test_category_breakdown.py -v
"""

import json
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

# ===========================================================================
# Unit tests — _compute_eval_summaries category breakdown
# ===========================================================================

class TestComputeEvalSummariesCategory:
    def test_category_breakdown_groups_by_category(self):
        """Results with different categories produce separate breakdown entries."""
        from routers.helpers import _compute_eval_summaries
        from benchmark import Target

        targets = [Target(model_id="m1", display_name="M1", provider="p")]
        results = [
            {
                "model_id": "m1",
                "success": True,
                "overall_score": 1.0,
                "tool_selection_score": 1.0,
                "param_accuracy": 1.0,
                "category": "simple",
                "error_type": None,
                "format_compliance": "PASS",
                "should_call_tool": True,
            },
            {
                "model_id": "m1",
                "success": True,
                "overall_score": 0.5,
                "tool_selection_score": 1.0,
                "param_accuracy": 0.0,
                "category": "complex",
                "error_type": "argument_hallucination",
                "format_compliance": "PASS",
                "should_call_tool": True,
            },
        ]
        summaries = _compute_eval_summaries(results, targets)
        assert len(summaries) == 1
        breakdown = summaries[0]["category_breakdown"]
        assert "simple" in breakdown
        assert "complex" in breakdown
        assert breakdown["simple"]["cases"] == 1
        assert breakdown["simple"]["passed"] == 1
        assert breakdown["complex"]["cases"] == 1
        assert breakdown["complex"]["passed"] == 0

    def test_category_breakdown_has_accuracy_fields(self):
        """Each category breakdown entry has accuracy_pct, tool_accuracy_pct, overall_pct."""
        from routers.helpers import _compute_eval_summaries
        from benchmark import Target

        targets = [Target(model_id="m1", display_name="M1", provider="p")]
        results = [
            {
                "model_id": "m1",
                "success": True,
                "overall_score": 1.0,
                "tool_selection_score": 1.0,
                "param_accuracy": 1.0,
                "category": "simple",
                "error_type": None,
                "format_compliance": "PASS",
                "should_call_tool": True,
            },
        ]
        summaries = _compute_eval_summaries(results, targets)
        cat_entry = summaries[0]["category_breakdown"]["simple"]
        assert "cases" in cat_entry
        assert "passed" in cat_entry
        assert "accuracy_pct" in cat_entry
        assert "tool_accuracy_pct" in cat_entry
        assert "overall_pct" in cat_entry

    def test_null_category_uses_uncategorized(self):
        """Result with no category field defaults to 'uncategorized' in breakdown."""
        from routers.helpers import _compute_eval_summaries
        from benchmark import Target

        targets = [Target(model_id="m1", display_name="M1", provider="p")]
        results = [
            {
                "model_id": "m1",
                "success": True,
                "overall_score": 1.0,
                "tool_selection_score": 1.0,
                "param_accuracy": 1.0,
                # no 'category' key
                "error_type": None,
                "format_compliance": "PASS",
                "should_call_tool": True,
            },
        ]
        summaries = _compute_eval_summaries(results, targets)
        breakdown = summaries[0]["category_breakdown"]
        assert "uncategorized" in breakdown

    def test_summary_includes_category_breakdown_key(self):
        """category_breakdown is always present in summaries output."""
        from routers.helpers import _compute_eval_summaries
        from benchmark import Target

        targets = [Target(model_id="m1", display_name="M1", provider="p")]
        results = [
            {
                "model_id": "m1",
                "success": True,
                "overall_score": 1.0,
                "tool_selection_score": 1.0,
                "param_accuracy": None,
                "error_type": None,
                "format_compliance": "PASS",
                "should_call_tool": True,
            },
        ]
        summaries = _compute_eval_summaries(results, targets)
        assert "category_breakdown" in summaries[0]


# ===========================================================================
# API contract tests — category field in suite import and eval results
# ===========================================================================

class TestCategoryInImportAndResults:
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

    async def test_import_with_category_preserves_it(
        self, app_client, auth_headers
    ):
        """Importing a suite with category on test_cases stores and returns the category."""
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Category Suite",
            "tools": [{"type": "function", "function": {
                "name": "search",
                "description": "Search",
                "parameters": {"type": "object",
                               "properties": {"q": {"type": "string"}},
                               "required": ["q"]},
            }}],
            "test_cases": [
                {
                    "prompt": "Search for cats",
                    "expected_tool": "search",
                    "expected_params": {"q": "cats"},
                    "category": "complex",
                },
                {
                    "prompt": "Search for dogs",
                    "expected_tool": "search",
                    "expected_params": {"q": "dogs"},
                    # no category — should default to 'simple'
                },
            ],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        # Retrieve test cases and verify categories
        detail_resp = await app_client.get(
            f"/api/tool-suites/{suite_id}", headers=auth_headers
        )
        assert detail_resp.status_code == 200
        data = detail_resp.json()
        cases = data.get("test_cases", [])
        assert len(cases) == 2

        cats = {c.get("category") for c in cases}
        assert "complex" in cats

    async def test_eval_result_has_category_field(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Running a tool eval produces results with a category field."""
        await self._setup_zai_config(app_client, auth_headers)
        from unittest.mock import patch, MagicMock, AsyncMock

        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Cat Field Suite",
            "tools": [{"type": "function", "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object",
                               "properties": {"city": {"type": "string"}},
                               "required": ["city"]},
            }}],
            "test_cases": [{
                "prompt": "Weather in Paris?",
                "expected_tool": "get_weather",
                "expected_params": {"city": "Paris"},
                "category": "simple",
            }],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

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

        history_resp = await app_client.get("/api/tool-eval/history", headers=auth_headers)
        assert history_resp.status_code == 200
        runs = history_resp.json().get("runs", [])
        if runs:
            results = json.loads(runs[0].get("results_json", "[]"))
            if results:
                for r in results:
                    assert "category" in r, f"category missing from result: {r.keys()}"

    async def test_eval_summary_has_category_breakdown(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Eval summary (summary_json) includes category_breakdown per model."""
        await self._setup_zai_config(app_client, auth_headers)
        from unittest.mock import patch, MagicMock, AsyncMock

        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Cat Breakdown Suite",
            "tools": [{"type": "function", "function": {
                "name": "lookup",
                "description": "Lookup",
                "parameters": {"type": "object",
                               "properties": {"term": {"type": "string"}},
                               "required": ["term"]},
            }}],
            "test_cases": [{
                "prompt": "Lookup cats",
                "expected_tool": "lookup",
                "expected_params": {"term": "cats"},
                "category": "simple",
            }],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        mock_msg = MagicMock()
        mock_msg.tool_calls = [MagicMock()]
        mock_msg.tool_calls[0].function.name = "lookup"
        mock_msg.tool_calls[0].function.arguments = json.dumps({"term": "cats"})
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
            try:
                summary = json.loads(runs[0].get("summary_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                summary = []
            if summary:
                for model_summary in summary:
                    assert "category_breakdown" in model_summary, (
                        f"category_breakdown missing from summary: {model_summary.keys()}"
                    )

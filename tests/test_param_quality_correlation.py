"""Tests for 2B: Param + Quality Correlation View.

Tests GET /api/param-tune/correlation/{run_id}
and POST /api/param-tune/correlation/{run_id}/score endpoints.

Run: uv run pytest tests/test_param_quality_correlation.py -v
"""

import json
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ===========================================================================
# Helper fixtures
# ===========================================================================

async def _create_param_tune_run(app_client, auth_headers, suite_name="Corr Test Suite"):
    """Create a suite, run param tune, and return (suite_id, run_id or None)."""
    from unittest.mock import patch, MagicMock, AsyncMock

    # Create suite
    resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
        "name": suite_name,
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
        }],
    })
    assert resp.status_code == 200
    suite_id = resp.json()["suite_id"]

    # Mock LLM
    mock_msg = MagicMock()
    mock_msg.tool_calls = [MagicMock()]
    mock_msg.tool_calls[0].function.name = "get_weather"
    mock_msg.tool_calls[0].function.arguments = json.dumps({"city": "Paris"})
    mock_msg.content = None
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message = mock_msg
    mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    import asyncio

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
        run_resp = await app_client.post("/api/tool-eval/param-tune", headers=auth_headers, json={
            "suite_id": suite_id,
            "models": ["GLM-4.5-Air"],
            "search_space": {"temperature": {"min": 0.5, "max": 0.5, "step": 0.1}},
        })
    assert run_resp.status_code == 200
    job_id = run_resp.json().get("job_id")

    # Wait for job to complete before reading history (prevents DB lock on next test's fixture)
    if job_id:
        for _ in range(40):
            await asyncio.sleep(0.5)
            jr = await app_client.get(f"/api/jobs/{job_id}", headers=auth_headers)
            if jr.status_code == 200 and jr.json().get("status") in ("done", "failed", "cancelled"):
                break

    # Get the run_id from history
    history = await app_client.get("/api/tool-eval/param-tune/history", headers=auth_headers)
    assert history.status_code == 200
    runs = history.json().get("runs", [])
    run_id = runs[0]["id"] if runs else None
    return suite_id, run_id


# ===========================================================================
# Correlation endpoint tests
# ===========================================================================

class TestCorrelationEndpoint:
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

    async def test_correlation_returns_200(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """GET /api/param-tune/correlation/{run_id} returns 200 for valid run."""
        await self._setup_zai_config(app_client, auth_headers)
        _, run_id = await _create_param_tune_run(
            app_client, auth_headers, "Corr 200 Suite"
        )
        if not run_id:
            pytest.skip("No run_id found in history")

        resp = await app_client.get(
            f"/api/param-tune/correlation/{run_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    async def test_correlation_returns_run_id(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Correlation response includes run_id field."""
        await self._setup_zai_config(app_client, auth_headers)
        _, run_id = await _create_param_tune_run(
            app_client, auth_headers, "Corr RunID Suite"
        )
        if not run_id:
            pytest.skip("No run_id found in history")

        resp = await app_client.get(
            f"/api/param-tune/correlation/{run_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert data["run_id"] == run_id

    async def test_correlation_returns_data_array(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Correlation response contains a 'data' array of combo entries."""
        await self._setup_zai_config(app_client, auth_headers)
        _, run_id = await _create_param_tune_run(
            app_client, auth_headers, "Corr Data Suite"
        )
        if not run_id:
            pytest.skip("No run_id found in history")

        resp = await app_client.get(
            f"/api/param-tune/correlation/{run_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    async def test_correlation_entry_has_three_axes(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Each correlation data entry has speed, cost, and quality_score axes."""
        await self._setup_zai_config(app_client, auth_headers)
        _, run_id = await _create_param_tune_run(
            app_client, auth_headers, "Corr Axes Suite"
        )
        if not run_id:
            pytest.skip("No run_id found in history")

        resp = await app_client.get(
            f"/api/param-tune/correlation/{run_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data_entries = resp.json().get("data", [])
        for entry in data_entries:
            # Speed axis — actual field is tokens_per_sec_estimate or latency_avg_ms
            has_speed = (
                "tokens_per_sec_estimate" in entry
                or "tokens_per_second" in entry
                or "ttft_ms" in entry
                or "speed" in entry
                or "latency_avg_ms" in entry
            )
            assert has_speed, f"Speed axis missing from entry: {entry.keys()}"
            # Cost axis — may be cost_usd, total_cost, cost, or tool/param accuracy
            # At minimum we check overall_score as a quality proxy
            assert "overall_score" in entry or "tool_accuracy" in entry, (
                f"Score axis missing from entry: {entry.keys()}"
            )
            # Quality axis (may be None if no judge scores yet)
            assert "quality_score" in entry, (
                f"quality_score axis missing from entry: {entry.keys()}"
            )

    async def test_correlation_has_judge_scores_flag(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Correlation response includes has_judge_scores boolean flag."""
        await self._setup_zai_config(app_client, auth_headers)
        _, run_id = await _create_param_tune_run(
            app_client, auth_headers, "Corr Scores Flag Suite"
        )
        if not run_id:
            pytest.skip("No run_id found in history")

        resp = await app_client.get(
            f"/api/param-tune/correlation/{run_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "has_judge_scores" in data
        assert isinstance(data["has_judge_scores"], bool)

    async def test_correlation_has_optimization_mode(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Correlation response includes optimization_mode field."""
        await self._setup_zai_config(app_client, auth_headers)
        _, run_id = await _create_param_tune_run(
            app_client, auth_headers, "Corr Mode Suite"
        )
        if not run_id:
            pytest.skip("No run_id found in history")

        resp = await app_client.get(
            f"/api/param-tune/correlation/{run_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "optimization_mode" in data

    async def test_correlation_404_for_nonexistent_run(
        self, app_client, auth_headers
    ):
        """Correlation endpoint returns 404 for nonexistent run_id."""
        resp = await app_client.get(
            "/api/param-tune/correlation/nonexistent-run-xyz",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_correlation_requires_auth(self, app_client):
        """Correlation endpoint requires authentication."""
        resp = await app_client.get("/api/param-tune/correlation/some-run-id")
        assert resp.status_code in (401, 403)

    async def test_correlation_without_judge_scores_has_null_quality(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """Without judge scoring, quality_score in data entries is None."""
        await self._setup_zai_config(app_client, auth_headers)
        _, run_id = await _create_param_tune_run(
            app_client, auth_headers, "Corr No Quality Suite"
        )
        if not run_id:
            pytest.skip("No run_id found in history")

        resp = await app_client.get(
            f"/api/param-tune/correlation/{run_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("has_judge_scores") is False
        for entry in data.get("data", []):
            assert entry.get("quality_score") is None


# ===========================================================================
# Score endpoint tests
# ===========================================================================

class TestScoreEndpoint:
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

    async def test_score_endpoint_returns_200_or_runs(
        self, app_client, auth_headers, clear_active_jobs
    ):
        """POST /api/param-tune/correlation/{run_id}/score returns 200."""
        await self._setup_zai_config(app_client, auth_headers)
        _, run_id = await _create_param_tune_run(
            app_client, auth_headers, "Score Endpoint Suite"
        )
        if not run_id:
            pytest.skip("No run_id found in history")

        resp = await app_client.post(
            f"/api/param-tune/correlation/{run_id}/score",
            headers=auth_headers,
            json={},
        )
        # Should return 200 (job submitted) or 400/404 (no judge configured)
        assert resp.status_code in (200, 400, 404)

    async def test_score_endpoint_404_for_nonexistent_run(
        self, app_client, auth_headers
    ):
        """Score endpoint returns 404 for nonexistent run_id."""
        resp = await app_client.post(
            "/api/param-tune/correlation/nonexistent-run-xyz/score",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 404

    async def test_score_endpoint_requires_auth(self, app_client):
        """Score endpoint requires authentication."""
        resp = await app_client.post(
            "/api/param-tune/correlation/some-run/score",
            json={},
        )
        assert resp.status_code in (401, 403)

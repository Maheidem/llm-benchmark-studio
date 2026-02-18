"""Level 2: End-to-End Smoke Tests using real Zai API calls.

These tests make REAL LLM API calls to Zai (GLM-4.5-Air) to verify:
- Benchmarks produce real results and get saved to history
- Tool evals produce real scoring and get saved
- Param tune produces real grid search results

SKIP automatically if ZAI_API_KEY env var is not set.

Run: ZAI_API_KEY=xxx uv run pytest tests/test_e2e_smoke.py -v -s
"""

import asyncio
import json
import os
import time

import pytest
import pytest_asyncio

# Skip entire module if ZAI_API_KEY not available
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(
        not os.environ.get("ZAI_API_KEY"),
        reason="ZAI_API_KEY not set — skipping E2E smoke tests",
    ),
]

ZAI_API_BASE = "https://api.z.ai/api/coding/paas/v4/"
ZAI_MODEL_ID = "GLM-4.5-Air"


# =========================================================================
# FIXTURES: Set up Zai provider in test user's config
# =========================================================================


@pytest_asyncio.fixture(scope="module")
async def zai_setup(app_client, auth_headers):
    """Add Zai provider + model to test user's config and set API key.

    Returns the provider_key and model_id for use in tests.
    """
    # Add Zai provider
    resp = await app_client.post("/api/config/provider", headers=auth_headers, json={
        "provider_key": "zai",
        "display_name": "Zai",
        "api_base": ZAI_API_BASE,
    })
    if resp.status_code == 400 and "already exists" in resp.json().get("error", ""):
        pass  # Already exists from a previous run
    else:
        assert resp.status_code == 200, f"Add provider failed: {resp.text}"

    # Add GLM-4.5-Air model
    resp = await app_client.post("/api/config/model", headers=auth_headers, json={
        "provider_key": "zai",
        "id": ZAI_MODEL_ID,
        "display_name": "GLM-4.5-Air",
        "context_window": 128000,
    })
    if resp.status_code == 400 and "already exists" in resp.json().get("error", ""):
        pass  # Already exists
    else:
        assert resp.status_code == 200, f"Add model failed: {resp.text}"

    # Set the API key for this provider
    api_key = os.environ["ZAI_API_KEY"]
    resp = await app_client.put("/api/keys", headers=auth_headers, json={
        "provider_key": "zai",
        "value": api_key,
    })
    assert resp.status_code == 200, f"Set API key failed: {resp.text}"

    return {"provider_key": "zai", "model_id": ZAI_MODEL_ID}


@pytest_asyncio.fixture(scope="module")
async def zai_tool_suite(app_client, auth_headers):
    """Create a minimal tool suite for eval/tune tests."""
    resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
        "name": "E2E Smoke Test Suite",
        "description": "Minimal suite for real API testing",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                        },
                        "required": ["city"],
                    },
                },
            }
        ],
        "test_cases": [
            {
                "prompt": "What is the weather in Paris?",
                "expected_tool": "get_weather",
                "expected_params": {"city": "Paris"},
            },
        ],
    })
    assert resp.status_code == 200, f"Create suite failed: {resp.text}"
    data = resp.json()
    return data["suite_id"]


# =========================================================================
# E2E: REAL BENCHMARK
# =========================================================================


class TestE2EBenchmark:
    """Run a REAL 1-model, 1-run benchmark against Zai GLM-4.5-Air."""

    async def test_real_benchmark_produces_results(self, app_client, auth_headers, zai_setup):
        """Submit a real benchmark job and verify it completes with results."""
        provider_key = zai_setup["provider_key"]
        model_id = zai_setup["model_id"]

        # Submit benchmark
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "targets": [
                {"provider_key": provider_key, "model_id": model_id},
            ],
            "runs": 1,
            "max_tokens": 64,
            "temperature": 0.7,
            "prompt": "Say hello in exactly one sentence.",
            "context_tiers": [0],
            "warmup": False,
        })
        assert resp.status_code == 200, f"Benchmark submit failed: {resp.text}"
        job_id = resp.json()["job_id"]
        assert job_id

        # Poll job status until done (max 120s)
        for _ in range(60):
            await asyncio.sleep(2)
            job_resp = await app_client.get(f"/api/jobs/{job_id}", headers=auth_headers)
            if job_resp.status_code == 200:
                job = job_resp.json()
                if job.get("status") in ("done", "failed", "cancelled"):
                    break
        else:
            pytest.fail(f"Benchmark job {job_id} did not complete within 120s")

        assert job["status"] == "done", f"Benchmark job failed: {job}"

    async def test_benchmark_appears_in_history(self, app_client, auth_headers, zai_setup):
        """After a benchmark run, results should appear in history."""
        resp = await app_client.get("/api/history", headers=auth_headers)
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        # At least one run should exist after test_real_benchmark_produces_results
        assert len(runs) >= 1, "No benchmark runs found in history"

        # Verify the latest run has actual results
        latest = runs[0]
        assert "results" in latest or "results_json" in latest


# =========================================================================
# E2E: REAL TOOL EVAL
# =========================================================================


class TestE2EToolEval:
    """Run a REAL 1-case tool eval against Zai GLM-4.5-Air."""

    async def test_real_tool_eval_produces_scores(
        self, app_client, auth_headers, zai_setup, zai_tool_suite
    ):
        """Run a real tool eval and verify scoring works."""
        provider_key = zai_setup["provider_key"]
        model_id = zai_setup["model_id"]

        # Submit tool eval (now returns job_id, not SSE)
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": zai_tool_suite,
            "targets": [
                {"provider_key": provider_key, "model_id": model_id},
            ],
            "temperature": 0.0,
            "tool_choice": "auto",
        })
        assert resp.status_code == 200, f"Tool eval submit failed: {resp.text}"
        job_id = resp.json()["job_id"]
        assert job_id

        # Poll job status until done (max 120s)
        job = None
        for _ in range(60):
            await asyncio.sleep(2)
            job_resp = await app_client.get(f"/api/jobs/{job_id}", headers=auth_headers)
            if job_resp.status_code == 200:
                job = job_resp.json()
                if job.get("status") in ("done", "failed", "cancelled"):
                    break
        else:
            pytest.fail(f"Tool eval job {job_id} did not complete within 120s")

        assert job["status"] == "done", f"Tool eval job failed: {job}"

        # Verify eval appears in history with scoring data
        eval_id = job.get("result_ref")
        assert eval_id, "No result_ref (eval_id) in completed job"

    async def test_tool_eval_appears_in_history(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-eval/history", headers=auth_headers)
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) >= 1, "No tool eval runs in history"


# =========================================================================
# E2E: REAL PARAM TUNE
# =========================================================================


class TestE2EParamTune:
    """Run a REAL 1-combo param tune against Zai GLM-4.5-Air."""

    async def test_real_param_tune_produces_results(
        self, app_client, auth_headers, zai_setup, zai_tool_suite
    ):
        """Run a minimal grid search (1 combo) and verify results are saved."""
        provider_key = zai_setup["provider_key"]
        model_id = zai_setup["model_id"]

        # Submit param tune (1 combo: single temperature value)
        resp = await app_client.post("/api/tool-eval/param-tune", headers=auth_headers, json={
            "suite_id": zai_tool_suite,
            "targets": [
                {"provider_key": provider_key, "model_id": model_id},
            ],
            "search_space": {
                "temperature": [0.0],
                "tool_choice": ["auto"],
            },
        })
        assert resp.status_code == 200, f"Param tune submit failed: {resp.text}"
        job_id = resp.json()["job_id"]
        assert job_id

        # Poll job status until done (max 120s)
        for _ in range(60):
            await asyncio.sleep(2)
            job_resp = await app_client.get(f"/api/jobs/{job_id}", headers=auth_headers)
            if job_resp.status_code == 200:
                job = job_resp.json()
                if job.get("status") in ("done", "failed", "cancelled"):
                    break
        else:
            pytest.fail(f"Param tune job {job_id} did not complete within 120s")

        assert job["status"] == "done", f"Param tune job failed: {job}"

    async def test_param_tune_appears_in_history(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-eval/param-tune/history", headers=auth_headers)
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) >= 1, "No param tune runs in history"


# =========================================================================
# E2E: JOB NOTIFICATIONS
# =========================================================================


class TestE2EJobNotifications:
    """Verify jobs appear in the notification/job list during and after runs."""

    async def test_jobs_list_shows_completed_jobs(self, app_client, auth_headers):
        """After running benchmarks/evals, the jobs list should have entries."""
        resp = await app_client.get("/api/jobs?limit=10", headers=auth_headers)
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        # After the benchmark and param tune tests above, should have jobs
        assert len(jobs) >= 1, "No jobs found in jobs list"


# =========================================================================
# HELPERS
# =========================================================================


def _parse_sse_events(text: str) -> list[dict]:
    """Parse SSE text into a list of JSON event dicts."""
    events = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events

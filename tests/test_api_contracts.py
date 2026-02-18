"""Level 1: API Contract Tests for LLM Benchmark Studio.

Tests EVERY API endpoint with the EXACT JSON body format the frontend sends.
Uses FastAPI TestClient (httpx.AsyncClient) with a real temporary SQLite DB.
LLM calls are NOT made here — we test routing, validation, auth, and DB flows.

Run: uv run pytest tests/test_api_contracts.py -v
"""

import json
import os
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio(loop_scope="session")


# =========================================================================
# AUTH FLOW
# =========================================================================


class TestAuthFlow:
    """Auth: register, login, refresh, me, logout."""

    async def test_register_new_user(self, app_client):
        resp = await app_client.post("/api/auth/register", json={
            "email": "newuser@test.local",
            "password": "SecurePass99!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["email"] == "newuser@test.local"

    async def test_register_duplicate_email(self, app_client):
        # Register first
        await app_client.post("/api/auth/register", json={
            "email": "dup@test.local",
            "password": "SecurePass99!",
        })
        # Try again
        resp = await app_client.post("/api/auth/register", json={
            "email": "dup@test.local",
            "password": "SecurePass99!",
        })
        assert resp.status_code == 409

    async def test_register_short_password(self, app_client):
        resp = await app_client.post("/api/auth/register", json={
            "email": "short@test.local",
            "password": "abc",
        })
        assert resp.status_code == 400

    async def test_register_invalid_email(self, app_client):
        resp = await app_client.post("/api/auth/register", json={
            "email": "not-an-email",
            "password": "SecurePass99!",
        })
        assert resp.status_code == 400

    async def test_login_success(self, app_client, test_user):
        user, _ = test_user
        resp = await app_client.post("/api/auth/login", json={
            "email": user["email"],
            "password": "TestPass123!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["id"] == user["id"]

    async def test_login_wrong_password(self, app_client, test_user):
        user, _ = test_user
        resp = await app_client.post("/api/auth/login", json={
            "email": user["email"],
            "password": "WrongPassword!",
        })
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, app_client):
        resp = await app_client.post("/api/auth/login", json={
            "email": "nobody@test.local",
            "password": "whatever",
        })
        assert resp.status_code == 401

    async def test_me_with_valid_token(self, app_client, auth_headers):
        resp = await app_client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "user" in data
        assert "email" in data["user"]

    async def test_me_without_token(self, app_client):
        resp = await app_client.get("/api/auth/me")
        assert resp.status_code == 401

    async def test_me_with_invalid_token(self, app_client):
        resp = await app_client.get("/api/auth/me", headers={
            "Authorization": "Bearer invalid-token-here",
        })
        assert resp.status_code == 401

    async def test_cli_token_generation(self, app_client, auth_headers):
        resp = await app_client.post("/api/auth/cli-token", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["expires_in_days"] == 30

    async def test_logout(self, app_client):
        # Register + login to get a session, then logout
        await app_client.post("/api/auth/register", json={
            "email": "logoutuser@test.local",
            "password": "LogoutPass123!",
        })
        resp = await app_client.post("/api/auth/logout")
        assert resp.status_code == 200


# =========================================================================
# HEALTH / META ENDPOINTS
# =========================================================================


class TestHealthEndpoints:
    """Non-auth endpoints: healthz, robots.txt, sitemap.xml."""

    async def test_healthz(self, app_client):
        resp = await app_client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    async def test_robots_txt(self, app_client):
        resp = await app_client.get("/robots.txt")
        assert resp.status_code == 200
        assert "User-agent" in resp.text

    async def test_sitemap_xml(self, app_client):
        resp = await app_client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert "urlset" in resp.text


# =========================================================================
# CONFIG CRUD
# =========================================================================


class TestConfigEndpoints:
    """Config: GET /api/config, CRUD providers and models."""

    async def test_get_config(self, app_client, auth_headers):
        resp = await app_client.get("/api/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "defaults" in data
        assert "providers" in data
        # Every provider should have provider_key
        for prov_name, prov_data in data["providers"].items():
            assert "provider_key" in prov_data, (
                f"Provider '{prov_name}' missing provider_key in /api/config response"
            )

    async def test_get_config_returns_default_providers(self, app_client, auth_headers):
        resp = await app_client.get("/api/config", headers=auth_headers)
        data = resp.json()
        # Default config should have at least OpenAI
        provider_keys = {p["provider_key"] for p in data["providers"].values()}
        assert "openai" in provider_keys

    async def test_add_provider(self, app_client, auth_headers):
        resp = await app_client.post("/api/config/provider", headers=auth_headers, json={
            "provider_key": "test_provider",
            "display_name": "Test Provider",
            "api_base": "http://localhost:1234/v1",
        })
        assert resp.status_code == 200
        assert resp.json()["provider_key"] == "test_provider"

    async def test_add_duplicate_provider(self, app_client, auth_headers):
        # Ensure it exists first
        await app_client.post("/api/config/provider", headers=auth_headers, json={
            "provider_key": "dup_provider",
            "display_name": "Dup Provider",
        })
        # Try again
        resp = await app_client.post("/api/config/provider", headers=auth_headers, json={
            "provider_key": "dup_provider",
            "display_name": "Dup Provider",
        })
        assert resp.status_code == 400

    async def test_add_provider_missing_key(self, app_client, auth_headers):
        resp = await app_client.post("/api/config/provider", headers=auth_headers, json={
            "display_name": "No Key Provider",
        })
        assert resp.status_code == 400

    async def test_update_provider(self, app_client, auth_headers):
        resp = await app_client.put("/api/config/provider", headers=auth_headers, json={
            "provider_key": "test_provider",
            "display_name": "Updated Test Provider",
            "api_base": "http://localhost:5678/v1",
        })
        assert resp.status_code == 200

    async def test_update_nonexistent_provider(self, app_client, auth_headers):
        resp = await app_client.put("/api/config/provider", headers=auth_headers, json={
            "provider_key": "nonexistent_xyz",
            "display_name": "Ghost",
        })
        assert resp.status_code == 404

    async def test_add_model(self, app_client, auth_headers):
        resp = await app_client.post("/api/config/model", headers=auth_headers, json={
            "provider_key": "test_provider",
            "id": "test-model-1",
            "display_name": "Test Model 1",
            "context_window": 32000,
        })
        assert resp.status_code == 200
        assert resp.json()["model_id"] == "test-model-1"

    async def test_add_duplicate_model(self, app_client, auth_headers):
        # Add again
        resp = await app_client.post("/api/config/model", headers=auth_headers, json={
            "provider_key": "test_provider",
            "id": "test-model-1",
        })
        assert resp.status_code == 400

    async def test_add_model_missing_fields(self, app_client, auth_headers):
        resp = await app_client.post("/api/config/model", headers=auth_headers, json={
            "provider_key": "test_provider",
            # missing "id"
        })
        assert resp.status_code == 400

    async def test_update_model(self, app_client, auth_headers):
        resp = await app_client.put("/api/config/model", headers=auth_headers, json={
            "provider_key": "test_provider",
            "model_id": "test-model-1",
            "display_name": "Updated Test Model",
            "context_window": 64000,
        })
        assert resp.status_code == 200

    async def test_update_nonexistent_model(self, app_client, auth_headers):
        resp = await app_client.put("/api/config/model", headers=auth_headers, json={
            "model_id": "ghost-model",
            "display_name": "Ghost",
        })
        assert resp.status_code == 404

    async def test_delete_model(self, app_client, auth_headers):
        # Add a model to delete
        await app_client.post("/api/config/model", headers=auth_headers, json={
            "provider_key": "test_provider",
            "id": "delete-me-model",
        })
        resp = await app_client.request(
            "DELETE", "/api/config/model", headers=auth_headers,
            json={"provider_key": "test_provider", "model_id": "delete-me-model"},
        )
        assert resp.status_code == 200

    async def test_delete_nonexistent_model(self, app_client, auth_headers):
        resp = await app_client.request(
            "DELETE", "/api/config/model", headers=auth_headers,
            json={"provider_key": "test_provider", "model_id": "nope"},
        )
        assert resp.status_code == 404

    async def test_delete_provider(self, app_client, auth_headers):
        # Add one to delete
        await app_client.post("/api/config/provider", headers=auth_headers, json={
            "provider_key": "to_delete_prov",
            "display_name": "To Delete",
        })
        resp = await app_client.request(
            "DELETE", "/api/config/provider", headers=auth_headers,
            json={"provider_key": "to_delete_prov"},
        )
        assert resp.status_code == 200


# =========================================================================
# API KEY MANAGEMENT (per-user keys)
# =========================================================================


class TestApiKeyManagement:
    """PUT/GET/DELETE /api/keys — per-user encrypted API keys."""

    async def test_get_keys_initial(self, app_client, auth_headers):
        resp = await app_client.get("/api/keys", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        # Should list providers from user config
        assert isinstance(data["keys"], list)

    async def test_set_key(self, app_client, auth_headers):
        resp = await app_client.put("/api/keys", headers=auth_headers, json={
            "provider_key": "openai",
            "value": "sk-test-fake-key-1234567890",
        })
        assert resp.status_code == 200
        assert resp.json()["provider_key"] == "openai"

    async def test_set_key_missing_provider(self, app_client, auth_headers):
        resp = await app_client.put("/api/keys", headers=auth_headers, json={
            "value": "sk-something",
        })
        assert resp.status_code == 400

    async def test_set_key_missing_value(self, app_client, auth_headers):
        resp = await app_client.put("/api/keys", headers=auth_headers, json={
            "provider_key": "openai",
        })
        assert resp.status_code == 400

    async def test_set_key_nonexistent_provider(self, app_client, auth_headers):
        resp = await app_client.put("/api/keys", headers=auth_headers, json={
            "provider_key": "fake_provider_xyz",
            "value": "some-key",
        })
        assert resp.status_code == 404

    async def test_delete_key(self, app_client, auth_headers):
        # Set it first
        await app_client.put("/api/keys", headers=auth_headers, json={
            "provider_key": "openai",
            "value": "sk-deleteme",
        })
        resp = await app_client.request(
            "DELETE", "/api/keys", headers=auth_headers,
            json={"provider_key": "openai"},
        )
        assert resp.status_code == 200

    async def test_delete_key_not_found(self, app_client, auth_headers):
        resp = await app_client.request(
            "DELETE", "/api/keys", headers=auth_headers,
            json={"provider_key": "never_set_key"},
        )
        assert resp.status_code == 404


# =========================================================================
# BENCHMARK ENDPOINT
# =========================================================================


class TestBenchmarkEndpoint:
    """POST /api/benchmark — validates request body, returns job_id."""

    async def test_benchmark_new_target_format(self, app_client, auth_headers):
        """Test the new targets: [{provider_key, model_id}] format."""
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "targets": [
                {"provider_key": "openai", "model_id": "gpt-4o"},
            ],
            "runs": 1,
            "max_tokens": 128,
            "temperature": 0.7,
            "prompt": "Say hello",
            "context_tiers": [0],
            "warmup": False,
        })
        # Should succeed or fail gracefully (no API key = will fail later in job)
        # We test that the endpoint accepts the format and returns job_id
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data

    async def test_benchmark_legacy_format(self, app_client, auth_headers):
        """Test the legacy models: [model_id] format."""
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "models": ["gpt-4o"],
            "runs": 1,
            "max_tokens": 128,
            "temperature": 0.7,
            "prompt": "Say hello",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data

    async def test_benchmark_empty_models(self, app_client, auth_headers):
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "models": [],
            "runs": 1,
        })
        assert resp.status_code == 400

    async def test_benchmark_invalid_runs(self, app_client, auth_headers):
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "models": ["gpt-4o"],
            "runs": 25,  # max 20
        })
        assert resp.status_code == 400

    async def test_benchmark_invalid_temperature(self, app_client, auth_headers):
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "models": ["gpt-4o"],
            "runs": 1,
            "temperature": 3.0,  # max 2.0
        })
        assert resp.status_code == 400

    async def test_benchmark_invalid_max_tokens(self, app_client, auth_headers):
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "models": ["gpt-4o"],
            "runs": 1,
            "max_tokens": 99999,  # max 16384
        })
        assert resp.status_code == 400

    async def test_benchmark_cancel(self, app_client, auth_headers):
        resp = await app_client.post("/api/benchmark/cancel", headers=auth_headers, json={})
        assert resp.status_code == 200


# =========================================================================
# HISTORY
# =========================================================================


class TestHistoryEndpoints:
    """GET /api/history, GET /api/history/{run_id}, DELETE /api/history/{run_id}."""

    async def test_get_history_empty(self, app_client, auth_headers):
        resp = await app_client.get("/api/history", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert isinstance(data["runs"], list)

    async def test_get_history_run_not_found(self, app_client, auth_headers):
        resp = await app_client.get("/api/history/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404

    async def test_delete_history_run_not_found(self, app_client, auth_headers):
        resp = await app_client.delete("/api/history/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404


# =========================================================================
# TOOL SUITES CRUD
# =========================================================================


class TestToolSuites:
    """CRUD for tool suites and test cases."""

    async def test_create_suite(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Contract Test Suite",
            "description": "For testing",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string"},
                            },
                        },
                    },
                }
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "suite_id" in data

    async def test_create_suite_missing_name(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "tools": [],
        })
        assert resp.status_code == 400

    async def test_list_suites(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-suites", headers=auth_headers)
        assert resp.status_code == 200
        assert "suites" in resp.json()

    async def test_get_suite(self, app_client, auth_headers):
        # Create one first
        create_resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Get Suite Test",
            "tools": [],
        })
        suite_id = create_resp.json()["suite_id"]

        resp = await app_client.get(f"/api/tool-suites/{suite_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Get Suite Test"

    async def test_get_nonexistent_suite(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-suites/fake-id", headers=auth_headers)
        assert resp.status_code == 404

    async def test_update_suite(self, app_client, auth_headers):
        create_resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Update Me",
            "tools": [],
        })
        suite_id = create_resp.json()["suite_id"]

        resp = await app_client.put(f"/api/tool-suites/{suite_id}", headers=auth_headers, json={
            "name": "Updated Name",
            "description": "Now with description",
        })
        assert resp.status_code == 200

    async def test_delete_suite(self, app_client, auth_headers):
        create_resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Delete Me",
            "tools": [],
        })
        suite_id = create_resp.json()["suite_id"]

        resp = await app_client.delete(f"/api/tool-suites/{suite_id}", headers=auth_headers)
        assert resp.status_code == 200

    async def test_import_suite(self, app_client, auth_headers):
        """Test the import endpoint with tools + test cases."""
        from tests.conftest import TOOL_SUITE_FIXTURE
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers,
                                     json=TOOL_SUITE_FIXTURE)
        assert resp.status_code == 200
        data = resp.json()
        assert data["suite_id"]
        assert data["test_cases_created"] == 1

    async def test_export_suite(self, app_client, auth_headers):
        # Create and import
        from tests.conftest import TOOL_SUITE_FIXTURE
        create_resp = await app_client.post("/api/tool-eval/import", headers=auth_headers,
                                            json=TOOL_SUITE_FIXTURE)
        suite_id = create_resp.json()["suite_id"]

        resp = await app_client.get(f"/api/tool-suites/{suite_id}/export", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == TOOL_SUITE_FIXTURE["name"]
        assert len(data["tools"]) == 1


# =========================================================================
# TEST CASES CRUD
# =========================================================================


class TestTestCases:
    """CRUD for test cases within a suite."""

    @pytest_asyncio.fixture
    async def suite_id(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Case CRUD Suite",
            "tools": [{"type": "function", "function": {"name": "test_tool", "description": "test"}}],
        })
        return resp.json()["suite_id"]

    async def test_create_single_case(self, app_client, auth_headers, suite_id):
        resp = await app_client.post(f"/api/tool-suites/{suite_id}/cases", headers=auth_headers, json={
            "prompt": "Test prompt",
            "expected_tool": "test_tool",
            "expected_params": {"key": "value"},
        })
        assert resp.status_code == 200
        assert "case_id" in resp.json()

    async def test_create_bulk_cases(self, app_client, auth_headers, suite_id):
        resp = await app_client.post(f"/api/tool-suites/{suite_id}/cases", headers=auth_headers, json={
            "cases": [
                {"prompt": "Prompt A", "expected_tool": "test_tool"},
                {"prompt": "Prompt B", "expected_tool": "test_tool"},
            ],
        })
        assert resp.status_code == 200
        assert resp.json()["created"] == 2

    async def test_create_case_missing_prompt(self, app_client, auth_headers, suite_id):
        resp = await app_client.post(f"/api/tool-suites/{suite_id}/cases", headers=auth_headers, json={
            "expected_tool": "test_tool",
        })
        assert resp.status_code == 400

    async def test_list_cases(self, app_client, auth_headers, suite_id):
        resp = await app_client.get(f"/api/tool-suites/{suite_id}/cases", headers=auth_headers)
        assert resp.status_code == 200
        assert "cases" in resp.json()


# =========================================================================
# TOOL EVAL ENDPOINT
# =========================================================================


class TestToolEvalEndpoint:
    """POST /api/tool-eval — validates request body."""

    async def test_tool_eval_missing_suite(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "models": ["gpt-4o"],
        })
        assert resp.status_code == 400

    async def test_tool_eval_missing_models(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": "some-id",
        })
        assert resp.status_code == 400

    async def test_tool_eval_invalid_temperature(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": "some-id",
            "models": ["gpt-4o"],
            "temperature": 5.0,
        })
        assert resp.status_code == 400

    async def test_tool_eval_invalid_tool_choice(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": "some-id",
            "models": ["gpt-4o"],
            "tool_choice": "invalid_value",
        })
        assert resp.status_code == 400

    async def test_tool_eval_nonexistent_suite(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": "nonexistent-suite-id",
            "models": ["gpt-4o"],
            "temperature": 0.0,
            "tool_choice": "required",
        })
        assert resp.status_code == 404

    async def test_tool_eval_cancel(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval/cancel", headers=auth_headers)
        assert resp.status_code == 200


# =========================================================================
# PARAM TUNE ENDPOINT
# =========================================================================


class TestParamTuneEndpoint:
    """POST /api/tool-eval/param-tune — validates request body."""

    async def test_param_tune_missing_suite(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval/param-tune", headers=auth_headers, json={
            "models": ["gpt-4o"],
            "search_space": {"temperature": {"min": 0, "max": 1, "step": 0.5}},
        })
        assert resp.status_code == 400

    async def test_param_tune_missing_models(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval/param-tune", headers=auth_headers, json={
            "suite_id": "some-id",
            "search_space": {"temperature": {"min": 0, "max": 1, "step": 0.5}},
        })
        assert resp.status_code == 400

    async def test_param_tune_empty_search_space(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval/param-tune", headers=auth_headers, json={
            "suite_id": "some-id",
            "models": ["gpt-4o"],
            "search_space": {},
        })
        assert resp.status_code == 400

    async def test_param_tune_cancel(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval/param-tune/cancel", headers=auth_headers, json={})
        assert resp.status_code == 200


# =========================================================================
# TOOL EVAL HISTORY
# =========================================================================


class TestToolEvalHistory:
    """Tool eval history endpoints."""

    async def test_list_eval_history(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-eval/history", headers=auth_headers)
        assert resp.status_code == 200
        assert "runs" in resp.json()

    async def test_get_eval_run_not_found(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-eval/history/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    async def test_delete_eval_run_not_found(self, app_client, auth_headers):
        resp = await app_client.delete("/api/tool-eval/history/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    async def test_param_tune_history(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-eval/param-tune/history", headers=auth_headers)
        assert resp.status_code == 200
        assert "runs" in resp.json()

    async def test_prompt_tune_history(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-eval/prompt-tune/history", headers=auth_headers)
        assert resp.status_code == 200
        assert "runs" in resp.json()


# =========================================================================
# JOBS
# =========================================================================


class TestJobEndpoints:
    """Job tracking REST endpoints."""

    async def test_list_jobs(self, app_client, auth_headers):
        resp = await app_client.get("/api/jobs", headers=auth_headers)
        assert resp.status_code == 200
        assert "jobs" in resp.json()

    async def test_get_job_not_found(self, app_client, auth_headers):
        resp = await app_client.get("/api/jobs/nonexistent-job", headers=auth_headers)
        assert resp.status_code == 404

    async def test_cancel_job_not_found(self, app_client, auth_headers):
        resp = await app_client.post("/api/jobs/nonexistent-job/cancel", headers=auth_headers)
        assert resp.status_code == 404


# =========================================================================
# PROMPT TEMPLATES
# =========================================================================


class TestPromptTemplates:
    """Prompt template CRUD."""

    async def test_get_prompts(self, app_client, auth_headers):
        resp = await app_client.get("/api/config/prompts", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # Default config has prompt templates
        assert isinstance(data, dict)

    async def test_add_prompt_template(self, app_client, auth_headers):
        resp = await app_client.post("/api/config/prompts", headers=auth_headers, json={
            "key": "test_prompt",
            "label": "Test Prompt",
            "category": "testing",
            "prompt": "Write a test prompt.",
        })
        assert resp.status_code == 200

    async def test_add_prompt_invalid_key(self, app_client, auth_headers):
        resp = await app_client.post("/api/config/prompts", headers=auth_headers, json={
            "key": "invalid key with spaces",
            "prompt": "Test",
        })
        assert resp.status_code == 400

    async def test_add_prompt_missing_prompt(self, app_client, auth_headers):
        resp = await app_client.post("/api/config/prompts", headers=auth_headers, json={
            "key": "missing_prompt",
        })
        assert resp.status_code == 400


# =========================================================================
# RATE LIMIT
# =========================================================================


class TestRateLimit:
    """Rate limit status endpoint."""

    async def test_get_rate_limit(self, app_client, auth_headers):
        resp = await app_client.get("/api/user/rate-limit", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "limit" in data
        assert "remaining" in data


# =========================================================================
# ADMIN ENDPOINTS
# =========================================================================


class TestAdminEndpoints:
    """Admin-only endpoints require admin role."""

    async def test_admin_users(self, app_client, admin_headers):
        resp = await app_client.get("/api/admin/users", headers=admin_headers)
        assert resp.status_code == 200
        assert "users" in resp.json()

    async def test_admin_stats(self, app_client, admin_headers):
        resp = await app_client.get("/api/admin/stats", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data

    async def test_admin_system(self, app_client, admin_headers):
        resp = await app_client.get("/api/admin/system", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "db_size_mb" in data
        assert "process_uptime_s" in data

    async def test_admin_audit(self, app_client, admin_headers):
        resp = await app_client.get("/api/admin/audit", headers=admin_headers)
        assert resp.status_code == 200
        assert "entries" in resp.json()

    async def test_admin_jobs(self, app_client, admin_headers):
        resp = await app_client.get("/api/admin/jobs", headers=admin_headers)
        assert resp.status_code == 200
        assert "jobs" in resp.json()

    async def test_admin_update_role_self(self, app_client, admin_headers, test_user):
        """Admin cannot change own role."""
        user, _ = test_user
        resp = await app_client.put(
            f"/api/admin/users/{user['id']}/role",
            headers=admin_headers,
            json={"role": "user"},
        )
        assert resp.status_code == 400

    async def test_admin_update_role_invalid(self, app_client, admin_headers, admin_user):
        user, _ = admin_user
        resp = await app_client.put(
            f"/api/admin/users/{user['id']}/role",
            headers=admin_headers,
            json={"role": "superadmin"},  # invalid role
        )
        assert resp.status_code == 400

    async def test_admin_delete_self(self, app_client, admin_headers, test_user):
        user, _ = test_user
        resp = await app_client.delete(
            f"/api/admin/users/{user['id']}",
            headers=admin_headers,
        )
        assert resp.status_code == 400  # Cannot delete self

    async def test_admin_endpoints_require_admin(self, app_client, admin_user):
        """Non-admin user should get 403 on admin endpoints."""
        # admin_user is the SECOND registered user (role=user)
        user, token = admin_user
        if user["role"] == "admin":
            pytest.skip("Second user was also admin")
        headers = {"Authorization": f"Bearer {token}"}
        resp = await app_client.get("/api/admin/users", headers=headers)
        assert resp.status_code == 403


# =========================================================================
# PROVIDER PARAMS
# =========================================================================


class TestProviderParams:
    """Provider params registry and validation endpoints."""

    async def test_get_registry(self, app_client, auth_headers):
        resp = await app_client.get("/api/provider-params/registry", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    async def test_validate_params(self, app_client, auth_headers):
        resp = await app_client.post("/api/provider-params/validate", headers=auth_headers, json={
            "provider_key": "openai",
            "model_id": "gpt-4o",
            "params": {"temperature": 0.5},
        })
        assert resp.status_code == 200


# =========================================================================
# SETTINGS
# =========================================================================


class TestSettings:
    """Phase 10 settings endpoints."""

    async def test_get_settings(self, app_client, auth_headers):
        resp = await app_client.get("/api/settings/phase10", headers=auth_headers)
        assert resp.status_code == 200

    async def test_save_settings(self, app_client, auth_headers):
        resp = await app_client.put("/api/settings/phase10", headers=auth_headers, json={
            "param_support": {},
            "presets": [],
        })
        assert resp.status_code == 200


# =========================================================================
# SCHEDULES
# =========================================================================


class TestSchedules:
    """Scheduled benchmark endpoints."""

    async def test_list_schedules(self, app_client, auth_headers):
        resp = await app_client.get("/api/schedules", headers=auth_headers)
        assert resp.status_code == 200
        assert "schedules" in resp.json()

    async def test_create_schedule(self, app_client, auth_headers):
        resp = await app_client.post("/api/schedules", headers=auth_headers, json={
            "name": "Test Schedule",
            "prompt": "Hello",
            "models": ["gpt-4o"],
            "interval_hours": 24,
        })
        assert resp.status_code == 200

    async def test_delete_nonexistent_schedule(self, app_client, auth_headers):
        resp = await app_client.delete("/api/schedules/nonexistent", headers=auth_headers)
        assert resp.status_code == 404


# =========================================================================
# ANALYTICS
# =========================================================================


class TestAnalytics:
    """Analytics endpoints."""

    async def test_leaderboard(self, app_client, auth_headers):
        resp = await app_client.get("/api/analytics/leaderboard", headers=auth_headers)
        assert resp.status_code == 200

    async def test_trends(self, app_client, auth_headers):
        resp = await app_client.get(
            "/api/analytics/trends?models=gpt-4&metric=tps&period=all",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    async def test_trends_missing_models(self, app_client, auth_headers):
        resp = await app_client.get("/api/analytics/trends", headers=auth_headers)
        assert resp.status_code == 400


# =========================================================================
# EXPORT
# =========================================================================


class TestExport:
    """Export endpoints."""

    async def test_export_history_csv(self, app_client, auth_headers):
        resp = await app_client.get("/api/export/history", headers=auth_headers)
        assert resp.status_code == 200

    async def test_export_settings(self, app_client, auth_headers):
        resp = await app_client.get("/api/export/settings", headers=auth_headers)
        assert resp.status_code == 200

    async def test_export_tool_eval_csv(self, app_client, auth_headers):
        resp = await app_client.get("/api/export/tool-eval", headers=auth_headers)
        assert resp.status_code == 200


# =========================================================================
# ONBOARDING
# =========================================================================


class TestOnboarding:
    """Onboarding status endpoints."""

    async def test_onboarding_status(self, app_client, auth_headers):
        resp = await app_client.get("/api/onboarding/status", headers=auth_headers)
        assert resp.status_code == 200

    async def test_onboarding_complete(self, app_client, auth_headers):
        resp = await app_client.post("/api/onboarding/complete", headers=auth_headers)
        assert resp.status_code == 200


# =========================================================================
# JUDGE ENDPOINTS (validation only — no real LLM calls)
# =========================================================================


class TestJudgeEndpoints:
    """Judge validation and history."""

    async def test_judge_reports_list(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-eval/judge/reports", headers=auth_headers)
        assert resp.status_code == 200
        assert "reports" in resp.json()

    async def test_judge_report_not_found(self, app_client, auth_headers):
        resp = await app_client.get("/api/tool-eval/judge/reports/fake-id", headers=auth_headers)
        assert resp.status_code == 404

    async def test_judge_cancel(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval/judge/cancel", headers=auth_headers)
        assert resp.status_code == 200

    async def test_judge_missing_eval_run(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval/judge", headers=auth_headers, json={
            "judge_model": "gpt-4o",
        })
        assert resp.status_code == 400

    async def test_judge_missing_model(self, app_client, auth_headers):
        resp = await app_client.post("/api/tool-eval/judge", headers=auth_headers, json={
            "eval_run_id": "some-id",
        })
        assert resp.status_code == 400


# =========================================================================
# MCP ENDPOINTS (validation only)
# =========================================================================


class TestMcpEndpoints:
    """MCP discover/import validation."""

    async def test_import_example(self, app_client):
        resp = await app_client.get("/api/tool-eval/import/example")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert "test_cases" in data


# =========================================================================
# PROVIDER KEY FLOW (the critical 3x regression)
# =========================================================================


class TestProviderKeyFlow:
    """Specifically test the provider_key compound key flow that broke 3 times.

    This verifies the full round-trip:
    1. /api/config returns provider_key in provider data
    2. Frontend sends targets with provider_key from config response
    3. /api/benchmark accepts the compound key format
    4. build_targets + _filter_targets correctly matches
    """

    async def test_config_returns_provider_key(self, app_client, auth_headers):
        """Every provider in /api/config must have provider_key."""
        resp = await app_client.get("/api/config", headers=auth_headers)
        data = resp.json()
        for display_name, prov_data in data["providers"].items():
            assert "provider_key" in prov_data, (
                f"Provider '{display_name}' missing provider_key"
            )
            # provider_key should be the config key, not the display_name
            # (they CAN be the same, e.g., "openai" == "OpenAI" is ok)
            assert prov_data["provider_key"], "provider_key must not be empty"

    async def test_roundtrip_config_to_benchmark(self, app_client, auth_headers):
        """Read config, build targets array as frontend would, POST benchmark."""
        # Step 1: Get config
        config_resp = await app_client.get("/api/config", headers=auth_headers)
        config_data = config_resp.json()

        # Step 2: Build targets array exactly as frontend does
        targets = []
        for display_name, prov_data in config_data["providers"].items():
            for model in prov_data["models"]:
                targets.append({
                    "provider_key": prov_data["provider_key"],
                    "model_id": model["model_id"],
                })
                break  # Just first model per provider
            if targets:
                break  # Just first provider

        if not targets:
            pytest.skip("No providers in config")

        # Step 3: POST benchmark with targets format
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "targets": targets,
            "runs": 1,
            "max_tokens": 128,
            "temperature": 0.7,
            "prompt": "Test",
            "warmup": False,
        })
        assert resp.status_code == 200
        assert "job_id" in resp.json()

    async def test_roundtrip_config_to_tool_eval(self, app_client, auth_headers):
        """Read config, build targets array, POST tool-eval (validates format)."""
        config_resp = await app_client.get("/api/config", headers=auth_headers)
        config_data = config_resp.json()

        targets = []
        for display_name, prov_data in config_data["providers"].items():
            for model in prov_data["models"]:
                targets.append({
                    "provider_key": prov_data["provider_key"],
                    "model_id": model["model_id"],
                })
                break
            if targets:
                break

        if not targets:
            pytest.skip("No providers in config")

        # Create a suite first
        suite_resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Roundtrip Test Suite",
            "tools": [{"type": "function", "function": {"name": "noop", "description": "noop"}}],
            "test_cases": [{"prompt": "test", "expected_tool": "noop"}],
        })
        suite_id = suite_resp.json()["suite_id"]

        # POST tool-eval with targets format — expect either 200 or SSE stream
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": suite_id,
            "targets": targets,
            "temperature": 0.0,
            "tool_choice": "required",
        })
        # The tool-eval returns a StreamingResponse (200) even if model calls will fail
        # What matters is it doesn't 400/404 due to target format issues
        assert resp.status_code == 200

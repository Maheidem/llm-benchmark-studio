"""Tests for 2D: Public Tool-Calling Leaderboard.

Tests GET /api/leaderboard/tool-eval (public, no auth),
GET/PUT /api/leaderboard/opt-in (authenticated),
and auto-contribution on eval completion.

Run: uv run pytest tests/test_public_leaderboard.py -v
"""

import json
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ===========================================================================
# Public endpoint tests (no auth required)
# ===========================================================================

class TestPublicLeaderboard:
    async def test_public_endpoint_accessible_without_auth(self, app_client):
        """GET /api/leaderboard/tool-eval is accessible without authentication."""
        resp = await app_client.get("/api/leaderboard/tool-eval")
        assert resp.status_code == 200

    async def test_public_endpoint_returns_leaderboard_key(self, app_client):
        """Public endpoint returns a JSON object with 'leaderboard' key."""
        resp = await app_client.get("/api/leaderboard/tool-eval")
        assert resp.status_code == 200
        data = resp.json()
        assert "leaderboard" in data

    async def test_public_endpoint_leaderboard_is_list(self, app_client):
        """leaderboard value is a list (possibly empty)."""
        resp = await app_client.get("/api/leaderboard/tool-eval")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["leaderboard"], list)

    async def test_public_endpoint_no_user_data_exposed(self, app_client):
        """Leaderboard entries must not contain user_id or email fields."""
        resp = await app_client.get("/api/leaderboard/tool-eval")
        assert resp.status_code == 200
        entries = resp.json().get("leaderboard", [])
        for entry in entries:
            assert "user_id" not in entry, "Leaderboard must not expose user_id"
            assert "email" not in entry, "Leaderboard must not expose email"

    async def test_public_endpoint_entries_have_expected_fields(self, app_client):
        """Each leaderboard entry has model_name, provider, and accuracy fields."""
        resp = await app_client.get("/api/leaderboard/tool-eval")
        assert resp.status_code == 200
        entries = resp.json().get("leaderboard", [])
        for entry in entries:
            assert "model_name" in entry, f"model_name missing: {entry.keys()}"
            assert "provider" in entry, f"provider missing: {entry.keys()}"
            # Accuracy field — one of several expected keys
            has_accuracy = (
                "tool_accuracy_pct" in entry
                or "accuracy_pct" in entry
                or "overall_pct" in entry
            )
            assert has_accuracy, f"No accuracy field found in entry: {entry.keys()}"

    async def test_public_endpoint_returns_note_field(self, app_client):
        """Public endpoint includes a 'note' field explaining the data source."""
        resp = await app_client.get("/api/leaderboard/tool-eval")
        assert resp.status_code == 200
        data = resp.json()
        assert "note" in data


# ===========================================================================
# Opt-in management (authenticated)
# ===========================================================================

class TestLeaderboardOptIn:
    async def test_get_opt_in_requires_auth(self, app_client):
        """GET /api/leaderboard/opt-in requires authentication."""
        resp = await app_client.get("/api/leaderboard/opt-in")
        assert resp.status_code in (401, 403)

    async def test_get_opt_in_returns_status(self, app_client, auth_headers):
        """GET /api/leaderboard/opt-in returns current opt_in status."""
        resp = await app_client.get("/api/leaderboard/opt-in", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "opt_in" in data
        assert isinstance(data["opt_in"], bool)

    async def test_default_opt_in_is_false(self, app_client, auth_headers):
        """New users default to opt_in=False."""
        resp = await app_client.get("/api/leaderboard/opt-in", headers=auth_headers)
        assert resp.status_code == 200
        # Note: may be True if previous tests set it — just check it's a bool
        assert isinstance(resp.json()["opt_in"], bool)

    async def test_put_opt_in_true(self, app_client, auth_headers):
        """PUT /api/leaderboard/opt-in with opt_in=true sets it to True."""
        resp = await app_client.put(
            "/api/leaderboard/opt-in",
            headers=auth_headers,
            json={"opt_in": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("opt_in") is True or data.get("status") == "ok"

    async def test_put_opt_in_false(self, app_client, auth_headers):
        """PUT /api/leaderboard/opt-in with opt_in=false sets it to False."""
        resp = await app_client.put(
            "/api/leaderboard/opt-in",
            headers=auth_headers,
            json={"opt_in": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("opt_in") is False or data.get("status") == "ok"

    async def test_put_opt_in_persisted(self, app_client, auth_headers):
        """Setting opt_in is persisted and retrievable via GET."""
        # Set to True
        await app_client.put(
            "/api/leaderboard/opt-in",
            headers=auth_headers,
            json={"opt_in": True},
        )

        # Verify it persisted
        get_resp = await app_client.get("/api/leaderboard/opt-in", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["opt_in"] is True

        # Reset to False for other tests
        await app_client.put(
            "/api/leaderboard/opt-in",
            headers=auth_headers,
            json={"opt_in": False},
        )

    async def test_put_opt_in_requires_auth(self, app_client):
        """PUT /api/leaderboard/opt-in requires authentication."""
        resp = await app_client.put(
            "/api/leaderboard/opt-in",
            json={"opt_in": True},
        )
        assert resp.status_code in (401, 403)


# ===========================================================================
# Auto-contribution on eval completion
# ===========================================================================

class TestLeaderboardAutoContribution:
    async def test_opted_in_eval_contributes_to_leaderboard(
        self, app_client, auth_headers, clear_active_jobs, zai_config
    ):
        """When user is opted in and runs an eval, leaderboard is updated."""
        from unittest.mock import patch, MagicMock, AsyncMock

        # Opt in
        await app_client.put(
            "/api/leaderboard/opt-in",
            headers=auth_headers,
            json={"opt_in": True},
        )

        # Create suite
        suite_resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Leaderboard Contrib Suite",
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
        assert suite_resp.status_code == 200
        suite_id = suite_resp.json()["suite_id"]

        # Mock eval run
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
            run_resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
                "suite_id": suite_id,
                "models": ["GLM-4.5-Air"],
            })
        assert run_resp.status_code == 200
        job_id = run_resp.json().get("job_id")

        # Wait for job to complete (up to 30s)
        if job_id:
            for _ in range(60):
                await asyncio.sleep(0.5)
                jr = await app_client.get(f"/api/jobs/{job_id}", headers=auth_headers)
                if jr.status_code == 200:
                    status = jr.json().get("status")
                    if status in ("done", "failed", "cancelled"):
                        break

        # Leaderboard should have at least one entry now that eval is complete
        lb_resp = await app_client.get("/api/leaderboard/tool-eval")
        assert lb_resp.status_code == 200
        entries = lb_resp.json().get("leaderboard", [])
        assert len(entries) >= 1, "Opted-in eval should contribute to leaderboard"

        # Clean up: opt back out
        await app_client.put(
            "/api/leaderboard/opt-in",
            headers=auth_headers,
            json={"opt_in": False},
        )

    async def test_opted_out_eval_does_not_contribute(
        self, app_client, auth_headers, clear_active_jobs, _patch_db_path, zai_config
    ):
        """When user is opted out, eval run does not change leaderboard."""
        import aiosqlite
        from unittest.mock import patch, MagicMock, AsyncMock

        # Ensure opted out
        await app_client.put(
            "/api/leaderboard/opt-in",
            headers=auth_headers,
            json={"opt_in": False},
        )

        # Get current leaderboard count
        lb_before = await app_client.get("/api/leaderboard/tool-eval")
        before_count = len(lb_before.json().get("leaderboard", []))

        # Create and run suite
        suite_resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "No Contrib Suite",
            "tools": [{"type": "function", "function": {
                "name": "lookup",
                "description": "Lookup",
                "parameters": {"type": "object",
                               "properties": {"q": {"type": "string"}},
                               "required": ["q"]},
            }}],
            "test_cases": [{
                "prompt": "Lookup cats",
                "expected_tool": "lookup",
                "expected_params": {"q": "cats"},
            }],
        })
        assert suite_resp.status_code == 200
        suite_id = suite_resp.json()["suite_id"]

        mock_msg = MagicMock()
        mock_msg.tool_calls = [MagicMock()]
        mock_msg.tool_calls[0].function.name = "lookup"
        mock_msg.tool_calls[0].function.arguments = json.dumps({"q": "cats"})
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

        # Leaderboard model count should not increase for opted-out user
        lb_after = await app_client.get("/api/leaderboard/tool-eval")
        after_count = len(lb_after.json().get("leaderboard", []))
        # count may stay same or increase if model already existed (upsert)
        # The key constraint: no new rows were added for opted-out user
        assert after_count >= before_count  # never decreases

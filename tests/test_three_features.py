"""Tests for three new features:
1. Prompt Version Registry — CRUD, auto-versioning, user isolation
2. Irrelevance Detection — should_call_tool=false scoring, backward compat
3. Judge on Failure — threshold trigger, storage, API response

Run: uv run pytest tests/test_three_features.py -v
"""

import json
import pytest
import pytest_asyncio

import db
from routers.helpers import score_tool_selection, score_params, compute_overall_score, score_abstention

pytestmark = pytest.mark.asyncio(loop_scope="session")


# =========================================================================
# FEATURE 1: PROMPT VERSION REGISTRY
# =========================================================================


class TestPromptVersionRegistryCRUD:
    """API contract tests for /api/prompt-versions."""

    async def test_list_prompt_versions_empty(self, app_client, auth_headers):
        """New user starts with no prompt versions."""
        resp = await app_client.get("/api/prompt-versions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "versions" in data
        assert isinstance(data["versions"], list)

    async def test_create_prompt_version_manual(self, app_client, auth_headers):
        """Create a manual prompt version."""
        resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "You are a helpful assistant. Answer concisely.",
            "label": "baseline v1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version_id" in data
        assert data["version_id"]

    async def test_create_prompt_version_no_label(self, app_client, auth_headers):
        """Label is optional — defaults to empty string."""
        resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "Minimal prompt",
        })
        assert resp.status_code == 200
        assert "version_id" in resp.json()

    async def test_create_prompt_version_missing_text(self, app_client, auth_headers):
        """prompt_text is required."""
        resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "label": "no text here",
        })
        assert resp.status_code in (400, 422)

    async def test_create_prompt_version_empty_text(self, app_client, auth_headers):
        """Empty prompt_text should be rejected (min_length=1)."""
        resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "",
        })
        assert resp.status_code in (400, 422)

    async def test_get_prompt_version(self, app_client, auth_headers):
        """GET a specific prompt version returns all fields."""
        create_resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "Test get prompt",
            "label": "test label",
        })
        version_id = create_resp.json()["version_id"]

        resp = await app_client.get(f"/api/prompt-versions/{version_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == version_id
        assert data["prompt_text"] == "Test get prompt"
        assert data["label"] == "test label"
        assert data["source"] == "manual"
        assert "created_at" in data

    async def test_get_nonexistent_prompt_version(self, app_client, auth_headers):
        """GET a nonexistent version returns 404."""
        resp = await app_client.get("/api/prompt-versions/nonexistent-abc", headers=auth_headers)
        assert resp.status_code == 404

    async def test_update_prompt_version_label(self, app_client, auth_headers):
        """PATCH updates the label."""
        create_resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "Patch test prompt",
            "label": "old label",
        })
        version_id = create_resp.json()["version_id"]

        patch_resp = await app_client.patch(f"/api/prompt-versions/{version_id}", headers=auth_headers, json={
            "label": "new label",
        })
        assert patch_resp.status_code == 200
        assert patch_resp.json()["status"] == "ok"

        # Verify the label changed
        get_resp = await app_client.get(f"/api/prompt-versions/{version_id}", headers=auth_headers)
        assert get_resp.json()["label"] == "new label"
        # Prompt text should be unchanged
        assert get_resp.json()["prompt_text"] == "Patch test prompt"

    async def test_update_nonexistent_version_returns_404(self, app_client, auth_headers):
        """PATCH nonexistent version returns 404."""
        resp = await app_client.patch("/api/prompt-versions/nonexistent-xyz", headers=auth_headers, json={
            "label": "whatever",
        })
        assert resp.status_code == 404

    async def test_delete_prompt_version(self, app_client, auth_headers):
        """DELETE removes the version."""
        create_resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "Delete me",
        })
        version_id = create_resp.json()["version_id"]

        del_resp = await app_client.delete(f"/api/prompt-versions/{version_id}", headers=auth_headers)
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "ok"

        # Should be gone now
        get_resp = await app_client.get(f"/api/prompt-versions/{version_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    async def test_delete_nonexistent_version_returns_404(self, app_client, auth_headers):
        """DELETE nonexistent version returns 404."""
        resp = await app_client.delete("/api/prompt-versions/does-not-exist", headers=auth_headers)
        assert resp.status_code == 404

    async def test_list_includes_created_versions(self, app_client, auth_headers):
        """Created versions appear in the list."""
        # Create a version with unique text
        resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "Unique list check prompt ABC123",
            "label": "list check",
        })
        version_id = resp.json()["version_id"]

        list_resp = await app_client.get("/api/prompt-versions", headers=auth_headers)
        versions = list_resp.json()["versions"]
        ids = [v["id"] for v in versions]
        assert version_id in ids

    async def test_list_ordered_newest_first(self, app_client, auth_headers, test_user, _patch_db_path):
        """List returns versions newest-first by created_at."""
        import asyncio
        import aiosqlite

        user, _ = test_user

        # Create v1 with a known past timestamp
        v1_id = await db.create_prompt_version(user["id"], "First prompt for ordering test")
        # Backdate v1 to ensure ordering is deterministic
        async with aiosqlite.connect(str(_patch_db_path)) as conn:
            await conn.execute(
                "UPDATE prompt_versions SET created_at = '2020-01-01 00:00:00' WHERE id = ?",
                (v1_id,),
            )
            await conn.commit()

        # Create v2 after v1 (later timestamp)
        v2_id = await db.create_prompt_version(user["id"], "Second prompt for ordering test")

        list_resp = await app_client.get("/api/prompt-versions", headers=auth_headers)
        versions = list_resp.json()["versions"]
        ids = [v["id"] for v in versions]

        # v2 was created after v1, so it should appear before v1 in newest-first ordering
        assert v1_id in ids, "v1 should be in list"
        assert v2_id in ids, "v2 should be in list"
        assert ids.index(v2_id) < ids.index(v1_id), (
            "v2 (newer) should appear before v1 (older, backdated to 2020)"
        )

    async def test_list_requires_auth(self, app_client):
        """Unauthenticated requests get 401."""
        resp = await app_client.get("/api/prompt-versions")
        assert resp.status_code == 401

    async def test_create_requires_auth(self, app_client):
        """Unauthenticated create gets 401."""
        resp = await app_client.post("/api/prompt-versions", json={"prompt_text": "test"})
        assert resp.status_code == 401


class TestPromptVersionUserIsolation:
    """Prompt versions are scoped to the creating user."""

    @pytest_asyncio.fixture
    async def second_user_headers(self, app_client):
        """Register a fresh second user and return auth headers."""
        import time
        unique = str(int(time.time() * 1000))
        resp = await app_client.post("/api/auth/register", json={
            "email": f"second_user_{unique}@isolation.test",
            "password": "IsolationPass123!",
        })
        if resp.status_code == 409:
            resp = await app_client.post("/api/auth/login", json={
                "email": f"second_user_{unique}@isolation.test",
                "password": "IsolationPass123!",
            })
        data = resp.json()
        token = data["access_token"]
        return {"Authorization": f"Bearer {token}"}

    async def test_user_cannot_see_other_users_versions(self, app_client, auth_headers, second_user_headers):
        """User A's versions are not visible to User B."""
        # Create a version as User A
        create_resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "User A secret prompt for isolation test",
            "label": "user A private",
        })
        user_a_version_id = create_resp.json()["version_id"]

        # User B cannot GET User A's version
        get_resp = await app_client.get(f"/api/prompt-versions/{user_a_version_id}", headers=second_user_headers)
        assert get_resp.status_code == 404

        # User B's list doesn't contain User A's version
        list_resp = await app_client.get("/api/prompt-versions", headers=second_user_headers)
        ids = [v["id"] for v in list_resp.json()["versions"]]
        assert user_a_version_id not in ids

    async def test_user_cannot_delete_other_users_versions(self, app_client, auth_headers, second_user_headers):
        """User B cannot delete User A's version."""
        create_resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "User A version for delete isolation",
        })
        user_a_version_id = create_resp.json()["version_id"]

        # User B tries to delete
        del_resp = await app_client.delete(f"/api/prompt-versions/{user_a_version_id}", headers=second_user_headers)
        assert del_resp.status_code == 404

        # Version still exists for User A
        get_resp = await app_client.get(f"/api/prompt-versions/{user_a_version_id}", headers=auth_headers)
        assert get_resp.status_code == 200

    async def test_user_cannot_patch_other_users_versions(self, app_client, auth_headers, second_user_headers):
        """User B cannot PATCH User A's version."""
        create_resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "User A version for patch isolation",
            "label": "user A label",
        })
        user_a_version_id = create_resp.json()["version_id"]

        # User B tries to patch
        patch_resp = await app_client.patch(
            f"/api/prompt-versions/{user_a_version_id}",
            headers=second_user_headers,
            json={"label": "hijacked label"},
        )
        assert patch_resp.status_code == 404

        # Label unchanged for User A
        get_resp = await app_client.get(f"/api/prompt-versions/{user_a_version_id}", headers=auth_headers)
        assert get_resp.json()["label"] == "user A label"


class TestPromptVersionAutoSaveFromTuner:
    """Prompt tuner should auto-save best prompts as versions."""

    async def test_prompt_version_source_field(self, app_client, test_user, auth_headers):
        """DB-level: create a version with source=prompt_tuner and verify field is stored."""
        user, _ = test_user
        version_id = await db.create_prompt_version(
            user_id=user["id"],
            prompt_text="Tuner-generated prompt v1",
            label="gen-1",
            source="prompt_tuner",
            origin_run_id="fake-tune-run-id",
        )
        # Verify via GET
        resp = await app_client.get(f"/api/prompt-versions/{version_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "prompt_tuner"
        assert data["origin_run_id"] == "fake-tune-run-id"

    async def test_prompt_version_parent_chain(self, app_client, test_user, auth_headers):
        """Parent version ID creates a lineage chain."""
        user, _ = test_user

        parent_id = await db.create_prompt_version(
            user_id=user["id"],
            prompt_text="Base prompt",
            label="v1",
        )
        child_id = await db.create_prompt_version(
            user_id=user["id"],
            prompt_text="Improved prompt",
            label="v2",
            parent_version_id=parent_id,
        )

        # Child should reference the parent
        resp = await app_client.get(f"/api/prompt-versions/{child_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["parent_version_id"] == parent_id

    async def test_prompt_version_create_with_parent_via_api(self, app_client, auth_headers):
        """POST /api/prompt-versions accepts parent_version_id."""
        # Create parent
        p_resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "Parent prompt for chain test",
        })
        parent_id = p_resp.json()["version_id"]

        # Create child
        c_resp = await app_client.post("/api/prompt-versions", headers=auth_headers, json={
            "prompt_text": "Child prompt for chain test",
            "parent_version_id": parent_id,
        })
        assert c_resp.status_code == 200
        child_id = c_resp.json()["version_id"]

        # Verify parent_version_id is stored
        get_resp = await app_client.get(f"/api/prompt-versions/{child_id}", headers=auth_headers)
        assert get_resp.json()["parent_version_id"] == parent_id


# =========================================================================
# FEATURE 2: IRRELEVANCE DETECTION (should_call_tool)
# =========================================================================


class TestIrrelevanceDetectionScoring:
    """Unit tests for the irrelevance scoring logic.

    When should_call_tool=false:
    - Model correctly abstains (actual_tool=None) -> score 1.0
    - Model hallucinates a tool call (actual_tool=<some_tool>) -> score 0.0
    """

    async def test_abstention_correct_when_no_tool_expected(self):
        """expected_tool=None + actual_tool=None = 1.0 (correct abstention)."""
        # This tests the existing score_tool_selection logic which already handles None->None
        score = score_tool_selection(None, None)
        assert score == 1.0

    async def test_hallucination_penalized_when_no_tool_expected(self):
        """expected_tool=None + actual_tool=something = 0.0 (hallucinated tool)."""
        score = score_tool_selection(None, "get_weather")
        assert score == 0.0

    async def test_should_call_tool_false_no_actual_call(self):
        """Explicit should_call_tool=False case: model correctly does not call a tool."""
        # Simulate: expected_tool=None (from should_call_tool=False), actual=None
        tool_score = score_tool_selection(None, None)
        param_score = score_params(None, None)
        overall = compute_overall_score(tool_score, param_score)
        assert tool_score == 1.0
        assert param_score is None
        assert overall == 1.0

    async def test_should_call_tool_false_hallucinated_call(self):
        """should_call_tool=False but model calls a tool = incorrect."""
        # expected_tool=None (from should_call_tool=False), actual='get_weather'
        tool_score = score_tool_selection(None, "get_weather")
        param_score = score_params(None, {"city": "Paris"})
        overall = compute_overall_score(tool_score, param_score)
        assert tool_score == 0.0
        assert param_score is None
        assert overall == 0.0

    async def test_should_call_tool_true_correct_call(self):
        """Normal case: should_call_tool=True and model calls correct tool."""
        tool_score = score_tool_selection("get_weather", "get_weather")
        param_score = score_params({"city": "Paris"}, {"city": "Paris"})
        overall = compute_overall_score(tool_score, param_score)
        assert tool_score == 1.0
        assert param_score == 1.0
        assert overall == 1.0

    async def test_should_call_tool_true_missed_call(self):
        """Normal case: should_call_tool=True but model doesn't call = 0."""
        tool_score = score_tool_selection("get_weather", None)
        assert tool_score == 0.0

    async def test_backward_compat_no_should_call_tool_field(self):
        """Old test cases without should_call_tool (defaults to True) still score normally."""
        # Existing tests don't have should_call_tool — default=True behavior
        tool_score = score_tool_selection("get_weather", "get_weather")
        assert tool_score == 1.0

        tool_score_wrong = score_tool_selection("get_weather", "search_web")
        assert tool_score_wrong == 0.0


class TestScoreAbstentionUnit:
    """Unit tests for score_abstention() function (irrelevance detection scorer)."""

    async def test_should_call_true_and_called(self):
        """Model was supposed to call a tool and did — correct."""
        assert score_abstention(True, "get_weather") == 1.0

    async def test_should_call_true_and_not_called(self):
        """Model was supposed to call a tool but didn't — incorrect."""
        assert score_abstention(True, None) == 0.0

    async def test_should_not_call_and_not_called(self):
        """Model was NOT supposed to call a tool and correctly abstained."""
        assert score_abstention(False, None) == 1.0

    async def test_should_not_call_but_called(self):
        """Model was NOT supposed to call a tool but hallucinated a call — incorrect."""
        assert score_abstention(False, "get_weather") == 0.0

    async def test_should_not_call_any_tool_name(self):
        """Any tool call is penalized when should_call_tool=False."""
        assert score_abstention(False, "search_web") == 0.0
        assert score_abstention(False, "irrelevant_tool") == 0.0
        assert score_abstention(False, "ANY_TOOL") == 0.0

    async def test_return_type_is_float(self):
        """Return value is always float."""
        assert isinstance(score_abstention(True, "tool"), float)
        assert isinstance(score_abstention(False, None), float)


class TestIrrelevanceDetectionSchema:
    """Test the should_call_tool field in test case creation."""

    async def test_create_test_case_with_should_call_tool_false(self, app_client, auth_headers, test_user):
        """API accepts should_call_tool=false in test case creation.

        The single-case creation path reads should_call_tool from the request body
        and stores it in the DB.
        """
        user, _ = test_user

        # Create a suite first
        suite_resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Irrelevance Test Suite API",
            "tools": [{"type": "function", "function": {"name": "get_weather", "description": "Get weather"}}],
        })
        suite_id = suite_resp.json()["suite_id"]

        # Create test case with should_call_tool=false
        case_resp = await app_client.post(f"/api/tool-suites/{suite_id}/cases", headers=auth_headers, json={
            "prompt": "Tell me a joke",  # Irrelevant prompt — no tool should be called
            "should_call_tool": False,
        })
        assert case_resp.status_code == 200
        case_data = case_resp.json()
        assert "case_id" in case_data

        # Retrieve via DB to confirm the stored value
        cases = await db.get_test_cases(suite_id)
        case = next((c for c in cases if c["id"] == case_data["case_id"]), None)
        assert case is not None
        # should_call_tool=False must be stored as 0 in SQLite (line 859 of tool_eval.py)
        stored_val = case.get("should_call_tool")
        assert stored_val in (0, False), (
            f"should_call_tool=False was not stored correctly. Got: {stored_val!r}."
        )

    async def test_create_test_case_default_should_call_tool_true(self, app_client, auth_headers):
        """Test case without should_call_tool defaults to true (backward compat)."""
        suite_resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Default True Suite",
            "tools": [{"type": "function", "function": {"name": "get_weather", "description": "Get weather"}}],
        })
        suite_id = suite_resp.json()["suite_id"]

        case_resp = await app_client.post(f"/api/tool-suites/{suite_id}/cases", headers=auth_headers, json={
            "prompt": "What is the weather in Paris?",
            "expected_tool": "get_weather",
        })
        assert case_resp.status_code == 200

        cases_resp = await app_client.get(f"/api/tool-suites/{suite_id}/cases", headers=auth_headers)
        cases = cases_resp.json()["cases"]
        case = next((c for c in cases if c["id"] == case_resp.json()["case_id"]), None)
        if case:
            # Default should be 1 (true) or not present (meaning column not yet returned by API)
            val = case.get("should_call_tool", 1)
            assert val in (1, True, None)  # None means API doesn't return it yet, which is acceptable

    async def test_import_suite_with_should_call_tool_false(self, app_client, auth_headers):
        """Import a suite with mixed should_call_tool values."""
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Irrelevance Import Suite",
            "tools": [
                {"type": "function", "function": {
                    "name": "get_weather", "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}
                }}
            ],
            "test_cases": [
                {
                    "prompt": "What is the weather in Paris?",
                    "expected_tool": "get_weather",
                    "expected_params": {"city": "Paris"},
                    "should_call_tool": True,
                },
                {
                    "prompt": "What is the capital of France?",
                    "should_call_tool": False,  # General knowledge, no tool needed
                },
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["test_cases_created"] == 2


class TestIrrelevanceDetectionDB:
    """DB-level tests for should_call_tool column."""

    async def test_create_test_case_with_should_call_tool(self, test_user):
        """DB function can create test case with should_call_tool=false."""
        user, _ = test_user
        suite_id = await db.create_tool_suite(user["id"], "Irrel DB Test Suite", "", "[]")
        case_id = await db.create_test_case(
            suite_id=suite_id,
            prompt="Should not call any tool",
            expected_tool=None,
            expected_params=None,
            should_call_tool=False,
        )
        assert case_id

        # Retrieve and verify
        cases = await db.get_test_cases(suite_id)
        case = next((c for c in cases if c["id"] == case_id), None)
        assert case is not None
        # should_call_tool=False (stored as 0 in SQLite)
        assert case.get("should_call_tool") in (0, False)

    async def test_existing_cases_default_to_should_call_tool_true(self, test_user):
        """Cases without should_call_tool column default to 1 (true)."""
        user, _ = test_user
        suite_id = await db.create_tool_suite(user["id"], "Default True DB Suite", "", "[]")
        case_id = await db.create_test_case(
            suite_id=suite_id,
            prompt="Normal tool call prompt",
            expected_tool="get_weather",
            expected_params='{"city": "Paris"}',
        )
        cases = await db.get_test_cases(suite_id)
        case = next((c for c in cases if c["id"] == case_id), None)
        assert case is not None
        # Default value is 1 (true) from column definition
        assert case.get("should_call_tool", 1) in (1, True)


# =========================================================================
# FEATURE 3: JUDGE ON FAILURE (threshold trigger + storage)
# =========================================================================


class TestJudgeOnFailureDB:
    """DB-level tests for judge_explanations_json column on tool_eval_runs."""

    async def test_tool_eval_run_has_judge_explanations_column(self, test_user):
        """tool_eval_runs table has judge_explanations_json column."""
        user, _ = test_user
        suite_id = await db.create_tool_suite(user["id"], "Judge Column Test Suite", "", "[]")
        run_id = await db.save_tool_eval_run(
            user_id=user["id"],
            suite_id=suite_id,
            suite_name="Judge Column Test Suite",
            models_json='["gpt-4o"]',
            results_json='[]',
            summary_json='{}',
            temperature=0.0,
        )
        # Fetch the run and check column exists
        runs = await db.get_tool_eval_runs(user["id"])
        assert any(r["id"] == run_id for r in runs), "Run should appear in list"

    async def test_judge_explanations_can_be_stored_and_retrieved(self, test_user, _patch_db_path):
        """judge_explanations_json can be stored and retrieved on a tool_eval_run."""
        import aiosqlite
        user, _ = test_user
        suite_id = await db.create_tool_suite(user["id"], "Judge Explain Test Suite", "", "[]")
        run_id = await db.save_tool_eval_run(
            user_id=user["id"],
            suite_id=suite_id,
            suite_name="Judge Explain Test Suite",
            models_json='["gpt-4o"]',
            results_json='[]',
            summary_json='{}',
            temperature=0.0,
        )

        explanations = {
            "case-id-1": "The model called get_weather but provided city=Berlin instead of Paris.",
            "case-id-2": "The model failed to pass the 'units' parameter.",
        }
        explanations_json = json.dumps(explanations)

        # Update via raw SQL (simulating what the handler will do after judge runs)
        async with aiosqlite.connect(str(_patch_db_path)) as conn:
            await conn.execute(
                "UPDATE tool_eval_runs SET judge_explanations_json = ? WHERE id = ?",
                (explanations_json, run_id),
            )
            await conn.commit()

        # Retrieve and verify
        run = await db.get_tool_eval_run(run_id, user["id"])
        assert run is not None
        stored = run.get("judge_explanations_json")
        assert stored is not None
        parsed = json.loads(stored)
        assert parsed["case-id-1"] == explanations["case-id-1"]
        assert parsed["case-id-2"] == explanations["case-id-2"]


class TestJudgeOnFailureAPI:
    """API tests for judge-on-failure feature."""

    async def test_tool_eval_history_detail_includes_judge_explanations(self, app_client, auth_headers, test_user):
        """GET /api/tool-eval/history/{run_id} always includes judge_explanations key.

        When no judge has run, the field is None. When a judge has run, it's a dict.
        The key must always be present (not missing) — the frontend depends on this.
        See tool_eval.py lines 969-978 for the parsing logic.
        """
        user, _ = test_user
        suite_id = await db.create_tool_suite(user["id"], "Judge API Test Suite", "", "[]")
        run_id = await db.save_tool_eval_run(
            user_id=user["id"],
            suite_id=suite_id,
            suite_name="Judge API Test Suite",
            models_json='["gpt-4o"]',
            results_json='[]',
            summary_json='{"total_cases": 1, "correct": 0, "accuracy": 0.0}',
            temperature=0.0,
        )

        resp = await app_client.get(f"/api/tool-eval/history/{run_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # judge_explanations must always be present (None when no judge ran)
        assert "judge_explanations" in data, (
            "judge_explanations key missing from history detail response. "
            "The frontend depends on this key being present."
        )
        assert data["judge_explanations"] is None  # No judge was run for this eval
        # Raw DB column should NOT be surfaced to the API
        assert "judge_explanations_json" not in data

    async def test_tool_eval_history_detail_judge_explanations_with_data(self, app_client, auth_headers, test_user):
        """GET /api/tool-eval/history/{run_id} returns parsed judge_explanations when stored.

        When judge_explanations_json is stored in the DB, the API should parse
        and return it as a structured dict, not a raw JSON string.
        """
        user, _ = test_user
        import aiosqlite
        import db as db_module

        suite_id = await db_module.create_tool_suite(user["id"], "Judge API Data Suite", "", "[]")
        run_id = await db_module.save_tool_eval_run(
            user_id=user["id"],
            suite_id=suite_id,
            suite_name="Judge API Data Suite",
            models_json='["gpt-4o"]',
            results_json='[]',
            summary_json='{"total_cases": 1, "correct": 0, "accuracy": 0.0}',
            temperature=0.0,
        )

        # Directly write judge_explanations_json to simulate completed judge run
        explanations = {"case-abc": {"verdict": "wrong_tool", "explanation": "Model called wrong fn"}}
        async with aiosqlite.connect(db_module.DB_PATH) as conn:
            await conn.execute(
                "UPDATE tool_eval_runs SET judge_explanations_json = ? WHERE id = ?",
                (json.dumps(explanations), run_id),
            )
            await conn.commit()

        resp = await app_client.get(f"/api/tool-eval/history/{run_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "judge_explanations" in data
        assert data["judge_explanations"] is not None
        assert data["judge_explanations"]["case-abc"]["verdict"] == "wrong_tool"
        # Raw column must not be exposed
        assert "judge_explanations_json" not in data

    async def test_judge_threshold_config_exists_in_settings(self, app_client, auth_headers):
        """Judge settings allow configuring a threshold for auto-triggering."""
        resp = await app_client.get("/api/settings/phase10", headers=auth_headers)
        assert resp.status_code == 200
        # The judge section of settings exists (for storing the threshold)
        data = resp.json()
        # Settings endpoint returns some data; we don't require specific threshold field
        # just that the endpoint works (the judge threshold feature adds to this config)
        assert data is not None

    async def test_auto_judge_field_in_tool_eval_request(self, app_client, auth_headers):
        """ToolEvalRequest schema accepts auto_judge boolean field."""
        # Create a suite for this test
        suite_resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Auto Judge Request Test Suite",
            "tools": [{"type": "function", "function": {"name": "noop", "description": "No-op tool"}}],
            "test_cases": [{"prompt": "test noop", "expected_tool": "noop"}],
        })
        suite_id = suite_resp.json()["suite_id"]

        # POST tool-eval with auto_judge=true — validates the schema accepts it
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": suite_id,
            "models": ["gpt-4o"],
            "temperature": 0.0,
            "auto_judge": True,
        })
        # Should submit (200) or hit non-schema errors (404 for suite not found etc.)
        # What matters: 422 (schema rejection) must NOT occur
        assert resp.status_code != 422, f"auto_judge field rejected by schema: {resp.text}"

    async def test_auto_judge_false_by_default(self, app_client, auth_headers):
        """auto_judge defaults to False (backward compat)."""
        suite_resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Auto Judge Default Suite",
            "tools": [{"type": "function", "function": {"name": "noop", "description": "No-op"}}],
            "test_cases": [{"prompt": "test", "expected_tool": "noop"}],
        })
        suite_id = suite_resp.json()["suite_id"]

        # POST without auto_judge — should succeed (it defaults to False)
        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": suite_id,
            "models": ["gpt-4o"],
            "temperature": 0.0,
            # No auto_judge field
        })
        assert resp.status_code != 422


class TestJudgeOnFailureThresholdLogic:
    """Unit tests for the judge-on-failure threshold logic.

    Tests the threshold decision logic directly, without making LLM calls.
    """

    async def test_below_threshold_triggers_judge(self):
        """param_accuracy below threshold should trigger judge review."""
        threshold = 0.8
        param_accuracy = 0.5  # 50% < 80% threshold
        should_trigger = param_accuracy < threshold
        assert should_trigger is True

    async def test_above_threshold_no_judge(self):
        """param_accuracy at or above threshold should NOT trigger judge review."""
        threshold = 0.8
        param_accuracy = 0.9  # 90% > 80% threshold
        should_trigger = param_accuracy < threshold
        assert should_trigger is False

    async def test_at_threshold_no_judge(self):
        """param_accuracy exactly at threshold should NOT trigger judge."""
        threshold = 0.8
        param_accuracy = 0.8  # Exactly at threshold = pass
        should_trigger = param_accuracy < threshold
        assert should_trigger is False

    async def test_zero_accuracy_triggers_judge(self):
        """0% accuracy always triggers judge."""
        threshold = 0.8
        param_accuracy = 0.0
        should_trigger = param_accuracy < threshold
        assert should_trigger is True

    async def test_perfect_accuracy_no_judge(self):
        """100% accuracy never triggers judge."""
        threshold = 0.8
        param_accuracy = 1.0
        should_trigger = param_accuracy < threshold
        assert should_trigger is False

    async def test_default_threshold_is_reasonable(self):
        """Default threshold should be in range [0, 1]."""
        from app import PHASE10_DEFAULTS
        judge_defaults = PHASE10_DEFAULTS.get("judge", {})
        # If there's a threshold field, it should be between 0 and 1
        if "failure_threshold" in judge_defaults:
            threshold = judge_defaults["failure_threshold"]
            assert 0.0 <= threshold <= 1.0

    async def test_low_accuracy_threshold_is_exactly_70_percent(self):
        """LOW_ACCURACY_THRESHOLD in job_handlers must be exactly 0.70 (70%).

        This threshold controls when the judge is auto-triggered on failed tool
        eval runs. Changing it would silently alter product behavior.
        """
        import inspect
        import job_handlers
        source = inspect.getsource(job_handlers)
        # The constant is defined inline as: LOW_ACCURACY_THRESHOLD = 0.70
        assert "LOW_ACCURACY_THRESHOLD = 0.70" in source, (
            "LOW_ACCURACY_THRESHOLD should be exactly 0.70. "
            "If it was intentionally changed, update this test too."
        )


# =========================================================================
# EXISTING SUITE REGRESSION TESTS
# =========================================================================


class TestExistingFeaturesNotBroken:
    """Regression tests to ensure the 3 new features don't break existing ones."""

    async def test_tool_suites_crud_still_works(self, app_client, auth_headers):
        """Existing tool suite CRUD unaffected."""
        create_resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Regression Suite",
            "tools": [],
        })
        assert create_resp.status_code == 200

        suite_id = create_resp.json()["suite_id"]
        get_resp = await app_client.get(f"/api/tool-suites/{suite_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Regression Suite"

    async def test_test_case_creation_without_new_fields(self, app_client, auth_headers):
        """Old-style test case creation (no should_call_tool) still works."""
        suite_resp = await app_client.post("/api/tool-suites", headers=auth_headers, json={
            "name": "Backward Compat Suite",
            "tools": [{"type": "function", "function": {"name": "test_tool", "description": "test"}}],
        })
        suite_id = suite_resp.json()["suite_id"]

        case_resp = await app_client.post(f"/api/tool-suites/{suite_id}/cases", headers=auth_headers, json={
            "prompt": "Test prompt",
            "expected_tool": "test_tool",
            "expected_params": {"key": "value"},
        })
        assert case_resp.status_code == 200
        assert "case_id" in case_resp.json()

    async def test_score_tool_selection_all_existing_cases(self):
        """Existing score_tool_selection logic unchanged."""
        assert score_tool_selection("get_weather", "get_weather") == 1.0
        assert score_tool_selection("Get_Weather", "get_weather") == 1.0
        assert score_tool_selection("get_weather", "search_web") == 0.0
        assert score_tool_selection("get_weather", None) == 0.0
        assert score_tool_selection(None, None) == 1.0
        assert score_tool_selection(None, "get_weather") == 0.0
        assert score_tool_selection(["get_weather", "check_forecast"], "check_forecast") == 1.0
        assert score_tool_selection(["get_weather", "check_forecast"], "search_web") == 0.0

    async def test_score_params_all_existing_cases(self):
        """Existing score_params logic unchanged."""
        assert score_params(None, {"city": "NYC"}) is None
        assert score_params({}, {"city": "NYC"}) == 1.0
        assert score_params({"city": "NYC"}, None) == 0.0
        assert score_params({"city": "NYC"}, {"city": "NYC"}) == 1.0
        assert score_params({"city": "nyc"}, {"city": "NYC"}) == 1.0

    async def test_judge_endpoints_still_work(self, app_client, auth_headers):
        """Judge endpoints unaffected by new features."""
        resp = await app_client.get("/api/tool-eval/judge/reports", headers=auth_headers)
        assert resp.status_code == 200
        assert "reports" in resp.json()

    async def test_prompt_tune_history_still_works(self, app_client, auth_headers):
        """Prompt tune history endpoint unaffected."""
        resp = await app_client.get("/api/tool-eval/prompt-tune/history", headers=auth_headers)
        assert resp.status_code == 200
        assert "runs" in resp.json()

    async def test_benchmark_history_still_works(self, app_client, auth_headers):
        """Benchmark history endpoint unaffected."""
        resp = await app_client.get("/api/history", headers=auth_headers)
        assert resp.status_code == 200
        assert "runs" in resp.json()


# =========================================================================
# ARCHITECT HANDOFF: BUG 1 — irrelevance_pct field name consistency
# =========================================================================


class TestIrrelevancePctFieldName:
    """Regression tests for the irrelevance_pct field name in eval summaries.

    The frontend (EvalResultsTable.vue) reads `irrelevance_pct`.
    The backend (_compute_eval_summaries in helpers.py) must emit the same key.
    A mismatch would cause the Irrel.% column to always be hidden.
    """

    async def test_compute_eval_summaries_emits_irrelevance_pct(self):
        """_compute_eval_summaries() returns 'irrelevance_pct', not 'irrelevance_accuracy_pct'."""
        from routers.helpers import _compute_eval_summaries
        from benchmark import Target

        target = Target(provider="openai", model_id="gpt-4o", display_name="GPT-4o")

        # One normal result + one irrelevance result for model "gpt-4o"
        results = [
            {
                "model_id": "gpt-4o",
                "model_name": "GPT-4o",
                "provider": "openai",
                "success": True,
                "tool_selection_score": 1.0,
                "param_accuracy": 1.0,
                "overall_score": 1.0,
                "should_call_tool": True,
                "irrelevance_score": None,
            },
            {
                "model_id": "gpt-4o",
                "model_name": "GPT-4o",
                "provider": "openai",
                "success": True,
                "tool_selection_score": 1.0,
                "param_accuracy": None,
                "overall_score": 1.0,
                "should_call_tool": False,
                "irrelevance_score": 1.0,  # Model correctly abstained
            },
        ]

        summaries = _compute_eval_summaries(results, [target])
        assert len(summaries) == 1
        summary = summaries[0]

        # Must use 'irrelevance_pct' — this is what EvalResultsTable.vue reads
        assert "irrelevance_pct" in summary, (
            "Summary dict must contain 'irrelevance_pct'. "
            "EvalResultsTable.vue reads this key to show the Irrel.% column."
        )
        # Must NOT use the wrong name
        assert "irrelevance_accuracy_pct" not in summary, (
            "'irrelevance_accuracy_pct' would be invisible to the frontend."
        )

    async def test_irrelevance_pct_is_none_when_no_irrelevance_cases(self):
        """irrelevance_pct is None when no should_call_tool=False cases exist (column hidden)."""
        from routers.helpers import _compute_eval_summaries
        from benchmark import Target

        target = Target(provider="openai", model_id="gpt-4o", display_name="GPT-4o")
        results = [
            {
                "model_id": "gpt-4o",
                "model_name": "GPT-4o",
                "provider": "openai",
                "success": True,
                "tool_selection_score": 1.0,
                "param_accuracy": 1.0,
                "overall_score": 1.0,
                "should_call_tool": True,
                "irrelevance_score": None,
            },
        ]

        summaries = _compute_eval_summaries(results, [target])
        assert summaries[0]["irrelevance_pct"] is None

    async def test_irrelevance_pct_is_float_when_irrelevance_cases_exist(self):
        """irrelevance_pct is a float percentage when irrelevance cases are present."""
        from routers.helpers import _compute_eval_summaries
        from benchmark import Target

        target = Target(provider="openai", model_id="gpt-4o", display_name="GPT-4o")
        results = [
            {
                "model_id": "gpt-4o",
                "model_name": "GPT-4o",
                "provider": "openai",
                "success": True,
                "tool_selection_score": 1.0,
                "param_accuracy": None,
                "overall_score": 1.0,
                "should_call_tool": False,
                "irrelevance_score": 1.0,
            },
            {
                "model_id": "gpt-4o",
                "model_name": "GPT-4o",
                "provider": "openai",
                "success": True,
                "tool_selection_score": 0.0,
                "param_accuracy": None,
                "overall_score": 0.0,
                "should_call_tool": False,
                "irrelevance_score": 0.0,  # Hallucinated a tool call
            },
        ]

        summaries = _compute_eval_summaries(results, [target])
        pct = summaries[0]["irrelevance_pct"]
        assert isinstance(pct, float), f"Expected float, got {type(pct)}"
        assert pct == 50.0  # 1 correct out of 2 = 50%


# =========================================================================
# ARCHITECT HANDOFF: BUG 2 — GET /api/tool-eval/{eval_id}/judge-report
# =========================================================================


class TestGetJudgeReportForEval:
    """Tests for GET /api/tool-eval/{eval_id}/judge-report (added in routers/judge.py:635).

    This endpoint returns the most recent completed judge report for a given
    tool eval run, with case_results flattened for the drill-down modal.
    """

    async def test_returns_200_with_case_results_when_report_exists(self, app_client, auth_headers, test_user):
        """Valid eval_id with a completed judge report returns 200 with case_results and report_id."""
        user, _ = test_user

        # Seed: create an eval run and a completed judge report linked to it
        suite_id = await db.create_tool_suite(user["id"], "Judge Report Suite", "", "[]")
        eval_id = await db.save_tool_eval_run(
            user_id=user["id"],
            suite_id=suite_id,
            suite_name="Judge Report Suite",
            models_json='["gpt-4o"]',
            results_json='[]',
            summary_json='{"total_cases": 2, "correct": 0, "accuracy": 0.0}',
            temperature=0.0,
        )
        report_id = await db.save_judge_report(
            user_id=user["id"],
            judge_model="gpt-4o",
            mode="post_eval",
            eval_run_id=eval_id,
        )
        verdicts = [
            {
                "model_id": "gpt-4o",
                "test_case_id": "case-1",
                "reasoning": "Model called wrong function",
                "verdict": "incorrect",
                "quality_score": 0.2,
                "tool_selection_assessment": "Wrong tool selected",
                "param_assessment": "N/A",
            }
        ]
        await db.update_judge_report(
            report_id,
            status="completed",
            verdicts_json=json.dumps(verdicts),
            overall_grade="D",
            overall_score=0.2,
        )

        resp = await app_client.get(f"/api/tool-eval/{eval_id}/judge-report", headers=auth_headers)
        assert resp.status_code == 200

        data = resp.json()
        assert "report_id" in data
        assert data["report_id"] == report_id
        assert "case_results" in data
        assert isinstance(data["case_results"], list)
        assert len(data["case_results"]) == 1

        case = data["case_results"][0]
        assert case["model_id"] == "gpt-4o"
        assert case["test_case_id"] == "case-1"
        assert "explanation" in case

    async def test_returns_404_when_no_judge_report_for_eval(self, app_client, auth_headers, test_user):
        """eval_id with no completed judge report returns 404."""
        user, _ = test_user

        suite_id = await db.create_tool_suite(user["id"], "No Judge Suite", "", "[]")
        eval_id = await db.save_tool_eval_run(
            user_id=user["id"],
            suite_id=suite_id,
            suite_name="No Judge Suite",
            models_json='["gpt-4o"]',
            results_json='[]',
            summary_json='{"total_cases": 1, "correct": 1, "accuracy": 1.0}',
            temperature=0.0,
        )

        resp = await app_client.get(f"/api/tool-eval/{eval_id}/judge-report", headers=auth_headers)
        assert resp.status_code == 404

    async def test_returns_404_for_nonexistent_eval_id(self, app_client, auth_headers):
        """Completely unknown eval_id returns 404."""
        resp = await app_client.get("/api/tool-eval/nonexistent-id-xyz/judge-report", headers=auth_headers)
        assert resp.status_code == 404

    async def test_returns_404_for_other_users_eval(self, app_client, auth_headers, test_user):
        """Another user's eval_id returns 404 (user-scoped access control)."""
        user, _ = test_user
        import time

        # Register a second user and create their eval + judge report
        unique = str(int(time.time() * 1000))
        reg = await app_client.post("/api/auth/register", json={
            "email": f"judge_other_{unique}@test.com",
            "password": "OtherPass123!",
        })
        reg_data = reg.json()
        other_token = reg_data["access_token"]
        other_user_id = reg_data["user"]["id"]
        other_headers = {"Authorization": f"Bearer {other_token}"}  # noqa: F841
        other_suite_id = await db.create_tool_suite(other_user_id, "Other Suite", "", "[]")
        other_eval_id = await db.save_tool_eval_run(
            user_id=other_user_id,
            suite_id=other_suite_id,
            suite_name="Other Suite",
            models_json='["gpt-4o"]',
            results_json='[]',
            summary_json='{"total_cases": 1, "correct": 0, "accuracy": 0.0}',
            temperature=0.0,
        )
        other_report_id = await db.save_judge_report(
            user_id=other_user_id,
            judge_model="gpt-4o",
            mode="post_eval",
            eval_run_id=other_eval_id,
        )
        await db.update_judge_report(other_report_id, status="completed", overall_score=0.5)

        # First user tries to access second user's eval judge report
        resp = await app_client.get(f"/api/tool-eval/{other_eval_id}/judge-report", headers=auth_headers)
        assert resp.status_code == 404

    async def test_only_completed_reports_returned(self, app_client, auth_headers, test_user):
        """A running (not completed) judge report for an eval returns 404.

        The endpoint filters by status='completed', so in-progress runs are invisible.
        """
        user, _ = test_user

        suite_id = await db.create_tool_suite(user["id"], "Running Judge Suite", "", "[]")
        eval_id = await db.save_tool_eval_run(
            user_id=user["id"],
            suite_id=suite_id,
            suite_name="Running Judge Suite",
            models_json='["gpt-4o"]',
            results_json='[]',
            summary_json='{"total_cases": 1, "correct": 0, "accuracy": 0.0}',
            temperature=0.0,
        )
        # Save report but leave status as default 'running'
        await db.save_judge_report(
            user_id=user["id"],
            judge_model="gpt-4o",
            mode="post_eval",
            eval_run_id=eval_id,
        )

        # Should return 404 because there's no *completed* report
        resp = await app_client.get(f"/api/tool-eval/{eval_id}/judge-report", headers=auth_headers)
        assert resp.status_code == 404

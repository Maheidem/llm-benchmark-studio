"""Tests for Judge Overhaul features (C1-C4).

Covers:
- C1: Judge Versioning — parent_report_id, version, instructions_json columns,
      get_judge_report_versions(), GET /api/tool-eval/judge/reports/{id}/versions
- C2: Score Override — verdict dict has judge_override_score / override_reason keys,
      prompt template contains override instructions
- C3: Inline Judge + Tuner Analysis — ToolEvalRequest.auto_judge, JudgeRequest tune fields
- C4: Judge Settings — GET/PUT /api/settings/judge with defaults and partial update

Run: uv run pytest tests/test_judge_overhaul.py -v
"""

import json
import pytest
import pytest_asyncio

import db

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_tool_suite(app_client, headers: dict) -> str:
    """Create a minimal tool suite and return its suite_id."""
    resp = await app_client.post("/api/tool-eval/import", headers=headers, json={
        "name": "Judge Overhaul Test Suite",
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }],
        "test_cases": [{
            "prompt": "What is the weather in Paris?",
            "expected_tool": "get_weather",
            "expected_params": {"city": "Paris"},
        }],
    })
    assert resp.status_code == 200, f"Suite creation failed: {resp.text}"
    return resp.json()["suite_id"]


async def _save_eval_run(test_user, suite_id: str) -> str:
    """Save a minimal tool eval run directly via db, return run_id."""
    user, _ = test_user
    results = json.dumps([{
        "test_case_id": "tc1",
        "prompt": "What is the weather in Paris?",
        "expected_tool": "get_weather",
        "expected_params": {"city": "Paris"},
        "actual_tool": "get_weather",
        "actual_params": {"city": "Paris"},
        "overall_score": 1.0,
    }])
    run_id = await db.save_tool_eval_run(
        user_id=user["id"],
        suite_id=suite_id,
        suite_name="Judge Overhaul Test Suite",
        models_json=json.dumps(["gpt-4o"]),
        results_json=results,
        summary_json=json.dumps({"total": 1, "passed": 1}),
        temperature=0.0,
    )
    return run_id


async def _save_judge_report(test_user, eval_run_id: str, **kwargs) -> str:
    """Save a judge report directly via db, return report_id."""
    user, _ = test_user
    return await db.save_judge_report(
        user_id=user["id"],
        judge_model=kwargs.get("judge_model", "gpt-4o"),
        mode=kwargs.get("mode", "post_eval"),
        eval_run_id=eval_run_id,
        parent_report_id=kwargs.get("parent_report_id"),
        version=kwargs.get("version", 1),
        instructions_json=kwargs.get("instructions_json"),
    )


# ---------------------------------------------------------------------------
# C1: Judge Versioning
# ---------------------------------------------------------------------------


class TestJudgeVersioning:
    """Judge reports now support parent_report_id, version, and instructions_json columns."""

    async def test_save_report_with_default_version(self, app_client, auth_headers, test_user, _patch_db_path):
        """A freshly saved report has version=1 and parent_report_id=None."""
        suite_id = await _create_tool_suite(app_client, auth_headers)
        eval_run_id = await _save_eval_run(test_user, suite_id)
        report_id = await _save_judge_report(test_user, eval_run_id)

        user, _ = test_user
        report = await db.get_judge_report(report_id, user["id"])
        assert report is not None
        assert report["version"] == 1
        assert report["parent_report_id"] is None

    async def test_save_report_with_parent_and_version(self, app_client, auth_headers, test_user, _patch_db_path):
        """A child report saves parent_report_id and incremented version correctly."""
        suite_id = await _create_tool_suite(app_client, auth_headers)
        eval_run_id = await _save_eval_run(test_user, suite_id)

        root_id = await _save_judge_report(test_user, eval_run_id, version=1)
        child_id = await _save_judge_report(
            test_user, eval_run_id,
            parent_report_id=root_id,
            version=2,
        )

        user, _ = test_user
        child = await db.get_judge_report(child_id, user["id"])
        assert child is not None
        assert child["parent_report_id"] == root_id
        assert child["version"] == 2

    async def test_save_report_with_instructions_json(self, app_client, auth_headers, test_user, _patch_db_path):
        """instructions_json column is persisted and retrievable."""
        suite_id = await _create_tool_suite(app_client, auth_headers)
        eval_run_id = await _save_eval_run(test_user, suite_id)

        instructions = json.dumps({
            "custom_instructions": "Be strict",
            "judge_model": "gpt-4o",
            "concurrency": 4,
        })
        report_id = await _save_judge_report(
            test_user, eval_run_id,
            version=1,
            instructions_json=instructions,
        )

        user, _ = test_user
        report = await db.get_judge_report(report_id, user["id"])
        assert report["instructions_json"] == instructions

    async def test_get_versions_for_root_report(self, app_client, auth_headers, test_user, _patch_db_path):
        """get_judge_report_versions returns [root] when no children exist."""
        suite_id = await _create_tool_suite(app_client, auth_headers)
        eval_run_id = await _save_eval_run(test_user, suite_id)

        root_id = await _save_judge_report(test_user, eval_run_id, version=1)

        user, _ = test_user
        versions = await db.get_judge_report_versions(root_id, user["id"])
        assert len(versions) >= 1
        ids = [v["id"] for v in versions]
        assert root_id in ids

    async def test_get_versions_returns_root_and_children(self, app_client, auth_headers, test_user, _patch_db_path):
        """get_judge_report_versions returns root + all children ordered by version ASC."""
        suite_id = await _create_tool_suite(app_client, auth_headers)
        eval_run_id = await _save_eval_run(test_user, suite_id)

        root_id = await _save_judge_report(test_user, eval_run_id, version=1)
        child2_id = await _save_judge_report(
            test_user, eval_run_id, parent_report_id=root_id, version=2
        )
        child3_id = await _save_judge_report(
            test_user, eval_run_id, parent_report_id=root_id, version=3
        )

        user, _ = test_user
        versions = await db.get_judge_report_versions(root_id, user["id"])
        ids = [v["id"] for v in versions]
        assert root_id in ids
        assert child2_id in ids
        assert child3_id in ids
        # Versions must be ordered ASC by version number
        version_nums = [v["version"] for v in versions if v["id"] in {root_id, child2_id, child3_id}]
        assert version_nums == sorted(version_nums)

    async def test_get_versions_from_child_finds_root(self, app_client, auth_headers, test_user, _patch_db_path):
        """Calling get_judge_report_versions from a child report finds the whole chain."""
        suite_id = await _create_tool_suite(app_client, auth_headers)
        eval_run_id = await _save_eval_run(test_user, suite_id)

        root_id = await _save_judge_report(test_user, eval_run_id, version=1)
        child_id = await _save_judge_report(
            test_user, eval_run_id, parent_report_id=root_id, version=2
        )

        user, _ = test_user
        # Query from the child — should still return both root and child
        versions = await db.get_judge_report_versions(child_id, user["id"])
        ids = [v["id"] for v in versions]
        assert root_id in ids
        assert child_id in ids

    async def test_get_versions_nonexistent_returns_empty(self, test_user, _patch_db_path):
        """get_judge_report_versions returns [] for a nonexistent report_id."""
        user, _ = test_user
        versions = await db.get_judge_report_versions("nonexistent-id", user["id"])
        assert versions == []

    async def test_versions_endpoint_returns_chain(self, app_client, auth_headers, test_user, _patch_db_path):
        """GET /api/tool-eval/judge/reports/{id}/versions returns the version chain."""
        suite_id = await _create_tool_suite(app_client, auth_headers)
        eval_run_id = await _save_eval_run(test_user, suite_id)

        root_id = await _save_judge_report(test_user, eval_run_id, version=1)
        child_id = await _save_judge_report(
            test_user, eval_run_id, parent_report_id=root_id, version=2
        )

        resp = await app_client.get(
            f"/api/tool-eval/judge/reports/{root_id}/versions",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "versions" in data
        ids = [v["id"] for v in data["versions"]]
        assert root_id in ids
        assert child_id in ids

    async def test_versions_endpoint_nonexistent_returns_404(self, app_client, auth_headers):
        """GET /api/tool-eval/judge/reports/{id}/versions returns 404 for unknown id."""
        resp = await app_client.get(
            "/api/tool-eval/judge/reports/nonexistent-report-id/versions",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_versions_endpoint_requires_auth(self, app_client, test_user, _patch_db_path):
        """GET /api/tool-eval/judge/reports/{id}/versions returns 401 without auth."""
        resp = await app_client.get(
            "/api/tool-eval/judge/reports/any-id/versions"
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# C2: Score Override
# ---------------------------------------------------------------------------


class TestScoreOverride:
    """Verdict dict includes judge_override_score and override_reason keys."""

    async def test_verdict_error_fallback_has_override_keys(self):
        """The error fallback dict in _judge_single_verdict includes override keys."""
        # Import the private function to inspect the fallback structure
        from routers.judge import _judge_single_verdict
        from unittest.mock import AsyncMock, patch
        from benchmark import Target

        fake_target = Target(
            provider="openai",
            model_id="gpt-4o",
            display_name="GPT-4o",
        )

        with patch("routers.judge._call_judge_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {}  # Empty → triggers setdefault path
            verdict = await _judge_single_verdict(
                judge_target=fake_target,
                tool_defs_text="{}",
                test_case={"prompt": "test"},
                result={
                    "prompt": "test",
                    "expected_tool": "noop",
                    "expected_params": {},
                    "actual_tool": "noop",
                    "actual_params": {},
                    "overall_score": 1.0,
                },
            )

        assert "judge_override_score" in verdict
        assert "override_reason" in verdict

    async def test_verdict_prompt_contains_override_instructions(self):
        """The judge verdict prompt template mentions judge_override_score."""
        from routers.judge import _JUDGE_VERDICT_PROMPT
        assert "judge_override_score" in _JUDGE_VERDICT_PROMPT
        assert "override_reason" in _JUDGE_VERDICT_PROMPT

    async def test_verdict_setdefault_sets_override_keys_to_none(self):
        """When judge returns a partial dict, setdefault fills override keys as None."""
        from routers.judge import _judge_single_verdict
        from unittest.mock import AsyncMock, patch
        from benchmark import Target

        fake_target = Target(
            provider="openai",
            model_id="gpt-4o",
            display_name="GPT-4o",
        )

        partial_response = {
            "quality_score": 4,
            "verdict": "pass",
            "summary": "Correct",
            "reasoning": "Good",
            "tool_selection_assessment": "correct",
            "param_assessment": "exact",
            # override keys deliberately missing — should be filled by setdefault
        }

        with patch("routers.judge._call_judge_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = partial_response
            verdict = await _judge_single_verdict(
                judge_target=fake_target,
                tool_defs_text="{}",
                test_case={"prompt": "test"},
                result={
                    "prompt": "test",
                    "expected_tool": "noop",
                    "expected_params": {},
                    "actual_tool": "noop",
                    "actual_params": {},
                    "overall_score": 1.0,
                },
            )

        assert verdict["judge_override_score"] is None
        assert verdict["override_reason"] is None


# ---------------------------------------------------------------------------
# C3: Inline Judge + Tuner Analysis
# ---------------------------------------------------------------------------


class TestAutoJudgeSchema:
    """ToolEvalRequest.auto_judge field validation."""

    async def test_tool_eval_request_accepts_auto_judge_false(self):
        """ToolEvalRequest with auto_judge=False validates successfully."""
        from schemas import ToolEvalRequest
        req = ToolEvalRequest(
            suite_id="suite-1",
            models=["gpt-4o"],
            auto_judge=False,
        )
        assert req.auto_judge is False

    async def test_tool_eval_request_accepts_auto_judge_true(self):
        """ToolEvalRequest with auto_judge=True validates successfully."""
        from schemas import ToolEvalRequest
        req = ToolEvalRequest(
            suite_id="suite-1",
            models=["gpt-4o"],
            auto_judge=True,
        )
        assert req.auto_judge is True

    async def test_tool_eval_request_defaults_auto_judge_to_false(self):
        """ToolEvalRequest auto_judge defaults to False when omitted."""
        from schemas import ToolEvalRequest
        req = ToolEvalRequest(
            suite_id="suite-1",
            models=["gpt-4o"],
        )
        assert req.auto_judge is False


class TestJudgeTunerFields:
    """JudgeRequest tune_run_id and tune_type field validation."""

    async def test_judge_request_accepts_tune_run_id_and_tune_type(self):
        """JudgeRequest with both tune_run_id and tune_type validates."""
        from schemas import JudgeRequest
        req = JudgeRequest(
            eval_run_id="run-1",
            judge_model="gpt-4o",
            tune_run_id="tune-run-abc",
            tune_type="param_tuner",
        )
        assert req.tune_run_id == "tune-run-abc"
        assert req.tune_type == "param_tuner"

    async def test_judge_request_accepts_prompt_tuner_type(self):
        """JudgeRequest accepts tune_type='prompt_tuner'."""
        from schemas import JudgeRequest
        req = JudgeRequest(
            eval_run_id="run-1",
            judge_model="gpt-4o",
            tune_run_id="prompt-run-xyz",
            tune_type="prompt_tuner",
        )
        assert req.tune_type == "prompt_tuner"

    async def test_judge_request_tune_type_without_tune_run_id_raises(self):
        """JudgeRequest raises ValueError when tune_type provided without tune_run_id."""
        from schemas import JudgeRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            JudgeRequest(
                eval_run_id="run-1",
                judge_model="gpt-4o",
                tune_type="param_tuner",
                # tune_run_id deliberately omitted
            )
        assert "tune_run_id" in str(exc_info.value)

    async def test_judge_request_tune_run_id_without_tune_type_is_valid(self):
        """JudgeRequest with tune_run_id but no tune_type is valid (tune_type is optional)."""
        from schemas import JudgeRequest
        req = JudgeRequest(
            eval_run_id="run-1",
            judge_model="gpt-4o",
            tune_run_id="some-run-id",
            # tune_type omitted intentionally
        )
        assert req.tune_run_id == "some-run-id"
        assert req.tune_type is None

    async def test_judge_request_tune_fields_default_to_none(self):
        """JudgeRequest tune_run_id and tune_type default to None when omitted."""
        from schemas import JudgeRequest
        req = JudgeRequest(
            eval_run_id="run-1",
            judge_model="gpt-4o",
        )
        assert req.tune_run_id is None
        assert req.tune_type is None

    async def test_judge_request_invalid_tune_type_rejected(self):
        """JudgeRequest rejects invalid tune_type values."""
        from schemas import JudgeRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            JudgeRequest(
                eval_run_id="run-1",
                judge_model="gpt-4o",
                tune_run_id="some-run",
                tune_type="invalid_tuner_type",
            )


# ---------------------------------------------------------------------------
# C4: Judge Settings
# ---------------------------------------------------------------------------


class TestJudgeSettings:
    """GET and PUT /api/settings/judge endpoint behaviour."""

    async def test_get_judge_settings_returns_defaults_when_none_saved(self, app_client, auth_headers):
        """GET /api/settings/judge returns full default settings object."""
        resp = await app_client.get("/api/settings/judge", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "default_judge_model" in data
        assert "default_mode" in data
        assert "score_override_policy" in data
        assert "auto_judge_after_eval" in data
        assert "concurrency" in data
        # Check defaults
        assert data["default_mode"] == "post_eval"
        assert data["score_override_policy"] == "always_allow"
        assert data["auto_judge_after_eval"] is False
        assert data["concurrency"] == 4

    async def test_get_judge_settings_requires_auth(self, app_client):
        """GET /api/settings/judge returns 401 without authorization."""
        resp = await app_client.get("/api/settings/judge")
        assert resp.status_code == 401

    async def test_put_judge_settings_saves_concurrency(self, app_client, auth_headers):
        """PUT /api/settings/judge saves concurrency field."""
        resp = await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "concurrency": 8,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify persisted
        get_resp = await app_client.get("/api/settings/judge", headers=auth_headers)
        assert get_resp.json()["concurrency"] == 8

    async def test_put_judge_settings_saves_default_model(self, app_client, auth_headers):
        """PUT /api/settings/judge saves default_judge_model."""
        resp = await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "default_judge_model": "gpt-4o",
            "default_judge_provider_key": "openai",
        })
        assert resp.status_code == 200

        get_resp = await app_client.get("/api/settings/judge", headers=auth_headers)
        data = get_resp.json()
        assert data["default_judge_model"] == "gpt-4o"
        assert data["default_judge_provider_key"] == "openai"

    async def test_put_judge_settings_partial_update_preserves_other_fields(self, app_client, auth_headers):
        """Partial PUT only updates specified fields, leaving others unchanged."""
        # First set multiple fields
        await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "concurrency": 6,
            "auto_judge_after_eval": True,
            "default_mode": "post_eval",
        })

        # Now update only concurrency
        resp = await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "concurrency": 10,
        })
        assert resp.status_code == 200

        # auto_judge_after_eval should be preserved
        get_resp = await app_client.get("/api/settings/judge", headers=auth_headers)
        data = get_resp.json()
        assert data["concurrency"] == 10
        assert data["auto_judge_after_eval"] is True

    async def test_put_judge_settings_saves_score_override_policy(self, app_client, auth_headers):
        """PUT /api/settings/judge saves score_override_policy."""
        resp = await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "score_override_policy": "require_confirmation",
        })
        assert resp.status_code == 200

        get_resp = await app_client.get("/api/settings/judge", headers=auth_headers)
        assert get_resp.json()["score_override_policy"] == "require_confirmation"

    async def test_put_judge_settings_invalid_score_override_policy_rejected(self, app_client, auth_headers):
        """PUT /api/settings/judge rejects invalid score_override_policy value."""
        resp = await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "score_override_policy": "invalid_policy",
        })
        assert resp.status_code == 422

    async def test_put_judge_settings_saves_all_fields(self, app_client, auth_headers):
        """PUT /api/settings/judge accepts a full settings object."""
        resp = await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "default_judge_model": "claude-opus-4",
            "default_judge_provider_key": "anthropic",
            "default_mode": "post_eval",
            "custom_instructions_template": "Be strict about parameter names.",
            "score_override_policy": "never",
            "auto_judge_after_eval": True,
            "concurrency": 2,
        })
        assert resp.status_code == 200

        get_resp = await app_client.get("/api/settings/judge", headers=auth_headers)
        data = get_resp.json()
        assert data["default_judge_model"] == "claude-opus-4"
        assert data["score_override_policy"] == "never"
        assert data["auto_judge_after_eval"] is True
        assert data["concurrency"] == 2

    async def test_put_judge_settings_requires_auth(self, app_client):
        """PUT /api/settings/judge returns 401 without authorization."""
        resp = await app_client.put("/api/settings/judge", json={"concurrency": 4})
        assert resp.status_code == 401

    async def test_put_judge_settings_invalid_concurrency_rejected(self, app_client, auth_headers):
        """PUT /api/settings/judge rejects concurrency outside 1-20 range."""
        resp = await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "concurrency": 0,
        })
        assert resp.status_code == 422

        resp = await app_client.put("/api/settings/judge", headers=auth_headers, json={
            "concurrency": 21,
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Rerun Endpoint
# ---------------------------------------------------------------------------


class TestJudgeRerunEndpoint:
    """POST /api/tool-eval/judge/rerun endpoint behaviour."""

    async def test_rerun_nonexistent_parent_returns_404(self, app_client, auth_headers):
        """POST /api/tool-eval/judge/rerun with unknown parent_report_id returns 404."""
        resp = await app_client.post(
            "/api/tool-eval/judge/rerun",
            headers=auth_headers,
            json={"parent_report_id": "nonexistent-parent-id"},
        )
        assert resp.status_code == 404

    async def test_rerun_missing_parent_report_id_returns_422(self, app_client, auth_headers):
        """POST /api/tool-eval/judge/rerun without parent_report_id returns 422."""
        resp = await app_client.post(
            "/api/tool-eval/judge/rerun",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 422

    async def test_rerun_requires_auth(self, app_client):
        """POST /api/tool-eval/judge/rerun returns 401 without auth."""
        resp = await app_client.post(
            "/api/tool-eval/judge/rerun",
            json={"parent_report_id": "some-id"},
        )
        assert resp.status_code == 401

"""Tests for Model Profiles feature.

Covers: CRUD via API endpoints, default management, uniqueness constraints,
per-model limit enforcement, from-tuner creation, profile fields in
eval/benchmark requests, and user isolation.

Run: uv run pytest tests/test_profiles.py -v
"""

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


# =========================================================================
# HELPERS
# =========================================================================


async def _register_and_login(app_client, email: str, password: str = "TestPass123!") -> dict:
    """Register (or login if already registered) and return auth headers."""
    resp = await app_client.post("/api/auth/register", json={
        "email": email,
        "password": password,
    })
    if resp.status_code == 409:
        resp = await app_client.post("/api/auth/login", json={
            "email": email,
            "password": password,
        })
    data = resp.json()
    token = data["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_profile(app_client, headers: dict, **kwargs) -> str:
    """Helper: create a profile and return its profile_id."""
    payload = {
        "model_id": "gpt-4o",
        "name": "Default Profile",
    }
    payload.update(kwargs)
    resp = await app_client.post("/api/profiles", headers=headers, json=payload)
    assert resp.status_code == 200, f"Profile creation failed: {resp.text}"
    return resp.json()["profile_id"]


# =========================================================================
# 1. BASIC CRUD
# =========================================================================


class TestProfileCrud:
    """Basic create, read, update, delete for profiles."""

    async def test_create_profile_minimal_returns_profile_id(self, app_client, auth_headers):
        resp = await app_client.post("/api/profiles", headers=auth_headers, json={
            "model_id": "gpt-4o",
            "name": "Minimal Profile",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "profile_id" in data
        assert len(data["profile_id"]) > 0

    async def test_create_profile_all_fields(self, app_client, auth_headers):
        resp = await app_client.post("/api/profiles", headers=auth_headers, json={
            "model_id": "gpt-4o",
            "name": "Full Profile",
            "description": "A complete profile with all fields",
            "params_json": {"temperature": 0.5, "top_p": 0.9},
            "system_prompt": "You are a helpful assistant.",
            "is_default": False,
            "origin_type": "manual",
            "origin_ref": None,
        })
        assert resp.status_code == 200
        assert "profile_id" in resp.json()

    async def test_create_profile_appears_in_list(self, app_client, auth_headers):
        # Create a distinctly named profile to verify it appears in the list
        name = "List Visibility Profile"
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-4o", name=name)

        resp = await app_client.get("/api/profiles", headers=auth_headers)
        assert resp.status_code == 200
        profiles = resp.json()["profiles"]
        ids = [p["id"] for p in profiles]
        assert profile_id in ids

    async def test_get_profile_by_id(self, app_client, auth_headers):
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-4o",
                                            name="Get By ID Profile")

        resp = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                    headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == profile_id
        assert data["name"] == "Get By ID Profile"
        assert data["model_id"] == "gpt-4o"

    async def test_get_profile_not_found_returns_404(self, app_client, auth_headers):
        resp = await app_client.get("/api/profiles/detail/nonexistent-profile-id",
                                    headers=auth_headers)
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_update_profile_name(self, app_client, auth_headers):
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-4o",
                                            name="Old Name")

        resp = await app_client.put(f"/api/profiles/{profile_id}",
                                    headers=auth_headers,
                                    json={"name": "New Name"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify the change
        detail = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                      headers=auth_headers)
        assert detail.json()["name"] == "New Name"

    async def test_update_profile_params(self, app_client, auth_headers):
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-4o",
                                            name="Params Update Profile")

        resp = await app_client.put(f"/api/profiles/{profile_id}",
                                    headers=auth_headers,
                                    json={"params_json": {"temperature": 0.3, "top_p": 0.8}})
        assert resp.status_code == 200

        # Verify persisted
        detail = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                      headers=auth_headers)
        assert detail.status_code == 200

    async def test_update_profile_system_prompt(self, app_client, auth_headers):
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-4o",
                                            name="System Prompt Profile")

        resp = await app_client.put(f"/api/profiles/{profile_id}",
                                    headers=auth_headers,
                                    json={"system_prompt": "You are a coding assistant."})
        assert resp.status_code == 200

    async def test_update_nonexistent_profile_returns_404(self, app_client, auth_headers):
        resp = await app_client.put("/api/profiles/nonexistent-id",
                                    headers=auth_headers,
                                    json={"name": "Ghost"})
        assert resp.status_code == 404

    async def test_update_profile_no_fields_returns_400(self, app_client, auth_headers):
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-4o",
                                            name="No Fields Profile")
        # Send empty update (no recognized fields)
        resp = await app_client.put(f"/api/profiles/{profile_id}",
                                    headers=auth_headers,
                                    json={})
        assert resp.status_code == 400

    async def test_delete_profile(self, app_client, auth_headers):
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-4o",
                                            name="Delete Me Profile")

        resp = await app_client.delete(f"/api/profiles/{profile_id}",
                                       headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Confirm gone
        detail = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                      headers=auth_headers)
        assert detail.status_code == 404

    async def test_delete_nonexistent_profile_returns_404(self, app_client, auth_headers):
        resp = await app_client.delete("/api/profiles/nonexistent-id",
                                       headers=auth_headers)
        assert resp.status_code == 404

    async def test_list_profiles_unauthenticated_returns_401(self, app_client):
        resp = await app_client.get("/api/profiles")
        assert resp.status_code == 401

    async def test_create_profile_unauthenticated_returns_401(self, app_client):
        resp = await app_client.post("/api/profiles", json={
            "model_id": "gpt-4o",
            "name": "No Auth Profile",
        })
        assert resp.status_code == 401


# =========================================================================
# 2. MODEL-SCOPED LISTING
# =========================================================================


class TestModelScopedListing:
    """GET /api/profiles/{model_id} filters by model."""

    async def test_list_profiles_for_specific_model(self, app_client, auth_headers):
        model_a = "gpt-list-test-a"
        model_b = "gpt-list-test-b"

        await _create_profile(app_client, auth_headers,
                               model_id=model_a, name="Model A Profile 1")
        await _create_profile(app_client, auth_headers,
                               model_id=model_b, name="Model B Profile 1")

        resp = await app_client.get(f"/api/profiles/{model_a}", headers=auth_headers)
        assert resp.status_code == 200
        profiles = resp.json()["profiles"]
        model_ids = {p["model_id"] for p in profiles}
        assert model_a in model_ids
        assert model_b not in model_ids

    async def test_list_all_profiles_returns_multiple_models(self, app_client, auth_headers):
        await _create_profile(app_client, auth_headers,
                               model_id="model-all-1", name="All List Profile 1")
        await _create_profile(app_client, auth_headers,
                               model_id="model-all-2", name="All List Profile 2")

        resp = await app_client.get("/api/profiles", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "profiles" in data
        assert isinstance(data["profiles"], list)


# =========================================================================
# 3. DEFAULT MANAGEMENT
# =========================================================================


class TestDefaultManagement:
    """is_default flag management and set-default endpoint."""

    async def test_create_profile_with_is_default_true(self, app_client, auth_headers):
        model_id = "gpt-default-create-test"
        resp = await app_client.post("/api/profiles", headers=auth_headers, json={
            "model_id": model_id,
            "name": "Default On Create",
            "is_default": True,
        })
        assert resp.status_code == 200
        profile_id = resp.json()["profile_id"]

        detail = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                      headers=auth_headers)
        assert detail.json()["is_default"] == 1

    async def test_setting_new_default_clears_old_default(self, app_client, auth_headers):
        model_id = "gpt-default-clear-test"

        # Create first as default
        first_id = await _create_profile(app_client, auth_headers,
                                          model_id=model_id,
                                          name="First Default",
                                          is_default=True)

        # Create second as default — should clear first
        second_id = await _create_profile(app_client, auth_headers,
                                           model_id=model_id,
                                           name="Second Default",
                                           is_default=True)

        first_detail = await app_client.get(f"/api/profiles/detail/{first_id}",
                                             headers=auth_headers)
        second_detail = await app_client.get(f"/api/profiles/detail/{second_id}",
                                              headers=auth_headers)

        assert first_detail.json()["is_default"] == 0, "Old default should be cleared"
        assert second_detail.json()["is_default"] == 1, "New default should be set"

    async def test_set_default_endpoint(self, app_client, auth_headers):
        model_id = "gpt-set-default-endpoint-test"

        first_id = await _create_profile(app_client, auth_headers,
                                          model_id=model_id,
                                          name="Non-Default A")
        second_id = await _create_profile(app_client, auth_headers,
                                           model_id=model_id,
                                           name="Non-Default B")

        # Use set-default endpoint on second
        resp = await app_client.post(f"/api/profiles/{second_id}/set-default",
                                     headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        second_detail = await app_client.get(f"/api/profiles/detail/{second_id}",
                                              headers=auth_headers)
        assert second_detail.json()["is_default"] == 1

    async def test_set_default_nonexistent_returns_404(self, app_client, auth_headers):
        resp = await app_client.post("/api/profiles/nonexistent-id/set-default",
                                     headers=auth_headers)
        assert resp.status_code == 404

    async def test_update_is_default_true_clears_old_default(self, app_client, auth_headers):
        model_id = "gpt-update-default-test"

        first_id = await _create_profile(app_client, auth_headers,
                                          model_id=model_id,
                                          name="Update Default First",
                                          is_default=True)
        second_id = await _create_profile(app_client, auth_headers,
                                           model_id=model_id,
                                           name="Update Default Second")

        # Update second to be default via PUT
        await app_client.put(f"/api/profiles/{second_id}",
                              headers=auth_headers,
                              json={"is_default": True})

        first_detail = await app_client.get(f"/api/profiles/detail/{first_id}",
                                             headers=auth_headers)
        second_detail = await app_client.get(f"/api/profiles/detail/{second_id}",
                                              headers=auth_headers)
        assert first_detail.json()["is_default"] == 0
        assert second_detail.json()["is_default"] == 1


# =========================================================================
# 4. UNIQUENESS CONSTRAINTS
# =========================================================================


class TestUniquenessConstraints:
    """Duplicate name per model rejected; same name for different model is OK."""

    async def test_duplicate_name_same_model_rejected_409(self, app_client, auth_headers):
        model_id = "gpt-unique-test"
        name = "Unique Name Conflict"

        await _create_profile(app_client, auth_headers,
                               model_id=model_id, name=name)

        resp = await app_client.post("/api/profiles", headers=auth_headers, json={
            "model_id": model_id,
            "name": name,
        })
        assert resp.status_code == 409
        assert "error" in resp.json()

    async def test_same_name_different_model_is_allowed(self, app_client, auth_headers):
        name = "Shared Name Different Model"

        resp_a = await app_client.post("/api/profiles", headers=auth_headers, json={
            "model_id": "model-unique-x",
            "name": name,
        })
        resp_b = await app_client.post("/api/profiles", headers=auth_headers, json={
            "model_id": "model-unique-y",
            "name": name,
        })

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

    async def test_update_to_existing_name_same_model_rejected_409(self, app_client, auth_headers):
        model_id = "gpt-update-unique-test"

        await _create_profile(app_client, auth_headers,
                               model_id=model_id, name="Taken Name")
        second_id = await _create_profile(app_client, auth_headers,
                                           model_id=model_id, name="Other Name")

        resp = await app_client.put(f"/api/profiles/{second_id}",
                                    headers=auth_headers,
                                    json={"name": "Taken Name"})
        assert resp.status_code == 409


# =========================================================================
# 5. MAX LIMIT ENFORCEMENT
# =========================================================================


class TestMaxLimitEnforcement:
    """Creating 21st profile for same model is rejected with 422."""

    async def test_create_21st_profile_rejected_422(self, app_client, auth_headers):
        model_id = "gpt-limit-test-model"

        # Create 20 profiles (the max)
        for i in range(20):
            resp = await app_client.post("/api/profiles", headers=auth_headers, json={
                "model_id": model_id,
                "name": f"Limit Profile {i}",
            })
            assert resp.status_code == 200, (
                f"Failed creating profile #{i}: {resp.text}"
            )

        # The 21st should be rejected
        resp = await app_client.post("/api/profiles", headers=auth_headers, json={
            "model_id": model_id,
            "name": "Profile 21 Over Limit",
        })
        assert resp.status_code == 422
        assert "error" in resp.json()


# =========================================================================
# 6. FROM-TUNER CREATION
# =========================================================================


class TestFromTunerCreation:
    """POST /api/profiles/from-tuner stores origin_type and origin_ref correctly."""

    async def test_create_profile_from_param_tuner(self, app_client, auth_headers):
        resp = await app_client.post("/api/profiles/from-tuner", headers=auth_headers, json={
            "model_id": "gpt-4o",
            "name": "From Param Tuner",
            "source_type": "param_tuner",
            "source_id": "tune-run-abc123",
            "params_json": {"temperature": 0.4, "top_p": 0.95},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "profile_id" in data

        # Verify origin fields stored correctly
        profile_id = data["profile_id"]
        detail = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                      headers=auth_headers)
        assert detail.status_code == 200
        detail_data = detail.json()
        assert detail_data["origin_type"] == "param_tuner"
        assert detail_data["origin_ref"] == "tune-run-abc123"

    async def test_create_profile_from_prompt_tuner(self, app_client, auth_headers):
        resp = await app_client.post("/api/profiles/from-tuner", headers=auth_headers, json={
            "model_id": "gpt-4o",
            "name": "From Prompt Tuner",
            "source_type": "prompt_tuner",
            "source_id": "prompt-run-xyz789",
            "system_prompt": "You are a concise assistant.",
            "set_as_default": False,
        })
        assert resp.status_code == 200
        profile_id = resp.json()["profile_id"]

        detail = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                      headers=auth_headers)
        assert detail.status_code == 200
        detail_data = detail.json()
        assert detail_data["origin_type"] == "prompt_tuner"
        assert detail_data["origin_ref"] == "prompt-run-xyz789"
        assert detail_data["system_prompt"] == "You are a concise assistant."

    async def test_create_from_tuner_set_as_default(self, app_client, auth_headers):
        model_id = "gpt-from-tuner-default"
        resp = await app_client.post("/api/profiles/from-tuner", headers=auth_headers, json={
            "model_id": model_id,
            "name": "Tuner Default Profile",
            "source_type": "param_tuner",
            "source_id": "tune-run-default-test",
            "set_as_default": True,
        })
        assert resp.status_code == 200
        profile_id = resp.json()["profile_id"]

        detail = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                      headers=auth_headers)
        assert detail.json()["is_default"] == 1

    async def test_create_from_tuner_missing_source_type_returns_422(self, app_client, auth_headers):
        resp = await app_client.post("/api/profiles/from-tuner", headers=auth_headers, json={
            "model_id": "gpt-4o",
            "name": "Missing Source Type",
            "source_id": "some-id",
            # missing source_type
        })
        assert resp.status_code == 422

    async def test_create_from_tuner_missing_source_id_returns_422(self, app_client, auth_headers):
        resp = await app_client.post("/api/profiles/from-tuner", headers=auth_headers, json={
            "model_id": "gpt-4o",
            "name": "Missing Source ID",
            "source_type": "param_tuner",
            # missing source_id
        })
        assert resp.status_code == 422

    async def test_create_from_tuner_duplicate_name_returns_409(self, app_client, auth_headers):
        await app_client.post("/api/profiles/from-tuner", headers=auth_headers, json={
            "model_id": "gpt-4o",
            "name": "Tuner Dupe Check",
            "source_type": "param_tuner",
            "source_id": "run-1",
        })
        resp = await app_client.post("/api/profiles/from-tuner", headers=auth_headers, json={
            "model_id": "gpt-4o",
            "name": "Tuner Dupe Check",
            "source_type": "param_tuner",
            "source_id": "run-2",
        })
        assert resp.status_code == 409


# =========================================================================
# 7. PROFILE FIELD IN EVAL / BENCHMARK REQUESTS
# =========================================================================


@pytest.mark.usefixtures("clear_active_jobs")
class TestProfileFieldInRequests:
    """profiles dict field accepted by ToolEvalRequest and BenchmarkRequest."""

    async def test_benchmark_request_accepts_profiles_dict(self, app_client, auth_headers):
        """BenchmarkRequest with profiles field is accepted (validation passes)."""
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-profiles-bench",
                                            name="Bench Profiles Test Profile")

        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "targets": [{"provider_key": "openai", "model_id": "gpt-profiles-bench"}],
            "runs": 1,
            "max_tokens": 128,
            "profiles": {"gpt-profiles-bench": profile_id},
        })
        # Should reach job creation (200), not fail on schema validation
        assert resp.status_code == 200
        assert "job_id" in resp.json()

    async def test_tool_eval_request_accepts_profiles_dict(self, app_client, auth_headers):
        """ToolEvalRequest with profiles field passes schema validation."""
        # Create a suite first
        suite_resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Profiles Eval Test Suite",
            "tools": [{"type": "function", "function": {"name": "noop", "description": "noop"}}],
            "test_cases": [{"prompt": "test prompt", "expected_tool": "noop"}],
        })
        suite_id = suite_resp.json()["suite_id"]

        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-4o",
                                            name="Eval Profiles Test Profile")

        resp = await app_client.post("/api/tool-eval", headers=auth_headers, json={
            "suite_id": suite_id,
            "models": ["gpt-4o"],
            "temperature": 0.0,
            "tool_choice": "required",
            "profiles": {"gpt-4o": profile_id},
        })
        # Should reach job/stream creation (200), not fail on schema validation
        assert resp.status_code == 200

    async def test_benchmark_profiles_none_still_accepted(self, app_client, auth_headers, clear_active_jobs):
        """profiles field is optional — None is accepted."""
        resp = await app_client.post("/api/benchmark", headers=auth_headers, json={
            "targets": [{"provider_key": "openai", "model_id": "gpt-4o"}],
            "runs": 1,
            "max_tokens": 128,
            "profiles": None,
        })
        assert resp.status_code == 200


# =========================================================================
# 8. USER ISOLATION
# =========================================================================


class TestUserIsolation:
    """User A cannot see or modify User B's profiles."""

    async def test_user_b_cannot_see_user_a_profiles(self, app_client, auth_headers):
        """Profiles created by user A are not visible to user B."""
        # Create profile as user A (the session test user)
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-isolation-test",
                                            name="User A Private Profile")

        # Register user B
        user_b_headers = await _register_and_login(
            app_client, "userb_isolation@profiles.test"
        )

        # User B lists all profiles — should not see user A's profile
        resp = await app_client.get("/api/profiles", headers=user_b_headers)
        assert resp.status_code == 200
        b_profile_ids = [p["id"] for p in resp.json()["profiles"]]
        assert profile_id not in b_profile_ids

    async def test_user_b_cannot_get_user_a_profile_by_id(self, app_client, auth_headers):
        """User B gets 404 when trying to fetch user A's profile by ID."""
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-isolation-get-test",
                                            name="User A Profile For Get Test")

        user_b_headers = await _register_and_login(
            app_client, "userb_get_isolation@profiles.test"
        )

        resp = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                    headers=user_b_headers)
        assert resp.status_code == 404

    async def test_user_b_cannot_update_user_a_profile(self, app_client, auth_headers):
        """User B gets 404 when trying to update user A's profile."""
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-isolation-update",
                                            name="User A Profile For Update Test")

        user_b_headers = await _register_and_login(
            app_client, "userb_update_isolation@profiles.test"
        )

        resp = await app_client.put(f"/api/profiles/{profile_id}",
                                    headers=user_b_headers,
                                    json={"name": "Hijacked Name"})
        assert resp.status_code == 404

    async def test_user_b_cannot_delete_user_a_profile(self, app_client, auth_headers):
        """User B gets 404 when trying to delete user A's profile."""
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-isolation-delete",
                                            name="User A Profile For Delete Test")

        user_b_headers = await _register_and_login(
            app_client, "userb_delete_isolation@profiles.test"
        )

        resp = await app_client.delete(f"/api/profiles/{profile_id}",
                                       headers=user_b_headers)
        assert resp.status_code == 404

        # Confirm it still exists for user A
        detail = await app_client.get(f"/api/profiles/detail/{profile_id}",
                                      headers=auth_headers)
        assert detail.status_code == 200

    async def test_user_b_cannot_set_default_on_user_a_profile(self, app_client, auth_headers):
        """User B gets 404 when trying to set-default on user A's profile."""
        profile_id = await _create_profile(app_client, auth_headers,
                                            model_id="gpt-isolation-setdefault",
                                            name="User A Profile For SetDefault Test")

        user_b_headers = await _register_and_login(
            app_client, "userb_setdefault_isolation@profiles.test"
        )

        resp = await app_client.post(f"/api/profiles/{profile_id}/set-default",
                                     headers=user_b_headers)
        assert resp.status_code == 404

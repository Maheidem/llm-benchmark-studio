"""Tests for db.py — CRUD operations using a temporary SQLite database."""

import json
import pytest
import pytest_asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone

import db


# ===========================================================================
# Fixtures — temp DB per test
# ===========================================================================


@pytest_asyncio.fixture
async def test_db(tmp_path, monkeypatch):
    """Patch db.DB_PATH to a temp file and initialise all tables."""
    temp_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", temp_db)
    await db.init_db()
    return temp_db


@pytest_asyncio.fixture
async def test_user(test_db):
    """Create a test user and return the user dict."""
    user = await db.create_user("test@example.com", "hashed_pw_123")
    return user


@pytest_asyncio.fixture
async def second_user(test_db):
    """Create a second user for isolation tests."""
    return await db.create_user("other@example.com", "hashed_pw_456")


# ===========================================================================
# init_db
# ===========================================================================


class TestInitDb:
    @pytest.mark.asyncio
    async def test_creates_tables(self, test_db):
        """init_db should create all expected tables."""
        import aiosqlite
        async with aiosqlite.connect(str(test_db)) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        expected = {
            "users", "refresh_tokens", "user_api_keys", "user_configs",
            "benchmark_runs", "rate_limits", "audit_log", "tool_suites",
            "tool_test_cases", "tool_eval_runs", "schedules",
            "param_tune_runs", "prompt_tune_runs", "judge_reports", "jobs",
            "experiments",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    @pytest.mark.asyncio
    async def test_idempotent(self, test_db):
        """Running init_db twice should not error."""
        await db.init_db()  # Second call


# ===========================================================================
# User CRUD
# ===========================================================================


class TestUserCrud:
    @pytest.mark.asyncio
    async def test_create_user(self, test_user):
        assert test_user["email"] == "test@example.com"
        assert test_user["role"] == "user"
        assert "id" in test_user

    @pytest.mark.asyncio
    async def test_get_user_by_email(self, test_user):
        found = await db.get_user_by_email("test@example.com")
        assert found is not None
        assert found["id"] == test_user["id"]

    @pytest.mark.asyncio
    async def test_get_user_by_email_case_insensitive(self, test_user):
        found = await db.get_user_by_email("TEST@EXAMPLE.COM")
        assert found is not None
        assert found["id"] == test_user["id"]

    @pytest.mark.asyncio
    async def test_get_user_by_email_not_found(self, test_db):
        found = await db.get_user_by_email("nobody@example.com")
        assert found is None

    @pytest.mark.asyncio
    async def test_get_user_by_id(self, test_user):
        found = await db.get_user_by_id(test_user["id"])
        assert found is not None
        assert found["email"] == "test@example.com"

    @pytest.mark.asyncio
    async def test_get_user_by_id_not_found(self, test_db):
        found = await db.get_user_by_id("nonexistent_id")
        assert found is None

    @pytest.mark.asyncio
    async def test_count_users(self, test_user, second_user):
        count = await db.count_users()
        assert count == 2

    @pytest.mark.asyncio
    async def test_set_onboarding_completed(self, test_user):
        await db.set_onboarding_completed(test_user["id"])
        found = await db.get_user_by_id(test_user["id"])
        assert found["onboarding_completed"] == 1

    @pytest.mark.asyncio
    async def test_duplicate_email_fails(self, test_user):
        import aiosqlite
        with pytest.raises(aiosqlite.IntegrityError):
            await db.create_user("test@example.com", "another_hash")


# ===========================================================================
# Refresh Token CRUD
# ===========================================================================


class TestRefreshTokenCrud:
    @pytest.mark.asyncio
    async def test_store_and_get_token(self, test_user):
        await db.store_refresh_token(test_user["id"], "hash123", "2099-12-31")
        token = await db.get_refresh_token("hash123")
        assert token is not None
        assert token["user_id"] == test_user["id"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_token(self, test_db):
        token = await db.get_refresh_token("doesnotexist")
        assert token is None

    @pytest.mark.asyncio
    async def test_delete_token(self, test_user):
        await db.store_refresh_token(test_user["id"], "hash_del", "2099-12-31")
        await db.delete_refresh_token("hash_del")
        assert await db.get_refresh_token("hash_del") is None

    @pytest.mark.asyncio
    async def test_delete_all_user_tokens(self, test_user):
        await db.store_refresh_token(test_user["id"], "h1", "2099-12-31")
        await db.store_refresh_token(test_user["id"], "h2", "2099-12-31")
        await db.delete_user_refresh_tokens(test_user["id"])
        assert await db.get_refresh_token("h1") is None
        assert await db.get_refresh_token("h2") is None


# ===========================================================================
# User Config CRUD
# ===========================================================================


class TestUserConfigCrud:
    @pytest.mark.asyncio
    async def test_save_and_get_config(self, test_user):
        config = {"providers": {"openai": {"models": []}}, "defaults": {"temperature": 0.7}}
        await db.save_user_config(test_user["id"], config)
        loaded = await db.get_user_config(test_user["id"])
        assert loaded is not None
        assert loaded["defaults"]["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_get_config_not_set(self, test_user):
        loaded = await db.get_user_config(test_user["id"])
        assert loaded is None

    @pytest.mark.asyncio
    async def test_upsert_config(self, test_user):
        """Saving config twice should update, not duplicate."""
        await db.save_user_config(test_user["id"], {"v": 1})
        await db.save_user_config(test_user["id"], {"v": 2})
        loaded = await db.get_user_config(test_user["id"])
        assert loaded["v"] == 2

    @pytest.mark.asyncio
    async def test_config_isolation(self, test_user, second_user):
        """Each user has their own config."""
        await db.save_user_config(test_user["id"], {"user": "A"})
        await db.save_user_config(second_user["id"], {"user": "B"})
        a = await db.get_user_config(test_user["id"])
        b = await db.get_user_config(second_user["id"])
        assert a["user"] == "A"
        assert b["user"] == "B"


# ===========================================================================
# User API Keys CRUD
# ===========================================================================


class TestUserApiKeysCrud:
    @pytest.mark.asyncio
    async def test_upsert_and_get_key(self, test_user):
        key_id = await db.upsert_user_key(
            test_user["id"], "openai", "OpenAI Key", "encrypted_val_123"
        )
        assert key_id is not None

        val = await db.get_user_key_for_provider(test_user["id"], "openai")
        assert val == "encrypted_val_123"

    @pytest.mark.asyncio
    async def test_get_key_not_found(self, test_user):
        val = await db.get_user_key_for_provider(test_user["id"], "nonexistent")
        assert val is None

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, test_user):
        await db.upsert_user_key(test_user["id"], "openai", "Key V1", "enc_v1")
        await db.upsert_user_key(test_user["id"], "openai", "Key V2", "enc_v2")
        val = await db.get_user_key_for_provider(test_user["id"], "openai")
        assert val == "enc_v2"

    @pytest.mark.asyncio
    async def test_list_user_keys(self, test_user):
        await db.upsert_user_key(test_user["id"], "openai", "OpenAI", "enc1")
        await db.upsert_user_key(test_user["id"], "anthropic", "Anthropic", "enc2")
        keys = await db.get_user_keys(test_user["id"])
        assert len(keys) == 2
        # Encrypted values should NOT be in the list
        for k in keys:
            assert "encrypted_value" not in k

    @pytest.mark.asyncio
    async def test_delete_key(self, test_user):
        await db.upsert_user_key(test_user["id"], "openai", "Key", "enc")
        deleted = await db.delete_user_key(test_user["id"], "openai")
        assert deleted is True
        assert await db.get_user_key_for_provider(test_user["id"], "openai") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key(self, test_user):
        deleted = await db.delete_user_key(test_user["id"], "nope")
        assert deleted is False


# ===========================================================================
# Benchmark Runs CRUD
# ===========================================================================


class TestBenchmarkRunsCrud:
    @pytest.mark.asyncio
    async def test_save_and_get_run(self, test_user):
        run_id = await db.save_benchmark_run(
            test_user["id"], "Test prompt", "0,5000", '{"results": []}'
        )
        run = await db.get_benchmark_run(run_id, test_user["id"])
        assert run is not None
        assert run["prompt"] == "Test prompt"

    @pytest.mark.asyncio
    async def test_get_user_runs(self, test_user):
        await db.save_benchmark_run(test_user["id"], "p1", "0", '{}')
        await db.save_benchmark_run(test_user["id"], "p2", "0", '{}')
        runs = await db.get_user_benchmark_runs(test_user["id"])
        assert len(runs) == 2

    @pytest.mark.asyncio
    async def test_delete_run(self, test_user):
        run_id = await db.save_benchmark_run(test_user["id"], "p", "0", '{}')
        deleted = await db.delete_benchmark_run(run_id, test_user["id"])
        assert deleted is True
        assert await db.get_benchmark_run(run_id, test_user["id"]) is None

    @pytest.mark.asyncio
    async def test_user_isolation(self, test_user, second_user):
        """User cannot access another user's runs."""
        run_id = await db.save_benchmark_run(test_user["id"], "private", "0", '{}')
        assert await db.get_benchmark_run(run_id, second_user["id"]) is None

    @pytest.mark.asyncio
    async def test_pagination(self, test_user):
        for i in range(5):
            await db.save_benchmark_run(test_user["id"], f"p{i}", "0", '{}')
        page1 = await db.get_user_benchmark_runs(test_user["id"], limit=2, offset=0)
        page2 = await db.get_user_benchmark_runs(test_user["id"], limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2


# ===========================================================================
# Tool Suites + Test Cases CRUD
# ===========================================================================


class TestToolSuitesCrud:
    @pytest.mark.asyncio
    async def test_create_and_get_suite(self, test_user):
        tools = json.dumps([{"name": "get_weather", "parameters": {}}])
        suite_id = await db.create_tool_suite(test_user["id"], "Weather Suite", "Tests weather tool", tools)
        suite = await db.get_tool_suite(suite_id, test_user["id"])
        assert suite is not None
        assert suite["name"] == "Weather Suite"

    @pytest.mark.asyncio
    async def test_list_suites(self, test_user):
        tools = json.dumps([])
        await db.create_tool_suite(test_user["id"], "Suite A", "", tools)
        await db.create_tool_suite(test_user["id"], "Suite B", "", tools)
        suites = await db.get_tool_suites(test_user["id"])
        assert len(suites) == 2

    @pytest.mark.asyncio
    async def test_update_suite(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Old Name", "", tools)
        updated = await db.update_tool_suite(suite_id, test_user["id"], name="New Name")
        assert updated is True
        suite = await db.get_tool_suite(suite_id, test_user["id"])
        assert suite["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_delete_suite_cascades_test_cases(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)
        await db.create_test_case(suite_id, "test prompt", "get_weather", '{}')
        deleted = await db.delete_tool_suite(suite_id, test_user["id"])
        assert deleted is True
        cases = await db.get_test_cases(suite_id)
        assert len(cases) == 0


class TestToolTestCasesCrud:
    @pytest.mark.asyncio
    async def test_create_and_get_cases(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)
        case_id = await db.create_test_case(
            suite_id, "What's the weather?", "get_weather", '{"city": "NYC"}'
        )
        cases = await db.get_test_cases(suite_id)
        assert len(cases) == 1
        assert cases[0]["prompt"] == "What's the weather?"
        assert cases[0]["expected_tool"] == "get_weather"

    @pytest.mark.asyncio
    async def test_update_test_case(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)
        case_id = await db.create_test_case(suite_id, "old", "tool_a", '{}')
        updated = await db.update_test_case(case_id, suite_id, prompt="new prompt")
        assert updated is True
        cases = await db.get_test_cases(suite_id)
        assert cases[0]["prompt"] == "new prompt"

    @pytest.mark.asyncio
    async def test_delete_test_case(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)
        case_id = await db.create_test_case(suite_id, "prompt", "tool", '{}')
        deleted = await db.delete_test_case(case_id, suite_id)
        assert deleted is True
        assert len(await db.get_test_cases(suite_id)) == 0

    @pytest.mark.asyncio
    async def test_multi_turn_config(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)
        mt = json.dumps({"multi_turn": True, "turns": 3})
        case_id = await db.create_test_case(suite_id, "prompt", "tool", '{}', multi_turn_config=mt)
        cases = await db.get_test_cases(suite_id)
        assert cases[0]["multi_turn_config"] == mt


# ===========================================================================
# Param Tune Runs CRUD
# ===========================================================================


class TestParamTuneRunsCrud:
    @pytest.mark.asyncio
    async def test_save_and_get_run(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)

        run_id = await db.save_param_tune_run(
            user_id=test_user["id"],
            suite_id=suite_id,

            models_json='["gpt-4o"]',
            search_space_json='{"temperature": {"min": 0, "max": 1, "step": 0.5}}',
            total_combos=3,
        )
        run = await db.get_param_tune_run(run_id, test_user["id"])
        assert run is not None
        assert run["status"] == "running"
        assert run["total_combos"] == 3

    @pytest.mark.asyncio
    async def test_update_run(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)

        run_id = await db.save_param_tune_run(
            user_id=test_user["id"],
            suite_id=suite_id,

            models_json='["gpt-4o"]',
            search_space_json='{}',
            total_combos=2,
        )
        await db.update_param_tune_run(
            run_id, test_user["id"],
            results_json='[{"score": 0.9}]',
            completed_combos=2,
            status="completed",
            duration_s=10.5,
            best_config_json='{"temperature": 0.7}',
            best_score=0.9,
        )
        run = await db.get_param_tune_run(run_id, test_user["id"])
        assert run["status"] == "completed"
        assert run["completed_combos"] == 2
        assert run["best_score"] == 0.9

    @pytest.mark.asyncio
    async def test_delete_run(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)

        run_id = await db.save_param_tune_run(
            user_id=test_user["id"],
            suite_id=suite_id,

            models_json='["gpt-4o"]',
            search_space_json='{}',
            total_combos=1,
        )
        deleted = await db.delete_param_tune_run(run_id, test_user["id"])
        assert deleted is True
        assert await db.get_param_tune_run(run_id, test_user["id"]) is None

    @pytest.mark.asyncio
    async def test_list_runs(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)

        for _ in range(3):
            await db.save_param_tune_run(
                user_id=test_user["id"],
                suite_id=suite_id,
    
                models_json='["gpt-4o"]',
                search_space_json='{}',
                total_combos=1,
            )
        runs = await db.get_param_tune_runs(test_user["id"])
        assert len(runs) == 3


# ===========================================================================
# Jobs CRUD
# ===========================================================================


class TestJobsCrud:
    @staticmethod
    def _job_id():
        import uuid
        return uuid.uuid4().hex

    @pytest.mark.asyncio
    async def test_create_and_get_job(self, test_user):
        jid = self._job_id()
        job = await db.create_job(
            job_id=jid,
            user_id=test_user["id"],
            job_type="benchmark",
            status="pending",
            params_json='{"models": ["gpt-4o"]}',
        )
        assert job is not None
        assert job["job_type"] == "benchmark"
        assert job["status"] == "pending"

        fetched = await db.get_job(jid)
        assert fetched is not None
        assert fetched["id"] == jid

    @pytest.mark.asyncio
    async def test_update_job_progress(self, test_user):
        jid = self._job_id()
        await db.create_job(jid, test_user["id"], "param_tune", "running", '{}')
        await db.update_job_progress(jid, 50, "Running combo 5/10")
        job = await db.get_job(jid)
        assert job["progress_pct"] == 50
        assert job["progress_detail"] == "Running combo 5/10"

    @pytest.mark.asyncio
    async def test_update_job_status(self, test_user):
        jid = self._job_id()
        await db.create_job(jid, test_user["id"], "tool_eval", "running", '{}')
        await db.update_job_status(jid, "done", result_ref="run_123")
        job = await db.get_job(jid)
        assert job["status"] == "done"
        assert job["result_ref"] == "run_123"

    @pytest.mark.asyncio
    async def test_get_user_active_jobs(self, test_user):
        await db.create_job(self._job_id(), test_user["id"], "benchmark", "pending", '{}')
        await db.create_job(self._job_id(), test_user["id"], "param_tune", "running", '{}')
        active = await db.get_user_active_jobs(test_user["id"])
        assert len(active) == 2

    @pytest.mark.asyncio
    async def test_get_user_recent_jobs(self, test_user):
        for _ in range(5):
            await db.create_job(self._job_id(), test_user["id"], "benchmark", "done", '{}')
        recent = await db.get_user_recent_jobs(test_user["id"], limit=3)
        assert len(recent) == 3

    @pytest.mark.asyncio
    async def test_job_type_validation(self, test_user):
        """Invalid job types should fail."""
        import aiosqlite
        with pytest.raises(aiosqlite.IntegrityError):
            await db.create_job(self._job_id(), test_user["id"], "invalid_type", "pending", '{}')


# ===========================================================================
# Audit Log
# ===========================================================================


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_log_audit(self, test_user):
        """log_audit should not raise even with valid params."""
        await db.log_audit(
            user_id=test_user["id"],
            username="test@example.com",
            action="test_action",
            resource_type="test",
            detail={"key": "value"},
        )
        # Verify entry exists
        import aiosqlite
        async with aiosqlite.connect(str(db.DB_PATH)) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'test_action'")
            count = (await cursor.fetchone())[0]
            assert count == 1

    @pytest.mark.asyncio
    async def test_log_audit_fire_and_forget(self, test_db):
        """log_audit with null user should not raise."""
        await db.log_audit(
            user_id=None,
            username="system",
            action="system_action",
        )


# ===========================================================================
# Cleanup Functions
# ===========================================================================


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_stale_param_tune_runs(self, test_user):
        tools = json.dumps([])
        suite_id = await db.create_tool_suite(test_user["id"], "Suite", "", tools)

        run_id = await db.save_param_tune_run(
            user_id=test_user["id"],
            suite_id=suite_id,

            models_json='["gpt-4o"]',
            search_space_json='{}',
            total_combos=1,
        )
        # Manually backdate the timestamp to make it stale
        import aiosqlite
        async with aiosqlite.connect(str(db.DB_PATH)) as conn:
            await conn.execute(
                "UPDATE param_tune_runs SET timestamp = datetime('now', '-60 minutes') WHERE id = ?",
                (run_id,),
            )
            await conn.commit()

        cleaned = await db.cleanup_stale_param_tune_runs(minutes=30)
        assert cleaned >= 1

        run = await db.get_param_tune_run(run_id, test_user["id"])
        assert run["status"] == "interrupted"

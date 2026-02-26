"""Tests validating database audit fixes (CRIT-1 through CRIT-5, MED-1 through MED-11, Group D).

Each test corresponds to a specific finding in .audit/ACCEPTANCE_CRITERIA_CHECKLIST.md.
Run: uv run pytest tests/test_db_migration_fixes.py -v
"""

import asyncio
import inspect
import uuid

import aiosqlite
import pytest

import db

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_isolated_user(suffix: str = "") -> str:
    """Insert a throwaway user directly into the DB and return its ID.

    Requires that the schema is already initialized (via the app_client fixture).
    """
    user_id = uuid.uuid4().hex
    email = f"migration_test_{user_id}{suffix}@test.local"
    await db._db.execute(
        "INSERT INTO users (id, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (user_id, email, "!test_hash", "user"),
    )
    return user_id


async def _delete_user(user_id: str) -> None:
    """Delete a user by ID via a single connection with FK enforcement (cascades to children)."""
    async with aiosqlite.connect(db._db._path()) as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        await conn.commit()


# ---------------------------------------------------------------------------
# CRIT-1: Leaderboard upsert race condition
# ---------------------------------------------------------------------------

class TestCrit1LeaderboardUpsertConcurrent:
    """CRIT-1: concurrent upserts for the same model must produce weighted averages."""

    async def test_leaderboard_upsert_concurrent_sample_count(self, app_client):
        """Two concurrent upserts for the same model should accumulate sample_count correctly."""
        # Create a test user, provider, and model for the model_db_id FK
        user_id = await _create_isolated_user("crit1-sc")
        provider_id = await db.create_provider(user_id, "test-prov-sc", "Test Provider SC")
        model_db_id = await db.create_model(provider_id, f"test-model-crit1-{uuid.uuid4().hex[:8]}", "Test Model SC")

        await asyncio.gather(
            db.upsert_leaderboard_entry(
                model_name="unused", provider="unused",
                tool_accuracy_pct=80.0, param_accuracy_pct=60.0,
                irrel_accuracy_pct=None, sample_count=10,
                model_db_id=model_db_id,
            ),
            db.upsert_leaderboard_entry(
                model_name="unused", provider="unused",
                tool_accuracy_pct=40.0, param_accuracy_pct=20.0,
                irrel_accuracy_pct=None, sample_count=10,
                model_db_id=model_db_id,
            ),
        )

        row = await db._db.fetch_one(
            "SELECT * FROM public_leaderboard WHERE model_db_id=?",
            (model_db_id,),
        )
        assert row is not None
        assert row["sample_count"] == 20, (
            f"Expected sample_count=20 (sum of both upserts), got {row['sample_count']}"
        )

    async def test_leaderboard_upsert_concurrent_weighted_average(self, app_client):
        """Concurrent upserts must produce SQL-level weighted averages, not last-write-wins."""
        # Create a test user, provider, and model for the model_db_id FK
        user_id = await _create_isolated_user("crit1-avg")
        provider_id = await db.create_provider(user_id, "test-prov-avg", "Test Provider Avg")
        model_db_id = await db.create_model(provider_id, f"test-model-crit1-avg-{uuid.uuid4().hex[:8]}", "Test Model Avg")

        # First insert: 100% accuracy, 10 samples
        await db.upsert_leaderboard_entry(
            model_name="unused", provider="unused",
            tool_accuracy_pct=100.0, param_accuracy_pct=100.0,
            irrel_accuracy_pct=None, sample_count=10,
            model_db_id=model_db_id,
        )
        # Second upsert: 0% accuracy, 10 samples -> weighted average = 50%
        await db.upsert_leaderboard_entry(
            model_name="unused", provider="unused",
            tool_accuracy_pct=0.0, param_accuracy_pct=0.0,
            irrel_accuracy_pct=None, sample_count=10,
            model_db_id=model_db_id,
        )

        row = await db._db.fetch_one(
            "SELECT * FROM public_leaderboard WHERE model_db_id=?",
            (model_db_id,),
        )
        assert row is not None
        # Weighted average of (100*10 + 0*10) / 20 = 50.0
        assert row["tool_accuracy_pct"] == pytest.approx(50.0, abs=0.1), (
            f"Expected tool_accuracy_pct≈50.0, got {row['tool_accuracy_pct']}"
        )
        assert row["sample_count"] == 20

    def test_leaderboard_upsert_uses_single_connection(self):
        """upsert_leaderboard_entry must use INSERT ... ON CONFLICT (no separate read then write)."""
        source = inspect.getsource(db.upsert_leaderboard_entry)
        assert "ON CONFLICT" in source, (
            "upsert_leaderboard_entry must use INSERT ... ON CONFLICT DO UPDATE"
        )
        # Must NOT use a separate fetch_one/fetch_all before writing
        assert "fetch_one" not in source, (
            "upsert_leaderboard_entry must not use a separate fetch_one (CRIT-1)"
        )
        assert "fetch_all" not in source, (
            "upsert_leaderboard_entry must not use fetch_all (CRIT-1)"
        )


# ---------------------------------------------------------------------------
# CRIT-2: Schedules CASCADE on user delete
# ---------------------------------------------------------------------------

class TestCrit2SchedulesCascade:
    """CRIT-2: deleting a user must cascade-delete their schedules."""

    async def test_schedules_cascade_on_user_delete(self, app_client):
        """Deleting a user removes their schedules via CASCADE."""
        user_id = await _create_isolated_user("crit2")

        schedule_id = await db.create_schedule(
            user_id=user_id,
            name="cascade-test-schedule",
            prompt="Hello",
            models_json='["model-a"]',
            max_tokens=256,
            temperature=0.7,
            interval_hours=24,
            next_run="2030-01-01T00:00:00",
        )

        # Verify schedule exists before deletion
        row = await db._db.fetch_one(
            "SELECT id FROM schedules WHERE id=?", (schedule_id,)
        )
        assert row is not None, "Schedule should exist before user deletion"

        # Delete the user — must succeed and cascade
        await _delete_user(user_id)

        # Schedule must now be gone
        row = await db._db.fetch_one(
            "SELECT id FROM schedules WHERE id=?", (schedule_id,)
        )
        assert row is None, (
            "Schedule should have been cascade-deleted when the user was deleted (CRIT-2)"
        )

    async def test_schedules_ddl_has_single_cascade_fk(self, app_client):
        """schedules DDL must have exactly one FK for user_id with ON DELETE CASCADE."""
        async with aiosqlite.connect(db._db._path()) as conn:
            # PRAGMA foreign_key_list returns rows: (id, seq, table, from, to, on_update, on_delete, match)
            cursor = await conn.execute("PRAGMA foreign_key_list(schedules)")
            rows = await cursor.fetchall()

        # Column index 3 = "from" (the FK column in schedules), index 6 = on_delete action
        user_id_fks = [r for r in rows if r[3] == "user_id"]
        assert len(user_id_fks) == 1, (
            f"Expected exactly 1 FK for schedules.user_id, got {len(user_id_fks)}"
        )
        on_delete = user_id_fks[0][6].upper()
        assert on_delete == "CASCADE", (
            f"schedules.user_id FK must be ON DELETE CASCADE, got ON DELETE {on_delete}"
        )


# ---------------------------------------------------------------------------
# CRIT-3: busy_timeout on read methods
# ---------------------------------------------------------------------------

class TestCrit3BusyTimeout:
    """CRIT-3: all DatabaseManager read methods must set PRAGMA busy_timeout=5000."""

    def test_fetch_one_has_busy_timeout(self):
        source = inspect.getsource(db.DatabaseManager.fetch_one)
        assert "busy_timeout" in source, "fetch_one must set PRAGMA busy_timeout"

    def test_fetch_all_has_busy_timeout(self):
        source = inspect.getsource(db.DatabaseManager.fetch_all)
        assert "busy_timeout" in source, "fetch_all must set PRAGMA busy_timeout"

    def test_execute_returning_scalar_has_busy_timeout(self):
        source = inspect.getsource(db.DatabaseManager.execute_returning_scalar)
        assert "busy_timeout" in source, "execute_returning_scalar must set PRAGMA busy_timeout"

    def test_get_db_has_busy_timeout(self):
        source = inspect.getsource(db.get_db)
        assert "busy_timeout" in source, "get_db must set PRAGMA busy_timeout"

    def test_write_methods_retain_busy_timeout(self):
        """Write methods must also still set busy_timeout (regression guard)."""
        for method_name in ("execute", "execute_returning_id", "execute_returning_rowcount"):
            source = inspect.getsource(getattr(db.DatabaseManager, method_name))
            assert "busy_timeout" in source, (
                f"DatabaseManager.{method_name} must still set busy_timeout"
            )


# ---------------------------------------------------------------------------
# CRIT-4: cleanup_expired_tokens called in lifespan
# ---------------------------------------------------------------------------

class TestCrit4CleanupExpiredTokens:
    """CRIT-4: app.py lifespan must call cleanup_expired_tokens."""

    def test_cleanup_expired_tokens_in_lifespan(self):
        """app.py must invoke cleanup_expired_tokens() at startup."""
        import app as app_module
        source = inspect.getsource(app_module.lifespan)
        assert "cleanup_expired_tokens" in source, (
            "app.py lifespan must call db.cleanup_expired_tokens() (CRIT-4)"
        )

    async def test_cleanup_expired_tokens_deletes_stale_rows(self, app_client):
        """cleanup_expired_tokens() removes tokens with expires_at in the past."""
        user_id = await _create_isolated_user("crit4")
        token_hash = f"expired_token_hash_{uuid.uuid4().hex}"

        # Insert an already-expired refresh token
        async with aiosqlite.connect(db._db._path()) as conn:
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(
                "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at) "
                "VALUES (lower(hex(randomblob(16))), ?, ?, datetime('now', '-1 day'))",
                (user_id, token_hash),
            )
            await conn.commit()

        # Verify it exists
        row = await db._db.fetch_one(
            "SELECT id FROM refresh_tokens WHERE token_hash=?", (token_hash,)
        )
        assert row is not None, "Expired token should exist before cleanup"

        deleted_count = await db.cleanup_expired_tokens()
        assert deleted_count >= 1, "cleanup_expired_tokens should have deleted at least 1 token"

        row = await db._db.fetch_one(
            "SELECT id FROM refresh_tokens WHERE token_hash=?", (token_hash,)
        )
        assert row is None, "Expired token should have been deleted by cleanup_expired_tokens"

        await _delete_user(user_id)


# ---------------------------------------------------------------------------
# CRIT-5: Suite import is atomic (rollback on failure)
# ---------------------------------------------------------------------------

class TestCrit5SuiteImportAtomic:
    """CRIT-5: create_suite_with_cases must use a single connection and roll back on failure."""

    def test_create_suite_with_cases_uses_single_connection(self):
        """create_suite_with_cases source must have exactly one aiosqlite.connect block."""
        source = inspect.getsource(db.create_suite_with_cases)
        connect_count = source.count("aiosqlite.connect")
        assert connect_count == 1, (
            f"create_suite_with_cases must use exactly one DB connection, found {connect_count}"
        )

    async def test_create_suite_with_cases_rolls_back_on_invalid_case(self, app_client):
        """Suite import with a case containing an invalid param_scoring rolls back entirely."""
        user_id = await _create_isolated_user("crit5")

        invalid_cases = [
            {"prompt": "Good case", "expected_tool": "get_weather",
             "expected_params": '{"city":"Paris"}', "param_scoring": "exact"},
            {"prompt": "Bad case", "expected_tool": "get_weather",
             "expected_params": '{}', "param_scoring": "bogus_invalid_value"},
        ]

        suite_name = f"atomic-test-{uuid.uuid4().hex[:8]}"

        with pytest.raises(Exception):
            await db.create_suite_with_cases(
                user_id=user_id,
                name=suite_name,
                description="Should rollback",
                tools_json='[]',
                cases=invalid_cases,
            )

        # Neither suite nor any cases should exist after rollback
        suite_row = await db._db.fetch_one(
            "SELECT id FROM tool_suites WHERE name=? AND user_id=?",
            (suite_name, user_id),
        )
        assert suite_row is None, (
            "Suite should have been rolled back after invalid case insert (CRIT-5)"
        )

        await _delete_user(user_id)

    async def test_create_suite_with_cases_success(self, app_client):
        """Successful import creates suite and all test cases atomically."""
        user_id = await _create_isolated_user("crit5-ok")

        cases = [
            {"prompt": f"Case {i}", "expected_tool": "get_weather",
             "expected_params": '{}', "param_scoring": "exact"}
            for i in range(5)
        ]
        suite_name = f"atomic-ok-{uuid.uuid4().hex[:8]}"

        suite_id = await db.create_suite_with_cases(
            user_id=user_id,
            name=suite_name,
            description="Should succeed",
            tools_json='[]',
            cases=cases,
        )
        assert suite_id is not None

        case_rows = await db._db.fetch_all(
            "SELECT id FROM tool_test_cases WHERE suite_id=?", (suite_id,)
        )
        assert len(case_rows) == 5, (
            f"Expected 5 test cases, got {len(case_rows)}"
        )

        await _delete_user(user_id)


# ---------------------------------------------------------------------------
# MED-1: create_profile uses single connection for count + insert
# ---------------------------------------------------------------------------

class TestMed1ProfileSingleConnection:
    """MED-1: create_profile must do count check and INSERT on the same connection."""

    def test_create_profile_single_connection(self):
        """create_profile source must use exactly one aiosqlite.connect block."""
        source = inspect.getsource(db.create_profile)
        connect_count = source.count("aiosqlite.connect")
        assert connect_count == 1, (
            f"create_profile must use a single DB connection for count+insert, "
            f"found {connect_count} connect() calls (MED-1)"
        )

    def test_create_profile_count_not_via_separate_fetch(self):
        """create_profile must not call _db.fetch_one separately for the count."""
        source = inspect.getsource(db.create_profile)
        # The count SELECT and INSERT must both be inside the same `async with` block
        # rather than calling _db.fetch_one (which opens its own separate connection)
        assert "_db.fetch_one" not in source, (
            "create_profile must not call _db.fetch_one for the count check (MED-1 TOCTOU)"
        )


# ---------------------------------------------------------------------------
# MED-4: audit_log ON DELETE SET NULL
# ---------------------------------------------------------------------------

class TestMed4AuditLogSetNull:
    """MED-4: deleting a user must SET NULL on audit_log.user_id, not cascade-delete or fail."""

    async def test_audit_log_set_null_on_user_delete(self, app_client):
        """audit_log entries survive user deletion with user_id set to NULL."""
        user_id = await _create_isolated_user("med4")

        await db.log_audit(
            user_id=user_id,
            username="migration_test_user_med4",
            action="test_action_med4",
            resource_type="test",
        )

        # Confirm the audit entry exists
        row = await db._db.fetch_one(
            "SELECT id, user_id FROM audit_log "
            "WHERE user_id=? AND action='test_action_med4'",
            (user_id,),
        )
        assert row is not None, "Audit log entry should exist before user deletion"
        log_id = row["id"]

        # Delete the user — must not raise FK error
        await _delete_user(user_id)

        # Entry must still exist with user_id=NULL
        row = await db._db.fetch_one(
            "SELECT id, user_id FROM audit_log WHERE id=?", (log_id,)
        )
        assert row is not None, "Audit log entry must survive user deletion (MED-4)"
        assert row["user_id"] is None, (
            f"audit_log.user_id must be NULL after user deletion, got {row['user_id']}"
        )

    async def test_audit_log_fk_has_on_delete_set_null(self, app_client):
        """audit_log DDL must declare ON DELETE SET NULL for user_id."""
        async with aiosqlite.connect(db._db._path()) as conn:
            # PRAGMA foreign_key_list: (id, seq, table, from, to, on_update, on_delete, match)
            cursor = await conn.execute("PRAGMA foreign_key_list(audit_log)")
            rows = await cursor.fetchall()

        user_id_fks = [r for r in rows if r[3] == "user_id"]
        assert len(user_id_fks) >= 1, "audit_log must have a FK on user_id"
        on_delete = user_id_fks[0][6].upper()
        assert on_delete == "SET NULL", (
            f"audit_log.user_id FK must be ON DELETE SET NULL, got ON DELETE {on_delete}"
        )


# ---------------------------------------------------------------------------
# MED-9: CHECK constraints reject invalid values
# ---------------------------------------------------------------------------

class TestMed9CheckConstraints:
    """MED-9: CHECK constraints must reject invalid enum values."""

    async def test_invalid_param_scoring_rejected(self, app_client):
        """Inserting a test case with param_scoring='bogus' must raise IntegrityError."""
        user_id = await _create_isolated_user("med9-scoring")
        suite_id = await db.create_tool_suite(
            user_id=user_id, name=f"check-suite-{uuid.uuid4().hex[:8]}",
            description="", tools_json="[]"
        )

        case_id = uuid.uuid4().hex
        with pytest.raises(Exception) as exc_info:
            async with aiosqlite.connect(db._db._path()) as conn:
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.execute(
                    "INSERT INTO tool_test_cases (id, suite_id, prompt, param_scoring) "
                    "VALUES (?, ?, ?, ?)",
                    (case_id, suite_id, "What is the weather?", "bogus"),
                )
                await conn.commit()

        err_msg = str(exc_info.value)
        assert any(word in err_msg.upper() for word in ("CHECK", "CONSTRAINT", "INTEGRITY")), (
            f"Expected CHECK constraint violation, got: {exc_info.value}"
        )

        await _delete_user(user_id)

    async def test_valid_param_scoring_accepted(self, app_client):
        """All valid param_scoring values must be accepted by the DB CHECK constraint."""
        user_id = await _create_isolated_user("med9-valid")
        suite_id = await db.create_tool_suite(
            user_id=user_id, name=f"valid-scoring-{uuid.uuid4().hex[:8]}",
            description="", tools_json="[]"
        )

        for scoring in ("exact", "fuzzy", "contains", "semantic"):
            case_id = uuid.uuid4().hex
            async with aiosqlite.connect(db._db._path()) as conn:
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.execute(
                    "INSERT INTO tool_test_cases (id, suite_id, prompt, param_scoring) "
                    "VALUES (?, ?, ?, ?)",
                    (case_id, suite_id, f"Test {scoring}", scoring),
                )
                await conn.commit()

        rows = await db._db.fetch_all(
            "SELECT param_scoring FROM tool_test_cases WHERE suite_id=?", (suite_id,)
        )
        assert len(rows) == 4, f"All 4 valid param_scoring values should have been inserted"

        await _delete_user(user_id)

    async def test_invalid_optimization_mode_rejected(self, app_client):
        """Inserting a param_tune_run with optimization_mode='bogus' must raise IntegrityError."""
        user_id = await _create_isolated_user("med9-optmode")
        suite_id = await db.create_tool_suite(
            user_id=user_id, name=f"check-suite-opt-{uuid.uuid4().hex[:8]}",
            description="", tools_json="[]"
        )

        run_id = uuid.uuid4().hex
        with pytest.raises(Exception) as exc_info:
            async with aiosqlite.connect(db._db._path()) as conn:
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.execute(
                    "INSERT INTO param_tune_runs "
                    "(id, user_id, suite_id, models_json, search_space_json, "
                    "total_combos, optimization_mode) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, user_id, suite_id, '[]', '{}', 1, "bogus"),
                )
                await conn.commit()

        err_msg = str(exc_info.value)
        assert any(word in err_msg.upper() for word in ("CHECK", "CONSTRAINT", "INTEGRITY")), (
            f"Expected CHECK constraint violation for optimization_mode, got: {exc_info.value}"
        )

        await _delete_user(user_id)

    async def test_invalid_schedule_interval_rejected(self, app_client):
        """Inserting a schedule with interval_hours=0 must raise IntegrityError (CHECK > 0)."""
        user_id = await _create_isolated_user("med9-interval")

        sched_id = uuid.uuid4().hex
        with pytest.raises(Exception) as exc_info:
            async with aiosqlite.connect(db._db._path()) as conn:
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.execute(
                    "INSERT INTO schedules "
                    "(id, user_id, name, prompt, models_json, interval_hours, next_run) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sched_id, user_id, "bad-sched", "hello", '[]', 0, "2030-01-01"),
                )
                await conn.commit()

        err_msg = str(exc_info.value)
        assert any(word in err_msg.upper() for word in ("CHECK", "CONSTRAINT", "INTEGRITY")), (
            f"Expected CHECK constraint violation for interval_hours=0, got: {exc_info.value}"
        )

        await _delete_user(user_id)


# ---------------------------------------------------------------------------
# MED-11: judge_reports status CHECK includes 'interrupted'
# ---------------------------------------------------------------------------

class TestMed11JudgeReportInterrupted:
    """MED-11: judge_reports.status must accept 'interrupted'."""

    async def test_judge_report_accepts_interrupted_status(self, app_client):
        """Updating a judge report to status='interrupted' must not raise."""
        user_id = await _create_isolated_user("med11")

        report_id = await db.save_judge_report(
            user_id=user_id,
            judge_model="test-judge-model",
            mode="post_eval",
        )
        assert report_id is not None

        # Must not raise
        await db.update_judge_report(report_id, status="interrupted")

        row = await db._db.fetch_one(
            "SELECT status FROM judge_reports WHERE id=?", (report_id,)
        )
        assert row is not None
        assert row["status"] == "interrupted", (
            f"Expected status='interrupted', got {row['status']}"
        )

        await _delete_user(user_id)

    async def test_judge_report_ddl_includes_interrupted(self, app_client):
        """judge_reports DDL must include 'interrupted' in the status CHECK constraint."""
        async with aiosqlite.connect(db._db._path()) as conn:
            cursor = await conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='judge_reports'"
            )
            row = await cursor.fetchone()

        assert row is not None
        ddl = row[0]
        assert "'interrupted'" in ddl, (
            "judge_reports DDL must include 'interrupted' in the status CHECK constraint (MED-11)"
        )


# ---------------------------------------------------------------------------
# Group D / MED-2: cleanup_old_jobs
# ---------------------------------------------------------------------------

class TestGroupDCleanupOldJobs:
    """MED-2 / Group D: cleanup_old_jobs removes stale terminal jobs, keeps recent ones."""

    async def test_cleanup_old_jobs_removes_old_terminal_jobs(self, app_client):
        """Jobs completed more than retention_days ago are deleted; recent ones are kept."""
        user_id = await _create_isolated_user("cleanup-old")

        # Insert an old completed job (201 days ago)
        old_job_id = uuid.uuid4().hex
        async with aiosqlite.connect(db._db._path()) as conn:
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(
                "INSERT INTO jobs (id, user_id, job_type, status, completed_at) "
                "VALUES (?, ?, 'benchmark', 'done', datetime('now', '-201 days'))",
                (old_job_id, user_id),
            )
            await conn.commit()

        # Insert a recent completed job (1 day ago)
        recent_job_id = uuid.uuid4().hex
        async with aiosqlite.connect(db._db._path()) as conn:
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(
                "INSERT INTO jobs (id, user_id, job_type, status, completed_at) "
                "VALUES (?, ?, 'benchmark', 'done', datetime('now', '-1 day'))",
                (recent_job_id, user_id),
            )
            await conn.commit()

        deleted = await db.cleanup_old_jobs(retention_days=180)
        assert deleted >= 1, f"Expected at least 1 old job deleted, got {deleted}"

        # Old job must be gone
        old_row = await db._db.fetch_one(
            "SELECT id FROM jobs WHERE id=?", (old_job_id,)
        )
        assert old_row is None, "Old terminal job should have been deleted by cleanup_old_jobs"

        # Recent job must still exist
        recent_row = await db._db.fetch_one(
            "SELECT id FROM jobs WHERE id=?", (recent_job_id,)
        )
        assert recent_row is not None, "Recent job must NOT be deleted by cleanup_old_jobs"

        await _delete_user(user_id)

    async def test_cleanup_old_jobs_keeps_active_jobs(self, app_client):
        """Jobs with non-terminal status are never deleted regardless of age."""
        user_id = await _create_isolated_user("cleanup-active")

        old_active_job_id = uuid.uuid4().hex
        async with aiosqlite.connect(db._db._path()) as conn:
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(
                "INSERT INTO jobs (id, user_id, job_type, status) "
                "VALUES (?, ?, 'benchmark', 'running')",
                (old_active_job_id, user_id),
            )
            await conn.commit()

        # Even with retention_days=0 (delete everything), running jobs survive
        await db.cleanup_old_jobs(retention_days=0)

        row = await db._db.fetch_one(
            "SELECT id FROM jobs WHERE id=?", (old_active_job_id,)
        )
        assert row is not None, "Running jobs must never be deleted by cleanup_old_jobs"

        await _delete_user(user_id)

    def test_cleanup_old_jobs_called_in_lifespan(self):
        """app.py lifespan must call cleanup_old_jobs() (MED-2)."""
        import app as app_module
        source = inspect.getsource(app_module.lifespan)
        assert "cleanup_old_jobs" in source, (
            "app.py lifespan must call db.cleanup_old_jobs() (MED-2)"
        )


# ---------------------------------------------------------------------------
# GLOBAL: init_db idempotency
# ---------------------------------------------------------------------------

class TestGlobalIdempotency:
    """GLOBAL-2: calling init_db() twice must not error or lose data."""

    async def test_init_db_is_idempotent(self, app_client):
        """Running init_db() a second time must not raise and must not drop tables."""
        before = await db._db.execute_returning_scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        )

        # Second call must not raise
        await db.init_db()

        after = await db._db.execute_returning_scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        )

        assert after >= before, (
            f"init_db() second call must not drop tables: before={before}, after={after}"
        )

# Database Fix Acceptance Criteria Checklist

**Project:** LLM Benchmark Studio
**Source:** `.audit/DATABASE_EVALUATION_REPORT.md`
**Date:** 2026-02-25
**Purpose:** Strict pass/fail criteria for validating every fix from the database audit.

---

## Global Acceptance Criteria (Must Pass Before Any Gate)

| ID | Criterion | Verification |
|----|-----------|-------------|
| GLOBAL-1 | All 988 existing tests pass with zero failures | `uv run pytest` -- exit code 0, 988+ tests pass |
| GLOBAL-2 | `init_db()` remains idempotent -- calling it twice causes no errors or data loss | Run `init_db()` twice sequentially in a test; assert no exceptions, assert table count unchanged |
| GLOBAL-3 | Migration handles existing data -- schema changes applied to populated DB without data loss | Test: create data with old schema, run `init_db()`, verify all rows still accessible |
| GLOBAL-4 | Schema changes work on empty DB (fresh install) | Delete test DB, run `init_db()`, verify all 20 tables created |
| GLOBAL-5 | No new Python dependencies added | `diff` the `pyproject.toml` before/after -- no new entries in `[dependencies]` |
| GLOBAL-6 | No existing API endpoints change their request/response contract | Existing API tests pass unchanged (no schema modifications to `schemas.py` request models) |

---

## Gate 1: Local (Must Pass Before Deploying to Staging)

### Critical Findings

#### CRIT-1: Leaderboard Upsert Race Condition
- **Finding:** `upsert_leaderboard_entry()` at `db.py:1982-2023` reads on Connection 1, writes on Connection 2. Lost update under concurrency.
- **Pass condition:** The function uses EITHER (a) `INSERT ... ON CONFLICT DO UPDATE` with SQL-level arithmetic for weighted averages, OR (b) a single connection with `BEGIN EXCLUSIVE` wrapping the read-then-write.
- **Verification:**
  1. Code inspection: `db.py` function `upsert_leaderboard_entry()` must NOT have a separate `fetch_one()` followed by a separate `execute()`. The read and write must share one connection.
  2. SQL inspection: If using `ON CONFLICT DO UPDATE`, the SET clause must compute the weighted average in SQL (e.g., `SET tool_accuracy_pct = (tool_accuracy_pct * sample_count + excluded.tool_accuracy_pct * excluded.sample_count) / (sample_count + excluded.sample_count)`).
  3. Test: Concurrent upsert test -- submit two upserts for the same model/provider simultaneously via `asyncio.gather()`. Assert `sample_count` equals the sum of both individual sample counts. Assert accuracy values reflect correct weighted average of both inputs, not just the last write.
- **Priority:** MUST-FIX

#### CRIT-2: Schedules Table Conflicting FK Declarations
- **Finding:** `db.py:295` declares `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE` (column-level) and `db.py:306` declares `FOREIGN KEY (user_id) REFERENCES users(id)` (table-level, NO CASCADE). The table-level FK blocks user deletion.
- **Pass condition:** Only ONE FK declaration for `schedules.user_id` exists, and it includes `ON DELETE CASCADE`.
- **Verification:**
  1. Code inspection: In `init_db()`, the `CREATE TABLE IF NOT EXISTS schedules` DDL must have exactly one FK reference for `user_id`, with `ON DELETE CASCADE`.
  2. Test: Create a user, create a schedule for that user, delete the user, assert the schedule is also deleted (CASCADE). This test must pass.
  3. SQL verification: `PRAGMA foreign_key_list(schedules)` returns exactly one entry for `user_id` with `on_delete = CASCADE`.
- **Priority:** MUST-FIX

#### CRIT-3: Missing busy_timeout on Read Operations
- **Finding:** `fetch_one()` (db.py:30-36), `fetch_all()` (db.py:38-44), `execute_returning_scalar()` (db.py:89-94), and `get_db()` (db.py:101-108) do not set `PRAGMA busy_timeout`. Reads can fail with SQLITE_BUSY under concurrent write load.
- **Pass condition:** All `DatabaseManager` methods AND `get_db()` set `PRAGMA busy_timeout=5000` before executing any query.
- **Verification:**
  1. Code inspection: Each of these four functions must contain `await conn.execute("PRAGMA busy_timeout=5000")` (or equivalent) BEFORE the user query executes.
  2. Verify `fetch_one`, `fetch_all`, `execute_returning_scalar` all have the pragma. Count occurrences: must be at least 4 distinct locations (the 3 read methods + `get_db()`).
  3. Note: Write methods `execute()`, `execute_returning_id()`, `execute_returning_row()`, `execute_returning_rowcount()` already set `busy_timeout=5000` -- verify they remain unchanged.
- **Priority:** MUST-FIX

#### CRIT-4: Refresh Token Cleanup Never Called
- **Finding:** `db.cleanup_expired_tokens()` exists at `db.py:826-828` but is never invoked. Expired refresh tokens grow unbounded.
- **Pass condition:** `cleanup_expired_tokens()` is called during app startup (in `app.py` lifespan function).
- **Verification:**
  1. Code inspection: `app.py` lifespan function must contain `await db.cleanup_expired_tokens()` (or equivalent call). Must be near the existing `await db.cleanup_audit_log(retention_days=90)` line.
  2. Grep: `grep -n "cleanup_expired_tokens" app.py` must return at least one match outside of imports.
  3. Test: Insert an expired refresh token (expires_at in the past), call the lifespan startup sequence, verify the token is deleted.
- **Priority:** MUST-FIX

#### CRIT-5: Suite/BFCL/MCP Imports Not Transactional
- **Finding:** Suite import (`routers/tool_eval.py:626-661`), BFCL import (`routers/tool_eval.py:926-963`), and MCP import (`routers/mcp.py:227-243`) each create a suite on one connection, then loop creating test cases on N separate connections. Crash mid-import leaves partial data.
- **Pass condition:** Each import operation uses a SINGLE database connection for the entire suite + test cases write. Either (a) a new `db.py` function that accepts a suite + list of test cases and writes them in one connection, or (b) direct use of a single connection with `executemany()` in the router.
- **Verification:**
  1. Code inspection: The import endpoints must NOT call `db.create_tool_suite()` followed by a loop of `db.create_test_case()`. Instead, one of:
     - A single `db.create_suite_with_test_cases()` function exists and is used, OR
     - The router opens one connection, does all inserts, then commits once.
  2. Connection count: The entire import (suite + N test cases) must open at most 1 database connection.
  3. Test: Mock `aiosqlite.connect` to raise after the suite insert but before all test cases are inserted. Verify neither the suite nor any test cases exist in the DB (rollback occurred).
  4. All three paths must be fixed: tool_eval suite import, BFCL import, MCP import.
- **Priority:** MUST-FIX

---

### Medium Findings

#### MED-1: Profile Count Check TOCTOU Race
- **Finding:** `db.py` `create_profile()` reads the count on one connection, then inserts on another. Two concurrent requests could both pass the count check and exceed the 20-profile limit.
- **Pass condition:** The count check and INSERT happen on the SAME connection, within a single transaction. Alternatively, a UNIQUE constraint or trigger enforces the limit at the DB level.
- **Verification:**
  1. Code inspection: `create_profile()` in `db.py` must not call `_db.fetch_one()` for the count and then separately call `_db.execute()` for the insert. Both must be on one connection.
  2. The count query and the INSERT must be under the same `async with aiosqlite.connect(...)` block.
- **Priority:** MUST-FIX

#### MED-2: No Cleanup for 7 Unbounded Tables
- **Finding:** `benchmark_runs`, `tool_eval_runs`, `param_tune_runs`, `prompt_tune_runs`, `judge_reports`, `jobs`, `prompt_versions` grow without bound.
- **Pass condition:** At minimum, the `jobs` table gets cleanup (oldest completed jobs beyond a retention period). Other tables: at least a documented plan or admin endpoint, not necessarily automated cleanup in this sprint.
- **Verification:**
  1. Code inspection: A cleanup function exists for the `jobs` table (e.g., `cleanup_old_jobs(retention_days=180)`).
  2. That function is called in `app.py` lifespan, OR an admin API endpoint exists to trigger it.
  3. Test: Insert jobs with timestamps older than retention, call cleanup, verify they are deleted while recent jobs remain.
- **Priority:** MUST-FIX (jobs table minimum; others can be deferred)

#### MED-3: Job Submit Slot Check Not Atomic
- **Finding:** `job_registry.py:131-163` -- the slot check under `_slot_lock` and the DB insert are not atomic. Brief concurrency overshoot possible.
- **Pass condition:** The DB insert is performed while still holding the `_slot_lock`, OR the slot counter update and DB insert are combined so a failure in one rolls back the other.
- **Verification:**
  1. Code inspection: In `submit()`, the `await db.create_job(...)` call must be INSIDE the `async with self._slot_lock:` block, not outside it.
  2. Alternatively, if the design keeps them separate, document why the brief overshoot is acceptable (comment in code).
- **Priority:** MUST-FIX

#### MED-4: audit_log FK Has No ON DELETE Action
- **Finding:** `db.py:191` -- `audit_log.user_id` references `users(id)` with no cascade. Admin code at `admin.py:92` manually sets `user_id=NULL` before deleting users, which is fragile.
- **Pass condition:** Either (a) `audit_log.user_id` FK gets `ON DELETE SET NULL`, OR (b) the manual NULL-out in admin.py is wrapped in the same transaction as the user delete (not separate connections).
- **Verification:**
  1. Code inspection: Check `db.py` DDL for `audit_log` -- FK should include `ON DELETE SET NULL`.
  2. If DDL cannot change (existing table), verify an ALTER TABLE migration or that the manual approach in admin.py is transactional.
  3. Test: Create user, create audit log entry for that user, delete user, verify audit log entry still exists with `user_id = NULL`.
- **Priority:** MUST-FIX

#### MED-5: rate_limits.updated_by FK Has No ON DELETE Action
- **Finding:** `db.py:183` -- `updated_by TEXT REFERENCES users(id)` has no ON DELETE. If an admin is deleted, dangling reference remains.
- **Pass condition:** `updated_by` FK gets `ON DELETE SET NULL`.
- **Verification:**
  1. Code inspection: DDL for `rate_limits` includes `ON DELETE SET NULL` on `updated_by`.
  2. If existing table, verify migration handles it.
  3. Test: Admin sets rate limit (updated_by = admin_id), admin is deleted, verify `updated_by` is NULL (not a FK violation).
- **Priority:** MUST-FIX

#### MED-6: judge_reports.eval_run_id_b Has No FK
- **Finding:** `db.py:399` -- `eval_run_id_b TEXT` has no FOREIGN KEY. Used in comparative mode. Dangling references possible if the referenced eval run is deleted.
- **Pass condition:** `eval_run_id_b` has a FK to `tool_eval_runs(id) ON DELETE SET NULL`.
- **Verification:**
  1. Code inspection: DDL or ALTER TABLE adds FK for `eval_run_id_b`.
  2. Test: Create two eval runs, create a comparative judge report referencing both. Delete eval run B. Verify `eval_run_id_b` is NULL (not dangling).
- **Priority:** MUST-FIX

#### MED-7: N+1 Test Case Creation in Imports
- **Finding:** Same as CRIT-5 but from a performance perspective. Each test case opens a new connection.
- **Pass condition:** Covered by CRIT-5 fix. The batch import uses one connection. Additionally, if possible, `executemany()` is used for the test case inserts.
- **Verification:**
  1. Covered by CRIT-5 verification.
  2. Bonus: Performance test -- import a suite with 100 test cases, verify it completes in under 2 seconds (vs ~10+ seconds with N+1).
- **Priority:** MUST-FIX (merged with CRIT-5)

#### MED-8: All Analytics Aggregation in Python
- **Finding:** Analytics endpoints load all JSON blobs and aggregate in Python. Scales poorly.
- **Pass condition:** This is a STRATEGIC fix. For this sprint: ACCEPTED AS-IS with a documented TODO. No code change required NOW.
- **Verification:**
  1. A TODO or comment exists in `routers/analytics.py` acknowledging the Python aggregation limitation.
  2. No regression: existing analytics endpoints still return correct data.
- **Priority:** DEFERRED (document only)

#### MED-9: 8 Missing CHECK Constraints
- **Finding:** `tool_test_cases.param_scoring`, `param_tune_runs.optimization_mode`, `tool_test_cases.category`, `schedules.interval_hours`, 7 boolean columns, score/percentage REAL columns lack CHECK constraints.
- **Pass condition:** At minimum, these CHECK constraints are added:
  - `param_scoring IN ('exact', 'fuzzy', 'contains', 'semantic')`
  - `optimization_mode IN ('grid', 'random', 'bayesian')`
  - `category IN ('simple', 'parallel', 'multi_turn', 'irrelevance')` OR category is nullable (no CHECK needed if free-form)
  - `interval_hours > 0`
- **Verification:**
  1. Code inspection: DDL or ALTER for each column includes the CHECK.
  2. Test: Attempt to insert a row with an invalid `param_scoring` value (e.g., 'bogus'). Verify the DB rejects it (IntegrityError).
  3. Note: Boolean and REAL checks are NICE-TO-HAVE for this sprint.
- **Priority:** MUST-FIX (first 4); NICE-TO-HAVE (booleans, REAL ranges)

#### MED-10: No Backup/Recovery Mechanism
- **Finding:** No `sqlite3_backup()`, no scheduled backup, no recovery procedure.
- **Pass condition:** DEFERRED for this sprint. Document the risk.
- **Verification:** N/A -- tracked as future work.
- **Priority:** DEFERRED

#### MED-11: judge_reports.status CHECK Missing 'interrupted'
- **Finding:** `db.py:406` -- CHECK allows `('running','completed','error')` but jobs system uses 'interrupted' status. If a judge job is interrupted, the judge_reports status cannot be set to 'interrupted'.
- **Pass condition:** The CHECK constraint for `judge_reports.status` includes 'interrupted': `CHECK(status IN ('running','completed','error','interrupted'))`.
- **Verification:**
  1. Code inspection: DDL for `judge_reports` includes 'interrupted' in the status CHECK.
  2. Test: Create a judge report, update its status to 'interrupted', verify no error.
  3. Note: Altering CHECK on existing tables requires migration (recreate table or new column). Verify migration approach.
- **Priority:** MUST-FIX

---

### Low Findings

#### LOW-1: 4 Redundant Indexes
- **Finding:** `idx_user_configs_user`, `idx_refresh_tokens_hash`, `idx_pwreset_token`, `idx_leaderboard_model` duplicate UNIQUE constraints.
- **Pass condition:** These 4 indexes are removed from `init_db()`.
- **Verification:**
  1. Code inspection: The 4 `CREATE INDEX` statements are removed.
  2. `init_db()` on fresh DB: `SELECT count(*) FROM sqlite_master WHERE type='index' AND name IN ('idx_user_configs_user','idx_refresh_tokens_hash','idx_pwreset_token','idx_leaderboard_model')` returns 0.
  3. Note: Existing DBs may retain these indexes. A `DROP INDEX IF EXISTS` migration is acceptable.
- **Priority:** NICE-TO-HAVE

#### LOW-2: No Migration Versioning
- **Finding:** 22 ALTER TABLE statements re-run every startup, caught by try/except.
- **Pass condition:** DEFERRED. This is a strategic improvement. For now: the try/except pattern continues to work. No regression.
- **Verification:** `init_db()` idempotency test (GLOBAL-2) covers this.
- **Priority:** NICE-TO-HAVE (deferred)

#### LOW-3: schedules.created Naming Inconsistency
- **Finding:** Column is `created` instead of `created_at` (all other tables use `created_at`).
- **Pass condition:** Either (a) add `created_at` alias via ALTER TABLE ADD COLUMN with migration, OR (b) document the inconsistency and leave as-is.
- **Verification:** If fixed, verify `schedules` DDL uses `created_at`. If not fixed, add a code comment.
- **Priority:** NICE-TO-HAVE

#### LOW-4: Dual PK Generation
- **Finding:** DDL defines `DEFAULT (lower(hex(randomblob(16))))` but application always uses `uuid.uuid4().hex`. The DEFAULT is never exercised.
- **Pass condition:** ACCEPTED AS-IS. The defaults serve as a safety net. No change required.
- **Verification:** N/A.
- **Priority:** NICE-TO-HAVE (no action)

#### LOW-5: Status Default Divergence
- **Finding:** `param_tune_runs` defaults to `status='running'` while `jobs` defaults to `status='pending'`.
- **Pass condition:** `param_tune_runs.status` default should be `'pending'` to match the `jobs` table convention, OR document why 'running' is intentional (param tunes start immediately without going through job queue).
- **Verification:**
  1. Code inspection: Check DDL for `param_tune_runs` status default.
  2. If changed to 'pending': verify no application code relies on the 'running' default.
- **Priority:** NICE-TO-HAVE

#### LOW-6: get_db() Pattern Relies on Caller to Close
- **Finding:** `get_db()` returns a raw connection. Caller must close it. 7 admin routes use try/finally to close.
- **Pass condition:** Either (a) `get_db()` is converted to an async context manager, OR (b) all callers are verified to properly close the connection in `finally` blocks.
- **Verification:**
  1. Grep: Every `await db.get_db()` call is inside a `try/finally` with `await conn.close()` in the `finally`.
  2. Count callers vs count proper cleanup patterns. All must match.
- **Priority:** NICE-TO-HAVE

#### LOW-7: Google OAuth Empty Password Hash
- **Finding:** `db.py:753` -- Google OAuth stores empty string as `password_hash`.
- **Pass condition:** Google OAuth users store a sentinel value (e.g., `'!oauth'`) or NULL instead of empty string, to distinguish from a valid bcrypt hash.
- **Verification:**
  1. Code inspection: Google OAuth user creation does not store `""` as password_hash.
  2. Test: Google OAuth user creation stores non-empty sentinel or NULL.
- **Priority:** NICE-TO-HAVE

#### LOW-8: Missing UNIQUE on Suite/Experiment/Schedule Names Per User
- **Finding:** No UNIQUE(user_id, name) on `tool_suites`, `experiments`, or `schedules`. Users can create duplicates.
- **Pass condition:** DEFERRED. Adding UNIQUE constraints to existing tables with potential duplicate data is risky without data cleanup. Document the gap.
- **Verification:** N/A.
- **Priority:** NICE-TO-HAVE (deferred)

#### LOW-9: No PRAGMA synchronous=FULL
- **Finding:** WAL mode defaults to `synchronous=NORMAL`. Theoretical data loss on power failure.
- **Pass condition:** ACCEPTED AS-IS for a SaaS benchmark tool. The performance cost of FULL is not justified.
- **Verification:** N/A -- no change.
- **Priority:** NICE-TO-HAVE (no action)

#### LOW-10: Per-Connection PRAGMA Overhead
- **Finding:** Every write connection runs `PRAGMA busy_timeout=5000` and `PRAGMA foreign_keys=ON`.
- **Pass condition:** ACCEPTED AS-IS. The overhead is negligible (~0.1ms per PRAGMA). A connection pool would eliminate this but adds complexity.
- **Verification:** N/A -- no change.
- **Priority:** NICE-TO-HAVE (no action)

#### LOW-11: Internal Writes Skip user_id in WHERE
- **Finding:** `update_judge_report()` (db.py:1410-1418) and `update_tool_eval_run()` (db.py:1179-1189) do not include `AND user_id = ?` in WHERE clause. Callers validate ownership first.
- **Pass condition:** Add `AND user_id = ?` to the WHERE clause of both functions, with `user_id` as a required parameter.
- **Verification:**
  1. Code inspection: Both functions accept `user_id` parameter and include it in the WHERE clause.
  2. Test: Attempt to update a judge report with a different user's ID. Verify no rows are affected.
- **Priority:** NICE-TO-HAVE

#### LOW-12: Test Coverage Gaps
- **Finding:** Schedules, judge_reports, experiments, model_profiles, prompt_versions, public_leaderboard CRUD not tested. No concurrent connection tests. No full cascade chain test.
- **Pass condition:** At minimum, add tests for:
  - Schedules CASCADE on user delete (validates CRIT-2 fix)
  - Judge report status update to 'interrupted' (validates MED-11 fix)
  - Leaderboard concurrent upsert (validates CRIT-1 fix)
  - Suite import rollback on failure (validates CRIT-5 fix)
- **Verification:** New test files or test functions exist and pass.
- **Priority:** MUST-FIX (for the 4 tests above that validate critical fixes); NICE-TO-HAVE (for full CRUD coverage of all tables)

---

## Gate 2: Staging (Must Pass Before Deploying to Prod)

| ID | Criterion | Verification |
|----|-----------|-------------|
| STAGE-1 | All Gate 1 criteria pass | CI pipeline green |
| STAGE-2 | Application starts successfully | Health check at `/healthz` returns 200 |
| STAGE-3 | Existing user data intact after migration | Log in as existing staging user; verify suites, runs, settings still present |
| STAGE-4 | Schedule CRUD works end-to-end | Create a schedule via UI, verify it appears; delete user, verify schedule gone |
| STAGE-5 | Tool eval suite import works | Import a suite with 20+ test cases via UI; verify all test cases created |
| STAGE-6 | Leaderboard updates correctly | Run a tool eval with leaderboard opt-in; verify leaderboard shows correct data |
| STAGE-7 | No SQLITE_BUSY errors in logs | After staging deployment, run 3 concurrent benchmarks. Check logs for `SQLITE_BUSY` or `database is locked` -- must find zero. Command: `curl -sf "https://staging-benchmark.maheidem.com/api/admin/logs?token=GiJg13w4wcR0nP0KKnyZmTgSjRei5d1AeBEXj613Xic&search=SQLITE_BUSY&lines=50"` |
| STAGE-8 | Refresh tokens cleaned up on startup | Check staging logs for evidence of `cleanup_expired_tokens` execution (or add log line) |
| STAGE-9 | Admin can delete a user who has schedules | Create a test user with schedules on staging, delete via admin panel, verify clean deletion with no FK errors |
| STAGE-10 | Jobs table not growing unbounded | After running several jobs, verify old completed jobs are cleaned up (if MED-2 implemented) |

---

## Gate 3: Production (Must Be Verified After Prod Deploy)

| ID | Criterion | Verification |
|----|-----------|-------------|
| PROD-1 | Health check passes | `curl https://benchmark.maheidem.com/healthz` returns 200 |
| PROD-2 | No errors in first 30 minutes of logs | Check prod logs for ERROR level entries |
| PROD-3 | Existing user sessions work (not invalidated) | Verify existing prod users can access the app without re-login |
| PROD-4 | All existing data accessible | Spot-check: existing benchmark runs, tool eval runs, schedules visible in UI |
| PROD-5 | Database integrity check | Run `PRAGMA integrity_check` on prod DB (via admin endpoint or direct access) -- must return "ok" |
| PROD-6 | Expired refresh tokens cleaned | After first startup, verify `refresh_tokens` table does not contain rows with `expires_at` in the past |

---

## Summary Scoreboard

| Category | Total | Must-Fix | Nice-to-Have | Deferred |
|----------|-------|----------|-------------|----------|
| Critical | 5 | 5 | 0 | 0 |
| Medium | 11 | 8 | 0 | 3 (MED-2 partial, MED-8, MED-10) |
| Low | 12 | 1 (LOW-12 partial) | 7 | 4 (LOW-2, LOW-4, LOW-8, LOW-9, LOW-10) |
| **Total** | **28** | **14** | **7** | **7** |

### Minimum Shipping Bar

To pass the product quality gate, ALL of the following must be true:

1. **5/5 Critical findings fixed and tested**
2. **8/11 Medium findings fixed** (MED-8, MED-10 deferred; MED-2 partial -- at least jobs table)
3. **LOW-12 partial** -- at minimum 4 new tests validating the critical fixes
4. **All 988+ existing tests pass**
5. **Gate 1 and Gate 2 criteria all green**

Any CRIT finding left unfixed is an automatic FAIL on the quality gate. No exceptions.

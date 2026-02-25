# Database Evaluation Report

**Project:** LLM Benchmark Studio
**Database:** SQLite 3 + aiosqlite (WAL mode)
**Date:** 2026-02-25
**Scope:** Schema design, ACID compliance, query performance, robustness
**Tables:** 20 | **Indexes:** 33 | **JSON blob columns:** 30+

---

## Executive Summary

| Dimension | Grade | Summary |
|-----------|-------|---------|
| Schema Design & Normalization | **B** | Solid relational design with deliberate denormalization via JSON blobs. 3NF achieved on relational columns. |
| ACID Compliance | **B-** | Atomicity gaps in multi-step writes. Consistency strong via CHECK/FK. Isolation adequate under WAL. Durability default. |
| Query Performance | **C+** | N+1 patterns in import paths. All aggregation in Python (not SQL). No batch writes. Indexing is thorough. |
| Robustness & Error Handling | **B** | Connection lifecycle is solid. No backup/recovery. Unbounded table growth. One conflicting FK declaration. |
| SQL Injection Safety | **A** | All queries parameterized. No injection vectors found. |
| Multi-User Isolation | **A-** | All queries scoped to user_id. Internal writes skip user_id check but callers validate ownership. |

**Overall: B-** — Production-viable for current scale. Key risks are crash-atomicity gaps, missing busy_timeout on reads, and unbounded table growth.

---

## Table of Contents

1. [Critical Findings (Fix Now)](#1-critical-findings)
2. [Schema & Normalization](#2-schema--normalization)
3. [ACID Compliance](#3-acid-compliance)
4. [Query Performance](#4-query-performance)
5. [Robustness & Error Handling](#5-robustness--error-handling)
6. [Security](#6-security)
7. [Recommendations](#7-recommendations)

---

## 1. Critical Findings

### CRIT-1: Leaderboard Upsert Race Condition
**File:** `db.py:1982-2023` | **Risk:** Data corruption

`upsert_leaderboard_entry()` reads existing data on Connection 1, then writes on Connection 2. Two concurrent tool evals completing for the same model/provider can both read the same row, compute weighted averages from stale data, and overwrite each other. **Lost update problem.**

**Fix:** Use `INSERT ... ON CONFLICT DO UPDATE` with SQL-level weighted average math, or wrap in a single connection with explicit `BEGIN EXCLUSIVE`.

### CRIT-2: Schedules Table — Conflicting FK Declarations
**File:** `db.py:295-306` | **Risk:** User deletion failure

The `schedules` table declares `user_id` FK twice:
- Line 295 (column-level): `REFERENCES users(id) ON DELETE CASCADE`
- Line 306 (table-level): `FOREIGN KEY (user_id) REFERENCES users(id)` — **no CASCADE**

The table-level FK without CASCADE blocks user deletion if the user has schedules. The two declarations conflict silently.

**Fix:** Remove the redundant table-level FK at line 306, or add `ON DELETE CASCADE` to it.

### CRIT-3: Missing busy_timeout on Read Operations
**File:** `db.py:30-44, 89-94, 101-108` | **Risk:** Spurious read failures

`fetch_one()`, `fetch_all()`, `execute_returning_scalar()`, and `get_db()` do not set `PRAGMA busy_timeout`. Under concurrent write load (benchmark + WebSocket status poll), reads can fail immediately with `SQLITE_BUSY` instead of waiting.

**Fix:** Add `PRAGMA busy_timeout=5000` to all `DatabaseManager` methods and `get_db()`.

### CRIT-4: Refresh Token Cleanup Never Called
**File:** `db.py:826-828` | **Risk:** Unbounded table growth

`cleanup_expired_tokens()` exists but is never invoked anywhere — not in lifespan, not in scheduler. Expired refresh tokens accumulate forever.

**Fix:** Call `cleanup_expired_tokens()` in `app.py` lifespan alongside the existing `cleanup_audit_log()` call.

### CRIT-5: Suite Import Not Transactional
**File:** `routers/tool_eval.py:626-661, 926-963` | **Risk:** Partial data on crash

Suite import creates one suite record then loops to create N test cases, each in a separate connection. A crash mid-import leaves a suite with partial test cases.

**Fix:** Use a single connection for the entire import operation, or use `executemany()` for batch insert.

---

## 2. Schema & Normalization

### 2.1 Table Catalog (20 tables)

| # | Table | PK Type | Row Growth | JSON Columns |
|---|-------|---------|------------|-------------|
| 1 | `users` | hex UUID | Bounded (registrations) | 0 |
| 2 | `refresh_tokens` | hex UUID | Unbounded (never cleaned) | 0 |
| 3 | `user_api_keys` | hex UUID | Bounded (~10/user) | 0 |
| 4 | `user_configs` | hex UUID | 1 per user | 1 (config_yaml) |
| 5 | `benchmark_runs` | hex UUID | **Unbounded** | 4 (results, metadata, config, tiers) |
| 6 | `rate_limits` | AUTOINCREMENT | 1 per user | 0 |
| 7 | `audit_log` | AUTOINCREMENT | **Unbounded** (90-day retention) | 0 |
| 8 | `tool_suites` | hex UUID | Bounded (~20/user) | 1 (tools_json) |
| 9 | `tool_test_cases` | hex UUID | Bounded (~50/suite) | 3 |
| 10 | `experiments` | hex UUID | Bounded (~20/user) | 2 |
| 11 | `tool_eval_runs` | hex UUID | **Unbounded** | **6** (most denormalized) |
| 12 | `schedules` | hex UUID | Bounded (~10/user) | 1 |
| 13 | `param_tune_runs` | hex UUID | **Unbounded** | 5 |
| 14 | `prompt_tune_runs` | hex UUID | **Unbounded** | 4 |
| 15 | `judge_reports` | hex UUID | **Unbounded** | 3 |
| 16 | `jobs` | hex UUID | **Unbounded** | 1 (params_json) |
| 17 | `model_profiles` | hex UUID | Bounded (20/model) | 1 |
| 18 | `password_reset_tokens` | hex UUID | Low volume | 0 |
| 19 | `prompt_versions` | hex UUID | **Unbounded** | 0 |
| 20 | `public_leaderboard` | hex UUID | Bounded (model count) | 0 |

### 2.2 Normalization Assessment

| Normal Form | Status | Notes |
|-------------|--------|-------|
| **1NF** | Partial | 30+ JSON blob columns violate atomic value principle. TEXT columns contain structured data that cannot be queried at the SQL level. |
| **2NF** | Pass | All tables use single-column PKs — no partial key dependencies possible. |
| **3NF** | Pass (with deliberate violations) | `suite_name` in 3 run tables is a transitive dependency (derivable from `suite_id`). `audit_log.username` duplicates `users.email`. Both are intentional for historical preservation. |
| **BCNF** | Pass | No violations beyond 3NF issues. |

**JSON Blob Trade-off:** The extensive use of JSON columns (`results_json`, `summary_json`, `tools_json`, etc.) is a pragmatic design choice. LLM benchmark results are heterogeneous and schema-evolving. Normalizing them would require frequent schema changes. The cost is that **all analytics aggregation must happen in Python**, which becomes a bottleneck at scale.

### 2.3 Foreign Key Map — Issues Found

| Issue | Location | Severity |
|-------|----------|----------|
| `schedules` conflicting FK (CRIT-2) | db.py:295 vs 306 | Critical |
| `audit_log.user_id` no ON DELETE action | db.py:191 | Medium (manual NULL-out in admin.py:92 handles it, but fragile) |
| `rate_limits.updated_by` no ON DELETE action | db.py:183 | Low (dangling ref if admin deleted) |
| `judge_reports.eval_run_id_b` no FK | db.py:399 | Medium (comparative mode, dangling refs possible) |
| `judge_reports.parent_report_id` no FK | db.py:568 | Low (ALTER TABLE limitation, code handles traversal) |
| `experiments.baseline_eval_id` no FK | db.py:238 | Low |
| 5 polymorphic refs without FKs | Various | By design (origin_ref, result_ref, etc.) |

### 2.4 Missing Constraints

**CHECK constraints that should exist:**

| Column | Expected Values | Location |
|--------|----------------|----------|
| `tool_test_cases.param_scoring` | exact, fuzzy, contains, semantic | db.py:223 |
| `param_tune_runs.optimization_mode` | grid, random, bayesian | db.py:662 |
| `tool_test_cases.category` | simple, parallel, multi_turn, irrelevance | db.py:648 |
| `schedules.interval_hours` | > 0 | db.py:301 |
| Boolean columns (7 total) | 0 or 1 | Various |
| Score/percentage REAL columns | >= 0 | Various |

**Redundant indexes (4):**
- `idx_user_configs_user` — `user_id` already has UNIQUE constraint
- `idx_refresh_tokens_hash` — `token_hash` already has UNIQUE constraint
- `idx_pwreset_token` — `token_hash` already has UNIQUE constraint
- `idx_leaderboard_model` — covered by UNIQUE(model_name, provider)

### 2.5 Naming Inconsistencies

| Issue | Location |
|-------|----------|
| `schedules.created` instead of `created_at` | db.py:305 |
| Dual PK generation (DDL default never exercised, app uses `uuid.uuid4().hex`) | All tables |
| `param_tune_runs` defaults to `status='running'` while `jobs` defaults to `status='pending'` | db.py:350 vs 433 |

---

## 3. ACID Compliance

### 3.1 Atomicity — B-

**Strong points:**
- `execute_returning_row()` handles multi-statement operations in a single connection
- Profile operations (create/update/set_default) use single-connection multi-step writes
- `upsert_user_key()` uses single connection for read-then-write
- All `async with` connections auto-rollback on exception (uncommitted changes discarded)

**Gaps:**

| Issue | Location | Impact |
|-------|----------|--------|
| Suite import: create suite + N test cases across N+1 connections | tool_eval.py:626-661 | Partial data on crash |
| BFCL import: same pattern | tool_eval.py:926-963 | Partial data on crash |
| MCP import: same pattern | mcp.py:227-243 | Partial data on crash |
| Leaderboard upsert: read-then-write across 2 connections | db.py:1982-2023 | Data corruption (race) |
| Profile count check: TOCTOU across 2 connections | db.py:2144-2183 | Can exceed 20-profile limit |
| Job submit: slot check vs DB insert not atomic | job_registry.py:131-163 | Brief concurrency overshoot |

**Zero explicit transactions:** No `BEGIN TRANSACTION`, `ROLLBACK`, or `SAVEPOINT` anywhere in the codebase. All atomicity relies on auto-commit behavior and single-connection scoping.

### 3.2 Consistency — A-

**Strong points:**
- `PRAGMA foreign_keys=ON` set on all write paths
- 12 CHECK constraints on status/type/mode columns
- UNIQUE constraints on email, token_hash, composite keys
- Pydantic validation on all API inputs

**Gap:** `foreign_keys=ON` is not set on `get_db()` (used by admin routes). If admin routes write via `get_db()`, FK constraints are unenforced. Currently admin.py always sets it manually (line 87: `await conn.execute("PRAGMA foreign_keys = ON")`), so this is covered but fragile.

### 3.3 Isolation — B+

**WAL mode** is set at `init_db()` (line 117). Since it's a database-level setting, all connections inherit it. WAL allows concurrent readers with a single writer, which is appropriate for asyncio + aiosqlite (single-writer thread).

**busy_timeout** (5000ms) is set on write methods only. Read methods can fail immediately under write contention (CRIT-3).

**No explicit isolation level control.** SQLite defaults to DEFERRED transactions under WAL. This is adequate for the application's concurrency model (single-instance, asyncio event loop serializes requests).

### 3.4 Durability — B

**`PRAGMA synchronous` is never set.** SQLite's WAL-mode default is `NORMAL`, which is durable against application crashes but has a theoretical data-loss window during power failure (committed WAL data could be lost if not yet checkpointed). For a SaaS benchmark tool, this is acceptable. Setting `synchronous=FULL` would eliminate the window at ~30% write performance cost.

---

## 4. Query Performance

### 4.1 Indexing — B+

33 indexes cover the major query patterns. Timestamp-based queries use composite `(user_id, timestamp DESC)` indexes for efficient pagination.

**Missing indexes:**

| Column | Query Pattern | Impact |
|--------|--------------|--------|
| `audit_log.username` | Admin filter `WHERE username = ?` | Medium (90-day table can be large) |
| `tool_eval_runs.suite_id` | FK lookups, suite-based filtering | Low-Medium |
| `param_tune_runs.suite_id` | FK lookups | Low |
| `prompt_tune_runs.suite_id` | FK lookups | Low |

### 4.2 N+1 Patterns — C

| Pattern | Location | Scale | Impact |
|---------|----------|-------|--------|
| Suite import: individual `create_test_case()` in loop | tool_eval.py:629-661 | Hundreds of test cases | **HIGH** — separate connection per row |
| BFCL import: same | tool_eval.py:929-962 | Hundreds | **HIGH** |
| MCP import: same | mcp.py:234-243 | 5-20 tools | Medium |
| `_check_rate_limit()`: 3 sequential DB calls | helpers.py:217-243 | Every job submit | Medium |
| `analytics_compare()`: loop over 2-4 run_ids | analytics.py:183-184 | 2-4 iterations | Low |
| `get_experiment_timeline()`: 4 sequential queries | db.py:1579-1659 | Per experiment | Low (shared connection) |

### 4.3 Aggregation — D

**All analytics aggregation happens in Python, not SQL.** The `analytics_leaderboard` and `analytics_trends` endpoints:
1. Fetch ALL benchmark/eval runs for a user (including full JSON blobs)
2. Deserialize every JSON blob in Python
3. Aggregate with nested loops
4. Discard the raw data

For a user with 200 benchmark runs × 10 models each, this loads ~2,000 result objects from JSON every time the analytics page loads. No caching, no materialization, no SQL-level aggregation.

**Root cause:** JSON blob storage makes SQL aggregation impossible. A normalized `benchmark_model_results` table would enable `SELECT model, AVG(tps), AVG(ttft_ms) FROM ... GROUP BY model`.

### 4.4 Pagination — B

Most list endpoints use `LIMIT ? OFFSET ?` with sensible defaults (50 rows). Run history uses `(user_id, timestamp DESC)` composite indexes.

**Unbounded queries (no LIMIT):**
- `get_tool_suites()`, `get_test_cases()`, `get_user_schedules()`, `get_user_keys()`
- `get_analytics_benchmark_runs()`, `get_analytics_tool_eval_runs()` — **most concerning**, pulls full JSON blobs
- `get_leaderboard()`, `get_all_active_jobs()`, `get_profiles()`

### 4.5 Write Performance — C+

- Every write opens a new connection + 2 PRAGMA statements + commit + close
- No batch inserts (`executemany` never used)
- No connection pooling (acceptable for SQLite, but PRAGMAs are redundant overhead)
- Dynamic UPDATE builders are safe (hardcoded field names, parameterized values)

---

## 5. Robustness & Error Handling

### 5.1 Connection Lifecycle — A-

| Pattern | Safety | Coverage |
|---------|--------|----------|
| `DatabaseManager` methods: `async with aiosqlite.connect()` | Leak-proof | ~80+ CRUD functions |
| Direct `aiosqlite.connect()` in db.py | Leak-proof (async with) | 5 functions needing multi-step writes |
| `get_db()` returns raw connection | **Fragile** (caller must close) | 7 admin routes + 1 lifespan |

### 5.2 Error Recovery — B

- Job registry `_run()` wrapper handles CancelledError and Exception with proper cleanup
- Startup recovery marks orphaned running/pending/queued jobs as interrupted
- Stale run cleanup in lifespan for judge/param/prompt runs
- `log_audit()` is fire-and-forget (never raises)
- No explicit ROLLBACK anywhere — relies on auto-rollback of uncommitted changes on connection close

### 5.3 Table Growth — D

| Table | Growth | Cleanup |
|-------|--------|---------|
| `audit_log` | Every action | 90-day retention (startup only) |
| `refresh_tokens` | Every login | **NONE** (cleanup function exists but never called) |
| `benchmark_runs` | Every benchmark | **NONE** |
| `tool_eval_runs` | Every eval | **NONE** |
| `param_tune_runs` | Every tune | **NONE** |
| `prompt_tune_runs` | Every tune | **NONE** |
| `judge_reports` | Every judge | **NONE** |
| `jobs` | Every job | **NONE** |
| `prompt_versions` | Every save | **NONE** |

No `VACUUM`, no `integrity_check`, no periodic cleanup for any table except `audit_log`.

### 5.4 Backup & Recovery — F

- Single SQLite file, no backup mechanism
- No `sqlite3_backup()` API usage
- No scheduled VACUUM or integrity checks
- If `.fernet_key` is lost, all encrypted API keys are unrecoverable
- No documented recovery procedure

### 5.5 Test Coverage — B

Tests use a real SQLite database (not mocks), which is excellent for catching real SQL issues. 988 tests across 31 files.

**Tested:**
- Table creation idempotency
- User/token/config/key/suite/test-case/param-tune/job CRUD
- CASCADE behavior (suite → test_cases)
- Audit log fire-and-forget
- Stale run cleanup

**Not tested:**
- Schedules, judge_reports, experiments, model_profiles, prompt_versions, public_leaderboard CRUD
- Concurrent connection handling / busy_timeout behavior
- Full cascade chain on user deletion
- Schema migration paths
- `get_db()` connection leak scenarios

---

## 6. Security

### 6.1 SQL Injection — A (Clean)

Every query uses parameterized placeholders (`?`). Dynamic column names in UPDATE builders come from hardcoded allowlists, never from user input. Dynamic IN clauses use proper placeholder construction. No injection vectors found.

### 6.2 Multi-User Isolation — A-

All CRUD queries scoped to `user_id`. Internal write functions (job status updates, eval run updates) skip `user_id` in WHERE but callers validate ownership first. Admin endpoints require `require_admin` dependency.

One defense-in-depth gap: `update_judge_report()`, `update_tool_eval_run()`, and job status functions should ideally include `AND user_id = ?` even though callers validate.

### 6.3 Encryption — B+

API keys encrypted with Fernet (symmetric). Refresh tokens hashed with SHA-256 before storage. Passwords hashed with bcrypt. Single-point-of-failure: `.fernet_key` file loss = all API keys unrecoverable.

---

## 7. Recommendations

### Priority 1 — Fix Now (Data Integrity)

| # | Action | Effort |
|---|--------|--------|
| 1 | Fix `schedules` conflicting FK — remove table-level FK at line 306 or add CASCADE | 5 min |
| 2 | Add `busy_timeout=5000` to `fetch_one`, `fetch_all`, `execute_returning_scalar`, `get_db()` | 10 min |
| 3 | Call `cleanup_expired_tokens()` in app.py lifespan | 2 min |
| 4 | Fix leaderboard upsert race — use `INSERT ON CONFLICT DO UPDATE` or single-connection | 30 min |
| 5 | Wrap suite/BFCL/MCP imports in a single connection with batch insert | 1 hr |

### Priority 2 — Improve Soon (Performance & Robustness)

| # | Action | Effort |
|---|--------|--------|
| 6 | Add retention/cleanup for `jobs`, `benchmark_runs`, and run tables (e.g., 180-day rotation) | 2 hr |
| 7 | Add missing CHECK constraints (param_scoring, optimization_mode, category, interval_hours) | 30 min |
| 8 | Combine `_check_rate_limit()` into a single query | 30 min |
| 9 | Add index on `audit_log.username` | 2 min |
| 10 | Remove 4 redundant indexes | 5 min |

### Priority 3 — Strategic (Scale Enablers)

| # | Action | Effort |
|---|--------|--------|
| 11 | Create normalized `benchmark_model_results` table for SQL-level analytics aggregation | 1 day |
| 12 | Add SQLite backup mechanism (daily `sqlite3_backup()` or file copy with WAL checkpoint) | 4 hr |
| 13 | Add `VACUUM` to periodic maintenance | 30 min |
| 14 | Consider migration versioning system (replace try/except ALTER with version tracking) | 1 day |
| 15 | Add defense-in-depth `user_id` checks to internal write functions | 2 hr |

---

## Appendix A: Complete Table Schema

<details>
<summary>Click to expand full 20-table schema with all columns and constraints</summary>

### users (db.py:121-129)
- id TEXT PK (hex UUID), email TEXT UNIQUE NOCASE, password_hash TEXT NOT NULL
- role TEXT CHECK(admin/user), onboarding_completed INT, google_id TEXT, avatar_url TEXT, leaderboard_opt_in INT

### refresh_tokens (db.py:132-138)
- id TEXT PK, user_id FK→users CASCADE, token_hash TEXT UNIQUE, expires_at TEXT

### user_api_keys (db.py:142-151)
- id TEXT PK, user_id FK→users CASCADE, provider_key TEXT, key_name TEXT, encrypted_value TEXT
- UNIQUE(user_id, provider_key)

### user_configs (db.py:155-160)
- id TEXT PK, user_id FK→users CASCADE UNIQUE, config_yaml TEXT

### benchmark_runs (db.py:164-173)
- id TEXT PK, user_id FK→users CASCADE, timestamp TEXT, prompt TEXT, context_tiers TEXT
- results_json TEXT NOT NULL, metadata TEXT, max_tokens INT, temperature REAL, warmup INT, config_json TEXT

### rate_limits (db.py:176-185)
- id INT PK AUTOINCREMENT, user_id FK→users CASCADE UNIQUE
- benchmarks_per_hour INT(20), max_concurrent INT(1), max_runs_per_benchmark INT(10)
- updated_by FK→users (NO CASCADE — bug)

### audit_log (db.py:188-200)
- id INT PK AUTOINCREMENT, timestamp TEXT, user_id FK→users (NO CASCADE — intentional)
- username TEXT, action TEXT, resource_type TEXT, resource_id TEXT, detail TEXT, ip_address TEXT, user_agent TEXT

### tool_suites (db.py:205-213)
- id TEXT PK, user_id FK→users CASCADE, name TEXT, description TEXT(''), tools_json TEXT, system_prompt TEXT

### tool_test_cases (db.py:217-226)
- id TEXT PK, suite_id FK→tool_suites CASCADE, prompt TEXT, expected_tool TEXT, expected_params TEXT
- param_scoring TEXT('exact'), multi_turn_config TEXT, scoring_config_json TEXT
- should_call_tool INT(1), category TEXT

### experiments (db.py:231-250)
- id TEXT PK, user_id FK→users CASCADE, suite_id FK→tool_suites CASCADE
- name TEXT, description TEXT(''), baseline_eval_id TEXT (no FK), baseline_score REAL
- best_config_json TEXT, best_score REAL(0), best_source TEXT, best_source_id TEXT (no FK)
- status TEXT CHECK(active/archived), suite_snapshot_json TEXT

### tool_eval_runs (db.py:256-271)
- id TEXT PK, user_id FK→users CASCADE, suite_id FK→tool_suites CASCADE
- suite_name TEXT (denormalized), models_json TEXT, results_json TEXT, summary_json TEXT
- temperature REAL(0), config_json TEXT, experiment_id FK→experiments SET NULL
- profiles_json TEXT, judge_explanations_json TEXT

### schedules (db.py:293-308)
- id TEXT PK, user_id FK→users (CONFLICTING: line 295 CASCADE, line 306 NO CASCADE)
- name TEXT, prompt TEXT, models_json TEXT, max_tokens INT(512), temperature REAL(0.7)
- interval_hours INT, enabled INT(1), last_run TEXT, next_run TEXT, created TEXT

### param_tune_runs (db.py:338-357)
- id TEXT PK, user_id FK→users CASCADE, suite_id FK→tool_suites CASCADE
- suite_name TEXT (denormalized), models_json TEXT, search_space_json TEXT
- results_json TEXT('[]'), best_config_json TEXT, best_score REAL(0)
- total_combos INT, completed_combos INT(0), status TEXT CHECK(5 values)
- duration_s REAL, experiment_id FK→experiments SET NULL
- judge_scores_json TEXT, optimization_mode TEXT('grid')

### prompt_tune_runs (db.py:365-387)
- id TEXT PK, user_id FK→users CASCADE, suite_id FK→tool_suites CASCADE
- suite_name TEXT (denormalized), mode TEXT CHECK(quick/evolutionary)
- target_models_json TEXT, meta_model TEXT, base_prompt TEXT, config_json TEXT
- generations_json TEXT('[]'), best_prompt TEXT, best_score REAL(0)
- status TEXT CHECK(5 values), total_prompts INT(0), completed_prompts INT(0)
- duration_s REAL, experiment_id FK→experiments SET NULL
- meta_provider_key TEXT, best_prompt_origin_json TEXT

### judge_reports (db.py:395-411)
- id TEXT PK, user_id FK→users CASCADE, eval_run_id FK→tool_eval_runs SET NULL
- eval_run_id_b TEXT (no FK), judge_model TEXT, mode TEXT CHECK(3 values)
- verdicts_json TEXT('[]'), report_json TEXT, overall_grade TEXT, overall_score REAL
- status TEXT CHECK(running/completed/error — missing 'interrupted')
- experiment_id FK→experiments SET NULL
- parent_report_id TEXT (no FK), version INT(1), instructions_json TEXT

### jobs (db.py:421-461)
- id TEXT PK, user_id FK→users CASCADE
- job_type TEXT CHECK(8 values), status TEXT CHECK(7 values)
- progress_pct INT CHECK(0-100), progress_detail TEXT(''), params_json TEXT('{}')
- result_ref TEXT, result_type TEXT, error_msg TEXT
- timeout_at TEXT, timeout_seconds INT(7200)

### model_profiles (db.py:478-494)
- id TEXT PK, user_id FK→users CASCADE, model_id TEXT, name TEXT('Default')
- description TEXT(''), is_default INT(0), params_json TEXT('{}'), system_prompt TEXT
- origin_type TEXT CHECK(4 values), origin_ref TEXT
- UNIQUE(user_id, model_id, name)

### password_reset_tokens (db.py:583-591)
- id TEXT PK, user_id FK→users CASCADE, token_hash TEXT UNIQUE
- expires_at TEXT, used INT(0)

### prompt_versions (db.py:615-626)
- id TEXT PK, user_id FK→users CASCADE, prompt_text TEXT, label TEXT('')
- source TEXT CHECK(manual/prompt_tuner/auto_optimize)
- parent_version_id FK→prompt_versions SET NULL, origin_run_id TEXT (no FK)

### public_leaderboard (db.py:669-682)
- id TEXT PK, model_name TEXT, provider TEXT
- tool_accuracy_pct REAL(0), param_accuracy_pct REAL(0), irrel_accuracy_pct REAL
- throughput_tps REAL, ttft_ms REAL, sample_count INT(0), last_updated TEXT
- UNIQUE(model_name, provider)

</details>

---

## Appendix B: All Findings by Severity

### Critical (5)
| ID | Finding | File:Line |
|----|---------|-----------|
| CRIT-1 | Leaderboard upsert race condition (lost update) | db.py:1982-2023 |
| CRIT-2 | Schedules conflicting FK blocks user deletion | db.py:295-306 |
| CRIT-3 | Missing busy_timeout on read operations | db.py:30-44, 89-94, 101-108 |
| CRIT-4 | Refresh token cleanup never called | db.py:826-828 |
| CRIT-5 | Suite/BFCL/MCP imports not transactional | tool_eval.py:626-661, 926-963 |

### Medium (11)
| ID | Finding | File:Line |
|----|---------|-----------|
| MED-1 | Profile count check TOCTOU race | db.py:2144-2183 |
| MED-2 | No cleanup for 7 unbounded tables | Various |
| MED-3 | Job submit slot check not atomic with DB insert | job_registry.py:131-163 |
| MED-4 | audit_log FK has no ON DELETE — relies on manual NULL-out | db.py:191, admin.py:92 |
| MED-5 | rate_limits.updated_by FK has no ON DELETE | db.py:183 |
| MED-6 | judge_reports.eval_run_id_b has no FK | db.py:399 |
| MED-7 | N+1 test case creation in imports (hundreds of connections) | tool_eval.py:629-661 |
| MED-8 | All analytics aggregation in Python, not SQL | analytics.py:31-167 |
| MED-9 | 8 missing CHECK constraints | Various |
| MED-10 | No backup/recovery mechanism | N/A |
| MED-11 | judge_reports.status CHECK missing 'interrupted' | db.py:406 |

### Low (12)
| ID | Finding | File:Line |
|----|---------|-----------|
| LOW-1 | 4 redundant indexes | db.py:275, 277, 592, 683 |
| LOW-2 | No migration versioning (22 ALTERs re-run every startup) | db.py:316-691 |
| LOW-3 | `schedules.created` naming inconsistency | db.py:305 |
| LOW-4 | Dual PK generation (DDL default never used) | All tables |
| LOW-5 | Status default divergence (running vs pending) | db.py:350, 433 |
| LOW-6 | get_db() pattern relies on caller to close | db.py:101-108 |
| LOW-7 | Google OAuth stores empty string as password_hash | db.py:753 |
| LOW-8 | Missing UNIQUE on suite/experiment/schedule names per user | Various |
| LOW-9 | No PRAGMA synchronous=FULL (WAL default NORMAL) | N/A |
| LOW-10 | Per-connection PRAGMA overhead on every write | db.py:48-53 |
| LOW-11 | Internal writes skip user_id in WHERE (callers validate) | db.py:1418, 1190 |
| LOW-12 | Test coverage gaps (5 tables untested for CRUD) | tests/ |

---

*Report generated by 4-agent database evaluation team. Each agent specialized in one dimension: Schema & Normalization, ACID Compliance, Query Performance, and Robustness.*

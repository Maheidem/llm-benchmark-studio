"""Database layer for LLM Benchmark Studio multi-user support.

Uses aiosqlite for async SQLite with WAL mode.
All tables are created on first startup via init_db().
"""

import json
import logging
import re
import secrets
import aiosqlite
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "benchmark_studio.db"


class DatabaseManager:
    """Centralized database connection management.

    Eliminates repeated connect/row_factory/commit patterns across ~80 functions.
    Uses the module-level DB_PATH so that monkeypatching DB_PATH in tests
    automatically applies to all queries.
    """

    def _path(self) -> str:
        return str(DB_PATH)

    async def fetch_one(self, query: str, params: tuple = ()) -> dict | None:
        """Execute query and return one row as dict, or None."""
        async with aiosqlite.connect(self._path()) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute query and return all rows as list of dicts."""
        async with aiosqlite.connect(self._path()) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def execute(self, query: str, params: tuple = ()) -> None:
        """Execute a write query (INSERT/UPDATE/DELETE) with auto-commit."""
        async with aiosqlite.connect(self._path()) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(query, params)
            await conn.commit()

    async def execute_returning_id(self, query: str, params: tuple = (), *, id_query: str, id_params: tuple = ()) -> str:
        """Execute INSERT, then fetch a generated ID with a follow-up SELECT."""
        async with aiosqlite.connect(self._path()) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA foreign_keys=ON")
            cursor = await conn.execute(query, params)
            await conn.commit()
            row = await (await conn.execute(id_query, id_params or (cursor.lastrowid,))).fetchone()
            return row[0]

    async def execute_returning_row(self, queries: list[tuple[str, tuple]], fetch_query: str, fetch_params: tuple) -> dict | None:
        """Execute write queries then fetch a row in the same connection."""
        async with aiosqlite.connect(self._path()) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA foreign_keys=ON")
            for query, params in queries:
                await conn.execute(query, params)
            await conn.commit()
            cursor = await conn.execute(fetch_query, fetch_params)
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def execute_returning_rowcount(self, query: str, params: tuple = ()) -> int:
        """Execute a write query and return cursor.rowcount."""
        async with aiosqlite.connect(self._path()) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA foreign_keys=ON")
            cursor = await conn.execute(query, params)
            await conn.commit()
            return cursor.rowcount

    async def execute_returning_scalar(self, query: str, params: tuple = ()):
        """Execute query and return the first column of the first row."""
        async with aiosqlite.connect(self._path()) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            return row[0] if row else None


# Module-level singleton
_db = DatabaseManager()


async def get_db() -> aiosqlite.Connection:
    """Get a database connection. Caller must close or use as context manager."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    """Create all tables if they don't exist. Called once at app startup."""
    logger.info("Initializing database at %s", DB_PATH)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")

        # Schema versioning (must be first -- migration helpers depend on it)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.commit()

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                email TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','user')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # --- Normalized providers + models (Phase 1) ---
        await db.execute("""
            CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                name TEXT NOT NULL,
                api_base TEXT,
                api_key_env TEXT,
                model_prefix TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, key)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_providers_user ON providers(user_id)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                litellm_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                context_window INTEGER NOT NULL DEFAULT 128000,
                max_output_tokens INTEGER,
                skip_params TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(provider_id, litellm_id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_models_provider ON models(provider_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_models_litellm ON models(litellm_id)")
        await db.commit()

        await db.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider_key TEXT NOT NULL,
                key_name TEXT NOT NULL,
                encrypted_value TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, provider_key)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_configs (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                config_yaml TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                prompt TEXT,
                context_tiers TEXT,
                results_json TEXT NOT NULL,
                metadata TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                benchmarks_per_hour INTEGER NOT NULL DEFAULT 20,
                max_concurrent INTEGER NOT NULL DEFAULT 1,
                max_runs_per_benchmark INTEGER NOT NULL DEFAULT 10,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_by TEXT REFERENCES users(id) ON DELETE SET NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                detail TEXT,
                ip_address TEXT,
                user_agent TEXT
            )
        """)

        # --- Tool Eval tables ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tool_suites (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                tools_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tool_test_cases (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                suite_id TEXT NOT NULL REFERENCES tool_suites(id) ON DELETE CASCADE,
                prompt TEXT NOT NULL,
                expected_tool TEXT,
                expected_params TEXT,
                param_scoring TEXT NOT NULL DEFAULT 'exact'
                    CHECK(param_scoring IN ('exact','fuzzy','contains','semantic')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # --- Experiments (M2) --- (created before eval/tune/judge tables that reference it)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                suite_id TEXT NOT NULL,
                suite_snapshot_json TEXT,
                baseline_eval_id TEXT,
                baseline_score REAL,
                best_config_json TEXT,
                best_score REAL DEFAULT 0.0,
                best_source TEXT,
                best_source_id TEXT,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'archived')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_experiments_user ON experiments(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_experiments_suite ON experiments(suite_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(user_id, status)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tool_eval_runs (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                suite_id TEXT NOT NULL,
                models_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                temperature REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                config_json TEXT,
                experiment_id TEXT,
                FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
            )
        """)

        # Indexes for common queries
        await db.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id)")
        # idx_refresh_tokens_hash removed: duplicates UNIQUE on token_hash
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_api_keys_user ON user_api_keys(user_id)")
        # idx_user_configs_user removed: duplicates UNIQUE on user_id
        await db.execute("CREATE INDEX IF NOT EXISTS idx_benchmark_runs_user ON benchmark_runs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_benchmark_runs_ts ON benchmark_runs(user_id, timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_log(username)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_suites_user ON tool_suites(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_test_cases_suite ON tool_test_cases(suite_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_user ON tool_eval_runs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_ts ON tool_eval_runs(user_id, timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_suite ON tool_eval_runs(suite_id)")

        await db.commit()

        # --- Scheduled Benchmarks ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                models_json TEXT NOT NULL,
                max_tokens INTEGER DEFAULT 512,
                temperature REAL DEFAULT 0.7,
                interval_hours INTEGER NOT NULL CHECK(interval_hours > 0),
                enabled INTEGER DEFAULT 1,
                last_run TEXT,
                next_run TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_schedules_next ON schedules(enabled, next_run)")

        await db.commit()

        # Phase 8: onboarding flag
        try:
            await db.execute("ALTER TABLE users ADD COLUMN onboarding_completed INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            logger.debug("Column onboarding_completed already exists")

        # Multi-turn tool eval support
        try:
            await db.execute("ALTER TABLE tool_test_cases ADD COLUMN multi_turn_config TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column multi_turn_config already exists")

        # S3: scoring_config_json on tool_test_cases (fuzzy scoring modes)
        try:
            await db.execute("ALTER TABLE tool_test_cases ADD COLUMN scoring_config_json TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column tool_test_cases.scoring_config_json already exists")

        # --- Parameter Tuner ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS param_tune_runs (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                suite_id TEXT NOT NULL,
                models_json TEXT NOT NULL,
                search_space_json TEXT NOT NULL,
                results_json TEXT NOT NULL DEFAULT '[]',
                best_config_json TEXT,
                best_score REAL DEFAULT 0.0,
                total_combos INTEGER NOT NULL,
                completed_combos INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','cancelled','error','interrupted')),
                duration_s REAL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                experiment_id TEXT,
                judge_scores_json TEXT,
                optimization_mode TEXT NOT NULL DEFAULT 'grid'
                    CHECK(optimization_mode IN ('grid','random','bayesian')),
                FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_user ON param_tune_runs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_ts ON param_tune_runs(user_id, timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_suite ON param_tune_runs(suite_id)")
        await db.commit()

        # --- Prompt Tuner ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS prompt_tune_runs (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                suite_id TEXT NOT NULL,
                mode TEXT NOT NULL CHECK(mode IN ('quick','evolutionary')),
                target_models_json TEXT NOT NULL,
                base_prompt TEXT,
                config_json TEXT NOT NULL,
                generations_json TEXT NOT NULL DEFAULT '[]',
                best_score REAL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','cancelled','error','interrupted')),
                total_prompts INTEGER DEFAULT 0,
                completed_prompts INTEGER DEFAULT 0,
                duration_s REAL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                experiment_id TEXT,
                best_prompt_origin_json TEXT,
                meta_model_id TEXT REFERENCES models(id) ON DELETE SET NULL,
                best_prompt_version_id TEXT REFERENCES prompt_versions(id) ON DELETE SET NULL,
                best_profile_id TEXT REFERENCES model_profiles(id) ON DELETE SET NULL,
                FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_user ON prompt_tune_runs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_ts ON prompt_tune_runs(user_id, timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_suite ON prompt_tune_runs(suite_id)")
        await db.commit()

        # --- LLM Judge ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS judge_reports (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                eval_run_id TEXT,
                eval_run_id_b TEXT,
                judge_model_id TEXT REFERENCES models(id) ON DELETE SET NULL,
                mode TEXT NOT NULL CHECK(mode IN ('post_eval','live_inline','comparative')),
                verdicts_json TEXT NOT NULL DEFAULT '[]',
                report_json TEXT,
                overall_grade TEXT,
                overall_score REAL,
                status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','error','interrupted')),
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                experiment_id TEXT,
                parent_report_id TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                instructions_json TEXT,
                FOREIGN KEY (eval_run_id) REFERENCES tool_eval_runs(id) ON DELETE SET NULL,
                FOREIGN KEY (eval_run_id_b) REFERENCES tool_eval_runs(id) ON DELETE SET NULL,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_user ON judge_reports(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_eval ON judge_reports(eval_run_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_ts ON judge_reports(user_id, timestamp DESC)")
        await db.commit()

        # --- Jobs (Process Tracker) ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

                -- Type discriminator (one of 8 process types)
                job_type TEXT NOT NULL CHECK(job_type IN (
                    'benchmark', 'tool_eval', 'judge', 'judge_compare',
                    'param_tune', 'prompt_tune', 'scheduled_benchmark',
                    'prompt_auto_optimize'
                )),

                -- Lifecycle
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
                    'pending', 'queued', 'running',
                    'done', 'failed', 'cancelled', 'interrupted'
                )),

                -- Progress tracking
                progress_pct INTEGER DEFAULT 0 CHECK(progress_pct BETWEEN 0 AND 100),
                progress_detail TEXT DEFAULT '',

                -- Input parameters (type-specific, stored as JSON)
                params_json TEXT NOT NULL DEFAULT '{}',

                -- Result reference (points to result in type-specific tables)
                result_ref TEXT,
                result_type TEXT,

                -- Error info
                error_msg TEXT,

                -- Timing
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                started_at TEXT,
                completed_at TEXT,
                timeout_at TEXT,

                -- Timeout config (seconds, default 7200 = 2 hours)
                timeout_seconds INTEGER NOT NULL DEFAULT 7200
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs(user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_timeout ON jobs(status, timeout_at)")
        await db.commit()

        # --- Normalized run_results (Phase 4) ---
        await db.execute("""
            CREATE TABLE IF NOT EXISTS run_results (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                run_id TEXT NOT NULL,
                run_type TEXT NOT NULL CHECK(run_type IN ('benchmark','tool_eval','param_tune','prompt_tune','judge')),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                model_litellm_id TEXT NOT NULL,
                model_db_id TEXT REFERENCES models(id) ON DELETE SET NULL,
                profile_id TEXT REFERENCES model_profiles(id) ON DELETE SET NULL,
                provider_key TEXT,
                tool_accuracy_pct REAL,
                param_accuracy_pct REAL,
                throughput_tps REAL,
                ttft_ms REAL,
                tokens_generated INTEGER,
                latency_p99_ms REAL,
                score REAL,
                cost REAL,
                raw_response_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_run_results_run ON run_results(run_id, run_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_run_results_user ON run_results(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_run_results_model ON run_results(model_litellm_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_run_results_model_db ON run_results(model_db_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_run_results_ts ON run_results(user_id, created_at DESC)")
        await db.commit()

        # experiment_id indexes (columns now in CREATE TABLE above)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_experiment ON tool_eval_runs(experiment_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_experiment ON param_tune_runs(experiment_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_experiment ON prompt_tune_runs(experiment_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_experiment ON judge_reports(experiment_id)")
        await db.commit()

        # --- Model Profiles ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS model_profiles (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT 'Default',
                description TEXT DEFAULT '',
                is_default INTEGER NOT NULL DEFAULT 0,
                params_json TEXT NOT NULL DEFAULT '{}',
                system_prompt TEXT,
                prompt_version_id TEXT REFERENCES prompt_versions(id) ON DELETE SET NULL,
                origin_type TEXT NOT NULL DEFAULT 'manual'
                    CHECK(origin_type IN ('manual', 'param_tuner', 'prompt_tuner', 'import')),
                origin_ref TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, model_id, name)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_model_profiles_user ON model_profiles(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_model_profiles_model ON model_profiles(user_id, model_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_model_profiles_default ON model_profiles(user_id, model_id, is_default)")
        await db.commit()

        # M6: system_prompt on tool_suites
        try:
            await db.execute("ALTER TABLE tool_suites ADD COLUMN system_prompt TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column tool_suites.system_prompt already exists")

        # B3: profiles_json on tool_eval_runs (stores which profiles were used)
        try:
            await db.execute("ALTER TABLE tool_eval_runs ADD COLUMN profiles_json TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column tool_eval_runs.profiles_json already exists")

        # Re-run columns for benchmark_runs + meta_provider_key for prompt_tune_runs
        for col_sql in [
            "ALTER TABLE benchmark_runs ADD COLUMN max_tokens INTEGER",
            "ALTER TABLE benchmark_runs ADD COLUMN temperature REAL",
            "ALTER TABLE benchmark_runs ADD COLUMN warmup INTEGER",
            "ALTER TABLE benchmark_runs ADD COLUMN config_json TEXT",
            "ALTER TABLE prompt_tune_runs ADD COLUMN meta_provider_key TEXT",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                pass

        # --- Migration: add 'interrupted' status to param_tune_runs / prompt_tune_runs ---
        # SQLite CHECK constraints can't be altered, so we recreate the tables.
        # Only runs if the existing schema still uses the old 4-value CHECK.
        for table_name in ("param_tune_runs", "prompt_tune_runs"):
            try:
                cursor = await db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                )
                row = await cursor.fetchone()
                if row:
                    ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
                    if "'interrupted'" not in ddl:
                        logger.info("Migrating %s: adding 'interrupted' to status CHECK", table_name)
                        new_ddl = ddl.replace(
                            "('running','completed','cancelled','error')",
                            "('running','completed','cancelled','error','interrupted')",
                        )
                        # Standard SQLite table-rebuild migration
                        await db.execute(f"ALTER TABLE {table_name} RENAME TO _{table_name}_old")
                        await db.execute(new_ddl)
                        cols = [c.strip().split()[0] for c in ddl.split("(", 1)[1].rsplit(")", 1)[0].split(",")
                                if c.strip() and not c.strip().upper().startswith(("PRIMARY", "FOREIGN", "CHECK", "UNIQUE"))]
                        # Simpler: just copy all data
                        await db.execute(f"INSERT INTO {table_name} SELECT * FROM _{table_name}_old")
                        await db.execute(f"DROP TABLE _{table_name}_old")
                        await db.commit()
                        logger.info("Migration complete for %s", table_name)
            except Exception:
                logger.exception("Failed to migrate %s CHECK constraint", table_name)

        # Migration: add best_prompt_origin_json to prompt_tune_runs
        try:
            await db.execute("ALTER TABLE prompt_tune_runs ADD COLUMN best_prompt_origin_json TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column prompt_tune_runs.best_prompt_origin_json already exists")

        # --- Judge versioning columns ---
        for col_sql in [
            "ALTER TABLE judge_reports ADD COLUMN parent_report_id TEXT",
            "ALTER TABLE judge_reports ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE judge_reports ADD COLUMN instructions_json TEXT",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_parent ON judge_reports(parent_report_id)")
        await db.commit()

        # --- Password Reset Tokens ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # idx_pwreset_token removed: duplicates UNIQUE on token_hash
        await db.commit()

        # --- Google OAuth columns on users ---
        for col_sql in [
            "ALTER TABLE users ADD COLUMN google_id TEXT",
            "ALTER TABLE users ADD COLUMN avatar_url TEXT",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                logger.debug("Column already exists: %s", col_sql)

        try:
            await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL")
            await db.commit()
        except Exception:
            logger.debug("Index idx_users_google_id already exists")

        # --- Prompt Version Registry ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS prompt_versions (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                prompt_text TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual'
                    CHECK(source IN ('manual', 'prompt_tuner', 'auto_optimize')),
                parent_version_id TEXT REFERENCES prompt_versions(id) ON DELETE SET NULL,
                origin_run_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_versions_user ON prompt_versions(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_versions_ts ON prompt_versions(user_id, created_at DESC)")
        await db.commit()

        # --- Irrelevance detection: should_call_tool on tool_test_cases ---
        try:
            await db.execute("ALTER TABLE tool_test_cases ADD COLUMN should_call_tool INTEGER NOT NULL DEFAULT 1")
            await db.commit()
        except Exception:
            logger.debug("Column tool_test_cases.should_call_tool already exists")

        # --- Judge wiring: judge_explanation on tool_eval_runs ---
        # Stored as JSON mapping test_case_id -> explanation text
        try:
            await db.execute("ALTER TABLE tool_eval_runs ADD COLUMN judge_explanations_json TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column tool_eval_runs.judge_explanations_json already exists")

        # T3: category tag on tool_test_cases (simple/parallel/multi-turn/irrelevance)
        try:
            await db.execute("ALTER TABLE tool_test_cases ADD COLUMN category TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column tool_test_cases.category already exists")

        # 2B: judge_scores_json on param_tune_runs (for param+quality correlation)
        try:
            await db.execute("ALTER TABLE param_tune_runs ADD COLUMN judge_scores_json TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column param_tune_runs.judge_scores_json already exists")

        # 2A: optimization_mode on param_tune_runs (grid/random/bayesian)
        try:
            await db.execute("ALTER TABLE param_tune_runs ADD COLUMN optimization_mode TEXT NOT NULL DEFAULT 'grid'")
            await db.commit()
        except Exception:
            logger.debug("Column param_tune_runs.optimization_mode already exists")

        # 2D: Public leaderboard table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS public_leaderboard (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                model_db_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
                tool_accuracy_pct REAL DEFAULT 0.0,
                param_accuracy_pct REAL DEFAULT 0.0,
                irrel_accuracy_pct REAL,
                throughput_tps REAL,
                ttft_ms REAL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                last_updated TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(model_db_id)
            )
        """)
        await db.commit()

        # 2D: leaderboard_opt_in on users
        try:
            await db.execute("ALTER TABLE users ADD COLUMN leaderboard_opt_in INTEGER NOT NULL DEFAULT 0")
            await db.commit()
        except Exception:
            logger.debug("Column users.leaderboard_opt_in already exists")

        # --- Drop redundant indexes on existing databases ---
        await db.execute("DROP INDEX IF EXISTS idx_user_configs_user")
        await db.execute("DROP INDEX IF EXISTS idx_refresh_tokens_hash")
        await db.execute("DROP INDEX IF EXISTS idx_pwreset_token")
        await db.execute("DROP INDEX IF EXISTS idx_leaderboard_model")
        await db.commit()

        # ======================================================================
        # Table rebuild migrations (Group C)
        # Pattern: check DDL -> RENAME -> CREATE new -> INSERT SELECT -> DROP old
        # ======================================================================

        # --- C1: Fix schedules conflicting FK (CRIT-2) + C5 interval_hours CHECK ---
        try:
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='schedules'"
            )
            row = await cursor.fetchone()
            if row:
                ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
                user_fk_refs = re.findall(r'REFERENCES\s+users\s*\(\s*id\s*\)', ddl, re.IGNORECASE)
                has_interval_check = 'interval_hours > 0' in ddl
                if len(user_fk_refs) > 1 or not has_interval_check:
                    old_count_cursor = await db.execute("SELECT COUNT(*) FROM schedules")
                    old_count = (await old_count_cursor.fetchone())[0]

                    logger.info("Migrating schedules table: fixing conflicting FK + adding CHECK constraints")
                    await db.execute("ALTER TABLE schedules RENAME TO _schedules_old")
                    await db.execute("""
                        CREATE TABLE schedules (
                            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            name TEXT NOT NULL,
                            prompt TEXT NOT NULL,
                            models_json TEXT NOT NULL,
                            max_tokens INTEGER DEFAULT 512,
                            temperature REAL DEFAULT 0.7,
                            interval_hours INTEGER NOT NULL CHECK(interval_hours > 0),
                            enabled INTEGER DEFAULT 1,
                            last_run TEXT,
                            next_run TEXT NOT NULL,
                            created TEXT NOT NULL DEFAULT (datetime('now'))
                        )
                    """)
                    await db.execute("INSERT INTO schedules SELECT * FROM _schedules_old")

                    new_count_cursor = await db.execute("SELECT COUNT(*) FROM schedules")
                    new_count = (await new_count_cursor.fetchone())[0]
                    assert old_count == new_count, f"Row count mismatch: {old_count} vs {new_count}"

                    await db.execute("DROP TABLE _schedules_old")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules(user_id)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_schedules_next ON schedules(enabled, next_run)")
                    await db.commit()
                    logger.info("Migrated schedules table: fixed conflicting FK")
        except Exception:
            logger.exception("Failed to migrate schedules table")

        # --- C2: Fix judge_reports.status CHECK (MED-11) ---
        try:
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='judge_reports'"
            )
            row = await cursor.fetchone()
            if row:
                ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
                if "'interrupted'" not in ddl or "eval_run_id_b) REFERENCES" not in ddl:
                    old_count_cursor = await db.execute("SELECT COUNT(*) FROM judge_reports")
                    old_count = (await old_count_cursor.fetchone())[0]

                    logger.info("Migrating judge_reports: adding interrupted status + eval_run_id_b FK")
                    await db.execute("ALTER TABLE judge_reports RENAME TO _judge_reports_old")
                    await db.execute("""
                        CREATE TABLE judge_reports (
                            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            eval_run_id TEXT,
                            eval_run_id_b TEXT,
                            judge_model TEXT NOT NULL,
                            mode TEXT NOT NULL CHECK(mode IN ('post_eval','live_inline','comparative')),
                            verdicts_json TEXT NOT NULL DEFAULT '[]',
                            report_json TEXT,
                            overall_grade TEXT,
                            overall_score REAL,
                            status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','error','interrupted')),
                            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                            experiment_id TEXT,
                            parent_report_id TEXT,
                            version INTEGER NOT NULL DEFAULT 1,
                            instructions_json TEXT,
                            FOREIGN KEY (eval_run_id) REFERENCES tool_eval_runs(id) ON DELETE SET NULL,
                            FOREIGN KEY (eval_run_id_b) REFERENCES tool_eval_runs(id) ON DELETE SET NULL,
                            FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
                        )
                    """)
                    await db.execute("""
                        INSERT INTO judge_reports
                        SELECT id, user_id, eval_run_id, eval_run_id_b, judge_model, mode,
                               verdicts_json, report_json, overall_grade, overall_score,
                               status, timestamp, experiment_id,
                               parent_report_id, version, instructions_json
                        FROM _judge_reports_old
                    """)

                    new_count_cursor = await db.execute("SELECT COUNT(*) FROM judge_reports")
                    new_count = (await new_count_cursor.fetchone())[0]
                    assert old_count == new_count, f"Row count mismatch: {old_count} vs {new_count}"

                    await db.execute("DROP TABLE _judge_reports_old")
                    # Recreate all 5 indexes for judge_reports
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_user ON judge_reports(user_id)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_eval ON judge_reports(eval_run_id)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_ts ON judge_reports(user_id, timestamp DESC)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_experiment ON judge_reports(experiment_id)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_parent ON judge_reports(parent_report_id)")
                    await db.commit()
                    logger.info("Migrated judge_reports: added interrupted status")
        except Exception:
            logger.exception("Failed to migrate judge_reports CHECK constraint")

        # --- C3: Fix audit_log FK (MED-4) ---
        try:
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='audit_log'"
            )
            row = await cursor.fetchone()
            if row:
                ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
                if 'SET NULL' not in ddl:
                    old_count_cursor = await db.execute("SELECT COUNT(*) FROM audit_log")
                    old_count = (await old_count_cursor.fetchone())[0]

                    logger.info("Migrating audit_log: adding ON DELETE SET NULL")
                    await db.execute("ALTER TABLE audit_log RENAME TO _audit_log_old")
                    await db.execute("""
                        CREATE TABLE audit_log (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                            user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                            username TEXT NOT NULL,
                            action TEXT NOT NULL,
                            resource_type TEXT,
                            resource_id TEXT,
                            detail TEXT,
                            ip_address TEXT,
                            user_agent TEXT
                        )
                    """)
                    await db.execute("INSERT INTO audit_log SELECT * FROM _audit_log_old")

                    new_count_cursor = await db.execute("SELECT COUNT(*) FROM audit_log")
                    new_count = (await new_count_cursor.fetchone())[0]
                    assert old_count == new_count, f"Row count mismatch: {old_count} vs {new_count}"

                    await db.execute("DROP TABLE _audit_log_old")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_log(username)")
                    await db.commit()
                    logger.info("Migrated audit_log: added ON DELETE SET NULL")
        except Exception:
            logger.exception("Failed to migrate audit_log FK")

        # --- C4: Fix rate_limits.updated_by FK (MED-5) ---
        try:
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='rate_limits'"
            )
            row = await cursor.fetchone()
            if row:
                ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
                if 'SET NULL' not in ddl:
                    old_count_cursor = await db.execute("SELECT COUNT(*) FROM rate_limits")
                    old_count = (await old_count_cursor.fetchone())[0]

                    logger.info("Migrating rate_limits: adding ON DELETE SET NULL to updated_by")
                    await db.execute("ALTER TABLE rate_limits RENAME TO _rate_limits_old")
                    await db.execute("""
                        CREATE TABLE rate_limits (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            benchmarks_per_hour INTEGER NOT NULL DEFAULT 20,
                            max_concurrent INTEGER NOT NULL DEFAULT 1,
                            max_runs_per_benchmark INTEGER NOT NULL DEFAULT 10,
                            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                            updated_by TEXT REFERENCES users(id) ON DELETE SET NULL
                        )
                    """)
                    await db.execute("INSERT INTO rate_limits SELECT * FROM _rate_limits_old")

                    new_count_cursor = await db.execute("SELECT COUNT(*) FROM rate_limits")
                    new_count = (await new_count_cursor.fetchone())[0]
                    assert old_count == new_count, f"Row count mismatch: {old_count} vs {new_count}"

                    await db.execute("DROP TABLE _rate_limits_old")
                    await db.commit()
                    logger.info("Migrated rate_limits: added ON DELETE SET NULL to updated_by")
        except Exception:
            logger.exception("Failed to migrate rate_limits FK")

        # --- C5a: tool_test_cases.param_scoring CHECK ---
        try:
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tool_test_cases'"
            )
            row = await cursor.fetchone()
            if row:
                ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
                if "param_scoring IN" not in ddl:
                    old_count_cursor = await db.execute("SELECT COUNT(*) FROM tool_test_cases")
                    old_count = (await old_count_cursor.fetchone())[0]

                    logger.info("Migrating tool_test_cases: adding param_scoring CHECK constraint")
                    await db.execute("ALTER TABLE tool_test_cases RENAME TO _tool_test_cases_old")
                    await db.execute("""
                        CREATE TABLE tool_test_cases (
                            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                            suite_id TEXT NOT NULL REFERENCES tool_suites(id) ON DELETE CASCADE,
                            prompt TEXT NOT NULL,
                            expected_tool TEXT,
                            expected_params TEXT,
                            param_scoring TEXT NOT NULL DEFAULT 'exact'
                                CHECK(param_scoring IN ('exact','fuzzy','contains','semantic')),
                            created_at TEXT NOT NULL DEFAULT (datetime('now')),
                            multi_turn_config TEXT,
                            scoring_config_json TEXT,
                            should_call_tool INTEGER NOT NULL DEFAULT 1,
                            category TEXT
                        )
                    """)
                    await db.execute("INSERT INTO tool_test_cases SELECT * FROM _tool_test_cases_old")

                    new_count_cursor = await db.execute("SELECT COUNT(*) FROM tool_test_cases")
                    new_count = (await new_count_cursor.fetchone())[0]
                    assert old_count == new_count, f"Row count mismatch: {old_count} vs {new_count}"

                    await db.execute("DROP TABLE _tool_test_cases_old")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_test_cases_suite ON tool_test_cases(suite_id)")
                    await db.commit()
                    logger.info("Migrated tool_test_cases: added param_scoring CHECK constraint")
        except Exception:
            logger.exception("Failed to migrate tool_test_cases CHECK constraint")

        # --- C5b: param_tune_runs.optimization_mode CHECK ---
        try:
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='param_tune_runs'"
            )
            row = await cursor.fetchone()
            if row:
                ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
                if "optimization_mode IN" not in ddl:
                    old_count_cursor = await db.execute("SELECT COUNT(*) FROM param_tune_runs")
                    old_count = (await old_count_cursor.fetchone())[0]

                    logger.info("Migrating param_tune_runs: adding optimization_mode CHECK constraint")
                    await db.execute("ALTER TABLE param_tune_runs RENAME TO _param_tune_runs_old")
                    await db.execute("""
                        CREATE TABLE param_tune_runs (
                            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            suite_id TEXT NOT NULL,
                            models_json TEXT NOT NULL,
                            search_space_json TEXT NOT NULL,
                            results_json TEXT NOT NULL DEFAULT '[]',
                            best_config_json TEXT,
                            best_score REAL DEFAULT 0.0,
                            total_combos INTEGER NOT NULL,
                            completed_combos INTEGER DEFAULT 0,
                            status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','cancelled','error','interrupted')),
                            duration_s REAL,
                            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                            experiment_id TEXT,
                            judge_scores_json TEXT,
                            optimization_mode TEXT NOT NULL DEFAULT 'grid'
                                CHECK(optimization_mode IN ('grid','random','bayesian')),
                            FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
                            FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
                        )
                    """)
                    await db.execute(
                        "INSERT INTO param_tune_runs "
                        "(id, user_id, suite_id, models_json, search_space_json, "
                        "results_json, best_config_json, best_score, total_combos, "
                        "completed_combos, status, duration_s, timestamp, experiment_id, "
                        "judge_scores_json, optimization_mode) "
                        "SELECT id, user_id, suite_id, models_json, search_space_json, "
                        "results_json, best_config_json, best_score, total_combos, "
                        "completed_combos, status, duration_s, timestamp, experiment_id, "
                        "judge_scores_json, optimization_mode "
                        "FROM _param_tune_runs_old"
                    )

                    new_count_cursor = await db.execute("SELECT COUNT(*) FROM param_tune_runs")
                    new_count = (await new_count_cursor.fetchone())[0]
                    assert old_count == new_count, f"Row count mismatch: {old_count} vs {new_count}"

                    await db.execute("DROP TABLE _param_tune_runs_old")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_user ON param_tune_runs(user_id)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_ts ON param_tune_runs(user_id, timestamp DESC)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_suite ON param_tune_runs(suite_id)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_experiment ON param_tune_runs(experiment_id)")
                    await db.commit()
                    logger.info("Migrated param_tune_runs: added optimization_mode CHECK constraint")
        except Exception:
            logger.exception("Failed to migrate param_tune_runs CHECK constraint")

        # Migration: add 'prompt_auto_optimize' to jobs CHECK constraint
        try:
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
            )
            row = await cursor.fetchone()
            if row:
                ddl = row[0] if isinstance(row, (tuple, list)) else row["sql"]
                if "'prompt_auto_optimize'" not in ddl:
                    logger.info("Migrating jobs: adding 'prompt_auto_optimize' to job_type CHECK")
                    new_ddl = ddl.replace(
                        "'scheduled_benchmark'\n                )",
                        "'scheduled_benchmark',\n                    'prompt_auto_optimize'\n                )",
                    )
                    if new_ddl == ddl:
                        # Try alternate formatting
                        new_ddl = ddl.replace(
                            "'scheduled_benchmark')",
                            "'scheduled_benchmark', 'prompt_auto_optimize')",
                        )
                    await db.execute("ALTER TABLE jobs RENAME TO _jobs_old")
                    await db.execute(new_ddl)
                    await db.execute("INSERT INTO jobs SELECT * FROM _jobs_old")
                    await db.execute("DROP TABLE _jobs_old")
                    await db.commit()
                    logger.info("Migration complete for jobs table")
        except Exception:
            logger.exception("Failed to migrate jobs CHECK constraint")

        # --- Migration: prompt_version_id FK on model_profiles ---
        try:
            await db.execute(
                "ALTER TABLE model_profiles ADD COLUMN prompt_version_id TEXT "
                "REFERENCES prompt_versions(id) ON DELETE SET NULL"
            )
            await db.commit()
            logger.info("Added prompt_version_id column to model_profiles")
        except Exception:
            logger.debug("Column model_profiles.prompt_version_id already exists")

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_profiles_prompt_version "
            "ON model_profiles(prompt_version_id)"
        )
        await db.commit()

        # Phase 1: dual-column additions for provider/model normalization
        for col_sql in [
            "ALTER TABLE user_api_keys ADD COLUMN provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL",
            "ALTER TABLE model_profiles ADD COLUMN model_db_id TEXT REFERENCES models(id) ON DELETE SET NULL",
            "ALTER TABLE prompt_versions ADD COLUMN model_db_id TEXT REFERENCES models(id) ON DELETE SET NULL",
            "ALTER TABLE judge_reports ADD COLUMN judge_model_id TEXT REFERENCES models(id) ON DELETE SET NULL",
            "ALTER TABLE public_leaderboard ADD COLUMN model_db_id TEXT REFERENCES models(id) ON DELETE SET NULL",
            "ALTER TABLE prompt_tune_runs ADD COLUMN meta_model_id TEXT REFERENCES models(id) ON DELETE SET NULL",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                pass

        # Phase 1: Seed providers/models from config for all existing users
        await _apply_migration(db, 100, "Seed providers/models from config", _seed_providers_from_config)

        # Phase 2: Drop denormalized suite_name from 3 tables
        await _apply_migration(db, 200, "Drop suite_name from eval/tune tables", _migration_200_drop_suite_name)

        # Phase 3: Prompt → prompt_versions FK columns
        await _apply_migration(db, 300, "Add prompt FK columns", _migration_300_prompt_fks)
        await _apply_migration(db, 301, "Backfill prompt version FKs", _migration_301_backfill_prompts)

        # Phase 4: Normalized run_results table
        await _apply_migration(db, 400, "Create run_results table", _migration_400_run_results)

        # Phase 5: best_profile_id FK on param_tune_runs
        await _apply_migration(db, 500, "Add best_profile_id FK to param_tune_runs", _migration_500_best_profile_id)

        # Phase 6: Final cutover — drop deprecated string columns via table rebuilds
        await _apply_migration(db, 600, "Drop deprecated string columns from normalized tables", _migration_600_final_cutover)


# --- Migration helpers ---

async def _get_schema_version(conn) -> int:
    """Get current schema version."""
    try:
        cursor = await conn.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        return row[0] if row and row[0] else 0
    except Exception:
        return 0


async def _apply_migration(conn, version: int, description: str, migration_fn):
    """Apply a numbered migration if not already applied."""
    current = await _get_schema_version(conn)
    if current >= version:
        logger.debug("Migration %d already applied, skipping", version)
        return False
    logger.info("Applying migration %d: %s", version, description)
    await migration_fn(conn)
    await conn.execute(
        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
        (version, description)
    )
    await conn.commit()
    logger.info("Migration %d complete", version)
    return True


async def _seed_providers_from_config(conn):
    """Migration 100: Seed providers/models tables from existing config data."""
    import yaml

    # Load default config
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            default_config = yaml.safe_load(f)
    else:
        default_config = {"providers": {}}

    # Get all users
    cursor = await conn.execute("SELECT id FROM users")
    users = await cursor.fetchall()

    for user_row in users:
        user_id = user_row[0] if isinstance(user_row, (tuple, list)) else user_row["id"]

        # Check if already seeded
        check = await conn.execute(
            "SELECT COUNT(*) FROM providers WHERE user_id = ?", (user_id,)
        )
        count = (await check.fetchone())[0]
        if count > 0:
            continue

        # Try user-specific config first
        uc_cursor = await conn.execute(
            "SELECT config_yaml FROM user_configs WHERE user_id = ?", (user_id,)
        )
        uc_row = await uc_cursor.fetchone()
        if uc_row:
            try:
                user_config = yaml.safe_load(
                    uc_row[0] if isinstance(uc_row, (tuple, list)) else uc_row["config_yaml"]
                )
            except Exception:
                user_config = default_config
        else:
            user_config = default_config

        # Seed providers and models
        sort_order = 0
        for prov_key, prov_cfg in user_config.get("providers", {}).items():
            provider_id = secrets.token_hex(16)
            await conn.execute(
                "INSERT OR IGNORE INTO providers "
                "(id, user_id, key, name, api_base, api_key_env, model_prefix, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (provider_id, user_id, prov_key,
                 prov_cfg.get("display_name", prov_key),
                 prov_cfg.get("api_base"),
                 prov_cfg.get("api_key_env"),
                 prov_cfg.get("model_id_prefix"),
                 sort_order),
            )
            sort_order += 1
            for model in prov_cfg.get("models", []):
                await conn.execute(
                    "INSERT OR IGNORE INTO models "
                    "(id, provider_id, litellm_id, display_name, context_window, "
                    "max_output_tokens, skip_params) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (secrets.token_hex(16), provider_id, model["id"],
                     model.get("display_name", model["id"]),
                     model.get("context_window", 128000),
                     model.get("max_output_tokens"),
                     json.dumps(model.get("skip_params", []))),
                )

        # Backfill user_api_keys.provider_id
        await conn.execute("""
            UPDATE user_api_keys SET provider_id = (
                SELECT p.id FROM providers p
                WHERE p.user_id = user_api_keys.user_id AND p.key = user_api_keys.provider_key
            )
            WHERE user_id = ? AND provider_id IS NULL
        """, (user_id,))

        # Backfill model_profiles.model_db_id
        await conn.execute("""
            UPDATE model_profiles SET model_db_id = (
                SELECT m.id FROM models m
                JOIN providers p ON m.provider_id = p.id
                WHERE p.user_id = model_profiles.user_id
                AND m.litellm_id = model_profiles.model_id
                LIMIT 1
            )
            WHERE user_id = ? AND model_db_id IS NULL
        """, (user_id,))

    await conn.commit()


async def _migration_200_drop_suite_name(conn):
    """Migration 200: Remove denormalized suite_name column from tool_eval_runs, param_tune_runs, prompt_tune_runs.

    Suite name is always available via JOIN on tool_suites.id = suite_id.
    Uses the standard table-rebuild pattern: RENAME -> CREATE -> INSERT SELECT -> verify -> DROP -> reindex.
    Only rebuilds tables that still have the suite_name column (skip on fresh DBs).
    """

    # Check if suite_name column exists before attempting rebuild
    cursor = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tool_eval_runs'"
    )
    row = await cursor.fetchone()
    ddl = (row[0] if isinstance(row, (tuple, list)) else row["sql"]) if row else ""
    if "suite_name" not in ddl:
        logger.info("Migration 200: suite_name already absent from tables, skipping rebuild")
        return

    # --- tool_eval_runs ---
    old_count_cursor = await conn.execute("SELECT COUNT(*) FROM tool_eval_runs")
    old_count = (await old_count_cursor.fetchone())[0]

    logger.info("Migration 200: rebuilding tool_eval_runs without suite_name (%d rows)", old_count)
    await conn.execute("ALTER TABLE tool_eval_runs RENAME TO _tool_eval_runs_old")
    await conn.execute("""
        CREATE TABLE tool_eval_runs (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            suite_id TEXT NOT NULL,
            models_json TEXT NOT NULL,
            results_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            temperature REAL DEFAULT 0.0,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            config_json TEXT,
            experiment_id TEXT,
            profiles_json TEXT,
            judge_explanations_json TEXT,
            FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
            FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
        )
    """)
    await conn.execute("""
        INSERT INTO tool_eval_runs
        (id, user_id, suite_id, models_json, results_json, summary_json,
         temperature, timestamp, config_json, experiment_id, profiles_json, judge_explanations_json)
        SELECT id, user_id, suite_id, models_json, results_json, summary_json,
               temperature, timestamp, config_json, experiment_id, profiles_json, judge_explanations_json
        FROM _tool_eval_runs_old
    """)

    new_count_cursor = await conn.execute("SELECT COUNT(*) FROM tool_eval_runs")
    new_count = (await new_count_cursor.fetchone())[0]
    assert old_count == new_count, f"tool_eval_runs row count mismatch: {old_count} vs {new_count}"

    await conn.execute("DROP TABLE _tool_eval_runs_old")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_user ON tool_eval_runs(user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_ts ON tool_eval_runs(user_id, timestamp DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_suite ON tool_eval_runs(suite_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_experiment ON tool_eval_runs(experiment_id)")
    await conn.commit()
    logger.info("Migration 200: tool_eval_runs rebuilt successfully")

    # --- param_tune_runs ---
    old_count_cursor = await conn.execute("SELECT COUNT(*) FROM param_tune_runs")
    old_count = (await old_count_cursor.fetchone())[0]

    logger.info("Migration 200: rebuilding param_tune_runs without suite_name (%d rows)", old_count)
    await conn.execute("ALTER TABLE param_tune_runs RENAME TO _param_tune_runs_old")
    await conn.execute("""
        CREATE TABLE param_tune_runs (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            suite_id TEXT NOT NULL,
            models_json TEXT NOT NULL,
            search_space_json TEXT NOT NULL,
            results_json TEXT NOT NULL DEFAULT '[]',
            best_config_json TEXT,
            best_score REAL DEFAULT 0.0,
            total_combos INTEGER NOT NULL,
            completed_combos INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','cancelled','error','interrupted')),
            duration_s REAL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            experiment_id TEXT,
            judge_scores_json TEXT,
            optimization_mode TEXT NOT NULL DEFAULT 'grid'
                CHECK(optimization_mode IN ('grid','random','bayesian')),
            FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
            FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
        )
    """)
    await conn.execute("""
        INSERT INTO param_tune_runs
        (id, user_id, suite_id, models_json, search_space_json, results_json,
         best_config_json, best_score, total_combos, completed_combos, status,
         duration_s, timestamp, experiment_id, judge_scores_json, optimization_mode)
        SELECT id, user_id, suite_id, models_json, search_space_json, results_json,
               best_config_json, best_score, total_combos, completed_combos, status,
               duration_s, timestamp, experiment_id, judge_scores_json, optimization_mode
        FROM _param_tune_runs_old
    """)

    new_count_cursor = await conn.execute("SELECT COUNT(*) FROM param_tune_runs")
    new_count = (await new_count_cursor.fetchone())[0]
    assert old_count == new_count, f"param_tune_runs row count mismatch: {old_count} vs {new_count}"

    await conn.execute("DROP TABLE _param_tune_runs_old")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_user ON param_tune_runs(user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_ts ON param_tune_runs(user_id, timestamp DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_suite ON param_tune_runs(suite_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_experiment ON param_tune_runs(experiment_id)")
    await conn.commit()
    logger.info("Migration 200: param_tune_runs rebuilt successfully")

    # --- prompt_tune_runs ---
    old_count_cursor = await conn.execute("SELECT COUNT(*) FROM prompt_tune_runs")
    old_count = (await old_count_cursor.fetchone())[0]

    logger.info("Migration 200: rebuilding prompt_tune_runs without suite_name (%d rows)", old_count)
    await conn.execute("ALTER TABLE prompt_tune_runs RENAME TO _prompt_tune_runs_old")
    await conn.execute("""
        CREATE TABLE prompt_tune_runs (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            suite_id TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode IN ('quick','evolutionary')),
            target_models_json TEXT NOT NULL,
            meta_model TEXT NOT NULL,
            base_prompt TEXT,
            config_json TEXT NOT NULL,
            generations_json TEXT NOT NULL DEFAULT '[]',
            best_prompt TEXT,
            best_score REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','cancelled','error','interrupted')),
            total_prompts INTEGER DEFAULT 0,
            completed_prompts INTEGER DEFAULT 0,
            duration_s REAL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            experiment_id TEXT,
            best_prompt_origin_json TEXT,
            meta_provider_key TEXT,
            meta_model_id TEXT REFERENCES models(id) ON DELETE SET NULL,
            FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
            FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
        )
    """)
    await conn.execute("""
        INSERT INTO prompt_tune_runs
        (id, user_id, suite_id, mode, target_models_json, meta_model,
         base_prompt, config_json, generations_json, best_prompt, best_score, status,
         total_prompts, completed_prompts, duration_s, timestamp, experiment_id,
         best_prompt_origin_json, meta_provider_key, meta_model_id)
        SELECT id, user_id, suite_id, mode, target_models_json, meta_model,
               base_prompt, config_json, generations_json, best_prompt, best_score, status,
               total_prompts, completed_prompts, duration_s, timestamp, experiment_id,
               best_prompt_origin_json, meta_provider_key, meta_model_id
        FROM _prompt_tune_runs_old
    """)

    new_count_cursor = await conn.execute("SELECT COUNT(*) FROM prompt_tune_runs")
    new_count = (await new_count_cursor.fetchone())[0]
    assert old_count == new_count, f"prompt_tune_runs row count mismatch: {old_count} vs {new_count}"

    await conn.execute("DROP TABLE _prompt_tune_runs_old")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_user ON prompt_tune_runs(user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_ts ON prompt_tune_runs(user_id, timestamp DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_suite ON prompt_tune_runs(suite_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_experiment ON prompt_tune_runs(experiment_id)")
    await conn.commit()
    logger.info("Migration 200: prompt_tune_runs rebuilt successfully")


async def _migration_300_prompt_fks(conn):
    """Migration 300: Add prompt_version_id FK to schedules and best_prompt_version_id to prompt_tune_runs."""
    # schedules.prompt_version_id -- links schedule prompt to prompt_versions
    try:
        await conn.execute(
            "ALTER TABLE schedules ADD COLUMN prompt_version_id TEXT "
            "REFERENCES prompt_versions(id) ON DELETE SET NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedules_prompt_version "
            "ON schedules(prompt_version_id)"
        )
        await conn.commit()
        logger.info("Added prompt_version_id to schedules")
    except Exception:
        logger.debug("schedules.prompt_version_id already exists")

    # prompt_tune_runs.best_prompt_version_id -- links best prompt result to prompt_versions
    try:
        await conn.execute(
            "ALTER TABLE prompt_tune_runs ADD COLUMN best_prompt_version_id TEXT "
            "REFERENCES prompt_versions(id) ON DELETE SET NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_tune_best_pv "
            "ON prompt_tune_runs(best_prompt_version_id)"
        )
        await conn.commit()
        logger.info("Added best_prompt_version_id to prompt_tune_runs")
    except Exception:
        logger.debug("prompt_tune_runs.best_prompt_version_id already exists")


async def _migration_301_backfill_prompts(conn):
    """Migration 301: Backfill prompt_version_id FKs for existing data.

    - For prompt_tune_runs: link best_prompt to existing prompt_versions via origin_run_id match.
    - For schedules: create a new prompt_version for each schedule's prompt text.
    """
    conn.row_factory = aiosqlite.Row

    # --- Backfill prompt_tune_runs ---
    # The prompt_tune_handler already creates a prompt_version with origin_run_id=tune_id.
    # Link those existing versions to best_prompt_version_id.
    # Note: On fresh databases (post-migration 600 schema), best_prompt column doesn't exist.
    # Check if the column exists before querying it.
    ddl_cursor = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='prompt_tune_runs'"
    )
    ddl_row = await ddl_cursor.fetchone()
    ddl_text = (ddl_row[0] if isinstance(ddl_row, (tuple, list)) else ddl_row["sql"]) if ddl_row else ""
    has_best_prompt_col = "best_prompt " in ddl_text and "best_prompt_version_id" in ddl_text

    linked_tunes = 0
    if has_best_prompt_col:
        cursor = await conn.execute(
            "SELECT ptr.id AS tune_id, ptr.user_id, ptr.best_prompt "
            "FROM prompt_tune_runs ptr "
            "WHERE ptr.best_prompt IS NOT NULL AND ptr.best_prompt_version_id IS NULL"
        )
        tune_rows = await cursor.fetchall()

        for row in tune_rows:
            tune_id = row["tune_id"]
            user_id = row["user_id"]
            best_prompt = row["best_prompt"]

            # Check for existing prompt_version linked by origin_run_id
            pv_cursor = await conn.execute(
                "SELECT id FROM prompt_versions "
                "WHERE user_id = ? AND origin_run_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id, tune_id),
            )
            pv_row = await pv_cursor.fetchone()

            if pv_row:
                # Link existing version
                await conn.execute(
                    "UPDATE prompt_tune_runs SET best_prompt_version_id = ? WHERE id = ?",
                    (pv_row["id"], tune_id),
                )
                linked_tunes += 1
            else:
                # Check for matching prompt_text from same user
                pv_cursor2 = await conn.execute(
                    "SELECT id FROM prompt_versions "
                    "WHERE user_id = ? AND prompt_text = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (user_id, best_prompt),
                )
                pv_row2 = await pv_cursor2.fetchone()
                if pv_row2:
                    await conn.execute(
                        "UPDATE prompt_tune_runs SET best_prompt_version_id = ? WHERE id = ?",
                        (pv_row2["id"], tune_id),
                    )
                    linked_tunes += 1
                else:
                    # Create new prompt_version
                    new_pv_id = secrets.token_hex(16)
                    await conn.execute(
                        "INSERT INTO prompt_versions (id, user_id, prompt_text, label, source, origin_run_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (new_pv_id, user_id, best_prompt,
                         f"Prompt Tune Best (backfill)", "prompt_tuner", tune_id),
                    )
                    await conn.execute(
                        "UPDATE prompt_tune_runs SET best_prompt_version_id = ? WHERE id = ?",
                        (new_pv_id, tune_id),
                    )
                    linked_tunes += 1
    else:
        logger.info("Migration 301: best_prompt column absent (fresh DB), skipping prompt_tune_runs backfill")

    logger.info("Migration 301: linked %d prompt_tune_runs to prompt_versions", linked_tunes)

    # --- Backfill schedules ---
    cursor = await conn.execute(
        "SELECT id, user_id, name, prompt FROM schedules "
        "WHERE prompt IS NOT NULL AND prompt != '' AND prompt_version_id IS NULL"
    )
    sched_rows = await cursor.fetchall()
    linked_scheds = 0

    for row in sched_rows:
        sched_id = row["id"]
        user_id = row["user_id"]
        sched_name = row["name"]
        prompt_text = row["prompt"]

        new_pv_id = secrets.token_hex(16)
        await conn.execute(
            "INSERT INTO prompt_versions (id, user_id, prompt_text, label, source, origin_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_pv_id, user_id, prompt_text,
             f"Schedule: {sched_name}", "manual", sched_id),
        )
        await conn.execute(
            "UPDATE schedules SET prompt_version_id = ? WHERE id = ?",
            (new_pv_id, sched_id),
        )
        linked_scheds += 1

    logger.info("Migration 301: created %d prompt_versions for schedules", linked_scheds)
    await conn.commit()


async def _migration_400_run_results(conn):
    """Migration 400: Create run_results table for normalized per-model metrics."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS run_results (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            run_id TEXT NOT NULL,
            run_type TEXT NOT NULL CHECK(run_type IN ('benchmark','tool_eval','param_tune','prompt_tune','judge')),
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            model_litellm_id TEXT NOT NULL,
            model_db_id TEXT REFERENCES models(id) ON DELETE SET NULL,
            profile_id TEXT REFERENCES model_profiles(id) ON DELETE SET NULL,
            provider_key TEXT,
            tool_accuracy_pct REAL,
            param_accuracy_pct REAL,
            throughput_tps REAL,
            ttft_ms REAL,
            tokens_generated INTEGER,
            latency_p99_ms REAL,
            score REAL,
            cost REAL,
            raw_response_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_run_results_run ON run_results(run_id, run_type)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_run_results_user ON run_results(user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_run_results_model ON run_results(model_litellm_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_run_results_model_db ON run_results(model_db_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_run_results_ts ON run_results(user_id, created_at DESC)")
    await conn.commit()
    logger.info("Created run_results table with indexes")


async def _migration_500_best_profile_id(conn):
    """Migration 500: Add best_profile_id FK to param_tune_runs."""
    try:
        await conn.execute(
            "ALTER TABLE param_tune_runs ADD COLUMN best_profile_id TEXT "
            "REFERENCES model_profiles(id) ON DELETE SET NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_param_tune_best_profile "
            "ON param_tune_runs(best_profile_id)"
        )
        await conn.commit()
        logger.info("Added best_profile_id to param_tune_runs")
    except Exception:
        logger.debug("param_tune_runs.best_profile_id already exists")


async def _migration_600_final_cutover(conn):
    """Migration 600: Drop deprecated string columns from normalized tables.

    Table rebuilds:
    - prompt_tune_runs: drop meta_model, meta_provider_key, best_prompt (keep FKs)
    - judge_reports: drop judge_model string (keep judge_model_id FK)
    - public_leaderboard: drop model_name, provider strings (keep model_db_id FK)

    SKIPPED (still needed by UI):
    - user_api_keys: provider_key still used by key management UI
    - model_profiles: model_id (litellm string) still used by profiles UI
    - schedules: prompt text still needed as fallback display (prompt_version_id may be NULL)
    """
    conn.row_factory = aiosqlite.Row

    # ---- 6e: prompt_tune_runs — drop meta_model, meta_provider_key, best_prompt ----
    # First check if the deprecated columns even exist (they won't on fresh DBs).
    ptr_ddl_cursor = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='prompt_tune_runs'"
    )
    ptr_ddl_row = await ptr_ddl_cursor.fetchone()
    ptr_ddl = (ptr_ddl_row[0] if isinstance(ptr_ddl_row, (tuple, list)) else ptr_ddl_row["sql"]) if ptr_ddl_row else ""
    ptr_has_meta_model = "meta_model " in ptr_ddl and "meta_model_id" != "meta_model "

    # More precise check: look for meta_model as a standalone column (not meta_model_id)
    ptr_has_old_columns = ("meta_model TEXT" in ptr_ddl or "meta_provider_key TEXT" in ptr_ddl
                           or "\n                best_prompt TEXT" in ptr_ddl
                           or ",\n                best_prompt " in ptr_ddl)

    if not ptr_has_old_columns:
        logger.info("Migration 600: prompt_tune_runs already has new schema, skipping rebuild")
    else:
        # Safety: only drop if meta_model_id and best_prompt_version_id are populated
        check = await conn.execute(
            "SELECT COUNT(*) FROM prompt_tune_runs WHERE meta_model_id IS NULL AND status = 'completed'"
        )
        null_meta_count = (await check.fetchone())[0]

        null_bpv_count = 0
        if "best_prompt " in ptr_ddl:
            check2 = await conn.execute(
                "SELECT COUNT(*) FROM prompt_tune_runs "
                "WHERE best_prompt IS NOT NULL AND best_prompt_version_id IS NULL"
            )
            null_bpv_count = (await check2.fetchone())[0]

        if null_meta_count > 0:
            logger.warning(
                "Migration 600: SKIPPING prompt_tune_runs rebuild — %d completed rows have NULL meta_model_id",
                null_meta_count,
            )
        elif null_bpv_count > 0:
            logger.warning(
                "Migration 600: SKIPPING prompt_tune_runs rebuild — %d rows have best_prompt without best_prompt_version_id",
                null_bpv_count,
            )
        else:
            old_count_cursor = await conn.execute("SELECT COUNT(*) FROM prompt_tune_runs")
            old_count = (await old_count_cursor.fetchone())[0]

            logger.info("Migration 600: rebuilding prompt_tune_runs — dropping meta_model, meta_provider_key, best_prompt (%d rows)", old_count)
            await conn.execute("ALTER TABLE prompt_tune_runs RENAME TO _prompt_tune_runs_old")
            await conn.execute("""
                CREATE TABLE prompt_tune_runs (
                    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    suite_id TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('quick','evolutionary')),
                    target_models_json TEXT NOT NULL,
                    base_prompt TEXT,
                    config_json TEXT NOT NULL,
                    generations_json TEXT NOT NULL DEFAULT '[]',
                    best_score REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','cancelled','error','interrupted')),
                    total_prompts INTEGER DEFAULT 0,
                    completed_prompts INTEGER DEFAULT 0,
                    duration_s REAL,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    experiment_id TEXT,
                    best_prompt_origin_json TEXT,
                    meta_model_id TEXT REFERENCES models(id) ON DELETE SET NULL,
                    best_prompt_version_id TEXT REFERENCES prompt_versions(id) ON DELETE SET NULL,
                    best_profile_id TEXT REFERENCES model_profiles(id) ON DELETE SET NULL,
                    FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
                    FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
                )
            """)
            await conn.execute("""
                INSERT INTO prompt_tune_runs
                (id, user_id, suite_id, mode, target_models_json,
                 base_prompt, config_json, generations_json, best_score, status,
                 total_prompts, completed_prompts, duration_s, timestamp, experiment_id,
                 best_prompt_origin_json, meta_model_id, best_prompt_version_id, best_profile_id)
                SELECT id, user_id, suite_id, mode, target_models_json,
                       base_prompt, config_json, generations_json, best_score, status,
                       total_prompts, completed_prompts, duration_s, timestamp, experiment_id,
                       best_prompt_origin_json, meta_model_id, best_prompt_version_id, best_profile_id
                FROM _prompt_tune_runs_old
            """)

            new_count_cursor = await conn.execute("SELECT COUNT(*) FROM prompt_tune_runs")
            new_count = (await new_count_cursor.fetchone())[0]
            assert old_count == new_count, f"prompt_tune_runs row count mismatch: {old_count} vs {new_count}"

            await conn.execute("DROP TABLE _prompt_tune_runs_old")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_user ON prompt_tune_runs(user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_ts ON prompt_tune_runs(user_id, timestamp DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_suite ON prompt_tune_runs(suite_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_experiment ON prompt_tune_runs(experiment_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_best_pv ON prompt_tune_runs(best_prompt_version_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_best_profile ON prompt_tune_runs(best_profile_id)")
            await conn.commit()
            logger.info("Migration 600: prompt_tune_runs rebuilt successfully")

    # ---- 6f: judge_reports — drop judge_model string (keep judge_model_id FK) ----
    check_jr = await conn.execute(
        "SELECT COUNT(*) FROM judge_reports WHERE judge_model_id IS NULL"
    )
    null_jm_count = (await check_jr.fetchone())[0]

    if null_jm_count > 0:
        logger.warning(
            "Migration 600: SKIPPING judge_reports rebuild — %d rows have NULL judge_model_id",
            null_jm_count,
        )
    else:
        old_count_cursor = await conn.execute("SELECT COUNT(*) FROM judge_reports")
        old_count = (await old_count_cursor.fetchone())[0]

        logger.info("Migration 600: rebuilding judge_reports — dropping judge_model string (%d rows)", old_count)
        await conn.execute("ALTER TABLE judge_reports RENAME TO _judge_reports_old")
        await conn.execute("""
            CREATE TABLE judge_reports (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                eval_run_id TEXT,
                eval_run_id_b TEXT,
                judge_model_id TEXT REFERENCES models(id) ON DELETE SET NULL,
                mode TEXT NOT NULL CHECK(mode IN ('post_eval','live_inline','comparative')),
                verdicts_json TEXT NOT NULL DEFAULT '[]',
                report_json TEXT,
                overall_grade TEXT,
                overall_score REAL,
                status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','error','interrupted')),
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                experiment_id TEXT,
                parent_report_id TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                instructions_json TEXT,
                FOREIGN KEY (eval_run_id) REFERENCES tool_eval_runs(id) ON DELETE SET NULL,
                FOREIGN KEY (eval_run_id_b) REFERENCES tool_eval_runs(id) ON DELETE SET NULL,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
            )
        """)
        await conn.execute("""
            INSERT INTO judge_reports
            (id, user_id, eval_run_id, eval_run_id_b, judge_model_id, mode,
             verdicts_json, report_json, overall_grade, overall_score,
             status, timestamp, experiment_id,
             parent_report_id, version, instructions_json)
            SELECT id, user_id, eval_run_id, eval_run_id_b, judge_model_id, mode,
                   verdicts_json, report_json, overall_grade, overall_score,
                   status, timestamp, experiment_id,
                   parent_report_id, version, instructions_json
            FROM _judge_reports_old
        """)

        new_count_cursor = await conn.execute("SELECT COUNT(*) FROM judge_reports")
        new_count = (await new_count_cursor.fetchone())[0]
        assert old_count == new_count, f"judge_reports row count mismatch: {old_count} vs {new_count}"

        await conn.execute("DROP TABLE _judge_reports_old")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_user ON judge_reports(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_eval ON judge_reports(eval_run_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_ts ON judge_reports(user_id, timestamp DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_experiment ON judge_reports(experiment_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_parent ON judge_reports(parent_report_id)")
        await conn.commit()
        logger.info("Migration 600: judge_reports rebuilt successfully")

    # ---- 6h: public_leaderboard — drop model_name, provider strings (keep model_db_id FK) ----
    check_lb = await conn.execute(
        "SELECT COUNT(*) FROM public_leaderboard WHERE model_db_id IS NULL"
    )
    null_lb_count = (await check_lb.fetchone())[0]

    if null_lb_count > 0:
        logger.warning(
            "Migration 600: SKIPPING public_leaderboard rebuild — %d rows have NULL model_db_id",
            null_lb_count,
        )
    else:
        old_count_cursor = await conn.execute("SELECT COUNT(*) FROM public_leaderboard")
        old_count = (await old_count_cursor.fetchone())[0]

        logger.info("Migration 600: rebuilding public_leaderboard — dropping model_name, provider strings (%d rows)", old_count)
        await conn.execute("ALTER TABLE public_leaderboard RENAME TO _public_leaderboard_old")
        await conn.execute("""
            CREATE TABLE public_leaderboard (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                model_db_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
                tool_accuracy_pct REAL DEFAULT 0.0,
                param_accuracy_pct REAL DEFAULT 0.0,
                irrel_accuracy_pct REAL,
                throughput_tps REAL,
                ttft_ms REAL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                last_updated TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(model_db_id)
            )
        """)
        await conn.execute("""
            INSERT INTO public_leaderboard
            (id, model_db_id, tool_accuracy_pct, param_accuracy_pct,
             irrel_accuracy_pct, throughput_tps, ttft_ms, sample_count, last_updated)
            SELECT id, model_db_id, tool_accuracy_pct, param_accuracy_pct,
                   irrel_accuracy_pct, throughput_tps, ttft_ms, sample_count, last_updated
            FROM _public_leaderboard_old
        """)

        new_count_cursor = await conn.execute("SELECT COUNT(*) FROM public_leaderboard")
        new_count = (await new_count_cursor.fetchone())[0]
        assert old_count == new_count, f"public_leaderboard row count mismatch: {old_count} vs {new_count}"

        await conn.execute("DROP TABLE _public_leaderboard_old")
        await conn.commit()
        logger.info("Migration 600: public_leaderboard rebuilt successfully")


# --- User CRUD ---

async def create_user(email: str, password_hash: str, role: str = "user") -> dict:
    """Insert a new user. Returns the user dict."""
    user_id = uuid.uuid4().hex
    row = await _db.execute_returning_row(
        [("INSERT INTO users (id, email, password_hash, role) VALUES (?, ?, ?, ?)",
          (user_id, email, password_hash, role))],
        "SELECT id, email, role, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    return row


async def get_user_by_email(email: str) -> dict | None:
    """Look up user by email (case-insensitive). Returns full row including password_hash."""
    return await _db.fetch_one("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,))


async def get_user_by_id(user_id: str) -> dict | None:
    """Look up user by ID. Returns row without password_hash."""
    return await _db.fetch_one(
        "SELECT id, email, role, created_at, updated_at, onboarding_completed FROM users WHERE id = ?",
        (user_id,),
    )


async def set_onboarding_completed(user_id: str):
    """Mark onboarding as completed for a user."""
    await _db.execute("UPDATE users SET onboarding_completed = 1 WHERE id = ?", (user_id,))


async def update_user_password(user_id: str, password_hash: str):
    """Update the password_hash for a user."""
    await _db.execute(
        "UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE id = ?",
        (password_hash, user_id),
    )


# --- Google OAuth CRUD ---

async def get_user_by_google_id(google_id: str) -> dict | None:
    """Look up user by their Google OAuth ID."""
    return await _db.fetch_one("SELECT * FROM users WHERE google_id = ?", (google_id,))


async def link_google_id(user_id: str, google_id: str, avatar_url: str | None):
    """Link a Google account to an existing user."""
    await _db.execute(
        "UPDATE users SET google_id = ?, avatar_url = ?, updated_at = datetime('now') WHERE id = ?",
        (google_id, avatar_url, user_id),
    )


async def create_google_user(email: str, google_id: str, avatar_url: str | None, role: str = "user") -> dict:
    """Create a new user authenticated via Google (no password). Returns the user dict."""
    user_id = uuid.uuid4().hex
    row = await _db.execute_returning_row(
        [("INSERT INTO users (id, email, password_hash, role, google_id, avatar_url) VALUES (?, ?, ?, ?, ?, ?)",
          (user_id, email, "", role, google_id, avatar_url))],
        "SELECT id, email, role, created_at, google_id, avatar_url FROM users WHERE id = ?",
        (user_id,),
    )
    return row


# --- Password Reset CRUD ---

async def store_password_reset_token(user_id: str, token_hash: str, expires_at: str):
    """Store a hashed password reset token."""
    await _db.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (user_id, token_hash, expires_at),
    )


async def get_password_reset_token(token_hash: str) -> dict | None:
    """Look up a password reset token by its hash. Returns None if not found/expired/used."""
    return await _db.fetch_one(
        "SELECT * FROM password_reset_tokens "
        "WHERE token_hash = ? AND used = 0 AND expires_at > datetime('now')",
        (token_hash,),
    )


async def consume_password_reset_token(token_hash: str):
    """Mark a reset token as used (single-use enforcement)."""
    await _db.execute(
        "UPDATE password_reset_tokens SET used = 1 WHERE token_hash = ?",
        (token_hash,),
    )


async def delete_user_reset_tokens(user_id: str):
    """Delete all reset tokens for a user (cleanup on new request or after successful reset)."""
    await _db.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,))


async def count_users() -> int:
    """Return total user count."""
    return await _db.execute_returning_scalar("SELECT COUNT(*) FROM users")


# --- Refresh token CRUD ---

async def store_refresh_token(user_id: str, token_hash: str, expires_at: str):
    """Store a hashed refresh token."""
    await _db.execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?) "
        "ON CONFLICT(token_hash) DO UPDATE SET user_id=excluded.user_id, expires_at=excluded.expires_at",
        (user_id, token_hash, expires_at),
    )


async def get_refresh_token(token_hash: str) -> dict | None:
    """Look up a refresh token by its hash."""
    return await _db.fetch_one(
        "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
    )


async def delete_refresh_token(token_hash: str):
    """Delete a specific refresh token (logout)."""
    await _db.execute("DELETE FROM refresh_tokens WHERE token_hash = ?", (token_hash,))


async def delete_user_refresh_tokens(user_id: str):
    """Delete all refresh tokens for a user (logout everywhere)."""
    await _db.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))


async def cleanup_expired_tokens() -> int:
    """Remove expired refresh tokens. Call periodically. Returns count deleted."""
    return await _db.execute_returning_rowcount(
        "DELETE FROM refresh_tokens WHERE expires_at < datetime('now')"
    )


# --- User API keys CRUD ---

async def get_user_keys(user_id: str) -> list[dict]:
    """List all API keys for a user (encrypted values NOT returned)."""
    return await _db.fetch_all(
        "SELECT id, provider_key, key_name, created_at, updated_at "
        "FROM user_api_keys WHERE user_id = ? ORDER BY provider_key",
        (user_id,),
    )


async def get_user_key_for_provider(user_id: str, provider_key: str) -> Optional[str]:
    """Return the encrypted_value for a specific user+provider, or None."""
    row = await _db.fetch_one(
        "SELECT encrypted_value FROM user_api_keys "
        "WHERE user_id = ? AND provider_key = ?",
        (user_id, provider_key),
    )
    return row["encrypted_value"] if row else None


async def upsert_user_key(
    user_id: str, provider_key: str, key_name: str, encrypted_value: str
) -> str:
    """Insert or update a user's API key for a provider. Returns the key ID."""
    # Multi-step logic requires raw connection (conditional INSERT vs UPDATE)
    async with aiosqlite.connect(_db._path()) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            "SELECT id FROM user_api_keys WHERE user_id = ? AND provider_key = ?",
            (user_id, provider_key),
        )
        existing = await cursor.fetchone()

        if existing:
            key_id = existing["id"]
            await db.execute(
                "UPDATE user_api_keys SET key_name = ?, encrypted_value = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (key_name, encrypted_value, key_id),
            )
        else:
            key_id = uuid.uuid4().hex
            await db.execute(
                "INSERT INTO user_api_keys (id, user_id, provider_key, key_name, encrypted_value) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_id, user_id, provider_key, key_name, encrypted_value),
            )

        await db.commit()
        return key_id


async def get_user_config(user_id: str) -> dict | None:
    """Get user's config YAML as dict. Returns None if not set."""
    row = await _db.fetch_one(
        "SELECT config_yaml FROM user_configs WHERE user_id = ?", (user_id,)
    )
    if row:
        import yaml
        return yaml.safe_load(row["config_yaml"])
    return None


async def save_user_config(user_id: str, config_dict: dict):
    """Save user's config dict as YAML to DB."""
    import yaml
    config_yaml = yaml.dump(config_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)
    await _db.execute(
        "INSERT INTO user_configs (id, user_id, config_yaml) VALUES (lower(hex(randomblob(16))), ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET config_yaml = excluded.config_yaml, updated_at = datetime('now')",
        (user_id, config_yaml),
    )


async def delete_user_key(user_id: str, provider_key: str) -> bool:
    """Delete a user's API key for a provider. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM user_api_keys WHERE user_id = ? AND provider_key = ?",
        (user_id, provider_key),
    )
    return count > 0


# --- Benchmark runs CRUD ---

async def save_benchmark_run(
    user_id: str, prompt: str, context_tiers: str, results_json: str,
    metadata: str = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    warmup: bool | None = None,
    config_json: str | None = None,
) -> str:
    """Save a benchmark run. Returns the run ID."""
    run_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO benchmark_runs (id, user_id, prompt, context_tiers, results_json, metadata, "
        "max_tokens, temperature, warmup, config_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, user_id, prompt, context_tiers, results_json, metadata,
         max_tokens, temperature, int(warmup) if warmup is not None else None, config_json),
    )
    return run_id


async def get_user_benchmark_runs(user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Get benchmark runs for a user, newest first."""
    return await _db.fetch_all(
        "SELECT id, timestamp, prompt, context_tiers, results_json, metadata, "
        "max_tokens, temperature, warmup, config_json "
        "FROM benchmark_runs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )


async def get_benchmark_run(run_id: str, user_id: str) -> dict | None:
    """Get a specific benchmark run (scoped to user)."""
    return await _db.fetch_one(
        "SELECT * FROM benchmark_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )


async def delete_benchmark_run(run_id: str, user_id: str) -> bool:
    """Delete a benchmark run (scoped to user)."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM benchmark_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )
    return count > 0


# --- Run Results (Phase 4 normalization) ---

async def save_run_result(
    run_id: str, run_type: str, user_id: str,
    model_litellm_id: str, model_db_id: str | None = None,
    profile_id: str | None = None, provider_key: str | None = None,
    tool_accuracy_pct: float | None = None, param_accuracy_pct: float | None = None,
    throughput_tps: float | None = None, ttft_ms: float | None = None,
    tokens_generated: int | None = None, latency_p99_ms: float | None = None,
    score: float | None = None, cost: float | None = None,
    raw_response_json: str | None = None,
) -> str:
    """Insert a single run result row. Returns the result ID."""
    result_id = secrets.token_hex(16)
    await _db.execute(
        "INSERT INTO run_results (id, run_id, run_type, user_id, model_litellm_id, "
        "model_db_id, profile_id, provider_key, tool_accuracy_pct, param_accuracy_pct, "
        "throughput_tps, ttft_ms, tokens_generated, latency_p99_ms, score, cost, raw_response_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (result_id, run_id, run_type, user_id, model_litellm_id,
         model_db_id, profile_id, provider_key, tool_accuracy_pct, param_accuracy_pct,
         throughput_tps, ttft_ms, tokens_generated, latency_p99_ms, score, cost, raw_response_json),
    )
    return result_id


async def get_run_results(run_id: str, run_type: str) -> list[dict]:
    """Get all results for a specific run."""
    return await _db.fetch_all(
        "SELECT * FROM run_results WHERE run_id = ? AND run_type = ? ORDER BY created_at",
        (run_id, run_type),
    )


async def get_run_results_for_user(
    user_id: str, run_type: str | None = None,
    limit: int = 100, offset: int = 0,
) -> list[dict]:
    """Get run results for a user, optionally filtered by run_type."""
    if run_type:
        return await _db.fetch_all(
            "SELECT * FROM run_results WHERE user_id = ? AND run_type = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, run_type, limit, offset),
        )
    return await _db.fetch_all(
        "SELECT * FROM run_results WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )


async def get_run_results_by_model(
    user_id: str, model_litellm_id: str, limit: int = 50,
) -> list[dict]:
    """Get all results for a specific model across run types."""
    return await _db.fetch_all(
        "SELECT * FROM run_results WHERE user_id = ? AND model_litellm_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (user_id, model_litellm_id, limit),
    )


# --- Audit Log ---

async def log_audit(
    user_id: Optional[str],
    username: str,
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    """Write an audit log entry. Fire-and-forget, never raises."""
    try:
        await _db.execute(
            """INSERT INTO audit_log
               (user_id, username, action, resource_type, resource_id, detail, ip_address, user_agent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                username,
                action,
                resource_type,
                resource_id,
                json.dumps(detail) if detail else None,
                ip_address,
                user_agent,
            ),
        )
    except Exception:
        logger.exception("Audit logging failed (action=%s, user_id=%s)", action, user_id)


# --- Tool Suites CRUD ---

async def create_tool_suite(user_id: str, name: str, description: str, tools_json: str) -> str:
    """Create a tool suite. Returns suite_id."""
    suite_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO tool_suites (id, user_id, name, description, tools_json) VALUES (?, ?, ?, ?, ?)",
        (suite_id, user_id, name, description, tools_json),
    )
    return suite_id


async def get_tool_suites(user_id: str) -> list[dict]:
    """List user's tool suites with tool_count and test_case_count."""
    return await _db.fetch_all(
        """SELECT ts.*,
            json_array_length(ts.tools_json) as tool_count,
            (SELECT COUNT(*) FROM tool_test_cases WHERE suite_id = ts.id) as test_case_count
        FROM tool_suites ts WHERE ts.user_id = ? ORDER BY ts.updated_at DESC""",
        (user_id,),
    )


async def get_tool_suite(suite_id: str, user_id: str) -> dict | None:
    """Get full suite with tools_json. Scoped to user."""
    return await _db.fetch_one(
        "SELECT * FROM tool_suites WHERE id = ? AND user_id = ?",
        (suite_id, user_id),
    )


async def update_tool_suite(suite_id: str, user_id: str, name: str = None, description: str = None, tools_json: str = None, system_prompt: str = None) -> bool:
    """Update suite fields. Returns True if found and updated."""
    fields = []
    params = []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if description is not None:
        fields.append("description = ?")
        params.append(description)
    if tools_json is not None:
        fields.append("tools_json = ?")
        params.append(tools_json)
    if system_prompt is not None:
        fields.append("system_prompt = ?")
        params.append(system_prompt)
    if not fields:
        return False
    fields.append("updated_at = datetime('now')")
    params.extend([suite_id, user_id])
    count = await _db.execute_returning_rowcount(
        f"UPDATE tool_suites SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
        params,
    )
    return count > 0


async def delete_tool_suite(suite_id: str, user_id: str) -> bool:
    """Delete suite (CASCADE deletes test cases). Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM tool_suites WHERE id = ? AND user_id = ?",
        (suite_id, user_id),
    )
    return count > 0


# --- Tool Test Cases CRUD ---

async def get_test_cases(suite_id: str) -> list[dict]:
    """List all test cases for a suite."""
    rows = await _db.fetch_all(
        "SELECT * FROM tool_test_cases WHERE suite_id = ? ORDER BY created_at",
        (suite_id,),
    )
    # Convert SQLite integer (0/1) to boolean for JSON serialization
    for r in rows:
        r["should_call_tool"] = bool(r.get("should_call_tool", 1))
    return rows


async def create_test_case(suite_id: str, prompt: str, expected_tool: str | None, expected_params: str | None, param_scoring: str = "exact", multi_turn_config: str | None = None, scoring_config_json: str | None = None, should_call_tool: bool = True, category: str | None = None) -> str:
    """Create a single test case. Returns case_id."""
    case_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO tool_test_cases (id, suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config, scoring_config_json, should_call_tool, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (case_id, suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config, scoring_config_json, 1 if should_call_tool else 0, category),
    )
    return case_id


async def create_suite_with_cases(
    user_id: str, name: str, description: str, tools_json: str,
    cases: list[dict], system_prompt: str | None = None,
) -> str:
    """Create a suite and all test cases in one atomic transaction (CRIT-5).

    Returns suite_id. On any failure, the entire operation rolls back.
    Each case dict should have: prompt, expected_tool, expected_params,
    and optionally: param_scoring, multi_turn_config, scoring_config_json,
    should_call_tool, category.
    """
    async with aiosqlite.connect(_db._path()) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        suite_id = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO tool_suites (id, user_id, name, description, tools_json, system_prompt) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (suite_id, user_id, name, description, tools_json, system_prompt or ""),
        )
        for case in cases:
            case_id = uuid.uuid4().hex
            await conn.execute(
                "INSERT INTO tool_test_cases "
                "(id, suite_id, prompt, expected_tool, expected_params, param_scoring, "
                "multi_turn_config, scoring_config_json, should_call_tool, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case_id, suite_id, case["prompt"],
                    case.get("expected_tool"), case.get("expected_params"),
                    case.get("param_scoring", "exact"),
                    case.get("multi_turn_config"),
                    case.get("scoring_config_json"),
                    1 if case.get("should_call_tool", True) else 0,
                    case.get("category"),
                ),
            )
        await conn.commit()
        return suite_id


async def create_test_cases_batch(suite_id: str, cases: list[dict]) -> int:
    """Add multiple test cases to a suite in one atomic transaction.

    Returns count of cases created. On any failure, the entire batch rolls back.
    """
    async with aiosqlite.connect(_db._path()) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        for case in cases:
            case_id = uuid.uuid4().hex
            await conn.execute(
                "INSERT INTO tool_test_cases "
                "(id, suite_id, prompt, expected_tool, expected_params, param_scoring, "
                "multi_turn_config, scoring_config_json, should_call_tool, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case_id, suite_id, case["prompt"],
                    case.get("expected_tool"), case.get("expected_params"),
                    case.get("param_scoring", "exact"),
                    case.get("multi_turn_config"),
                    case.get("scoring_config_json"),
                    1 if case.get("should_call_tool", True) else 0,
                    case.get("category"),
                ),
            )
        await conn.commit()
        return len(cases)


async def update_test_case(case_id: str, suite_id: str, prompt: str = None, expected_tool: str = None, expected_params: str = None, param_scoring: str = None, multi_turn_config: str | None = None, scoring_config_json: str | None = None, should_call_tool: bool | None = None, category: str | None = None) -> bool:
    """Update a test case. Returns True if found."""
    fields = []
    params = []
    if prompt is not None:
        fields.append("prompt = ?")
        params.append(prompt)
    if expected_tool is not None:
        fields.append("expected_tool = ?")
        params.append(expected_tool)
    if expected_params is not None:
        fields.append("expected_params = ?")
        params.append(expected_params)
    if param_scoring is not None:
        fields.append("param_scoring = ?")
        params.append(param_scoring)
    if multi_turn_config is not None:
        fields.append("multi_turn_config = ?")
        params.append(multi_turn_config)
    if scoring_config_json is not None:
        fields.append("scoring_config_json = ?")
        params.append(scoring_config_json)
    if should_call_tool is not None:
        fields.append("should_call_tool = ?")
        params.append(1 if should_call_tool else 0)
    if category is not None:
        fields.append("category = ?")
        params.append(category)
    if not fields:
        return False
    params.extend([case_id, suite_id])
    count = await _db.execute_returning_rowcount(
        f"UPDATE tool_test_cases SET {', '.join(fields)} WHERE id = ? AND suite_id = ?",
        params,
    )
    return count > 0


async def delete_test_case(case_id: str, suite_id: str) -> bool:
    """Delete a test case. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM tool_test_cases WHERE id = ? AND suite_id = ?",
        (case_id, suite_id),
    )
    return count > 0


# --- Tool Eval Runs CRUD ---

async def save_tool_eval_run(user_id: str, suite_id: str, models_json: str, results_json: str, summary_json: str, temperature: float, config_json: str | None = None, experiment_id: str | None = None, profiles_json: str | None = None) -> str:
    """Save eval run. Returns run_id."""
    run_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO tool_eval_runs (id, user_id, suite_id, models_json, results_json, summary_json, temperature, config_json, experiment_id, profiles_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, user_id, suite_id, models_json, results_json, summary_json, temperature, config_json, experiment_id, profiles_json),
    )
    return run_id


async def get_tool_eval_runs(user_id: str, limit: int = 50) -> list[dict]:
    """List user's eval runs (exclude full results_json for list view).

    Includes the most recent completed judge report's grade/score for each run
    via a LEFT JOIN on judge_reports (M5).
    """
    return await _db.fetch_all(
        "SELECT r.id, r.suite_id, ts.name AS suite_name, r.models_json, r.summary_json, "
        "r.temperature, r.timestamp, r.config_json, "
        "j.overall_grade AS judge_grade, j.overall_score AS judge_score "
        "FROM tool_eval_runs r "
        "LEFT JOIN tool_suites ts ON ts.id = r.suite_id "
        "LEFT JOIN ("
        "  SELECT eval_run_id, overall_grade, overall_score, "
        "    ROW_NUMBER() OVER (PARTITION BY eval_run_id ORDER BY timestamp DESC) AS rn "
        "  FROM judge_reports WHERE status = 'completed'"
        ") j ON j.eval_run_id = r.id AND j.rn = 1 "
        "WHERE r.user_id = ? ORDER BY r.timestamp DESC LIMIT ?",
        (user_id, limit),
    )


async def get_tool_eval_run(run_id: str, user_id: str) -> dict | None:
    """Get full eval run including results_json. Includes suite_name via JOIN."""
    return await _db.fetch_one(
        "SELECT r.*, ts.name AS suite_name "
        "FROM tool_eval_runs r "
        "LEFT JOIN tool_suites ts ON ts.id = r.suite_id "
        "WHERE r.id = ? AND r.user_id = ?",
        (run_id, user_id),
    )


async def update_tool_eval_run(run_id: str, *, judge_explanations_json: str | None = None) -> bool:
    """Update fields on an existing tool_eval_run. Returns True if found."""
    fields = []
    params = []
    if judge_explanations_json is not None:
        fields.append("judge_explanations_json = ?")
        params.append(judge_explanations_json)
    if not fields:
        return False
    params.append(run_id)
    count = await _db.execute_returning_rowcount(
        f"UPDATE tool_eval_runs SET {', '.join(fields)} WHERE id = ?",
        params,
    )
    return count > 0


async def delete_tool_eval_run(run_id: str, user_id: str) -> bool:
    """Delete eval run. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM tool_eval_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )
    return count > 0


# --- Parameter Tuner CRUD ---

async def save_param_tune_run(
    user_id: str, suite_id: str, models_json: str,
    search_space_json: str, total_combos: int,
    experiment_id: str | None = None,
) -> str:
    """Create a new param tune run (status=running). Returns run_id."""
    run_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO param_tune_runs (id, user_id, suite_id, models_json, search_space_json, total_combos, experiment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, user_id, suite_id, models_json, search_space_json, total_combos, experiment_id),
    )
    return run_id


async def update_param_tune_run(
    run_id: str, user_id: str, *,
    results_json: str | None = None,
    best_config_json: str | None = None,
    best_score: float | None = None,
    completed_combos: int | None = None,
    status: str | None = None,
    duration_s: float | None = None,
    judge_scores_json: str | None = None,
    best_profile_id: str | None = None,
) -> bool:
    """Update a param tune run. Only non-None fields are updated."""
    updates = []
    values = []
    if results_json is not None:
        updates.append("results_json = ?")
        values.append(results_json)
    if best_config_json is not None:
        updates.append("best_config_json = ?")
        values.append(best_config_json)
    if best_score is not None:
        updates.append("best_score = ?")
        values.append(best_score)
    if completed_combos is not None:
        updates.append("completed_combos = ?")
        values.append(completed_combos)
    if status is not None:
        updates.append("status = ?")
        values.append(status)
    if duration_s is not None:
        updates.append("duration_s = ?")
        values.append(duration_s)
    if judge_scores_json is not None:
        updates.append("judge_scores_json = ?")
        values.append(judge_scores_json)
    if best_profile_id is not None:
        updates.append("best_profile_id = ?")
        values.append(best_profile_id)
    if not updates:
        return False
    values.extend([run_id, user_id])
    count = await _db.execute_returning_rowcount(
        f"UPDATE param_tune_runs SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    return count > 0


async def get_param_tune_runs(user_id: str, limit: int = 50) -> list[dict]:
    """List user's param tune runs (exclude full results_json for list view)."""
    return await _db.fetch_all(
        "SELECT r.id, r.suite_id, ts.name AS suite_name, r.models_json, r.total_combos, r.completed_combos, "
        "r.best_score, r.best_config_json, r.status, r.duration_s, r.timestamp "
        "FROM param_tune_runs r "
        "LEFT JOIN tool_suites ts ON ts.id = r.suite_id "
        "WHERE r.user_id = ? ORDER BY r.timestamp DESC LIMIT ?",
        (user_id, limit),
    )


async def get_param_tune_run(run_id: str, user_id: str) -> dict | None:
    """Get full param tune run including results_json. Includes suite_name via JOIN."""
    return await _db.fetch_one(
        "SELECT r.*, ts.name AS suite_name "
        "FROM param_tune_runs r "
        "LEFT JOIN tool_suites ts ON ts.id = r.suite_id "
        "WHERE r.id = ? AND r.user_id = ?",
        (run_id, user_id),
    )


async def delete_param_tune_run(run_id: str, user_id: str) -> bool:
    """Delete param tune run. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM param_tune_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )
    return count > 0


# --- Prompt Tuner CRUD ---

async def save_prompt_tune_run(
    user_id: str, suite_id: str, mode: str,
    target_models_json: str, meta_model: str, base_prompt: str | None,
    config_json: str, total_prompts: int,
    experiment_id: str | None = None,
    meta_provider_key: str | None = None,
    meta_model_id: str | None = None,
) -> str:
    """Create a new prompt tune run (status=running). Returns run_id.

    Args:
        meta_model: Legacy litellm model ID string (kept for backward compat, ignored if table rebuilt).
        meta_model_id: FK to models table (preferred).
    """
    run_id = uuid.uuid4().hex
    # Use the new schema (without meta_model/meta_provider_key columns) if available,
    # fall back to old schema for databases not yet migrated.
    try:
        await _db.execute(
            "INSERT INTO prompt_tune_runs (id, user_id, suite_id, mode, "
            "target_models_json, base_prompt, config_json, total_prompts, experiment_id, meta_model_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, user_id, suite_id, mode,
             target_models_json, base_prompt, config_json, total_prompts, experiment_id, meta_model_id),
        )
    except Exception:
        # Pre-migration 600 schema still has meta_model and meta_provider_key columns
        await _db.execute(
            "INSERT INTO prompt_tune_runs (id, user_id, suite_id, mode, "
            "target_models_json, meta_model, base_prompt, config_json, total_prompts, "
            "experiment_id, meta_provider_key, meta_model_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, user_id, suite_id, mode,
             target_models_json, meta_model, base_prompt, config_json, total_prompts,
             experiment_id, meta_provider_key, meta_model_id),
        )
    return run_id


async def update_prompt_tune_run(
    run_id: str, user_id: str, *,
    generations_json: str | None = None,
    best_prompt: str | None = None,
    best_score: float | None = None,
    best_prompt_origin_json: str | None = None,
    best_prompt_version_id: str | None = None,
    completed_prompts: int | None = None,
    status: str | None = None,
    duration_s: float | None = None,
) -> bool:
    """Update a prompt tune run. Only non-None fields are updated.

    Note: best_prompt is accepted for backward compat but silently ignored
    after migration 600 drops the column. Use best_prompt_version_id instead.
    """
    updates = []
    values = []
    if generations_json is not None:
        updates.append("generations_json = ?")
        values.append(generations_json)
    if best_score is not None:
        updates.append("best_score = ?")
        values.append(best_score)
    if best_prompt_origin_json is not None:
        updates.append("best_prompt_origin_json = ?")
        values.append(best_prompt_origin_json)
    if best_prompt_version_id is not None:
        updates.append("best_prompt_version_id = ?")
        values.append(best_prompt_version_id)
    if completed_prompts is not None:
        updates.append("completed_prompts = ?")
        values.append(completed_prompts)
    if status is not None:
        updates.append("status = ?")
        values.append(status)
    if duration_s is not None:
        updates.append("duration_s = ?")
        values.append(duration_s)
    # best_prompt: try to write if column still exists (pre-migration 600)
    if best_prompt is not None:
        updates.append("best_prompt = ?")
        values.append(best_prompt)
    if not updates:
        return False
    values.extend([run_id, user_id])
    try:
        count = await _db.execute_returning_rowcount(
            f"UPDATE prompt_tune_runs SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
            values,
        )
    except Exception:
        # Column best_prompt may have been dropped — retry without it
        if best_prompt is not None:
            try:
                idx = updates.index("best_prompt = ?")
                updates.pop(idx)
                values.pop(idx)
            except ValueError:
                raise
            if not updates:
                return False
            count = await _db.execute_returning_rowcount(
                f"UPDATE prompt_tune_runs SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                values,
            )
        else:
            raise
    return count > 0


async def get_prompt_tune_runs(user_id: str, limit: int = 50) -> list[dict]:
    """List user's prompt tune runs (exclude large JSON for list view)."""
    return await _db.fetch_all(
        "SELECT r.id, r.suite_id, ts.name AS suite_name, r.mode, r.target_models_json, "
        "r.meta_model_id, r.best_prompt_version_id, "
        "r.best_score, r.status, r.total_prompts, r.completed_prompts, r.duration_s, r.timestamp "
        "FROM prompt_tune_runs r "
        "LEFT JOIN tool_suites ts ON ts.id = r.suite_id "
        "WHERE r.user_id = ? ORDER BY r.timestamp DESC LIMIT ?",
        (user_id, limit),
    )


async def get_prompt_tune_run(run_id: str, user_id: str) -> dict | None:
    """Get full prompt tune run including generations_json. Includes suite_name via JOIN."""
    return await _db.fetch_one(
        "SELECT r.*, ts.name AS suite_name "
        "FROM prompt_tune_runs r "
        "LEFT JOIN tool_suites ts ON ts.id = r.suite_id "
        "WHERE r.id = ? AND r.user_id = ?",
        (run_id, user_id),
    )


async def delete_prompt_tune_run(run_id: str, user_id: str) -> bool:
    """Delete prompt tune run. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM prompt_tune_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )
    return count > 0


# --- Judge Reports CRUD ---


async def save_judge_report(
    user_id: str,
    judge_model: str,
    mode: str,
    eval_run_id: str | None = None,
    eval_run_id_b: str | None = None,
    experiment_id: str | None = None,
    parent_report_id: str | None = None,
    version: int = 1,
    instructions_json: str | None = None,
    judge_model_id: str | None = None,
) -> str:
    """Create a new judge report (status=running). Returns report id.

    Args:
        judge_model: Legacy litellm model ID string (kept for backward compat).
        judge_model_id: FK to models table (preferred, used after migration 600).
    """
    try:
        # Post-migration 600: judge_model column is dropped, use judge_model_id
        return await _db.execute_returning_id(
            "INSERT INTO judge_reports (user_id, eval_run_id, eval_run_id_b, judge_model_id, mode, "
            "experiment_id, parent_report_id, version, instructions_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, eval_run_id, eval_run_id_b, judge_model_id, mode,
             experiment_id, parent_report_id, version, instructions_json),
            id_query="SELECT id FROM judge_reports WHERE rowid = ?",
        )
    except Exception:
        # Pre-migration 600: judge_model column still exists
        return await _db.execute_returning_id(
            "INSERT INTO judge_reports (user_id, eval_run_id, eval_run_id_b, judge_model, mode, "
            "experiment_id, parent_report_id, version, instructions_json, judge_model_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, eval_run_id, eval_run_id_b, judge_model, mode,
             experiment_id, parent_report_id, version, instructions_json, judge_model_id),
            id_query="SELECT id FROM judge_reports WHERE rowid = ?",
        )


async def update_judge_report(report_id: str, **fields) -> None:
    """Update judge report fields (verdicts_json, report_json, overall_grade, overall_score, status)."""
    allowed = {"verdicts_json", "report_json", "overall_grade", "overall_score", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [report_id]
    await _db.execute(f"UPDATE judge_reports SET {set_clause} WHERE id = ?", values)


async def get_judge_reports(user_id: str, limit: int = 50) -> list[dict]:
    """List user's judge reports (exclude full verdicts_json for list view)."""
    return await _db.fetch_all(
        "SELECT jr.id, jr.eval_run_id, jr.eval_run_id_b, jr.judge_model_id, "
        "m.litellm_id AS judge_model, m.display_name AS judge_model_display, "
        "jr.mode, jr.overall_grade, jr.overall_score, jr.status, jr.timestamp, "
        "jr.parent_report_id, jr.version, jr.instructions_json "
        "FROM judge_reports jr "
        "LEFT JOIN models m ON jr.judge_model_id = m.id "
        "WHERE jr.user_id = ? ORDER BY jr.timestamp DESC LIMIT ?",
        (user_id, limit),
    )


async def get_judge_report(report_id: str, user_id: str) -> dict | None:
    """Get full judge report including verdicts_json and report_json."""
    return await _db.fetch_one(
        "SELECT jr.*, m.litellm_id AS judge_model, m.display_name AS judge_model_display "
        "FROM judge_reports jr "
        "LEFT JOIN models m ON jr.judge_model_id = m.id "
        "WHERE jr.id = ? AND jr.user_id = ?",
        (report_id, user_id),
    )


async def get_judge_report_for_eval(eval_run_id: str, user_id: str) -> dict | None:
    """Get the most recent completed judge report for a tool eval run."""
    return await _db.fetch_one(
        "SELECT jr.*, m.litellm_id AS judge_model, m.display_name AS judge_model_display "
        "FROM judge_reports jr "
        "LEFT JOIN models m ON jr.judge_model_id = m.id "
        "WHERE jr.eval_run_id = ? AND jr.user_id = ? AND jr.status = 'completed' "
        "ORDER BY jr.timestamp DESC LIMIT 1",
        (eval_run_id, user_id),
    )


async def delete_judge_report(report_id: str, user_id: str) -> bool:
    """Delete judge report. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM judge_reports WHERE id = ? AND user_id = ?",
        (report_id, user_id),
    )
    return count > 0


async def get_judge_report_versions(report_id: str, user_id: str) -> list[dict]:
    """Get all versions linked to a root report (including the root itself).

    Given any report_id in a version chain, find the root and return all versions.
    This is a simple 2-level model: root + children. Re-runs always point to
    a root report, not to other re-runs.
    Returns list of dicts ordered by version ASC.
    """
    # Step 1: Get the given report to determine the root
    report = await _db.fetch_one(
        "SELECT id, parent_report_id FROM judge_reports WHERE id = ? AND user_id = ?",
        (report_id, user_id),
    )
    if not report:
        return []

    # Step 2: Find root -- if this report has a parent, that parent is the root
    root_id = report["parent_report_id"] if report["parent_report_id"] else report["id"]

    # Step 3: Get all reports in the chain (root + children pointing to root)
    return await _db.fetch_all(
        "SELECT jr.id, jr.eval_run_id, jr.judge_model_id, "
        "m.litellm_id AS judge_model, m.display_name AS judge_model_display, "
        "jr.mode, jr.overall_grade, jr.overall_score, "
        "jr.status, jr.timestamp, jr.parent_report_id, jr.version, jr.instructions_json "
        "FROM judge_reports jr "
        "LEFT JOIN models m ON jr.judge_model_id = m.id "
        "WHERE jr.user_id = ? AND (jr.id = ? OR jr.parent_report_id = ?) "
        "ORDER BY jr.version ASC",
        (user_id, root_id, root_id),
    )


# --- Experiment CRUD ---


async def create_experiment(
    user_id: str, name: str, suite_id: str,
    description: str = "",
    suite_snapshot_json: str | None = None,
    baseline_eval_id: str | None = None,
    baseline_score: float | None = None,
) -> str:
    """Create a new experiment. Returns experiment id."""
    exp_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO experiments "
        "(id, user_id, name, description, suite_id, "
        "suite_snapshot_json, baseline_eval_id, baseline_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (exp_id, user_id, name, description, suite_id,
         suite_snapshot_json, baseline_eval_id, baseline_score),
    )
    return exp_id


async def get_experiment(exp_id: str, user_id: str) -> dict | None:
    """Get experiment by id."""
    return await _db.fetch_one(
        "SELECT * FROM experiments WHERE id = ? AND user_id = ?",
        (exp_id, user_id),
    )


async def get_experiments(user_id: str, limit: int = 50) -> list[dict]:
    """List user's active experiments with run_count and suite_name."""
    return await _db.fetch_all(
        "SELECT e.id, e.name, e.description, e.suite_id, "
        "e.baseline_score, e.best_score, e.best_source, e.status, "
        "e.created_at, e.updated_at, "
        "ts.name AS suite_name, "
        "(SELECT COUNT(*) FROM tool_eval_runs r WHERE r.experiment_id = e.id) "
        "+ (SELECT COUNT(*) FROM param_tune_runs p WHERE p.experiment_id = e.id) "
        "+ (SELECT COUNT(*) FROM prompt_tune_runs pt WHERE pt.experiment_id = e.id) "
        "+ (SELECT COUNT(*) FROM judge_reports j WHERE j.experiment_id = e.id) "
        "AS run_count "
        "FROM experiments e "
        "LEFT JOIN tool_suites ts ON ts.id = e.suite_id "
        "WHERE e.user_id = ? AND e.status = 'active' "
        "ORDER BY e.updated_at DESC LIMIT ?",
        (user_id, limit),
    )


async def update_experiment(
    exp_id: str, user_id: str, **fields
) -> bool:
    """Update experiment fields. Only non-None fields are updated."""
    allowed = {
        "name", "description", "baseline_eval_id", "baseline_score",
        "best_config_json", "best_score", "best_source",
        "best_source_id", "status", "suite_snapshot_json",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = "datetime('now')"
    set_parts = []
    values = []
    for k, v in updates.items():
        if v == "datetime('now')":
            set_parts.append(f"{k} = datetime('now')")
        else:
            set_parts.append(f"{k} = ?")
            values.append(v)
    values.extend([exp_id, user_id])
    count = await _db.execute_returning_rowcount(
        f"UPDATE experiments SET {', '.join(set_parts)} "
        "WHERE id = ? AND user_id = ?",
        values,
    )
    return count > 0


async def delete_experiment(exp_id: str, user_id: str) -> bool:
    """Delete experiment. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM experiments WHERE id = ? AND user_id = ?",
        (exp_id, user_id),
    )
    return count > 0


async def get_experiment_timeline(
    exp_id: str, user_id: str
) -> list[dict]:
    """Get all runs linked to an experiment, ordered by timestamp.

    Returns a flat list of dicts with 'type' discriminator.
    """
    entries = []
    # Multi-query read within single connection for efficiency
    async with aiosqlite.connect(_db._path()) as conn:
        conn.row_factory = aiosqlite.Row

        # Eval runs
        cursor = await conn.execute(
            "SELECT id, timestamp, summary_json, config_json "
            "FROM tool_eval_runs "
            "WHERE experiment_id = ? AND user_id = ?",
            (exp_id, user_id),
        )
        for row in await cursor.fetchall():
            entries.append({
                "type": "eval", "id": row["id"],
                "timestamp": row["timestamp"],
                "summary_json": row["summary_json"],
                "config_json": row["config_json"],
            })

        # Param tune runs
        cursor = await conn.execute(
            "SELECT id, timestamp, best_score, best_config_json, status "
            "FROM param_tune_runs "
            "WHERE experiment_id = ? AND user_id = ?",
            (exp_id, user_id),
        )
        for row in await cursor.fetchall():
            entries.append({
                "type": "param_tune", "id": row["id"],
                "timestamp": row["timestamp"],
                "best_score": row["best_score"],
                "best_config_json": row["best_config_json"],
                "status": row["status"],
            })

        # Prompt tune runs
        cursor = await conn.execute(
            "SELECT ptr.id, ptr.timestamp, ptr.best_score, ptr.best_prompt_version_id, "
            "pv.prompt_text AS best_prompt, ptr.status "
            "FROM prompt_tune_runs ptr "
            "LEFT JOIN prompt_versions pv ON ptr.best_prompt_version_id = pv.id "
            "WHERE ptr.experiment_id = ? AND ptr.user_id = ?",
            (exp_id, user_id),
        )
        for row in await cursor.fetchall():
            entries.append({
                "type": "prompt_tune", "id": row["id"],
                "timestamp": row["timestamp"],
                "best_score": row["best_score"],
                "best_prompt": row["best_prompt"],
                "status": row["status"],
            })

        # Judge reports
        cursor = await conn.execute(
            "SELECT id, timestamp, overall_grade, overall_score, "
            "mode, eval_run_id, status "
            "FROM judge_reports "
            "WHERE experiment_id = ? AND user_id = ?",
            (exp_id, user_id),
        )
        for row in await cursor.fetchall():
            entries.append({
                "type": "judge", "id": row["id"],
                "timestamp": row["timestamp"],
                "overall_grade": row["overall_grade"],
                "overall_score": row["overall_score"],
                "mode": row["mode"],
                "eval_run_id": row["eval_run_id"],
                "status": row["status"],
            })

    # Sort by timestamp
    entries.sort(key=lambda e: e["timestamp"])
    return entries


async def cleanup_stale_judge_reports(minutes: int = 30) -> int:
    """Mark any 'running' judge reports older than `minutes` as 'error'.

    Returns number of rows updated. Called on startup to recover orphaned reports.
    """
    return await _db.execute_returning_rowcount(
        "UPDATE judge_reports SET status = 'error' "
        "WHERE status = 'running' AND timestamp < datetime('now', ?)",
        (f"-{minutes} minutes",),
    )


async def cleanup_stale_param_tune_runs(minutes: int = 30) -> int:
    """Mark any 'running' param tune runs older than `minutes` as 'interrupted'.

    Returns number of rows updated. Called on startup to recover orphaned runs.
    """
    return await _db.execute_returning_rowcount(
        "UPDATE param_tune_runs SET status = 'interrupted' "
        "WHERE status = 'running' AND timestamp < datetime('now', ?)",
        (f"-{minutes} minutes",),
    )


async def cleanup_stale_prompt_tune_runs(minutes: int = 30) -> int:
    """Mark any 'running' prompt tune runs older than `minutes` as 'interrupted'.

    Returns number of rows updated. Called on startup to recover orphaned runs.
    """
    return await _db.execute_returning_rowcount(
        "UPDATE prompt_tune_runs SET status = 'interrupted' "
        "WHERE status = 'running' AND timestamp < datetime('now', ?)",
        (f"-{minutes} minutes",),
    )


async def cleanup_old_jobs(retention_days: int = 180) -> int:
    """Delete terminal jobs older than retention_days. Returns count deleted."""
    return await _db.execute_returning_rowcount(
        "DELETE FROM jobs WHERE status IN ('done', 'failed', 'cancelled', 'interrupted') "
        "AND completed_at < datetime('now', ?)",
        (f'-{retention_days} days',),
    )


async def cleanup_old_password_reset_tokens() -> int:
    """Delete expired or used password reset tokens."""
    return await _db.execute_returning_rowcount(
        "DELETE FROM password_reset_tokens WHERE used = 1 OR expires_at < datetime('now')"
    )


# --- Analytics Queries ---

_PERIOD_MAP = {
    "7d": "-7 days",
    "30d": "-30 days",
    "90d": "-90 days",
    "all": None,
}


def _period_filter(period: str) -> tuple[str, list]:
    """Return (SQL WHERE clause fragment, params) for a period filter on `timestamp`."""
    interval = _PERIOD_MAP.get(period)
    if interval:
        return "AND timestamp > datetime('now', ?)", [interval]
    return "", []


async def get_analytics_benchmark_runs(user_id: str, period: str = "all") -> list[dict]:
    """Return benchmark runs for a user within the given period.

    Each row includes id, timestamp, prompt, results_json.
    The caller parses results_json and aggregates in Python.
    """
    where_extra, params = _period_filter(period)
    return await _db.fetch_all(
        f"SELECT id, timestamp, prompt, results_json "
        f"FROM benchmark_runs WHERE user_id = ? {where_extra} "
        f"ORDER BY timestamp DESC",
        [user_id] + params,
    )


async def get_analytics_tool_eval_runs(user_id: str, period: str = "all") -> list[dict]:
    """Return tool eval runs for a user within the given period.

    Each row includes id, timestamp, summary_json.
    """
    where_extra, params = _period_filter(period)
    return await _db.fetch_all(
        f"SELECT id, timestamp, summary_json "
        f"FROM tool_eval_runs WHERE user_id = ? {where_extra} "
        f"ORDER BY timestamp DESC",
        [user_id] + params,
    )


# --- Audit Log ---

async def cleanup_audit_log(retention_days: int = 90):
    """Delete audit entries older than retention_days."""
    try:
        await _db.execute(
            "DELETE FROM audit_log WHERE timestamp < datetime('now', ?)",
            (f'-{retention_days} days',),
        )
    except Exception:
        logger.exception("Failed to clean up audit log entries")


# --- Schedules CRUD ---

async def create_schedule(
    user_id: str, name: str, prompt: str, models_json: str,
    max_tokens: int, temperature: float, interval_hours: int, next_run: str,
    prompt_version_id: str | None = None,
) -> str:
    """Create a scheduled benchmark. Returns schedule ID."""
    schedule_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO schedules (id, user_id, name, prompt, models_json, max_tokens, temperature, interval_hours, next_run, prompt_version_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (schedule_id, user_id, name, prompt, models_json, max_tokens, temperature, interval_hours, next_run, prompt_version_id),
    )
    return schedule_id


async def get_user_schedules(user_id: str) -> list[dict]:
    """List all schedules for a user."""
    return await _db.fetch_all(
        "SELECT * FROM schedules WHERE user_id = ? ORDER BY created DESC",
        (user_id,),
    )


async def get_schedule(schedule_id: str, user_id: str) -> dict | None:
    """Get a specific schedule (scoped to user)."""
    return await _db.fetch_one(
        "SELECT * FROM schedules WHERE id = ? AND user_id = ?",
        (schedule_id, user_id),
    )


async def update_schedule(schedule_id: str, user_id: str, **kwargs) -> bool:
    """Update schedule fields. Returns True if found and updated."""
    allowed = {"name", "prompt", "models_json", "max_tokens", "temperature", "interval_hours", "enabled", "next_run", "prompt_version_id"}
    fields = []
    params = []
    for key, value in kwargs.items():
        if key in allowed and value is not None:
            fields.append(f"{key} = ?")
            params.append(value)
    if not fields:
        return False
    params.extend([schedule_id, user_id])
    count = await _db.execute_returning_rowcount(
        f"UPDATE schedules SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
        params,
    )
    return count > 0


async def delete_schedule(schedule_id: str, user_id: str) -> bool:
    """Delete a schedule. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM schedules WHERE id = ? AND user_id = ?",
        (schedule_id, user_id),
    )
    return count > 0


async def get_due_schedules() -> list[dict]:
    """Get all enabled schedules where next_run <= now. Used by background scheduler."""
    return await _db.fetch_all(
        "SELECT * FROM schedules WHERE enabled = 1 AND next_run <= datetime('now')"
    )


async def update_schedule_after_run(schedule_id: str, last_run: str, next_run: str):
    """Update last_run and next_run after a scheduled benchmark completes."""
    await _db.execute(
        "UPDATE schedules SET last_run = ?, next_run = ? WHERE id = ?",
        (last_run, next_run, schedule_id),
    )


# --- Jobs (Process Tracker) CRUD ---


async def create_job(
    job_id: str,
    user_id: str,
    job_type: str,
    status: str,
    params_json: str,
    timeout_seconds: int = 7200,
    progress_detail: str = "",
) -> dict:
    """Create a new job record. Returns the job dict."""
    row = await _db.execute_returning_row(
        [("INSERT INTO jobs (id, user_id, job_type, status, params_json, timeout_seconds, progress_detail) "
          "VALUES (?, ?, ?, ?, ?, ?, ?)",
          (job_id, user_id, job_type, status, params_json, timeout_seconds, progress_detail))],
        "SELECT * FROM jobs WHERE id = ?",
        (job_id,),
    )
    return row


async def get_job(job_id: str) -> dict | None:
    """Get a single job by ID."""
    return await _db.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))


async def update_job_started(job_id: str, started_at: str, timeout_at: str):
    """Mark a job as running with start time and timeout deadline."""
    await _db.execute(
        "UPDATE jobs SET status = 'running', started_at = ?, timeout_at = ? WHERE id = ?",
        (started_at, timeout_at, job_id),
    )


async def update_job_progress(job_id: str, progress_pct: int, progress_detail: str = ""):
    """Update progress fields for a running job."""
    await _db.execute(
        "UPDATE jobs SET progress_pct = ?, progress_detail = ? WHERE id = ?",
        (progress_pct, progress_detail, job_id),
    )


async def update_job_status(
    job_id: str,
    status: str,
    completed_at: str | None = None,
    result_ref: str | None = None,
    error_msg: str | None = None,
):
    """Update job status and optional terminal fields (completed_at, result_ref, error_msg)."""
    fields = ["status = ?"]
    values: list = [status]
    if completed_at is not None:
        fields.append("completed_at = ?")
        values.append(completed_at)
    if result_ref is not None:
        fields.append("result_ref = ?")
        values.append(result_ref)
    if error_msg is not None:
        fields.append("error_msg = ?")
        values.append(error_msg)
    values.append(job_id)
    await _db.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)


async def set_job_result_ref(job_id: str, result_ref: str):
    """Set result_ref on a job without changing its status.

    Used to store the tune_id (or other result reference) early, so the
    frontend can discover it on reconnection before the job completes.
    """
    await _db.execute(
        "UPDATE jobs SET result_ref = ? WHERE id = ?",
        (result_ref, job_id),
    )


async def get_user_active_jobs(user_id: str) -> list[dict]:
    """Get jobs with status in ('pending', 'queued', 'running') for a user, oldest first."""
    return await _db.fetch_all(
        "SELECT * FROM jobs WHERE user_id = ? AND status IN ('pending', 'queued', 'running') "
        "ORDER BY created_at ASC",
        (user_id,),
    )


async def get_user_recent_jobs(user_id: str, limit: int = 10) -> list[dict]:
    """Get recent completed/failed/cancelled/interrupted jobs, newest first."""
    return await _db.fetch_all(
        "SELECT * FROM jobs WHERE user_id = ? AND status IN ('done', 'failed', 'cancelled', 'interrupted') "
        "ORDER BY completed_at DESC LIMIT ?",
        (user_id, limit),
    )


async def get_user_jobs(user_id: str, status: str | None = None, limit: int = 20) -> list[dict]:
    """List jobs for a user with optional status filter. Newest first."""
    query = "SELECT * FROM jobs WHERE user_id = ?"
    params: list = [user_id]
    if status:
        # Support comma-separated status values
        statuses = [s.strip() for s in status.split(",")]
        placeholders = ", ".join("?" for _ in statuses)
        query += f" AND status IN ({placeholders})"
        params.extend(statuses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return await _db.fetch_all(query, params)


async def get_next_queued_job(user_id: str) -> dict | None:
    """Get the oldest queued job for a user (FIFO)."""
    return await _db.fetch_one(
        "SELECT * FROM jobs WHERE user_id = ? AND status = 'queued' "
        "ORDER BY created_at ASC LIMIT 1",
        (user_id,),
    )


async def mark_interrupted_jobs() -> int:
    """On startup, mark all running/pending/queued jobs as interrupted. Returns count."""
    return await _db.execute_returning_rowcount(
        "UPDATE jobs SET status = 'interrupted', completed_at = datetime('now') "
        "WHERE status IN ('running', 'pending', 'queued')"
    )


async def get_timed_out_jobs() -> list[dict]:
    """Get jobs where status='running' and timeout_at < now."""
    return await _db.fetch_all(
        "SELECT * FROM jobs WHERE status = 'running' AND timeout_at IS NOT NULL "
        "AND timeout_at < datetime('now')"
    )


async def get_all_active_jobs() -> list[dict]:
    """Admin: get all active jobs across all users with user email."""
    return await _db.fetch_all(
        "SELECT j.*, u.email as user_email FROM jobs j "
        "JOIN users u ON j.user_id = u.id "
        "WHERE j.status IN ('pending', 'queued', 'running') "
        "ORDER BY j.created_at ASC"
    )


# --- Public Leaderboard CRUD (2D) ---

async def upsert_leaderboard_entry(
    model_name: str, provider: str,
    tool_accuracy_pct: float,
    param_accuracy_pct: float,
    irrel_accuracy_pct: float | None,
    sample_count: int,
    throughput_tps: float | None = None,
    ttft_ms: float | None = None,
    model_db_id: str | None = None,
) -> None:
    """Insert or update a leaderboard entry, aggregating with existing data.

    Uses INSERT ... ON CONFLICT DO UPDATE with SQL-level weighted averaging
    to avoid the read-then-write race condition (CRIT-1).

    After migration 600, the table uses model_db_id as the unique key instead
    of (model_name, provider). Falls back to the old schema if needed.
    """
    async with aiosqlite.connect(_db._path()) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        try:
            # Post-migration 600: model_db_id is the unique key
            await conn.execute("""
                INSERT INTO public_leaderboard
                    (id, model_db_id, tool_accuracy_pct, param_accuracy_pct,
                     irrel_accuracy_pct, throughput_tps, ttft_ms, sample_count, last_updated)
                VALUES (lower(hex(randomblob(16))), ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(model_db_id) DO UPDATE SET
                    tool_accuracy_pct = round(
                        (public_leaderboard.tool_accuracy_pct * public_leaderboard.sample_count
                         + excluded.tool_accuracy_pct * excluded.sample_count)
                        / (public_leaderboard.sample_count + excluded.sample_count), 2),
                    param_accuracy_pct = round(
                        (public_leaderboard.param_accuracy_pct * public_leaderboard.sample_count
                         + excluded.param_accuracy_pct * excluded.sample_count)
                        / (public_leaderboard.sample_count + excluded.sample_count), 2),
                    irrel_accuracy_pct = CASE
                        WHEN excluded.irrel_accuracy_pct IS NOT NULL AND public_leaderboard.irrel_accuracy_pct IS NOT NULL
                        THEN round(
                            (public_leaderboard.irrel_accuracy_pct * public_leaderboard.sample_count
                             + excluded.irrel_accuracy_pct * excluded.sample_count)
                            / (public_leaderboard.sample_count + excluded.sample_count), 2)
                        WHEN excluded.irrel_accuracy_pct IS NOT NULL
                        THEN excluded.irrel_accuracy_pct
                        ELSE public_leaderboard.irrel_accuracy_pct
                    END,
                    throughput_tps = CASE
                        WHEN excluded.throughput_tps IS NOT NULL AND public_leaderboard.throughput_tps IS NOT NULL
                        THEN (public_leaderboard.throughput_tps * public_leaderboard.sample_count
                              + excluded.throughput_tps * excluded.sample_count)
                             / (public_leaderboard.sample_count + excluded.sample_count)
                        WHEN excluded.throughput_tps IS NOT NULL
                        THEN excluded.throughput_tps
                        ELSE public_leaderboard.throughput_tps
                    END,
                    ttft_ms = CASE
                        WHEN excluded.ttft_ms IS NOT NULL AND public_leaderboard.ttft_ms IS NOT NULL
                        THEN (public_leaderboard.ttft_ms * public_leaderboard.sample_count
                              + excluded.ttft_ms * excluded.sample_count)
                             / (public_leaderboard.sample_count + excluded.sample_count)
                        WHEN excluded.ttft_ms IS NOT NULL
                        THEN excluded.ttft_ms
                        ELSE public_leaderboard.ttft_ms
                    END,
                    sample_count = public_leaderboard.sample_count + excluded.sample_count,
                    last_updated = datetime('now')
            """, (model_db_id, round(tool_accuracy_pct, 2), round(param_accuracy_pct, 2),
                  irrel_accuracy_pct, throughput_tps, ttft_ms, sample_count))
        except Exception:
            # Pre-migration 600: model_name + provider is the unique key
            await conn.execute("""
                INSERT INTO public_leaderboard
                    (id, model_name, provider, tool_accuracy_pct, param_accuracy_pct,
                     irrel_accuracy_pct, throughput_tps, ttft_ms, sample_count, last_updated)
                VALUES (lower(hex(randomblob(16))), ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(model_name, provider) DO UPDATE SET
                    tool_accuracy_pct = round(
                        (public_leaderboard.tool_accuracy_pct * public_leaderboard.sample_count
                         + excluded.tool_accuracy_pct * excluded.sample_count)
                        / (public_leaderboard.sample_count + excluded.sample_count), 2),
                    param_accuracy_pct = round(
                        (public_leaderboard.param_accuracy_pct * public_leaderboard.sample_count
                         + excluded.param_accuracy_pct * excluded.sample_count)
                        / (public_leaderboard.sample_count + excluded.sample_count), 2),
                    irrel_accuracy_pct = CASE
                        WHEN excluded.irrel_accuracy_pct IS NOT NULL AND public_leaderboard.irrel_accuracy_pct IS NOT NULL
                        THEN round(
                            (public_leaderboard.irrel_accuracy_pct * public_leaderboard.sample_count
                             + excluded.irrel_accuracy_pct * excluded.sample_count)
                            / (public_leaderboard.sample_count + excluded.sample_count), 2)
                        WHEN excluded.irrel_accuracy_pct IS NOT NULL
                        THEN excluded.irrel_accuracy_pct
                        ELSE public_leaderboard.irrel_accuracy_pct
                    END,
                    throughput_tps = CASE
                        WHEN excluded.throughput_tps IS NOT NULL AND public_leaderboard.throughput_tps IS NOT NULL
                        THEN (public_leaderboard.throughput_tps * public_leaderboard.sample_count
                              + excluded.throughput_tps * excluded.sample_count)
                             / (public_leaderboard.sample_count + excluded.sample_count)
                        WHEN excluded.throughput_tps IS NOT NULL
                        THEN excluded.throughput_tps
                        ELSE public_leaderboard.throughput_tps
                    END,
                    ttft_ms = CASE
                        WHEN excluded.ttft_ms IS NOT NULL AND public_leaderboard.ttft_ms IS NOT NULL
                        THEN (public_leaderboard.ttft_ms * public_leaderboard.sample_count
                              + excluded.ttft_ms * excluded.sample_count)
                             / (public_leaderboard.sample_count + excluded.sample_count)
                        WHEN excluded.ttft_ms IS NOT NULL
                        THEN excluded.ttft_ms
                        ELSE public_leaderboard.ttft_ms
                    END,
                    sample_count = public_leaderboard.sample_count + excluded.sample_count,
                    last_updated = datetime('now')
            """, (model_name, provider, round(tool_accuracy_pct, 2), round(param_accuracy_pct, 2),
                  irrel_accuracy_pct, throughput_tps, ttft_ms, sample_count))
        await conn.commit()


async def get_leaderboard() -> list[dict]:
    """Return public leaderboard entries sorted by tool accuracy descending."""
    try:
        # Post-migration 600: resolve model_name/provider from FK
        return await _db.fetch_all(
            "SELECT m.display_name AS model_name, p.name AS provider, "
            "lb.tool_accuracy_pct, lb.param_accuracy_pct, "
            "lb.irrel_accuracy_pct, lb.throughput_tps, lb.ttft_ms, lb.sample_count, lb.last_updated "
            "FROM public_leaderboard lb "
            "JOIN models m ON lb.model_db_id = m.id "
            "JOIN providers p ON m.provider_id = p.id "
            "ORDER BY lb.tool_accuracy_pct DESC, lb.param_accuracy_pct DESC"
        )
    except Exception:
        # Pre-migration 600: model_name and provider are direct columns
        return await _db.fetch_all(
            "SELECT model_name, provider, tool_accuracy_pct, param_accuracy_pct, "
            "irrel_accuracy_pct, throughput_tps, ttft_ms, sample_count, last_updated "
            "FROM public_leaderboard ORDER BY tool_accuracy_pct DESC, param_accuracy_pct DESC"
        )


async def get_user_leaderboard_opt_in(user_id: str) -> bool:
    """Return True if user has opted in to leaderboard contributions."""
    row = await _db.fetch_one(
        "SELECT leaderboard_opt_in FROM users WHERE id = ?", (user_id,)
    )
    return bool(row["leaderboard_opt_in"]) if row else False


async def set_user_leaderboard_opt_in(user_id: str, opt_in: bool) -> None:
    """Set user's leaderboard opt-in preference."""
    await _db.execute(
        "UPDATE users SET leaderboard_opt_in = ? WHERE id = ?",
        (1 if opt_in else 0, user_id),
    )


async def save_param_tune_run_with_mode(
    user_id: str, suite_id: str, models_json: str,
    search_space_json: str, total_combos: int,
    optimization_mode: str = "grid",
    experiment_id: str | None = None,
) -> str:
    """Create a new param tune run with optimization mode. Returns run_id."""
    run_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO param_tune_runs (id, user_id, suite_id, models_json, "
        "search_space_json, total_combos, optimization_mode, experiment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, user_id, suite_id, models_json, search_space_json,
         total_combos, optimization_mode, experiment_id),
    )
    return run_id


async def get_user_rate_limit(user_id: str) -> dict | None:
    """Get rate limit row for a user (includes max_concurrent). Returns None if no custom limit."""
    return await _db.fetch_one(
        "SELECT * FROM rate_limits WHERE user_id = ?",
        (user_id,),
    )


async def get_user_active_job_count(user_id: str) -> int:
    """Count jobs with status IN ('pending', 'queued', 'running') for a user."""
    result = await _db.fetch_one(
        "SELECT COUNT(*) as cnt FROM jobs WHERE user_id = ? AND status IN ('pending', 'queued', 'running')",
        (user_id,)
    )
    return result["cnt"] if result else 0


async def get_user_recent_job_count(user_id: str, hours: int = 1) -> int:
    """Count jobs created in the last N hours for a user."""
    result = await _db.fetch_one(
        "SELECT COUNT(*) as cnt FROM jobs WHERE user_id = ? AND created_at > datetime('now', ?)",
        (user_id, f'-{hours} hours')
    )
    return result["cnt"] if result else 0


# --- Model Profiles CRUD ---

MAX_PROFILES_PER_MODEL = 20


async def get_profiles(user_id: str, model_id: str | None = None) -> list[dict]:
    """List profiles for a user, optionally filtered by model_id.

    Returns list of dicts ordered by model_id, is_default DESC, name.
    Joins prompt_versions so that linked version text overrides inline system_prompt.
    """
    base_sql = (
        "SELECT mp.id, mp.user_id, mp.model_id, mp.name, mp.description, mp.is_default, "
        "mp.params_json, "
        "COALESCE(pv.prompt_text, mp.system_prompt) as system_prompt, "
        "mp.prompt_version_id, "
        "pv.label as prompt_version_label, "
        "mp.origin_type, mp.origin_ref, mp.created_at, mp.updated_at "
        "FROM model_profiles mp "
        "LEFT JOIN prompt_versions pv ON mp.prompt_version_id = pv.id"
    )
    if model_id:
        return await _db.fetch_all(
            base_sql + " WHERE mp.user_id = ? AND mp.model_id = ? "
            "ORDER BY mp.model_id, mp.is_default DESC, mp.name",
            (user_id, model_id),
        )
    return await _db.fetch_all(
        base_sql + " WHERE mp.user_id = ? "
        "ORDER BY mp.model_id, mp.is_default DESC, mp.name",
        (user_id,),
    )


async def get_profile(profile_id: str, user_id: str) -> dict | None:
    """Get a single profile by ID with ownership check.

    Joins prompt_versions so that linked version text overrides inline system_prompt.
    """
    return await _db.fetch_one(
        "SELECT mp.id, mp.user_id, mp.model_id, mp.name, mp.description, mp.is_default, "
        "mp.params_json, "
        "COALESCE(pv.prompt_text, mp.system_prompt) as system_prompt, "
        "mp.prompt_version_id, "
        "pv.label as prompt_version_label, "
        "mp.origin_type, mp.origin_ref, mp.created_at, mp.updated_at "
        "FROM model_profiles mp "
        "LEFT JOIN prompt_versions pv ON mp.prompt_version_id = pv.id "
        "WHERE mp.id = ? AND mp.user_id = ?",
        (profile_id, user_id),
    )


async def get_default_profile(user_id: str, model_id: str) -> dict | None:
    """Get the default profile for a specific model. Returns None if no default exists."""
    return await _db.fetch_one(
        "SELECT * FROM model_profiles WHERE user_id = ? AND model_id = ? AND is_default = 1",
        (user_id, model_id),
    )


async def create_profile(
    user_id: str,
    model_id: str,
    name: str,
    description: str | None = None,
    params_json: str | None = None,
    system_prompt: str | None = None,
    is_default: bool = False,
    origin_type: str = "manual",
    origin_ref: str | None = None,
    prompt_version_id: str | None = None,
) -> str:
    """Create a new model profile.

    Enforces max 20 profiles per model per user.
    If is_default=True, clears existing default for this user+model first.
    If prompt_version_id is set, system_prompt is cleared (version takes precedence).
    Returns profile_id.
    Raises ValueError if the per-model limit is exceeded.

    Count check and INSERT happen on a single connection to prevent
    TOCTOU race conditions (MED-1).
    """
    # Mutual exclusion: version link overrides inline prompt
    if prompt_version_id:
        system_prompt = None
    profile_id = uuid.uuid4().hex

    async with aiosqlite.connect(_db._path()) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")

        # Count check on the same connection as the insert
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM model_profiles WHERE user_id = ? AND model_id = ?",
            (user_id, model_id),
        )
        count_row = await cursor.fetchone()
        if count_row and count_row["cnt"] >= MAX_PROFILES_PER_MODEL:
            raise ValueError(
                f"Maximum of {MAX_PROFILES_PER_MODEL} profiles per model reached"
            )

        if is_default:
            await conn.execute(
                "UPDATE model_profiles SET is_default = 0, updated_at = datetime('now') "
                "WHERE user_id = ? AND model_id = ? AND is_default = 1",
                (user_id, model_id),
            )

        await conn.execute(
            "INSERT INTO model_profiles "
            "(id, user_id, model_id, name, description, is_default, params_json, "
            "system_prompt, prompt_version_id, origin_type, origin_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id, user_id, model_id, name,
                description or "",
                1 if is_default else 0,
                params_json or "{}",
                system_prompt,
                prompt_version_id,
                origin_type,
                origin_ref,
            ),
        )
        await conn.commit()

    return profile_id


async def update_profile(profile_id: str, user_id: str, **kwargs) -> bool:
    """Partial update of a model profile.

    Accepted kwargs: name, description, params_json, system_prompt, is_default,
    prompt_version_id.
    Mutual exclusion: setting prompt_version_id clears system_prompt and vice versa.
    If is_default=True, clears existing default for the same user+model first.
    Always updates updated_at timestamp.
    Returns True if the profile was found and updated.
    """
    allowed = {"name", "description", "params_json", "system_prompt", "is_default", "prompt_version_id"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and (v is not None or k in ("is_default", "system_prompt", "prompt_version_id"))}

    # Mutual exclusion: version link and inline prompt cannot coexist
    if "prompt_version_id" in updates and updates["prompt_version_id"] is not None:
        updates["system_prompt"] = None
    elif "system_prompt" in updates and updates["system_prompt"] is not None:
        updates["prompt_version_id"] = None
    if not updates:
        return False

    # If setting as default, we need the model_id and a multi-step transaction
    if updates.get("is_default"):
        async with aiosqlite.connect(_db._path()) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")

            # Get the profile to find its model_id
            cursor = await conn.execute(
                "SELECT model_id FROM model_profiles WHERE id = ? AND user_id = ?",
                (profile_id, user_id),
            )
            row = await cursor.fetchone()
            if not row:
                return False

            model_id = row["model_id"]

            # Clear old default
            await conn.execute(
                "UPDATE model_profiles SET is_default = 0, updated_at = datetime('now') "
                "WHERE user_id = ? AND model_id = ? AND is_default = 1",
                (user_id, model_id),
            )

            # Build SET clause
            fields = []
            params = []
            for k, v in updates.items():
                if k == "is_default":
                    fields.append("is_default = ?")
                    params.append(1 if v else 0)
                else:
                    fields.append(f"{k} = ?")
                    params.append(v)
            fields.append("updated_at = datetime('now')")
            params.extend([profile_id, user_id])

            cursor = await conn.execute(
                f"UPDATE model_profiles SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
                params,
            )
            await conn.commit()
            return cursor.rowcount > 0

    # Simple update (no default-clearing needed)
    fields = []
    params = []
    for k, v in updates.items():
        if k == "is_default":
            fields.append("is_default = ?")
            params.append(1 if v else 0)
        else:
            fields.append(f"{k} = ?")
            params.append(v)
    fields.append("updated_at = datetime('now')")
    params.extend([profile_id, user_id])
    count = await _db.execute_returning_rowcount(
        f"UPDATE model_profiles SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
        params,
    )
    return count > 0


async def delete_profile(profile_id: str, user_id: str) -> bool:
    """Delete a model profile with ownership check. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM model_profiles WHERE id = ? AND user_id = ?",
        (profile_id, user_id),
    )
    return count > 0



# --- Prompt Version Registry CRUD ---

async def create_prompt_version(
    user_id: str,
    prompt_text: str,
    label: str = "",
    source: str = "manual",
    parent_version_id: str | None = None,
    origin_run_id: str | None = None,
) -> str:
    """Create a new prompt version. Returns version_id."""
    version_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO prompt_versions (id, user_id, prompt_text, label, source, parent_version_id, origin_run_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (version_id, user_id, prompt_text, label, source, parent_version_id, origin_run_id),
    )
    return version_id


async def get_prompt_versions(user_id: str, limit: int = 100) -> list[dict]:
    """List user's prompt versions, newest first.

    Includes profile_count: number of model_profiles linked to each version.
    """
    return await _db.fetch_all(
        "SELECT pv.*, "
        "(SELECT COUNT(*) FROM model_profiles mp "
        "WHERE mp.prompt_version_id = pv.id) as profile_count "
        "FROM prompt_versions pv WHERE pv.user_id = ? "
        "ORDER BY pv.created_at DESC LIMIT ?",
        (user_id, limit),
    )


async def get_prompt_version(version_id: str, user_id: str) -> dict | None:
    """Get a single prompt version (scoped to user)."""
    return await _db.fetch_one(
        "SELECT * FROM prompt_versions WHERE id = ? AND user_id = ?",
        (version_id, user_id),
    )


async def update_prompt_version_label(version_id: str, user_id: str, label: str) -> bool:
    """Update the label of a prompt version. Returns True if found."""
    count = await _db.execute_returning_rowcount(
        "UPDATE prompt_versions SET label = ? WHERE id = ? AND user_id = ?",
        (label, version_id, user_id),
    )
    return count > 0


async def delete_prompt_version(version_id: str, user_id: str) -> bool:
    """Delete a prompt version. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM prompt_versions WHERE id = ? AND user_id = ?",
        (version_id, user_id),
    )
    return count > 0


async def set_default_profile(profile_id: str, user_id: str) -> bool:
    """Set a profile as the default for its model. Clears any existing default.

    Returns True if the profile was found and updated.
    """
    async with aiosqlite.connect(_db._path()) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")

        # Get the profile to find its model_id
        cursor = await conn.execute(
            "SELECT model_id FROM model_profiles WHERE id = ? AND user_id = ?",
            (profile_id, user_id),
        )
        row = await cursor.fetchone()
        if not row:
            return False

        model_id = row["model_id"]

        # Clear existing default for this user+model
        await conn.execute(
            "UPDATE model_profiles SET is_default = 0, updated_at = datetime('now') "
            "WHERE user_id = ? AND model_id = ? AND is_default = 1",
            (user_id, model_id),
        )

        # Set new default
        cursor = await conn.execute(
            "UPDATE model_profiles SET is_default = 1, updated_at = datetime('now') "
            "WHERE id = ? AND user_id = ?",
            (profile_id, user_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


# --- Provider CRUD ---

async def get_providers(user_id: str) -> list[dict]:
    """Get all providers for a user, ordered by sort_order then name."""
    return await _db.fetch_all(
        "SELECT * FROM providers WHERE user_id = ? ORDER BY sort_order, name",
        (user_id,),
    )


async def get_provider(provider_id: str) -> dict | None:
    """Get a single provider by its ID."""
    return await _db.fetch_one("SELECT * FROM providers WHERE id = ?", (provider_id,))


async def get_provider_by_key(user_id: str, key: str) -> dict | None:
    """Get a provider by user_id and key."""
    return await _db.fetch_one(
        "SELECT * FROM providers WHERE user_id = ? AND key = ?", (user_id, key)
    )


async def create_provider(user_id: str, key: str, name: str, api_base: str | None = None,
                           api_key_env: str | None = None, model_prefix: str | None = None) -> str:
    """Create a new provider. Returns the provider ID."""
    provider_id = secrets.token_hex(16)
    await _db.execute(
        "INSERT INTO providers (id, user_id, key, name, api_base, api_key_env, model_prefix) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (provider_id, user_id, key, name, api_base, api_key_env, model_prefix),
    )
    return provider_id


async def update_provider(provider_id: str, **kwargs) -> bool:
    """Update a provider. Only allowed fields are updated. Returns True if updated."""
    allowed = {"key", "name", "api_base", "api_key_env", "model_prefix", "is_active", "sort_order"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    set_parts = []
    values = []
    for k, v in updates.items():
        set_parts.append(f"{k} = ?")
        values.append(v)
    set_parts.append("updated_at = datetime('now')")
    set_clause = ", ".join(set_parts)
    result = await _db.execute_returning_rowcount(
        f"UPDATE providers SET {set_clause} WHERE id = ?", tuple(values) + (provider_id,)
    )
    return result > 0


async def delete_provider(provider_id: str) -> bool:
    """Delete a provider and all its models (cascade). Returns True if deleted."""
    result = await _db.execute_returning_rowcount(
        "DELETE FROM providers WHERE id = ?", (provider_id,)
    )
    return result > 0


# --- Model CRUD ---

async def get_models_for_provider(provider_id: str) -> list[dict]:
    """Get all active models for a provider."""
    return await _db.fetch_all(
        "SELECT * FROM models WHERE provider_id = ? AND is_active = 1 ORDER BY display_name",
        (provider_id,),
    )


async def get_all_models_for_user(user_id: str) -> list[dict]:
    """Get all active models across all active providers for a user."""
    return await _db.fetch_all(
        "SELECT m.*, p.key AS provider_key, p.name AS provider_name, p.api_base, "
        "p.api_key_env, p.model_prefix "
        "FROM models m JOIN providers p ON m.provider_id = p.id "
        "WHERE p.user_id = ? AND p.is_active = 1 AND m.is_active = 1 "
        "ORDER BY p.sort_order, p.name, m.display_name",
        (user_id,),
    )


async def get_model(model_id: str) -> dict | None:
    """Get a single model by its ID, including provider info."""
    return await _db.fetch_one(
        "SELECT m.*, p.user_id, p.key AS provider_key, p.name AS provider_name, p.api_base, "
        "p.api_key_env, p.model_prefix "
        "FROM models m JOIN providers p ON m.provider_id = p.id WHERE m.id = ?",
        (model_id,),
    )


async def get_model_by_litellm_id(user_id: str, litellm_id: str) -> dict | None:
    """Get a model by user_id and litellm_id."""
    return await _db.fetch_one(
        "SELECT m.*, p.key AS provider_key, p.name AS provider_name "
        "FROM models m JOIN providers p ON m.provider_id = p.id "
        "WHERE p.user_id = ? AND m.litellm_id = ?",
        (user_id, litellm_id),
    )


async def create_model(provider_id: str, litellm_id: str, display_name: str,
                        context_window: int = 128000, max_output_tokens: int | None = None,
                        skip_params: str | None = None) -> str:
    """Create a new model under a provider. Returns the model ID."""
    model_id = secrets.token_hex(16)
    await _db.execute(
        "INSERT INTO models (id, provider_id, litellm_id, display_name, context_window, "
        "max_output_tokens, skip_params) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (model_id, provider_id, litellm_id, display_name, context_window,
         max_output_tokens, skip_params),
    )
    return model_id


async def update_model(model_id: str, **kwargs) -> bool:
    """Update a model. Only allowed fields are updated. Returns True if updated."""
    allowed = {"litellm_id", "display_name", "context_window", "max_output_tokens",
               "skip_params", "is_active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    set_parts = []
    values = []
    for k, v in updates.items():
        set_parts.append(f"{k} = ?")
        values.append(v)
    set_parts.append("updated_at = datetime('now')")
    set_clause = ", ".join(set_parts)
    result = await _db.execute_returning_rowcount(
        f"UPDATE models SET {set_clause} WHERE id = ?", tuple(values) + (model_id,)
    )
    return result > 0


async def delete_model(model_id: str) -> bool:
    """Delete a model. Returns True if deleted."""
    result = await _db.execute_returning_rowcount(
        "DELETE FROM models WHERE id = ?", (model_id,)
    )
    return result > 0


async def seed_providers_for_new_user(user_id: str):
    """Seed default providers/models for a newly registered user from config.yaml."""
    import yaml

    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        return
    with open(config_path) as f:
        config = yaml.safe_load(f)

    async with aiosqlite.connect(_db._path()) as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        sort_order = 0
        for prov_key, prov_cfg in config.get("providers", {}).items():
            provider_id = secrets.token_hex(16)
            await conn.execute(
                "INSERT OR IGNORE INTO providers "
                "(id, user_id, key, name, api_base, api_key_env, model_prefix, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (provider_id, user_id, prov_key,
                 prov_cfg.get("display_name", prov_key),
                 prov_cfg.get("api_base"),
                 prov_cfg.get("api_key_env"),
                 prov_cfg.get("model_id_prefix"),
                 sort_order),
            )
            sort_order += 1
            for model in prov_cfg.get("models", []):
                await conn.execute(
                    "INSERT OR IGNORE INTO models "
                    "(id, provider_id, litellm_id, display_name, context_window, "
                    "max_output_tokens, skip_params) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (secrets.token_hex(16), provider_id, model["id"],
                     model.get("display_name", model["id"]),
                     model.get("context_window", 128000),
                     model.get("max_output_tokens"),
                     json.dumps(model.get("skip_params", []))),
                )
        await conn.commit()

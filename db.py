"""Database layer for LLM Benchmark Studio multi-user support.

Uses aiosqlite for async SQLite with WAL mode.
All tables are created on first startup via init_db().
"""

import json
import logging
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
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute query and return all rows as list of dicts."""
        async with aiosqlite.connect(self._path()) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def execute(self, query: str, params: tuple = ()) -> None:
        """Execute a write query (INSERT/UPDATE/DELETE) with auto-commit."""
        async with aiosqlite.connect(self._path()) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(query, params)
            await conn.commit()

    async def execute_returning_id(self, query: str, params: tuple = (), *, id_query: str, id_params: tuple = ()) -> str:
        """Execute INSERT, then fetch a generated ID with a follow-up SELECT."""
        async with aiosqlite.connect(self._path()) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            cursor = await conn.execute(query, params)
            await conn.commit()
            row = await (await conn.execute(id_query, id_params or (cursor.lastrowid,))).fetchone()
            return row[0]

    async def execute_returning_row(self, queries: list[tuple[str, tuple]], fetch_query: str, fetch_params: tuple) -> dict | None:
        """Execute write queries then fetch a row in the same connection."""
        async with aiosqlite.connect(self._path()) as conn:
            conn.row_factory = aiosqlite.Row
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
            await conn.execute("PRAGMA foreign_keys=ON")
            cursor = await conn.execute(query, params)
            await conn.commit()
            return cursor.rowcount

    async def execute_returning_scalar(self, query: str, params: tuple = ()):
        """Execute query and return the first column of the first row."""
        async with aiosqlite.connect(self._path()) as conn:
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
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    """Create all tables if they don't exist. Called once at app startup."""
    logger.info("Initializing database at %s", DB_PATH)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

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
                updated_by TEXT REFERENCES users(id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                user_id TEXT REFERENCES users(id),
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
                param_scoring TEXT NOT NULL DEFAULT 'exact',
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
                suite_name TEXT NOT NULL,
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_api_keys_user ON user_api_keys(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_configs_user ON user_configs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_benchmark_runs_user ON benchmark_runs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_benchmark_runs_ts ON benchmark_runs(user_id, timestamp DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_suites_user ON tool_suites(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_test_cases_suite ON tool_test_cases(suite_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_user ON tool_eval_runs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_ts ON tool_eval_runs(user_id, timestamp DESC)")

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
                interval_hours INTEGER NOT NULL,
                enabled INTEGER DEFAULT 1,
                last_run TEXT,
                next_run TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
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
                suite_name TEXT NOT NULL,
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
                FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_user ON param_tune_runs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_ts ON param_tune_runs(user_id, timestamp DESC)")
        await db.commit()

        # --- Prompt Tuner ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS prompt_tune_runs (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                suite_id TEXT NOT NULL,
                suite_name TEXT NOT NULL,
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
                FOREIGN KEY (suite_id) REFERENCES tool_suites(id) ON DELETE CASCADE,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_user ON prompt_tune_runs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_ts ON prompt_tune_runs(user_id, timestamp DESC)")
        await db.commit()

        # --- LLM Judge ---

        await db.execute("""
            CREATE TABLE IF NOT EXISTS judge_reports (
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
                status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','error')),
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                experiment_id TEXT,
                FOREIGN KEY (eval_run_id) REFERENCES tool_eval_runs(id) ON DELETE SET NULL,
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

                -- Type discriminator (one of 7 process types)
                job_type TEXT NOT NULL CHECK(job_type IN (
                    'benchmark', 'tool_eval', 'judge', 'judge_compare',
                    'param_tune', 'prompt_tune', 'scheduled_benchmark'
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

        # experiment_id indexes (columns now in CREATE TABLE above)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tool_eval_runs_experiment ON tool_eval_runs(experiment_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_param_tune_runs_experiment ON param_tune_runs(experiment_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_experiment ON prompt_tune_runs(experiment_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_judge_reports_experiment ON judge_reports(experiment_id)")
        await db.commit()

        # M6: system_prompt on tool_suites
        try:
            await db.execute("ALTER TABLE tool_suites ADD COLUMN system_prompt TEXT")
            await db.commit()
        except Exception:
            logger.debug("Column tool_suites.system_prompt already exists")

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


async def cleanup_expired_tokens():
    """Remove expired refresh tokens. Call periodically."""
    await _db.execute("DELETE FROM refresh_tokens WHERE expires_at < datetime('now')")


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
    user_id: str, prompt: str, context_tiers: str, results_json: str, metadata: str = None
) -> str:
    """Save a benchmark run. Returns the run ID."""
    run_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO benchmark_runs (id, user_id, prompt, context_tiers, results_json, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, user_id, prompt, context_tiers, results_json, metadata),
    )
    return run_id


async def get_user_benchmark_runs(user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Get benchmark runs for a user, newest first."""
    return await _db.fetch_all(
        "SELECT id, timestamp, prompt, context_tiers, results_json, metadata "
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
    return await _db.fetch_all(
        "SELECT * FROM tool_test_cases WHERE suite_id = ? ORDER BY created_at",
        (suite_id,),
    )


async def create_test_case(suite_id: str, prompt: str, expected_tool: str | None, expected_params: str | None, param_scoring: str = "exact", multi_turn_config: str | None = None, scoring_config_json: str | None = None) -> str:
    """Create a single test case. Returns case_id."""
    case_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO tool_test_cases (id, suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config, scoring_config_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (case_id, suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config, scoring_config_json),
    )
    return case_id


async def update_test_case(case_id: str, suite_id: str, prompt: str = None, expected_tool: str = None, expected_params: str = None, param_scoring: str = None, multi_turn_config: str | None = None, scoring_config_json: str | None = None) -> bool:
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

async def save_tool_eval_run(user_id: str, suite_id: str, suite_name: str, models_json: str, results_json: str, summary_json: str, temperature: float, config_json: str | None = None, experiment_id: str | None = None) -> str:
    """Save eval run. Returns run_id."""
    run_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO tool_eval_runs (id, user_id, suite_id, suite_name, models_json, results_json, summary_json, temperature, config_json, experiment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, user_id, suite_id, suite_name, models_json, results_json, summary_json, temperature, config_json, experiment_id),
    )
    return run_id


async def get_tool_eval_runs(user_id: str, limit: int = 50) -> list[dict]:
    """List user's eval runs (exclude full results_json for list view).

    Includes the most recent completed judge report's grade/score for each run
    via a LEFT JOIN on judge_reports (M5).
    """
    return await _db.fetch_all(
        "SELECT r.id, r.suite_id, r.suite_name, r.models_json, r.summary_json, "
        "r.temperature, r.timestamp, r.config_json, "
        "j.overall_grade AS judge_grade, j.overall_score AS judge_score "
        "FROM tool_eval_runs r "
        "LEFT JOIN ("
        "  SELECT eval_run_id, overall_grade, overall_score, "
        "    ROW_NUMBER() OVER (PARTITION BY eval_run_id ORDER BY timestamp DESC) AS rn "
        "  FROM judge_reports WHERE status = 'completed'"
        ") j ON j.eval_run_id = r.id AND j.rn = 1 "
        "WHERE r.user_id = ? ORDER BY r.timestamp DESC LIMIT ?",
        (user_id, limit),
    )


async def get_tool_eval_run(run_id: str, user_id: str) -> dict | None:
    """Get full eval run including results_json."""
    return await _db.fetch_one(
        "SELECT * FROM tool_eval_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )


async def delete_tool_eval_run(run_id: str, user_id: str) -> bool:
    """Delete eval run. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM tool_eval_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    )
    return count > 0


# --- Parameter Tuner CRUD ---

async def save_param_tune_run(
    user_id: str, suite_id: str, suite_name: str, models_json: str,
    search_space_json: str, total_combos: int,
    experiment_id: str | None = None,
) -> str:
    """Create a new param tune run (status=running). Returns run_id."""
    run_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO param_tune_runs (id, user_id, suite_id, suite_name, models_json, search_space_json, total_combos, experiment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, user_id, suite_id, suite_name, models_json, search_space_json, total_combos, experiment_id),
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
        "SELECT id, suite_id, suite_name, models_json, total_combos, completed_combos, "
        "best_score, status, duration_s, timestamp "
        "FROM param_tune_runs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit),
    )


async def get_param_tune_run(run_id: str, user_id: str) -> dict | None:
    """Get full param tune run including results_json."""
    return await _db.fetch_one(
        "SELECT * FROM param_tune_runs WHERE id = ? AND user_id = ?",
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
    user_id: str, suite_id: str, suite_name: str, mode: str,
    target_models_json: str, meta_model: str, base_prompt: str | None,
    config_json: str, total_prompts: int,
    experiment_id: str | None = None,
) -> str:
    """Create a new prompt tune run (status=running). Returns run_id."""
    run_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO prompt_tune_runs (id, user_id, suite_id, suite_name, mode, "
        "target_models_json, meta_model, base_prompt, config_json, total_prompts, experiment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, user_id, suite_id, suite_name, mode,
         target_models_json, meta_model, base_prompt, config_json, total_prompts, experiment_id),
    )
    return run_id


async def update_prompt_tune_run(
    run_id: str, user_id: str, *,
    generations_json: str | None = None,
    best_prompt: str | None = None,
    best_score: float | None = None,
    completed_prompts: int | None = None,
    status: str | None = None,
    duration_s: float | None = None,
) -> bool:
    """Update a prompt tune run. Only non-None fields are updated."""
    updates = []
    values = []
    if generations_json is not None:
        updates.append("generations_json = ?")
        values.append(generations_json)
    if best_prompt is not None:
        updates.append("best_prompt = ?")
        values.append(best_prompt)
    if best_score is not None:
        updates.append("best_score = ?")
        values.append(best_score)
    if completed_prompts is not None:
        updates.append("completed_prompts = ?")
        values.append(completed_prompts)
    if status is not None:
        updates.append("status = ?")
        values.append(status)
    if duration_s is not None:
        updates.append("duration_s = ?")
        values.append(duration_s)
    if not updates:
        return False
    values.extend([run_id, user_id])
    count = await _db.execute_returning_rowcount(
        f"UPDATE prompt_tune_runs SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    return count > 0


async def get_prompt_tune_runs(user_id: str, limit: int = 50) -> list[dict]:
    """List user's prompt tune runs (exclude large JSON for list view)."""
    return await _db.fetch_all(
        "SELECT id, suite_id, suite_name, mode, target_models_json, meta_model, "
        "best_score, status, total_prompts, completed_prompts, duration_s, timestamp "
        "FROM prompt_tune_runs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit),
    )


async def get_prompt_tune_run(run_id: str, user_id: str) -> dict | None:
    """Get full prompt tune run including generations_json."""
    return await _db.fetch_one(
        "SELECT * FROM prompt_tune_runs WHERE id = ? AND user_id = ?",
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
) -> str:
    """Create a new judge report (status=running). Returns report id."""
    return await _db.execute_returning_id(
        "INSERT INTO judge_reports (user_id, eval_run_id, eval_run_id_b, judge_model, mode, experiment_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, eval_run_id, eval_run_id_b, judge_model, mode, experiment_id),
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
        "SELECT id, eval_run_id, eval_run_id_b, judge_model, mode, "
        "overall_grade, overall_score, status, timestamp "
        "FROM judge_reports WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit),
    )


async def get_judge_report(report_id: str, user_id: str) -> dict | None:
    """Get full judge report including verdicts_json and report_json."""
    return await _db.fetch_one(
        "SELECT * FROM judge_reports WHERE id = ? AND user_id = ?",
        (report_id, user_id),
    )


async def delete_judge_report(report_id: str, user_id: str) -> bool:
    """Delete judge report. Returns True if deleted."""
    count = await _db.execute_returning_rowcount(
        "DELETE FROM judge_reports WHERE id = ? AND user_id = ?",
        (report_id, user_id),
    )
    return count > 0


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
            "SELECT id, timestamp, best_score, best_prompt, status "
            "FROM prompt_tune_runs "
            "WHERE experiment_id = ? AND user_id = ?",
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
) -> str:
    """Create a scheduled benchmark. Returns schedule ID."""
    schedule_id = uuid.uuid4().hex
    await _db.execute(
        "INSERT INTO schedules (id, user_id, name, prompt, models_json, max_tokens, temperature, interval_hours, next_run) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (schedule_id, user_id, name, prompt, models_json, max_tokens, temperature, interval_hours, next_run),
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
    allowed = {"name", "prompt", "models_json", "max_tokens", "temperature", "interval_hours", "enabled", "next_run"}
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

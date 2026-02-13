"""Database layer for LLM Benchmark Studio multi-user support.

Uses aiosqlite for async SQLite with WAL mode.
All tables are created on first startup via init_db().
"""

import json
import aiosqlite
import uuid
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "benchmark_studio.db"


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
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
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


# --- User CRUD ---

async def create_user(email: str, password_hash: str, role: str = "user") -> dict:
    """Insert a new user. Returns the user dict."""
    user_id = uuid.uuid4().hex
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT INTO users (id, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (user_id, email, password_hash, role),
        )
        await db.commit()
        cursor = await db.execute("SELECT id, email, role, created_at FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row)


async def get_user_by_email(email: str) -> dict | None:
    """Look up user by email (case-insensitive). Returns full row including password_hash."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_user_by_id(user_id: str) -> dict | None:
    """Look up user by ID. Returns row without password_hash."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, email, role, created_at, updated_at FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def count_users() -> int:
    """Return total user count."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        row = await cursor.fetchone()
        return row[0]


# --- Refresh token CRUD ---

async def store_refresh_token(user_id: str, token_hash: str, expires_at: str):
    """Store a hashed refresh token."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(token_hash) DO UPDATE SET user_id=excluded.user_id, expires_at=excluded.expires_at",
            (user_id, token_hash, expires_at),
        )
        await db.commit()


async def get_refresh_token(token_hash: str) -> dict | None:
    """Look up a refresh token by its hash."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_refresh_token(token_hash: str):
    """Delete a specific refresh token (logout)."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM refresh_tokens WHERE token_hash = ?", (token_hash,))
        await db.commit()


async def delete_user_refresh_tokens(user_id: str):
    """Delete all refresh tokens for a user (logout everywhere)."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))
        await db.commit()


async def cleanup_expired_tokens():
    """Remove expired refresh tokens. Call periodically."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM refresh_tokens WHERE expires_at < datetime('now')")
        await db.commit()


# --- User API keys CRUD ---

async def get_user_keys(user_id: str) -> list[dict]:
    """List all API keys for a user (encrypted values NOT returned)."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, provider_key, key_name, created_at, updated_at "
            "FROM user_api_keys WHERE user_id = ? ORDER BY provider_key",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_user_key_for_provider(user_id: str, provider_key: str) -> Optional[str]:
    """Return the encrypted_value for a specific user+provider, or None."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT encrypted_value FROM user_api_keys "
            "WHERE user_id = ? AND provider_key = ?",
            (user_id, provider_key),
        )
        row = await cursor.fetchone()
        return row["encrypted_value"] if row else None


async def upsert_user_key(
    user_id: str, provider_key: str, key_name: str, encrypted_value: str
) -> str:
    """Insert or update a user's API key for a provider. Returns the key ID."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
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
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT config_yaml FROM user_configs WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row:
            import yaml
            return yaml.safe_load(row["config_yaml"])
        return None


async def save_user_config(user_id: str, config_dict: dict):
    """Save user's config dict as YAML to DB."""
    import yaml
    config_yaml = yaml.dump(config_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO user_configs (id, user_id, config_yaml) VALUES (lower(hex(randomblob(16))), ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET config_yaml = excluded.config_yaml, updated_at = datetime('now')",
            (user_id, config_yaml),
        )
        await db.commit()


async def delete_user_key(user_id: str, provider_key: str) -> bool:
    """Delete a user's API key for a provider. Returns True if deleted."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            "DELETE FROM user_api_keys WHERE user_id = ? AND provider_key = ?",
            (user_id, provider_key),
        )
        await db.commit()
        return cursor.rowcount > 0


# --- Benchmark runs CRUD ---

async def save_benchmark_run(
    user_id: str, prompt: str, context_tiers: str, results_json: str, metadata: str = None
) -> str:
    """Save a benchmark run. Returns the run ID."""
    run_id = uuid.uuid4().hex
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO benchmark_runs (id, user_id, prompt, context_tiers, results_json, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, user_id, prompt, context_tiers, results_json, metadata),
        )
        await db.commit()
    return run_id


async def get_user_benchmark_runs(user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Get benchmark runs for a user, newest first."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, timestamp, prompt, context_tiers, results_json, metadata "
            "FROM benchmark_runs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_benchmark_run(run_id: str, user_id: str) -> dict | None:
    """Get a specific benchmark run (scoped to user)."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM benchmark_runs WHERE id = ? AND user_id = ?",
            (run_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_benchmark_run(run_id: str, user_id: str) -> bool:
    """Delete a benchmark run (scoped to user)."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            "DELETE FROM benchmark_runs WHERE id = ? AND user_id = ?",
            (run_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


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
        async with aiosqlite.connect(str(DB_PATH)) as conn:
            await conn.execute(
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
            await conn.commit()
    except Exception:
        pass  # Audit logging must never break the app


# --- Tool Suites CRUD ---

async def create_tool_suite(user_id: str, name: str, description: str, tools_json: str) -> str:
    """Create a tool suite. Returns suite_id."""
    suite_id = uuid.uuid4().hex
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO tool_suites (id, user_id, name, description, tools_json) VALUES (?, ?, ?, ?, ?)",
            (suite_id, user_id, name, description, tools_json),
        )
        await db.commit()
    return suite_id


async def get_tool_suites(user_id: str) -> list[dict]:
    """List user's tool suites with tool_count and test_case_count."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT ts.*,
                json_array_length(ts.tools_json) as tool_count,
                (SELECT COUNT(*) FROM tool_test_cases WHERE suite_id = ts.id) as test_case_count
            FROM tool_suites ts WHERE ts.user_id = ? ORDER BY ts.updated_at DESC""",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_tool_suite(suite_id: str, user_id: str) -> dict | None:
    """Get full suite with tools_json. Scoped to user."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tool_suites WHERE id = ? AND user_id = ?",
            (suite_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_tool_suite(suite_id: str, user_id: str, name: str = None, description: str = None, tools_json: str = None) -> bool:
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
    if not fields:
        return False
    fields.append("updated_at = datetime('now')")
    params.extend([suite_id, user_id])
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            f"UPDATE tool_suites SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_tool_suite(suite_id: str, user_id: str) -> bool:
    """Delete suite (CASCADE deletes test cases). Returns True if deleted."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            "DELETE FROM tool_suites WHERE id = ? AND user_id = ?",
            (suite_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# --- Tool Test Cases CRUD ---

async def get_test_cases(suite_id: str) -> list[dict]:
    """List all test cases for a suite."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tool_test_cases WHERE suite_id = ? ORDER BY created_at",
            (suite_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def create_test_case(suite_id: str, prompt: str, expected_tool: str | None, expected_params: str | None, param_scoring: str = "exact") -> str:
    """Create a single test case. Returns case_id."""
    case_id = uuid.uuid4().hex
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO tool_test_cases (id, suite_id, prompt, expected_tool, expected_params, param_scoring) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (case_id, suite_id, prompt, expected_tool, expected_params, param_scoring),
        )
        await db.commit()
    return case_id


async def update_test_case(case_id: str, suite_id: str, prompt: str = None, expected_tool: str = None, expected_params: str = None, param_scoring: str = None) -> bool:
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
    if not fields:
        return False
    params.extend([case_id, suite_id])
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            f"UPDATE tool_test_cases SET {', '.join(fields)} WHERE id = ? AND suite_id = ?",
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_test_case(case_id: str, suite_id: str) -> bool:
    """Delete a test case. Returns True if deleted."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            "DELETE FROM tool_test_cases WHERE id = ? AND suite_id = ?",
            (case_id, suite_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# --- Tool Eval Runs CRUD ---

async def save_tool_eval_run(user_id: str, suite_id: str, suite_name: str, models_json: str, results_json: str, summary_json: str, temperature: float) -> str:
    """Save eval run. Returns run_id."""
    run_id = uuid.uuid4().hex
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO tool_eval_runs (id, user_id, suite_id, suite_name, models_json, results_json, summary_json, temperature) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, user_id, suite_id, suite_name, models_json, results_json, summary_json, temperature),
        )
        await db.commit()
    return run_id


async def get_tool_eval_runs(user_id: str, limit: int = 50) -> list[dict]:
    """List user's eval runs (exclude full results_json for list view)."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, suite_id, suite_name, models_json, summary_json, temperature, timestamp "
            "FROM tool_eval_runs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_tool_eval_run(run_id: str, user_id: str) -> dict | None:
    """Get full eval run including results_json."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tool_eval_runs WHERE id = ? AND user_id = ?",
            (run_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_tool_eval_run(run_id: str, user_id: str) -> bool:
    """Delete eval run. Returns True if deleted."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            "DELETE FROM tool_eval_runs WHERE id = ? AND user_id = ?",
            (run_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# --- Audit Log ---

async def cleanup_audit_log(retention_days: int = 90):
    """Delete audit entries older than retention_days."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as conn:
            await conn.execute(
                "DELETE FROM audit_log WHERE timestamp < datetime('now', ?)",
                (f'-{retention_days} days',),
            )
            await conn.commit()
    except Exception:
        pass

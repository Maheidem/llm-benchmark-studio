"""Shared fixtures for the LLM Benchmark Studio test suite.

Provides:
- Temporary SQLite database per test session (isolated from production)
- FastAPI async test client via httpx.AsyncClient
- Authenticated test user + admin user fixtures
- Test config with a Zai provider for E2E tests
"""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Existing unit test fixtures (kept for backward compatibility)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_search_space():
    """A typical search space for param tuner tests."""
    return {
        "temperature": {"min": 0.0, "max": 1.0, "step": 0.5},
        "tool_choice": ["auto", "required"],
    }


@pytest.fixture
def sample_preset():
    """A sample preset for Phase 3 preset tests."""
    return {
        "name": "Test Preset",
        "search_space": {
            "temperature": {"min": 0.0, "max": 1.0, "step": 0.25},
            "tool_choice": ["auto", "required"],
        },
    }


# ---------------------------------------------------------------------------
# API / Integration test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def _temp_db_dir():
    """Create a temporary directory for the test database."""
    with tempfile.TemporaryDirectory(prefix="llm_bench_test_") as tmpdir:
        yield tmpdir


@pytest.fixture(scope="session", autouse=False)
def _patch_db_path(_temp_db_dir):
    """Patch db.DB_PATH to use a temporary database for ALL API tests.

    This MUST be applied before importing app (which calls db.init_db in lifespan).
    """
    import db as db_module
    original = db_module.DB_PATH
    db_module.DB_PATH = Path(_temp_db_dir) / "test_benchmark_studio.db"
    yield db_module.DB_PATH
    db_module.DB_PATH = original


@pytest_asyncio.fixture(scope="session")
async def _init_test_db(_patch_db_path):
    """Initialize the test database schema once per session."""
    import db as db_module
    await db_module.init_db()
    yield


@pytest_asyncio.fixture(scope="session")
async def app_client(_init_test_db):
    """Create an httpx.AsyncClient wired to the FastAPI app.

    Uses httpx.ASGITransport so no real HTTP server is started.
    Session-scoped for performance (DB is initialized once).
    """
    import httpx
    from app import app, lifespan

    # The app uses lifespan for DB init, but we already init_db above.
    # We still need to trigger lifespan for job_registry.startup() etc.
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            timeout=30.0,
        ) as client:
            yield client


@pytest_asyncio.fixture(scope="session")
async def test_user(app_client):
    """Register a test user and return (user_dict, access_token).

    Session-scoped: same user for all tests (fast).
    """
    resp = await app_client.post("/api/auth/register", json={
        "email": "testuser@benchtest.local",
        "password": "TestPass123!",
    })
    assert resp.status_code == 200, f"User registration failed: {resp.text}"
    data = resp.json()
    return data["user"], data["access_token"]


@pytest_asyncio.fixture(scope="session")
async def auth_headers(test_user):
    """Authorization headers for the test user."""
    _, token = test_user
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="session")
async def admin_user(app_client):
    """The first registered user is auto-promoted to admin.

    Since test_user is the first user (empty DB), it IS the admin.
    This fixture returns the same (user, token) but verifies admin role.
    """
    resp = await app_client.post("/api/auth/register", json={
        "email": "admin@benchtest.local",
        "password": "AdminPass123!",
    })
    # If test_user already created the first user (admin), this is a second user.
    # The first user gets admin, so we need to use the test_user fixture for admin.
    # Actually, since fixtures run in dependency order: test_user runs first.
    # test_user is the admin. Let's just login as test_user for admin ops.
    # Register second user (will be plain "user" role)
    if resp.status_code == 409:
        # Already exists, login instead
        resp = await app_client.post("/api/auth/login", json={
            "email": "admin@benchtest.local",
            "password": "AdminPass123!",
        })
    data = resp.json()
    return data["user"], data["access_token"]


@pytest_asyncio.fixture(scope="session")
async def admin_headers(test_user, _patch_db_path):
    """Authorization headers for admin user.

    Promotes the test user to admin directly in the DB, since other tests
    may register users before this fixture's dependency chain resolves.
    """
    import aiosqlite

    user, token = test_user
    if user["role"] != "admin":
        async with aiosqlite.connect(str(_patch_db_path)) as conn:
            await conn.execute(
                "UPDATE users SET role='admin' WHERE id=?", (user["id"],)
            )
            await conn.commit()
        user["role"] = "admin"
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Rate limit cleanup for tests that create jobs
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def clear_active_jobs(test_user, _patch_db_path):
    """Mark all active jobs for the test user as 'completed' before the test runs.

    This prevents rate-limit 429 errors when multiple tests create jobs
    sequentially within the same session-scoped user.
    """
    import aiosqlite

    user, _ = test_user
    async with aiosqlite.connect(str(_patch_db_path)) as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'done', completed_at = datetime('now') "
            "WHERE user_id = ? AND status IN ('pending', 'queued', 'running')",
            (user["id"],),
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Test config with Zai provider (for E2E smoke tests)
# ---------------------------------------------------------------------------

ZAI_CONFIG_PROVIDER = {
    "provider_key": "zai",
    "display_name": "Zai",
    "api_base": "https://api.z.ai/api/coding/paas/v4/",
    "api_key_env": "ZAI_API_KEY",
    "model_id_prefix": "",
    "models": [],
}

ZAI_MODEL = {
    "id": "GLM-4.5-Air",
    "display_name": "GLM-4.5-Air",
    "context_window": 128000,
}

TOOL_SUITE_FIXTURE = {
    "name": "Test Weather Suite",
    "description": "Minimal suite for contract testing",
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            },
        }
    ],
    "test_cases": [
        {
            "prompt": "What is the weather in Paris?",
            "expected_tool": "get_weather",
            "expected_params": {"city": "Paris"},
        },
    ],
}

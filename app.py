#!/usr/bin/env python3
"""LLM Benchmark Studio - Web dashboard for benchmarking LLM providers.

Usage:
    python app.py                  # Start on port 8501
    python app.py --port 3333      # Custom port
"""

import argparse
import asyncio
import csv
import io
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import litellm
import yaml

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response

# Load .env before importing benchmark (needs API keys)
_dir = Path(__file__).parent
load_dotenv(_dir / ".env", override=True)

APP_VERSION = os.getenv("APP_VERSION", "dev")

from benchmark import (  # noqa: E402
    AggregatedResult,
    RunResult,
    Target,
    _compute_variance,
    generate_context_text,
    load_config,
    build_targets,
    run_single,
    save_results,
    sanitize_error,
)
import auth
import db
from keyvault import vault
from mcp import ClientSession
from mcp.client.sse import sse_client

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app_instance):
    """Initialize database on startup."""
    await db.init_db()
    # Clean up old audit log entries
    await db.cleanup_audit_log(retention_days=90)
    # Ensure ADMIN_EMAIL user has admin role (promote existing or create new)
    admin_email = os.environ.get("ADMIN_EMAIL")
    if admin_email:
        existing = await db.get_user_by_email(admin_email)
        if existing and existing["role"] != "admin":
            async with await db.get_db() as conn:
                await conn.execute("UPDATE users SET role='admin' WHERE id=?", (existing["id"],))
                await conn.commit()
            print(f"  Promoted to admin: {admin_email}")
        elif not existing:
            admin_pass = os.environ.get("ADMIN_PASSWORD")
            if admin_pass:
                hashed = auth.hash_password(admin_pass)
                await db.create_user(admin_email, hashed, role="admin")
                print(f"  Admin account created: {admin_email}")
    # Launch background scheduler for scheduled benchmarks
    scheduler_task = asyncio.create_task(_run_scheduler())
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass

async def _run_scheduled_benchmark(schedule: dict):
    """Execute a single scheduled benchmark run and save results."""
    from dataclasses import replace as dc_replace

    user_id = schedule["user_id"]
    models = json.loads(schedule["models_json"])
    prompt = schedule["prompt"]
    max_tokens = schedule.get("max_tokens", 512)
    temperature = schedule.get("temperature", 0.7)

    config = await _get_user_config(user_id)
    all_targets = build_targets(config)

    # Filter to scheduled models
    targets = [t for t in all_targets if t.model_id in models]
    if not targets:
        return

    # Inject per-user API keys
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    # Run benchmarks (single run per model, no warmup for scheduled)
    all_results = []
    for target in targets:
        result = await async_run_single(target, prompt, max_tokens, temperature)
        all_results.append({
            "provider": target.provider,
            "model": target.display_name,
            "model_id": target.model_id,
            "run": 1,
            "runs": 1,
            "context_tokens": 0,
            "ttft_ms": round(result.ttft_ms, 2),
            "total_time_s": round(result.total_time_s, 3),
            "output_tokens": result.output_tokens,
            "input_tokens": result.input_tokens,
            "tokens_per_second": round(result.tokens_per_second, 2),
            "input_tokens_per_second": round(result.input_tokens_per_second, 2),
            "cost": round(result.cost, 8),
            "success": result.success,
            "error": result.error,
        })

    # Save to benchmark_runs (same as manual benchmarks)
    if all_results:
        await db.save_benchmark_run(
            user_id=user_id,
            prompt=prompt,
            context_tiers=json.dumps([0]),
            results_json=json.dumps(all_results),
            metadata=json.dumps({"source": "schedule", "schedule_id": schedule["id"], "schedule_name": schedule["name"]}),
        )


async def _run_scheduler():
    """Background task that checks for due schedules every 60 seconds."""
    while True:
        try:
            await asyncio.sleep(60)
            due = await db.get_due_schedules()
            for schedule in due:
                try:
                    await _run_scheduled_benchmark(schedule)
                except Exception as exc:
                    print(f"  [scheduler] Error running schedule {schedule['id']}: {exc}")
                # Update timestamps regardless of success
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                interval = schedule["interval_hours"]
                next_run = (datetime.now(timezone.utc) + timedelta(hours=interval)).strftime("%Y-%m-%d %H:%M:%S")
                await db.update_schedule_after_run(schedule["id"], now, next_run)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"  [scheduler] Unexpected error: {exc}")


app = FastAPI(title="LLM Benchmark Studio", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Security Headers Middleware
# ---------------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)

        # Content Security Policy
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )

        # Prevent MIME sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions policy (disable unused browser features)
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        return response


app.add_middleware(SecurityHeadersMiddleware)

# CORS configuration (only enabled when CORS_ORIGINS is set)
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

CONFIG_PATH = str(_dir / "config.yaml")

# ---------------------------------------------------------------------------
# Per-user config: default config for new users + DB helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "defaults": {
        "max_tokens": 512,
        "temperature": 0.7,
        "context_tiers": [0],
        "prompt": "Explain the concept of recursion in programming. Include a simple example in Python with comments.",
    },
    "prompt_templates": {
        "recursion": {
            "category": "reasoning",
            "label": "Explain Recursion",
            "prompt": "Explain the concept of recursion in programming. Include a simple example in Python with comments.",
        },
        "code_generation": {
            "category": "code",
            "label": "Generate Sorting Algorithm",
            "prompt": "Write a Python function that implements merge sort. Include type hints and docstrings.",
        },
        "creative": {
            "category": "creative",
            "label": "Short Story",
            "prompt": "Write a short story (300 words) about a robot discovering nature for the first time.",
        },
        "qa": {
            "category": "short_qa",
            "label": "Quick Q&A",
            "prompt": "What are the three main types of machine learning? Explain each in one sentence.",
        },
    },
    "providers": {
        "openai": {
            "display_name": "OpenAI",
            "api_key_env": "OPENAI_API_KEY",
            "models": [
                {"id": "gpt-4o", "display_name": "GPT-4o", "context_window": 128000},
                {"id": "gpt-4o-mini", "display_name": "GPT-4o Mini", "context_window": 128000},
            ],
        },
        "anthropic": {
            "display_name": "Anthropic",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model_id_prefix": "anthropic",
            "models": [
                {
                    "id": "anthropic/claude-sonnet-4-5",
                    "display_name": "Claude Sonnet 4.5",
                    "context_window": 200000,
                    "skip_params": ["temperature"],
                },
            ],
        },
        "google_gemini": {
            "display_name": "Google Gemini",
            "api_key_env": "GEMINI_API_KEY",
            "model_id_prefix": "gemini",
            "models": [
                {
                    "id": "gemini/gemini-2.5-flash",
                    "display_name": "Gemini 2.5 Flash",
                    "context_window": 1000000,
                },
            ],
        },
    },
}


async def _get_user_config(user_id: str) -> dict:
    """Load user's config from DB, falling back to DEFAULT_CONFIG for new users."""
    config = await db.get_user_config(user_id)
    if config is None:
        await db.save_user_config(user_id, DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    return config


async def _save_user_config(user_id: str, config: dict):
    """Save user's config to DB."""
    await db.save_user_config(user_id, config)


# Per-user concurrency guards
_user_locks: dict[str, asyncio.Lock] = {}
_user_cancel: dict[str, asyncio.Event] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _get_user_cancel(user_id: str) -> asyncio.Event:
    if user_id not in _user_cancel:
        _user_cancel[user_id] = asyncio.Event()
    return _user_cancel[user_id]


# Rate limiter
RATE_LIMIT_PER_HOUR = int(os.environ.get("BENCHMARK_RATE_LIMIT", "2000"))
_rate_windows: dict[str, list[float]] = {}


def _check_rate_limit(user_id: str) -> tuple[bool, int]:
    """Check if user is within rate limit. Returns (allowed, remaining)."""
    now = time.time()
    if user_id not in _rate_windows:
        _rate_windows[user_id] = []
    window = _rate_windows[user_id]
    # Prune entries older than 1 hour
    cutoff = now - 3600
    _rate_windows[user_id] = [t for t in window if t > cutoff]
    window = _rate_windows[user_id]

    remaining = RATE_LIMIT_PER_HOUR - len(window)
    if remaining <= 0:
        return False, 0
    return True, remaining


def _record_rate_limit(user_id: str):
    """Record a benchmark execution for rate limiting."""
    if user_id not in _rate_windows:
        _rate_windows[user_id] = []
    _rate_windows[user_id].append(time.time())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard UI."""
    return (_dir / "index.html").read_text()


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/robots.txt")
async def robots_txt(request: Request):
    base_url = str(request.base_url).rstrip("/")
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    base_url = str(request.base_url).rstrip("/")
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{base_url}/</loc>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )
    return Response(content=content, media_type="application/xml")


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
app.post("/api/auth/register")(auth.register_handler)
app.post("/api/auth/login")(auth.login_handler)
app.post("/api/auth/refresh")(auth.refresh_handler)
app.post("/api/auth/logout")(auth.logout_handler)
app.get("/api/auth/me")(auth.me_handler)


@app.post("/api/auth/cli-token")
async def generate_cli_token(user: dict = Depends(auth.get_current_user)):
    """Generate a long-lived JWT for CLI usage (30 days)."""
    from datetime import timedelta, datetime, timezone
    from jose import jwt as jose_jwt

    expire = datetime.now(timezone.utc) + timedelta(days=30)
    payload = {
        "sub": user["id"],
        "role": user["role"],
        "exp": expire,
        "type": "cli",
    }
    token = jose_jwt.encode(payload, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    return {"token": token, "expires_in_days": 30}


# ---------------------------------------------------------------------------
# Admin endpoints (all require admin role)
# ---------------------------------------------------------------------------
_process_start_time = time.time()


@app.get("/api/admin/users")
async def admin_list_users(current_user: dict = Depends(auth.require_admin)):
    """List all users with last login, benchmark count, key count."""
    conn = await db.get_db()
    try:
        cursor = await conn.execute("""
            SELECT u.id, u.email, u.role, u.created_at,
                   (SELECT MAX(timestamp) FROM audit_log
                    WHERE user_id = u.id AND action = 'user_login') as last_login,
                   (SELECT COUNT(*) FROM benchmark_runs
                    WHERE user_id = u.id) as benchmark_count,
                   (SELECT COUNT(*) FROM user_api_keys
                    WHERE user_id = u.id) as key_count
            FROM users u
            ORDER BY u.created_at DESC
        """)
        rows = await cursor.fetchall()
        return {"users": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.put("/api/admin/users/{user_id}/role")
async def admin_update_role(user_id: str, request: Request, current_user: dict = Depends(auth.require_admin)):
    """Change a user's role (admin/user). Cannot change own role."""
    body = await request.json()
    new_role = body.get("role")
    if new_role not in ("admin", "user"):
        return JSONResponse({"error": "role must be 'admin' or 'user'"}, status_code=400)
    if user_id == current_user["id"]:
        return JSONResponse({"error": "Cannot change your own role"}, status_code=400)

    conn = await db.get_db()
    try:
        await conn.execute("UPDATE users SET role = ?, updated_at = datetime('now') WHERE id = ?", (new_role, user_id))
        await conn.commit()
    finally:
        await conn.close()

    await db.log_audit(
        current_user["id"], current_user.get("email", ""), "admin_user_update",
        resource_type="user", resource_id=str(user_id),
        detail={"change": "role", "new_role": new_role},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"status": "ok"}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request, current_user: dict = Depends(auth.require_admin)):
    """Delete a user and all their data (cascade). Cannot delete self."""
    if user_id == current_user["id"]:
        return JSONResponse({"error": "Cannot delete your own account"}, status_code=400)

    conn = await db.get_db()
    try:
        cursor = await conn.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "User not found"}, status_code=404)
        deleted_email = row["email"]

        # Unlink audit_log entries (no CASCADE, but keep records)
        await conn.execute("UPDATE audit_log SET user_id = NULL WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await conn.commit()
    finally:
        await conn.close()

    await db.log_audit(
        current_user["id"], current_user.get("email", ""), "admin_user_delete",
        resource_type="user", resource_id=str(user_id),
        detail={"deleted_email": deleted_email},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"status": "ok"}


@app.get("/api/admin/stats")
async def admin_stats(current_user: dict = Depends(auth.require_admin)):
    """Usage statistics: benchmark counts, top users, keys by provider."""
    conn = await db.get_db()
    try:
        stats = {}

        # Benchmark counts by time window
        for label, interval in [("24h", "-1 day"), ("7d", "-7 days"), ("30d", "-30 days")]:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM benchmark_runs WHERE timestamp > datetime('now', ?)",
                (interval,),
            )
            row = await cursor.fetchone()
            stats[f"benchmarks_{label}"] = row["cnt"]

        # Top users by benchmark count
        cursor = await conn.execute("""
            SELECT u.email as username, COUNT(*) as cnt
            FROM benchmark_runs br JOIN users u ON br.user_id = u.id
            GROUP BY br.user_id ORDER BY cnt DESC LIMIT 10
        """)
        rows = await cursor.fetchall()
        stats["top_users"] = [dict(r) for r in rows]

        # Keys by provider
        cursor = await conn.execute("""
            SELECT provider_key as provider, COUNT(*) as user_count
            FROM user_api_keys GROUP BY provider_key
        """)
        rows = await cursor.fetchall()
        stats["keys_by_provider"] = [dict(r) for r in rows]

        # Total users
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
        stats["total_users"] = row["cnt"]

        return stats
    finally:
        await conn.close()


@app.get("/api/admin/system")
async def admin_system_health(current_user: dict = Depends(auth.require_admin)):
    """System health: db size, results count, uptime, benchmark status."""
    db_path = db.DB_PATH
    results_dir = _dir / "results"

    db_size_mb = 0
    if db_path.exists():
        db_size_mb = round(db_path.stat().st_size / 1024 / 1024, 2)

    results_count = 0
    results_size_mb = 0
    if results_dir.exists():
        json_files = list(results_dir.glob("*.json"))
        results_count = len(json_files)
        results_size_mb = round(sum(f.stat().st_size for f in json_files) / 1024 / 1024, 2)

    return {
        "db_size_mb": db_size_mb,
        "results_size_mb": results_size_mb,
        "results_count": results_count,
        "benchmark_active": any(lock.locked() for lock in _user_locks.values()),
        "process_uptime_s": round(time.time() - _process_start_time),
    }


@app.get("/api/admin/audit")
async def admin_audit_log(
    request: Request,
    user: str = None,
    action: str = None,
    since: str = None,
    limit: int = 100,
    offset: int = 0,
    current_user: dict = Depends(auth.require_admin),
):
    """Audit log with optional filters and pagination."""
    conn = await db.get_db()
    try:
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []

        if user:
            query += " AND username = ?"
            params.append(user)
        if action:
            query += " AND action = ?"
            params.append(action)
        if since:
            query += " AND timestamp > ?"
            params.append(since)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        return {"entries": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.put("/api/admin/users/{user_id}/rate-limit")
async def admin_set_rate_limit(user_id: str, request: Request, current_user: dict = Depends(auth.require_admin)):
    """Set per-user rate limits."""
    body = await request.json()
    conn = await db.get_db()
    try:
        await conn.execute("""
            INSERT INTO rate_limits (user_id, benchmarks_per_hour, max_concurrent, max_runs_per_benchmark, updated_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                benchmarks_per_hour = excluded.benchmarks_per_hour,
                max_concurrent = excluded.max_concurrent,
                max_runs_per_benchmark = excluded.max_runs_per_benchmark,
                updated_at = datetime('now'),
                updated_by = excluded.updated_by
        """, (
            user_id,
            body.get("benchmarks_per_hour", 20),
            body.get("max_concurrent", 1),
            body.get("max_runs_per_benchmark", 10),
            current_user["id"],
        ))
        await conn.commit()
    finally:
        await conn.close()

    await db.log_audit(
        current_user["id"], current_user.get("email", ""), "admin_rate_limit",
        resource_type="config", resource_id=str(user_id),
        detail=body,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"status": "ok"}


@app.get("/api/admin/users/{user_id}/rate-limit")
async def admin_get_rate_limit(user_id: str, current_user: dict = Depends(auth.require_admin)):
    """Get per-user rate limits (returns defaults if none set)."""
    conn = await db.get_db()
    try:
        cursor = await conn.execute("SELECT * FROM rate_limits WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"user_id": user_id, "benchmarks_per_hour": 20, "max_concurrent": 1, "max_runs_per_benchmark": 10}
    finally:
        await conn.close()


@app.get("/api/config")
async def get_config(user: dict = Depends(auth.get_current_user)):
    """Return available providers and models from per-user config."""
    config = await _get_user_config(user["id"])

    providers = {}
    for prov_key, prov_cfg in config.get("providers", {}).items():
        display_name = prov_cfg.get("display_name", prov_key)
        models = []
        for model in prov_cfg.get("models", []):
            m = {
                "model_id": model["id"],
                "display_name": model.get("display_name", model["id"]),
                "context_window": model.get("context_window", 128000),
                "max_output_tokens": model.get("max_output_tokens"),
                "skip_params": model.get("skip_params", []),
            }
            # Include any custom fields (not standard keys)
            standard_keys = {"id", "display_name", "context_window", "max_output_tokens", "skip_params"}
            for k, v in model.items():
                if k not in standard_keys:
                    m[k] = v
            models.append(m)

        providers[display_name] = {
            "provider_key": prov_key,
            "display_name": display_name,
            "api_base": prov_cfg.get("api_base", ""),
            "api_key_env": prov_cfg.get("api_key_env", ""),
            "api_key": "***" if prov_cfg.get("api_key") else "",
            "model_id_prefix": prov_cfg.get("model_id_prefix", ""),
            "models": models,
        }

    return {
        "defaults": config.get("defaults", {}),
        "providers": providers,
    }


@app.put("/api/config/model")
async def update_model_config(request: Request, user: dict = Depends(auth.get_current_user)):
    """Update per-model settings in user's config (full edit support)."""
    body = await request.json()
    model_id = body.get("model_id")
    provider_key = body.get("provider_key")
    if not model_id:
        return JSONResponse({"error": "model_id required"}, status_code=400)

    config = await _get_user_config(user["id"])

    # Find the model — use provider_key if given, else search all
    found = False
    for prov_key, prov_cfg in config.get("providers", {}).items():
        if provider_key and prov_key != provider_key:
            continue
        for model in prov_cfg.get("models", []):
            if model["id"] == model_id:
                # Rename model ID
                new_id = body.get("new_model_id")
                if new_id and new_id != model_id:
                    model["id"] = new_id

                # Display name
                if "display_name" in body:
                    dn = body["display_name"]
                    if dn:
                        model["display_name"] = dn
                    else:
                        # Auto-derive from id
                        mid = model["id"]
                        model["display_name"] = mid.split("/")[-1] if "/" in mid else mid

                # Context window
                if "context_window" in body and body["context_window"] is not None:
                    model["context_window"] = int(body["context_window"])

                # Max output tokens
                if "max_output_tokens" in body:
                    val = body["max_output_tokens"]
                    if val is None or val == "":
                        model.pop("max_output_tokens", None)
                    else:
                        model["max_output_tokens"] = int(val)

                # Skip params (replace entire list)
                if "skip_params" in body:
                    sp = body["skip_params"]
                    if sp and len(sp) > 0:
                        model["skip_params"] = sp
                    else:
                        model.pop("skip_params", None)

                # Custom fields (merge; null deletes)
                if "custom_fields" in body and isinstance(body["custom_fields"], dict):
                    standard = {"id", "display_name", "context_window", "max_output_tokens", "skip_params"}
                    for k, v in body["custom_fields"].items():
                        if k in standard:
                            continue
                        if v is None:
                            model.pop(k, None)
                        else:
                            model[k] = v

                found = True
                break
        if found:
            break

    if not found:
        return JSONResponse({"error": f"Model {model_id} not found"}, status_code=404)

    await _save_user_config(user["id"], config)
    return {"status": "ok", "model_id": body.get("new_model_id") or model_id}


def _save_config(config: dict):
    """Write config dict back to YAML."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


@app.post("/api/config/model")
async def add_model(request: Request, user: dict = Depends(auth.get_current_user)):
    """Add a new model to a provider."""
    body = await request.json()
    prov_key = body.get("provider_key")
    model_id = body.get("id")
    if not prov_key or not model_id:
        return JSONResponse({"error": "provider_key and id required"}, status_code=400)

    config = await _get_user_config(user["id"])

    if prov_key not in config.get("providers", {}):
        return JSONResponse({"error": f"Provider '{prov_key}' not found"}, status_code=400)

    prov_cfg = config["providers"][prov_key]

    # Auto-prepend model_id_prefix if provider has one and ID doesn't already start with it
    prefix = prov_cfg.get("model_id_prefix", "")
    if prefix and not model_id.startswith(prefix + "/"):
        model_id = f"{prefix}/{model_id}"

    # Check for duplicate
    for m in prov_cfg.get("models", []):
        if m["id"] == model_id:
            return JSONResponse({"error": f"Model '{model_id}' already exists"}, status_code=400)

    # Auto-derive display_name from last segment of id
    display_name = body.get("display_name") or (model_id.split("/")[-1] if "/" in model_id else model_id)

    new_model = {"id": model_id, "display_name": display_name}
    if body.get("context_window"):
        new_model["context_window"] = int(body["context_window"])
    if body.get("max_output_tokens"):
        new_model["max_output_tokens"] = int(body["max_output_tokens"])

    prov_cfg.setdefault("models", []).append(new_model)
    await _save_user_config(user["id"], config)
    return {"status": "ok", "model_id": model_id}


@app.delete("/api/config/model")
async def delete_model(request: Request, user: dict = Depends(auth.get_current_user)):
    """Remove a model from a provider."""
    body = await request.json()
    prov_key = body.get("provider_key")
    model_id = body.get("model_id")
    if not prov_key or not model_id:
        return JSONResponse({"error": "provider_key and model_id required"}, status_code=400)

    config = await _get_user_config(user["id"])

    prov_cfg = config.get("providers", {}).get(prov_key)
    if not prov_cfg:
        return JSONResponse({"error": f"Provider '{prov_key}' not found"}, status_code=404)

    models = prov_cfg.get("models", [])
    original_len = len(models)
    prov_cfg["models"] = [m for m in models if m["id"] != model_id]

    if len(prov_cfg["models"]) == original_len:
        return JSONResponse({"error": f"Model '{model_id}' not found"}, status_code=404)

    await _save_user_config(user["id"], config)
    return {"status": "ok"}


@app.post("/api/config/provider")
async def add_provider(request: Request, user: dict = Depends(auth.get_current_user)):
    """Add a new provider."""
    body = await request.json()
    prov_key = body.get("provider_key")
    if not prov_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)

    config = await _get_user_config(user["id"])

    if prov_key in config.get("providers", {}):
        return JSONResponse({"error": f"Provider '{prov_key}' already exists"}, status_code=400)

    new_prov = {"display_name": body.get("display_name", prov_key), "models": []}
    if body.get("api_base"):
        new_prov["api_base"] = body["api_base"]
    if body.get("api_key_env"):
        new_prov["api_key_env"] = body["api_key_env"]
    if body.get("api_key"):
        new_prov["api_key"] = body["api_key"]
    if body.get("model_id_prefix"):
        new_prov["model_id_prefix"] = body["model_id_prefix"]

    config.setdefault("providers", {})[prov_key] = new_prov
    await _save_user_config(user["id"], config)
    return {"status": "ok", "provider_key": prov_key}


@app.put("/api/config/provider")
async def update_provider(request: Request, user: dict = Depends(auth.get_current_user)):
    """Edit provider settings (not its models)."""
    body = await request.json()
    prov_key = body.get("provider_key")
    if not prov_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)

    config = await _get_user_config(user["id"])

    prov_cfg = config.get("providers", {}).get(prov_key)
    if not prov_cfg:
        return JSONResponse({"error": f"Provider '{prov_key}' not found"}, status_code=404)

    if "display_name" in body:
        prov_cfg["display_name"] = body["display_name"]
    if "api_base" in body:
        if body["api_base"]:
            prov_cfg["api_base"] = body["api_base"]
        else:
            prov_cfg.pop("api_base", None)
    if "api_key_env" in body:
        if body["api_key_env"]:
            prov_cfg["api_key_env"] = body["api_key_env"]
        else:
            prov_cfg.pop("api_key_env", None)
    if "api_key" in body:
        if body["api_key"]:
            prov_cfg["api_key"] = body["api_key"]
        else:
            prov_cfg.pop("api_key", None)
    if "model_id_prefix" in body:
        if body["model_id_prefix"]:
            prov_cfg["model_id_prefix"] = body["model_id_prefix"]
        else:
            prov_cfg.pop("model_id_prefix", None)

    await _save_user_config(user["id"], config)
    return {"status": "ok"}


@app.get("/api/models/discover")
async def discover_models(provider_key: str, user: dict = Depends(auth.get_current_user)):
    """Discover available models from a provider's API."""
    import httpx

    config = await _get_user_config(user["id"])
    prov_cfg = config.get("providers", {}).get(provider_key)
    if not prov_cfg:
        return JSONResponse({"error": f"Provider '{provider_key}' not found"}, status_code=404)

    # Resolve API key: user key > global env
    api_key = None
    encrypted = await db.get_user_key_for_provider(user["id"], provider_key)
    if encrypted:
        try:
            api_key = vault.decrypt(encrypted)
        except Exception:
            pass
    if not api_key:
        key_env = prov_cfg.get("api_key_env", "")
        if key_env:
            api_key = os.getenv(key_env)
    if not api_key:
        api_key = prov_cfg.get("api_key")

    api_base = prov_cfg.get("api_base", "")
    prefix = prov_cfg.get("model_id_prefix", "")
    key_env = prov_cfg.get("api_key_env", "")

    # Detect which API pattern to use
    api_type = "openai"  # default
    if prefix == "anthropic" or "ANTHROPIC" in key_env.upper():
        api_type = "anthropic"
    elif prefix == "gemini" or "GEMINI" in key_env.upper():
        api_type = "gemini"
    elif api_base:
        api_type = "generic"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            models = []

            if api_type == "anthropic":
                url = "https://api.anthropic.com/v1/models?limit=100"
                headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for m in data:
                    mid = m.get("id", "")
                    dn = m.get("display_name", mid)
                    full_id = f"{prefix}/{mid}" if prefix and not mid.startswith(prefix + "/") else mid
                    models.append({"id": full_id, "display_name": dn})

            elif api_type == "gemini":
                url = "https://generativelanguage.googleapis.com/v1beta/models"
                params = {"key": api_key or "", "pageSize": 100}
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json().get("models", [])
                for m in data:
                    raw_name = m.get("name", "")
                    # Strip "models/" prefix
                    mid = raw_name.replace("models/", "", 1) if raw_name.startswith("models/") else raw_name
                    dn = m.get("displayName", mid)
                    full_id = f"{prefix}/{mid}" if prefix and not mid.startswith(prefix + "/") else mid
                    models.append({"id": full_id, "display_name": dn})

            elif api_type == "generic":
                # OpenAI-compatible format against custom api_base
                url = f"{api_base.rstrip('/')}/models"
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for m in data:
                    mid = m.get("id", "")
                    full_id = f"{prefix}/{mid}" if prefix and not mid.startswith(prefix + "/") else mid
                    models.append({"id": full_id, "display_name": mid})

            else:
                # OpenAI
                url = "https://api.openai.com/v1/models"
                headers = {"Authorization": f"Bearer {api_key or ''}"}
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for m in data:
                    mid = m.get("id", "")
                    models.append({"id": mid, "display_name": mid})

            # Sort alphabetically by id
            models.sort(key=lambda x: x["id"])
            return {"models": models}

    except httpx.HTTPStatusError as e:
        return JSONResponse(
            {"error": f"Provider API returned {e.response.status_code}: {e.response.text[:200]}"},
            status_code=502,
        )
    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to fetch models: {str(e)[:200]}"},
            status_code=502,
        )


@app.delete("/api/config/provider")
async def delete_provider(request: Request, user: dict = Depends(auth.get_current_user)):
    """Remove a provider and all its models."""
    body = await request.json()
    prov_key = body.get("provider_key")
    if not prov_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)

    config = await _get_user_config(user["id"])

    if prov_key not in config.get("providers", {}):
        return JSONResponse({"error": f"Provider '{prov_key}' not found"}, status_code=404)

    del config["providers"][prov_key]
    await _save_user_config(user["id"], config)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Env / API Key management
# ---------------------------------------------------------------------------
ENV_PATH = _dir / ".env"


def _parse_env_file() -> list[tuple[str, str, str]]:
    """Parse .env file → list of (key_name, value, raw_line). Skips comments/blanks."""
    entries = []
    if not ENV_PATH.exists():
        return entries
    for line in ENV_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', stripped)
        if match:
            entries.append((match.group(1), match.group(2), line))
    return entries


def _mask_value(val: str) -> str:
    """Mask all but last 4 chars: ****xxxx."""
    if not val or len(val) <= 4:
        return "****"
    return "****" + val[-4:]


@app.get("/api/env")
async def get_env_keys(user: dict = Depends(auth.require_admin)):
    """List env keys with masked values."""
    entries = _parse_env_file()
    env_keys = {name: val for name, val, _ in entries}

    # Also include api_key_env refs from admin's config that may be missing from .env
    config = await _get_user_config(user["id"])
    for prov_cfg in config.get("providers", {}).values():
        ref = prov_cfg.get("api_key_env", "")
        if ref and ref not in env_keys:
            env_keys[ref] = ""

    keys = []
    for name, val in env_keys.items():
        keys.append({
            "name": name,
            "masked_value": _mask_value(val) if val else "",
            "is_set": bool(val),
        })
    return {"keys": keys}


@app.put("/api/env")
async def update_env_key(request: Request, user: dict = Depends(auth.require_admin)):
    """Update or add an env key in .env file."""
    body = await request.json()
    name = body.get("name", "").strip()
    value = body.get("value", "")
    if not name or not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
        return JSONResponse({"error": "Invalid key name"}, status_code=400)

    # Read existing .env, preserving comments/order
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()

    # Try to update existing key
    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', stripped)
        if match and match.group(1) == name:
            lines[i] = f"{name}={value}"
            updated = True
            break

    if not updated:
        lines.append(f"{name}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n")

    # Reload into current process
    os.environ[name] = value

    return {"status": "ok", "name": name, "masked_value": _mask_value(value)}


@app.delete("/api/env")
async def delete_env_key(request: Request, user: dict = Depends(auth.require_admin)):
    """Remove an env key from .env file."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    if not ENV_PATH.exists():
        return JSONResponse({"error": "No .env file"}, status_code=404)

    lines = ENV_PATH.read_text().splitlines()
    new_lines = []
    removed = False
    for line in lines:
        stripped = line.strip()
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', stripped)
        if match and match.group(1) == name:
            removed = True
            continue
        new_lines.append(line)

    if not removed:
        return JSONResponse({"error": f"Key '{name}' not found"}, status_code=404)

    ENV_PATH.write_text("\n".join(new_lines) + "\n")
    os.environ.pop(name, None)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Per-User API Key Management
# ---------------------------------------------------------------------------

@app.get("/api/keys")
async def get_my_keys(user: dict = Depends(auth.get_current_user)):
    """List the current user's API keys (provider + status, never plaintext)."""
    user_keys = await db.get_user_keys(user["id"])

    # Build provider list from user's config so user sees ALL their providers
    config = await _get_user_config(user["id"])
    providers = {}
    for prov_key, prov_cfg in config.get("providers", {}).items():
        key_env = prov_cfg.get("api_key_env", "")
        has_global = bool(prov_cfg.get("api_key")) or (bool(os.getenv(key_env)) if key_env else False)
        providers[prov_key] = {
            "provider_key": prov_key,
            "display_name": prov_cfg.get("display_name", prov_key),
            "key_env_name": key_env,
            "has_global_key": has_global,
            "has_user_key": False,
            "user_key_updated_at": None,
        }

    # Overlay user keys
    for uk in user_keys:
        pk = uk["provider_key"]
        if pk in providers:
            providers[pk]["has_user_key"] = True
            providers[pk]["user_key_updated_at"] = uk["updated_at"]

    return {"keys": list(providers.values())}


@app.put("/api/keys")
async def set_my_key(request: Request, user: dict = Depends(auth.get_current_user)):
    """Set or update the current user's API key for a provider."""
    body = await request.json()
    provider_key = body.get("provider_key", "").strip()
    value = body.get("value", "")

    if not provider_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)
    if not value:
        return JSONResponse({"error": "value required"}, status_code=400)

    # Validate provider exists in user's config
    config = await _get_user_config(user["id"])
    prov_cfg = config.get("providers", {}).get(provider_key)
    if not prov_cfg:
        return JSONResponse({"error": f"Provider '{provider_key}' not found"}, status_code=404)

    key_name = prov_cfg.get("api_key_env", f"{provider_key.upper()}_API_KEY")
    encrypted = vault.encrypt(value)
    key_id = await db.upsert_user_key(user["id"], provider_key, key_name, encrypted)

    return {"status": "ok", "key_id": key_id, "provider_key": provider_key}


@app.delete("/api/keys")
async def delete_my_key(request: Request, user: dict = Depends(auth.get_current_user)):
    """Remove the current user's API key for a provider."""
    body = await request.json()
    provider_key = body.get("provider_key", "").strip()

    if not provider_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)

    deleted = await db.delete_user_key(user["id"], provider_key)
    if not deleted:
        return JSONResponse({"error": "Key not found"}, status_code=404)

    return {"status": "ok"}


@app.post("/api/benchmark/cancel")
async def cancel_benchmark(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running benchmark."""
    _get_user_cancel(user["id"]).set()

    await db.log_audit(
        user_id=user["id"],
        username=user.get("email", ""),
        action="benchmark_cancel",
        resource_type="benchmark",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )

    return {"status": "ok", "message": "Cancellation requested"}


@app.get("/api/user/rate-limit")
async def get_rate_limit(user: dict = Depends(auth.get_current_user)):
    """Return the user's current rate limit status."""
    allowed, remaining = _check_rate_limit(user["id"])
    return {"limit": RATE_LIMIT_PER_HOUR, "remaining": remaining, "window": "1 hour"}


@app.post("/api/benchmark")
async def run_benchmark(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run benchmarks and stream results via SSE."""
    body = await request.json()
    model_ids = body.get("models", [])
    runs = body.get("runs", 3)
    max_tokens = body.get("max_tokens", 512)
    temperature = body.get("temperature", 0.7)
    prompt = body.get("prompt", "")
    context_tiers = body.get("context_tiers", [0])
    warmup = body.get("warmup", True)

    # --- Input validation ---
    if not isinstance(model_ids, list) or len(model_ids) == 0:
        return JSONResponse(
            {"error": "models must be a non-empty list"},
            status_code=400,
        )
    if not isinstance(runs, (int, float)) or int(runs) < 1 or int(runs) > 20:
        return JSONResponse(
            {"error": "runs must be between 1 and 20"},
            status_code=400,
        )
    runs = int(runs)
    if not isinstance(max_tokens, (int, float)) or int(max_tokens) < 1 or int(max_tokens) > 16384:
        return JSONResponse(
            {"error": "max_tokens must be between 1 and 16384"},
            status_code=400,
        )
    max_tokens = int(max_tokens)
    if not isinstance(temperature, (int, float)) or float(temperature) < 0.0 or float(temperature) > 2.0:
        return JSONResponse(
            {"error": "temperature must be between 0.0 and 2.0"},
            status_code=400,
        )
    temperature = float(temperature)

    # --- Rate limit check ---
    allowed, remaining = _check_rate_limit(user["id"])
    if not allowed:
        return JSONResponse(
            {"error": f"Rate limit exceeded. Max {RATE_LIMIT_PER_HOUR} benchmarks per hour."},
            status_code=429,
        )
    _record_rate_limit(user["id"])

    # Audit: benchmark start
    await db.log_audit(
        user_id=user["id"],
        username=user.get("email", ""),
        action="benchmark_start",
        resource_type="benchmark",
        detail={"models": model_ids, "runs": runs, "context_tiers": context_tiers},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )

    # --- Concurrent benchmark guard (per-user) ---
    user_lock = _get_user_lock(user["id"])
    if user_lock.locked():
        return JSONResponse(
            {"error": "Benchmark already running"},
            status_code=409,
        )

    config = await _get_user_config(user["id"])
    defaults = config.get("defaults", {})
    all_targets = build_targets(config)

    # Filter to requested models (or run all if none specified)
    if model_ids:
        targets = [t for t in all_targets if t.model_id in model_ids]
    else:
        targets = all_targets

    # Inject per-user API keys (user key > global fallback)
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user["id"], t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    if not prompt.strip():
        prompt = defaults.get("prompt", "Explain recursion in programming with a Python example.")

    cancel_event = _get_user_cancel(user["id"])

    async def generate():
        await user_lock.acquire()
        cancel_event.clear()
        try:
            # Calculate total runs across all tiers
            total = 0
            for tier in context_tiers:
                for target in targets:
                    headroom = target.context_window - max_tokens - 100
                    if tier == 0 or tier <= headroom:
                        total += runs

            queue = asyncio.Queue()

            # Group targets by provider for parallel execution
            # Within a provider: sequential (same endpoint, avoid self-contention)
            # Across providers: fully parallel (independent endpoints)
            provider_groups = {}
            for target in targets:
                provider_groups.setdefault(target.provider, []).append(target)

            async def run_provider(prov_targets):
                """Run all benchmarks for one provider sequentially."""
                for tier in context_tiers:
                    for target in prov_targets:
                        if cancel_event.is_set():
                            return
                        headroom = target.context_window - max_tokens - 100
                        if tier > 0 and tier > headroom:
                            await queue.put({
                                "type": "skipped",
                                "provider": target.provider,
                                "model": target.display_name,
                                "model_id": target.model_id,
                                "context_tokens": tier,
                                "reason": f"{tier // 1000}K exceeds {target.context_window // 1000}K context window",
                            })
                            continue

                        # Warm-up run (discarded)
                        if warmup:
                            await async_run_single(
                                target, prompt, max_tokens, temperature, tier
                            )

                        for r in range(runs):
                            if cancel_event.is_set():
                                return
                            result = await async_run_single(
                                target, prompt, max_tokens, temperature, tier
                            )
                            await queue.put({
                                "type": "result",
                                "provider": target.provider,
                                "model": target.display_name,
                                "model_id": target.model_id,
                                "run": r + 1,
                                "runs": runs,
                                "context_tokens": tier,
                                "ttft_ms": round(result.ttft_ms, 2),
                                "total_time_s": round(result.total_time_s, 3),
                                "output_tokens": result.output_tokens,
                                "input_tokens": result.input_tokens,
                                "tokens_per_second": round(result.tokens_per_second, 2),
                                "input_tokens_per_second": round(result.input_tokens_per_second, 2),
                                "cost": round(result.cost, 8),
                                "success": result.success,
                                "error": result.error,
                            })

            # Launch all provider groups as concurrent tasks
            tasks = [asyncio.create_task(run_provider(g))
                     for g in provider_groups.values()]

            async def sentinel():
                """Wait for all provider tasks to finish, then signal done."""
                await asyncio.gather(*tasks, return_exceptions=True)
                await queue.put(None)

            asyncio.create_task(sentinel())

            # Consume queue and yield SSE events as they arrive (interleaved)
            current = 0
            all_results = []
            while True:
                # Use timeout to send heartbeats while waiting for results
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield _sse({"type": "heartbeat"})
                    continue
                if item is None:
                    break
                if cancel_event.is_set():
                    # Cancel remaining tasks
                    for t in tasks:
                        t.cancel()
                    yield _sse({"type": "cancelled"})
                    return
                if item["type"] == "result":
                    current += 1
                    # Emit progress before the result data
                    yield _sse({
                        "type": "progress",
                        "current": current,
                        "total": total,
                        "provider": item["provider"],
                        "model": item["model"],
                        "run": item["run"],
                        "runs": item["runs"],
                        "context_tokens": item["context_tokens"],
                    })
                    all_results.append(item)
                yield _sse(item)

            # Save results to JSON file (for CLI compatibility)
            if all_results:
                agg_results = _aggregate(all_results, config)
                saved = save_results(agg_results, prompt, context_tiers=context_tiers)

                # Save results to DB for per-user history
                await db.save_benchmark_run(
                    user_id=user["id"],
                    prompt=prompt,
                    context_tiers=json.dumps(context_tiers),
                    results_json=json.dumps(all_results),
                )

                # Audit: benchmark complete
                await db.log_audit(
                    user_id=user["id"],
                    username=user.get("email", ""),
                    action="benchmark_complete",
                    resource_type="benchmark",
                    detail={"models": model_ids, "result_count": len(all_results)},
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent", ""),
                )

                yield _sse({"type": "complete", "saved_to": str(saved)})
            else:
                yield _sse({"type": "complete", "saved_to": ""})

        except Exception as e:
            yield _sse({"type": "error", "message": sanitize_error(str(e))})
        finally:
            user_lock.release()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/api/history")
async def get_history(user: dict = Depends(auth.get_current_user)):
    """Get the current user's benchmark history from the database."""
    runs = await db.get_user_benchmark_runs(user["id"])
    # Parse results_json back to objects for the frontend
    for run in runs:
        if isinstance(run.get("results_json"), str):
            run["results"] = json.loads(run["results_json"])
            del run["results_json"]
        if isinstance(run.get("context_tiers"), str):
            try:
                run["context_tiers"] = json.loads(run["context_tiers"])
            except (json.JSONDecodeError, TypeError):
                pass
    return {"runs": runs}


@app.get("/api/history/{run_id}")
async def get_history_run(run_id: str, user: dict = Depends(auth.get_current_user)):
    """Return a specific benchmark run from the database."""
    run = await db.get_benchmark_run(run_id, user["id"])
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    if isinstance(run.get("results_json"), str):
        run["results"] = json.loads(run["results_json"])
        del run["results_json"]
    if isinstance(run.get("context_tiers"), str):
        try:
            run["context_tiers"] = json.loads(run["context_tiers"])
        except (json.JSONDecodeError, TypeError):
            pass
    return run


@app.delete("/api/history/{run_id}")
async def delete_history_run(run_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a benchmark run from history."""
    deleted = await db.delete_benchmark_run(run_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

@app.get("/api/config/prompts")
async def get_prompt_templates(user: dict = Depends(auth.get_current_user)):
    """Return prompt templates from user's config."""
    config = await _get_user_config(user["id"])
    return config.get("prompt_templates", {})


@app.post("/api/config/prompts")
async def add_prompt_template(request: Request, user: dict = Depends(auth.get_current_user)):
    """Add a new prompt template."""
    body = await request.json()
    key = body.get("key", "").strip()
    if not key or not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
        return JSONResponse({"error": "Invalid template key"}, status_code=400)
    label = body.get("label", key)
    category = body.get("category", "general")
    prompt_text = body.get("prompt", "").strip()
    if not prompt_text:
        return JSONResponse({"error": "prompt is required"}, status_code=400)

    config = await _get_user_config(user["id"])

    config.setdefault("prompt_templates", {})[key] = {
        "category": category,
        "label": label,
        "prompt": prompt_text,
    }

    await _save_user_config(user["id"], config)
    return {"status": "ok", "key": key}


# ---------------------------------------------------------------------------
# Tool Eval: Suites, Test Cases, Eval Execution, History
# ---------------------------------------------------------------------------


def _validate_tools(tools: list) -> str | None:
    """Return error message if tools are invalid, None if ok."""
    if not isinstance(tools, list) or len(tools) == 0:
        return "tools must be a non-empty array"
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict):
            return f"tools[{i}] must be an object"
        if tool.get("type") != "function":
            return f"tools[{i}].type must be 'function'"
        fn = tool.get("function", {})
        if not fn.get("name"):
            return f"tools[{i}].function.name is required"
    return None


def _parse_expected_tool(value):
    """Parse expected_tool from DB storage format to Python type.

    DB stores: None (NULL), "tool_name" (string), or '["a","b"]' (JSON array).
    Returns: None, str, or list[str].
    """
    if value is None:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return value


def _serialize_expected_tool(value) -> str | None:
    """Serialize expected_tool for DB storage.

    Accepts: None, str, or list[str].
    Returns: None, "tool_name", or '["a","b"]'.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)


# --- Tool Suites ---

@app.get("/api/tool-suites")
async def list_tool_suites(user: dict = Depends(auth.get_current_user)):
    """List user's tool suites."""
    suites = await db.get_tool_suites(user["id"])
    return {"suites": suites}


@app.post("/api/tool-suites")
async def create_tool_suite(request: Request, user: dict = Depends(auth.get_current_user)):
    """Create a new tool suite."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    description = body.get("description", "")
    tools = body.get("tools", [])
    if tools:
        err = _validate_tools(tools)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    suite_id = await db.create_tool_suite(user["id"], name, description, json.dumps(tools))
    return {"status": "ok", "suite_id": suite_id}


@app.post("/api/tool-eval/import")
async def import_tool_suite(request: Request, user: dict = Depends(auth.get_current_user)):
    """Import a complete tool suite (tools + test cases) from JSON."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    description = body.get("description", "")
    tools = body.get("tools", [])
    if tools:
        err = _validate_tools(tools)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    suite_id = await db.create_tool_suite(user["id"], name, description, json.dumps(tools))
    test_cases = body.get("test_cases", [])
    created = 0
    for item in test_cases:
        prompt = item.get("prompt", "").strip()
        if not prompt:
            continue
        expected_tool = _serialize_expected_tool(item.get("expected_tool"))
        expected_params = json.dumps(item["expected_params"]) if item.get("expected_params") is not None else None
        param_scoring = item.get("param_scoring", "exact")
        await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring)
        created += 1
    return {"status": "ok", "suite_id": suite_id, "test_cases_created": created}


@app.get("/api/tool-suites/{suite_id}")
async def get_tool_suite(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full suite with tools and test cases."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    suite["tools"] = json.loads(suite["tools_json"])
    del suite["tools_json"]
    cases = await db.get_test_cases(suite_id)
    for c in cases:
        c["expected_tool"] = _parse_expected_tool(c["expected_tool"])
        if c["expected_params"]:
            try:
                c["expected_params"] = json.loads(c["expected_params"])
            except (json.JSONDecodeError, TypeError):
                pass
    suite["test_cases"] = cases
    return suite


@app.put("/api/tool-suites/{suite_id}")
async def update_tool_suite(suite_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Update suite name/description/tools."""
    body = await request.json()
    name = body.get("name")
    description = body.get("description")
    tools = body.get("tools")
    tools_json = None
    if tools is not None:
        if tools:
            err = _validate_tools(tools)
            if err:
                return JSONResponse({"error": err}, status_code=400)
        tools_json = json.dumps(tools)
    updated = await db.update_tool_suite(suite_id, user["id"], name=name, description=description, tools_json=tools_json)
    if not updated:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    return {"status": "ok"}


@app.delete("/api/tool-suites/{suite_id}")
async def delete_tool_suite(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a suite and its test cases."""
    deleted = await db.delete_tool_suite(suite_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    return {"status": "ok"}


# --- Test Cases ---

@app.get("/api/tool-suites/{suite_id}/cases")
async def list_test_cases(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """List test cases for a suite."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(suite_id)
    for c in cases:
        c["expected_tool"] = _parse_expected_tool(c["expected_tool"])
        if c["expected_params"]:
            try:
                c["expected_params"] = json.loads(c["expected_params"])
            except (json.JSONDecodeError, TypeError):
                pass
    return {"cases": cases}


@app.post("/api/tool-suites/{suite_id}/cases")
async def create_test_cases(suite_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Add test case(s) to a suite. Supports single or bulk via 'cases' array."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    body = await request.json()

    # Bulk mode
    if "cases" in body and isinstance(body["cases"], list):
        created = 0
        for item in body["cases"]:
            prompt = item.get("prompt", "").strip()
            if not prompt:
                continue
            expected_tool = _serialize_expected_tool(item.get("expected_tool"))
            expected_params = json.dumps(item["expected_params"]) if item.get("expected_params") is not None else None
            param_scoring = item.get("param_scoring", "exact")
            await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring)
            created += 1
        return {"status": "ok", "created": created}

    # Single mode
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    expected_tool = _serialize_expected_tool(body.get("expected_tool"))
    expected_params = json.dumps(body["expected_params"]) if body.get("expected_params") is not None else None
    param_scoring = body.get("param_scoring", "exact")
    case_id = await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring)
    return {"status": "ok", "case_id": case_id}


@app.put("/api/tool-suites/{suite_id}/cases/{case_id}")
async def update_test_case(suite_id: str, case_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Update a test case."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    body = await request.json()
    prompt = body.get("prompt")
    expected_tool = _serialize_expected_tool(body.get("expected_tool")) if "expected_tool" in body else None
    expected_params = json.dumps(body["expected_params"]) if "expected_params" in body and body["expected_params"] is not None else None
    param_scoring = body.get("param_scoring")
    updated = await db.update_test_case(case_id, suite_id, prompt=prompt, expected_tool=expected_tool, expected_params=expected_params, param_scoring=param_scoring)
    if not updated:
        return JSONResponse({"error": "Test case not found"}, status_code=404)
    return {"status": "ok"}


@app.delete("/api/tool-suites/{suite_id}/cases/{case_id}")
async def delete_test_case(suite_id: str, case_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a test case."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    deleted = await db.delete_test_case(case_id, suite_id)
    if not deleted:
        return JSONResponse({"error": "Test case not found"}, status_code=404)
    return {"status": "ok"}


# --- Eval History ---

@app.get("/api/tool-eval/history")
async def list_tool_eval_runs(user: dict = Depends(auth.get_current_user)):
    """List user's past eval runs."""
    runs = await db.get_tool_eval_runs(user["id"])
    for run in runs:
        if isinstance(run.get("models_json"), str):
            run["models"] = json.loads(run["models_json"])
            del run["models_json"]
        if isinstance(run.get("summary_json"), str):
            run["summary"] = json.loads(run["summary_json"])
            del run["summary_json"]
    return {"runs": runs}


@app.get("/api/tool-eval/history/{eval_id}")
async def get_tool_eval_run(eval_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full eval run details."""
    run = await db.get_tool_eval_run(eval_id, user["id"])
    if not run:
        return JSONResponse({"error": "Eval run not found"}, status_code=404)
    if isinstance(run.get("models_json"), str):
        run["models"] = json.loads(run["models_json"])
        del run["models_json"]
    if isinstance(run.get("results_json"), str):
        run["results"] = json.loads(run["results_json"])
        del run["results_json"]
    if isinstance(run.get("summary_json"), str):
        run["summary"] = json.loads(run["summary_json"])
        del run["summary_json"]
    return run


@app.delete("/api/tool-eval/history/{eval_id}")
async def delete_tool_eval_run(eval_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete an eval run."""
    deleted = await db.delete_tool_eval_run(eval_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Eval run not found"}, status_code=404)
    return {"status": "ok"}


# --- Eval Engine: Scoring ---

def score_tool_selection(expected_tool, actual_tool: str | None) -> float:
    """Score tool selection accuracy for one test case.

    Args:
        expected_tool: str, list[str], or None
        actual_tool: str or None (what the model actually called)

    Returns: 1.0 or 0.0
    """
    if expected_tool is None:
        return 1.0 if actual_tool is None else 0.0
    if actual_tool is None:
        return 0.0
    if isinstance(expected_tool, list):
        return 1.0 if actual_tool.lower() in [e.lower() for e in expected_tool] else 0.0
    return 1.0 if actual_tool.lower() == expected_tool.lower() else 0.0


def score_params(expected_params: dict | None, actual_params: dict | None) -> float | None:
    """Score parameter accuracy for one test case.

    Returns: float 0.0-1.0, or None if params not scored.
    """
    if expected_params is None:
        return None
    if not expected_params:
        return 1.0  # empty dict = nothing to check
    if actual_params is None:
        return 0.0

    correct = 0
    total = len(expected_params)
    for key, expected_val in expected_params.items():
        if key not in actual_params:
            continue
        actual_val = actual_params[key]
        # String comparison: case-insensitive
        if isinstance(expected_val, str) and isinstance(actual_val, str):
            if expected_val.lower() == actual_val.lower():
                correct += 1
        # Numeric comparison
        elif isinstance(expected_val, (int, float)) and isinstance(actual_val, (int, float)):
            if float(expected_val) == float(actual_val):
                correct += 1
        # Exact match for everything else
        elif expected_val == actual_val:
            correct += 1

    return correct / total if total > 0 else 1.0


def compute_overall_score(tool_score: float, param_score: float | None) -> float:
    """Compute weighted overall score.

    When params scored: 0.6 * tool_score + 0.4 * param_score
    When params not scored: tool_score
    """
    if param_score is None:
        return tool_score
    return 0.6 * tool_score + 0.4 * param_score


# --- Eval Engine: Single Eval Execution ---

async def run_single_eval(
    target: Target,
    tools: list[dict],
    test_case: dict,
    temperature: float,
    tool_choice: str = "required",
) -> dict:
    """Run one test case against one model. Returns result dict.

    Uses litellm.acompletion() (non-streaming, since we need tool_calls).
    """
    # Parse expected values
    expected_tool = _parse_expected_tool(test_case.get("expected_tool"))
    expected_params = test_case.get("expected_params")
    if isinstance(expected_params, str):
        try:
            expected_params = json.loads(expected_params)
        except (json.JSONDecodeError, TypeError):
            expected_params = None

    result = {
        "model_id": target.model_id,
        "test_case_id": test_case["id"],
        "prompt": test_case["prompt"],
        "expected_tool": expected_tool,
        "expected_params": expected_params,
        "actual_tool": None,
        "actual_params": None,
        "tool_selection_score": 0.0,
        "param_accuracy": None,
        "overall_score": 0.0,
        "success": True,
        "error": "",
        "latency_ms": 0,
        "raw_request": None,
        "raw_response": None,
    }

    kwargs = {
        "model": target.model_id,
        "messages": [{"role": "user", "content": test_case["prompt"]}],
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": 1024,
        "timeout": 60,
        "num_retries": 1,
    }
    if target.api_base:
        kwargs["api_base"] = target.api_base
    if target.api_key:
        kwargs["api_key"] = target.api_key
    # Only add temperature if not skipped
    if "temperature" not in (target.skip_params or []):
        kwargs["temperature"] = temperature
    # Remove other skipped params
    if target.skip_params:
        for p in target.skip_params:
            if p != "temperature":
                kwargs.pop(p, None)

    # Capture raw request (sanitize: remove api_key)
    raw_req = dict(kwargs)
    raw_req.pop("api_key", None)
    # Convert tools to a summary (full tools are too large for storage)
    if "tools" in raw_req:
        raw_req["tools_summary"] = [t["function"]["name"] for t in raw_req["tools"]]
        raw_req["tools_count"] = len(raw_req["tools"])
        raw_req["tools"] = raw_req["tools"]  # Keep full tools for inspection
    result["raw_request"] = raw_req

    try:
        start = time.perf_counter()
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception:
            # Fallback: some providers don't support tool_choice="required"
            if kwargs.get("tool_choice") == "required":
                kwargs["tool_choice"] = "auto"
                response = await litellm.acompletion(**kwargs)
            else:
                raise
        latency_ms = (time.perf_counter() - start) * 1000

        message = response.choices[0].message
        if message.tool_calls and len(message.tool_calls) > 0:
            result["actual_tool"] = message.tool_calls[0].function.name
            try:
                result["actual_params"] = json.loads(message.tool_calls[0].function.arguments)
            except (json.JSONDecodeError, TypeError):
                result["actual_params"] = None
        else:
            result["actual_tool"] = None
            result["actual_params"] = None

        result["latency_ms"] = round(latency_ms)

        # Capture raw response
        raw_resp = {
            "id": getattr(response, "id", None),
            "model": getattr(response, "model", None),
            "choices": [],
            "usage": None,
        }
        if hasattr(response, "usage") and response.usage:
            raw_resp["usage"] = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }
        for choice in response.choices:
            c = {
                "index": choice.index,
                "finish_reason": choice.finish_reason,
                "message": {
                    "role": getattr(choice.message, "role", None),
                    "content": getattr(choice.message, "content", None),
                    "tool_calls": None,
                }
            }
            if choice.message.tool_calls:
                c["message"]["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in choice.message.tool_calls
                ]
            raw_resp["choices"].append(c)
        result["raw_response"] = raw_resp

    except Exception as e:
        result["success"] = False
        result["error"] = sanitize_error(str(e)[:200], target.api_key)
        result["raw_request"] = raw_req
        return result

    # Score
    result["tool_selection_score"] = score_tool_selection(expected_tool, result["actual_tool"])
    result["param_accuracy"] = score_params(expected_params, result["actual_params"])
    result["overall_score"] = compute_overall_score(result["tool_selection_score"], result["param_accuracy"])

    return result


# --- Eval Engine: Summary Computation ---

def _compute_eval_summaries(results: list[dict], targets: list[Target]) -> list[dict]:
    """Compute per-model aggregate scores from individual results."""
    target_map = {t.model_id: t for t in targets}

    # Group by model_id
    by_model: dict[str, list[dict]] = {}
    for r in results:
        by_model.setdefault(r["model_id"], []).append(r)

    summaries = []
    for model_id, model_results in by_model.items():
        target = target_map.get(model_id)
        model_name = target.display_name if target else model_id
        provider = target.provider if target else ""

        tool_scores = [r["tool_selection_score"] for r in model_results if r["success"]]
        param_scores = [r["param_accuracy"] for r in model_results if r["success"] and r["param_accuracy"] is not None]
        overall_scores = [r["overall_score"] for r in model_results if r["success"]]

        tool_acc = (sum(tool_scores) / len(tool_scores) * 100) if tool_scores else 0.0
        param_acc = (sum(param_scores) / len(param_scores) * 100) if param_scores else 0.0
        overall = (sum(overall_scores) / len(overall_scores) * 100) if overall_scores else 0.0
        cases_passed = sum(1 for r in model_results if r["success"] and r["overall_score"] == 1.0)

        summaries.append({
            "model_id": model_id,
            "model_name": model_name,
            "provider": provider,
            "tool_accuracy_pct": round(tool_acc, 1),
            "param_accuracy_pct": round(param_acc, 1),
            "overall_pct": round(overall, 1),
            "cases_run": len(model_results),
            "cases_passed": cases_passed,
        })

    return summaries


# --- Eval Engine: SSE Endpoint ---

@app.post("/api/tool-eval")
async def run_tool_eval(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run tool calling eval and stream results via SSE."""
    body = await request.json()
    suite_id = body.get("suite_id")
    model_ids = body.get("models", [])
    temperature = body.get("temperature", 0.0)
    tool_choice = body.get("tool_choice", "required")

    # --- Validation ---
    if not suite_id:
        return JSONResponse({"error": "suite_id is required"}, status_code=400)
    if not isinstance(model_ids, list) or len(model_ids) == 0:
        return JSONResponse({"error": "models must be a non-empty list"}, status_code=400)
    if not isinstance(temperature, (int, float)) or float(temperature) < 0.0 or float(temperature) > 2.0:
        return JSONResponse({"error": "temperature must be between 0.0 and 2.0"}, status_code=400)
    temperature = float(temperature)
    if tool_choice not in ("auto", "required", "none"):
        return JSONResponse({"error": "tool_choice must be 'auto', 'required', or 'none'"}, status_code=400)

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(suite_id)
    if not cases:
        return JSONResponse({"error": "Suite has no test cases"}, status_code=400)
    tools = json.loads(suite["tools_json"])

    # Rate limit check
    allowed, remaining = _check_rate_limit(user["id"])
    if not allowed:
        return JSONResponse(
            {"error": f"Rate limit exceeded. Max {RATE_LIMIT_PER_HOUR} per hour."},
            status_code=429,
        )
    _record_rate_limit(user["id"])

    # Concurrent guard (shared with benchmarks)
    user_lock = _get_user_lock(user["id"])
    if user_lock.locked():
        return JSONResponse(
            {"error": "A benchmark or eval is already running"},
            status_code=409,
        )

    # Build targets from user config
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)
    targets = [t for t in all_targets if t.model_id in model_ids]
    if not targets:
        return JSONResponse({"error": "No matching models found in config"}, status_code=400)

    # Inject per-user API keys
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user["id"], t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    cancel_event = _get_user_cancel(user["id"])

    async def generate():
        await user_lock.acquire()
        cancel_event.clear()
        try:
            total = len(targets) * len(cases)
            queue = asyncio.Queue()

            # Group targets by provider
            provider_groups: dict[str, list[Target]] = {}
            for target in targets:
                provider_groups.setdefault(target.provider, []).append(target)

            async def run_provider(prov_targets):
                """Run all test cases for models in this provider."""
                for target in prov_targets:
                    for case in cases:
                        if cancel_event.is_set():
                            return
                        result = await run_single_eval(target, tools, case, temperature, tool_choice)
                        await queue.put(result)

            # Launch provider groups in parallel
            tasks = [asyncio.create_task(run_provider(g))
                     for g in provider_groups.values()]

            async def sentinel():
                await asyncio.gather(*tasks, return_exceptions=True)
                await queue.put(None)

            asyncio.create_task(sentinel())

            # Consume and emit SSE events
            current = 0
            all_results = []
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield _sse({"type": "heartbeat"})
                    continue
                if item is None:
                    break
                if cancel_event.is_set():
                    for t in tasks:
                        t.cancel()
                    yield _sse({"type": "cancelled"})
                    return

                current += 1
                # Find target display name
                target_map = {t.model_id: t for t in targets}
                t = target_map.get(item["model_id"])
                model_display = t.display_name if t else item["model_id"]

                yield _sse({
                    "type": "progress",
                    "current": current,
                    "total": total,
                    "model": model_display,
                    "test_case": item["test_case_id"],
                })
                yield _sse({"type": "result", **item})
                all_results.append(item)

            # Compute per-model summaries
            summaries = _compute_eval_summaries(all_results, targets)
            for s in summaries:
                yield _sse({"type": "model_summary", **s})

            # Save to DB
            eval_id = await db.save_tool_eval_run(
                user_id=user["id"],
                suite_id=suite["id"],
                suite_name=suite["name"],
                models_json=json.dumps(model_ids),
                results_json=json.dumps(all_results),
                summary_json=json.dumps(summaries),
                temperature=temperature,
            )

            yield _sse({"type": "complete", "eval_id": eval_id})

        except Exception as e:
            yield _sse({"type": "error", "message": sanitize_error(str(e))})
        finally:
            user_lock.release()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.post("/api/tool-eval/cancel")
async def cancel_tool_eval(user: dict = Depends(auth.get_current_user)):
    """Cancel a running tool eval."""
    _get_user_cancel(user["id"]).set()
    return {"status": "ok", "message": "Cancellation requested"}


# ---------------------------------------------------------------------------
# MCP Integration
# ---------------------------------------------------------------------------


async def discover_mcp_tools(url: str, timeout: float = 10.0) -> dict:
    """Connect to an MCP server via SSE and return discovered tools.

    Raises ValueError for invalid URLs, TimeoutError for timeouts,
    and ConnectionError for connection failures.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    if not parsed.hostname:
        raise ValueError("Invalid URL: missing hostname")

    try:
        async with sse_client(url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(session.initialize(), timeout=timeout)
                result = await asyncio.wait_for(session.list_tools(), timeout=timeout)

                server_name = "unknown"
                if hasattr(session, "server_info") and session.server_info:
                    server_name = getattr(session.server_info, "name", "unknown")

                return {
                    "server_name": server_name,
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "inputSchema": t.inputSchema if t.inputSchema else {"type": "object", "properties": {}},
                            "parameter_count": len((t.inputSchema or {}).get("properties", {})),
                        }
                        for t in result.tools
                    ],
                }
    except asyncio.TimeoutError:
        raise TimeoutError("Connection timed out. The MCP server may be unreachable.")
    except OSError as e:
        raise ConnectionError(
            f"Could not connect to MCP server. Check the URL and ensure the server is running. ({e})"
        )
    except Exception as e:
        if isinstance(e, (ValueError, TimeoutError, ConnectionError)):
            raise
        raise ConnectionError(
            f"The server responded but doesn't appear to be a valid MCP server. ({type(e).__name__}: {e})"
        )


def mcp_tool_to_openai(mcp_tool: dict) -> dict:
    """Convert an MCP tool schema to OpenAI function calling format."""
    description = mcp_tool.get("description", "")
    if len(description) > 1024:
        description = description[:1021] + "..."

    return {
        "type": "function",
        "function": {
            "name": mcp_tool["name"],
            "description": description,
            "parameters": mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
        },
    }


def generate_test_case(tool: dict) -> dict:
    """Generate a sample test case from an OpenAI-format tool definition."""
    fn = tool["function"]
    params = fn.get("parameters", {})
    properties = params.get("properties", {})
    required = params.get("required", [])

    # Build example params from required fields only
    example_params = {}
    for name, schema in properties.items():
        if name in required:
            example_params[name] = _example_value(name, schema)

    # Build a concrete prompt that includes example param values
    desc = fn.get("description", fn["name"])
    if example_params:
        param_parts = [f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                       for k, v in example_params.items()]
        prompt = f"Use the {fn['name']} tool: {desc.rstrip('.')}. Use these values: {', '.join(param_parts)}"
    else:
        prompt = f"Use the {fn['name']} tool to {desc.lower().rstrip('.')}"

    return {
        "prompt": prompt,
        "expected_tool": fn["name"],
        "expected_params": example_params if example_params else None,
    }


def _example_value(param_name: str, schema: dict):
    """Generate a realistic placeholder value based on param name and JSON Schema."""
    t = schema.get("type", "string")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "string":
        # Use param name to generate realistic values instead of description text
        name_lower = param_name.lower()
        if "url" in name_lower or "uri" in name_lower or "link" in name_lower:
            return "https://example.com"
        if "path" in name_lower or "file" in name_lower:
            return "/tmp/example.txt"
        if "email" in name_lower:
            return "user@example.com"
        if "name" in name_lower:
            return "example"
        if "query" in name_lower or "search" in name_lower:
            return "test query"
        if "selector" in name_lower or "css" in name_lower:
            return "#main-content"
        if "city" in name_lower or "location" in name_lower:
            return "San Francisco"
        if "code" in name_lower or "script" in name_lower:
            return "console.log('hello')"
        return "example"
    if t == "number" or t == "integer":
        return 42
    if t == "boolean":
        return True
    if t == "array":
        return []
    return "example"


@app.post("/api/mcp/discover")
async def mcp_discover(request: Request, user: dict = Depends(auth.get_current_user)):
    """Connect to an MCP server and return discovered tools."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "url is required"}, status_code=400)
    url = (body.get("url") or "").strip()

    if not url:
        return JSONResponse({"error": "url is required"}, status_code=400)

    try:
        result = await discover_mcp_tools(url, timeout=10.0)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except TimeoutError as e:
        return JSONResponse({"error": str(e)}, status_code=504)
    except ConnectionError as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    if not result["tools"]:
        return JSONResponse(
            {"error": "Connected successfully, but the server has no tools available."},
            status_code=200,
        )

    return {
        "status": "ok",
        "server_name": result["server_name"],
        "tools": result["tools"],
        "tool_count": len(result["tools"]),
    }


@app.post("/api/mcp/import")
async def mcp_import(request: Request, user: dict = Depends(auth.get_current_user)):
    """Import selected MCP tools as a new tool suite."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "No tools selected"}, status_code=400)
    tools = body.get("tools", [])
    suite_name = (body.get("suite_name") or "").strip()
    suite_description = body.get("suite_description", "")
    generate_tests = body.get("generate_test_cases", False)

    if not tools:
        return JSONResponse({"error": "No tools selected"}, status_code=400)

    # Default suite name from first 3 tool names
    if not suite_name:
        names = [t.get("name", "?") for t in tools[:3]]
        suffix = "..." if len(tools) > 3 else ""
        suite_name = f"MCP: {', '.join(names)}{suffix}"

    # Deduplicate tool names
    seen_names = {}
    for tool in tools:
        name = tool["name"]
        if name in seen_names:
            seen_names[name] += 1
            tool["name"] = f"{name}_{seen_names[name]}"
        else:
            seen_names[name] = 1

    # Convert MCP schemas to OpenAI format
    openai_tools = [mcp_tool_to_openai(t) for t in tools]

    # Validate converted tools using existing validator
    err = _validate_tools(openai_tools)
    if err:
        return JSONResponse({"error": f"Schema conversion error: {err}"}, status_code=400)

    # Create suite via existing DB function
    suite_id = await db.create_tool_suite(
        user["id"], suite_name, suite_description, json.dumps(openai_tools)
    )

    # Generate test cases if requested
    test_cases_generated = 0
    if generate_tests:
        for tool in openai_tools:
            tc = generate_test_case(tool)
            await db.create_test_case(
                suite_id,
                tc["prompt"],
                tc["expected_tool"],
                json.dumps(tc["expected_params"]) if tc["expected_params"] else None,
                "exact",
            )
            test_cases_generated += 1

    return {
        "status": "ok",
        "suite_id": suite_id,
        "tools_imported": len(openai_tools),
        "test_cases_generated": test_cases_generated,
    }


# ---------------------------------------------------------------------------
# Provider health check
# ---------------------------------------------------------------------------

@app.get("/api/health/providers")
async def health_check_providers(user: dict = Depends(auth.get_current_user)):
    """Check connectivity to each configured provider with a tiny completion."""
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)

    # Inject per-user keys so health check validates the user's actual keys
    user_keys_cache = {}
    for t in all_targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user["id"], t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    all_targets = inject_user_keys(all_targets, user_keys_cache)

    # Pick one model per provider for the health check
    provider_targets = {}
    for t in all_targets:
        if t.provider not in provider_targets:
            provider_targets[t.provider] = t

    async def check_one(name: str, target: Target) -> dict:
        kwargs = {
            "model": target.model_id,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5,
            "timeout": 10,
        }
        if target.api_base:
            kwargs["api_base"] = target.api_base
        if target.api_key:
            kwargs["api_key"] = target.api_key
        if target.skip_params:
            for p in target.skip_params:
                kwargs.pop(p, None)

        start = time.perf_counter()
        try:
            await litellm.acompletion(**kwargs)
            latency = (time.perf_counter() - start) * 1000
            return {"name": name, "status": "ok", "latency_ms": round(latency)}
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return {"name": name, "status": "error", "latency_ms": round(latency), "error": sanitize_error(str(e)[:200], target.api_key)}

    results = await asyncio.gather(
        *[check_one(name, t) for name, t in provider_targets.items()]
    )
    return {"providers": list(results)}


# ---------------------------------------------------------------------------
# Async benchmark execution (used by SSE endpoint)
# ---------------------------------------------------------------------------


def inject_user_keys(targets: list[Target], user_keys_cache: dict[str, str]) -> list[Target]:
    """Clone targets with user-specific API keys injected.

    Key resolution: user key > global key (already on target).
    Returns a NEW list of Target objects (originals are not mutated).
    """
    from dataclasses import replace

    injected = []
    for target in targets:
        if not target.provider_key:
            injected.append(target)
            continue

        encrypted = user_keys_cache.get(target.provider_key)
        if encrypted:
            try:
                decrypted = vault.decrypt(encrypted)
                injected.append(replace(target, api_key=decrypted))
                continue
            except Exception:
                pass  # Decryption failed; fall through to global key

        # No user key found -- keep the global key (already on target)
        injected.append(target)

    return injected


async def async_run_single(
    target: Target, prompt: str, max_tokens: int, temperature: float,
    context_tokens: int = 0, timeout: int = 120,
) -> RunResult:
    """Execute a single streaming benchmark run using async litellm."""
    result = RunResult(target=target, context_tokens=context_tokens)

    messages = []
    if context_tokens > 0:
        context_text = generate_context_text(context_tokens)
        messages.append({"role": "system", "content": context_text})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": target.model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timeout": timeout,
        "num_retries": 2,
    }
    if target.api_base:
        kwargs["api_base"] = target.api_base
    if target.api_key:
        kwargs["api_key"] = target.api_key
    # Remove params this model doesn't support
    if target.skip_params:
        for p in target.skip_params:
            kwargs.pop(p, None)

    try:
        start = time.perf_counter()
        stream = await litellm.acompletion(**kwargs)

        ttft = None
        chunk_count = 0
        usage_from_stream = None

        async for chunk in stream:
            now = time.perf_counter()

            # Time to first token
            if ttft is None:
                ttft = (now - start) * 1000  # ms

            # Count content-bearing chunks (1 chunk ~ 1 token)
            if (
                chunk.choices
                and chunk.choices[0].delta
                and chunk.choices[0].delta.content
            ):
                chunk_count += 1

            # Capture usage from final chunk if provider supports it
            if hasattr(chunk, "usage") and chunk.usage:
                usage_from_stream = chunk.usage

        total = time.perf_counter() - start

        # Prefer provider-reported counts; fall back to chunk counting
        if usage_from_stream:
            result.output_tokens = usage_from_stream.completion_tokens or chunk_count
            result.input_tokens = usage_from_stream.prompt_tokens or 0
        else:
            result.output_tokens = chunk_count
            result.input_tokens = 0

        result.ttft_ms = ttft or 0.0
        result.total_time_s = total
        result.tokens_per_second = (
            result.output_tokens / total if total > 0 else 0.0
        )

        # Input tokens/second: how fast the model processes the prompt
        if result.ttft_ms > 0 and result.input_tokens > 0:
            result.input_tokens_per_second = result.input_tokens / (result.ttft_ms / 1000)

        # Cost tracking (not all models support this)
        try:
            result.cost = litellm.completion_cost(
                model=target.model_id,
                prompt=str(result.input_tokens),
                completion=str(result.output_tokens),
                prompt_tokens=result.input_tokens,
                completion_tokens=result.output_tokens,
            )
        except Exception:
            result.cost = 0.0

        # Custom pricing fallback: when LiteLLM returns 0, use config pricing
        if result.cost == 0.0 and target.input_cost_per_mtok is not None and target.output_cost_per_mtok is not None:
            result.cost = (
                result.input_tokens * target.input_cost_per_mtok
                + result.output_tokens * target.output_cost_per_mtok
            ) / 1_000_000

    except litellm.exceptions.RateLimitError as e:
        result.success = False
        result.error = f"[rate_limited] {sanitize_error(str(e)[:180], target.api_key)}"
    except litellm.exceptions.AuthenticationError as e:
        result.success = False
        result.error = f"[auth_failed] {sanitize_error(str(e)[:180], target.api_key)}"
    except litellm.exceptions.Timeout as e:
        result.success = False
        result.error = f"[timeout] {sanitize_error(str(e)[:180], target.api_key)}"
    except Exception as e:
        result.success = False
        result.error = sanitize_error(str(e)[:200], target.api_key)

    return result


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

_VALID_PERIODS = {"7d", "30d", "90d", "all"}


@app.get("/api/analytics/leaderboard")
async def analytics_leaderboard(
    type: str = "benchmark",
    period: str = "all",
    user: dict = Depends(auth.get_current_user),
):
    """Aggregate benchmark or tool-eval results into a ranked leaderboard."""
    if period not in _VALID_PERIODS:
        return JSONResponse({"error": f"period must be one of {sorted(_VALID_PERIODS)}"}, status_code=400)
    if type not in ("benchmark", "tool_eval"):
        return JSONResponse({"error": "type must be 'benchmark' or 'tool_eval'"}, status_code=400)

    if type == "tool_eval":
        runs = await db.get_analytics_tool_eval_runs(user["id"], period)
        # Aggregate per model: avg tool_score, param_score, overall_score, count
        model_agg: dict[tuple[str, str], dict] = {}  # (model, provider) -> stats
        for run in runs:
            summaries = json.loads(run["summary_json"]) if isinstance(run["summary_json"], str) else run["summary_json"]
            for s in summaries:
                # summary_json uses model_name/model_id, not model
                model_name = s.get("model_name") or s.get("model", "")
                key = (model_name, s.get("provider", ""))
                if key not in model_agg:
                    model_agg[key] = {
                        "model": model_name,
                        "provider": s.get("provider", ""),
                        "tool_scores": [],
                        "param_scores": [],
                        "overall_scores": [],
                        "last_eval": run["timestamp"],
                    }
                entry = model_agg[key]
                # summary_json uses *_accuracy_pct / overall_pct field names
                tool_val = s.get("tool_accuracy_pct") if s.get("tool_accuracy_pct") is not None else s.get("tool_score")
                param_val = s.get("param_accuracy_pct") if s.get("param_accuracy_pct") is not None else s.get("param_score")
                overall_val = s.get("overall_pct") if s.get("overall_pct") is not None else s.get("overall_score")
                if tool_val is not None:
                    entry["tool_scores"].append(float(tool_val))
                if param_val is not None:
                    entry["param_scores"].append(float(param_val))
                if overall_val is not None:
                    entry["overall_scores"].append(float(overall_val))
                # Track latest eval timestamp
                if run["timestamp"] > entry["last_eval"]:
                    entry["last_eval"] = run["timestamp"]

        models = []
        for (model, provider), stats in model_agg.items():
            n_tool = len(stats["tool_scores"])
            n_param = len(stats["param_scores"])
            n_overall = len(stats["overall_scores"])
            models.append({
                "model": model,
                "provider": provider,
                "avg_tool_pct": round(sum(stats["tool_scores"]) / n_tool, 1) if n_tool else 0,
                "avg_param_pct": round(sum(stats["param_scores"]) / n_param, 1) if n_param else 0,
                "avg_overall_pct": round(sum(stats["overall_scores"]) / n_overall, 1) if n_overall else 0,
                "total_evals": max(n_tool, n_param, n_overall),
                "last_eval": stats["last_eval"],
            })
        # Sort by overall % descending
        models.sort(key=lambda m: m["avg_overall_pct"], reverse=True)
        return {"type": "tool_eval", "period": period, "models": models}

    # Default: benchmark leaderboard
    runs = await db.get_analytics_benchmark_runs(user["id"], period)
    # Aggregate per (display_name, provider): avg TPS, avg TTFT, avg cost, count
    model_agg: dict[tuple[str, str], dict] = {}
    for run in runs:
        results = json.loads(run["results_json"]) if isinstance(run["results_json"], str) else run["results_json"]
        for r in results:
            if not r.get("success", False):
                continue
            key = (r.get("model", ""), r.get("provider", ""))
            if key not in model_agg:
                model_agg[key] = {
                    "model": r.get("model", ""),
                    "provider": r.get("provider", ""),
                    "tps_vals": [],
                    "ttft_vals": [],
                    "cost_vals": [],
                    "last_run": run["timestamp"],
                }
            entry = model_agg[key]
            entry["tps_vals"].append(float(r.get("tokens_per_second", 0)))
            entry["ttft_vals"].append(float(r.get("ttft_ms", 0)))
            entry["cost_vals"].append(float(r.get("cost", 0)))
            if run["timestamp"] > entry["last_run"]:
                entry["last_run"] = run["timestamp"]

    models = []
    for (model, provider), stats in model_agg.items():
        n = len(stats["tps_vals"])
        models.append({
            "model": model,
            "provider": provider,
            "avg_tps": round(sum(stats["tps_vals"]) / n, 2) if n else 0,
            "avg_ttft_ms": round(sum(stats["ttft_vals"]) / n, 1) if n else 0,
            "avg_cost": round(sum(stats["cost_vals"]) / n, 6) if n else 0,
            "total_runs": n,
            "last_run": stats["last_run"],
        })
    # Sort by avg TPS descending
    models.sort(key=lambda m: m["avg_tps"], reverse=True)
    return {"type": "benchmark", "period": period, "models": models}


@app.get("/api/analytics/trends")
async def analytics_trends(
    models: str = "",
    metric: str = "tps",
    period: str = "all",
    user: dict = Depends(auth.get_current_user),
):
    """Return time-series data for selected models and metric."""
    if period not in _VALID_PERIODS:
        return JSONResponse({"error": f"period must be one of {sorted(_VALID_PERIODS)}"}, status_code=400)
    if metric not in ("tps", "ttft"):
        return JSONResponse({"error": "metric must be 'tps' or 'ttft'"}, status_code=400)

    model_names = [m.strip() for m in models.split(",") if m.strip()] if models else []
    if not model_names:
        return JSONResponse({"error": "models parameter is required (comma-separated)"}, status_code=400)

    runs = await db.get_analytics_benchmark_runs(user["id"], period)

    # Build per-model time series
    # Each run timestamp becomes a data point with the avg metric for that model in that run
    series_map: dict[str, list[dict]] = {name: [] for name in model_names}

    for run in runs:
        results = json.loads(run["results_json"]) if isinstance(run["results_json"], str) else run["results_json"]
        # Group results in this run by model name
        run_model_vals: dict[str, list[float]] = {}
        for r in results:
            if not r.get("success", False):
                continue
            display = r.get("model", "")
            if display in series_map:
                run_model_vals.setdefault(display, []).append(
                    float(r.get("tokens_per_second", 0)) if metric == "tps" else float(r.get("ttft_ms", 0))
                )

        # Average across runs within this benchmark for each model
        for model_name, vals in run_model_vals.items():
            if vals:
                series_map[model_name].append({
                    "timestamp": run["timestamp"],
                    "value": round(sum(vals) / len(vals), 2),
                })

    # Build response; only include models that have data, sort points chronologically
    series = []
    for name in model_names:
        points = series_map.get(name, [])
        if points:
            points.sort(key=lambda p: p["timestamp"])
            series.append({"model": name, "points": points})

    return {"metric": metric, "series": series}


@app.get("/api/analytics/compare")
async def analytics_compare(
    runs: str = "",
    user: dict = Depends(auth.get_current_user),
):
    """Compare 2-4 specific benchmark runs side-by-side."""
    run_ids = [r.strip() for r in runs.split(",") if r.strip()] if runs else []
    if len(run_ids) < 2:
        return JSONResponse({"error": "At least 2 run IDs required"}, status_code=400)
    if len(run_ids) > 4:
        return JSONResponse({"error": "Maximum 4 runs can be compared"}, status_code=400)

    comparison = []
    for run_id in run_ids:
        run = await db.get_benchmark_run(run_id, user["id"])
        if not run:
            return JSONResponse({"error": f"Run '{run_id}' not found"}, status_code=404)

        results = json.loads(run["results_json"]) if isinstance(run.get("results_json", ""), str) else run.get("results_json", [])

        # Aggregate per model within this run
        model_map: dict[tuple[str, str], dict] = {}
        for r in results:
            if not r.get("success", False):
                continue
            key = (r.get("model", ""), r.get("provider", ""))
            if key not in model_map:
                model_map[key] = {
                    "model": r.get("model", ""),
                    "provider": r.get("provider", ""),
                    "tps_vals": [],
                    "ttft_vals": [],
                    "cost_vals": [],
                    "context_tokens": r.get("context_tokens", 0),
                }
            model_map[key]["tps_vals"].append(float(r.get("tokens_per_second", 0)))
            model_map[key]["ttft_vals"].append(float(r.get("ttft_ms", 0)))
            model_map[key]["cost_vals"].append(float(r.get("cost", 0)))

        run_models = []
        for (model, provider), stats in model_map.items():
            n = len(stats["tps_vals"])
            run_models.append({
                "model": model,
                "provider": provider,
                "avg_tps": round(sum(stats["tps_vals"]) / n, 2) if n else 0,
                "avg_ttft_ms": round(sum(stats["ttft_vals"]) / n, 1) if n else 0,
                "context_tokens": stats["context_tokens"],
                "avg_cost": round(sum(stats["cost_vals"]) / n, 8) if n else 0,
            })

        comparison.append({
            "id": run["id"],
            "timestamp": run["timestamp"],
            "prompt": run.get("prompt", ""),
            "models": run_models,
        })

    return {"runs": comparison}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"data: {json.dumps(data)}\n\n"


def _aggregate(raw_results: list[dict], config: dict) -> list[AggregatedResult]:
    """Convert raw result dicts into AggregatedResults for saving."""
    # Group by (model_id, provider, context_tokens) to distinguish
    # same model_id served by different providers (e.g. two LM Studio instances)
    grouped = {}
    for r in raw_results:
        key = (r["model_id"], r["provider"], r.get("context_tokens", 0))
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(r)

    agg_list = []
    all_targets = build_targets(config)
    target_map = {(t.model_id, t.provider): t for t in all_targets}

    for (mid, provider, ctx_tokens), runs in grouped.items():
        target = target_map.get((mid, provider), Target(
            provider=provider,
            model_id=mid,
            display_name=runs[0]["model"],
        ))
        successes = [r for r in runs if r["success"]]
        n = len(successes)

        agg = AggregatedResult(
            target=target,
            runs=len(runs),
            failures=len(runs) - n,
        )
        if n > 0:
            agg.avg_ttft_ms = sum(r["ttft_ms"] for r in successes) / n
            agg.avg_total_time_s = sum(r["total_time_s"] for r in successes) / n
            agg.avg_tokens_per_second = sum(r["tokens_per_second"] for r in successes) / n
            agg.avg_output_tokens = sum(r["output_tokens"] for r in successes) / n
            agg.avg_cost = sum(r.get("cost", 0) for r in successes) / n
            agg.total_cost = sum(r.get("cost", 0) for r in successes)
            input_tps_vals = [r.get("input_tokens_per_second", 0) for r in successes if r.get("input_tokens_per_second", 0) > 0]
            if input_tps_vals:
                agg.avg_input_tps = sum(input_tps_vals) / len(input_tps_vals)

        # Store context_tokens on the result for saving
        agg.all_results = [RunResult(
            target=target,
            context_tokens=ctx_tokens,
            ttft_ms=r["ttft_ms"],
            total_time_s=r["total_time_s"],
            output_tokens=r["output_tokens"],
            input_tokens=r.get("input_tokens", 0),
            tokens_per_second=r["tokens_per_second"],
            input_tokens_per_second=r.get("input_tokens_per_second", 0),
            cost=r.get("cost", 0),
            success=r["success"],
            error=r.get("error", ""),
        ) for r in runs]

        # Compute variance stats (identical to benchmark.py)
        if n > 0:
            success_results = [rr for rr in agg.all_results if rr.success]
            _compute_variance(agg, success_results)

        agg_list.append(agg)

    return agg_list


# ---------------------------------------------------------------------------
# Scheduled Benchmarks
# ---------------------------------------------------------------------------


@app.get("/api/schedules")
async def list_schedules(user: dict = Depends(auth.get_current_user)):
    """List the current user's scheduled benchmarks."""
    schedules = await db.get_user_schedules(user["id"])
    for s in schedules:
        if isinstance(s.get("models_json"), str):
            s["models"] = json.loads(s["models_json"])
            del s["models_json"]
    return {"schedules": schedules}


@app.post("/api/schedules")
async def create_schedule(request: Request, user: dict = Depends(auth.get_current_user)):
    """Create a new scheduled benchmark."""
    body = await request.json()
    name = body.get("name", "").strip()
    prompt = body.get("prompt", "").strip()
    models = body.get("models", [])
    interval_hours = body.get("interval_hours")
    max_tokens = body.get("max_tokens", 512)
    temperature = body.get("temperature", 0.7)

    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    if not isinstance(models, list) or len(models) == 0:
        return JSONResponse({"error": "models must be a non-empty list"}, status_code=400)
    if not isinstance(interval_hours, (int, float)) or int(interval_hours) < 1:
        return JSONResponse({"error": "interval_hours must be >= 1"}, status_code=400)

    interval_hours = int(interval_hours)
    max_tokens = int(max_tokens) if max_tokens else 512
    temperature = float(temperature) if temperature is not None else 0.7

    # next_run = now + interval
    next_run = (datetime.now(timezone.utc) + timedelta(hours=interval_hours)).strftime("%Y-%m-%d %H:%M:%S")

    schedule_id = await db.create_schedule(
        user_id=user["id"],
        name=name,
        prompt=prompt,
        models_json=json.dumps(models),
        max_tokens=max_tokens,
        temperature=temperature,
        interval_hours=interval_hours,
        next_run=next_run,
    )

    return {"status": "ok", "id": schedule_id}


@app.put("/api/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Update an existing scheduled benchmark."""
    body = await request.json()

    kwargs = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "prompt" in body:
        kwargs["prompt"] = body["prompt"]
    if "models" in body:
        kwargs["models_json"] = json.dumps(body["models"])
    if "max_tokens" in body:
        kwargs["max_tokens"] = int(body["max_tokens"])
    if "temperature" in body:
        kwargs["temperature"] = float(body["temperature"])
    if "interval_hours" in body:
        kwargs["interval_hours"] = int(body["interval_hours"])
        # Recalculate next_run when interval changes
        kwargs["next_run"] = (datetime.now(timezone.utc) + timedelta(hours=int(body["interval_hours"]))).strftime("%Y-%m-%d %H:%M:%S")
    if "enabled" in body:
        kwargs["enabled"] = 1 if body["enabled"] else 0

    updated = await db.update_schedule(schedule_id, user["id"], **kwargs)
    if not updated:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)

    return {"status": "ok"}


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a scheduled benchmark."""
    deleted = await db.delete_schedule(schedule_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    return {"status": "ok"}


@app.post("/api/schedules/{schedule_id}/trigger")
async def trigger_schedule(schedule_id: str, user: dict = Depends(auth.get_current_user)):
    """Manually trigger a scheduled benchmark (run now)."""
    schedule = await db.get_schedule(schedule_id, user["id"])
    if not schedule:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)

    # Run in background so the HTTP response returns immediately
    async def _run():
        try:
            await _run_scheduled_benchmark(schedule)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            interval = schedule["interval_hours"]
            next_run = (datetime.now(timezone.utc) + timedelta(hours=interval)).strftime("%Y-%m-%d %H:%M:%S")
            await db.update_schedule_after_run(schedule["id"], now, next_run)
        except Exception as exc:
            print(f"  [trigger] Error running schedule {schedule['id']}: {exc}")

    asyncio.create_task(_run())
    return {"status": "ok", "message": "Benchmark triggered"}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@app.get("/api/export/history")
async def export_history_csv(user: dict = Depends(auth.get_current_user)):
    """Export all benchmark runs as CSV."""
    runs = await db.get_user_benchmark_runs(user["id"], limit=10000, offset=0)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp", "prompt", "model", "provider",
        "tokens_per_second", "ttft_ms", "cost",
        "context_tokens", "output_tokens",
    ])

    for run in runs:
        results = json.loads(run["results_json"]) if isinstance(run.get("results_json"), str) else run.get("results_json", [])
        for r in results:
            writer.writerow([
                run.get("timestamp", ""),
                run.get("prompt", ""),
                r.get("model", ""),
                r.get("provider", ""),
                r.get("tokens_per_second", ""),
                r.get("ttft_ms", ""),
                r.get("cost", ""),
                r.get("context_tokens", ""),
                r.get("output_tokens", ""),
            ])

    return StreamingResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=benchmark_history.csv"},
    )


@app.get("/api/export/leaderboard")
async def export_leaderboard_csv(
    type: str = "benchmark",
    period: str = "all",
    user: dict = Depends(auth.get_current_user),
):
    """Export leaderboard as CSV (reuses analytics aggregation logic)."""
    if period not in _VALID_PERIODS:
        return JSONResponse({"error": f"period must be one of {sorted(_VALID_PERIODS)}"}, status_code=400)
    if type not in ("benchmark", "tool_eval"):
        return JSONResponse({"error": "type must be 'benchmark' or 'tool_eval'"}, status_code=400)

    output = io.StringIO()
    writer = csv.writer(output)

    if type == "tool_eval":
        runs = await db.get_analytics_tool_eval_runs(user["id"], period)
        model_agg: dict[tuple[str, str], dict] = {}
        for run in runs:
            summaries = json.loads(run["summary_json"]) if isinstance(run["summary_json"], str) else run["summary_json"]
            for s in summaries:
                model_name = s.get("model_name") or s.get("model", "")
                key = (model_name, s.get("provider", ""))
                if key not in model_agg:
                    model_agg[key] = {
                        "model": model_name,
                        "provider": s.get("provider", ""),
                        "tool_scores": [],
                        "param_scores": [],
                        "overall_scores": [],
                        "last_eval": run["timestamp"],
                    }
                entry = model_agg[key]
                tool_val = s.get("tool_accuracy_pct") if s.get("tool_accuracy_pct") is not None else s.get("tool_score")
                param_val = s.get("param_accuracy_pct") if s.get("param_accuracy_pct") is not None else s.get("param_score")
                overall_val = s.get("overall_pct") if s.get("overall_pct") is not None else s.get("overall_score")
                if tool_val is not None:
                    entry["tool_scores"].append(float(tool_val))
                if param_val is not None:
                    entry["param_scores"].append(float(param_val))
                if overall_val is not None:
                    entry["overall_scores"].append(float(overall_val))
                if run["timestamp"] > entry["last_eval"]:
                    entry["last_eval"] = run["timestamp"]

        models = []
        for (model, provider), stats in model_agg.items():
            n_tool = len(stats["tool_scores"])
            n_param = len(stats["param_scores"])
            n_overall = len(stats["overall_scores"])
            models.append({
                "model": model,
                "provider": provider,
                "avg_tool_pct": round(sum(stats["tool_scores"]) / n_tool, 1) if n_tool else 0,
                "avg_param_pct": round(sum(stats["param_scores"]) / n_param, 1) if n_param else 0,
                "avg_overall_pct": round(sum(stats["overall_scores"]) / n_overall, 1) if n_overall else 0,
                "total_evals": max(n_tool, n_param, n_overall),
                "last_eval": stats["last_eval"],
            })
        models.sort(key=lambda m: m["avg_overall_pct"], reverse=True)

        writer.writerow([
            "rank", "model", "provider", "avg_tool_pct", "avg_param_pct",
            "avg_overall_pct", "total_evals", "last_eval",
        ])
        for rank, m in enumerate(models, 1):
            writer.writerow([
                rank, m["model"], m["provider"], m["avg_tool_pct"],
                m["avg_param_pct"], m["avg_overall_pct"],
                m["total_evals"], m["last_eval"],
            ])

        filename = f"leaderboard_tool_eval_{period}.csv"
    else:
        runs = await db.get_analytics_benchmark_runs(user["id"], period)
        model_agg_bm: dict[tuple[str, str], dict] = {}
        for run in runs:
            results = json.loads(run["results_json"]) if isinstance(run["results_json"], str) else run["results_json"]
            for r in results:
                if not r.get("success", False):
                    continue
                key = (r.get("model", ""), r.get("provider", ""))
                if key not in model_agg_bm:
                    model_agg_bm[key] = {
                        "model": r.get("model", ""),
                        "provider": r.get("provider", ""),
                        "tps_vals": [],
                        "ttft_vals": [],
                        "cost_vals": [],
                        "last_run": run["timestamp"],
                    }
                entry = model_agg_bm[key]
                entry["tps_vals"].append(float(r.get("tokens_per_second", 0)))
                entry["ttft_vals"].append(float(r.get("ttft_ms", 0)))
                entry["cost_vals"].append(float(r.get("cost", 0)))
                if run["timestamp"] > entry["last_run"]:
                    entry["last_run"] = run["timestamp"]

        models = []
        for (model, provider), stats in model_agg_bm.items():
            n = len(stats["tps_vals"])
            models.append({
                "model": model,
                "provider": provider,
                "avg_tps": round(sum(stats["tps_vals"]) / n, 2) if n else 0,
                "avg_ttft_ms": round(sum(stats["ttft_vals"]) / n, 1) if n else 0,
                "avg_cost": round(sum(stats["cost_vals"]) / n, 6) if n else 0,
                "total_runs": n,
                "last_run": stats["last_run"],
            })
        models.sort(key=lambda m: m["avg_tps"], reverse=True)

        writer.writerow([
            "rank", "model", "provider", "avg_tps", "avg_ttft_ms",
            "avg_cost", "total_runs", "last_run",
        ])
        for rank, m in enumerate(models, 1):
            writer.writerow([
                rank, m["model"], m["provider"], m["avg_tps"],
                m["avg_ttft_ms"], m["avg_cost"],
                m["total_runs"], m["last_run"],
            ])

        filename = f"leaderboard_benchmark_{period}.csv"

    return StreamingResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/export/tool-eval")
async def export_tool_eval_csv(user: dict = Depends(auth.get_current_user)):
    """Export all tool eval runs as CSV."""
    runs = await db.get_tool_eval_runs(user["id"], limit=10000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp", "suite_name", "model", "provider",
        "tool_accuracy_pct", "param_accuracy_pct", "overall_pct",
        "cases_run", "cases_passed",
    ])

    for run in runs:
        summaries = json.loads(run["summary_json"]) if isinstance(run.get("summary_json"), str) else run.get("summary_json", [])
        for s in summaries:
            model_name = s.get("model_name") or s.get("model", "")
            writer.writerow([
                run.get("timestamp", ""),
                run.get("suite_name", ""),
                model_name,
                s.get("provider", ""),
                s.get("tool_accuracy_pct", ""),
                s.get("param_accuracy_pct", ""),
                s.get("overall_pct", ""),
                s.get("cases_run", ""),
                s.get("cases_passed", ""),
            ])

    return StreamingResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tool_eval_history.csv"},
    )


@app.get("/api/export/eval/{eval_id}")
async def export_eval_json(eval_id: str, user: dict = Depends(auth.get_current_user)):
    """Export a tool eval run as JSON with full raw request/response data."""
    run = await db.get_tool_eval_run(eval_id, user["id"])
    if not run:
        return JSONResponse({"error": "Eval run not found"}, status_code=404)

    export_data = {
        "eval_id": run["id"],
        "suite_name": run.get("suite_name", ""),
        "models": json.loads(run.get("models_json", "[]")) if isinstance(run.get("models_json"), str) else run.get("models_json", []),
        "temperature": run.get("temperature"),
        "timestamp": run.get("created_at", "") or run.get("timestamp", ""),
        "results": json.loads(run.get("results_json", "[]")) if isinstance(run.get("results_json"), str) else run.get("results_json", []),
        "summary": json.loads(run.get("summary_json", "[]")) if isinstance(run.get("summary_json"), str) else run.get("summary_json", []),
    }

    headers = {
        "Content-Disposition": f'attachment; filename=eval-{eval_id[:8]}.json',
    }
    return JSONResponse(content=export_data, headers=headers)


@app.get("/api/export/run/{run_id}")
async def export_run_csv(run_id: str, user: dict = Depends(auth.get_current_user)):
    """Export a single benchmark run as CSV."""
    run = await db.get_benchmark_run(run_id, user["id"])
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)

    results = json.loads(run["results_json"]) if isinstance(run.get("results_json"), str) else run.get("results_json", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "model", "provider", "tokens_per_second", "ttft_ms",
        "cost", "context_tokens", "output_tokens", "input_tokens",
    ])

    for r in results:
        writer.writerow([
            r.get("model", ""),
            r.get("provider", ""),
            r.get("tokens_per_second", ""),
            r.get("ttft_ms", ""),
            r.get("cost", ""),
            r.get("context_tokens", ""),
            r.get("output_tokens", ""),
            r.get("input_tokens", ""),
        ])

    return StreamingResponse(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=benchmark_run_{run_id}.csv"},
    )


@app.get("/api/export/settings")
async def export_settings(user: dict = Depends(auth.get_current_user)):
    """Export the user's complete configuration as a JSON file download."""
    config = await _get_user_config(user["id"])

    # Extract defaults
    defaults = config.get("defaults", {})

    # Extract providers
    providers = config.get("providers", {})

    # Build API key metadata list (provider_key + key_name only, no secrets)
    user_keys = await db.get_user_keys(user["id"])
    api_keys = [
        {"provider_key": uk["provider_key"], "key_name": uk.get("key_name", "")}
        for uk in user_keys
    ]

    export_data = {
        "export_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "defaults": defaults,
        "providers": providers,
        "api_keys": api_keys,
    }

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"benchmark-settings-{date_str}.json"

    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/api/import/settings")
async def import_settings(request: Request, user: dict = Depends(auth.get_current_user)):
    """Import settings from a previously exported JSON file."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    # Validate required fields
    if not isinstance(body, dict):
        return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
    if "export_version" not in body:
        return JSONResponse({"error": "Missing 'export_version' field"}, status_code=400)
    if "providers" not in body or not isinstance(body["providers"], dict):
        return JSONResponse({"error": "Missing or invalid 'providers' field"}, status_code=400)

    config = await _get_user_config(user["id"])
    existing_providers = config.get("providers", {})

    imported_providers = body["providers"]
    providers_added = 0
    providers_updated = 0

    for prov_key, prov_cfg in imported_providers.items():
        if prov_key in existing_providers:
            existing_providers[prov_key] = prov_cfg
            providers_updated += 1
        else:
            existing_providers[prov_key] = prov_cfg
            providers_added += 1

    config["providers"] = existing_providers

    # Overwrite defaults if present in import
    if "defaults" in body and isinstance(body["defaults"], dict):
        config["defaults"] = body["defaults"]

    await _save_user_config(user["id"], config)

    return {
        "status": "ok",
        "providers_imported": providers_added + providers_updated,
        "providers_updated": providers_updated,
        "providers_added": providers_added,
    }


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


@app.get("/api/onboarding/status")
async def onboarding_status(user: dict = Depends(auth.get_current_user)):
    """Check if user has completed onboarding."""
    full_user = await db.get_user_by_id(user["id"])
    completed = bool(full_user.get("onboarding_completed", 0)) if full_user else False
    return {"completed": completed}


@app.post("/api/onboarding/complete")
async def onboarding_complete(user: dict = Depends(auth.get_current_user)):
    """Mark onboarding as completed for the current user."""
    await db.set_onboarding_completed(user["id"])
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    # Warn if using auto-generated keys in production
    if not os.environ.get("FERNET_KEY"):
        print("  [!] FERNET_KEY not set. Using auto-generated key from data/.fernet_key")
        print("  [!] Set FERNET_KEY env var in production and BACK UP the key.\n")

    parser = argparse.ArgumentParser(description="LLM Benchmark Studio")
    parser.add_argument("--port", type=int, default=8501, help="Port (default: 8501)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    print(f"\n  LLM Benchmark Studio running at http://localhost:{args.port}\n")
    log_level = os.environ.get("LOG_LEVEL", "warning").lower()
    uvicorn.run(app, host=args.host, port=args.port, log_level=log_level)

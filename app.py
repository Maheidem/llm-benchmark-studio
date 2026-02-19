#!/usr/bin/env python3
"""LLM Benchmark Studio - Web dashboard for benchmarking LLM providers.

Usage:
    python app.py                  # Start on port 8501
    python app.py --port 3333      # Custom port
"""

import argparse
import asyncio
import collections
import csv
import io
import json
import logging
import logging.handlers
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import litellm
import yaml

# Disable retry loops at two layers:
# 1. LiteLLM wrapper (default num_retries=2) — we handle retries ourselves
#    in _generate_prompts_meta and _call_judge_model with exponential backoff.
# 2. OpenAI SDK internal (default max_retries=2) — without this, OpenAI-compatible
#    endpoints (LM Studio) trigger invisible retry loops inside the SDK.
litellm.num_retries = 0
os.environ.setdefault("OPENAI_MAX_RETRIES", "0")

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, WebSocket, WebSocketDisconnect
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
from provider_params import (
    PROVIDER_REGISTRY,
    identify_provider,
    validate_params,
    build_litellm_kwargs,
)
from mcp import ClientSession
from mcp.client.sse import sse_client
from ws_manager import ConnectionManager
from job_registry import registry as job_registry

from contextlib import asynccontextmanager


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

class _JSONFormatter(logging.Formatter):
    """JSON log formatter for Docker stdout (machine-parseable)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Merge any extra fields passed via extra={...}
        for key in ("user_id", "job_id", "method", "path", "status", "duration_ms",
                     "provider", "model", "action", "ip", "detail"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry, default=str)


def configure_logging() -> None:
    """Set up application-wide logging.

    Reads LOG_LEVEL from env (default: 'warning').
    Uses JSON format for Docker stdout compatibility.
    """
    level_name = os.environ.get("LOG_LEVEL", "warning").upper()
    level = getattr(logging, level_name, logging.WARNING)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers to avoid duplicates on reload
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(_JSONFormatter())
    root.addHandler(handler)

    # Align uvicorn loggers with our level
    for uv_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(uv_logger_name)
        uv_logger.setLevel(level)

    # In-memory ring buffer for /api/admin/logs endpoint
    global _log_buffer
    buf_handler = logging.Handler()
    buf_handler.setFormatter(_JSONFormatter())
    buf_handler.emit = lambda record: _log_buffer.append(buf_handler.format(record))
    root.addHandler(buf_handler)

    # Quiet noisy third-party loggers unless explicitly debugging
    if level > logging.DEBUG:
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("litellm").setLevel(logging.WARNING)


_log_buffer: collections.deque = collections.deque(maxlen=2000)

# Configure logging BEFORE anything else runs (import-time side effects)
configure_logging()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app_instance):
    """Initialize database on startup."""
    logger.info("LLM Benchmark Studio starting (version=%s)", APP_VERSION)
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
            logger.info("Promoted existing user to admin: %s", admin_email)
        elif not existing:
            admin_pass = os.environ.get("ADMIN_PASSWORD")
            if admin_pass:
                hashed = auth.hash_password(admin_pass)
                await db.create_user(admin_email, hashed, role="admin")
                logger.info("Admin account created: %s", admin_email)
    # Clean up orphaned judge reports stuck in "running" from prior crashes
    stale_count = await db.cleanup_stale_judge_reports(minutes=30)
    if stale_count:
        logger.info("Cleaned up %d stale judge report(s)", stale_count)
    # Clean up orphaned param/prompt tune runs stuck in "running" from prior crashes
    stale_param = await db.cleanup_stale_param_tune_runs(minutes=0)
    if stale_param:
        logger.info("Cleaned up %d stale param tune run(s)", stale_param)
    stale_prompt = await db.cleanup_stale_prompt_tune_runs(minutes=0)
    if stale_prompt:
        logger.info("Cleaned up %d stale prompt tune run(s)", stale_prompt)
    # Initialize job registry (startup recovery + watchdog)
    job_registry.set_ws_manager(ws_manager)
    await job_registry.startup()
    # Launch background scheduler for scheduled benchmarks
    scheduler_task = asyncio.create_task(_run_scheduler())
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        logger.debug("Scheduler task cancelled during shutdown")
    await job_registry.shutdown()

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
                logger.info("Scheduled benchmark triggered: schedule_id=%s name=%s", schedule["id"], schedule.get("name", ""))
                try:
                    await _run_scheduled_benchmark(schedule)
                except Exception as exc:
                    logger.exception("Scheduler error running schedule %s", schedule["id"])
                # Update timestamps regardless of success
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                interval = schedule["interval_hours"]
                next_run = (datetime.now(timezone.utc) + timedelta(hours=interval)).strftime("%Y-%m-%d %H:%M:%S")
                await db.update_schedule_after_run(schedule["id"], now, next_run)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Scheduler unexpected error")


app = FastAPI(title="LLM Benchmark Studio", lifespan=lifespan)

# WebSocket connection manager (singleton)
ws_manager = ConnectionManager()

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


# ---------------------------------------------------------------------------
# Request Logging Middleware
# ---------------------------------------------------------------------------

# Paths to skip logging (noisy/health endpoints)
_SKIP_LOG_PATHS = frozenset({"/healthz", "/favicon.ico"})


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log HTTP requests with method, path, status, duration, and user_id."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip noisy endpoints
        if path in _SKIP_LOG_PATHS or path.startswith("/static"):
            return await call_next(request)

        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        # Try to extract user_id from JWT (best-effort, no DB call)
        user_id = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                from jose import jwt as _jwt
                payload = _jwt.decode(
                    auth_header[7:],
                    auth.JWT_SECRET,
                    algorithms=[auth.JWT_ALGORITHM],
                    options={"verify_exp": False},
                )
                user_id = payload.get("sub")
            except Exception:
                pass

        method = request.method
        logger.info(
            "REQ %s %s %s",
            request_id, method, path,
            extra={"method": method, "path": path, "user_id": user_id},
        )

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000)

        status = response.status_code
        log_level = logging.INFO
        if 400 <= status < 500:
            log_level = logging.WARNING
        elif status >= 500:
            log_level = logging.ERROR

        logger.log(
            log_level,
            "RES %s %d %dms",
            request_id, status, duration_ms,
            extra={"method": method, "path": path, "status": status, "duration_ms": duration_ms, "user_id": user_id},
        )

        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RequestLoggingMiddleware)

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


def _parse_target_selection(body: dict) -> tuple[list[str], set[tuple[str, str]] | None]:
    """Parse model/target selection from request body.

    Supports two formats:
      1. New: ``targets: [{"provider_key": "...", "model_id": "..."}, ...]``
         → Returns (model_ids, target_set) where target_set is a set of
           (provider_key, model_id) tuples for precise matching.
      2. Legacy: ``models: ["model_id_1", ...]``
         → Returns (model_ids, None) for backward-compatible model_id-only matching.

    Returns:
        (model_ids, target_set):
            model_ids: flat list of model_id strings (for logging / combo count).
            target_set: set of (provider_key, model_id) or None for legacy mode.
    """
    targets_list = body.get("targets")
    if targets_list and isinstance(targets_list, list):
        target_set: set[tuple[str, str]] = set()
        model_ids: list[str] = []
        for entry in targets_list:
            if isinstance(entry, dict) and "provider_key" in entry and "model_id" in entry:
                target_set.add((entry["provider_key"], entry["model_id"]))
                model_ids.append(entry["model_id"])
        if target_set:
            return model_ids, target_set
    # Fallback to legacy flat list
    model_ids = body.get("models", [])
    return model_ids, None


def _filter_targets(all_targets: list[Target], model_ids: list[str],
                    target_set: set[tuple[str, str]] | None) -> list[Target]:
    """Filter all_targets using precise (provider_key, model_id) or legacy model_id matching.

    When target_set is provided (new format), matches on (provider_key, model_id).
    Otherwise falls back to model_id-only matching (legacy).
    """
    if target_set:
        return [t for t in all_targets if (t.provider_key, t.model_id) in target_set]
    if model_ids:
        return [t for t in all_targets if t.model_id in model_ids]
    return all_targets


def _target_key(target: Target) -> str:
    """Return a unique key for a target: 'provider_key::model_id'.

    Used wherever we need to index by target (e.g. validated_target_combos)
    to avoid collisions when two providers share the same model_id.
    """
    return f"{target.provider_key or ''}::{target.model_id}"


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
    return HTMLResponse(
        content=(_dir / "index.html").read_text(),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


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
# WebSocket endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time job status updates.

    Auth: JWT access token passed as query param ?token=xxx
    On connect: sends a 'sync' message with active + recent jobs.
    Listens for client messages: 'ping' (keep-alive), 'cancel' (cancel a job).
    """
    from jose import JWTError, ExpiredSignatureError

    # --- Auth: validate JWT from query param ---
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4001, reason="Missing token")
        return

    try:
        payload = auth.decode_token(token)
        if payload.get("type") not in ("access", "cli"):
            raise ValueError("Invalid token type")
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("No sub in token")
    except (JWTError, ExpiredSignatureError, ValueError):
        await ws.close(code=4001, reason="Invalid token")
        return

    # Look up user to get role (for admin broadcast)
    user = await db.get_user_by_id(user_id)
    if not user:
        await ws.close(code=4001, reason="User not found")
        return

    role = user.get("role", "user")

    # --- Register connection (max 5 per user) ---
    connected = await ws_manager.connect(user_id, role, ws)
    if not connected:
        return  # Too many connections, already closed by manager

    # --- Send initial sync: active + recent jobs ---
    try:
        active_jobs = await db.get_user_active_jobs(user_id)
        recent_jobs = await db.get_user_recent_jobs(user_id, limit=10)
        await ws.send_json({
            "type": "sync",
            "active_jobs": active_jobs,
            "recent_jobs": recent_jobs,
        })
    except Exception:
        logger.exception("WebSocket initial sync failed (user_id=%s)", user_id)
        await ws_manager.disconnect(user_id, ws)
        return

    # --- Listen loop with 90s receive timeout ---
    # The timeout catches dead connections from unclean proxy disconnects
    # (e.g. Cloudflare closing without sending a close frame).
    # Clients should send a ping at least every 60s to stay alive.
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_json(), timeout=90)
            except asyncio.TimeoutError:
                # No message received in 90s — assume dead connection
                try:
                    await ws.close(code=4002, reason="Receive timeout")
                except Exception:
                    logger.debug("WebSocket close failed during timeout disconnect")
                break

            msg_type = data.get("type")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "cancel":
                job_id = data.get("job_id")
                if job_id:
                    await job_registry.cancel(job_id, user_id)

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected (user_id=%s)", user_id)
    except Exception:
        logger.exception("WebSocket unexpected error (user_id=%s)", user_id)
    finally:
        await ws_manager.disconnect(user_id, ws)


# ---------------------------------------------------------------------------
# Job tracking REST endpoints
# ---------------------------------------------------------------------------


@app.get("/api/jobs")
async def list_jobs(request: Request, user: dict = Depends(auth.get_current_user)):
    """List current user's jobs. Optional query params: ?status=running,queued&limit=20"""
    status_filter = request.query_params.get("status")
    limit = int(request.query_params.get("limit", "20"))
    jobs = await db.get_user_jobs(user["id"], status=status_filter, limit=limit)
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(auth.get_current_user)):
    """Get a single job's details (scoped to the current user)."""
    job = await db.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


async def _cleanup_orphaned_tune_run(job: dict) -> bool:
    """If a terminal job has a linked tune run still showing 'running', mark it interrupted.

    Returns True if a cleanup was performed, False if nothing to clean up.
    """
    result_ref = job.get("result_ref")
    if not result_ref:
        return False
    job_type = job.get("job_type", "")
    user_id = job["user_id"]
    try:
        if "param" in job_type:
            run = await db.get_param_tune_run(result_ref, user_id)
            if run and run.get("status") == "running":
                await db.update_param_tune_run(result_ref, user_id, status="interrupted")
                logger.info("Cleaned up orphaned param_tune_run %s (job %s already %s)", result_ref, job["id"], job["status"])
                return True
        elif "prompt" in job_type:
            run = await db.get_prompt_tune_run(result_ref, user_id)
            if run and run.get("status") == "running":
                await db.update_prompt_tune_run(result_ref, user_id, status="interrupted")
                logger.info("Cleaned up orphaned prompt_tune_run %s (job %s already %s)", result_ref, job["id"], job["status"])
                return True
    except Exception:
        logger.exception("Failed to clean up orphaned tune run %s", result_ref)
    return False


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a specific job (user can only cancel their own jobs)."""
    job = await db.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    if job["status"] in ("done", "failed", "cancelled", "interrupted"):
        # Job already terminal -- but linked tune run might still show "running" (ghost).
        # Clean it up so the frontend sees a consistent state.
        cleaned = await _cleanup_orphaned_tune_run(job)
        if cleaned:
            await ws_manager.send_to_user(user["id"], {
                "type": "job_cancelled", "job_id": job_id,
            })
            return {"status": "ok", "message": "Job already finished, cleaned up linked run", "was_orphan": True}
        return JSONResponse({"error": "Job already finished"}, status_code=400)

    # For queued jobs, just mark cancelled directly
    if job["status"] in ("pending", "queued"):
        await db.update_job_status(
            job_id, "cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        await ws_manager.send_to_user(user["id"], {
            "type": "job_cancelled", "job_id": job_id,
        })
        return {"status": "ok", "message": "Job cancelled"}

    # For running jobs, use the job registry to signal cancellation
    cancelled = await job_registry.cancel(job_id, user["id"])
    if not cancelled:
        # Fallback: mark as cancelled directly if registry doesn't know about it
        await db.update_job_status(
            job_id, "cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        await ws_manager.send_to_user(user["id"], {
            "type": "job_cancelled", "job_id": job_id,
        })

    await db.log_audit(
        user_id=user["id"],
        username=user.get("email", ""),
        action="job_cancel",
        resource_type="job",
        resource_id=job_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )

    return {"status": "ok", "message": "Cancellation requested"}


@app.get("/api/admin/jobs")
async def admin_list_jobs(current_user: dict = Depends(auth.require_admin)):
    """Admin: list all active jobs across all users."""
    jobs = await db.get_all_active_jobs()
    return {"jobs": jobs}


@app.post("/api/admin/jobs/{job_id}/cancel")
async def admin_cancel_job(job_id: str, request: Request, current_user: dict = Depends(auth.require_admin)):
    """Admin: cancel any user's job."""
    job = await db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    if job["status"] in ("done", "failed", "cancelled", "interrupted"):
        # Job already terminal -- clean up any orphaned linked tune run
        cleaned = await _cleanup_orphaned_tune_run(job)
        if cleaned:
            await ws_manager.send_to_user(job["user_id"], {
                "type": "job_cancelled", "job_id": job_id,
            })
            return {"status": "ok", "message": "Job already finished, cleaned up linked run", "was_orphan": True}
        return JSONResponse({"error": "Job already finished"}, status_code=400)

    # Use job registry for running jobs (signals cancel_event)
    cancelled = await job_registry.cancel(job_id, current_user["id"], is_admin=True)
    if not cancelled:
        # Fallback for jobs not tracked by registry
        await db.update_job_status(
            job_id, "cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        await ws_manager.send_to_user(job["user_id"], {
            "type": "job_cancelled", "job_id": job_id,
        })

    await db.log_audit(
        user_id=current_user["id"],
        username=current_user.get("email", ""),
        action="admin_job_cancel",
        resource_type="job",
        resource_id=job_id,
        detail={"target_user": job["user_id"]},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )

    return {"status": "ok", "message": "Job cancelled by admin"}


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


@app.get("/api/admin/logs")
async def admin_get_logs(
    request: Request,
    lines: int = 100,
    level: str | None = None,
    search: str | None = None,
    token: str | None = None,
):
    """Return recent application log entries from in-memory ring buffer.

    Auth: either admin JWT (via cookie/header) OR LOG_ACCESS_TOKEN query param.

    Query params:
        lines  - max entries to return (default 100, max 2000)
        level  - filter by level: DEBUG, INFO, WARNING, ERROR, CRITICAL
        search - substring filter on log message
        token  - static LOG_ACCESS_TOKEN for CLI access without JWT
    """
    # Auth: static token OR admin JWT
    log_token = os.environ.get("LOG_ACCESS_TOKEN", "")
    if token and log_token and token == log_token:
        pass  # static token auth OK
    else:
        # Fall back to JWT admin auth
        try:
            user = await auth.get_current_user(request)
            if user.get("role") != "admin":
                return JSONResponse({"error": "Admin required"}, status_code=403)
        except Exception:
            return JSONResponse({"error": "Set LOG_ACCESS_TOKEN env var or use admin JWT"}, status_code=401)

    lines = min(max(1, lines), 2000)
    entries = list(_log_buffer)
    if level:
        level_upper = level.upper()
        entries = [e for e in entries if f'"level": "{level_upper}"' in e]
    if search:
        entries = [e for e in entries if search.lower() in e.lower()]
    return {"count": len(entries[-lines:]), "logs": entries[-lines:]}


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

    # Get active jobs from the job registry for system health
    active_jobs = await db.get_all_active_jobs()

    return {
        "db_size_mb": db_size_mb,
        "results_size_mb": results_size_mb,
        "results_count": results_count,
        "benchmark_active": any(lock.locked() for lock in _user_locks.values()) or any(
            j["job_type"] == "benchmark" for j in active_jobs
        ),
        "active_jobs": active_jobs,
        "total_active": len([j for j in active_jobs if j["status"] == "running"]),
        "total_queued": len([j for j in active_jobs if j["status"] == "queued"]),
        "connected_ws_clients": ws_manager.get_connection_count(),
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

                # System prompt (empty string or null clears)
                if "system_prompt" in body:
                    sp_val = body["system_prompt"]
                    if sp_val and isinstance(sp_val, str) and sp_val.strip():
                        model["system_prompt"] = sp_val.strip()
                    else:
                        model.pop("system_prompt", None)

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
            logger.warning("Failed to decrypt API key for provider=%s user_id=%s", provider_key, user["id"])
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


@app.get("/api/lm-studio/detect")
async def detect_lm_studio_backend(provider_key: str, user: dict = Depends(auth.get_current_user)):
    """Detect LM Studio model backend type (GGUF vs MLX) via /v1/models.

    Queries the provider's api_base for the /v1/models endpoint and reads
    the compatibility_type field from each loaded model.

    Returns: {
        "available": true/false,
        "models": [{"id": "...", "compatibility_type": "gguf"|"mlx"|"unknown"}],
        "backend_type": "gguf"|"mlx"|"mixed"|"unknown",
        "mlx_unsupported_params": ["mirostat", "mirostat_eta", "mirostat_tau", "typical_p"]
    }
    """
    import httpx

    config = await _get_user_config(user["id"])
    prov_cfg = config.get("providers", {}).get(provider_key)
    if not prov_cfg:
        return JSONResponse({"error": f"Provider '{provider_key}' not found"}, status_code=404)

    api_base = prov_cfg.get("api_base", "")
    if not api_base:
        return {"available": False, "models": [], "backend_type": "unknown",
                "error": "No api_base configured for this provider"}

    # Resolve API key
    api_key = None
    encrypted = await db.get_user_key_for_provider(user["id"], provider_key)
    if encrypted:
        try:
            api_key = vault.decrypt(encrypted)
        except Exception:
            pass
    if not api_key:
        env_var = prov_cfg.get("api_key_env", "")
        if env_var:
            api_key = os.environ.get(env_var, "")

    url = f"{api_base.rstrip('/')}/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Params that MLX backend does NOT support (llama.cpp / GGUF only)
    mlx_unsupported = ["mirostat", "mirostat_eta", "mirostat_tau", "typical_p"]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])

        models = []
        compat_types = set()
        for m in data:
            ct = m.get("compatibility_type", "unknown")
            models.append({
                "id": m.get("id", ""),
                "compatibility_type": ct,
            })
            if ct and ct != "unknown":
                compat_types.add(ct)

        # Determine overall backend type
        if len(compat_types) == 0:
            backend_type = "unknown"
        elif len(compat_types) == 1:
            backend_type = compat_types.pop()
        else:
            backend_type = "mixed"

        return {
            "available": True,
            "models": models,
            "backend_type": backend_type,
            "mlx_unsupported_params": mlx_unsupported if backend_type in ("mlx", "mixed") else [],
        }

    except httpx.TimeoutException:
        return {"available": False, "models": [], "backend_type": "unknown",
                "error": "LM Studio not responding (timeout)"}
    except Exception as e:
        return {"available": False, "models": [], "backend_type": "unknown",
                "error": f"Failed to query LM Studio: {str(e)[:200]}"}


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
    logger.info("API key set: user_id=%s provider=%s", user["id"], provider_key)

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

    logger.info("API key deleted: user_id=%s provider=%s", user["id"], provider_key)
    return {"status": "ok"}


@app.post("/api/benchmark/cancel")
async def cancel_benchmark(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running benchmark.

    Accepts optional {job_id} in request body. If not provided, cancels
    the most recent active benchmark job for this user (backward compat).
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        logger.debug("Cancel request with empty/invalid body (backward compat)")

    job_id = body.get("job_id")

    if job_id:
        # Cancel specific job
        await job_registry.cancel(job_id, user["id"])
    else:
        # Backward compat: cancel the most recent active benchmark for this user
        # Also signal the old cancel event for any legacy SSE streams
        _get_user_cancel(user["id"]).set()
        active = await db.get_user_active_jobs(user["id"])
        for j in active:
            if j["job_type"] == "benchmark":
                await job_registry.cancel(j["id"], user["id"])
                break

    await db.log_audit(
        user_id=user["id"],
        username=user.get("email", ""),
        action="benchmark_cancel",
        resource_type="benchmark",
        resource_id=job_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )

    return {"status": "ok", "message": "Cancellation requested"}


@app.get("/api/user/rate-limit")
async def get_rate_limit(user: dict = Depends(auth.get_current_user)):
    """Return the user's current rate limit status."""
    allowed, remaining = _check_rate_limit(user["id"])
    return {"limit": RATE_LIMIT_PER_HOUR, "remaining": remaining, "window": "1 hour"}


async def _benchmark_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for benchmark execution.

    Extracts the core benchmark logic from the old SSE generator.
    Returns the benchmark_run ID on success, or None.
    """
    user_id = params["user_id"]
    model_ids = params["models"]
    _raw_ts = params.get("target_set")  # serialized as list-of-lists
    target_set = {tuple(t) for t in _raw_ts} if _raw_ts else None
    runs = params.get("runs", 3)
    max_tokens = params.get("max_tokens", 512)
    temperature = params.get("temperature", 0.7)
    prompt = params.get("prompt", "")
    context_tiers = params.get("context_tiers", [0])
    warmup = params.get("warmup", True)
    provider_params = params.get("provider_params")

    logger.info(
        "Benchmark started: job_id=%s user_id=%s models=%d tiers=%s runs=%d",
        job_id, user_id, len(model_ids) if model_ids else 0, context_tiers, runs,
    )

    config = await _get_user_config(user_id)
    defaults = config.get("defaults", {})
    all_targets = build_targets(config)

    # Filter to requested models (precise provider_key+model_id or legacy model_id-only)
    targets = _filter_targets(all_targets, model_ids, target_set)

    # Inject per-user API keys
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    if not prompt.strip():
        prompt = defaults.get("prompt", "Explain recursion in programming with a Python example.")

    # Calculate total runs across all tiers
    total = 0
    for tier in context_tiers:
        for target in targets:
            headroom = target.context_window - max_tokens - 100
            if tier == 0 or tier <= headroom:
                total += runs

    if total == 0:
        return None

    # Group targets by provider for parallel execution
    provider_groups = {}
    for target in targets:
        provider_groups.setdefault(target.provider, []).append(target)

    results_queue = asyncio.Queue()

    # Helper to send WebSocket messages directly to the user
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Send benchmark_init so frontend can set up per-provider progress tracking
    await _ws_send({
        "type": "benchmark_init",
        "job_id": job_id,
        "data": {
            "targets": [{"provider_key": t.provider_key, "model_id": t.model_id} for t in targets],
            "runs": runs,
            "context_tiers": context_tiers,
            "max_tokens": max_tokens,
        },
    })

    async def run_provider(prov_targets):
        """Run all benchmarks for one provider sequentially."""
        for tier in context_tiers:
            for target in prov_targets:
                if cancel_event.is_set():
                    return
                headroom = target.context_window - max_tokens - 100
                if tier > 0 and tier > headroom:
                    continue  # Skip tier exceeding context window

                # Warm-up run (discarded)
                if warmup:
                    await async_run_single(
                        target, prompt, max_tokens, temperature, tier,
                        provider_params=provider_params,
                    )

                for r in range(runs):
                    if cancel_event.is_set():
                        return

                    # Notify frontend that this provider/model/run is starting
                    await _ws_send({
                        "type": "benchmark_progress",
                        "job_id": job_id,
                        "data": {
                            "type": "progress",
                            "provider": target.provider,
                            "model": target.display_name,
                            "model_id": target.model_id,
                            "run": r + 1,
                            "runs": runs,
                            "context_tokens": tier,
                        },
                    })

                    result = await async_run_single(
                        target, prompt, max_tokens, temperature, tier,
                        provider_params=provider_params,
                    )
                    await results_queue.put({
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
    tasks = [asyncio.create_task(run_provider(g)) for g in provider_groups.values()]

    async def sentinel():
        await asyncio.gather(*tasks, return_exceptions=True)
        await results_queue.put(None)

    asyncio.create_task(sentinel())

    # Consume results and report progress
    current = 0
    all_results = []
    while True:
        try:
            item = await asyncio.wait_for(results_queue.get(), timeout=15)
        except asyncio.TimeoutError:
            continue  # Keep waiting
        if item is None:
            break
        if cancel_event.is_set():
            for t in tasks:
                t.cancel()
            return None
        if item["type"] == "result":
            current += 1
            all_results.append(item)
            pct = int((current / total) * 100) if total > 0 else 0
            detail = f"{item['model']}, Run {item['run']}/{item['runs']}"
            if item["context_tokens"] > 0:
                detail += f", Context {item['context_tokens'] // 1000}K"
            await progress_cb(pct, detail)

            # Send individual result data to frontend via WebSocket
            await _ws_send({
                "type": "benchmark_result",
                "job_id": job_id,
                "data": item,
            })

    # Save results
    if all_results:
        agg_results = _aggregate(all_results, config)
        save_results(agg_results, prompt, context_tiers=context_tiers)

        run_id = await db.save_benchmark_run(
            user_id=user_id,
            prompt=prompt,
            context_tiers=json.dumps(context_tiers),
            results_json=json.dumps(all_results),
        )

        logger.info(
            "Benchmark completed: job_id=%s user_id=%s results=%d run_id=%s",
            job_id, user_id, len(all_results), run_id,
        )

        await db.log_audit(
            user_id=user_id,
            username=params.get("user_email", ""),
            action="benchmark_complete",
            resource_type="benchmark",
            detail={"models": model_ids, "result_count": len(all_results)},
        )

        return run_id

    logger.info("Benchmark produced no results: job_id=%s user_id=%s", job_id, user_id)
    return None


# Register the benchmark handler with the job registry
job_registry.register_handler("benchmark", _benchmark_handler)


@app.post("/api/benchmark")
async def run_benchmark(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run benchmarks via the job registry. Returns job_id immediately.

    Progress is delivered via WebSocket (not SSE). The frontend receives
    job_created, job_started, job_progress, and job_completed events.
    """
    body = await request.json()
    model_ids, target_set = _parse_target_selection(body)
    runs = body.get("runs", 3)
    max_tokens = body.get("max_tokens", 512)
    temperature = body.get("temperature", 0.7)
    prompt = body.get("prompt", "")
    context_tiers = body.get("context_tiers", [0])
    warmup = body.get("warmup", True)
    provider_params = body.get("provider_params")

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

    # Build progress detail for initial display
    model_count = len(model_ids)
    progress_detail = f"Benchmark: {model_count} model{'s' if model_count != 1 else ''}, {runs} run{'s' if runs != 1 else ''} each"

    # Submit to job registry (starts immediately or queues based on concurrency limit)
    params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "models": model_ids,
        "target_set": [list(t) for t in target_set] if target_set else None,
        "runs": runs,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "prompt": prompt,
        "context_tiers": context_tiers,
        "warmup": warmup,
        "provider_params": provider_params,
    }

    job_id = await job_registry.submit(
        job_type="benchmark",
        user_id=user["id"],
        params=params,
        progress_detail=progress_detail,
    )

    # Return immediately -- frontend gets progress via WebSocket
    return {"job_id": job_id, "status": "submitted"}


@app.get("/api/history")
async def get_history(user: dict = Depends(auth.get_current_user)):
    """Get the current user's benchmark history from the database."""
    runs = await db.get_user_benchmark_runs(user["id"])
    # Parse results_json back to objects for the frontend
    for run in runs:
        if isinstance(run.get("results_json"), str):
            try:
                run["results"] = json.loads(run["results_json"])
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse results_json for run")
                run["results"] = []
            del run["results_json"]
        if isinstance(run.get("context_tiers"), str):
            try:
                run["context_tiers"] = json.loads(run["context_tiers"])
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse context_tiers for run")
    return {"runs": runs}


@app.get("/api/history/{run_id}")
async def get_history_run(run_id: str, user: dict = Depends(auth.get_current_user)):
    """Return a specific benchmark run from the database."""
    run = await db.get_benchmark_run(run_id, user["id"])
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    if isinstance(run.get("results_json"), str):
        try:
            run["results"] = json.loads(run["results_json"])
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse results_json for run %s", run_id)
            run["results"] = []
        del run["results_json"]
    if isinstance(run.get("context_tiers"), str):
        try:
            run["context_tiers"] = json.loads(run["context_tiers"])
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse context_tiers for run %s", run_id)
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
        logger.debug("_maybe_parse_json: value is not JSON, returning as-is")
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
        # Multi-turn config
        mt_config = None
        if item.get("multi_turn") or item.get("multi_turn_config"):
            mt_obj = item.get("multi_turn_config") or {}
            if not mt_obj and item.get("multi_turn"):
                mt_obj = {"multi_turn": True}
            if item.get("max_rounds"):
                mt_obj["max_rounds"] = item["max_rounds"]
            if item.get("mock_responses"):
                mt_obj["mock_responses"] = item["mock_responses"]
            if item.get("valid_prerequisites"):
                mt_obj["valid_prerequisites"] = item["valid_prerequisites"]
            if item.get("optimal_hops"):
                mt_obj["optimal_hops"] = item["optimal_hops"]
            if not mt_obj.get("multi_turn"):
                mt_obj["multi_turn"] = True
            mt_config = json.dumps(mt_obj)
        await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config=mt_config)
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
                logger.debug("Failed to parse expected_params for test case in suite %s", suite_id)
        if c.get("multi_turn_config"):
            try:
                mt = json.loads(c["multi_turn_config"]) if isinstance(c["multi_turn_config"], str) else c["multi_turn_config"]
                c["multi_turn"] = mt.get("multi_turn", False)
                c["max_rounds"] = mt.get("max_rounds", 5)
                c["mock_responses"] = mt.get("mock_responses", {})
                c["valid_prerequisites"] = mt.get("valid_prerequisites", [])
                c["optimal_hops"] = mt.get("optimal_hops", 2)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse multi_turn_config for test case in suite %s", suite_id)
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


@app.get("/api/tool-suites/{suite_id}/export")
async def export_tool_suite(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """Export a tool suite as a downloadable JSON file (matches import format)."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    tools = json.loads(suite["tools_json"]) if suite.get("tools_json") else []
    cases = await db.get_test_cases(suite_id)
    test_cases = []
    for c in cases:
        tc = {"prompt": c["prompt"]}
        et = _parse_expected_tool(c["expected_tool"])
        if et is not None:
            tc["expected_tool"] = et
        if c.get("expected_params"):
            try:
                tc["expected_params"] = json.loads(c["expected_params"]) if isinstance(c["expected_params"], str) else c["expected_params"]
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse expected_params during export")
        if c.get("param_scoring") and c["param_scoring"] != "exact":
            tc["param_scoring"] = c["param_scoring"]
        if c.get("multi_turn_config"):
            try:
                mt = json.loads(c["multi_turn_config"]) if isinstance(c["multi_turn_config"], str) else c["multi_turn_config"]
                if mt.get("multi_turn"):
                    tc["multi_turn"] = True
                    for k in ("max_rounds", "mock_responses", "valid_prerequisites", "optimal_hops"):
                        if k in mt:
                            tc[k] = mt[k]
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse multi_turn_config during export")
        test_cases.append(tc)
    export_data = {
        "name": suite.get("name", "Untitled"),
        "description": suite.get("description", ""),
        "tools": tools,
        "test_cases": test_cases,
    }
    slug = re.sub(r'[^a-z0-9]+', '-', (suite.get("name") or "suite").lower()).strip('-')[:40]
    headers = {"Content-Disposition": f'attachment; filename=suite-{slug}.json'}
    return JSONResponse(content=export_data, headers=headers)


@app.get("/api/tool-eval/import/example")
async def tool_eval_import_example():
    """Return an example JSON template for suite import."""
    example = {
        "name": "Weather API Suite",
        "description": "Tests weather-related tool calling",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                            "units": {"type": "string", "enum": ["celsius", "fahrenheit"], "description": "Temperature units"}
                        },
                        "required": ["city"]
                    }
                }
            }
        ],
        "test_cases": [
            {
                "prompt": "What's the weather in Paris?",
                "expected_tool": "get_weather",
                "expected_params": {"city": "Paris"}
            },
            {
                "prompt": "Check temperature in Tokyo in fahrenheit",
                "expected_tool": "get_weather",
                "expected_params": {"city": "Tokyo", "units": "fahrenheit"}
            },
            {
                "prompt": "Tell me a joke",
                "expected_tool": None,
                "expected_params": None
            }
        ]
    }
    headers = {"Content-Disposition": 'attachment; filename=suite-example.json'}
    return JSONResponse(content=example, headers=headers)


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
                logger.debug("Failed to parse expected_params for case in suite %s", suite_id)
        if c.get("multi_turn_config"):
            try:
                mt = json.loads(c["multi_turn_config"]) if isinstance(c["multi_turn_config"], str) else c["multi_turn_config"]
                c["multi_turn"] = mt.get("multi_turn", False)
                c["max_rounds"] = mt.get("max_rounds", 5)
                c["mock_responses"] = mt.get("mock_responses", {})
                c["valid_prerequisites"] = mt.get("valid_prerequisites", [])
                c["optimal_hops"] = mt.get("optimal_hops", 2)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse multi_turn_config for case in suite %s", suite_id)
    return {"cases": cases}


@app.post("/api/tool-suites/{suite_id}/cases")
async def create_test_cases(suite_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Add test case(s) to a suite. Supports single or bulk via 'cases' array."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    body = await request.json()

    def _extract_mt_config(item: dict) -> str | None:
        """Extract multi_turn_config JSON string from a request item."""
        if not item.get("multi_turn") and not item.get("multi_turn_config"):
            return None
        mt_obj = item.get("multi_turn_config") or {}
        if not mt_obj and item.get("multi_turn"):
            mt_obj = {"multi_turn": True}
        if item.get("max_rounds"):
            mt_obj["max_rounds"] = item["max_rounds"]
        if item.get("mock_responses"):
            mt_obj["mock_responses"] = item["mock_responses"]
        if item.get("valid_prerequisites"):
            mt_obj["valid_prerequisites"] = item["valid_prerequisites"]
        if item.get("optimal_hops"):
            mt_obj["optimal_hops"] = item["optimal_hops"]
        if not mt_obj.get("multi_turn"):
            mt_obj["multi_turn"] = True
        return json.dumps(mt_obj)

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
            mt_config = _extract_mt_config(item)
            await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config=mt_config)
            created += 1
        return {"status": "ok", "created": created}

    # Single mode
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    expected_tool = _serialize_expected_tool(body.get("expected_tool"))
    expected_params = json.dumps(body["expected_params"]) if body.get("expected_params") is not None else None
    param_scoring = body.get("param_scoring", "exact")
    mt_config = _extract_mt_config(body)
    case_id = await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config=mt_config)
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
    # Multi-turn config
    mt_config = None
    if "multi_turn" in body or "multi_turn_config" in body:
        if body.get("multi_turn"):
            mt_obj = body.get("multi_turn_config") or {}
            if not mt_obj:
                mt_obj = {"multi_turn": True}
            if body.get("max_rounds"):
                mt_obj["max_rounds"] = body["max_rounds"]
            if body.get("mock_responses"):
                mt_obj["mock_responses"] = body["mock_responses"]
            if body.get("valid_prerequisites"):
                mt_obj["valid_prerequisites"] = body["valid_prerequisites"]
            if body.get("optimal_hops"):
                mt_obj["optimal_hops"] = body["optimal_hops"]
            if not mt_obj.get("multi_turn"):
                mt_obj["multi_turn"] = True
            mt_config = json.dumps(mt_obj)
        else:
            # multi_turn explicitly set to false -- clear the config
            mt_config = ""  # empty string to clear in DB
    updated = await db.update_test_case(case_id, suite_id, prompt=prompt, expected_tool=expected_tool, expected_params=expected_params, param_scoring=param_scoring, multi_turn_config=mt_config)
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


def score_multi_turn(
    tool_chain: list[dict],
    expected_tool: str | list[str],
    expected_params: dict | None,
    valid_prerequisites: list[str],
    optimal_hops: int,
) -> dict:
    """Score a multi-turn tool calling chain.

    Returns dict with: completion, efficiency, redundancy_penalty, detour_penalty, overall_score
    """
    if not tool_chain:
        return {"completion": 0.0, "efficiency": 0.0, "redundancy_penalty": 0.0, "detour_penalty": 0.0, "overall_score": 0.0}

    # --- Completion: did the final tool call match expected? ---
    final_call = tool_chain[-1]
    tool_score = score_tool_selection(expected_tool, final_call.get("tool_name"))
    param_score = score_params(expected_params, final_call.get("params"))
    completion = compute_overall_score(tool_score, param_score)

    # --- Efficiency: optimal_hops / actual_hops ---
    actual_hops = len(tool_chain)
    efficiency = min(1.0, optimal_hops / actual_hops) if actual_hops > 0 else 0.0

    # --- Redundancy: -10% per consecutive identical tool call ---
    redundancy_penalty = 0.0
    for i in range(1, len(tool_chain)):
        if tool_chain[i].get("tool_name") == tool_chain[i-1].get("tool_name"):
            redundancy_penalty += 0.10

    # --- Detour: -10% per call not in valid_prerequisites and not the final tool ---
    detour_penalty = 0.0
    valid_set = set(p.lower() for p in valid_prerequisites) if valid_prerequisites else set()
    # Also add the expected final tool(s) to valid set
    if isinstance(expected_tool, list):
        valid_set.update(t.lower() for t in expected_tool)
    elif expected_tool:
        valid_set.add(expected_tool.lower())

    for call in tool_chain[:-1]:  # exclude final call
        name = (call.get("tool_name") or "").lower()
        if name and name not in valid_set:
            detour_penalty += 0.10

    # --- Composite ---
    overall = max(0.0, completion * efficiency - redundancy_penalty - detour_penalty)
    overall = min(1.0, overall)

    return {
        "completion": round(completion, 4),
        "efficiency": round(efficiency, 4),
        "redundancy_penalty": round(redundancy_penalty, 4),
        "detour_penalty": round(detour_penalty, 4),
        "overall_score": round(overall, 4),
    }


# --- Eval Engine: Helpers ---

def _capture_raw_response(response) -> dict:
    """Extract raw response data from a litellm response object."""
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
    return raw_resp


def _tool_matches(actual_tool: str | None, expected_tool) -> bool:
    """Check if actual tool matches expected (str or list)."""
    if actual_tool is None or expected_tool is None:
        return False
    if isinstance(expected_tool, list):
        return actual_tool.lower() in [e.lower() for e in expected_tool]
    return actual_tool.lower() == expected_tool.lower()


# --- Eval Engine: Single Eval Execution ---

async def run_single_eval(
    target: Target,
    tools: list[dict],
    test_case: dict,
    temperature: float,
    tool_choice: str = "required",
    provider_params: dict | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Run one test case against one model. Returns result dict.

    Uses litellm.acompletion() (non-streaming, since we need tool_calls).
    Optional system_prompt injects a system message before the user prompt
    (used by Prompt Tuner to test prompt variations).
    """
    # Parse expected values
    expected_tool = _parse_expected_tool(test_case.get("expected_tool"))
    expected_params = test_case.get("expected_params")
    if isinstance(expected_params, str):
        try:
            expected_params = json.loads(expected_params)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse expected_params for test case %s", test_case.get("id"))
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

    # Build validated+clamped params via provider_params module
    pp_copy = dict(provider_params) if provider_params else None
    extra = build_litellm_kwargs(
        target, provider_params=pp_copy, temperature=temperature,
    )

    # Build messages: per-model system_prompt (from config) + explicit system_prompt (from prompt tuner)
    messages = []
    combined_system = ""
    if target.system_prompt:
        combined_system = target.system_prompt
    if system_prompt:
        combined_system = (combined_system + "\n\n" + system_prompt) if combined_system else system_prompt
    if combined_system:
        messages.append({"role": "system", "content": combined_system})
    messages.append({"role": "user", "content": test_case["prompt"]})

    kwargs = {
        "model": target.model_id,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": 1024,
        "timeout": 120,
    }
    if target.api_base:
        kwargs["api_base"] = target.api_base
    if target.api_key:
        kwargs["api_key"] = target.api_key
    # Apply validated params from build_litellm_kwargs
    if extra:
        kwargs.update(extra)
    else:
        # Fallback: no provider_params, apply directly (backward compat)
        if "temperature" not in (target.skip_params or []):
            kwargs["temperature"] = temperature
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
                logger.debug("tool_choice=required failed, falling back to auto for %s", target.model_id)
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
                logger.debug("Failed to parse tool call arguments")
                result["actual_params"] = None
        else:
            result["actual_tool"] = None
            result["actual_params"] = None

        # Normalize: some local LLMs stuff a full JSON object into function.name
        # (e.g. '{"name":"get_time","arguments":{}}' instead of just 'get_time').
        # Also handles models that return tool calls as text in message.content.
        _raw_tool = result.get("actual_tool")
        if _raw_tool and _raw_tool.strip().startswith("{"):
            try:
                parsed = json.loads(_raw_tool)
                if "name" in parsed:
                    result["actual_tool"] = parsed["name"]
                    if not result.get("actual_params") and (parsed.get("arguments") or parsed.get("parameters")):
                        result["actual_params"] = parsed.get("arguments") or parsed.get("parameters")
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse JSON-in-tool-name for model %s", target.model_id)
        elif not _raw_tool and message.content:
            try:
                content = message.content.strip()
                start_idx = content.find('{')
                end_idx = content.rfind('}')
                if start_idx >= 0 and end_idx > start_idx:
                    parsed = json.loads(content[start_idx:end_idx + 1])
                    if "name" in parsed:
                        result["actual_tool"] = parsed["name"]
                        result["actual_params"] = parsed.get("arguments") or parsed.get("parameters") or {}
            except Exception:
                logger.debug("Failed to extract tool call from message content for model %s", target.model_id)

        result["latency_ms"] = round(latency_ms)

        result["raw_response"] = _capture_raw_response(response)

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


# --- Eval Engine: Multi-Turn Eval Execution ---

async def run_multi_turn_eval(
    target: Target,
    tools: list[dict],
    test_case: dict,
    temperature: float,
    tool_choice: str = "required",
    provider_params: dict | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Run a multi-turn test case against one model. Returns result dict.

    Loops up to max_rounds, feeding mock tool responses back to the model
    until it calls the expected final tool or exhausts rounds.
    Optional system_prompt injects a system message before the user prompt
    (used by Prompt Tuner to test prompt variations).
    """
    mt_config = test_case.get("_mt_config", {})
    max_rounds = mt_config.get("max_rounds", 5)
    mock_responses = mt_config.get("mock_responses", {})
    valid_prerequisites = mt_config.get("valid_prerequisites", [])
    optimal_hops = mt_config.get("optimal_hops", 2)

    expected_tool = _parse_expected_tool(test_case.get("expected_tool"))
    expected_params = test_case.get("expected_params")
    if isinstance(expected_params, str):
        try:
            expected_params = json.loads(expected_params)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse expected_params for multi-turn test case %s", test_case.get("id"))
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
        # Multi-turn specific fields
        "multi_turn": True,
        "tool_chain": [],
        "rounds_used": 0,
        "completion_score": 0.0,
        "efficiency_score": 0.0,
        "redundancy_penalty": 0.0,
        "detour_penalty": 0.0,
        "raw_exchanges": [],
    }

    # Build messages: per-model system_prompt (from config) + explicit system_prompt (from prompt tuner)
    messages = []
    combined_system = ""
    if target.system_prompt:
        combined_system = target.system_prompt
    if system_prompt:
        combined_system = (combined_system + "\n\n" + system_prompt) if combined_system else system_prompt
    if combined_system:
        messages.append({"role": "system", "content": combined_system})
    messages.append({"role": "user", "content": test_case["prompt"]})

    # Build validated+clamped params via provider_params module
    pp_copy = dict(provider_params) if provider_params else None
    extra = build_litellm_kwargs(
        target, provider_params=pp_copy, temperature=temperature,
    )

    base_kwargs = {
        "model": target.model_id,
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": 1024,
        "timeout": 120,
    }
    if target.api_base:
        base_kwargs["api_base"] = target.api_base
    if target.api_key:
        base_kwargs["api_key"] = target.api_key
    # Apply validated params from build_litellm_kwargs
    if extra:
        base_kwargs.update(extra)
    else:
        # Fallback: no provider_params, apply directly (backward compat)
        if "temperature" not in (target.skip_params or []):
            base_kwargs["temperature"] = temperature
        if target.skip_params:
            for p in target.skip_params:
                if p != "temperature":
                    base_kwargs.pop(p, None)

    total_latency = 0.0

    try:
        for round_num in range(max_rounds):
            kwargs = {**base_kwargs, "messages": messages}

            # Capture raw request (sanitize)
            raw_req = dict(kwargs)
            raw_req.pop("api_key", None)
            if "tools" in raw_req:
                raw_req["tools_summary"] = [t["function"]["name"] for t in raw_req["tools"]]
                raw_req["tools_count"] = len(raw_req["tools"])

            start = time.perf_counter()
            try:
                response = await litellm.acompletion(**kwargs)
            except Exception:
                if kwargs.get("tool_choice") == "required":
                    logger.debug("tool_choice=required failed in multi-turn, falling back to auto for %s", target.model_id)
                    kwargs["tool_choice"] = "auto"
                    response = await litellm.acompletion(**kwargs)
                else:
                    raise
            latency_ms = (time.perf_counter() - start) * 1000
            total_latency += latency_ms

            raw_resp = _capture_raw_response(response)
            result["raw_exchanges"].append({"request": raw_req, "response": raw_resp})

            message = response.choices[0].message

            # Check if model made a tool call
            if not message.tool_calls or len(message.tool_calls) == 0:
                # Model stopped calling tools -- end loop
                result["rounds_used"] = round_num + 1
                break

            tool_call = message.tool_calls[0]
            called_tool = tool_call.function.name
            try:
                called_params = json.loads(tool_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse multi-turn tool call arguments")
                called_params = None

            result["tool_chain"].append({
                "tool_name": called_tool,
                "params": called_params,
                "round": round_num + 1,
            })

            # Check if this is the expected final tool
            if _tool_matches(called_tool, expected_tool):
                result["actual_tool"] = called_tool
                result["actual_params"] = called_params
                result["rounds_used"] = round_num + 1
                break

            # Not the final tool -- look up mock response and continue
            mock_result = mock_responses.get(called_tool, {"status": "ok"})

            # Append assistant message + tool result to conversation
            messages.append({
                "role": "assistant",
                "content": getattr(message, "content", None) or "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": called_tool,
                            "arguments": tool_call.function.arguments,
                        }
                    }
                ],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(mock_result) if isinstance(mock_result, dict) else str(mock_result),
            })

            result["rounds_used"] = round_num + 1
        else:
            # Hit max rounds without finding the expected tool
            result["rounds_used"] = max_rounds

        result["latency_ms"] = round(total_latency)

        # Set raw_request/raw_response to first/last exchange for compatibility
        if result["raw_exchanges"]:
            result["raw_request"] = result["raw_exchanges"][0]["request"]
            result["raw_response"] = result["raw_exchanges"][-1]["response"]

        # If model never called the expected tool, actual_tool stays None
        if result["actual_tool"] is None and result["tool_chain"]:
            result["actual_tool"] = result["tool_chain"][-1]["tool_name"]
            result["actual_params"] = result["tool_chain"][-1]["params"]

        # Score using multi-turn scoring
        scores = score_multi_turn(
            tool_chain=result["tool_chain"],
            expected_tool=expected_tool,
            expected_params=expected_params,
            valid_prerequisites=valid_prerequisites,
            optimal_hops=optimal_hops,
        )
        result["completion_score"] = scores["completion"]
        result["efficiency_score"] = scores["efficiency"]
        result["redundancy_penalty"] = scores["redundancy_penalty"]
        result["detour_penalty"] = scores["detour_penalty"]
        result["overall_score"] = scores["overall_score"]

        # Also set individual scores for summary compatibility
        result["tool_selection_score"] = score_tool_selection(expected_tool, result["actual_tool"])
        result["param_accuracy"] = score_params(expected_params, result["actual_params"])

    except Exception as e:
        result["success"] = False
        result["error"] = sanitize_error(str(e)[:200], target.api_key)
        return result

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


# --- Eval Engine: Job Registry Handler ---


async def _tool_eval_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for tool eval execution.

    Extracts the core tool eval logic from the old SSE generator.
    Returns the eval_id on success, or None.
    """
    user_id = params["user_id"]
    suite_id = params["suite_id"]
    model_ids = params["models"]
    _raw_ts = params.get("target_set")
    target_set = {tuple(t) for t in _raw_ts} if _raw_ts else None
    temperature = float(params.get("temperature", 0.0))
    tool_choice = params.get("tool_choice", "required")
    provider_params = params.get("provider_params")
    judge_config = params.get("judge")
    judge_concurrency = int(params.get("judge_concurrency", 4))

    logger.info(
        "Tool eval started: job_id=%s user_id=%s models=%d",
        job_id, user_id, len(model_ids) if model_ids else 0,
    )


    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    cases = await db.get_test_cases(suite_id)
    tools = json.loads(suite["tools_json"])

    # Build targets
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    targets = _filter_targets(all_targets, model_ids, target_set)

    # Inject per-user API keys
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    if not targets:
        return None

    # Judge setup (opt-in)
    judge_enabled = False
    judge_mode = "none"
    judge_target = None
    judge_custom_instructions = ""
    if isinstance(judge_config, dict) and judge_config.get("enabled"):
        judge_mode = judge_config.get("mode", "none")
        judge_model_id = judge_config.get("judge_model")
        judge_custom_instructions = judge_config.get("custom_instructions", "")
        if judge_mode in ("live_inline", "post_eval") and judge_model_id:
            judge_enabled = True
            # Prefer already-injected eval targets (keys already set)
            jt_list = [t for t in targets if t.model_id == judge_model_id]
            if jt_list:
                judge_target = jt_list[0]
                logger.debug("Judge target found in eval set (key already injected): %s", judge_model_id)
            else:
                # Judge model not in eval set — fall back to all_targets + inject
                jt_list = [t for t in all_targets if t.model_id == judge_model_id]
                if jt_list:
                    judge_target = jt_list[0]
                    if judge_target.provider_key:
                        enc = await db.get_user_key_for_provider(user_id, judge_target.provider_key)
                        if enc:
                            judge_target = inject_user_keys([judge_target], {judge_target.provider_key: enc})[0]
                    logger.debug("Judge target from all_targets (separate injection): %s", judge_model_id)
                else:
                    judge_enabled = False
            if judge_target:
                logger.debug(
                    "Judge target ready: model=%s api_base=%s has_key=%s",
                    judge_target.model_id, judge_target.api_base,
                    bool(judge_target.api_key),
                )

    # Auto-cap judge concurrency when judge shares an endpoint with eval models.
    # Local models (LM Studio, Ollama) can't handle many concurrent requests;
    # firing 4+ judge calls overwhelms the server (502).
    if judge_enabled and judge_target and judge_mode in ("live_inline", "post_eval"):
        eval_bases = {t.api_base for t in targets if t.api_base}
        if judge_target.api_base and judge_target.api_base in eval_bases:
            judge_concurrency = min(judge_concurrency, 1)
            logger.info("Judge shares endpoint with eval model — capping concurrency to 1")

    total = len(targets) * len(cases)
    results_queue = asyncio.Queue()

    # Helper to send WebSocket messages directly to the user
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Send init event so frontend can set up tracking
    await _ws_send({
        "type": "tool_eval_init",
        "job_id": job_id,
        "data": {
            "targets": [{"provider_key": t.provider_key, "model_id": t.model_id, "display_name": t.display_name} for t in targets],
            "total_cases": len(cases),
            "suite_name": suite["name"],
            "judge_enabled": judge_enabled,
            "judge_mode": judge_mode,
        },
    })

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
                mt_config = None
                if case.get("multi_turn_config"):
                    try:
                        mt_config = json.loads(case["multi_turn_config"]) if isinstance(case["multi_turn_config"], str) else case["multi_turn_config"]
                    except (json.JSONDecodeError, TypeError):
                        logger.debug("Failed to parse multi_turn_config in tool eval handler")
                        mt_config = None

                if mt_config and mt_config.get("multi_turn"):
                    case_with_mt = {**case, "_mt_config": mt_config}
                    result = await run_multi_turn_eval(target, tools, case_with_mt, temperature, tool_choice, provider_params=provider_params)
                else:
                    result = await run_single_eval(target, tools, case, temperature, tool_choice, provider_params=provider_params)
                await results_queue.put(result)

    # Launch provider groups in parallel
    tasks = [asyncio.create_task(run_provider(g)) for g in provider_groups.values()]

    async def sentinel():
        await asyncio.gather(*tasks, return_exceptions=True)
        await results_queue.put(None)

    asyncio.create_task(sentinel())

    # Consume results and report progress
    current = 0
    all_results = []
    judge_verdicts = []
    judge_sem = asyncio.Semaphore(judge_concurrency)
    judge_queue = asyncio.Queue() if (judge_enabled and judge_mode == "live_inline" and judge_target) else None
    judge_tasks = []
    tool_defs_text = _build_tool_definitions_text(tools) if judge_enabled else ""
    target_map = {t.model_id: t for t in targets}

    while True:
        try:
            item = await asyncio.wait_for(results_queue.get(), timeout=15)
        except asyncio.TimeoutError:
            # Drain any ready judge verdicts
            if judge_queue:
                while not judge_queue.empty():
                    verdict = judge_queue.get_nowait()
                    judge_verdicts.append(verdict)
                    await _ws_send({"type": "judge_verdict", "job_id": job_id, **verdict})
            continue
        if item is None:
            break
        if cancel_event.is_set():
            for t in tasks:
                t.cancel()
            return None

        current += 1
        t = target_map.get(item["model_id"])
        model_display = t.display_name if t else item["model_id"]
        item["model_name"] = model_display

        # Report progress
        pct = int((current / total) * 100) if total > 0 else 0
        detail = f"{model_display}: {item.get('test_case_id', '?')}"
        await progress_cb(pct, detail)

        # Send progress + result via WebSocket
        await _ws_send({
            "type": "tool_eval_progress",
            "job_id": job_id,
            "data": {
                "current": current,
                "total": total,
                "model": model_display,
                "test_case": item.get("test_case_id", "?"),
            },
        })
        await _ws_send({
            "type": "tool_eval_result",
            "job_id": job_id,
            "data": item,
        })
        all_results.append(item)

        # Live inline judge: fire concurrent judge task per result (with semaphore)
        if judge_queue and judge_target:
            async def _judge_async(jt, td, res, jq, sem, ci=judge_custom_instructions):
                async with sem:
                    try:
                        v = await _judge_single_verdict(jt, td, {}, res, custom_instructions=ci)
                        v["test_case_id"] = res.get("test_case_id", "?")
                        v["model_id"] = res.get("model_id", "?")
                        await jq.put(v)
                    except Exception:
                        logger.exception("Inline judge failed for case=%s model=%s", res.get("test_case_id", "?"), res.get("model_id", "?"))
                        await jq.put({
                            "test_case_id": res.get("test_case_id", "?"),
                            "model_id": res.get("model_id", "?"),
                            "quality_score": 0,
                            "verdict": "error",
                            "summary": "Judge error",
                            "reasoning": "Judge model call failed",
                            "tool_selection_assessment": "unknown",
                            "param_assessment": "unknown",
                        })
            judge_tasks.append(asyncio.create_task(
                _judge_async(judge_target, tool_defs_text, item, judge_queue, judge_sem)
            ))

        # Drain any ready judge verdicts
        if judge_queue:
            while not judge_queue.empty():
                verdict = judge_queue.get_nowait()
                judge_verdicts.append(verdict)
                await _ws_send({"type": "judge_verdict", "job_id": job_id, **verdict})

    # Wait for remaining live inline judge tasks
    if judge_tasks:
        await asyncio.gather(*judge_tasks, return_exceptions=True)
        while not judge_queue.empty():
            verdict = judge_queue.get_nowait()
            judge_verdicts.append(verdict)
            await _ws_send({"type": "judge_verdict", "job_id": job_id, **verdict})

    # Compute per-model summaries
    summaries = _compute_eval_summaries(all_results, targets)
    for s in summaries:
        await _ws_send({"type": "tool_eval_summary", "job_id": job_id, "data": s})

    # Save to DB
    eval_id = await db.save_tool_eval_run(
        user_id=user_id,
        suite_id=suite["id"],
        suite_name=suite["name"],
        models_json=json.dumps(model_ids),
        results_json=json.dumps(all_results),
        summary_json=json.dumps(summaries),
        temperature=temperature,
    )

    # Store result_ref so frontend can discover eval_id on reconnect
    await db.set_job_result_ref(job_id, eval_id)

    # --- Post-eval judge mode ---
    judge_report_id = None
    if judge_enabled and judge_mode == "post_eval" and judge_target and all_results:
        try:
            # Brief pause to let cloudflared/local server release eval connections
            await asyncio.sleep(2)
            logger.info("Post-eval judge starting: model=%s api_base=%s concurrency=%d cases=%d",
                        judge_target.model_id, judge_target.api_base, judge_concurrency, len(all_results))
            judge_report_id = await db.save_judge_report(
                user_id=user_id,
                judge_model=judge_target.model_id,
                mode="post_eval",
                eval_run_id=eval_id,
            )
            await _ws_send({
                "type": "judge_start",
                "job_id": job_id,
                "mode": "post_eval",
                "judge_model": judge_target.display_name,
                "cases_to_review": len(all_results),
            })

            model_results: dict[str, list[dict]] = {}
            for r in all_results:
                mid = r.get("model_id", "unknown")
                model_results.setdefault(mid, []).append(r)

            pe_verdicts = []
            all_pe_reports = []
            judge_completed = 0
            judge_total = len(all_results)
            for mid, mres in model_results.items():
                model_vds = []
                # Use semaphore for concurrent judge calls
                sem = asyncio.Semaphore(judge_concurrency)

                async def _judge_one(r, _mid=mid):
                    async with sem:
                        v = await _judge_single_verdict(judge_target, tool_defs_text, {}, r, custom_instructions=judge_custom_instructions)
                        v["test_case_id"] = r.get("test_case_id", "?")
                        v["model_id"] = _mid
                        return v

                judge_batch = [asyncio.create_task(_judge_one(r)) for r in mres]
                try:
                    for coro in asyncio.as_completed(judge_batch):
                        if cancel_event.is_set():
                            for bt in judge_batch:
                                bt.cancel()
                            break
                        v = await coro
                        pe_verdicts.append(v)
                        model_vds.append(v)
                        judge_completed += 1
                        await _ws_send({"type": "judge_verdict", "job_id": job_id, **v})
                        # Update progress for judge phase
                        j_pct = int((judge_completed / judge_total) * 100) if judge_total > 0 else 0
                        await progress_cb(j_pct, f"Judge: {judge_completed}/{judge_total}")
                except Exception:
                    # Cancel remaining judge tasks to avoid orphaned "Task exception was never retrieved"
                    for bt in judge_batch:
                        bt.cancel()
                    raise

                if cancel_event.is_set():
                    break

                tgt = target_map.get(mid)
                mname = tgt.display_name if tgt else mid
                pe_report_data = await _judge_crosscase(judge_target, mname, model_vds)
                pe_report_data["model_id"] = mid
                pe_report_data["model_name"] = mname
                all_pe_reports.append(pe_report_data)
                await _ws_send({"type": "judge_report", "job_id": job_id, "eval_id": eval_id, "report": pe_report_data})

            # Derive overall grade/score from the best model's report
            if all_pe_reports:
                best_pe = max(all_pe_reports, key=lambda r: r.get("overall_score", 0))
                pe_final_grade = best_pe.get("overall_grade", "?")
                pe_final_score = best_pe.get("overall_score", 0)
            else:
                pe_final_grade = "?"
                pe_final_score = 0

            await db.update_judge_report(
                judge_report_id,
                verdicts_json=json.dumps(pe_verdicts),
                report_json=json.dumps(all_pe_reports),
                overall_grade=pe_final_grade,
                overall_score=pe_final_score,
                status="completed",
            )
            await _ws_send({"type": "judge_complete", "job_id": job_id, "judge_report_id": judge_report_id})
        except Exception as je:
            logger.exception("Post-eval judge failed: job_id=%s", job_id)
            if judge_report_id:
                await db.update_judge_report(judge_report_id, status="error")

    # --- Live inline judge: save report ---
    elif judge_enabled and judge_mode == "live_inline" and judge_verdicts:
        try:
            judge_report_id = await db.save_judge_report(
                user_id=user_id,
                judge_model=judge_target.model_id,
                mode="live_inline",
                eval_run_id=eval_id,
            )
            model_results_j: dict[str, list[dict]] = {}
            for v in judge_verdicts:
                mid = v.get("model_id", "unknown")
                model_results_j.setdefault(mid, []).append(v)

            all_li_reports = []
            for mid, mvds in model_results_j.items():
                tgt = target_map.get(mid)
                mname = tgt.display_name if tgt else mid
                li_report_data = await _judge_crosscase(judge_target, mname, mvds)
                li_report_data["model_id"] = mid
                li_report_data["model_name"] = mname
                all_li_reports.append(li_report_data)
                await _ws_send({"type": "judge_report", "job_id": job_id, "eval_id": eval_id, "report": li_report_data})

            # Derive overall grade/score from the best model's report
            if all_li_reports:
                best_li = max(all_li_reports, key=lambda r: r.get("overall_score", 0))
                li_final_grade = best_li.get("overall_grade", "?")
                li_final_score = best_li.get("overall_score", 0)
            else:
                li_final_grade = "?"
                li_final_score = 0

            await db.update_judge_report(
                judge_report_id,
                verdicts_json=json.dumps(judge_verdicts),
                report_json=json.dumps(all_li_reports),
                overall_grade=li_final_grade,
                overall_score=li_final_score,
                status="completed",
            )
            await _ws_send({"type": "judge_complete", "job_id": job_id, "judge_report_id": judge_report_id})
        except Exception as je:
            logger.exception("Live inline judge report failed: job_id=%s", job_id)
            if judge_report_id:
                await db.update_judge_report(judge_report_id, status="error")

    # Send completion event
    await _ws_send({
        "type": "tool_eval_complete",
        "job_id": job_id,
        "eval_id": eval_id,
        "judge_report_id": judge_report_id,
    })

    logger.info(
        "Tool eval completed: job_id=%s user_id=%s results=%d eval_id=%s",
        job_id, user_id, len(all_results), eval_id,
    )

    return eval_id


# Register the tool eval handler with the job registry
job_registry.register_handler("tool_eval", _tool_eval_handler)


@app.post("/api/tool-eval")
async def run_tool_eval(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run tool calling eval via job registry. Returns job_id immediately.

    Progress is delivered via WebSocket (tool_eval_result, tool_eval_progress, tool_eval_complete events).
    """
    body = await request.json()
    suite_id = body.get("suite_id")
    model_ids, target_set = _parse_target_selection(body)
    temperature = body.get("temperature", 0.0)
    tool_choice = body.get("tool_choice", "required")
    provider_params = body.get("provider_params")
    judge_config = body.get("judge")

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

    # Load suite + test cases (validate before submitting job)
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(suite_id)
    if not cases:
        return JSONResponse({"error": "Suite has no test cases"}, status_code=400)

    # Rate limit check
    allowed, remaining = _check_rate_limit(user["id"])
    if not allowed:
        return JSONResponse(
            {"error": f"Rate limit exceeded. Max {RATE_LIMIT_PER_HOUR} per hour."},
            status_code=429,
        )
    _record_rate_limit(user["id"])

    # Build targets (validate models exist in config)
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)
    targets = _filter_targets(all_targets, model_ids, target_set)
    if not targets:
        return JSONResponse({"error": "No matching models found in config"}, status_code=400)

    # Build progress detail
    model_count = len(targets)
    progress_detail = f"Tool Eval: {model_count} model{'s' if model_count != 1 else ''}, {suite['name']}"

    # Submit to job registry
    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "suite_id": suite_id,
        "models": model_ids,
        "target_set": [list(t) for t in target_set] if target_set else None,
        "temperature": temperature,
        "tool_choice": tool_choice,
        "provider_params": provider_params,
        "judge": judge_config,
        "judge_concurrency": body.get("judge_concurrency", 4),
    }

    job_id = await job_registry.submit(
        job_type="tool_eval",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@app.post("/api/tool-eval/cancel")
async def cancel_tool_eval(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running tool eval via job registry."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = body.get("job_id")
    if job_id:
        cancelled = await job_registry.cancel(job_id, user["id"])
        if cancelled:
            return {"status": "ok", "message": "Cancellation requested"}
        return JSONResponse({"error": "Job not found or not cancellable"}, status_code=404)
    # Fallback: cancel via legacy user-level event (for backward compatibility)
    _get_user_cancel(user["id"]).set()
    return {"status": "ok", "message": "Cancellation requested"}


# ---------------------------------------------------------------------------
# Parameter Tuner (GridSearchCV for Tool Calling)
# ---------------------------------------------------------------------------


def _expand_search_space(search_space: dict) -> list[dict]:
    """Expand a search space definition into a flat list of parameter configs.

    Numeric params: {"min": 0.0, "max": 1.0, "step": 0.5} -> [0.0, 0.5, 1.0]
    Categorical params: ["auto", "required"] -> ["auto", "required"]
    """
    import itertools

    param_names = []
    param_values = []

    for name, spec in search_space.items():
        if isinstance(spec, list):
            # Categorical
            if not spec:
                continue
            param_names.append(name)
            param_values.append(spec)
        elif isinstance(spec, dict):
            # Numeric range
            p_min = float(spec.get("min", 0))
            p_max = float(spec.get("max", 1))
            step = float(spec.get("step", 0.1))
            if step <= 0 or p_min > p_max:
                continue
            vals = []
            v = p_min
            while v <= p_max + 1e-9:
                vals.append(round(v, 6))
                v += step
            if not vals:
                continue
            param_names.append(name)
            param_values.append(vals)

    if not param_names:
        return [{}]

    combos = []
    for combo in itertools.product(*param_values):
        combos.append(dict(zip(param_names, combo)))
    return combos


async def _param_tune_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for parameter tuning grid search.

    Extracts the core param tune logic from the old SSE generator.
    Returns the tune_id on success, or None.
    """
    user_id = params["user_id"]
    suite_id = params["suite_id"]
    model_ids = params["models"]
    _raw_ts = params.get("target_set")  # serialized as list-of-lists
    target_set = {tuple(t) for t in _raw_ts} if _raw_ts else None
    search_space = params.get("search_space", {})
    per_model_search_spaces = params.get("per_model_search_spaces", {})

    logger.info(
        "Param tune started: job_id=%s user_id=%s models=%d",
        job_id, user_id, len(model_ids),
    )

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    cases = await db.get_test_cases(suite_id)
    tools = json.loads(suite["tools_json"])

    # Build targets first (may differ from model_ids if duplicates exist)
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    targets = _filter_targets(all_targets, model_ids, target_set)

    # Expand search spaces — use len(targets) not len(model_ids) for accurate count
    per_model_combos: dict[str, list[dict]] = {}
    if per_model_search_spaces and isinstance(per_model_search_spaces, dict):
        for mid, ss in per_model_search_spaces.items():
            if isinstance(ss, dict) and ss:
                per_model_combos[mid] = _expand_search_space(ss)
        combos = _expand_search_space(search_space) if search_space else [{}]
    else:
        combos = _expand_search_space(search_space)

    # Pre-validate and deduplicate combos per target.
    # validate_params() clamps/drops params, so many raw combos become identical
    # after validation.  We dedup here so total_combos matches reality.
    # Each entry stores (original_combo, resolved_combo, adjustments).
    validated_target_combos: dict[str, list[tuple[dict, dict, list[dict]]]] = {}
    for t in targets:
        tkey = _target_key(t)
        raw_combos = per_model_combos.get(t.model_id, combos)
        prov_key = identify_provider(t.model_id, getattr(t, "provider_key", None))
        seen: set[tuple] = set()
        unique: list[tuple[dict, dict, list[dict]]] = []
        for combo in raw_combos:
            # Build the same params_to_check that run_provider would
            temp = float(combo.get("temperature", 0.0))
            pp = {k: v for k, v in combo.items() if k not in ("temperature", "tool_choice", "max_tokens")}
            params_to_check = {"temperature": temp, **pp}
            validation = validate_params(prov_key, t.model_id, params_to_check)
            resolved = validation["resolved_params"]
            adjustments = validation.get("adjustments", [])
            # Dedup key: sorted tuple of resolved params + tool_choice (which isn't validated)
            tc = combo.get("tool_choice", "required")
            dedup_key = (tc,) + tuple(sorted(resolved.items()))
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique.append((combo, resolved, adjustments))
        validated_target_combos[tkey] = unique

    total_combos = sum(len(validated_target_combos.get(_target_key(t), [])) for t in targets)

    # Inject per-user API keys
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    # Pre-create the tune run in DB
    tune_id = await db.save_param_tune_run(
        user_id=user_id,
        suite_id=suite["id"],
        suite_name=suite["name"],
        models_json=json.dumps(model_ids),
        search_space_json=json.dumps(per_model_search_spaces if per_model_combos else search_space),
        total_combos=total_combos,
    )

    # Store result_ref early so the frontend can discover tune_id on reconnect
    await db.set_job_result_ref(job_id, tune_id)

    # Helper to send WebSocket messages directly to the user
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Notify frontend of tune start
    await _ws_send({
        "type": "tune_start",
        "job_id": job_id,
        "tune_id": tune_id,
        "total_combos": total_combos,
        "models": model_ids,
        "suite_name": suite["name"],
    })

    start_time = time.perf_counter()
    all_results = []
    completed = 0
    results_queue = asyncio.Queue()

    # Group targets by provider
    provider_groups: dict[str, list[Target]] = {}
    for target in targets:
        provider_groups.setdefault(target.provider, []).append(target)

    async def run_provider(prov_targets):
        """Run all combos for all models in this provider group."""
        for target in prov_targets:
            target_combos = validated_target_combos.get(_target_key(target), [])
            for combo_idx, (combo, resolved, combo_adjustments) in enumerate(target_combos):
                if cancel_event.is_set():
                    return

                # Extract combo params (use resolved values from pre-validation)
                temp = float(resolved.get("temperature", combo.get("temperature", 0.0)))
                tc = combo.get("tool_choice", "required")

                # Build provider_params from resolved (tier2 params, already validated)
                pp = {}
                for k, v in resolved.items():
                    if k not in ("temperature", "tool_choice", "max_tokens"):
                        pp[k] = v

                # Run all test cases for this combo
                case_results = []
                for case in cases:
                    if cancel_event.is_set():
                        return

                    _record_rate_limit(user_id)

                    # Check if multi-turn
                    mt_config = None
                    if case.get("multi_turn_config"):
                        try:
                            mt_config = json.loads(case["multi_turn_config"]) if isinstance(case["multi_turn_config"], str) else case["multi_turn_config"]
                        except (json.JSONDecodeError, TypeError):
                            logger.debug("Failed to parse multi_turn_config in param tuner")
                            mt_config = None

                    if mt_config and mt_config.get("multi_turn"):
                        case_with_mt = {**case, "_mt_config": mt_config}
                        r = await run_multi_turn_eval(target, tools, case_with_mt, temp, tc, provider_params=pp if pp else None)
                    else:
                        r = await run_single_eval(target, tools, case, temp, tc, provider_params=pp if pp else None)
                    case_results.append(r)

                # Compute aggregate scores for this combo
                tool_scores = [r["tool_selection_score"] for r in case_results if r.get("success")]
                param_scores = [r["param_accuracy"] for r in case_results if r.get("success") and r.get("param_accuracy") is not None]
                overall_scores = [r["overall_score"] for r in case_results if r.get("success")]
                latencies = [r["latency_ms"] for r in case_results if r.get("success") and r.get("latency_ms")]

                cases_passed = sum(1 for r in case_results if r.get("success") and r.get("overall_score", 0) == 1.0)

                # Trim case results for storage/WS (exclude raw_request/raw_response)
                trimmed_cases = []
                for cr in case_results:
                    trimmed_cases.append({
                        "test_case_id": cr.get("test_case_id"),
                        "prompt": cr.get("prompt", ""),
                        "expected_tool": cr.get("expected_tool"),
                        "actual_tool": cr.get("actual_tool"),
                        "expected_params": cr.get("expected_params"),
                        "actual_params": cr.get("actual_params"),
                        "tool_selection_score": cr.get("tool_selection_score", 0.0),
                        "param_accuracy": cr.get("param_accuracy"),
                        "overall_score": cr.get("overall_score", 0.0),
                        "success": cr.get("success", False),
                        "error": cr.get("error", ""),
                        "latency_ms": cr.get("latency_ms", 0),
                    })

                combo_result = {
                    "combo_index": combo_idx,
                    "model_id": target.model_id,
                    "provider_key": target.provider_key or "",
                    "model_name": target.display_name,
                    "config": combo,
                    "overall_score": round(sum(overall_scores) / len(overall_scores), 4) if overall_scores else 0.0,
                    "tool_accuracy": round(sum(tool_scores) / len(tool_scores) * 100, 2) if tool_scores else 0.0,
                    "param_accuracy": round(sum(param_scores) / len(param_scores) * 100, 2) if param_scores else 0.0,
                    "latency_avg_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
                    "cases_passed": cases_passed,
                    "cases_total": len(cases),
                    "adjustments": combo_adjustments,
                    "case_results": trimmed_cases,
                }

                await results_queue.put(combo_result)

    # Launch provider groups in parallel
    tasks = [asyncio.create_task(run_provider(g)) for g in provider_groups.values()]

    async def sentinel():
        await asyncio.gather(*tasks, return_exceptions=True)
        await results_queue.put(None)

    asyncio.create_task(sentinel())

    # Consume results and report progress via WebSocket
    while True:
        try:
            item = await asyncio.wait_for(results_queue.get(), timeout=15)
        except asyncio.TimeoutError:
            continue  # Keep waiting
        if item is None:
            break
        if cancel_event.is_set():
            for t in tasks:
                t.cancel()
            # Save partial results
            duration = time.perf_counter() - start_time
            await db.update_param_tune_run(
                tune_id, user_id,
                results_json=json.dumps(all_results),
                completed_combos=completed,
                status="cancelled",
                duration_s=round(duration, 2),
                best_config_json=json.dumps(_find_best_config(all_results)),
                best_score=_find_best_score(all_results),
            )
            return None

        completed += 1
        all_results.append(item)

        # Send combo result to frontend via WebSocket
        await _ws_send({
            "type": "combo_result",
            "job_id": job_id,
            "tune_id": tune_id,
            "data": item,
        })

        # Incrementally save results to DB so reconnecting clients can fetch them
        await db.update_param_tune_run(
            tune_id, user_id,
            results_json=json.dumps(all_results),
            completed_combos=completed,
        )

        # Update job progress
        pct = int((completed / total_combos) * 100) if total_combos > 0 else 0
        detail = f"{item['model_name']}, combo {completed}/{total_combos}"
        await progress_cb(pct, detail)

    # Tuning complete -- find best config
    duration = time.perf_counter() - start_time
    best_config = _find_best_config(all_results)
    best_score = _find_best_score(all_results)

    await db.update_param_tune_run(
        tune_id, user_id,
        results_json=json.dumps(all_results),
        best_config_json=json.dumps(best_config),
        best_score=best_score,
        completed_combos=completed,
        status="completed",
        duration_s=round(duration, 2),
    )

    # Send completion event to frontend
    await _ws_send({
        "type": "tune_complete",
        "job_id": job_id,
        "tune_id": tune_id,
        "best_config": best_config,
        "best_score": best_score,
        "duration_s": round(duration, 2),
    })

    logger.info(
        "Param tune completed: job_id=%s tune_id=%s user_id=%s combos=%d best_score=%.4f",
        job_id, tune_id, user_id, completed, best_score,
    )

    return tune_id


# Register the param tune handler with the job registry
job_registry.register_handler("param_tune", _param_tune_handler)


@app.post("/api/tool-eval/param-tune")
async def run_param_tune(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run a parameter tuning grid search via job registry. Returns job_id immediately.

    Progress is delivered via WebSocket (combo_result, tune_start, tune_complete events).
    """
    body = await request.json()
    suite_id = body.get("suite_id")
    model_ids, target_set = _parse_target_selection(body)
    search_space = body.get("search_space", {})
    per_model_search_spaces = body.get("per_model_search_spaces", {})

    # --- Validation ---
    if not suite_id:
        return JSONResponse({"error": "suite_id is required"}, status_code=400)
    if not isinstance(model_ids, list) or len(model_ids) == 0:
        return JSONResponse({"error": "models must be a non-empty list"}, status_code=400)

    # Validate search spaces
    if per_model_search_spaces and isinstance(per_model_search_spaces, dict):
        has_valid = any(isinstance(ss, dict) and ss for ss in per_model_search_spaces.values())
        if not has_valid:
            return JSONResponse({"error": "per_model_search_spaces produced no combinations"}, status_code=400)
    elif not isinstance(search_space, dict) or not search_space:
        return JSONResponse({"error": "search_space must be a non-empty dict"}, status_code=400)
    else:
        combos = _expand_search_space(search_space)
        if len(combos) == 0:
            return JSONResponse({"error": "search_space produced no combinations"}, status_code=400)

    # Load suite + test cases (validate before submitting job)
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(suite_id)
    if not cases:
        return JSONResponse({"error": "Suite has no test cases"}, status_code=400)

    # Rate limit check
    allowed, remaining = _check_rate_limit(user["id"])
    if not allowed:
        return JSONResponse(
            {"error": f"Rate limit exceeded. Max {RATE_LIMIT_PER_HOUR} per hour."},
            status_code=429,
        )
    _record_rate_limit(user["id"])

    # Build targets (validate models exist in config)
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)
    targets = _filter_targets(all_targets, model_ids, target_set)
    if not targets:
        return JSONResponse({"error": "No matching models found in config"}, status_code=400)

    # Build progress detail
    model_count = len(targets)
    progress_detail = f"Param Tune: {model_count} model{'s' if model_count != 1 else ''}, {suite['name']}"

    # Submit to job registry
    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "suite_id": suite_id,
        "models": model_ids,
        "target_set": [list(t) for t in target_set] if target_set else None,
        "search_space": search_space,
        "per_model_search_spaces": per_model_search_spaces,
    }

    job_id = await job_registry.submit(
        job_type="param_tune",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


def _find_best_config(results: list[dict]) -> dict | None:
    """Find the config with the highest overall_score."""
    if not results:
        return None
    best = max(results, key=lambda r: r.get("overall_score", 0))
    return best.get("config")


def _find_best_score(results: list[dict]) -> float:
    """Find the highest overall_score."""
    if not results:
        return 0.0
    return max(r.get("overall_score", 0) for r in results)


@app.post("/api/tool-eval/param-tune/cancel")
async def cancel_param_tune(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running param tune via job registry."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = body.get("job_id")

    if job_id:
        # Cancel via job registry (preferred)
        cancelled = await job_registry.cancel(job_id, user["id"])
        if cancelled:
            return {"status": "ok", "message": "Cancellation requested"}
        return JSONResponse({"error": "Job not found or already finished"}, status_code=404)

    # Fallback: cancel via legacy user-level event (backward compat)
    _get_user_cancel(user["id"]).set()
    return {"status": "ok", "message": "Cancellation requested"}


@app.get("/api/tool-eval/param-tune/history")
async def get_param_tune_history(user: dict = Depends(auth.get_current_user)):
    """List user's param tune runs."""
    runs = await db.get_param_tune_runs(user["id"])
    return {"runs": runs}


@app.get("/api/tool-eval/param-tune/history/{tune_id}")
async def get_param_tune_detail(tune_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full param tune run details including all results."""
    run = await db.get_param_tune_run(tune_id, user["id"])
    if not run:
        return JSONResponse({"error": "Tune run not found"}, status_code=404)
    return run


@app.delete("/api/tool-eval/param-tune/history/{tune_id}")
async def delete_param_tune(tune_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a param tune run."""
    deleted = await db.delete_param_tune_run(tune_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Tune run not found"}, status_code=404)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Prompt Tuner (AI-Generated Prompt Optimization)
# ---------------------------------------------------------------------------

_QUICK_META_PROMPT = """You are a prompt engineering expert. Your job is to create {n} distinct system prompt variations for an LLM that will use tool calling.

CONTEXT:
- The LLM will use these tools: {tools_summary}
- Test scenarios it must handle: {test_cases_summary}
- Base prompt to improve upon: "{base_prompt}"

Generate {n} variations, each with a DIFFERENT approach. Vary structure, tone, and emphasis -- not just word choice. Each prompt must instruct the model to use the provided tools. Keep prompts under 500 tokens each.

Suggested styles: concise & direct, detailed & explicit, structured (numbered rules), conversational, technical/specification-like.

Return a JSON object with a "prompts" key containing an array:
{{"prompts": [{{"style": "concise", "prompt": "..."}}, ...]}}
Do not include any text before or after the JSON object."""

_EVO_META_PROMPT = """You are a prompt evolution expert. You are mutating winning system prompts to create the next generation.

PARENT PROMPTS (these scored highest in the previous generation):
{parent_prompts}

CONTEXT:
- Tools available: {tools_summary}
- Test scenarios: {test_cases_summary}

TASK:
Create {n} new variations by mutating the parent prompts. For each:
- Keep the core intent that made the parent successful
- Change structure, phrasing, emphasis, or add/remove instructions
- Try at least one bold mutation (significantly different approach)
- Try at least one conservative mutation (small refinement of best parent)

Return a JSON object with a "prompts" key containing an array:
{{"prompts": [{{"parent_index": 0, "mutation_type": "bold", "prompt": "..."}}, ...]}}
Do not include any text before or after the JSON object."""

_DEFAULT_BASE_PROMPT = "You are a helpful assistant that uses tools to answer questions. When the user asks you to perform an action, use the appropriate tool."


def _parse_meta_response(text: str) -> list[dict]:
    """Parse JSON from meta-model response.

    Handles (in order):
    1. {"prompts": [...]} wrapper (JSON object mode)
    2. Bare JSON array
    3. Markdown code block stripping
    4. Regex fallback for embedded arrays
    """
    text = text.strip()

    # Helper: unwrap {"prompts": [...]} or return list directly
    def _unwrap(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("prompts"), list):
            return data["prompts"]
        return None

    # Try direct JSON parse
    try:
        result = _unwrap(json.loads(text))
        if result is not None:
            return result
    except json.JSONDecodeError:
        pass
    # Strip markdown code blocks and try again
    stripped = re.sub(r'```(?:json)?\s*', '', text).strip()
    try:
        result = _unwrap(json.loads(stripped))
        if result is not None:
            return result
    except json.JSONDecodeError:
        pass
    # Regex fallback: find JSON object or array in response
    for pattern in (r'\{[\s\S]*\}', r'\[[\s\S]*\]'):
        match = re.search(pattern, stripped)
        if match:
            try:
                result = _unwrap(json.loads(match.group()))
                if result is not None:
                    return result
            except json.JSONDecodeError:
                pass
    logger.debug("_parse_meta_response: all parse strategies failed, returning empty list")
    return []


_META_RETRYABLE_ERRORS = (
    litellm.exceptions.BadGatewayError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.Timeout,
)


async def _generate_prompts_meta(
    meta_target: Target,
    prompt_template: str,
    *,
    _max_retries: int = 3,
    _base_delay: float = 2.0,
    **format_kwargs,
) -> list[dict]:
    """Call meta-model to generate prompt variations. Returns parsed list.

    Retries transient errors (502/503/500/connection/timeout) with exponential
    backoff. Retries once on empty/unparseable response.
    """
    formatted = prompt_template.format(**format_kwargs)

    kwargs = {
        "model": meta_target.model_id,
        "messages": [{"role": "user", "content": formatted}],
        "timeout": 120,
        "num_retries": 0,  # We handle retries ourselves with backoff
        "response_format": {"type": "json_object"},
    }
    if meta_target.api_base:
        kwargs["api_base"] = meta_target.api_base
    if meta_target.api_key:
        kwargs["api_key"] = meta_target.api_key
    # Use build_litellm_kwargs for provider-aware param handling
    # (e.g. O-series models skip temperature, skip_params honoured)
    kwargs.update(build_litellm_kwargs(
        meta_target, temperature=0.9, max_tokens=4096,
    ))

    last_exc: Exception | None = None
    _json_mode_supported = True  # Assume supported; disable on first BadRequestError
    for attempt in range(1, _max_retries + 1):
        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content or ""
            prompts = _parse_meta_response(content)
            if prompts:
                return prompts
            # Empty response — retry once with backoff
            logger.warning(
                "Meta model returned no parseable prompts (attempt %d/%d, content_len=%d)",
                attempt, _max_retries, len(content),
            )
            if attempt < _max_retries:
                await asyncio.sleep(_base_delay * (2 ** (attempt - 1)))
                continue
            return []  # All retries exhausted with empty results
        except litellm.exceptions.BadRequestError as exc:
            # Some models don't support response_format — retry without it
            if _json_mode_supported and "response_format" in kwargs:
                logger.info(
                    "Meta model rejected response_format (attempt %d/%d): %s — retrying without JSON mode",
                    attempt, _max_retries, exc,
                )
                _json_mode_supported = False
                kwargs.pop("response_format", None)
                continue  # Don't count this as a retry attempt
            raise
        except _META_RETRYABLE_ERRORS as exc:
            last_exc = exc
            # Some models (e.g. LM Studio) crash on response_format instead
            # of returning a clean 400 — strip it on first transient failure
            if _json_mode_supported and "response_format" in kwargs:
                logger.info(
                    "Meta model transient error with JSON mode (attempt %d/%d): %s — disabling response_format and retrying",
                    attempt, _max_retries, exc,
                )
                _json_mode_supported = False
                kwargs.pop("response_format", None)
                await asyncio.sleep(_base_delay)
                continue  # Don't count this as a retry attempt
            if attempt < _max_retries:
                delay = _base_delay * (2 ** (attempt - 1))
                logger.info(
                    "Meta model transient error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt, _max_retries, exc, delay,
                )
                await asyncio.sleep(delay)
                continue
            raise  # Last attempt — propagate

    # Should not reach here, but safety net
    if last_exc:
        raise last_exc
    return []


def _build_tools_summary(tools: list[dict]) -> str:
    """Build a concise tools summary for meta-prompts."""
    parts = []
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "unknown")
        desc = fn.get("description", "")[:100]
        params = list(fn.get("parameters", {}).get("properties", {}).keys())
        parts.append(f"- {name}: {desc} (params: {', '.join(params)})")
    return "\n".join(parts)


def _build_test_cases_summary(cases: list) -> str:
    """Build a concise test cases summary for meta-prompts."""
    parts = []
    for c in cases[:10]:  # Limit to 10 for prompt size
        prompt = (c.get("prompt") or "")[:120]
        expected = c.get("expected_tool", "?")
        parts.append(f"- \"{prompt}\" -> expects tool: {expected}")
    return "\n".join(parts)


async def _prompt_tune_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for prompt tuning (Quick or Evolutionary).

    Extracts core logic from the old SSE generator.
    Returns the tune_id on success, or None on cancel.
    """
    user_id = params["user_id"]
    suite_id = params["suite_id"]
    target_model_ids = params["target_models"]
    _raw_ts = params.get("target_set")
    target_set_eval = {tuple(t) for t in _raw_ts} if _raw_ts else None
    meta_model_id = params["meta_model"]
    mode = params.get("mode", "quick")
    base_prompt = params.get("base_prompt") or _DEFAULT_BASE_PROMPT
    cfg = params.get("config", {})

    population_size = int(cfg.get("population_size", 5))
    generations = int(cfg.get("generations", 1 if mode == "quick" else 3))
    selection_ratio = float(cfg.get("selection_ratio", 0.4))
    eval_temperature = float(cfg.get("temperature", 0.0))
    eval_tool_choice = cfg.get("tool_choice", "required")

    if mode == "quick":
        generations = 1

    population_size = max(3, min(population_size, 20))
    generations = max(1, min(generations, 10))
    selection_ratio = max(0.2, min(selection_ratio, 0.8))
    total_prompts = population_size * generations

    logger.info(
        "Prompt tune started: job_id=%s user_id=%s mode=%s total_prompts=%d",
        job_id, user_id, mode, total_prompts,
    )

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    cases = await db.get_test_cases(suite_id)
    tools = json.loads(suite["tools_json"])

    # Build targets
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)

    # Find meta-model target
    meta_targets = [t for t in all_targets if t.model_id == meta_model_id]
    eval_targets = _filter_targets(all_targets, target_model_ids, target_set_eval)

    # Inject user keys
    user_keys_cache = {}
    for t in meta_targets + eval_targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    meta_targets = inject_user_keys(meta_targets, user_keys_cache)
    eval_targets = inject_user_keys(eval_targets, user_keys_cache)

    meta_target = meta_targets[0]

    tools_summary = _build_tools_summary(tools)
    test_cases_summary = _build_test_cases_summary(cases)
    total_eval_calls = total_prompts * len(cases) * len(eval_targets)

    # Pre-create DB record
    tune_id = await db.save_prompt_tune_run(
        user_id=user_id,
        suite_id=suite["id"],
        suite_name=suite["name"],
        mode=mode,
        target_models_json=json.dumps(target_model_ids),
        meta_model=meta_model_id,
        base_prompt=base_prompt,
        config_json=json.dumps(cfg),
        total_prompts=total_prompts,
    )

    # Store result_ref early so the frontend can discover tune_id on reconnect
    await db.set_job_result_ref(job_id, tune_id)

    # Helper to send WebSocket messages directly to the user
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Notify frontend of tune start
    await _ws_send({
        "type": "tune_start",
        "job_id": job_id,
        "tune_id": tune_id,
        "mode": mode,
        "total_prompts": total_prompts,
        "total_eval_calls": total_eval_calls,
        "suite_name": suite["name"],
    })

    start_time = time.perf_counter()
    all_generations = []
    completed_prompts = 0
    best_prompt = None
    best_score = 0.0
    survivors = []  # For evolutionary mode

    for gen_num in range(1, generations + 1):
        if cancel_event.is_set():
            break

        await _ws_send({
            "type": "generation_start",
            "job_id": job_id,
            "tune_id": tune_id,
            "generation": gen_num,
            "total_generations": generations,
            "population_size": population_size,
        })

        # --- Generate prompts ---
        if gen_num == 1 or mode == "quick":
            raw_prompts = await _generate_prompts_meta(
                meta_target, _QUICK_META_PROMPT,
                n=population_size,
                tools_summary=tools_summary,
                test_cases_summary=test_cases_summary,
                base_prompt=base_prompt,
            )
        else:
            parent_text = "\n".join(
                f"#{i+1} (score={s['avg_score']:.2f}): \"{s['text'][:200]}...\""
                for i, s in enumerate(survivors)
            )
            raw_prompts = await _generate_prompts_meta(
                meta_target, _EVO_META_PROMPT,
                n=population_size,
                tools_summary=tools_summary,
                test_cases_summary=test_cases_summary,
                parent_prompts=parent_text,
            )

        # Handle empty meta response
        if not raw_prompts:
            logger.warning(
                "Meta model returned 0 prompts for generation %d — skipping",
                gen_num,
            )
            await _ws_send({
                "type": "generation_error",
                "job_id": job_id,
                "tune_id": tune_id,
                "generation": gen_num,
                "message": "Meta model returned no prompts. Skipping generation.",
            })
            continue

        # Normalize to list of prompt dicts
        gen_prompts = []
        for idx, rp in enumerate(raw_prompts[:population_size]):
            text = rp.get("prompt", "") if isinstance(rp, dict) else str(rp)
            style = rp.get("style", rp.get("mutation_type", "variation")) if isinstance(rp, dict) else "variation"
            parent_idx = rp.get("parent_index") if isinstance(rp, dict) else None
            gen_prompts.append({
                "index": idx,
                "style": style,
                "text": text,
                "parent_index": parent_idx,
                "mutation_type": rp.get("mutation_type") if isinstance(rp, dict) else None,
                "scores": {},
                "avg_score": 0.0,
                "survived": False,
            })

            await _ws_send({
                "type": "prompt_generated",
                "job_id": job_id,
                "tune_id": tune_id,
                "generation": gen_num,
                "prompt_index": idx,
                "prompt_text": text[:300],
                "style": style,
                "parent_prompt": parent_idx,
            })

        # --- Evaluate each prompt ---
        for p_info in gen_prompts:
            if cancel_event.is_set():
                break

            model_scores = {}
            for target in eval_targets:
                if cancel_event.is_set():
                    break

                await _ws_send({
                    "type": "prompt_eval_start",
                    "job_id": job_id,
                    "tune_id": tune_id,
                    "generation": gen_num,
                    "prompt_index": p_info["index"],
                    "model": target.display_name,
                })

                # Run all test cases with this prompt as system_prompt
                case_results = []
                for case in cases:
                    if cancel_event.is_set():
                        break
                    _record_rate_limit(user_id)

                    # Dispatch: multi-turn or single-turn
                    mt_config = None
                    if case.get("multi_turn_config"):
                        try:
                            mt_config = json.loads(case["multi_turn_config"]) if isinstance(case["multi_turn_config"], str) else case["multi_turn_config"]
                        except (json.JSONDecodeError, TypeError):
                            mt_config = None

                    if mt_config and mt_config.get("multi_turn"):
                        case_with_mt = {**case, "_mt_config": mt_config}
                        r = await run_multi_turn_eval(
                            target, tools, case_with_mt, eval_temperature,
                            eval_tool_choice, system_prompt=p_info["text"],
                        )
                    else:
                        r = await run_single_eval(
                            target, tools, case, eval_temperature,
                            eval_tool_choice, system_prompt=p_info["text"],
                        )
                    case_results.append(r)

                # Compute scores
                overall_scores = [r["overall_score"] for r in case_results if r.get("success")]
                tool_scores = [r["tool_selection_score"] for r in case_results if r.get("success")]
                param_scores = [r["param_accuracy"] for r in case_results if r.get("success") and r.get("param_accuracy") is not None]

                avg_overall = sum(overall_scores) / len(overall_scores) if overall_scores else 0.0
                model_scores[target.model_id] = {
                    "overall": round(avg_overall, 4),
                    "tool_acc": round(sum(tool_scores) / len(tool_scores) * 100, 2) if tool_scores else 0.0,
                    "param_acc": round(sum(param_scores) / len(param_scores) * 100, 2) if param_scores else 0.0,
                }

                await _ws_send({
                    "type": "prompt_eval_result",
                    "job_id": job_id,
                    "tune_id": tune_id,
                    "generation": gen_num,
                    "prompt_index": p_info["index"],
                    "model_id": target.model_id,
                    "overall_score": model_scores[target.model_id]["overall"],
                    "tool_accuracy": model_scores[target.model_id]["tool_acc"],
                    "param_accuracy": model_scores[target.model_id]["param_acc"],
                })

            p_info["scores"] = model_scores
            all_model_scores = [s["overall"] for s in model_scores.values()]
            p_info["avg_score"] = round(sum(all_model_scores) / len(all_model_scores), 4) if all_model_scores else 0.0

            completed_prompts += 1

            # Track global best
            if p_info["avg_score"] > best_score:
                best_score = p_info["avg_score"]
                best_prompt = p_info["text"]

            # Update job progress
            pct = int((completed_prompts / total_prompts) * 100) if total_prompts > 0 else 0
            detail = f"Gen {gen_num}/{generations}, prompt {completed_prompts}/{total_prompts}"
            await progress_cb(pct, detail)

        # --- Selection (Evolutionary mode) ---
        gen_prompts.sort(key=lambda p: p["avg_score"], reverse=True)
        n_survivors = max(1, int(len(gen_prompts) * selection_ratio))
        for i, p in enumerate(gen_prompts):
            p["survived"] = i < n_survivors

        survivors = [p for p in gen_prompts if p["survived"]]

        gen_best = gen_prompts[0] if gen_prompts else None
        all_generations.append({
            "generation": gen_num,
            "prompts": gen_prompts,
            "best_index": gen_best["index"] if gen_best else None,
            "best_score": gen_best["avg_score"] if gen_best else 0.0,
        })

        await _ws_send({
            "type": "generation_complete",
            "job_id": job_id,
            "tune_id": tune_id,
            "generation": gen_num,
            "best_score": gen_best["avg_score"] if gen_best else 0.0,
            "best_prompt_index": gen_best["index"] if gen_best else None,
            "survivors": [p["index"] for p in survivors],
        })

        # Incrementally save results to DB
        await db.update_prompt_tune_run(
            tune_id, user_id,
            generations_json=json.dumps(all_generations),
            best_prompt=best_prompt,
            best_score=best_score,
            completed_prompts=completed_prompts,
        )

    # --- Tuning complete ---
    duration = time.perf_counter() - start_time

    if cancel_event.is_set():
        await db.update_prompt_tune_run(
            tune_id, user_id,
            generations_json=json.dumps(all_generations),
            best_prompt=best_prompt,
            best_score=best_score,
            completed_prompts=completed_prompts,
            status="cancelled",
            duration_s=round(duration, 2),
        )
        return None

    await db.update_prompt_tune_run(
        tune_id, user_id,
        generations_json=json.dumps(all_generations),
        best_prompt=best_prompt,
        best_score=best_score,
        completed_prompts=completed_prompts,
        status="completed",
        duration_s=round(duration, 2),
    )

    # Send completion event to frontend
    await _ws_send({
        "type": "tune_complete",
        "job_id": job_id,
        "tune_id": tune_id,
        "best_prompt": best_prompt,
        "best_score": best_score,
        "duration_s": round(duration, 2),
    })

    logger.info(
        "Prompt tune completed: job_id=%s tune_id=%s user_id=%s prompts=%d best_score=%.4f",
        job_id, tune_id, user_id, completed_prompts, best_score,
    )

    return tune_id


# Register the prompt tune handler with the job registry
job_registry.register_handler("prompt_tune", _prompt_tune_handler)


@app.post("/api/tool-eval/prompt-tune")
async def run_prompt_tune(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run a prompt tuning session (Quick or Evolutionary mode) via job registry.

    Progress is delivered via WebSocket (tune_start, prompt_eval_result, etc.).
    Returns job_id immediately.
    """
    body = await request.json()
    suite_id = body.get("suite_id")
    # Support precise target selection for eval targets
    _target_models_body = {"targets": body.get("target_targets"), "models": body.get("target_models", [])}
    target_model_ids, target_set_eval = _parse_target_selection(_target_models_body)
    meta_model_id = body.get("meta_model", "")
    mode = body.get("mode", "quick")
    base_prompt = body.get("base_prompt") or _DEFAULT_BASE_PROMPT
    cfg = body.get("config", {})

    # --- Validation ---
    if not suite_id:
        return JSONResponse({"error": "suite_id is required"}, status_code=400)
    if not isinstance(target_model_ids, list) or len(target_model_ids) == 0:
        return JSONResponse({"error": "target_models must be a non-empty list"}, status_code=400)
    if not meta_model_id:
        return JSONResponse({"error": "meta_model is required"}, status_code=400)
    if mode not in ("quick", "evolutionary"):
        return JSONResponse({"error": "mode must be 'quick' or 'evolutionary'"}, status_code=400)

    # Load suite + test cases (validate before submitting job)
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(suite_id)
    if not cases:
        return JSONResponse({"error": "Suite has no test cases"}, status_code=400)

    # Rate limit check
    allowed, remaining = _check_rate_limit(user["id"])
    if not allowed:
        return JSONResponse(
            {"error": f"Rate limit exceeded. Max {RATE_LIMIT_PER_HOUR} per hour."},
            status_code=429,
        )
    _record_rate_limit(user["id"])

    # Build targets (validate models exist in config)
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)

    # Find meta-model target
    meta_targets = [t for t in all_targets if t.model_id == meta_model_id]
    if not meta_targets:
        return JSONResponse({"error": f"Meta model '{meta_model_id}' not found in config"}, status_code=400)

    # Find eval targets
    eval_targets = _filter_targets(all_targets, target_model_ids, target_set_eval)
    if not eval_targets:
        return JSONResponse({"error": "No matching target models found in config"}, status_code=400)

    # Build progress detail
    model_count = len(eval_targets)
    progress_detail = f"Prompt Tune: {mode}, {model_count} model{'s' if model_count != 1 else ''}, {suite['name']}"

    # Submit to job registry
    job_params = {
        "user_id": user["id"],
        "suite_id": suite_id,
        "target_models": target_model_ids,
        "target_set": [list(t) for t in target_set_eval] if target_set_eval else None,
        "meta_model": meta_model_id,
        "mode": mode,
        "base_prompt": base_prompt,
        "config": cfg,
    }

    job_id = await job_registry.submit(
        job_type="prompt_tune",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@app.post("/api/tool-eval/prompt-tune/cancel")
async def cancel_prompt_tune(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running prompt tune via job registry."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = body.get("job_id")

    if job_id:
        cancelled = await job_registry.cancel(job_id, user["id"])
        if cancelled:
            return {"status": "ok", "message": "Cancellation requested"}
        return JSONResponse({"error": "Job not found or already finished"}, status_code=404)

    return JSONResponse({"error": "job_id is required"}, status_code=400)


@app.get("/api/tool-eval/prompt-tune/estimate")
async def estimate_prompt_tune(
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Get cost/time estimate before running prompt tuning."""
    suite_id = request.query_params.get("suite_id", "")
    mode = request.query_params.get("mode", "quick")
    population_size = int(request.query_params.get("population_size", "5"))
    generations = int(request.query_params.get("generations", "1" if mode == "quick" else "3"))
    num_models = int(request.query_params.get("num_models", "1"))

    if mode == "quick":
        generations = 1

    total_prompts = population_size * generations

    # Count test cases
    num_cases = 0
    if suite_id:
        cases = await db.get_test_cases(suite_id)
        num_cases = len(cases) if cases else 0

    total_eval_calls = total_prompts * num_cases * num_models
    total_meta_calls = generations  # One meta-call per generation
    total_api_calls = total_meta_calls + total_eval_calls

    # Rough estimate: ~2s per eval call, ~5s per meta call
    estimated_s = total_meta_calls * 5 + total_eval_calls * 2

    warning = None
    if total_api_calls > 100:
        warning = f"This will make {total_api_calls} API calls. Consider reducing population or generations."

    return {
        "total_prompt_generations": total_prompts,
        "total_eval_calls": total_eval_calls,
        "total_api_calls": total_api_calls,
        "estimated_duration_s": estimated_s,
        "warning": warning,
    }


@app.get("/api/tool-eval/prompt-tune/history")
async def get_prompt_tune_history(user: dict = Depends(auth.get_current_user)):
    """List user's prompt tune runs."""
    runs = await db.get_prompt_tune_runs(user["id"])
    return {"runs": runs}


@app.get("/api/tool-eval/prompt-tune/history/{tune_id}")
async def get_prompt_tune_detail(tune_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full prompt tune run details."""
    run = await db.get_prompt_tune_run(tune_id, user["id"])
    if not run:
        return JSONResponse({"error": "Tune run not found"}, status_code=404)
    return run


@app.delete("/api/tool-eval/prompt-tune/history/{tune_id}")
async def delete_prompt_tune(tune_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a prompt tune run."""
    deleted = await db.delete_prompt_tune_run(tune_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Tune run not found"}, status_code=404)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# LLM Judge (AI-Powered Eval Quality Assessment)
# ---------------------------------------------------------------------------

_JUDGE_VERDICT_PROMPT = """You are an expert evaluator of LLM tool calling quality. You are judging how well a model performed on a tool calling task.
{custom_instructions}
TOOL DEFINITIONS:
{tool_definitions}

TEST CASE:
- User prompt: "{test_prompt}"
- Expected tool: {expected_tool}
- Expected parameters: {expected_params}

MODEL RESPONSE:
- Tool called: {actual_tool}
- Parameters used: {actual_params}
- Automated score: {overall_score}

EVALUATE:
1. Tool Selection: Was the right tool chosen? If different from expected, was it still reasonable?
2. Parameter Accuracy: Were parameters correct? Close but not exact? Missing important ones?
3. Reasoning Quality: Does the tool call show understanding of the user's intent?
4. Edge Cases: Did the model handle ambiguity well?

Return ONLY valid JSON (no text before/after):
{{"quality_score": 1, "verdict": "pass", "summary": "One-line summary max 100 chars", "reasoning": "Detailed 2-3 sentence explanation", "tool_selection_assessment": "correct", "param_assessment": "exact"}}

quality_score: integer 1-5
verdict: "pass" or "marginal" or "fail"
tool_selection_assessment: "correct" or "acceptable_alternative" or "wrong"
param_assessment: "exact" or "close" or "partial" or "wrong"
"""

_JUDGE_COMPARE_PROMPT = """You are an expert judge comparing two LLMs' tool calling performance.

TOOL DEFINITIONS:
{tool_definitions}

TEST CASE {case_num}/{total_cases}:
- User prompt: "{test_prompt}"
- Expected: {expected_tool}({expected_params})

Model A ({model_a_name}):
- Called: {a_tool}({a_params})
- Automated score: {a_score}

Model B ({model_b_name}):
- Called: {b_tool}({b_params})
- Automated score: {b_score}

Return ONLY valid JSON (no text before/after):
{{"winner": "model_a", "confidence": 0.85, "reasoning": "Why this model won on this case"}}

winner: "model_a" or "model_b" or "tie"
confidence: float 0.0-1.0"""

_JUDGE_CROSSCASE_PROMPT = """You have evaluated {n} test cases for model {model_name}. Here are the per-case verdicts:

{verdicts_summary}

Provide a cross-case analysis:
1. What patterns of strength/weakness do you see?
2. What types of tool calls does this model handle well/poorly?
3. Overall grade (A/B/C/D/F with +/-) and what it means
4. Specific recommendations for improvement

Return ONLY valid JSON (no text before/after):
{{"overall_grade": "B+", "overall_score": 82, "strengths": ["strength1", "strength2"], "weaknesses": ["weakness1"], "cross_case_analysis": "Paragraph of analysis", "recommendations": ["recommendation1"]}}

overall_score: integer 0-100
overall_grade: letter grade with optional +/-"""

_JUDGE_COMPARE_SUMMARY_PROMPT = """You compared Model A ({model_a_name}) and Model B ({model_b_name}) across {n} test cases.

Per-case results:
{case_results}

Provide an overall comparison summary.

Return ONLY valid JSON (no text before/after):
{{"overall_winner": "model_a", "score_a": 78, "score_b": 65, "summary": "2-3 sentence summary of the comparison", "tie_cases": 1}}

overall_winner: "model_a" or "model_b" or "tie"
score_a, score_b: integer 0-100"""


def _parse_judge_json(text: str) -> dict:
    """Parse a JSON object from judge model response. Returns dict or empty dict."""
    text = text.strip()
    # Direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # Strip markdown code blocks
    stripped = re.sub(r'```(?:json)?\s*', '', text).strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # Regex fallback: find JSON object
    match = re.search(r'\{[\s\S]*\}', stripped)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    logger.debug("_parse_judge_json: all parse strategies failed, returning empty dict")
    return {}


_JUDGE_RETRYABLE_ERRORS = (
    litellm.exceptions.BadGatewayError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.Timeout,
)

async def _call_judge_model(
    judge_target: Target,
    prompt: str,
    *,
    _max_retries: int = 3,
    _base_delay: float = 2.0,
) -> dict:
    """Call the judge model with a prompt, return parsed JSON dict.

    Retries transient errors (502/503/500/connection/timeout) with exponential
    backoff.  Non-transient errors (auth, 400, 404, rate-limit) propagate
    immediately.
    """
    kwargs = {
        "model": judge_target.model_id,
        "messages": [{"role": "user", "content": prompt}],
        "timeout": 120,
        "num_retries": 0,  # We handle retries ourselves with backoff
    }
    if judge_target.api_base:
        kwargs["api_base"] = judge_target.api_base
    if judge_target.api_key:
        kwargs["api_key"] = judge_target.api_key
    # Use build_litellm_kwargs for provider-aware param handling (skip_params, clamping)
    extra = build_litellm_kwargs(
        judge_target, temperature=0.0, max_tokens=2048,
    )
    if extra:
        kwargs.update(extra)
    else:
        # Fallback: no provider_params resolved — apply judge defaults directly
        if "temperature" not in (judge_target.skip_params or []):
            kwargs["temperature"] = 0.0  # AD-6: reproducible judge assessments
        kwargs["max_tokens"] = 2048

    logger.debug("Judge call: model=%s api_base=%s prompt_len=%d", judge_target.model_id, judge_target.api_base, len(prompt))

    last_exc: Exception | None = None
    for attempt in range(1, _max_retries + 1):
        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content or ""
            return _parse_judge_json(content)
        except _JUDGE_RETRYABLE_ERRORS as exc:
            last_exc = exc
            if attempt < _max_retries:
                delay = _base_delay * (2 ** (attempt - 1))  # 2s, 4s, 8s
                logger.info(
                    "Judge call transient error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt, _max_retries, exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "Judge call failed after %d attempts: %s",
                    _max_retries, exc,
                )
    raise last_exc  # type: ignore[misc]


def _build_tool_definitions_text(tools: list[dict]) -> str:
    """Build tool definitions text for judge prompts."""
    parts = []
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "unknown")
        desc = fn.get("description", "")
        params = fn.get("parameters", {}).get("properties", {})
        param_strs = []
        for pname, pspec in params.items():
            ptype = pspec.get("type", "any")
            pdesc = pspec.get("description", "")[:60]
            param_strs.append(f"    {pname} ({ptype}): {pdesc}")
        parts.append(f"- {name}: {desc}\n  Parameters:\n" + "\n".join(param_strs))
    return "\n".join(parts)


async def _judge_single_verdict(
    judge_target: Target,
    tool_defs_text: str,
    test_case: dict,
    result: dict,
    custom_instructions: str = "",
) -> dict:
    """Judge a single test case result. Returns verdict dict."""
    ci_block = f"\nADDITIONAL EVALUATION INSTRUCTIONS:\n{custom_instructions}\n" if custom_instructions.strip() else ""
    prompt = _JUDGE_VERDICT_PROMPT.format(
        tool_definitions=tool_defs_text,
        test_prompt=result.get("prompt", test_case.get("prompt", "")),
        expected_tool=result.get("expected_tool", "?"),
        expected_params=json.dumps(result.get("expected_params", {})),
        actual_tool=result.get("actual_tool", "none"),
        actual_params=json.dumps(result.get("actual_params", {})),
        overall_score=result.get("overall_score", 0),
        custom_instructions=ci_block,
    )
    verdict = await _call_judge_model(judge_target, prompt)
    if not verdict:
        return {
            "quality_score": 0,
            "verdict": "error",
            "summary": "Judge model returned invalid response",
            "reasoning": "Could not parse judge response",
            "tool_selection_assessment": "unknown",
            "param_assessment": "unknown",
        }
    # Ensure required keys
    verdict.setdefault("quality_score", 0)
    verdict.setdefault("verdict", "fail")
    verdict.setdefault("summary", "")
    verdict.setdefault("reasoning", "")
    verdict.setdefault("tool_selection_assessment", "unknown")
    verdict.setdefault("param_assessment", "unknown")
    return verdict


async def _judge_crosscase(
    judge_target: Target,
    model_name: str,
    verdicts: list[dict],
) -> dict:
    """Generate cross-case analysis report from verdicts."""
    summary_parts = []
    for v in verdicts:
        tc_id = v.get("test_case_id", "?")
        verdict = v.get("verdict", "?")
        score = v.get("quality_score", 0)
        summary = v.get("summary", "")
        summary_parts.append(f"- Case {tc_id}: {verdict} (score {score}/5) - {summary}")

    prompt = _JUDGE_CROSSCASE_PROMPT.format(
        n=len(verdicts),
        model_name=model_name,
        verdicts_summary="\n".join(summary_parts),
    )
    report = await _call_judge_model(judge_target, prompt)
    if not report:
        return {
            "overall_grade": "?",
            "overall_score": 0,
            "strengths": [],
            "weaknesses": [],
            "cross_case_analysis": "Judge model could not generate analysis",
            "recommendations": [],
        }
    report.setdefault("overall_grade", "?")
    report.setdefault("overall_score", 0)
    report.setdefault("strengths", [])
    report.setdefault("weaknesses", [])
    report.setdefault("cross_case_analysis", "")
    report.setdefault("recommendations", [])
    return report


# --- Judge: Job Registry Handlers ---


async def _judge_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for post-eval judge execution.

    Runs judge verdicts with configurable concurrency via asyncio.Semaphore.
    Returns the judge_report_id on success, or None.
    """
    user_id = params["user_id"]
    eval_run_id = params["eval_run_id"]
    judge_model_id = params["judge_model"]
    custom_instructions = params.get("custom_instructions", "")
    concurrency = int(params.get("concurrency", 4))

    logger.info(
        "Judge started: job_id=%s user_id=%s eval_run_id=%s concurrency=%d",
        job_id, user_id, eval_run_id, concurrency,
    )

    # Load eval run
    eval_run = await db.get_tool_eval_run(eval_run_id, user_id)
    results = json.loads(eval_run.get("results_json", "[]"))

    # Load suite for tool definitions
    suite = await db.get_tool_suite(eval_run["suite_id"], user_id)
    tools = json.loads(suite["tools_json"]) if suite else []
    tool_defs_text = _build_tool_definitions_text(tools)

    # Build judge target
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    judge_targets = [t for t in all_targets if t.model_id == judge_model_id]
    if not judge_targets:
        return None
    judge_target = judge_targets[0]

    # Inject user API key
    if judge_target.provider_key:
        encrypted = await db.get_user_key_for_provider(user_id, judge_target.provider_key)
        if encrypted:
            judge_target = inject_user_keys([judge_target], {judge_target.provider_key: encrypted})[0]
    logger.debug(
        "Standalone judge target ready: model=%s api_base=%s has_key=%s",
        judge_target.model_id, judge_target.api_base,
        bool(judge_target.api_key),
    )

    # Helper to send WebSocket messages
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Create judge report in DB
    report_id = await db.save_judge_report(
        user_id=user_id,
        judge_model=judge_model_id,
        mode="post_eval",
        eval_run_id=eval_run_id,
    )

    # Store result_ref early
    await db.set_job_result_ref(job_id, report_id)

    await _ws_send({
        "type": "judge_start",
        "job_id": job_id,
        "mode": "post_eval",
        "judge_model": judge_target.display_name,
        "cases_to_review": len(results),
        "judge_report_id": report_id,
    })

    # Group results by model for per-model reports
    model_results: dict[str, list[dict]] = {}
    for r in results:
        mid = r.get("model_id", "unknown")
        model_results.setdefault(mid, []).append(r)

    target_map = {t.model_id: t for t in all_targets}
    all_verdicts = []
    all_model_reports = []
    completed = 0
    total_verdicts = len(results)
    sem = asyncio.Semaphore(concurrency)

    for model_id, model_res in model_results.items():
        if cancel_event.is_set():
            break

        model_verdicts = []

        async def _judge_one(r, _mid=model_id):
            async with sem:
                v = await _judge_single_verdict(
                    judge_target, tool_defs_text, {}, r,
                    custom_instructions=custom_instructions,
                )
                v["test_case_id"] = r.get("test_case_id", "?")
                v["model_id"] = _mid
                return v

        judge_batch = [asyncio.create_task(_judge_one(r)) for r in model_res]
        for coro in asyncio.as_completed(judge_batch):
            if cancel_event.is_set():
                for bt in judge_batch:
                    bt.cancel()
                break
            v = await coro
            all_verdicts.append(v)
            model_verdicts.append(v)
            completed += 1
            await _ws_send({"type": "judge_verdict", "job_id": job_id, **v})
            # Progress tracking
            j_pct = int((completed / total_verdicts) * 100) if total_verdicts > 0 else 0
            tgt = target_map.get(model_id)
            mname = tgt.display_name if tgt else model_id
            await progress_cb(j_pct, f"Judge {mname}: {completed}/{total_verdicts}")

        if cancel_event.is_set():
            if report_id:
                await db.update_judge_report(report_id, verdicts_json=json.dumps(all_verdicts), status="error")
            return None

        # Cross-case analysis per model
        tgt = target_map.get(model_id)
        mname = tgt.display_name if tgt else model_id
        report_data = await _judge_crosscase(judge_target, mname, model_verdicts)
        report_data["model_id"] = model_id
        report_data["model_name"] = mname
        all_model_reports.append(report_data)
        await _ws_send({"type": "judge_report", "job_id": job_id, "eval_id": eval_run_id, "report": report_data})

    # Save completed report
    if all_model_reports:
        best_report = max(all_model_reports, key=lambda r: r.get("overall_score", 0))
        final_grade = best_report.get("overall_grade", "?")
        final_score = best_report.get("overall_score", 0)
    else:
        final_grade = "?"
        final_score = 0

    await db.update_judge_report(
        report_id,
        verdicts_json=json.dumps(all_verdicts),
        report_json=json.dumps(all_model_reports),
        overall_grade=final_grade,
        overall_score=final_score,
        status="completed",
    )

    await _ws_send({"type": "judge_complete", "job_id": job_id, "judge_report_id": report_id})

    logger.info(
        "Judge completed: job_id=%s user_id=%s report_id=%s verdicts=%d",
        job_id, user_id, report_id, len(all_verdicts),
    )

    return report_id


# Register the judge handler with the job registry
job_registry.register_handler("judge", _judge_handler)


async def _judge_compare_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for comparative judge execution.

    Compares two eval runs with configurable concurrency.
    Returns the judge_report_id on success, or None.
    """
    user_id = params["user_id"]
    eval_run_id_a = params["eval_run_id_a"]
    eval_run_id_b = params["eval_run_id_b"]
    judge_model_id = params["judge_model"]
    concurrency = int(params.get("concurrency", 4))

    logger.info(
        "Judge compare started: job_id=%s user_id=%s run_a=%s run_b=%s",
        job_id, user_id, eval_run_id_a, eval_run_id_b,
    )

    # Load both runs
    run_a = await db.get_tool_eval_run(eval_run_id_a, user_id)
    run_b = await db.get_tool_eval_run(eval_run_id_b, user_id)
    results_a = json.loads(run_a.get("results_json", "[]"))
    results_b = json.loads(run_b.get("results_json", "[]"))

    # Load suite for tool definitions
    suite = await db.get_tool_suite(run_a["suite_id"], user_id)
    tools = json.loads(suite["tools_json"]) if suite else []
    tool_defs_text = _build_tool_definitions_text(tools)

    # Build judge target
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    judge_targets = [t for t in all_targets if t.model_id == judge_model_id]
    if not judge_targets:
        return None
    judge_target = judge_targets[0]

    if judge_target.provider_key:
        encrypted = await db.get_user_key_for_provider(user_id, judge_target.provider_key)
        if encrypted:
            judge_target = inject_user_keys([judge_target], {judge_target.provider_key: encrypted})[0]

    # Helper to send WebSocket messages
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Determine model names
    models_a = json.loads(run_a.get("models_json", "[]"))
    models_b = json.loads(run_b.get("models_json", "[]"))
    model_a_name = models_a[0] if models_a else "Model A"
    model_b_name = models_b[0] if models_b else "Model B"
    target_map = {t.model_id: t for t in all_targets}
    ta = target_map.get(model_a_name)
    tb = target_map.get(model_b_name)
    if ta:
        model_a_name = ta.display_name
    if tb:
        model_b_name = tb.display_name

    # Index results by test_case_id
    a_by_tc = {r["test_case_id"]: r for r in results_a if "test_case_id" in r}
    b_by_tc = {r["test_case_id"]: r for r in results_b if "test_case_id" in r}
    common_tcs = sorted(set(a_by_tc.keys()) & set(b_by_tc.keys()))

    if not common_tcs:
        return None

    # Create report in DB
    report_id = await db.save_judge_report(
        user_id=user_id,
        judge_model=judge_model_id,
        mode="comparative",
        eval_run_id=eval_run_id_a,
        eval_run_id_b=eval_run_id_b,
    )

    # Store result_ref early
    await db.set_job_result_ref(job_id, report_id)

    await _ws_send({
        "type": "compare_start",
        "job_id": job_id,
        "model_a": model_a_name,
        "model_b": model_b_name,
        "cases": len(common_tcs),
        "judge_report_id": report_id,
    })

    case_comparisons = []
    completed = 0
    total_cases = len(common_tcs)
    sem = asyncio.Semaphore(concurrency)

    async def _compare_one(idx, tc_id):
        async with sem:
            ra = a_by_tc[tc_id]
            rb = b_by_tc[tc_id]
            prompt = _JUDGE_COMPARE_PROMPT.format(
                tool_definitions=tool_defs_text,
                case_num=idx + 1,
                total_cases=total_cases,
                test_prompt=ra.get("prompt", ""),
                expected_tool=ra.get("expected_tool", "?"),
                expected_params=json.dumps(ra.get("expected_params", {})),
                model_a_name=model_a_name,
                a_tool=ra.get("actual_tool", "none"),
                a_params=json.dumps(ra.get("actual_params", {})),
                a_score=ra.get("overall_score", 0),
                model_b_name=model_b_name,
                b_tool=rb.get("actual_tool", "none"),
                b_params=json.dumps(rb.get("actual_params", {})),
                b_score=rb.get("overall_score", 0),
            )
            comparison = await _call_judge_model(judge_target, prompt)
            if not comparison:
                comparison = {"winner": "tie", "confidence": 0, "reasoning": "Judge error"}
            comparison.setdefault("winner", "tie")
            comparison.setdefault("confidence", 0)
            comparison.setdefault("reasoning", "")
            comparison["test_case_id"] = tc_id
            return comparison

    compare_tasks = [asyncio.create_task(_compare_one(idx, tc_id)) for idx, tc_id in enumerate(common_tcs)]
    for coro in asyncio.as_completed(compare_tasks):
        if cancel_event.is_set():
            for ct in compare_tasks:
                ct.cancel()
            if report_id:
                await db.update_judge_report(report_id, verdicts_json=json.dumps(case_comparisons), status="error")
            return None
        comparison = await coro
        case_comparisons.append(comparison)
        completed += 1
        await _ws_send({"type": "compare_case", "job_id": job_id, **comparison})
        c_pct = int((completed / total_cases) * 100) if total_cases > 0 else 0
        await progress_cb(c_pct, f"Compare: {completed}/{total_cases}")

    # Generate overall summary
    case_results_text = []
    for c in case_comparisons:
        case_results_text.append(
            f"- Case {c['test_case_id']}: winner={c['winner']}, "
            f"confidence={c.get('confidence', 0)}, reason: {c.get('reasoning', '')[:100]}"
        )

    summary_prompt = _JUDGE_COMPARE_SUMMARY_PROMPT.format(
        model_a_name=model_a_name,
        model_b_name=model_b_name,
        n=len(case_comparisons),
        case_results="\n".join(case_results_text),
    )
    summary = await _call_judge_model(judge_target, summary_prompt)
    if not summary:
        a_wins = sum(1 for c in case_comparisons if c.get("winner") == "model_a")
        b_wins = sum(1 for c in case_comparisons if c.get("winner") == "model_b")
        ties = len(case_comparisons) - a_wins - b_wins
        winner = "model_a" if a_wins > b_wins else ("model_b" if b_wins > a_wins else "tie")
        summary = {
            "overall_winner": winner,
            "score_a": round(a_wins / len(case_comparisons) * 100) if case_comparisons else 0,
            "score_b": round(b_wins / len(case_comparisons) * 100) if case_comparisons else 0,
            "summary": f"{model_a_name} won {a_wins}, {model_b_name} won {b_wins}, {ties} ties.",
            "tie_cases": ties,
        }

    summary.setdefault("overall_winner", "tie")
    summary.setdefault("score_a", 0)
    summary.setdefault("score_b", 0)
    summary.setdefault("summary", "")
    summary.setdefault("tie_cases", 0)
    summary["judge_report_id"] = report_id

    # Save report
    overall_grade = summary.get("overall_winner", "tie")
    overall_score = max(summary.get("score_a", 0), summary.get("score_b", 0))

    full_report = {
        "model_a": model_a_name,
        "model_b": model_b_name,
        "case_comparisons": case_comparisons,
        **summary,
    }

    await db.update_judge_report(
        report_id,
        verdicts_json=json.dumps(case_comparisons),
        report_json=json.dumps(full_report),
        overall_grade=overall_grade,
        overall_score=overall_score,
        status="completed",
    )

    await _ws_send({"type": "compare_complete", "job_id": job_id, **summary})

    logger.info(
        "Judge compare completed: job_id=%s user_id=%s report_id=%s cases=%d",
        job_id, user_id, report_id, len(case_comparisons),
    )

    return report_id


# Register the judge compare handler with the job registry
job_registry.register_handler("judge_compare", _judge_compare_handler)


@app.post("/api/tool-eval/judge")
async def run_judge_post_eval(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run post-eval judge via job registry. Returns job_id immediately.

    Progress is delivered via WebSocket (judge_verdict, judge_report, judge_complete events).
    """
    body = await request.json()
    eval_run_id = body.get("eval_run_id")
    judge_model_id = body.get("judge_model")
    custom_instructions = body.get("custom_instructions", "")
    concurrency = body.get("concurrency", 4)

    if not eval_run_id:
        return JSONResponse({"error": "eval_run_id is required"}, status_code=400)
    if not judge_model_id:
        return JSONResponse({"error": "judge_model is required"}, status_code=400)

    # Load eval run (validate before submitting job)
    eval_run = await db.get_tool_eval_run(eval_run_id, user["id"])
    if not eval_run:
        return JSONResponse({"error": "Eval run not found"}, status_code=404)

    results = json.loads(eval_run.get("results_json", "[]"))
    if not results:
        return JSONResponse({"error": "Eval run has no results"}, status_code=400)

    # Rate limit
    allowed, _ = _check_rate_limit(user["id"])
    if not allowed:
        return JSONResponse(
            {"error": f"Rate limit exceeded. Max {RATE_LIMIT_PER_HOUR} per hour."},
            status_code=429,
        )
    _record_rate_limit(user["id"])

    # Validate judge model exists
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)
    judge_targets = [t for t in all_targets if t.model_id == judge_model_id]
    if not judge_targets:
        return JSONResponse({"error": f"Judge model '{judge_model_id}' not found in config"}, status_code=400)

    progress_detail = f"Judge: {len(results)} verdicts, {judge_targets[0].display_name}"

    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "eval_run_id": eval_run_id,
        "judge_model": judge_model_id,
        "custom_instructions": custom_instructions,
        "concurrency": concurrency,
    }

    job_id = await job_registry.submit(
        job_type="judge",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@app.post("/api/tool-eval/judge/compare")
async def run_judge_compare(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run comparative judge via job registry. Returns job_id immediately.

    Progress is delivered via WebSocket (compare_case, compare_complete events).
    """
    body = await request.json()
    eval_run_id_a = body.get("eval_run_id_a")
    eval_run_id_b = body.get("eval_run_id_b")
    judge_model_id = body.get("judge_model")
    concurrency = body.get("concurrency", 4)

    if not eval_run_id_a or not eval_run_id_b:
        return JSONResponse({"error": "eval_run_id_a and eval_run_id_b are required"}, status_code=400)
    if not judge_model_id:
        return JSONResponse({"error": "judge_model is required"}, status_code=400)

    # Load both runs (validate before submitting job)
    run_a = await db.get_tool_eval_run(eval_run_id_a, user["id"])
    run_b = await db.get_tool_eval_run(eval_run_id_b, user["id"])
    if not run_a:
        return JSONResponse({"error": "Eval run A not found"}, status_code=404)
    if not run_b:
        return JSONResponse({"error": "Eval run B not found"}, status_code=404)

    results_a = json.loads(run_a.get("results_json", "[]"))
    results_b = json.loads(run_b.get("results_json", "[]"))
    if not results_a or not results_b:
        return JSONResponse({"error": "Both eval runs must have results"}, status_code=400)

    # Index and check for common test cases
    a_by_tc = {r["test_case_id"]: r for r in results_a if "test_case_id" in r}
    b_by_tc = {r["test_case_id"]: r for r in results_b if "test_case_id" in r}
    common_tcs = sorted(set(a_by_tc.keys()) & set(b_by_tc.keys()))
    if not common_tcs:
        return JSONResponse({"error": "No common test cases between the two runs"}, status_code=400)

    # Rate limit
    allowed, _ = _check_rate_limit(user["id"])
    if not allowed:
        return JSONResponse(
            {"error": f"Rate limit exceeded. Max {RATE_LIMIT_PER_HOUR} per hour."},
            status_code=429,
        )
    _record_rate_limit(user["id"])

    # Validate judge model exists
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)
    judge_targets = [t for t in all_targets if t.model_id == judge_model_id]
    if not judge_targets:
        return JSONResponse({"error": f"Judge model '{judge_model_id}' not found in config"}, status_code=400)

    progress_detail = f"Compare: {len(common_tcs)} cases, {judge_targets[0].display_name}"

    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "eval_run_id_a": eval_run_id_a,
        "eval_run_id_b": eval_run_id_b,
        "judge_model": judge_model_id,
        "concurrency": concurrency,
    }

    job_id = await job_registry.submit(
        job_type="judge_compare",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@app.post("/api/tool-eval/judge/cancel")
async def cancel_judge(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running judge operation via job registry."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = body.get("job_id")
    if job_id:
        cancelled = await job_registry.cancel(job_id, user["id"])
        if cancelled:
            return {"status": "ok", "message": "Cancellation requested"}
        return JSONResponse({"error": "Job not found or not cancellable"}, status_code=404)
    # Fallback: cancel via legacy user-level event
    _get_user_cancel(user["id"]).set()
    return {"status": "ok", "message": "Cancellation requested"}


@app.get("/api/tool-eval/judge/reports")
async def list_judge_reports(user: dict = Depends(auth.get_current_user)):
    """List judge reports for the current user."""
    reports = await db.get_judge_reports(user["id"])
    return {"reports": reports}


@app.get("/api/tool-eval/judge/reports/{report_id}")
async def get_judge_report(report_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full judge report detail."""
    report = await db.get_judge_report(report_id, user["id"])
    if not report:
        return JSONResponse({"error": "Judge report not found"}, status_code=404)
    return report


@app.delete("/api/tool-eval/judge/reports/{report_id}")
async def delete_judge_report(report_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a judge report."""
    deleted = await db.delete_judge_report(report_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Judge report not found"}, status_code=404)
    return {"status": "ok"}


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
        logger.debug("MCP discover: invalid/empty request body")
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
        logger.debug("MCP import: invalid/empty request body")
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
# Provider Parameters API
# ---------------------------------------------------------------------------


@app.get("/api/provider-params/registry")
async def get_provider_params_registry(user: dict = Depends(auth.get_current_user)):
    """Return the full provider parameter registry for the UI."""
    return {"providers": PROVIDER_REGISTRY}


@app.post("/api/provider-params/validate")
async def validate_provider_params(request: Request, user: dict = Depends(auth.get_current_user)):
    """Validate parameters against provider constraints.

    Request: {"provider_key": "anthropic", "model_id": "anthropic/claude-sonnet-4-5", "params": {...}}
    Returns: {"valid": bool, "adjustments": [...], "warnings": [...], "resolved_params": {...}}
    """
    body = await request.json()
    provider_key = body.get("provider_key", "")
    model_id = body.get("model_id", "")
    params = body.get("params", {})

    if not model_id:
        return JSONResponse({"error": "model_id is required"}, status_code=400)
    if not isinstance(params, dict):
        return JSONResponse({"error": "params must be a dict"}, status_code=400)

    provider = identify_provider(model_id, provider_key or None)
    result = validate_params(provider, model_id, params)
    return result


@app.post("/api/param-support/seed")
async def seed_param_support(user: dict = Depends(auth.get_current_user)):
    """Generate default param_support config from PROVIDER_REGISTRY.

    Transforms tier1+tier2 definitions into user-editable provider_defaults
    that the frontend uses as the single source of truth for the param tuner UI.

    Returns: {provider_defaults: {...}, model_overrides: {...}, presets: []}
    """
    provider_defaults = {}
    model_overrides = {}

    for prov_key, prov in PROVIDER_REGISTRY.items():
        if prov_key == "_unknown":
            continue  # Skip fallback provider
        params = {}
        # Merge tier1 + tier2 into a flat param dict
        for tier_key in ("tier1", "tier2"):
            tier = prov.get(tier_key, {})
            for param_name, spec in tier.items():
                # Only include params that are supported (True, "partial", "unknown")
                supported = spec.get("supported", True)
                if supported is False:
                    continue
                params[param_name] = {
                    "enabled": True,
                    "supported": supported,
                }
                # Copy numeric range fields
                for field in ("min", "max", "step", "default", "type"):
                    if field in spec:
                        params[param_name][field] = spec[field]
                # Copy enum values
                if "values" in spec:
                    params[param_name]["values"] = spec["values"]
                # Copy notes
                if "note" in spec:
                    params[param_name]["note"] = spec["note"]
        provider_defaults[prov_key] = {
            "display_name": prov.get("display_name", prov_key),
            "params": params,
        }

        # Copy model_overrides if present
        if "model_overrides" in prov:
            model_overrides[prov_key] = prov["model_overrides"]

    result = {
        "provider_defaults": provider_defaults,
        "model_overrides": model_overrides,
        "presets": list(BUILTIN_PARAM_PRESETS),
    }
    return result


# Built-in vendor-recommended parameter presets
BUILTIN_PARAM_PRESETS = [
    {
        "name": "Qwen3 Coder 30B (Recommended)",
        "builtin": True,
        "search_space": {
            "temperature": [0.7],
            "top_p": [0.8],
            "top_k": [20],
        },
        "system_prompt": "Greedy decoding (temp=0) worsens quality. Always use sampling.",
    },
    {
        "name": "GLM-4.7 Flash (Z.AI Recommended)",
        "builtin": True,
        "search_space": {
            "temperature": [0.8],
            "top_p": [0.6],
            "top_k": [2],
        },
        "system_prompt": "Very low top_k recommended for MoE architecture.",
    },
]


# ---------------------------------------------------------------------------
# Phase 10 Settings API
# ---------------------------------------------------------------------------

PHASE10_DEFAULTS = {
    "judge": {
        "enabled": False,
        "model_id": "",
        "mode": "post_eval",
        "temperature": 0.0,
        "max_tokens": 4096,
        "custom_instructions": "",
    },
    "param_tuner": {
        "max_combinations": 50,
        "temp_min": 0.0,
        "temp_max": 1.0,
        "temp_step": 0.5,
        "top_p_min": 0.5,
        "top_p_max": 1.0,
        "top_p_step": 0.25,
        "presets": [],
    },
    "prompt_tuner": {
        "mode": "quick",
        "generations": 3,
        "population_size": 5,
        "max_api_calls": 100,
    },
}


@app.get("/api/settings/phase10")
async def get_phase10_settings(user: dict = Depends(auth.get_current_user)):
    """Return Phase 10 feature settings (judge, param tuner, prompt tuner, param_support)."""
    config = await _get_user_config(user["id"])
    return {
        "judge": {**PHASE10_DEFAULTS["judge"], **config.get("judge_defaults", {})},
        "param_tuner": {**PHASE10_DEFAULTS["param_tuner"], **config.get("param_tuner_defaults", {})},
        "prompt_tuner": {**PHASE10_DEFAULTS["prompt_tuner"], **config.get("prompt_tuner_defaults", {})},
        "param_support": config.get("param_support_defaults", None),
    }


@app.put("/api/settings/phase10")
async def save_phase10_settings(request: Request, user: dict = Depends(auth.get_current_user)):
    """Save Phase 10 feature settings."""
    body = await request.json()
    config = await _get_user_config(user["id"])

    # Validate and merge each section
    allowed_sections = {"judge", "param_tuner", "prompt_tuner"}
    for section in allowed_sections:
        if section in body and isinstance(body[section], dict):
            config_key = f"{section}_defaults"
            section_data = body[section]

            # Validate param_tuner.presets if present
            if section == "param_tuner" and "presets" in section_data:
                presets = section_data["presets"]
                if not isinstance(presets, list):
                    return JSONResponse({"error": "presets must be an array"}, status_code=400)
                if len(presets) > 20:
                    return JSONResponse({"error": "Maximum 20 presets allowed"}, status_code=400)
                for i, preset in enumerate(presets):
                    if not isinstance(preset, dict):
                        return JSONResponse({"error": f"Preset {i} must be an object"}, status_code=400)
                    if not preset.get("name") or not isinstance(preset["name"], str):
                        return JSONResponse({"error": f"Preset {i} must have a non-empty 'name' string"}, status_code=400)
                    if not isinstance(preset.get("search_space"), dict):
                        return JSONResponse({"error": f"Preset {i} must have a 'search_space' object"}, status_code=400)

            config[config_key] = {**PHASE10_DEFAULTS[section], **section_data}

    # Handle param_support separately (not in PHASE10_DEFAULTS, stored as-is)
    if "param_support" in body:
        ps = body["param_support"]
        if ps is None:
            # Allow clearing param_support
            config.pop("param_support_defaults", None)
        elif isinstance(ps, dict):
            # Validate structure
            if not isinstance(ps.get("provider_defaults", {}), dict):
                return JSONResponse({"error": "param_support.provider_defaults must be an object"}, status_code=400)
            if not isinstance(ps.get("model_overrides", {}), dict):
                return JSONResponse({"error": "param_support.model_overrides must be an object"}, status_code=400)
            config["param_support_defaults"] = ps
        else:
            return JSONResponse({"error": "param_support must be an object or null"}, status_code=400)

    await _save_user_config(user["id"], config)
    return {"ok": True}


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
                logger.warning("API key decryption failed for provider=%s, falling back to global key", target.provider_key)

        # No user key found -- keep the global key (already on target)
        injected.append(target)

    return injected


async def async_run_single(
    target: Target, prompt: str, max_tokens: int, temperature: float,
    context_tokens: int = 0, timeout: int = 120,
    provider_params: dict | None = None,
) -> RunResult:
    """Execute a single streaming benchmark run using async litellm."""
    result = RunResult(target=target, context_tokens=context_tokens)

    messages = []
    # Per-model system prompt (prepended before context text)
    if target.system_prompt:
        if context_tokens > 0:
            context_text = generate_context_text(context_tokens)
            messages.append({"role": "system", "content": target.system_prompt + "\n\n" + context_text})
        else:
            messages.append({"role": "system", "content": target.system_prompt})
    elif context_tokens > 0:
        context_text = generate_context_text(context_tokens)
        messages.append({"role": "system", "content": context_text})
    messages.append({"role": "user", "content": prompt})

    # Build validated+clamped params via provider_params module
    pp_copy = dict(provider_params) if provider_params else None
    extra = build_litellm_kwargs(
        target, provider_params=pp_copy,
        temperature=temperature, max_tokens=max_tokens,
    )

    kwargs = {
        "model": target.model_id,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timeout": timeout,
    }
    # Apply validated params (temperature, max_tokens, plus any tier2/passthrough)
    if extra:
        kwargs.update(extra)
    else:
        # Fallback: no provider_params, apply directly (backward compat for scheduled benchmarks)
        kwargs["max_tokens"] = max_tokens
        kwargs["temperature"] = temperature
        if target.skip_params:
            for p in target.skip_params:
                kwargs.pop(p, None)

    if target.api_base:
        kwargs["api_base"] = target.api_base
    if target.api_key:
        kwargs["api_key"] = target.api_key

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
            logger.debug("Cost calculation not available for model %s", target.model_id)
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
            logger.exception("Trigger error running schedule %s", schedule["id"])

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
        logger.debug("Import settings: invalid JSON body")
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
        logger.warning("FERNET_KEY not set. Using auto-generated key from data/.fernet_key")
        logger.warning("Set FERNET_KEY env var in production and BACK UP the key.")

    parser = argparse.ArgumentParser(description="LLM Benchmark Studio")
    parser.add_argument("--port", type=int, default=8501, help="Port (default: 8501)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    logger.info("LLM Benchmark Studio starting on http://localhost:%d", args.port)
    log_level = os.environ.get("LOG_LEVEL", "warning").lower()
    uvicorn.run(app, host=args.host, port=args.port, log_level=log_level)

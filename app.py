#!/usr/bin/env python3
"""LLM Benchmark Studio - Web dashboard for benchmarking LLM providers.

Usage:
    python app.py                  # Start on port 8501
    python app.py --port 3333      # Custom port
"""

import argparse
import asyncio
import collections
import json
import logging
import logging.handlers
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import litellm

# Disable retry loops at two layers:
# 1. LiteLLM wrapper (default num_retries=2) -- we handle retries ourselves
#    in _generate_prompts_meta and _call_judge_model with exponential backoff.
# 2. OpenAI SDK internal (default max_retries=2) -- without this, OpenAI-compatible
#    endpoints (LM Studio) trigger invisible retry loops inside the SDK.
litellm.num_retries = 0
os.environ.setdefault("OPENAI_MAX_RETRIES", "0")

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse

# Load .env before importing benchmark (needs API keys)
_dir = Path(__file__).parent
load_dotenv(_dir / ".env", override=True)

APP_VERSION = os.getenv("APP_VERSION", "dev")

from benchmark import build_targets  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from ws_manager import ConnectionManager  # noqa: E402
from job_registry import registry as job_registry  # noqa: E402

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
# Import helpers (shared state lives here now)
# ---------------------------------------------------------------------------
from routers.helpers import (  # noqa: E402
    _get_user_config,
    inject_user_keys,
    async_run_single,
)

# ---------------------------------------------------------------------------
# Scheduled benchmark runner (used by lifespan + schedules router)
# ---------------------------------------------------------------------------

async def _run_scheduled_benchmark(schedule: dict):
    """Execute a single scheduled benchmark run and save results."""
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
                except Exception:
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


# ---------------------------------------------------------------------------
# WebSocket connection manager (singleton)
# ---------------------------------------------------------------------------
ws_manager = ConnectionManager()


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


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

app = FastAPI(title="LLM Benchmark Studio", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Global Pydantic ValidationError handler
# ---------------------------------------------------------------------------
from pydantic import ValidationError as PydanticValidationError  # noqa: E402
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402


@app.exception_handler(PydanticValidationError)
async def pydantic_validation_handler(request, exc):
    return _JSONResponse(status_code=422, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Security Headers Middleware
# ---------------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402


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


# ---------------------------------------------------------------------------
# Include all routers + inject ws_manager / _log_buffer / _run_scheduled_benchmark
# ---------------------------------------------------------------------------
import routers  # noqa: E402
import job_handlers  # noqa: E402
from routers import (  # noqa: E402
    admin, jobs, websocket, benchmark, tool_eval, param_tune,
    prompt_tune, judge, schedules,
)

# Inject ws_manager into every router module and job_handlers that needs it
for mod in (websocket, jobs, admin, benchmark, tool_eval, param_tune, prompt_tune, judge, job_handlers):
    mod.ws_manager = ws_manager

# Register all job handlers with the job registry
job_handlers.register_all_handlers()

# Inject _log_buffer into admin
admin._log_buffer = _log_buffer

# Inject _run_scheduled_benchmark into schedules
schedules._run_scheduled_benchmark = _run_scheduled_benchmark

# Register all routers
for r in routers.all_routers:
    app.include_router(r)


# ---------------------------------------------------------------------------
# Static / infrastructure routes (remain on app directly)
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
# Backward-compatible re-exports (tests import from 'app')
# ---------------------------------------------------------------------------
from routers.helpers import (  # noqa: E402, F811
    # Target selection
    _parse_target_selection,
    _filter_targets,
    _target_key,
    _find_target,
    # Rate limiting
    _check_rate_limit,
    # Scoring
    score_tool_selection,
    score_params,
    compute_overall_score,
    score_multi_turn,
    # Tool helpers
    _validate_tools,
    _parse_expected_tool,
    _serialize_expected_tool,
    _tool_matches,
    _mask_value,
    # Eval helpers
    _compute_eval_summaries,
    _build_tools_summary,
    _build_test_cases_summary,
    _build_tool_definitions_text,
    _sse,
    # Parse helpers
    _parse_meta_response,
    _parse_judge_json,
    # Search space
    _expand_search_space,
    # Presets
    BUILTIN_PARAM_PRESETS,
    PHASE10_DEFAULTS,
    # Experiment helpers
    _find_best_config,
    _find_best_score,
    # Config
    DEFAULT_CONFIG,
)

# MCP helpers (tests import from 'app')
from routers.mcp import (  # noqa: E402
    mcp_tool_to_openai,
    generate_test_case,
    _example_value,
)


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

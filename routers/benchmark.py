"""Benchmark execution, history, and cancel routes."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from schemas import BenchmarkRequest
from job_registry import registry as job_registry
from routers.helpers import (
    _parse_target_selection,
    _get_user_cancel,
    _check_rate_limit,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["benchmark"])

# Module-level ws_manager -- set by app.py after import
ws_manager = None


@router.post("/api/benchmark")
async def run_benchmark(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run benchmarks via the job registry. Returns job_id immediately.

    Progress is delivered via WebSocket (not SSE). The frontend receives
    job_created, job_started, job_progress, and job_completed events.
    """
    raw = await request.json()

    # Validate core fields via Pydantic
    try:
        validated = BenchmarkRequest(**raw)
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    model_ids, target_set = _parse_target_selection(raw)
    runs = validated.runs
    max_tokens = validated.max_tokens
    temperature = validated.temperature
    prompt = validated.prompt
    context_tiers = validated.context_tiers
    warmup = raw.get("warmup", True)
    provider_params = raw.get("provider_params")

    # --- Rate limit check (raises HTTPException 429 if exceeded) ---
    await _check_rate_limit(user["id"])

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


@router.post("/api/benchmark/cancel")
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


@router.get("/api/user/rate-limit")
async def get_rate_limit(user: dict = Depends(auth.get_current_user)):
    """Return the user's current rate limit status."""
    limits = await db.get_user_rate_limit(user["id"])
    max_per_hour = limits["benchmarks_per_hour"] if limits else 20
    recent = await db.get_user_recent_job_count(user["id"], hours=1)
    remaining = max(0, max_per_hour - recent)
    return {"limit": max_per_hour, "remaining": remaining, "window": "1 hour"}


@router.get("/api/history")
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


@router.get("/api/history/{run_id}")
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


@router.delete("/api/history/{run_id}")
async def delete_history_run(run_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a benchmark run from history."""
    deleted = await db.delete_benchmark_run(run_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return {"status": "ok"}

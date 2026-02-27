"""Benchmark execution, history, and cancel routes."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from schemas import BenchmarkRequest, DirectBenchmarkRequest
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
    profiles = raw.get("profiles")
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
        "profiles": profiles,
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


@router.post("/api/benchmark/direct-results")
async def save_direct_results(request: Request, user: dict = Depends(auth.get_current_user)):
    """Persist benchmark results collected directly in the browser (Direct Local Access).

    The browser ran benchmarks against local LLMs via fetch(), measured TTFT/throughput
    with performance.now(), and now sends the results here for DB persistence.
    """
    raw = await request.json()

    try:
        validated = DirectBenchmarkRequest(**raw)
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    if not validated.results:
        raise HTTPException(422, detail="No results to save")

    # Build litellm_id -> DB model UUID lookup
    all_models = await db.get_all_models_for_user(user["id"])
    model_lookup = {m["litellm_id"]: m["id"] for m in all_models}

    # Create benchmark run
    context_tiers_str = ",".join(str(t) for t in validated.context_tiers)
    config_json = json.dumps({"source": "direct_local"})

    run_id = await db.save_benchmark_run(
        user_id=user["id"],
        prompt=validated.prompt,
        context_tiers=context_tiers_str,
        max_tokens=validated.max_tokens,
        temperature=validated.temperature,
        warmup=validated.warmup,
        config_json=config_json,
    )

    # Save individual results, resolving litellm_id to DB UUID
    saved = 0
    skipped = 0
    for r in validated.results:
        db_model_id = model_lookup.get(r.model_id)
        if not db_model_id:
            logger.warning("direct-results: unknown model_id '%s', skipping", r.model_id)
            skipped += 1
            continue

        await db.save_benchmark_result(
            run_id=run_id,
            model_id=db_model_id,
            run_number=r.run_number,
            context_tokens=r.context_tokens,
            ttft_ms=r.ttft_ms,
            total_time_s=r.total_time_s,
            output_tokens=r.output_tokens,
            input_tokens=r.input_tokens,
            tokens_per_second=r.tokens_per_second,
            input_tokens_per_second=r.input_tokens_per_second,
            cost=r.cost,
            success=r.success,
            error=r.error,
        )
        saved += 1

    logger.info("direct-results: run_id=%s saved=%d skipped=%d", run_id, saved, skipped)

    return {"status": "ok", "run_id": run_id, "saved": saved, "skipped": skipped}


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
    for run in runs:
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
    # Fetch results from normalized benchmark_results table
    run["results"] = await db.get_benchmark_results(run_id)
    if isinstance(run.get("context_tiers"), str):
        try:
            run["context_tiers"] = json.loads(run["context_tiers"])
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse context_tiers for run %s", run_id)
    if isinstance(run.get("config_json"), str):
        try:
            run["config"] = json.loads(run["config_json"])
        except (json.JSONDecodeError, TypeError):
            run["config"] = None
        del run["config_json"]
    return run


@router.delete("/api/history/{run_id}")
async def delete_history_run(run_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a benchmark run from history."""
    deleted = await db.delete_benchmark_run(run_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return {"status": "ok"}

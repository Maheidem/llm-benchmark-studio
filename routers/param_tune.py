"""Parameter tuner routes (GridSearchCV for Tool Calling)."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from benchmark import build_targets
from schemas import ParamTuneRequest
from job_registry import registry as job_registry
from routers.helpers import (
    _get_user_config,
    _parse_target_selection,
    _filter_targets,
    _get_user_cancel,
    _check_rate_limit,
    _expand_search_space,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["param_tune"])

# Module-level ws_manager -- set by app.py after import
ws_manager = None


# ---------------------------------------------------------------------------
# Param Tune REST endpoints
# ---------------------------------------------------------------------------


@router.post("/api/tool-eval/param-tune")
async def run_param_tune(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run a parameter tuning grid search via job registry. Returns job_id immediately."""
    body = await request.json()

    # Validate core fields via Pydantic
    try:
        validated = ParamTuneRequest(
            suite_id=body.get("suite_id", ""),
            models=body.get("models") or None,
            targets=body.get("targets") or None,
            search_space=body.get("search_space", {}),
            experiment_id=body.get("experiment_id"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    suite_id = validated.suite_id
    model_ids, target_set = _parse_target_selection(body)
    search_space = validated.search_space
    per_model_search_spaces = body.get("per_model_search_spaces", {})

    # --- Validation ---
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

    # Rate limit check (raises HTTPException 429 if exceeded)
    await _check_rate_limit(user["id"])

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
    experiment_id = body.get("experiment_id")
    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "suite_id": suite_id,
        "models": model_ids,
        "target_set": [list(t) for t in target_set] if target_set else None,
        "search_space": search_space,
        "per_model_search_spaces": per_model_search_spaces,
        "experiment_id": experiment_id,
    }

    job_id = await job_registry.submit(
        job_type="param_tune",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@router.post("/api/tool-eval/param-tune/cancel")
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


@router.get("/api/tool-eval/param-tune/history")
async def get_param_tune_history(user: dict = Depends(auth.get_current_user)):
    """List user's param tune runs."""
    runs = await db.get_param_tune_runs(user["id"])
    return {"runs": runs}


@router.get("/api/tool-eval/param-tune/history/{tune_id}")
async def get_param_tune_detail(tune_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full param tune run details including all results."""
    run = await db.get_param_tune_run(tune_id, user["id"])
    if not run:
        return JSONResponse({"error": "Tune run not found"}, status_code=404)
    return run


@router.delete("/api/tool-eval/param-tune/history/{tune_id}")
async def delete_param_tune(tune_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a param tune run."""
    deleted = await db.delete_param_tune_run(tune_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Tune run not found"}, status_code=404)
    return {"status": "ok"}

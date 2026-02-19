"""Experiment management routes (M2)."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from benchmark import build_targets
from schemas import ExperimentCreate, ExperimentUpdate
from job_registry import registry as job_registry
from routers.helpers import (
    _get_user_config,
    _parse_target_selection,
    _filter_targets,
    _check_rate_limit,
    _avg_overall_from_summaries,
    _build_config_summary,
    _maybe_update_experiment_best,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["experiments"])


@router.get("/api/experiments")
async def list_experiments(user: dict = Depends(auth.get_current_user)):
    """List user's active experiments."""
    experiments = await db.get_experiments(user["id"])
    return {"experiments": experiments}


@router.post("/api/experiments")
async def create_experiment(request: Request, user: dict = Depends(auth.get_current_user)):
    """Create a new experiment."""
    body = await request.json()

    # Validate via Pydantic
    try:
        validated = ExperimentCreate(
            name=body.get("name", ""),
            description=body.get("description"),
            suite_id=body.get("suite_id", ""),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    name = validated.name.strip()
    suite_id = validated.suite_id
    description = validated.description or ""

    # Validate suite ownership
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)

    # Optional baseline
    baseline_eval_id = body.get("baseline_eval_id")
    baseline_score = None
    if baseline_eval_id:
        eval_run = await db.get_tool_eval_run(baseline_eval_id, user["id"])
        if not eval_run:
            return JSONResponse({"error": "Baseline eval run not found"}, status_code=404)
        if eval_run["suite_id"] != suite_id:
            return JSONResponse(
                {"error": "Baseline eval run suite_id does not match experiment suite_id"},
                status_code=400,
            )
        summary = json.loads(eval_run.get("summary_json", "[]"))
        baseline_score = _avg_overall_from_summaries(summary)

    # Optional suite snapshot
    suite_snapshot_json = None
    if body.get("snapshot_suite"):
        cases = await db.get_test_cases(suite_id)
        snapshot = {
            "suite": {
                "id": suite["id"],
                "name": suite["name"],
                "tools_json": suite["tools_json"],
            },
            "test_cases": cases,
        }
        suite_snapshot_json = json.dumps(snapshot)

    exp_id = await db.create_experiment(
        user_id=user["id"],
        name=name,
        suite_id=suite_id,
        description=description,
        suite_snapshot_json=suite_snapshot_json,
        baseline_eval_id=baseline_eval_id,
        baseline_score=baseline_score,
    )

    return {"status": "ok", "experiment_id": exp_id, "baseline_score": baseline_score}


@router.get("/api/experiments/{experiment_id}")
async def get_experiment(experiment_id: str, user: dict = Depends(auth.get_current_user)):
    """Get single experiment with parsed best_config."""
    exp = await db.get_experiment(experiment_id, user["id"])
    if not exp:
        return JSONResponse({"error": "Experiment not found"}, status_code=404)

    # Add suite_name
    suite = await db.get_tool_suite(exp["suite_id"], user["id"])
    exp["suite_name"] = suite["name"] if suite else None

    # Parse best_config_json for frontend convenience
    if exp.get("best_config_json"):
        try:
            exp["best_config"] = json.loads(exp["best_config_json"])
        except (json.JSONDecodeError, TypeError):
            exp["best_config"] = None
    else:
        exp["best_config"] = None

    return exp


@router.put("/api/experiments/{experiment_id}")
async def update_experiment(experiment_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Update experiment name/description."""
    body = await request.json()

    # Validate via Pydantic (all fields optional for update)
    try:
        validated = ExperimentUpdate(
            name=body.get("name"),
            description=body.get("description"),
            status=body.get("status"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    fields = {}
    if validated.name is not None:
        name = validated.name.strip()
        if not name:
            return JSONResponse({"error": "name cannot be empty"}, status_code=400)
        fields["name"] = name
    if validated.description is not None:
        fields["description"] = validated.description
    if validated.status is not None:
        fields["status"] = validated.status

    if not fields:
        return JSONResponse({"error": "No fields to update"}, status_code=400)

    updated = await db.update_experiment(experiment_id, user["id"], **fields)
    if not updated:
        return JSONResponse({"error": "Experiment not found"}, status_code=404)
    return {"status": "ok"}


@router.delete("/api/experiments/{experiment_id}")
async def delete_experiment(experiment_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete an experiment."""
    deleted = await db.delete_experiment(experiment_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Experiment not found"}, status_code=404)
    return {"status": "ok"}


@router.put("/api/experiments/{experiment_id}/baseline")
async def pin_experiment_baseline(
    experiment_id: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Pin or re-pin a baseline eval run for an experiment."""
    body = await request.json()
    eval_run_id = body.get("eval_run_id")
    if not eval_run_id:
        return JSONResponse(
            {"error": "eval_run_id is required"}, status_code=400
        )

    exp = await db.get_experiment(experiment_id, user["id"])
    if not exp:
        return JSONResponse(
            {"error": "Experiment not found"}, status_code=404
        )

    eval_run = await db.get_tool_eval_run(eval_run_id, user["id"])
    if not eval_run:
        return JSONResponse(
            {"error": "Eval run not found"}, status_code=404
        )

    if eval_run["suite_id"] != exp["suite_id"]:
        return JSONResponse(
            {"error": "Eval run suite_id does not match experiment suite_id"},
            status_code=400,
        )

    summary = json.loads(eval_run.get("summary_json", "[]"))
    if summary:
        scores = [s.get("overall_pct", 0) for s in summary]
        baseline_score = round(sum(scores) / len(scores) / 100, 4)
    else:
        baseline_score = 0.0

    await db.update_experiment(
        experiment_id, user["id"],
        baseline_eval_id=eval_run_id,
        baseline_score=baseline_score,
    )

    return {
        "status": "ok",
        "baseline_eval_id": eval_run_id,
        "baseline_score": baseline_score,
    }


@router.get("/api/experiments/{experiment_id}/timeline")
async def get_experiment_timeline(
    experiment_id: str,
    user: dict = Depends(auth.get_current_user),
):
    """Get ordered timeline of all linked runs across features."""
    exp = await db.get_experiment(experiment_id, user["id"])
    if not exp:
        return JSONResponse({"error": "Experiment not found"}, status_code=404)

    raw_entries = await db.get_experiment_timeline(experiment_id, user["id"])
    baseline_score = exp.get("baseline_score")
    baseline_eval_id = exp.get("baseline_eval_id")

    entries = []
    for e in raw_entries:
        entry = {"type": e["type"], "id": e["id"], "timestamp": e["timestamp"]}

        if e["type"] == "eval":
            summary = json.loads(e.get("summary_json") or "[]")
            score = _avg_overall_from_summaries(summary)
            entry["score"] = score
            entry["delta"] = round(score - baseline_score, 4) if baseline_score is not None else None
            entry["is_baseline"] = (e["id"] == baseline_eval_id)
            if e.get("config_json"):
                try:
                    cfg = json.loads(e["config_json"])
                    entry["config_summary"] = _build_config_summary(cfg)
                    if cfg.get("promoted_from"):
                        entry["promoted_from"] = cfg["promoted_from"]
                except (json.JSONDecodeError, TypeError):
                    entry["config_summary"] = "defaults"
            else:
                entry["config_summary"] = "defaults"

        elif e["type"] == "param_tune":
            score = e.get("best_score") or 0.0
            entry["score"] = score
            entry["delta"] = round(score - baseline_score, 4) if baseline_score is not None else None
            entry["status"] = e.get("status")
            if e.get("best_config_json"):
                try:
                    cfg = json.loads(e["best_config_json"])
                    entry["config_summary"] = "best: " + _build_config_summary(cfg)
                except (json.JSONDecodeError, TypeError):
                    entry["config_summary"] = "defaults"
            else:
                entry["config_summary"] = "defaults"

        elif e["type"] == "prompt_tune":
            score = e.get("best_score") or 0.0
            entry["score"] = score
            entry["delta"] = round(score - baseline_score, 4) if baseline_score is not None else None
            entry["status"] = e.get("status")
            bp = e.get("best_prompt") or ""
            entry["prompt_preview"] = bp[:80] if bp else ""

        elif e["type"] == "judge":
            entry["grade"] = e.get("overall_grade")
            entry["overall_score"] = e.get("overall_score")
            entry["mode"] = e.get("mode")
            entry["eval_run_id"] = e.get("eval_run_id")
            entry["status"] = e.get("status")

        entries.append(entry)

    # Build best info
    best_config = None
    if exp.get("best_config_json"):
        try:
            best_config = json.loads(exp["best_config_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "experiment_id": experiment_id,
        "experiment_name": exp["name"],
        "baseline": {
            "eval_id": baseline_eval_id,
            "score": baseline_score,
        } if baseline_eval_id else None,
        "best": {
            "score": exp.get("best_score") or 0.0,
            "source": exp.get("best_source"),
            "source_id": exp.get("best_source_id"),
            "config": best_config,
        },
        "entries": entries,
    }


@router.post("/api/experiments/{experiment_id}/run-best")
async def run_experiment_best(
    experiment_id: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Convenience: run eval using the experiment's best_config_json."""
    exp = await db.get_experiment(experiment_id, user["id"])
    if not exp:
        return JSONResponse({"error": "Experiment not found"}, status_code=404)

    if not exp.get("best_config_json"):
        return JSONResponse({"error": "Experiment has no best config yet"}, status_code=400)

    try:
        best_config = json.loads(exp["best_config_json"])
    except (json.JSONDecodeError, TypeError):
        return JSONResponse({"error": "Invalid best config JSON"}, status_code=400)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    # Build targets from best config or request override
    targets = body.get("targets")
    models = body.get("models")
    if not targets and not models:
        target_set = best_config.get("target_set", [])
        if target_set:
            targets = [{"provider_key": t[0], "model_id": t[1]} for t in target_set]

    if not targets and not models:
        return JSONResponse(
            {"error": "No models specified and best config has no target_set"},
            status_code=400,
        )

    # Build payload for the tool-eval endpoint
    eval_body = {
        "suite_id": exp["suite_id"],
        "temperature": best_config.get("temperature", 0.0),
        "tool_choice": best_config.get("tool_choice", "required"),
        "experiment_id": experiment_id,
    }
    if targets:
        eval_body["targets"] = targets
    if models:
        eval_body["models"] = models
    if best_config.get("provider_params"):
        eval_body["provider_params"] = best_config["provider_params"]
    if best_config.get("system_prompt"):
        eval_body["system_prompt"] = best_config["system_prompt"]

    model_ids, target_set = _parse_target_selection(eval_body)
    if not model_ids:
        return JSONResponse({"error": "No matching models found"}, status_code=400)

    # Validate suite
    suite = await db.get_tool_suite(exp["suite_id"], user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(exp["suite_id"])
    if not cases:
        return JSONResponse({"error": "Suite has no test cases"}, status_code=400)

    # Rate limit (raises HTTPException 429 if exceeded)
    await _check_rate_limit(user["id"])

    # Build targets and validate
    user_config = await _get_user_config(user["id"])
    all_targets = build_targets(user_config)
    filtered_targets = _filter_targets(all_targets, model_ids, target_set)
    if not filtered_targets:
        return JSONResponse({"error": "No matching models found in config"}, status_code=400)

    model_count = len(filtered_targets)
    progress_detail = f"Tool Eval: {model_count} model{'s' if model_count != 1 else ''}, {suite['name']} (from experiment)"

    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "suite_id": exp["suite_id"],
        "models": model_ids,
        "target_set": [list(t) for t in target_set] if target_set else None,
        "temperature": best_config.get("temperature", 0.0),
        "tool_choice": best_config.get("tool_choice", "required"),
        "provider_params": best_config.get("provider_params"),
        "system_prompt": best_config.get("system_prompt"),
        "experiment_id": experiment_id,
    }

    job_id = await job_registry.submit(
        job_type="tool_eval",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {
        "job_id": job_id,
        "status": "submitted",
        "config_used": {
            "temperature": best_config.get("temperature", 0.0),
            "tool_choice": best_config.get("tool_choice", "required"),
        },
    }

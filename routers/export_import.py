"""Export and import routes for benchmarks, tool evals, settings."""

import csv
import io
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, StreamingResponse

import auth
import db
from routers.helpers import _get_user_config, _save_user_config, _VALID_PERIODS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["export_import"])


@router.get("/api/export/history")
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


@router.get("/api/export/leaderboard")
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


@router.get("/api/export/tool-eval")
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


@router.get("/api/export/eval/{eval_id}")
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


@router.get("/api/export/run/{run_id}")
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


@router.get("/api/export/settings")
async def export_settings(user: dict = Depends(auth.get_current_user)):
    """Export the user's complete configuration as a JSON file download."""
    config = await _get_user_config(user["id"])

    defaults = config.get("defaults", {})
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


@router.post("/api/import/settings")
async def import_settings(request: Request, user: dict = Depends(auth.get_current_user)):
    """Import settings from a previously exported JSON file."""
    try:
        body = await request.json()
    except Exception:
        logger.debug("Import settings: invalid JSON body")
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

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

    if "defaults" in body and isinstance(body["defaults"], dict):
        config["defaults"] = body["defaults"]

    await _save_user_config(user["id"], config)

    return {
        "status": "ok",
        "providers_imported": providers_added + providers_updated,
        "providers_updated": providers_updated,
        "providers_added": providers_added,
    }

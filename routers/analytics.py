"""Analytics routes: leaderboard, trends, compare."""

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

import auth
import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])

_VALID_PERIODS = {"7d", "30d", "90d", "all"}


@router.get("/api/analytics/leaderboard")
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
        return {"type": "tool_eval", "period": period, "models": models}

    # Default: benchmark leaderboard
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
    return {"type": "benchmark", "period": period, "models": models}


@router.get("/api/analytics/trends")
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

    series_map: dict[str, list[dict]] = {name: [] for name in model_names}

    for run in runs:
        results = json.loads(run["results_json"]) if isinstance(run["results_json"], str) else run["results_json"]
        run_model_vals: dict[str, list[float]] = {}
        for r in results:
            if not r.get("success", False):
                continue
            display = r.get("model", "")
            if display in series_map:
                run_model_vals.setdefault(display, []).append(
                    float(r.get("tokens_per_second", 0)) if metric == "tps" else float(r.get("ttft_ms", 0))
                )

        for model_name, vals in run_model_vals.items():
            if vals:
                series_map[model_name].append({
                    "timestamp": run["timestamp"],
                    "value": round(sum(vals) / len(vals), 2),
                })

    series = []
    for name in model_names:
        points = series_map.get(name, [])
        if points:
            points.sort(key=lambda p: p["timestamp"])
            series.append({"model": name, "points": points})

    return {"metric": metric, "series": series}


@router.get("/api/analytics/compare")
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

#!/usr/bin/env python3
"""LLM Benchmark Studio - Web dashboard for benchmarking LLM providers.

Usage:
    python app.py                  # Start on port 8501
    python app.py --port 3333      # Custom port
"""

import argparse
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

import yaml

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# Load .env before importing benchmark (needs API keys)
_dir = Path(__file__).parent
load_dotenv(_dir / ".env")

from benchmark import (  # noqa: E402
    AggregatedResult,
    load_config,
    build_targets,
    run_single,
    save_results,
)

app = FastAPI(title="LLM Benchmark Studio")
CONFIG_PATH = str(_dir / "config.yaml")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard UI."""
    return (_dir / "index.html").read_text()


@app.get("/api/config")
async def get_config():
    """Return available providers and models from config with provider metadata."""
    config = load_config(CONFIG_PATH)

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
async def update_model_config(request: Request):
    """Update per-model settings in config.yaml (full edit support)."""
    body = await request.json()
    model_id = body.get("model_id")
    provider_key = body.get("provider_key")
    if not model_id:
        return JSONResponse({"error": "model_id required"}, status_code=400)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

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

    _save_config(config)
    return {"status": "ok", "model_id": body.get("new_model_id") or model_id}


def _save_config(config: dict):
    """Write config dict back to YAML."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


@app.post("/api/config/model")
async def add_model(request: Request):
    """Add a new model to a provider."""
    body = await request.json()
    prov_key = body.get("provider_key")
    model_id = body.get("id")
    if not prov_key or not model_id:
        return JSONResponse({"error": "provider_key and id required"}, status_code=400)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

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
    _save_config(config)
    return {"status": "ok", "model_id": model_id}


@app.delete("/api/config/model")
async def delete_model(request: Request):
    """Remove a model from a provider."""
    body = await request.json()
    prov_key = body.get("provider_key")
    model_id = body.get("model_id")
    if not prov_key or not model_id:
        return JSONResponse({"error": "provider_key and model_id required"}, status_code=400)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    prov_cfg = config.get("providers", {}).get(prov_key)
    if not prov_cfg:
        return JSONResponse({"error": f"Provider '{prov_key}' not found"}, status_code=404)

    models = prov_cfg.get("models", [])
    original_len = len(models)
    prov_cfg["models"] = [m for m in models if m["id"] != model_id]

    if len(prov_cfg["models"]) == original_len:
        return JSONResponse({"error": f"Model '{model_id}' not found"}, status_code=404)

    _save_config(config)
    return {"status": "ok"}


@app.post("/api/config/provider")
async def add_provider(request: Request):
    """Add a new provider."""
    body = await request.json()
    prov_key = body.get("provider_key")
    if not prov_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

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
    _save_config(config)
    return {"status": "ok", "provider_key": prov_key}


@app.put("/api/config/provider")
async def update_provider(request: Request):
    """Edit provider settings (not its models)."""
    body = await request.json()
    prov_key = body.get("provider_key")
    if not prov_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

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

    _save_config(config)
    return {"status": "ok"}


@app.delete("/api/config/provider")
async def delete_provider(request: Request):
    """Remove a provider and all its models."""
    body = await request.json()
    prov_key = body.get("provider_key")
    if not prov_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    if prov_key not in config.get("providers", {}):
        return JSONResponse({"error": f"Provider '{prov_key}' not found"}, status_code=404)

    del config["providers"][prov_key]
    _save_config(config)
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
async def get_env_keys():
    """List env keys with masked values."""
    entries = _parse_env_file()
    env_keys = {name: val for name, val, _ in entries}

    # Also include api_key_env refs from config that may be missing from .env
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
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
async def update_env_key(request: Request):
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
async def delete_env_key(request: Request):
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


@app.post("/api/benchmark")
async def run_benchmark(request: Request):
    """Run benchmarks and stream results via SSE."""
    body = await request.json()
    model_ids = body.get("models", [])
    runs = body.get("runs", 1)
    max_tokens = body.get("max_tokens", 512)
    temperature = body.get("temperature", 0.7)
    prompt = body.get("prompt", "")
    context_tiers = body.get("context_tiers", [0])

    config = load_config(CONFIG_PATH)
    defaults = config.get("defaults", {})
    all_targets = build_targets(config)

    # Filter to requested models (or run all if none specified)
    if model_ids:
        targets = [t for t in all_targets if t.model_id in model_ids]
    else:
        targets = all_targets

    if not prompt.strip():
        prompt = defaults.get("prompt", "Explain recursion in programming with a Python example.")

    loop = asyncio.get_event_loop()

    async def generate():
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

                    for r in range(runs):
                        result = await loop.run_in_executor(
                            None, run_single, target, prompt, max_tokens, temperature, tier
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
            item = await queue.get()
            if item is None:
                break
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

        # Save results
        agg_results = _aggregate(all_results, config)
        saved = save_results(agg_results, prompt, context_tiers=context_tiers)

        yield _sse({"type": "complete", "saved_to": str(saved)})

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/history")
async def get_history():
    """Return previous benchmark runs."""
    results_dir = _dir / "results"
    if not results_dir.exists():
        return JSONResponse([])

    files = sorted(results_dir.glob("benchmark_*.json"), reverse=True)
    history = []
    for f in files[:20]:
        try:
            data = json.loads(f.read_text())
            data["filename"] = f.name
            history.append(data)
        except Exception:
            continue
    return JSONResponse(history)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"data: {json.dumps(data)}\n\n"


def _aggregate(raw_results: list[dict], config: dict) -> list[AggregatedResult]:
    """Convert raw result dicts into AggregatedResults for saving."""
    from benchmark import Target, RunResult

    # Group by (model_id, context_tokens)
    grouped = {}
    for r in raw_results:
        key = (r["model_id"], r.get("context_tokens", 0))
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(r)

    agg_list = []
    all_targets = build_targets(config)
    target_map = {t.model_id: t for t in all_targets}

    for (mid, ctx_tokens), runs in grouped.items():
        target = target_map.get(mid, Target(
            provider=runs[0]["provider"],
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

        # Store context_tokens on the result for saving
        agg.all_results = [RunResult(
            target=target,
            context_tokens=ctx_tokens,
            ttft_ms=r["ttft_ms"],
            total_time_s=r["total_time_s"],
            output_tokens=r["output_tokens"],
            input_tokens=r.get("input_tokens", 0),
            tokens_per_second=r["tokens_per_second"],
            success=r["success"],
            error=r.get("error", ""),
        ) for r in runs]

        agg_list.append(agg)

    return agg_list


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="LLM Benchmark Studio")
    parser.add_argument("--port", type=int, default=8501, help="Port (default: 8501)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    print(f"\n  LLM Benchmark Studio running at http://localhost:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

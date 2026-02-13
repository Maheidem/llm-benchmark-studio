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
import time
from pathlib import Path

import litellm
import yaml

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# Load .env before importing benchmark (needs API keys)
_dir = Path(__file__).parent
load_dotenv(_dir / ".env", override=True)

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

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app_instance):
    """Initialize database on startup."""
    await db.init_db()
    # Optionally bootstrap admin from env vars
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_pass = os.environ.get("ADMIN_PASSWORD")
    if admin_email and admin_pass:
        existing = await db.get_user_by_email(admin_email)
        if not existing:
            hashed = auth.hash_password(admin_pass)
            await db.create_user(admin_email, hashed, role="admin")
            print(f"  Admin account created: {admin_email}")
    yield

app = FastAPI(title="LLM Benchmark Studio", lifespan=lifespan)
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


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
app.post("/api/auth/register")(auth.register_handler)
app.post("/api/auth/login")(auth.login_handler)
app.post("/api/auth/refresh")(auth.refresh_handler)
app.post("/api/auth/logout")(auth.logout_handler)
app.get("/api/auth/me")(auth.me_handler)


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
async def cancel_benchmark(user: dict = Depends(auth.get_current_user)):
    """Cancel a running benchmark."""
    _get_user_cancel(user["id"]).set()
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
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

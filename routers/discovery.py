"""Provider discovery, health check, and parameter support routes."""

import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

import litellm

import auth
import db
from benchmark import Target, build_targets, sanitize_error
from keyvault import vault
from provider_params import (
    PROVIDER_REGISTRY,
    identify_provider,
    validate_params,
    build_litellm_kwargs,
)
from routers.helpers import (
    _get_user_config,
    _save_user_config,
    inject_user_keys,
    BUILTIN_PARAM_PRESETS,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["discovery"])


@router.get("/api/models/discover")
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
                    mid = raw_name.replace("models/", "", 1) if raw_name.startswith("models/") else raw_name
                    dn = m.get("displayName", mid)
                    full_id = f"{prefix}/{mid}" if prefix and not mid.startswith(prefix + "/") else mid
                    models.append({"id": full_id, "display_name": dn})

            elif api_type == "generic":
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
                url = "https://api.openai.com/v1/models"
                headers = {"Authorization": f"Bearer {api_key or ''}"}
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for m in data:
                    mid = m.get("id", "")
                    models.append({"id": mid, "display_name": mid})

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


@router.get("/api/lm-studio/detect")
async def detect_lm_studio_backend(provider_key: str, user: dict = Depends(auth.get_current_user)):
    """Detect LM Studio model backend type (GGUF vs MLX) via /v1/models."""
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


@router.get("/api/health/providers")
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


@router.get("/api/provider-params/registry")
async def get_provider_params_registry(user: dict = Depends(auth.get_current_user)):
    """Return the full provider parameter registry for the UI."""
    return {"providers": PROVIDER_REGISTRY}


@router.post("/api/provider-params/validate")
async def validate_provider_params(request: Request, user: dict = Depends(auth.get_current_user)):
    """Validate parameters against provider constraints."""
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


@router.post("/api/param-support/seed")
async def seed_param_support(user: dict = Depends(auth.get_current_user)):
    """Generate default param_support config from PROVIDER_REGISTRY."""
    provider_defaults = {}
    model_overrides = {}

    for prov_key, prov in PROVIDER_REGISTRY.items():
        if prov_key == "_unknown":
            continue
        params = {}
        for tier_key in ("tier1", "tier2"):
            tier = prov.get(tier_key, {})
            for param_name, spec in tier.items():
                supported = spec.get("supported", True)
                if supported is False:
                    continue
                params[param_name] = {
                    "enabled": True,
                    "supported": supported,
                }
                for field in ("min", "max", "step", "default", "type"):
                    if field in spec:
                        params[param_name][field] = spec[field]
                if "values" in spec:
                    params[param_name]["values"] = spec["values"]
                if "note" in spec:
                    params[param_name]["note"] = spec["note"]
        provider_defaults[prov_key] = {
            "display_name": prov.get("display_name", prov_key),
            "params": params,
        }

        if "model_overrides" in prov:
            model_overrides[prov_key] = prov["model_overrides"]

    result = {
        "provider_defaults": provider_defaults,
        "model_overrides": model_overrides,
        "presets": list(BUILTIN_PARAM_PRESETS),
    }
    return result

"""Config routes: providers, models, prompts."""

import json
import logging
import os
import re

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from keyvault import vault
from schemas import ModelConfigUpdate, ProviderCreate
from routers.helpers import _get_user_config, _save_user_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])


@router.get("/api/config")
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


@router.put("/api/config/model")
async def update_model_config(request: Request, user: dict = Depends(auth.get_current_user)):
    """Update per-model settings in user's config (full edit support)."""
    body = await request.json()

    # Validate core fields via Pydantic (model_id, provider_key are required)
    try:
        validated = ModelConfigUpdate(**body)
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    model_id = validated.model_id
    provider_key = validated.provider_key

    config = await _get_user_config(user["id"])

    found = False
    for prov_key, prov_cfg in config.get("providers", {}).items():
        if provider_key and prov_key != provider_key:
            continue
        for model in prov_cfg.get("models", []):
            if model["id"] == model_id:
                new_id = body.get("new_model_id")
                if new_id and new_id != model_id:
                    model["id"] = new_id

                if "display_name" in body:
                    dn = body["display_name"]
                    if dn:
                        model["display_name"] = dn
                    else:
                        mid = model["id"]
                        model["display_name"] = mid.split("/")[-1] if "/" in mid else mid

                if "context_window" in body and body["context_window"] is not None:
                    model["context_window"] = int(body["context_window"])

                if "max_output_tokens" in body:
                    val = body["max_output_tokens"]
                    if val is None or val == "":
                        model.pop("max_output_tokens", None)
                    else:
                        model["max_output_tokens"] = int(val)

                if "skip_params" in body:
                    sp = body["skip_params"]
                    if sp and len(sp) > 0:
                        model["skip_params"] = sp
                    else:
                        model.pop("skip_params", None)

                if "system_prompt" in body:
                    sp_val = body["system_prompt"]
                    if sp_val and isinstance(sp_val, str) and sp_val.strip():
                        model["system_prompt"] = sp_val.strip()
                    else:
                        model.pop("system_prompt", None)

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


@router.post("/api/config/model")
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

    prefix = prov_cfg.get("model_id_prefix", "")
    if prefix and not model_id.startswith(prefix + "/"):
        model_id = f"{prefix}/{model_id}"

    for m in prov_cfg.get("models", []):
        if m["id"] == model_id:
            return JSONResponse({"error": f"Model '{model_id}' already exists"}, status_code=400)

    display_name = body.get("display_name") or (model_id.split("/")[-1] if "/" in model_id else model_id)

    new_model = {"id": model_id, "display_name": display_name}
    if body.get("context_window"):
        new_model["context_window"] = int(body["context_window"])
    if body.get("max_output_tokens"):
        new_model["max_output_tokens"] = int(body["max_output_tokens"])

    prov_cfg.setdefault("models", []).append(new_model)
    await _save_user_config(user["id"], config)
    return {"status": "ok", "model_id": model_id}


@router.delete("/api/config/model")
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


@router.post("/api/config/provider")
async def add_provider(request: Request, user: dict = Depends(auth.get_current_user)):
    """Add a new provider."""
    body = await request.json()

    # Validate via Pydantic
    try:
        validated = ProviderCreate(**body)
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    prov_key = validated.provider_key

    config = await _get_user_config(user["id"])

    if prov_key in config.get("providers", {}):
        return JSONResponse({"error": f"Provider '{prov_key}' already exists"}, status_code=400)

    new_prov = {"display_name": validated.display_name, "models": []}
    if validated.api_base:
        new_prov["api_base"] = validated.api_base
    if validated.api_key_env:
        new_prov["api_key_env"] = validated.api_key_env
    if body.get("api_key"):
        new_prov["api_key"] = body["api_key"]
    if validated.model_id_prefix:
        new_prov["model_id_prefix"] = validated.model_id_prefix

    config.setdefault("providers", {})[prov_key] = new_prov
    await _save_user_config(user["id"], config)
    return {"status": "ok", "provider_key": prov_key}


@router.put("/api/config/provider")
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


@router.delete("/api/config/provider")
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


# --- Prompt templates ---

@router.get("/api/config/prompts")
async def get_prompt_templates(user: dict = Depends(auth.get_current_user)):
    """Return prompt templates from user's config."""
    config = await _get_user_config(user["id"])
    return config.get("prompt_templates", {})


@router.post("/api/config/prompts")
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

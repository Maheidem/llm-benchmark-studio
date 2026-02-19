"""Per-user API key management routes."""

import logging
import os

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from keyvault import vault
from schemas import ApiKeyUpdate
from routers.helpers import _get_user_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["keys"])


@router.get("/api/keys")
async def get_my_keys(user: dict = Depends(auth.get_current_user)):
    """List the current user's API keys (provider + status, never plaintext)."""
    user_keys = await db.get_user_keys(user["id"])

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

    for uk in user_keys:
        pk = uk["provider_key"]
        if pk in providers:
            providers[pk]["has_user_key"] = True
            providers[pk]["user_key_updated_at"] = uk["updated_at"]

    return {"keys": list(providers.values())}


@router.put("/api/keys")
async def set_my_key(request: Request, user: dict = Depends(auth.get_current_user)):
    """Set or update the current user's API key for a provider."""
    body = await request.json()

    # Validate via Pydantic (map 'value' -> 'api_key' for backward compat)
    try:
        validated = ApiKeyUpdate(
            provider_key=body.get("provider_key", ""),
            api_key=body.get("value", body.get("api_key", "")),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    provider_key = validated.provider_key
    value = validated.api_key

    config = await _get_user_config(user["id"])
    prov_cfg = config.get("providers", {}).get(provider_key)
    if not prov_cfg:
        return JSONResponse({"error": f"Provider '{provider_key}' not found"}, status_code=404)

    key_name = prov_cfg.get("api_key_env", f"{provider_key.upper()}_API_KEY")
    encrypted = vault.encrypt(value)
    key_id = await db.upsert_user_key(user["id"], provider_key, key_name, encrypted)
    logger.info("API key set: user_id=%s provider=%s", user["id"], provider_key)

    return {"status": "ok", "key_id": key_id, "provider_key": provider_key}


@router.delete("/api/keys")
async def delete_my_key(request: Request, user: dict = Depends(auth.get_current_user)):
    """Remove the current user's API key for a provider."""
    body = await request.json()
    provider_key = body.get("provider_key", "").strip()

    if not provider_key:
        return JSONResponse({"error": "provider_key required"}, status_code=400)

    deleted = await db.delete_user_key(user["id"], provider_key)
    if not deleted:
        return JSONResponse({"error": "Key not found"}, status_code=404)

    logger.info("API key deleted: user_id=%s provider=%s", user["id"], provider_key)
    return {"status": "ok"}

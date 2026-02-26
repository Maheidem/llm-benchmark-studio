"""Provider and Model management endpoints (v2 normalized)."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
import db
from schemas import (
    NormalizedProviderCreate,
    NormalizedProviderUpdate,
    NormalizedProviderResponse,
    NormalizedModelCreate,
    NormalizedModelUpdate,
    NormalizedModelResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["providers"])

# Module-level ws_manager -- set by app.py after import
ws_manager = None


# ─── Providers ───


@router.get("/providers", response_model=list[NormalizedProviderResponse])
async def list_providers(user: dict = Depends(get_current_user)):
    """List all providers for the current user."""
    return await db.get_providers(user["id"])


@router.post("/providers", response_model=NormalizedProviderResponse, status_code=201)
async def create_provider(body: NormalizedProviderCreate, user: dict = Depends(get_current_user)):
    """Create a new provider."""
    existing = await db.get_provider_by_key(user["id"], body.key)
    if existing:
        raise HTTPException(409, f"Provider '{body.key}' already exists")
    pid = await db.create_provider(
        user["id"],
        body.key,
        body.name,
        api_base=body.api_base,
        api_key_env=body.api_key_env,
        model_prefix=body.model_prefix,
    )
    return await db.get_provider(pid)


@router.get("/providers/{provider_id}", response_model=NormalizedProviderResponse)
async def get_provider(provider_id: str, user: dict = Depends(get_current_user)):
    """Get a single provider by ID."""
    p = await db.get_provider(provider_id)
    if not p or p["user_id"] != user["id"]:
        raise HTTPException(404, "Provider not found")
    return p


@router.put("/providers/{provider_id}", response_model=NormalizedProviderResponse)
async def update_provider(
    provider_id: str, body: NormalizedProviderUpdate, user: dict = Depends(get_current_user)
):
    """Update a provider."""
    p = await db.get_provider(provider_id)
    if not p or p["user_id"] != user["id"]:
        raise HTTPException(404, "Provider not found")
    updates = body.model_dump(exclude_none=True)
    if updates:
        await db.update_provider(provider_id, **updates)
    return await db.get_provider(provider_id)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, user: dict = Depends(get_current_user)):
    """Delete a provider and all its models."""
    p = await db.get_provider(provider_id)
    if not p or p["user_id"] != user["id"]:
        raise HTTPException(404, "Provider not found")
    await db.delete_provider(provider_id)


# ─── Models ───


@router.get("/providers/{provider_id}/models", response_model=list[NormalizedModelResponse])
async def list_models(provider_id: str, user: dict = Depends(get_current_user)):
    """List all active models for a provider."""
    p = await db.get_provider(provider_id)
    if not p or p["user_id"] != user["id"]:
        raise HTTPException(404, "Provider not found")
    rows = await db.get_models_for_provider(provider_id)
    results = []
    for r in rows:
        r["skip_params"] = json.loads(r["skip_params"]) if r.get("skip_params") else []
        r["provider_key"] = p["key"]
        r["provider_name"] = p["name"]
        results.append(r)
    return results


@router.get("/models", response_model=list[NormalizedModelResponse])
async def list_all_models(user: dict = Depends(get_current_user)):
    """List all active models across all active providers for the current user."""
    rows = await db.get_all_models_for_user(user["id"])
    for r in rows:
        r["skip_params"] = json.loads(r["skip_params"]) if r.get("skip_params") else []
    return rows


@router.post(
    "/providers/{provider_id}/models",
    response_model=NormalizedModelResponse,
    status_code=201,
)
async def create_model(
    provider_id: str, body: NormalizedModelCreate, user: dict = Depends(get_current_user)
):
    """Create a new model under a provider."""
    p = await db.get_provider(provider_id)
    if not p or p["user_id"] != user["id"]:
        raise HTTPException(404, "Provider not found")
    skip = json.dumps(body.skip_params) if body.skip_params else None
    mid = await db.create_model(
        provider_id,
        body.litellm_id,
        body.display_name,
        context_window=body.context_window,
        max_output_tokens=body.max_output_tokens,
        skip_params=skip,
    )
    result = await db.get_model(mid)
    result["skip_params"] = json.loads(result["skip_params"]) if result.get("skip_params") else []
    return result


@router.put("/models/{model_id}", response_model=NormalizedModelResponse)
async def update_model(
    model_id: str, body: NormalizedModelUpdate, user: dict = Depends(get_current_user)
):
    """Update a model."""
    m = await db.get_model(model_id)
    if not m or m["user_id"] != user["id"]:
        raise HTTPException(404, "Model not found")
    updates = body.model_dump(exclude_none=True)
    if "skip_params" in updates:
        updates["skip_params"] = json.dumps(updates["skip_params"])
    if updates:
        await db.update_model(model_id, **updates)
    result = await db.get_model(model_id)
    result["skip_params"] = json.loads(result["skip_params"]) if result.get("skip_params") else []
    return result


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(model_id: str, user: dict = Depends(get_current_user)):
    """Delete a model."""
    m = await db.get_model(model_id)
    if not m or m["user_id"] != user["id"]:
        raise HTTPException(404, "Model not found")
    await db.delete_model(model_id)

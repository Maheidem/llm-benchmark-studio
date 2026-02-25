"""Prompt Version Registry routes.

CRUD for versioned system prompts. Supports manual creation and
auto-save from the prompt tuner (source='prompt_tuner').
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from schemas import PromptVersionCreate, PromptVersionUpdate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["prompt_versions"])


@router.get("/api/prompt-versions")
async def list_prompt_versions(user: dict = Depends(auth.get_current_user)):
    """List all prompt versions for the current user, newest first."""
    versions = await db.get_prompt_versions(user["id"])
    return {"versions": versions}


@router.post("/api/prompt-versions")
async def create_prompt_version(request: Request, user: dict = Depends(auth.get_current_user)):
    """Save a new prompt version manually."""
    body = await request.json()
    try:
        validated = PromptVersionCreate(
            prompt_text=body.get("prompt_text", ""),
            label=body.get("label", ""),
            source=body.get("source", "manual"),
            parent_version_id=body.get("parent_version_id"),
        )
    except (ValidationError, Exception) as e:
        return JSONResponse({"error": str(e)}, status_code=422)

    version_id = await db.create_prompt_version(
        user_id=user["id"],
        prompt_text=validated.prompt_text,
        label=validated.label,
        source=validated.source,
        parent_version_id=validated.parent_version_id,
    )
    return {"status": "ok", "version_id": version_id}


@router.get("/api/prompt-versions/{version_id}")
async def get_prompt_version(version_id: str, user: dict = Depends(auth.get_current_user)):
    """Get a single prompt version."""
    version = await db.get_prompt_version(version_id, user["id"])
    if not version:
        return JSONResponse({"error": "Version not found"}, status_code=404)
    return version


@router.patch("/api/prompt-versions/{version_id}")
async def update_prompt_version(version_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Update the label of a prompt version."""
    body = await request.json()
    try:
        validated = PromptVersionUpdate(label=body.get("label", ""))
    except (ValidationError, Exception) as e:
        return JSONResponse({"error": str(e)}, status_code=422)

    updated = await db.update_prompt_version_label(version_id, user["id"], validated.label)
    if not updated:
        return JSONResponse({"error": "Version not found"}, status_code=404)
    return {"status": "ok"}


@router.delete("/api/prompt-versions/{version_id}")
async def delete_prompt_version(version_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a prompt version."""
    deleted = await db.delete_prompt_version(version_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Version not found"}, status_code=404)
    return {"status": "ok"}

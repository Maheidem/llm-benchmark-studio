"""Model Profiles CRUD router."""

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

import auth
import db
from schemas import ProfileCreate, ProfileUpdate, ProfileFromTuner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("")
async def list_profiles(user: dict = Depends(auth.get_current_user)):
    """List all profiles for current user."""
    profiles = await db.get_profiles(user["id"])
    return {"profiles": profiles}


@router.get("/detail/{profile_id}")
async def get_profile(profile_id: str, user: dict = Depends(auth.get_current_user)):
    """Get single profile by ID."""
    profile = await db.get_profile(profile_id, user["id"])
    if not profile:
        return JSONResponse({"error": "Profile not found"}, status_code=404)
    return profile


@router.post("/from-tuner")
async def create_from_tuner(body: ProfileFromTuner, user: dict = Depends(auth.get_current_user)):
    """Create a profile from a param/prompt tuner result."""
    try:
        profile_id = await db.create_profile(
            user_id=user["id"],
            model_id=body.model_id,
            name=body.name,
            params_json=json.dumps(body.params_json) if body.params_json else None,
            system_prompt=body.system_prompt,
            is_default=body.set_as_default,
            origin_type=body.source_type,
            origin_ref=body.source_id,
        )
        return {"status": "ok", "profile_id": profile_id}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return JSONResponse({"error": "A profile with this name already exists for this model"}, status_code=409)
        raise


@router.post("/{profile_id}/set-default")
async def set_default(profile_id: str, user: dict = Depends(auth.get_current_user)):
    """Set a profile as default for its model."""
    updated = await db.set_default_profile(profile_id, user["id"])
    if not updated:
        return JSONResponse({"error": "Profile not found"}, status_code=404)
    return {"status": "ok"}


@router.post("")
async def create_profile(body: ProfileCreate, user: dict = Depends(auth.get_current_user)):
    """Create a new profile."""
    try:
        profile_id = await db.create_profile(
            user_id=user["id"],
            model_id=body.model_id,
            name=body.name,
            description=body.description,
            params_json=json.dumps(body.params_json) if body.params_json else None,
            system_prompt=body.system_prompt,
            is_default=body.is_default,
            origin_type=body.origin_type,
            origin_ref=body.origin_ref,
        )
        return {"status": "ok", "profile_id": profile_id}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return JSONResponse({"error": "A profile with this name already exists for this model"}, status_code=409)
        raise


@router.put("/{profile_id}")
async def update_profile(profile_id: str, body: ProfileUpdate, user: dict = Depends(auth.get_current_user)):
    """Update a profile."""
    update_data = {}
    if body.name is not None:
        update_data["name"] = body.name
    if body.description is not None:
        update_data["description"] = body.description
    if body.params_json is not None:
        update_data["params_json"] = json.dumps(body.params_json)
    if body.system_prompt is not None:
        update_data["system_prompt"] = body.system_prompt
    if body.is_default is not None:
        update_data["is_default"] = body.is_default

    if not update_data:
        return JSONResponse({"error": "No fields to update"}, status_code=400)

    try:
        updated = await db.update_profile(profile_id, user["id"], **update_data)
        if not updated:
            return JSONResponse({"error": "Profile not found"}, status_code=404)
        return {"status": "ok"}
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return JSONResponse({"error": "A profile with this name already exists for this model"}, status_code=409)
        raise


@router.delete("/{profile_id}")
async def delete_profile(profile_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a profile."""
    deleted = await db.delete_profile(profile_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Profile not found"}, status_code=404)
    return {"status": "ok"}


@router.get("/{model_id:path}")
async def list_model_profiles(model_id: str, user: dict = Depends(auth.get_current_user)):
    """List profiles for a specific model."""
    profiles = await db.get_profiles(user["id"], model_id=model_id)
    return {"profiles": profiles}

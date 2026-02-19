"""Onboarding status routes."""

import logging

from fastapi import APIRouter, Depends

import auth
import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["onboarding"])


@router.get("/api/onboarding/status")
async def onboarding_status(user: dict = Depends(auth.get_current_user)):
    """Check if user has completed onboarding."""
    full_user = await db.get_user_by_id(user["id"])
    completed = bool(full_user.get("onboarding_completed", 0)) if full_user else False
    return {"completed": completed}


@router.post("/api/onboarding/complete")
async def onboarding_complete(user: dict = Depends(auth.get_current_user)):
    """Mark onboarding as completed for the current user."""
    await db.set_onboarding_completed(user["id"])
    return {"status": "ok"}

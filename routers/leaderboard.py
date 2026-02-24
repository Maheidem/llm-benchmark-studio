"""2D: Public Tool-Calling Leaderboard routes.

Public endpoints (no auth) for reading the leaderboard.
Authenticated endpoints for opt-in/opt-out settings.
Leaderboard is fed by anonymous aggregation from tool eval completions.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import auth
import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leaderboard"])


# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------

@router.get("/api/leaderboard/tool-eval")
async def get_public_leaderboard():
    """Return public tool-calling leaderboard aggregated across all contributing users.

    Entries are anonymous -- no user data is exposed.
    Sorted by tool accuracy descending.
    Rate limit: handled by reverse proxy / FastAPI middleware.
    """
    entries = await db.get_leaderboard()
    return {
        "leaderboard": entries,
        "note": "Aggregated from opt-in user contributions. All data is anonymous.",
    }


# ---------------------------------------------------------------------------
# Authenticated endpoints (opt-in management)
# ---------------------------------------------------------------------------

@router.get("/api/leaderboard/opt-in")
async def get_leaderboard_opt_in(user: dict = Depends(auth.get_current_user)):
    """Return current user's leaderboard opt-in status."""
    opted_in = await db.get_user_leaderboard_opt_in(user["id"])
    return {"opt_in": opted_in}


@router.put("/api/leaderboard/opt-in")
async def set_leaderboard_opt_in(request: Request, user: dict = Depends(auth.get_current_user)):
    """Set user's leaderboard opt-in preference.

    Body: {"opt_in": true|false}
    """
    body = await request.json()
    opt_in = bool(body.get("opt_in", False))
    await db.set_user_leaderboard_opt_in(user["id"], opt_in)
    logger.info("Leaderboard opt-in set: user_id=%s opt_in=%s", user["id"], opt_in)
    return {"status": "ok", "opt_in": opt_in}

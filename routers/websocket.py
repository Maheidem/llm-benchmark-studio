"""WebSocket endpoint for real-time job status updates."""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import auth
import db
from job_registry import registry as job_registry

logger = logging.getLogger(__name__)

router = APIRouter()

# These are set by app.py after import
ws_manager = None


def _build_reconnect_init(job: dict) -> dict | None:
    """Build a job-type-specific init message for reconnecting clients.

    When a client reconnects (page refresh), it needs the init event to
    set up its progress tracking structures. This reconstructs that event
    from the job's stored params_json and current progress.
    """
    job_type = job.get("job_type", "")
    job_id = job["id"]
    progress_pct = job.get("progress_pct", 0) or 0

    try:
        params = json.loads(job.get("params_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        return None

    if job_type == "benchmark":
        # Reconstruct benchmark_init from stored params
        models = params.get("models", [])
        target_set = params.get("target_set")
        targets = []
        if target_set:
            for t in target_set:
                if len(t) >= 2:
                    targets.append({"provider_key": t[0], "model_id": t[1]})
        else:
            # Legacy: model_ids only (no provider_key)
            for mid in models:
                targets.append({"provider_key": "", "model_id": mid})

        return {
            "type": "benchmark_init",
            "job_id": job_id,
            "reconnect": True,
            "progress_pct": progress_pct,
            "data": {
                "targets": targets,
                "runs": params.get("runs", 1),
                "context_tiers": params.get("context_tiers", [0]),
                "max_tokens": params.get("max_tokens", 512),
            },
        }

    if job_type == "tool_eval":
        return {
            "type": "tool_eval_init",
            "job_id": job_id,
            "reconnect": True,
            "progress_pct": progress_pct,
            "data": {
                "targets": [],  # Frontend doesn't need full target list for reconnect
                "total_cases": 0,
                "suite_name": "",
            },
        }

    if job_type == "param_tune":
        return {
            "type": "tune_start",
            "job_id": job_id,
            "reconnect": True,
            "progress_pct": progress_pct,
            "tune_id": job.get("result_ref", ""),
            "total_combos": 0,
            "models": params.get("models", []),
            "suite_name": "",
        }

    if job_type == "prompt_tune":
        return {
            "type": "tune_start",
            "job_id": job_id,
            "reconnect": True,
            "progress_pct": progress_pct,
            "tune_id": job.get("result_ref", ""),
            "mode": params.get("mode", "quick"),
            "total_prompts": 0,
            "total_eval_calls": 0,
            "suite_name": "",
        }

    return None


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time job status updates.

    Auth: JWT access token passed as query param ?token=xxx
    On connect: sends a 'sync' message with active + recent jobs.
    Then re-sends init events for any running jobs so reconnecting
    clients can resume progress tracking.
    Listens for client messages: 'ping' (keep-alive), 'cancel' (cancel a job).
    """
    from jose import JWTError, ExpiredSignatureError

    # --- Auth: validate JWT from query param ---
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4001, reason="Missing token")
        return

    try:
        payload = auth.decode_token(token)
        if payload.get("type") not in ("access", "cli"):
            raise ValueError("Invalid token type")
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("No sub in token")
    except (JWTError, ExpiredSignatureError, ValueError):
        await ws.close(code=4001, reason="Invalid token")
        return

    # Look up user to get role (for admin broadcast)
    user = await db.get_user_by_id(user_id)
    if not user:
        await ws.close(code=4001, reason="User not found")
        return

    role = user.get("role", "user")

    # --- Register connection (max 5 per user) ---
    connected = await ws_manager.connect(user_id, role, ws)
    if not connected:
        return  # Too many connections, already closed by manager

    # --- Send initial sync: active + recent jobs ---
    try:
        active_jobs = await db.get_user_active_jobs(user_id)
        recent_jobs = await db.get_user_recent_jobs(user_id, limit=10)
        await ws.send_json({
            "type": "sync",
            "active_jobs": active_jobs,
            "recent_jobs": recent_jobs,
        })

        # Re-send init events for running jobs so reconnecting clients
        # can resume progress tracking (fixes stale 0/0 after refresh)
        for job in active_jobs:
            if job.get("status") == "running":
                init_msg = _build_reconnect_init(job)
                if init_msg:
                    await ws.send_json(init_msg)
    except Exception:
        logger.exception("WebSocket initial sync failed (user_id=%s)", user_id)
        await ws_manager.disconnect(user_id, ws)
        return

    # --- Listen loop with 90s receive timeout ---
    # The timeout catches dead connections from unclean proxy disconnects
    # (e.g. Cloudflare closing without sending a close frame).
    # Clients should send a ping at least every 60s to stay alive.
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_json(), timeout=90)
            except asyncio.TimeoutError:
                # No message received in 90s — assume dead connection
                try:
                    await ws.close(code=4002, reason="Receive timeout")
                except Exception:
                    logger.debug("WebSocket close failed during timeout disconnect")
                break

            msg_type = data.get("type")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "cancel":
                job_id = data.get("job_id")
                if job_id:
                    await job_registry.cancel(job_id, user_id)

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected (user_id=%s)", user_id)
    except Exception:
        logger.exception("WebSocket unexpected error (user_id=%s)", user_id)
    finally:
        await ws_manager.disconnect(user_id, ws)

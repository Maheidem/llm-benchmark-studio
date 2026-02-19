"""WebSocket endpoint for real-time job status updates."""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import auth
import db
from job_registry import registry as job_registry

logger = logging.getLogger(__name__)

router = APIRouter()

# These are set by app.py after import
ws_manager = None


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time job status updates.

    Auth: JWT access token passed as query param ?token=xxx
    On connect: sends a 'sync' message with active + recent jobs.
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

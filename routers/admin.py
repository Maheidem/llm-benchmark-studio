"""Admin endpoints (all require admin role)."""

import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from schemas import RateLimitUpdate
from routers.helpers import _get_user_config, _user_locks, _user_cancel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

# These are set by app.py after import
_log_buffer = None
_process_start_time = None
ws_manager = None
_dir = None


@router.get("/api/admin/users")
async def admin_list_users(current_user: dict = Depends(auth.require_admin)):
    """List all users with last login, benchmark count, key count."""
    conn = await db.get_db()
    try:
        cursor = await conn.execute("""
            SELECT u.id, u.email, u.role, u.created_at,
                   (SELECT MAX(timestamp) FROM audit_log
                    WHERE user_id = u.id AND action = 'user_login') as last_login,
                   (SELECT COUNT(*) FROM benchmark_runs
                    WHERE user_id = u.id) as benchmark_count,
                   (SELECT COUNT(*) FROM user_api_keys
                    WHERE user_id = u.id) as key_count
            FROM users u
            ORDER BY u.created_at DESC
        """)
        rows = await cursor.fetchall()
        return {"users": [dict(r) for r in rows]}
    finally:
        await conn.close()


@router.put("/api/admin/users/{user_id}/role")
async def admin_update_role(user_id: str, request: Request, current_user: dict = Depends(auth.require_admin)):
    """Change a user's role (admin/user). Cannot change own role."""
    body = await request.json()
    new_role = body.get("role")
    if new_role not in ("admin", "user"):
        return JSONResponse({"error": "role must be 'admin' or 'user'"}, status_code=400)
    if user_id == current_user["id"]:
        return JSONResponse({"error": "Cannot change your own role"}, status_code=400)

    conn = await db.get_db()
    try:
        await conn.execute("UPDATE users SET role = ?, updated_at = datetime('now') WHERE id = ?", (new_role, user_id))
        await conn.commit()
    finally:
        await conn.close()

    await db.log_audit(
        current_user["id"], current_user.get("email", ""), "admin_user_update",
        resource_type="user", resource_id=str(user_id),
        detail={"change": "role", "new_role": new_role},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"status": "ok"}


@router.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request, current_user: dict = Depends(auth.require_admin)):
    """Delete a user and all their data (cascade). Cannot delete self."""
    if user_id == current_user["id"]:
        return JSONResponse({"error": "Cannot delete your own account"}, status_code=400)

    conn = await db.get_db()
    try:
        cursor = await conn.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "User not found"}, status_code=404)
        deleted_email = row["email"]

        # Unlink audit_log entries (no CASCADE, but keep records)
        await conn.execute("UPDATE audit_log SET user_id = NULL WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await conn.commit()
    finally:
        await conn.close()

    # Clean up in-memory state for deleted user to prevent memory leaks
    _user_locks.pop(user_id, None)
    _user_cancel.pop(user_id, None)

    await db.log_audit(
        current_user["id"], current_user.get("email", ""), "admin_user_delete",
        resource_type="user", resource_id=str(user_id),
        detail={"deleted_email": deleted_email},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"status": "ok"}


@router.get("/api/admin/logs")
async def admin_get_logs(
    request: Request,
    lines: int = 100,
    level: str | None = None,
    search: str | None = None,
    token: str | None = None,
):
    """Return recent application log entries from in-memory ring buffer.

    Auth: either admin JWT (via cookie/header) OR LOG_ACCESS_TOKEN query param.
    """
    # Auth: static token OR admin JWT
    log_token = os.environ.get("LOG_ACCESS_TOKEN", "")
    if token and log_token and token == log_token:
        pass  # static token auth OK
    else:
        # Fall back to JWT admin auth
        try:
            user = await auth.get_current_user(request)
            if user.get("role") != "admin":
                return JSONResponse({"error": "Admin required"}, status_code=403)
        except Exception:
            return JSONResponse({"error": "Set LOG_ACCESS_TOKEN env var or use admin JWT"}, status_code=401)

    lines = min(max(1, lines), 2000)
    entries = list(_log_buffer) if _log_buffer else []
    if level:
        level_upper = level.upper()
        entries = [e for e in entries if f'"level": "{level_upper}"' in e]
    if search:
        entries = [e for e in entries if search.lower() in e.lower()]
    return {"count": len(entries[-lines:]), "logs": entries[-lines:]}


@router.get("/api/admin/stats")
async def admin_stats(current_user: dict = Depends(auth.require_admin)):
    """Usage statistics: benchmark counts, top users, keys by provider."""
    conn = await db.get_db()
    try:
        stats = {}

        # Benchmark counts by time window
        for label, interval in [("24h", "-1 day"), ("7d", "-7 days"), ("30d", "-30 days")]:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM benchmark_runs WHERE timestamp > datetime('now', ?)",
                (interval,),
            )
            row = await cursor.fetchone()
            stats[f"benchmarks_{label}"] = row["cnt"]

        # Top users by benchmark count
        cursor = await conn.execute("""
            SELECT u.email as username, COUNT(*) as cnt
            FROM benchmark_runs br JOIN users u ON br.user_id = u.id
            GROUP BY br.user_id ORDER BY cnt DESC LIMIT 10
        """)
        rows = await cursor.fetchall()
        stats["top_users"] = [dict(r) for r in rows]

        # Keys by provider
        cursor = await conn.execute("""
            SELECT provider_key as provider, COUNT(*) as user_count
            FROM user_api_keys GROUP BY provider_key
        """)
        rows = await cursor.fetchall()
        stats["keys_by_provider"] = [dict(r) for r in rows]

        # Total users
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
        stats["total_users"] = row["cnt"]

        return stats
    finally:
        await conn.close()


@router.get("/api/admin/system")
async def admin_system_health(current_user: dict = Depends(auth.require_admin)):
    """System health: db size, results count, uptime, benchmark status."""
    db_path = db.DB_PATH
    results_dir = _dir / "results" if _dir else None

    db_size_mb = 0
    if db_path.exists():
        db_size_mb = round(db_path.stat().st_size / 1024 / 1024, 2)

    results_count = 0
    results_size_mb = 0
    if results_dir and results_dir.exists():
        json_files = list(results_dir.glob("*.json"))
        results_count = len(json_files)
        results_size_mb = round(sum(f.stat().st_size for f in json_files) / 1024 / 1024, 2)

    # Get active jobs from the job registry for system health
    active_jobs = await db.get_all_active_jobs()

    return {
        "db_size_mb": db_size_mb,
        "results_size_mb": results_size_mb,
        "results_count": results_count,
        "benchmark_active": any(lock.locked() for lock in _user_locks.values()) or any(
            j["job_type"] == "benchmark" for j in active_jobs
        ),
        "active_jobs": active_jobs,
        "total_active": len([j for j in active_jobs if j["status"] == "running"]),
        "total_queued": len([j for j in active_jobs if j["status"] == "queued"]),
        "connected_ws_clients": ws_manager.get_connection_count() if ws_manager else 0,
        "process_uptime_s": round(time.time() - _process_start_time) if _process_start_time else 0,
    }


@router.get("/api/admin/audit")
async def admin_audit_log(
    request: Request,
    user: str = None,
    action: str = None,
    since: str = None,
    limit: int = 100,
    offset: int = 0,
    current_user: dict = Depends(auth.require_admin),
):
    """Audit log with optional filters and pagination."""
    conn = await db.get_db()
    try:
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []

        if user:
            query += " AND username = ?"
            params.append(user)
        if action:
            query += " AND action = ?"
            params.append(action)
        if since:
            query += " AND timestamp > ?"
            params.append(since)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        return {"entries": [dict(r) for r in rows]}
    finally:
        await conn.close()


@router.put("/api/admin/users/{user_id}/rate-limit")
async def admin_set_rate_limit(user_id: str, request: Request, current_user: dict = Depends(auth.require_admin)):
    """Set per-user rate limits."""
    body = await request.json()

    # Validate via Pydantic
    try:
        validated = RateLimitUpdate(
            benchmarks_per_hour=body.get("benchmarks_per_hour", 20),
            max_concurrent=body.get("max_concurrent", 1),
            max_runs_per_benchmark=body.get("max_runs_per_benchmark", 10),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    conn = await db.get_db()
    try:
        await conn.execute("""
            INSERT INTO rate_limits (user_id, benchmarks_per_hour, max_concurrent, max_runs_per_benchmark, updated_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                benchmarks_per_hour = excluded.benchmarks_per_hour,
                max_concurrent = excluded.max_concurrent,
                max_runs_per_benchmark = excluded.max_runs_per_benchmark,
                updated_at = datetime('now'),
                updated_by = excluded.updated_by
        """, (
            user_id,
            validated.benchmarks_per_hour,
            validated.max_concurrent,
            validated.max_runs_per_benchmark,
            current_user["id"],
        ))
        await conn.commit()
    finally:
        await conn.close()

    await db.log_audit(
        current_user["id"], current_user.get("email", ""), "admin_rate_limit",
        resource_type="config", resource_id=str(user_id),
        detail=body,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"status": "ok"}


@router.get("/api/admin/users/{user_id}/rate-limit")
async def admin_get_rate_limit(user_id: str, current_user: dict = Depends(auth.require_admin)):
    """Get per-user rate limits (returns defaults if none set)."""
    conn = await db.get_db()
    try:
        cursor = await conn.execute("SELECT * FROM rate_limits WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"user_id": user_id, "benchmarks_per_hour": 20, "max_concurrent": 1, "max_runs_per_benchmark": 10}
    finally:
        await conn.close()

"""Job tracking REST endpoints."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

import auth
import db
from job_registry import registry as job_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])

# ws_manager is set by app.py after import
ws_manager = None


async def _cleanup_orphaned_tune_run(job: dict) -> bool:
    """If a terminal job has a linked tune run still showing 'running', mark it interrupted.

    Returns True if a cleanup was performed, False if nothing to clean up.
    """
    result_ref = job.get("result_ref")
    if not result_ref:
        return False
    job_type = job.get("job_type", "")
    user_id = job["user_id"]
    try:
        if "param" in job_type:
            run = await db.get_param_tune_run(result_ref, user_id)
            if run and run.get("status") == "running":
                await db.update_param_tune_run(result_ref, user_id, status="interrupted")
                logger.info("Cleaned up orphaned param_tune_run %s (job %s already %s)", result_ref, job["id"], job["status"])
                return True
        elif "prompt" in job_type:
            run = await db.get_prompt_tune_run(result_ref, user_id)
            if run and run.get("status") == "running":
                await db.update_prompt_tune_run(result_ref, user_id, status="interrupted")
                logger.info("Cleaned up orphaned prompt_tune_run %s (job %s already %s)", result_ref, job["id"], job["status"])
                return True
    except Exception:
        logger.exception("Failed to clean up orphaned tune run %s", result_ref)
    return False


@router.get("/api/jobs")
async def list_jobs(request: Request, user: dict = Depends(auth.get_current_user)):
    """List current user's jobs. Optional query params: ?status=running,queued&limit=20"""
    status_filter = request.query_params.get("status")
    limit = int(request.query_params.get("limit", "20"))
    jobs = await db.get_user_jobs(user["id"], status=status_filter, limit=limit)
    return {"jobs": jobs}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(auth.get_current_user)):
    """Get a single job's details (scoped to the current user)."""
    job = await db.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a specific job (user can only cancel their own jobs)."""
    job = await db.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    if job["status"] in ("done", "failed", "cancelled", "interrupted"):
        # Job already terminal -- but linked tune run might still show "running" (ghost).
        # Clean it up so the frontend sees a consistent state.
        cleaned = await _cleanup_orphaned_tune_run(job)
        if cleaned:
            if ws_manager:
                await ws_manager.send_to_user(user["id"], {
                    "type": "job_cancelled", "job_id": job_id,
                })
            return {"status": "ok", "message": "Job already finished, cleaned up linked run", "was_orphan": True}
        return JSONResponse({"error": "Job already finished"}, status_code=400)

    # For queued jobs, just mark cancelled directly
    if job["status"] in ("pending", "queued"):
        await db.update_job_status(
            job_id, "cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if ws_manager:
            await ws_manager.send_to_user(user["id"], {
                "type": "job_cancelled", "job_id": job_id,
            })
        return {"status": "ok", "message": "Job cancelled"}

    # For running jobs, use the job registry to signal cancellation
    cancelled = await job_registry.cancel(job_id, user["id"])
    if not cancelled:
        # Fallback: mark as cancelled directly if registry doesn't know about it
        await db.update_job_status(
            job_id, "cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if ws_manager:
            await ws_manager.send_to_user(user["id"], {
                "type": "job_cancelled", "job_id": job_id,
            })

    await db.log_audit(
        user_id=user["id"],
        username=user.get("email", ""),
        action="job_cancel",
        resource_type="job",
        resource_id=job_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )

    return {"status": "ok", "message": "Cancellation requested"}


@router.get("/api/admin/jobs")
async def admin_list_jobs(current_user: dict = Depends(auth.require_admin)):
    """Admin: list all active jobs across all users."""
    jobs = await db.get_all_active_jobs()
    return {"jobs": jobs}


@router.post("/api/admin/jobs/{job_id}/cancel")
async def admin_cancel_job(job_id: str, request: Request, current_user: dict = Depends(auth.require_admin)):
    """Admin: cancel any user's job."""
    job = await db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    if job["status"] in ("done", "failed", "cancelled", "interrupted"):
        # Job already terminal -- clean up any orphaned linked tune run
        cleaned = await _cleanup_orphaned_tune_run(job)
        if cleaned:
            if ws_manager:
                await ws_manager.send_to_user(job["user_id"], {
                    "type": "job_cancelled", "job_id": job_id,
                })
            return {"status": "ok", "message": "Job already finished, cleaned up linked run", "was_orphan": True}
        return JSONResponse({"error": "Job already finished"}, status_code=400)

    # Use job registry for running jobs (signals cancel_event)
    cancelled = await job_registry.cancel(job_id, current_user["id"], is_admin=True)
    if not cancelled:
        # Fallback for jobs not tracked by registry
        await db.update_job_status(
            job_id, "cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if ws_manager:
            await ws_manager.send_to_user(job["user_id"], {
                "type": "job_cancelled", "job_id": job_id,
            })

    await db.log_audit(
        user_id=current_user["id"],
        username=current_user.get("email", ""),
        action="admin_job_cancel",
        resource_type="job",
        resource_id=job_id,
        detail={"target_user": job["user_id"]},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
    )

    return {"status": "ok", "message": "Job cancelled by admin"}

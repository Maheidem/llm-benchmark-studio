"""Job Registry for LLM Benchmark Studio.

Manages background job execution with per-user concurrency limits and queuing.
Jobs run as asyncio tasks, persist state to SQLite, and broadcast status via WebSocket.

Usage:
    from job_registry import registry

    # Register a handler for a job type
    registry.register_handler("benchmark", benchmark_handler)

    # Submit a job
    job_id = await registry.submit("benchmark", user_id, params)

    # Cancel a job
    await registry.cancel(job_id, user_id)
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Awaitable

import db

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


VALID_TRANSITIONS = {
    JobStatus.PENDING: {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED},
    JobStatus.DONE: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
    JobStatus.INTERRUPTED: set(),
}


def validate_transition(current: str, new: str) -> bool:
    """Check if a job status transition is valid."""
    try:
        current_status = JobStatus(current)
        new_status = JobStatus(new)
    except ValueError:
        return False
    return new_status in VALID_TRANSITIONS.get(current_status, set())


class JobRegistry:
    """Manages background job execution with per-user concurrency limits and queuing."""

    def __init__(self):
        self._running: dict[str, asyncio.Task] = {}           # job_id -> Task
        self._cancel_events: dict[str, asyncio.Event] = {}    # job_id -> Event
        self._user_slots: dict[str, int] = {}                 # user_id -> active count
        self._handlers: dict[str, Callable] = {}              # job_type -> handler func
        self._ws_manager = None                                # set via set_ws_manager()
        self._watchdog_task: asyncio.Task | None = None
        self._slot_lock = asyncio.Lock()                       # prevent race in slot accounting

    def set_ws_manager(self, manager):
        """Set the WebSocket connection manager for broadcasting."""
        self._ws_manager = manager

    def register_handler(self, job_type: str, handler: Callable):
        """Register a handler function for a job type.

        Handler signature:
            async def handler(
                job_id: str,
                params: dict,
                cancel_event: asyncio.Event,
                progress_cb: Callable[[int, str], Awaitable[None]],
            ) -> str | None:
                # Returns result_ref (e.g. benchmark_run row ID) on success, or None.
        """
        self._handlers[job_type] = handler

    async def startup(self):
        """Called during app lifespan startup."""
        logger.info("Job registry starting up")
        await self._startup_recovery()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def shutdown(self):
        """Called during app lifespan shutdown."""
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                logger.debug("Watchdog task cancelled during shutdown")
        # Cancel all running jobs and mark as interrupted
        for job_id, task in list(self._running.items()):
            task.cancel()
        # Wait briefly for tasks to finish their finally blocks
        if self._running:
            await asyncio.sleep(0.5)
        # Mark any remaining running jobs as interrupted in DB
        for job_id in list(self._running.keys()):
            await self._update_status(job_id, "interrupted")

    async def submit(
        self,
        job_type: str,
        user_id: str,
        params: dict,
        timeout_seconds: int = 7200,
        progress_detail: str = "",
    ) -> str:
        """Submit a new job. Returns job_id.

        If under concurrency limit -> starts immediately (status: running)
        If at limit -> queued (status: queued)
        """
        job_id = uuid.uuid4().hex

        async with self._slot_lock:
            # Check concurrency limit
            limit = await self._get_user_limit(user_id)
            active_count = self._user_slots.get(user_id, 0)
            initial_status = "queued" if active_count >= limit else "pending"

        # Insert into DB
        await db.create_job(
            job_id=job_id,
            user_id=user_id,
            job_type=job_type,
            status=initial_status,
            params_json=json.dumps(params),
            timeout_seconds=timeout_seconds,
            progress_detail=progress_detail,
        )

        logger.info("Job created: job_id=%s type=%s user_id=%s status=%s", job_id, job_type, user_id, initial_status)

        # Broadcast job_created
        await self._broadcast(user_id, {
            "type": "job_created",
            "job_id": job_id,
            "job_type": job_type,
            "status": initial_status,
            "progress_detail": progress_detail,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        if initial_status != "queued":
            await self._start_job(job_id, user_id, job_type, params, timeout_seconds)

        return job_id

    async def cancel(self, job_id: str, user_id: str, is_admin: bool = False) -> bool:
        """Cancel a job. Returns True if cancellation was initiated."""
        job = await db.get_job(job_id)
        if not job:
            return False
        if not is_admin and job["user_id"] != user_id:
            return False

        if job["status"] in ("pending", "queued"):
            # Not yet running -- just mark cancelled
            logger.info("Job cancelled (not yet running): job_id=%s user_id=%s", job_id, user_id)
            await self._update_status(job_id, "cancelled")
            await self._broadcast(job["user_id"], {
                "type": "job_cancelled", "job_id": job_id,
            })
            return True

        if job["status"] == "running":
            # Signal cancellation via event
            logger.info("Job cancel requested (running): job_id=%s user_id=%s", job_id, user_id)
            cancel_event = self._cancel_events.get(job_id)
            if cancel_event:
                cancel_event.set()
                return True
            # Ghost job: DB says running but no in-memory task/event (orphaned after restart)
            logger.warning("Ghost job detected (no cancel_event): job_id=%s, marking interrupted", job_id)
            await self._update_status(job_id, "interrupted")
            # Also clean up the linked tune run if applicable
            await self._cleanup_linked_tune_run(job)
            await self._broadcast(job["user_id"], {
                "type": "job_cancelled", "job_id": job_id,
            })
            return True

        return False  # Already terminal

    def get_cancel_event(self, job_id: str) -> asyncio.Event | None:
        """Get the cancel event for a running job (used by handlers)."""
        return self._cancel_events.get(job_id)

    def is_job_running(self, job_id: str) -> bool:
        """Check if a job is currently running as an asyncio task."""
        return job_id in self._running

    def get_active_count(self, user_id: str) -> int:
        """Get the number of active jobs for a user."""
        return self._user_slots.get(user_id, 0)

    # --- Internal methods ---

    async def _start_job(self, job_id, user_id, job_type, params, timeout_seconds):
        """Actually start executing a job as a background task."""
        handler = self._handlers.get(job_type)
        if not handler:
            await self._update_status(job_id, "failed", error_msg=f"No handler for {job_type}")
            await self._broadcast(user_id, {
                "type": "job_failed", "job_id": job_id, "error": f"No handler for {job_type}",
            })
            return

        cancel_event = asyncio.Event()
        self._cancel_events[job_id] = cancel_event

        # Validate transition to running (defensive: log warning but don't block)
        job = await db.get_job(job_id)
        if job and not validate_transition(job["status"], "running"):
            logger.warning(
                "Invalid job transition for %s: %s -> running", job_id, job["status"]
            )

        # Update status to running with start time and timeout
        now = datetime.now(timezone.utc)
        timeout_at = now + timedelta(seconds=timeout_seconds)
        await db.update_job_started(job_id, now.isoformat(), timeout_at.isoformat())

        async with self._slot_lock:
            self._user_slots[user_id] = self._user_slots.get(user_id, 0) + 1

        await self._broadcast(user_id, {
            "type": "job_started", "job_id": job_id, "job_type": job_type,
        })

        async def _run():
            try:
                # progress_cb: called by handler to report progress
                async def progress_cb(pct: int, detail: str = ""):
                    await db.update_job_progress(job_id, pct, detail)
                    await self._broadcast(user_id, {
                        "type": "job_progress",
                        "job_id": job_id,
                        "progress_pct": pct,
                        "progress_detail": detail,
                    })

                result_ref = await handler(job_id, params, cancel_event, progress_cb)

                if cancel_event.is_set():
                    await self._update_status(job_id, "cancelled")
                    await self._broadcast(user_id, {
                        "type": "job_cancelled", "job_id": job_id,
                    })
                else:
                    await self._update_status(job_id, "done", result_ref=result_ref)
                    await self._broadcast(user_id, {
                        "type": "job_completed", "job_id": job_id, "result_ref": result_ref,
                    })
            except asyncio.CancelledError:
                logger.debug("Job %s cancelled", job_id)
                await self._update_status(job_id, "interrupted")
                await self._broadcast(user_id, {
                    "type": "job_failed", "job_id": job_id, "error": "Interrupted",
                })
            except Exception as e:
                logger.exception("Job %s failed", job_id)
                await self._update_status(job_id, "failed", error_msg=str(e)[:500])
                await self._broadcast(user_id, {
                    "type": "job_failed", "job_id": job_id, "error": str(e)[:500],
                })
            finally:
                self._running.pop(job_id, None)
                self._cancel_events.pop(job_id, None)
                async with self._slot_lock:
                    self._user_slots[user_id] = max(
                        0, self._user_slots.get(user_id, 1) - 1
                    )
                # Process queue -- maybe another job can start now
                await self._process_queue(user_id)

        task = asyncio.create_task(_run())
        self._running[job_id] = task

    async def _process_queue(self, user_id: str):
        """Check if any queued jobs for this user can be started."""
        limit = await self._get_user_limit(user_id)

        while True:
            async with self._slot_lock:
                active = self._user_slots.get(user_id, 0)
                if active >= limit:
                    break

            # Get oldest queued job for this user
            job = await db.get_next_queued_job(user_id)
            if not job:
                break
            await self._start_job(
                job["id"], user_id, job["job_type"],
                json.loads(job["params_json"]), job["timeout_seconds"],
            )

    async def _startup_recovery(self):
        """On server restart, mark any running/pending/queued jobs as interrupted."""
        count = await db.mark_interrupted_jobs()
        if count > 0:
            logger.warning("Marked %d orphaned jobs as interrupted on startup", count)

    async def _watchdog(self):
        """Periodically check for timed-out jobs (every 60s)."""
        while True:
            try:
                await asyncio.sleep(60)
                timed_out = await db.get_timed_out_jobs()
                for job in timed_out:
                    job_id = job["id"]
                    # Cancel the asyncio task if still running
                    task = self._running.get(job_id)
                    if task:
                        task.cancel()
                    await self._update_status(job_id, "failed", error_msg="Timeout exceeded")
                    await self._broadcast(job["user_id"], {
                        "type": "job_failed", "job_id": job_id, "error": "Timeout exceeded",
                    })
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Watchdog error")

    async def _update_status(
        self,
        job_id: str,
        status: str,
        result_ref: str = None,
        error_msg: str = None,
    ):
        """Update job status in DB with optional terminal fields."""
        # Validate state transition (defensive: log warning but don't block)
        job = await db.get_job(job_id)
        if job:
            old_status = job["status"]
            if not validate_transition(old_status, status):
                logger.warning(
                    "Invalid job transition for %s: %s -> %s", job_id, old_status, status
                )
        completed_at = (
            datetime.now(timezone.utc).isoformat()
            if status in ("done", "failed", "cancelled", "interrupted")
            else None
        )
        logger.info("Job state transition: job_id=%s -> %s", job_id, status)
        await db.update_job_status(job_id, status, completed_at, result_ref, error_msg)

    async def _cleanup_linked_tune_run(self, job: dict):
        """If a job is linked to a param/prompt tune run, mark it interrupted too."""
        result_ref = job.get("result_ref")
        if not result_ref:
            return
        job_type = job.get("job_type", "")
        user_id = job["user_id"]
        try:
            if "param" in job_type:
                await db.update_param_tune_run(result_ref, user_id, status="interrupted")
                logger.info("Marked param_tune_run %s as interrupted (ghost cleanup)", result_ref)
            elif "prompt" in job_type:
                await db.update_prompt_tune_run(result_ref, user_id, status="interrupted")
                logger.info("Marked prompt_tune_run %s as interrupted (ghost cleanup)", result_ref)
        except Exception:
            logger.exception("Failed to clean up linked tune run %s", result_ref)

    async def _get_user_limit(self, user_id: str) -> int:
        """Get the user's max concurrent jobs from rate_limits table."""
        limit_row = await db.get_user_rate_limit(user_id)
        return limit_row.get("max_concurrent", 1) if limit_row else 1

    async def _broadcast(self, user_id: str, message: dict):
        """Send a WebSocket message to all tabs for a user."""
        if self._ws_manager:
            await self._ws_manager.send_to_user(user_id, message)


# Module-level singleton
registry = JobRegistry()

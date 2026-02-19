"""Scheduled benchmarks routes."""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from schemas import ScheduleCreate, ScheduleUpdate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["schedules"])

# Set by app.py after import
_run_scheduled_benchmark = None


@router.get("/api/schedules")
async def list_schedules(user: dict = Depends(auth.get_current_user)):
    """List the current user's scheduled benchmarks."""
    schedules = await db.get_user_schedules(user["id"])
    for s in schedules:
        if isinstance(s.get("models_json"), str):
            s["models"] = json.loads(s["models_json"])
            del s["models_json"]
    return {"schedules": schedules}


@router.post("/api/schedules")
async def create_schedule(request: Request, user: dict = Depends(auth.get_current_user)):
    """Create a new scheduled benchmark."""
    body = await request.json()

    # Validate via Pydantic
    try:
        validated = ScheduleCreate(
            name=body.get("name", ""),
            prompt=body.get("prompt", ""),
            models_json=body.get("models", []),
            max_tokens=body.get("max_tokens", 512),
            temperature=body.get("temperature", 0.7),
            interval_hours=body.get("interval_hours", 0),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    name = validated.name.strip()
    prompt = validated.prompt.strip()
    models = validated.models_json
    interval_hours = validated.interval_hours
    max_tokens = validated.max_tokens
    temperature = validated.temperature

    # next_run = now + interval
    next_run = (datetime.now(timezone.utc) + timedelta(hours=interval_hours)).strftime("%Y-%m-%d %H:%M:%S")

    schedule_id = await db.create_schedule(
        user_id=user["id"],
        name=name,
        prompt=prompt,
        models_json=json.dumps(models),
        max_tokens=max_tokens,
        temperature=temperature,
        interval_hours=interval_hours,
        next_run=next_run,
    )

    return {"status": "ok", "id": schedule_id}


@router.put("/api/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Update an existing scheduled benchmark."""
    body = await request.json()

    # Validate via Pydantic (all fields optional for update)
    try:
        validated = ScheduleUpdate(
            name=body.get("name"),
            prompt=body.get("prompt"),
            models_json=body.get("models"),
            max_tokens=body.get("max_tokens"),
            temperature=body.get("temperature"),
            interval_hours=body.get("interval_hours"),
            enabled=body.get("enabled"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    kwargs = {}
    if validated.name is not None:
        kwargs["name"] = validated.name
    if validated.prompt is not None:
        kwargs["prompt"] = validated.prompt
    if validated.models_json is not None:
        kwargs["models_json"] = json.dumps(validated.models_json)
    if validated.max_tokens is not None:
        kwargs["max_tokens"] = validated.max_tokens
    if validated.temperature is not None:
        kwargs["temperature"] = validated.temperature
    if validated.interval_hours is not None:
        kwargs["interval_hours"] = validated.interval_hours
        # Recalculate next_run when interval changes
        kwargs["next_run"] = (datetime.now(timezone.utc) + timedelta(hours=validated.interval_hours)).strftime("%Y-%m-%d %H:%M:%S")
    if validated.enabled is not None:
        kwargs["enabled"] = 1 if validated.enabled else 0

    updated = await db.update_schedule(schedule_id, user["id"], **kwargs)
    if not updated:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)

    return {"status": "ok"}


@router.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a scheduled benchmark."""
    deleted = await db.delete_schedule(schedule_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    return {"status": "ok"}


@router.post("/api/schedules/{schedule_id}/trigger")
async def trigger_schedule(schedule_id: str, user: dict = Depends(auth.get_current_user)):
    """Manually trigger a scheduled benchmark (run now)."""
    schedule = await db.get_schedule(schedule_id, user["id"])
    if not schedule:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)

    # Run in background so the HTTP response returns immediately
    async def _run():
        try:
            await _run_scheduled_benchmark(schedule)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            interval = schedule["interval_hours"]
            next_run = (datetime.now(timezone.utc) + timedelta(hours=interval)).strftime("%Y-%m-%d %H:%M:%S")
            await db.update_schedule_after_run(schedule["id"], now, next_run)
        except Exception:
            logger.exception("Trigger error running schedule %s", schedule["id"])

    asyncio.create_task(_run())
    return {"status": "ok", "message": "Benchmark triggered"}

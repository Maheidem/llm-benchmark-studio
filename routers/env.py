"""Env / .env file API key management routes (admin only)."""

import os
import re
import logging

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
from schemas import SAFE_ENV_VARS, EnvVarUpdate
from routers.helpers import _get_user_config, ENV_PATH, _mask_value

logger = logging.getLogger(__name__)

router = APIRouter(tags=["env"])


def _parse_env_file() -> list[tuple[str, str, str]]:
    """Parse .env file -> list of (key_name, value, raw_line). Skips comments/blanks."""
    entries = []
    if not ENV_PATH.exists():
        return entries
    for line in ENV_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', stripped)
        if match:
            entries.append((match.group(1), match.group(2), line))
    return entries


@router.get("/api/env")
async def get_env_keys(user: dict = Depends(auth.require_admin)):
    """List env keys with masked values."""
    entries = _parse_env_file()
    env_keys = {name: val for name, val, _ in entries}

    # Also include api_key_env refs from admin's config that may be missing from .env
    config = await _get_user_config(user["id"])
    for prov_cfg in config.get("providers", {}).values():
        ref = prov_cfg.get("api_key_env", "")
        if ref and ref not in env_keys:
            env_keys[ref] = ""

    keys = []
    for name, val in env_keys.items():
        keys.append({
            "name": name,
            "masked_value": _mask_value(val) if val else "",
            "is_set": bool(val),
        })
    return {"keys": keys}


@router.put("/api/env")
async def update_env_key(request: Request, user: dict = Depends(auth.require_admin)):
    """Update or add an env key in .env file."""
    body = await request.json()

    # Validate via Pydantic (includes SAFE_ENV_VARS whitelist check)
    try:
        validated = EnvVarUpdate(
            name=body.get("name", ""),
            value=body.get("value", ""),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    name = validated.name
    value = validated.value

    # Read existing .env, preserving comments/order
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()

    # Try to update existing key
    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', stripped)
        if match and match.group(1) == name:
            lines[i] = f"{name}={value}"
            updated = True
            break

    if not updated:
        lines.append(f"{name}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n")

    # Reload into current process
    os.environ[name] = value

    return {"status": "ok", "name": name, "masked_value": _mask_value(value)}


@router.delete("/api/env")
async def delete_env_key(request: Request, user: dict = Depends(auth.require_admin)):
    """Remove an env key from .env file."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    if not ENV_PATH.exists():
        return JSONResponse({"error": "No .env file"}, status_code=404)

    lines = ENV_PATH.read_text().splitlines()
    new_lines = []
    removed = False
    for line in lines:
        stripped = line.strip()
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', stripped)
        if match and match.group(1) == name:
            removed = True
            continue
        new_lines.append(line)

    if not removed:
        return JSONResponse({"error": f"Key '{name}' not found"}, status_code=404)

    ENV_PATH.write_text("\n".join(new_lines) + "\n")
    os.environ.pop(name, None)
    return {"status": "ok"}

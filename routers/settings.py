"""Phase 10 settings API routes."""

import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

import auth
from routers.helpers import _get_user_config, _save_user_config, PHASE10_DEFAULTS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])


@router.get("/api/settings/phase10")
async def get_phase10_settings(user: dict = Depends(auth.get_current_user)):
    """Return Phase 10 feature settings (judge, param tuner, prompt tuner, param_support)."""
    config = await _get_user_config(user["id"])
    return {
        "judge": {**PHASE10_DEFAULTS["judge"], **config.get("judge_defaults", {})},
        "param_tuner": {**PHASE10_DEFAULTS["param_tuner"], **config.get("param_tuner_defaults", {})},
        "prompt_tuner": {**PHASE10_DEFAULTS["prompt_tuner"], **config.get("prompt_tuner_defaults", {})},
        "param_support": config.get("param_support_defaults", None),
    }


@router.put("/api/settings/phase10")
async def save_phase10_settings(request: Request, user: dict = Depends(auth.get_current_user)):
    """Save Phase 10 feature settings."""
    body = await request.json()
    config = await _get_user_config(user["id"])

    # Validate and merge each section
    allowed_sections = {"judge", "param_tuner", "prompt_tuner"}
    for section in allowed_sections:
        if section in body and isinstance(body[section], dict):
            config_key = f"{section}_defaults"
            section_data = body[section]

            # Validate param_tuner.presets if present
            if section == "param_tuner" and "presets" in section_data:
                presets = section_data["presets"]
                if not isinstance(presets, list):
                    return JSONResponse({"error": "presets must be an array"}, status_code=400)
                if len(presets) > 20:
                    return JSONResponse({"error": "Maximum 20 presets allowed"}, status_code=400)
                for i, preset in enumerate(presets):
                    if not isinstance(preset, dict):
                        return JSONResponse({"error": f"Preset {i} must be an object"}, status_code=400)
                    if not preset.get("name") or not isinstance(preset["name"], str):
                        return JSONResponse({"error": f"Preset {i} must have a non-empty 'name' string"}, status_code=400)
                    if not isinstance(preset.get("search_space"), dict):
                        return JSONResponse({"error": f"Preset {i} must have a 'search_space' object"}, status_code=400)

            config[config_key] = {**PHASE10_DEFAULTS[section], **section_data}

    # Handle param_support separately (not in PHASE10_DEFAULTS, stored as-is)
    if "param_support" in body:
        ps = body["param_support"]
        if ps is None:
            # Allow clearing param_support
            config.pop("param_support_defaults", None)
        elif isinstance(ps, dict):
            # Validate structure
            if not isinstance(ps.get("provider_defaults", {}), dict):
                return JSONResponse({"error": "param_support.provider_defaults must be an object"}, status_code=400)
            if not isinstance(ps.get("model_overrides", {}), dict):
                return JSONResponse({"error": "param_support.model_overrides must be an object"}, status_code=400)
            config["param_support_defaults"] = ps
        else:
            return JSONResponse({"error": "param_support must be an object or null"}, status_code=400)

    await _save_user_config(user["id"], config)
    return {"ok": True}

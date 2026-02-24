"""Prompt tuning routes (quick & evolutionary modes)."""

import asyncio
import json
import logging

import litellm
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from benchmark import Target, build_targets
from schemas import PromptTuneRequest
from job_registry import registry as job_registry
from provider_params import build_litellm_kwargs
from routers.helpers import (
    _get_user_config,
    _parse_target_selection,
    _filter_targets,
    _find_target,
    _check_rate_limit,
    _parse_meta_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["prompt_tune"])

# Module-level ws_manager -- set by app.py after import
ws_manager = None


# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

_QUICK_META_PROMPT = """You are a prompt engineering expert. Your job is to create {n} distinct system prompt variations for an LLM that will use tool calling.

CONTEXT:
- The LLM will use these tools: {tools_summary}
- Test scenarios it must handle: {test_cases_summary}
- Base prompt to improve upon: "{base_prompt}"

Generate {n} variations, each with a DIFFERENT approach. Vary structure, tone, and emphasis -- not just word choice. Each prompt must instruct the model to use the provided tools. Keep prompts under 500 tokens each.

Suggested styles: concise & direct, detailed & explicit, structured (numbered rules), conversational, technical/specification-like.

Return a JSON object with a "prompts" key containing an array:
{{"prompts": [{{"style": "concise", "prompt": "..."}}, ...]}}
Do not include any text before or after the JSON object."""

_EVO_META_PROMPT = """You are a prompt evolution expert. You are mutating winning system prompts to create the next generation.

PARENT PROMPTS (these scored highest in the previous generation):
{parent_prompts}

CONTEXT:
- Tools available: {tools_summary}
- Test scenarios: {test_cases_summary}

TASK:
Create {n} new variations by mutating the parent prompts. For each:
- Keep the core intent that made the parent successful
- Change structure, phrasing, emphasis, or add/remove instructions
- Try at least one bold mutation (significantly different approach)
- Try at least one conservative mutation (small refinement of best parent)

Return a JSON object with a "prompts" key containing an array:
{{"prompts": [{{"parent_index": 0, "mutation_type": "bold", "prompt": "..."}}, ...]}}
Do not include any text before or after the JSON object."""

_DEFAULT_BASE_PROMPT = "You are a helpful assistant that uses tools to answer questions. When the user asks you to perform an action, use the appropriate tool."


# ---------------------------------------------------------------------------
# Meta-model caller
# ---------------------------------------------------------------------------

_META_RETRYABLE_ERRORS = (
    litellm.exceptions.BadGatewayError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.Timeout,
)


async def _generate_prompts_meta(
    meta_target: Target,
    prompt_template: str,
    *,
    _max_retries: int = 3,
    _base_delay: float = 2.0,
    **format_kwargs,
) -> list[dict]:
    """Call meta-model to generate prompt variations. Returns parsed list.

    Retries transient errors (502/503/500/connection/timeout) with exponential
    backoff. Retries once on empty/unparseable response.
    """
    formatted = prompt_template.format(**format_kwargs)

    kwargs = {
        "model": meta_target.model_id,
        "messages": [{"role": "user", "content": formatted}],
        "timeout": 120,
        "num_retries": 0,  # We handle retries ourselves with backoff
    }
    # Only add structured output for providers that LiteLLM confirms support it.
    # Others (exo, ZAI, Ollama, unknown) rely on the prompt instructions + parser.
    if litellm.supports_response_schema(model=meta_target.model_id):
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "prompt_list",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "prompts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "style": {"type": "string"},
                                    "prompt": {"type": "string"},
                                },
                                "required": ["style", "prompt"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["prompts"],
                    "additionalProperties": False,
                },
            },
        }
    if meta_target.api_base:
        kwargs["api_base"] = meta_target.api_base
    if meta_target.api_key:
        kwargs["api_key"] = meta_target.api_key
    # Use build_litellm_kwargs for provider-aware param handling
    # (e.g. O-series models skip temperature, skip_params honoured)
    kwargs.update(build_litellm_kwargs(
        meta_target, temperature=0.9, max_tokens=4096,
    ))

    last_exc: Exception | None = None
    _json_mode_supported = "response_format" in kwargs  # Only true when we actually added it

    # Diagnostic: log exact kwargs (sans messages content) for debugging call routing
    _diag = {k: v for k, v in kwargs.items() if k != "messages"}
    _diag["api_key"] = "***" if kwargs.get("api_key") else None
    _diag["has_response_format"] = "response_format" in kwargs
    logger.info("Meta model call kwargs: %s", _diag)

    for attempt in range(1, _max_retries + 1):
        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content or ""
            prompts = _parse_meta_response(content)
            if prompts:
                return prompts
            # Empty response -- retry once with backoff
            logger.warning(
                "Meta model returned no parseable prompts (attempt %d/%d, content_len=%d)",
                attempt, _max_retries, len(content),
            )
            if attempt < _max_retries:
                await asyncio.sleep(_base_delay * (2 ** (attempt - 1)))
                continue
            return []  # All retries exhausted with empty results
        except litellm.exceptions.BadRequestError as exc:
            # Some models don't support response_format -- retry without it
            if _json_mode_supported and "response_format" in kwargs:
                logger.info(
                    "Meta model rejected response_format (attempt %d/%d): %s -- retrying without JSON mode",
                    attempt, _max_retries, exc,
                )
                _json_mode_supported = False
                kwargs.pop("response_format", None)
                continue  # Don't count this as a retry attempt
            raise
        except _META_RETRYABLE_ERRORS as exc:
            last_exc = exc
            # Some models (e.g. LM Studio) crash on response_format instead
            # of returning a clean 400 -- strip it on first transient failure
            if _json_mode_supported and "response_format" in kwargs:
                logger.info(
                    "Meta model transient error with JSON mode (attempt %d/%d): %s -- disabling response_format and retrying",
                    attempt, _max_retries, exc,
                )
                _json_mode_supported = False
                kwargs.pop("response_format", None)
                await asyncio.sleep(_base_delay)
                continue  # Don't count this as a retry attempt
            if attempt < _max_retries:
                delay = _base_delay * (2 ** (attempt - 1))
                logger.info(
                    "Meta model transient error (attempt %d/%d): %s -- retrying in %.1fs",
                    attempt, _max_retries, exc, delay,
                )
                await asyncio.sleep(delay)
                continue
            raise  # Last attempt -- propagate

    # Should not reach here, but safety net
    if last_exc:
        raise last_exc
    return []


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@router.post("/api/tool-eval/prompt-tune")
async def run_prompt_tune(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run a prompt tuning session (Quick or Evolutionary mode) via job registry.

    Progress is delivered via WebSocket (tune_start, prompt_eval_result, etc.).
    Returns job_id immediately.
    """
    body = await request.json()

    # Support precise target selection for eval targets
    _target_models_body = {"targets": body.get("target_targets"), "models": body.get("target_models", [])}
    target_model_ids, target_set_eval = _parse_target_selection(_target_models_body)

    # Validate core fields via Pydantic
    try:
        validated = PromptTuneRequest(
            suite_id=body.get("suite_id", ""),
            mode=body.get("mode", "quick"),
            target_models=target_model_ids or [],
            meta_model=body.get("meta_model", ""),
            base_prompt=body.get("base_prompt"),
            config=body.get("config"),
            experiment_id=body.get("experiment_id"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    suite_id = validated.suite_id
    meta_model_id = validated.meta_model
    meta_provider_key = body.get("meta_provider_key")
    mode = validated.mode
    base_prompt = validated.base_prompt or _DEFAULT_BASE_PROMPT
    cfg = validated.config or {}

    # Load suite + test cases (validate before submitting job)
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(suite_id)
    if not cases:
        return JSONResponse({"error": "Suite has no test cases"}, status_code=400)

    # Rate limit check (raises HTTPException 429 if exceeded)
    await _check_rate_limit(user["id"])

    # Build targets (validate models exist in config)
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)

    # Find meta-model target
    meta_targets = _find_target(all_targets, meta_model_id, meta_provider_key)
    if not meta_targets:
        return JSONResponse({"error": f"Meta model '{meta_model_id}' not found in config"}, status_code=400)

    # Find eval targets
    eval_targets = _filter_targets(all_targets, target_model_ids, target_set_eval)
    if not eval_targets:
        return JSONResponse({"error": "No matching target models found in config"}, status_code=400)

    # Build progress detail
    model_count = len(eval_targets)
    progress_detail = f"Prompt Tune: {mode}, {model_count} model{'s' if model_count != 1 else ''}, {suite['name']}"

    # Submit to job registry
    experiment_id = body.get("experiment_id")
    job_params = {
        "user_id": user["id"],
        "suite_id": suite_id,
        "target_models": target_model_ids,
        "target_set": [list(t) for t in target_set_eval] if target_set_eval else None,
        "meta_model": meta_model_id,
        "meta_provider_key": meta_provider_key,
        "mode": mode,
        "base_prompt": base_prompt,
        "config": cfg,
        "experiment_id": experiment_id,
    }

    job_id = await job_registry.submit(
        job_type="prompt_tune",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@router.post("/api/tool-eval/prompt-tune/cancel")
async def cancel_prompt_tune(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running prompt tune via job registry."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = body.get("job_id")

    if job_id:
        cancelled = await job_registry.cancel(job_id, user["id"])
        if cancelled:
            return {"status": "ok", "message": "Cancellation requested"}
        return JSONResponse({"error": "Job not found or already finished"}, status_code=404)

    return JSONResponse({"error": "job_id is required"}, status_code=400)


@router.get("/api/tool-eval/prompt-tune/estimate")
async def estimate_prompt_tune(
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Get cost/time estimate before running prompt tuning."""
    suite_id = request.query_params.get("suite_id", "")
    mode = request.query_params.get("mode", "quick")
    population_size = int(request.query_params.get("population_size", "5"))
    generations = int(request.query_params.get("generations", "1" if mode == "quick" else "3"))
    num_models = int(request.query_params.get("num_models", "1"))

    if mode == "quick":
        generations = 1

    total_prompts = population_size * generations

    # Count test cases
    num_cases = 0
    if suite_id:
        cases = await db.get_test_cases(suite_id)
        num_cases = len(cases) if cases else 0

    total_eval_calls = total_prompts * num_cases * num_models
    total_meta_calls = generations  # One meta-call per generation
    total_api_calls = total_meta_calls + total_eval_calls

    # Rough estimate: ~2s per eval call, ~5s per meta call
    estimated_s = total_meta_calls * 5 + total_eval_calls * 2

    warning = None
    if total_api_calls > 100:
        warning = f"This will make {total_api_calls} API calls. Consider reducing population or generations."

    return {
        "total_prompt_generations": total_prompts,
        "total_eval_calls": total_eval_calls,
        "total_api_calls": total_api_calls,
        "estimated_duration_s": estimated_s,
        "warning": warning,
    }


@router.get("/api/tool-eval/prompt-tune/history")
async def get_prompt_tune_history(user: dict = Depends(auth.get_current_user)):
    """List user's prompt tune runs."""
    runs = await db.get_prompt_tune_runs(user["id"])
    return {"runs": runs}


@router.get("/api/tool-eval/prompt-tune/history/{tune_id}")
async def get_prompt_tune_detail(tune_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full prompt tune run details."""
    run = await db.get_prompt_tune_run(tune_id, user["id"])
    if not run:
        return JSONResponse({"error": "Tune run not found"}, status_code=404)
    return run


@router.delete("/api/tool-eval/prompt-tune/history/{tune_id}")
async def delete_prompt_tune(tune_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a prompt tune run."""
    deleted = await db.delete_prompt_tune_run(tune_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Tune run not found"}, status_code=404)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# I2: Auto-Optimize endpoint
# ---------------------------------------------------------------------------

@router.post("/api/tool-eval/prompt-tune/auto-optimize")
async def run_prompt_auto_optimize(request: Request, user: dict = Depends(auth.get_current_user)):
    """I2: Run OPRO/APE-style prompt auto-optimization via job registry.

    Iteratively generates prompt variants using a meta-model, evaluates them
    against a test suite, and feeds top performers back for the next round.
    Results are saved to the prompt_versions registry.

    Body:
        suite_id: str — test suite to evaluate against
        target_models: list[str] — model IDs to evaluate prompts on
        meta_model: str — model ID to use for prompt generation
        meta_provider_key: str (optional) — provider key for meta model
        base_prompt: str (optional) — starting prompt (default generic)
        max_iterations: int (1-10, default 3) — number of optimization rounds
        population_size: int (3-20, default 5) — prompts per iteration
        selection_ratio: float (0.2-0.8, default 0.4) — fraction to keep as parents
        eval_temperature: float (default 0.0) — temperature for eval calls
        eval_tool_choice: str (default "required") — tool_choice for eval calls
        experiment_id: str (optional) — auto-promote best prompt to experiment

    Returns: {job_id, status: "submitted"}
    """
    body = await request.json()

    suite_id = body.get("suite_id", "")
    if not suite_id:
        return JSONResponse({"error": "suite_id is required"}, status_code=400)

    # Support precise target selection
    _target_models_body = {"targets": body.get("target_targets"), "models": body.get("target_models", [])}
    target_model_ids, target_set_eval = _parse_target_selection(_target_models_body)

    meta_model_id = body.get("meta_model", "")
    if not meta_model_id:
        return JSONResponse({"error": "meta_model is required"}, status_code=400)

    if not target_model_ids:
        return JSONResponse({"error": "target_models must be a non-empty list"}, status_code=400)

    # Validate suite exists + has test cases
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(suite_id)
    if not cases:
        return JSONResponse({"error": "Suite has no test cases"}, status_code=400)

    # Rate limit check
    await _check_rate_limit(user["id"])

    # Validate targets exist in config
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)

    meta_targets = _find_target(all_targets, meta_model_id, body.get("meta_provider_key"))
    if not meta_targets:
        return JSONResponse({"error": f"Meta model '{meta_model_id}' not found in config"}, status_code=400)

    eval_targets = _filter_targets(all_targets, target_model_ids, target_set_eval)
    if not eval_targets:
        return JSONResponse({"error": "No matching target models found in config"}, status_code=400)

    # Clamp config params
    max_iterations = max(1, min(int(body.get("max_iterations", 3)), 10))
    population_size = max(3, min(int(body.get("population_size", 5)), 20))

    progress_detail = (
        f"Auto-Optimize: {max_iterations} iter, {len(eval_targets)} model"
        f"{'s' if len(eval_targets) != 1 else ''}, {suite['name']}"
    )

    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "suite_id": suite_id,
        "target_models": target_model_ids,
        "target_set": [list(t) for t in target_set_eval] if target_set_eval else None,
        "meta_model": meta_model_id,
        "meta_provider_key": body.get("meta_provider_key"),
        "base_prompt": body.get("base_prompt"),
        "max_iterations": max_iterations,
        "population_size": population_size,
        "selection_ratio": float(body.get("selection_ratio", 0.4)),
        "eval_temperature": float(body.get("eval_temperature", 0.0)),
        "eval_tool_choice": body.get("eval_tool_choice", "required"),
        "experiment_id": body.get("experiment_id"),
    }

    job_id = await job_registry.submit(
        job_type="prompt_auto_optimize",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@router.post("/api/tool-eval/prompt-tune/auto-optimize/cancel")
async def cancel_prompt_auto_optimize(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running prompt auto-optimize job."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = body.get("job_id")

    if not job_id:
        return JSONResponse({"error": "job_id is required"}, status_code=400)

    cancelled = await job_registry.cancel(job_id, user["id"])
    if cancelled:
        return {"status": "ok", "message": "Cancellation requested"}
    return JSONResponse({"error": "Job not found or already finished"}, status_code=404)

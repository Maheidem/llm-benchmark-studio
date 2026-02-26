"""Shared helpers, state, and utilities used across multiple routers.

This module centralizes:
- Per-user config management (get/save)
- Target selection/filtering helpers
- Concurrency guards (locks, cancel events)
- Rate limiting
- Key injection
- Scoring functions
- Eval engine helpers
- Aggregation and SSE utilities
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import replace
from pathlib import Path

import litellm

from benchmark import (
    AggregatedResult,
    RunResult,
    Target,
    _compute_variance,
    build_targets,
    generate_context_text,
    run_single,
    save_results,
    sanitize_error,
)
import auth
import db
from keyvault import vault
from provider_params import (
    PROVIDER_REGISTRY,
    identify_provider,
    validate_params,
    build_litellm_kwargs,
)

logger = logging.getLogger(__name__)

_dir = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Per-user config: default config for new users + DB helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "defaults": {
        "max_tokens": 512,
        "temperature": 0.7,
        "context_tiers": [0],
        "prompt": "Explain the concept of recursion in programming. Include a simple example in Python with comments.",
    },
    "prompt_templates": {
        "recursion": {
            "category": "reasoning",
            "label": "Explain Recursion",
            "prompt": "Explain the concept of recursion in programming. Include a simple example in Python with comments.",
        },
        "code_generation": {
            "category": "code",
            "label": "Generate Sorting Algorithm",
            "prompt": "Write a Python function that implements merge sort. Include type hints and docstrings.",
        },
        "creative": {
            "category": "creative",
            "label": "Short Story",
            "prompt": "Write a short story (300 words) about a robot discovering nature for the first time.",
        },
        "qa": {
            "category": "short_qa",
            "label": "Quick Q&A",
            "prompt": "What are the three main types of machine learning? Explain each in one sentence.",
        },
    },
    "providers": {
        "openai": {
            "display_name": "OpenAI",
            "api_key_env": "OPENAI_API_KEY",
            "models": [
                {"id": "gpt-4o", "display_name": "GPT-4o", "context_window": 128000},
                {"id": "gpt-4o-mini", "display_name": "GPT-4o Mini", "context_window": 128000},
            ],
        },
        "anthropic": {
            "display_name": "Anthropic",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model_id_prefix": "anthropic",
            "models": [
                {
                    "id": "anthropic/claude-sonnet-4-5",
                    "display_name": "Claude Sonnet 4.5",
                    "context_window": 200000,
                    "skip_params": ["temperature"],
                },
            ],
        },
        "google_gemini": {
            "display_name": "Google Gemini",
            "api_key_env": "GEMINI_API_KEY",
            "model_id_prefix": "gemini",
            "models": [
                {
                    "id": "gemini/gemini-2.5-flash",
                    "display_name": "Gemini 2.5 Flash",
                    "context_window": 1000000,
                },
            ],
        },
    },
}


async def _get_user_config(user_id: str) -> dict:
    """Build config dict from normalized providers/models tables + user_configs settings.

    The ``providers`` key is always read from the providers/models tables (source of truth).
    All other settings (defaults, prompt_templates, judge_settings, *_defaults) are read
    from the legacy user_configs table so existing features remain backward-compatible.
    """
    # 1. Non-provider settings from user_configs (defaults, prompt_templates, judge_settings, etc.)
    settings = await db.get_user_config(user_id)
    if settings is None:
        # New user: save default settings (no provider seeding — onboarding handles that)
        settings = {k: v for k, v in DEFAULT_CONFIG.items() if k != "providers"}
        await db.save_user_config(user_id, settings)

    config = dict(settings)

    # 2. Build providers section from normalized tables (always authoritative)
    providers = await db.get_providers(user_id)

    config["providers"] = {}
    for p in providers:
        if not p.get("is_active", 1):
            continue
        models = await db.get_models_for_provider(p["id"])
        config["providers"][p["key"]] = {
            "display_name": p["name"],
            **({"api_base": p["api_base"]} if p.get("api_base") else {}),
            **({"api_key_env": p["api_key_env"]} if p.get("api_key_env") else {}),
            **({"model_id_prefix": p["model_prefix"]} if p.get("model_prefix") else {}),
            "models": [
                {
                    "id": m["litellm_id"],
                    "display_name": m["display_name"],
                    "context_window": m.get("context_window", 128000),
                    **({"max_output_tokens": m["max_output_tokens"]} if m.get("max_output_tokens") else {}),
                    **({"skip_params": json.loads(m["skip_params"])} if m.get("skip_params") and m["skip_params"] != "[]" else {}),
                }
                for m in models
            ],
        }

    return config


async def _save_user_config(user_id: str, config: dict):
    """Save config: provider/model changes go to normalized tables, everything else to user_configs.

    The ``providers`` key is written to the providers/models tables.
    All other keys (defaults, prompt_templates, judge_settings, *_defaults) are saved
    to the user_configs table as before.
    """
    # 1. Persist providers/models to normalized tables if present
    incoming_providers = config.get("providers")
    if incoming_providers is not None:
        existing_providers = await db.get_providers(user_id)
        existing_by_key = {p["key"]: p for p in existing_providers}
        seen_keys = set()

        for prov_key, prov_cfg in incoming_providers.items():
            seen_keys.add(prov_key)
            existing = existing_by_key.get(prov_key)

            if existing:
                # Update existing provider
                await db.update_provider(
                    existing["id"],
                    name=prov_cfg.get("display_name", prov_key),
                    api_base=prov_cfg.get("api_base"),
                    api_key_env=prov_cfg.get("api_key_env"),
                    model_prefix=prov_cfg.get("model_id_prefix"),
                )
                # Sync models
                existing_models = await db.get_models_for_provider(existing["id"])
                existing_models_by_id = {m["litellm_id"]: m for m in existing_models}
                incoming_model_ids = set()

                for model in prov_cfg.get("models", []):
                    model_litellm_id = model["id"]
                    incoming_model_ids.add(model_litellm_id)
                    skip_params_str = json.dumps(model.get("skip_params", []))

                    if model_litellm_id in existing_models_by_id:
                        # Update existing model
                        em = existing_models_by_id[model_litellm_id]
                        await db.update_model(
                            em["id"],
                            display_name=model.get("display_name", model_litellm_id),
                            context_window=model.get("context_window", 128000),
                            max_output_tokens=model.get("max_output_tokens"),
                            skip_params=skip_params_str,
                        )
                    else:
                        # Add new model
                        await db.create_model(
                            provider_id=existing["id"],
                            litellm_id=model_litellm_id,
                            display_name=model.get("display_name", model_litellm_id),
                            context_window=model.get("context_window", 128000),
                            max_output_tokens=model.get("max_output_tokens"),
                            skip_params=skip_params_str,
                        )

                # Soft-delete models removed from config
                for litellm_id, em in existing_models_by_id.items():
                    if litellm_id not in incoming_model_ids:
                        await db.delete_model(em["id"])
            else:
                # Create new provider
                provider_id = await db.create_provider(
                    user_id=user_id,
                    key=prov_key,
                    name=prov_cfg.get("display_name", prov_key),
                    api_base=prov_cfg.get("api_base"),
                    api_key_env=prov_cfg.get("api_key_env"),
                    model_prefix=prov_cfg.get("model_id_prefix"),
                )
                for model in prov_cfg.get("models", []):
                    await db.create_model(
                        provider_id=provider_id,
                        litellm_id=model["id"],
                        display_name=model.get("display_name", model["id"]),
                        context_window=model.get("context_window", 128000),
                        max_output_tokens=model.get("max_output_tokens"),
                        skip_params=json.dumps(model.get("skip_params", [])),
                    )

        # Delete providers removed from config
        for prov_key, existing in existing_by_key.items():
            if prov_key not in seen_keys:
                await db.delete_provider(existing["id"])

    # 2. Save non-provider settings to user_configs
    settings = {k: v for k, v in config.items() if k != "providers"}
    await db.save_user_config(user_id, settings)


# ---------------------------------------------------------------------------
# Target selection and filtering
# ---------------------------------------------------------------------------


def _parse_target_selection(body: dict) -> tuple[list[str], set[tuple[str, str]] | None]:
    """Parse model/target selection from request body.

    Supports two formats:
      1. New: ``targets: [{"provider_key": "...", "model_id": "..."}, ...]``
         -> Returns (model_ids, target_set) where target_set is a set of
           (provider_key, model_id) tuples for precise matching.
      2. Legacy: ``models: ["model_id_1", ...]``
         -> Returns (model_ids, None) for backward-compatible model_id-only matching.

    Returns:
        (model_ids, target_set):
            model_ids: flat list of model_id strings (for logging / combo count).
            target_set: set of (provider_key, model_id) or None for legacy mode.
    """
    targets_list = body.get("targets")
    if targets_list and isinstance(targets_list, list):
        target_set: set[tuple[str, str]] = set()
        model_ids: list[str] = []
        for entry in targets_list:
            if isinstance(entry, dict) and "provider_key" in entry and "model_id" in entry:
                target_set.add((entry["provider_key"], entry["model_id"]))
                model_ids.append(entry["model_id"])
        if target_set:
            return model_ids, target_set
    # Fallback to legacy flat list
    model_ids = body.get("models", [])
    return model_ids, None


def _filter_targets(all_targets: list[Target], model_ids: list[str],
                    target_set: set[tuple[str, str]] | None) -> list[Target]:
    """Filter all_targets using precise (provider_key, model_id) or legacy model_id matching."""
    if target_set:
        return [t for t in all_targets if (t.provider_key, t.model_id) in target_set]
    if model_ids:
        return [t for t in all_targets if t.model_id in model_ids]
    return all_targets


def _target_key(target: Target) -> str:
    """Return a unique key for a target: 'provider_key::model_id'."""
    return f"{target.provider_key or ''}::{target.model_id}"


def _find_target(all_targets: list[Target], model_id: str,
                 provider_key: str | None = None) -> list[Target]:
    """Find targets by model_id, optionally qualified by provider_key."""
    if provider_key:
        return [t for t in all_targets if t.provider_key == provider_key and t.model_id == model_id]
    return [t for t in all_targets if t.model_id == model_id]


# ---------------------------------------------------------------------------
# Per-user concurrency guards
# ---------------------------------------------------------------------------

_user_locks: dict[str, asyncio.Lock] = {}
_user_cancel: dict[str, asyncio.Event] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _get_user_cancel(user_id: str) -> asyncio.Event:
    if user_id not in _user_cancel:
        _user_cancel[user_id] = asyncio.Event()
    return _user_cancel[user_id]


# ---------------------------------------------------------------------------
# Rate limiter (DB-backed via jobs table)
# ---------------------------------------------------------------------------


async def _check_rate_limit(user_id: str):
    """Check if user has exceeded their rate limit. Uses DB for persistence.

    Raises HTTPException(429) if limit exceeded. Returns None on success.
    """
    from fastapi import HTTPException

    # Get user's rate limit config
    limits = await db.get_user_rate_limit(user_id)
    max_per_hour = limits["benchmarks_per_hour"] if limits else 20
    max_concurrent = limits["max_concurrent"] if limits else 1

    # Check concurrent jobs
    active = await db.get_user_active_job_count(user_id)
    if active >= max_concurrent:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent jobs ({active}/{max_concurrent}). Wait for current jobs to finish.",
        )

    # Check hourly limit
    recent = await db.get_user_recent_job_count(user_id, hours=1)
    if recent >= max_per_hour:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({recent}/{max_per_hour} per hour). Try again later.",
        )


# ---------------------------------------------------------------------------
# Key injection
# ---------------------------------------------------------------------------


def inject_user_keys(targets: list[Target], user_keys_cache: dict[str, str]) -> list[Target]:
    """Clone targets with user-specific API keys injected.

    Key resolution: user key > global key (already on target).
    Returns a NEW list of Target objects (originals are not mutated).
    """
    injected = []
    for target in targets:
        if not target.provider_key:
            injected.append(target)
            continue

        encrypted = user_keys_cache.get(target.provider_key)
        if encrypted:
            try:
                decrypted = vault.decrypt(encrypted)
                injected.append(replace(target, api_key=decrypted))
                continue
            except Exception:
                logger.warning("API key decryption failed for provider=%s, falling back to global key", target.provider_key)

        # No user key found -- keep the global key (already on target)
        injected.append(target)

    return injected


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T1: Format compliance classification
# ---------------------------------------------------------------------------

# Valid BFCL error types for T2 (locked taxonomy, priority order)
_ERROR_TYPES = [
    "tool_hallucination",       # called a tool that doesn't exist in the tool set
    "argument_hallucination",   # passed arguments with made-up values
    "invalid_invocation",       # invalid JSON, missing required fields, malformed structure
    "partial_execution",        # only partially completed the expected tool chain
    "output_hallucination",     # invented output values that weren't in context
    "invalid_reasoning",        # intermediate reasoning step was logically invalid
    "reentrant_failure",        # re-entered a failed tool call without handling error
    "unclassified",             # catch-all when no specific type matches
]


def classify_format_compliance(
    raw_response_had_tool_calls: bool,
    tool_name_was_json_blob: bool,
    params_parse_failed: bool,
    actual_tool: str | None,
    expected_tool,
) -> str:
    """T1: Classify JSON normalization compliance.

    Returns "PASS", "NORMALIZED", or "FAIL":
    - PASS: model returned well-formed tool calls natively
    - NORMALIZED: tool call extracted via fallback JSON parsing
    - FAIL: no usable tool call produced (error case or missing)
    """
    if actual_tool is None:
        # If no expected tool, PASS (correct abstention)
        if expected_tool is None:
            return "PASS"
        return "FAIL"

    if not raw_response_had_tool_calls and actual_tool:
        # Tool found via JSON normalization path (content parsing)
        return "NORMALIZED"

    if tool_name_was_json_blob or params_parse_failed:
        # Needed to extract from JSON blob in tool name
        return "NORMALIZED"

    return "PASS"


def classify_error_type(
    success: bool,
    actual_tool: str | None,
    actual_params: dict | None,
    expected_tool,
    expected_params: dict | None,
    tool_names_in_suite: set[str],
    overall_score: float,
    params_parse_failed: bool = False,
    is_multi_turn: bool = False,
    rounds_used: int = 0,
    optimal_hops: int = 1,
) -> str | None:
    """T2: Classify failure into one of 8 error types (locked taxonomy).

    Returns error type string or None if no error (passing case).
    Priority order: tool_hallucination → argument_hallucination → invalid_invocation
      → partial_execution → output_hallucination → invalid_reasoning
      → reentrant_failure → unclassified
    """
    if overall_score == 1.0 and success:
        return None  # Passing case, no error

    # invalid_invocation: API-level failure or params could not be parsed at all
    # (invalid JSON, missing required fields, malformed structure)
    if not success or params_parse_failed:
        return "invalid_invocation"

    # tool_hallucination: model called a tool not in the suite
    if actual_tool and tool_names_in_suite and actual_tool.lower() not in tool_names_in_suite:
        return "tool_hallucination"

    # argument_hallucination: correct tool but wrong/missing argument values
    if actual_tool and expected_tool:
        tool_match = (
            actual_tool.lower() in [e.lower() for e in expected_tool]
            if isinstance(expected_tool, list)
            else actual_tool.lower() == expected_tool.lower()
        )
        if tool_match and expected_params:
            return "argument_hallucination"

    # reentrant_failure: multi-turn case that exceeded rounds
    if is_multi_turn and rounds_used >= optimal_hops * 2:
        return "reentrant_failure"

    # partial_execution: multi-turn case that didn't complete all hops
    if is_multi_turn and rounds_used > 0:
        return "partial_execution"

    # invalid_reasoning: wrong tool selected (not a hallucination, just wrong choice)
    if actual_tool and expected_tool:
        return "invalid_reasoning"

    # unclassified: catch-all when no specific type matches
    return "unclassified"


def score_tool_selection(expected_tool, actual_tool: str | None) -> float:
    """Score tool selection accuracy for one test case."""
    if expected_tool is None:
        return 1.0 if actual_tool is None else 0.0
    if actual_tool is None:
        return 0.0
    if isinstance(expected_tool, list):
        return 1.0 if actual_tool.lower() in [e.lower() for e in expected_tool] else 0.0
    return 1.0 if actual_tool.lower() == expected_tool.lower() else 0.0


def score_abstention(should_call_tool: bool, actual_tool: str | None) -> float:
    """Score irrelevance detection: did the model correctly abstain (or call) a tool?

    Returns 1.0 when the model's behavior matches should_call_tool:
    - should_call_tool=True + tool called -> 1.0 (correct: model used a tool)
    - should_call_tool=False + no tool called -> 1.0 (correct: model abstained)
    - should_call_tool=True + no tool called -> 0.0
    - should_call_tool=False + tool called -> 0.0
    """
    model_called = actual_tool is not None
    if should_call_tool:
        return 1.0 if model_called else 0.0
    else:
        return 1.0 if not model_called else 0.0


def _match_value(expected_val, actual_val, mode: str = "exact", epsilon: float = 0.01) -> bool:
    """Compare a single expected vs actual value using the given scoring mode."""
    if mode == "case_insensitive":
        if isinstance(expected_val, str) and isinstance(actual_val, str):
            return expected_val.lower() == actual_val.lower()
        return expected_val == actual_val

    if mode == "contains":
        es = str(expected_val).lower()
        av = str(actual_val).lower()
        return es in av or av in es

    if mode == "numeric_tolerance":
        try:
            return abs(float(expected_val) - float(actual_val)) <= epsilon
        except (ValueError, TypeError):
            return str(expected_val) == str(actual_val)

    if mode == "regex":
        try:
            return bool(re.search(str(expected_val), str(actual_val)))
        except re.error:
            return str(expected_val) == str(actual_val)

    # Default: exact (with existing case-insensitive string and exact numeric logic)
    if isinstance(expected_val, str) and isinstance(actual_val, str):
        return expected_val.lower() == actual_val.lower()
    if isinstance(expected_val, (int, float)) and isinstance(actual_val, (int, float)):
        return float(expected_val) == float(actual_val)
    return expected_val == actual_val


def score_params(expected_params: dict | None, actual_params: dict | None, scoring_config: dict | None = None) -> float | None:
    """Score parameter accuracy for one test case."""
    if expected_params is None:
        return None
    if not expected_params:
        return 1.0
    if actual_params is None:
        return 0.0

    mode = "exact"
    epsilon = 0.01
    if scoring_config:
        mode = scoring_config.get("mode", "exact")
        epsilon = scoring_config.get("epsilon", 0.01)

    correct = 0
    total = len(expected_params)
    for key, expected_val in expected_params.items():
        if key not in actual_params:
            continue
        actual_val = actual_params[key]
        if _match_value(expected_val, actual_val, mode, epsilon):
            correct += 1

    return correct / total if total > 0 else 1.0


def compute_overall_score(tool_score: float, param_score: float | None) -> float:
    """Compute weighted overall score."""
    if param_score is None:
        return tool_score
    return 0.6 * tool_score + 0.4 * param_score


def score_multi_turn(
    tool_chain: list[dict],
    expected_tool: str | list[str],
    expected_params: dict | None,
    valid_prerequisites: list[str],
    optimal_hops: int,
    scoring_config: dict | None = None,
) -> dict:
    """Score a multi-turn tool calling chain."""
    if not tool_chain:
        return {"completion": 0.0, "efficiency": 0.0, "redundancy_penalty": 0.0, "detour_penalty": 0.0, "overall_score": 0.0}

    # --- Completion ---
    final_call = tool_chain[-1]
    tool_score = score_tool_selection(expected_tool, final_call.get("tool_name"))
    param_score = score_params(expected_params, final_call.get("params"), scoring_config=scoring_config)
    completion = compute_overall_score(tool_score, param_score)

    # --- Efficiency ---
    actual_hops = len(tool_chain)
    efficiency = min(1.0, optimal_hops / actual_hops) if actual_hops > 0 else 0.0

    # --- Redundancy ---
    redundancy_penalty = 0.0
    for i in range(1, len(tool_chain)):
        if tool_chain[i].get("tool_name") == tool_chain[i-1].get("tool_name"):
            redundancy_penalty += 0.10

    # --- Detour ---
    detour_penalty = 0.0
    valid_set = set(p.lower() for p in valid_prerequisites) if valid_prerequisites else set()
    if isinstance(expected_tool, list):
        valid_set.update(t.lower() for t in expected_tool)
    elif expected_tool:
        valid_set.add(expected_tool.lower())

    for call in tool_chain[:-1]:
        name = (call.get("tool_name") or "").lower()
        if name and name not in valid_set:
            detour_penalty += 0.10

    # --- Composite ---
    overall = max(0.0, completion * efficiency - redundancy_penalty - detour_penalty)
    overall = min(1.0, overall)

    return {
        "completion": round(completion, 4),
        "efficiency": round(efficiency, 4),
        "redundancy_penalty": round(redundancy_penalty, 4),
        "detour_penalty": round(detour_penalty, 4),
        "overall_score": round(overall, 4),
    }


# ---------------------------------------------------------------------------
# Tool eval helpers
# ---------------------------------------------------------------------------


def _validate_tools(tools: list) -> str | None:
    """Return error message if tools are invalid, None if ok."""
    if not isinstance(tools, list) or len(tools) == 0:
        return "tools must be a non-empty array"
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict):
            return f"tools[{i}] must be an object"
        if tool.get("type") != "function":
            return f"tools[{i}].type must be 'function'"
        fn = tool.get("function", {})
        if not fn.get("name"):
            return f"tools[{i}].function.name is required"
    return None


def _parse_expected_tool(value):
    """Parse expected_tool from DB storage format to Python type."""
    if value is None:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        logger.debug("_maybe_parse_json: value is not JSON, returning as-is")
    return value


def _serialize_expected_tool(value) -> str | None:
    """Serialize expected_tool for DB storage."""
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)


def _tool_matches(actual_tool: str | None, expected_tool) -> bool:
    """Check if actual tool matches expected (str or list)."""
    if actual_tool is None or expected_tool is None:
        return False
    if isinstance(expected_tool, list):
        return actual_tool.lower() in [e.lower() for e in expected_tool]
    return actual_tool.lower() == expected_tool.lower()


def _capture_raw_response(response) -> dict:
    """Extract raw response data from a litellm response object."""
    raw_resp = {
        "id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "choices": [],
        "usage": None,
    }
    if hasattr(response, "usage") and response.usage:
        raw_resp["usage"] = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
            "total_tokens": getattr(response.usage, "total_tokens", None),
        }
    for choice in response.choices:
        c = {
            "index": choice.index,
            "finish_reason": choice.finish_reason,
            "message": {
                "role": getattr(choice.message, "role", None),
                "content": getattr(choice.message, "content", None),
                "tool_calls": None,
            }
        }
        if choice.message.tool_calls:
            c["message"]["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                }
                for tc in choice.message.tool_calls
            ]
        raw_resp["choices"].append(c)
    return raw_resp


# ---------------------------------------------------------------------------
# Eval summary computation
# ---------------------------------------------------------------------------


def _compute_eval_summaries(results: list[dict], targets: list[Target]) -> list[dict]:
    """Compute per-model aggregate scores from individual results.

    Includes T2 error type counts and T3 per-category breakdown.
    """
    target_map = {t.model_id: t for t in targets}

    by_model: dict[str, list[dict]] = {}
    for r in results:
        by_model.setdefault(r["model_id"], []).append(r)

    summaries = []
    for model_id, model_results in by_model.items():
        target = target_map.get(model_id)
        model_name = target.display_name if target else model_id
        provider = target.provider if target else ""

        tool_scores = [r["tool_selection_score"] for r in model_results if r["success"]]
        param_scores = [r["param_accuracy"] for r in model_results if r["success"] and r["param_accuracy"] is not None]
        overall_scores = [r["overall_score"] for r in model_results if r["success"]]

        tool_acc = (sum(tool_scores) / len(tool_scores) * 100) if tool_scores else 0.0
        param_acc = (sum(param_scores) / len(param_scores) * 100) if param_scores else 0.0
        overall = (sum(overall_scores) / len(overall_scores) * 100) if overall_scores else 0.0
        cases_passed = sum(1 for r in model_results if r["success"] and r["overall_score"] == 1.0)

        # Irrelevance score: only from cases where should_call_tool=False
        irrelevance_cases = [r for r in model_results if r["success"] and not r.get("should_call_tool", True)]
        irrelevance_scores = [r.get("irrelevance_score", 0.0) for r in irrelevance_cases]
        irrelevance_acc = (sum(irrelevance_scores) / len(irrelevance_scores) * 100) if irrelevance_scores else None

        # T2: Aggregate error type counts
        error_type_counts: dict[str, int] = {}
        for r in model_results:
            et = r.get("error_type")
            if et:
                error_type_counts[et] = error_type_counts.get(et, 0) + 1

        # T3: Per-category breakdown (BFCL-style)
        category_breakdown: dict[str, dict] = {}
        for r in model_results:
            cat = r.get("category") or "uncategorized"
            if cat not in category_breakdown:
                category_breakdown[cat] = {
                    "cases": 0, "passed": 0,
                    "tool_scores": [], "overall_scores": [],
                }
            category_breakdown[cat]["cases"] += 1
            if r["success"] and r.get("overall_score", 0) == 1.0:
                category_breakdown[cat]["passed"] += 1
            if r.get("success"):
                category_breakdown[cat]["tool_scores"].append(r.get("tool_selection_score", 0.0))
                category_breakdown[cat]["overall_scores"].append(r.get("overall_score", 0.0))

        # Compute per-category accuracy
        cat_summary = {}
        for cat, data in category_breakdown.items():
            ts = data["tool_scores"]
            os_ = data["overall_scores"]
            cat_summary[cat] = {
                "cases": data["cases"],
                "passed": data["passed"],
                "accuracy_pct": round(data["passed"] / data["cases"] * 100, 1) if data["cases"] else 0.0,
                "tool_accuracy_pct": round(sum(ts) / len(ts) * 100, 1) if ts else 0.0,
                "overall_pct": round(sum(os_) / len(os_) * 100, 1) if os_ else 0.0,
            }

        # T1: Format compliance breakdown
        format_compliance_counts: dict[str, int] = {}
        for r in model_results:
            fc = r.get("format_compliance", "PASS")
            format_compliance_counts[fc] = format_compliance_counts.get(fc, 0) + 1

        summaries.append({
            "model_id": model_id,
            "model_name": model_name,
            "provider": provider,
            "tool_accuracy_pct": round(tool_acc, 1),
            "param_accuracy_pct": round(param_acc, 1),
            "overall_pct": round(overall, 1),
            "cases_run": len(model_results),
            "cases_passed": cases_passed,
            "irrelevance_pct": round(irrelevance_acc, 1) if irrelevance_acc is not None else None,
            "irrelevance_cases": len(irrelevance_cases),
            # T1: format compliance
            "format_compliance_counts": format_compliance_counts,
            # T2: error taxonomy
            "error_type_counts": error_type_counts,
            # T3: category breakdown
            "category_breakdown": cat_summary,
        })

    return summaries


def _avg_overall_from_summaries(summaries: list[dict]) -> float:
    """Compute average overall score (0.0-1.0) from eval summaries."""
    if not summaries:
        return 0.0
    scores = [s.get("overall_pct", 0) for s in summaries]
    return round(sum(scores) / len(scores) / 100, 4)


def _build_config_summary(config: dict) -> str:
    """Build human-readable summary from config_json dict."""
    parts = []
    if "temperature" in config:
        parts.append(f"temp={config['temperature']}")
    if "tool_choice" in config:
        parts.append(f"tool_choice={config['tool_choice']}")
    if config.get("provider_params"):
        pp = config["provider_params"]
        parts.extend(f"{k}={v}" for k, v in sorted(pp.items()))
    if config.get("system_prompt"):
        sp = config["system_prompt"]
        if isinstance(sp, dict):
            keys = [k for k, v in sp.items() if v and str(v).strip()]
            parts.append(f"prompt={len(keys)} entry{'s' if len(keys) != 1 else ''}")
        elif isinstance(sp, str):
            parts.append(f"prompt='{sp[:40]}...'")
    return ", ".join(parts) if parts else "defaults"


async def _maybe_update_experiment_best(
    experiment_id: str,
    user_id: str,
    score: float,
    config_json: str,
    source: str,
    source_id: str,
    ws_manager=None,
) -> bool:
    """Update experiment's best_* fields if the new score exceeds current best."""
    exp = await db.get_experiment(experiment_id, user_id)
    if not exp:
        return False
    current_best = exp.get("best_score") or 0.0
    if score > current_best:
        await db.update_experiment(
            experiment_id, user_id,
            best_config_json=config_json,
            best_score=score,
            best_source=source,
            best_source_id=source_id,
        )
        if ws_manager:
            await ws_manager.send_to_user(user_id, {
                "type": "experiment_best_updated",
                "experiment_id": experiment_id,
                "best_score": score,
                "best_source": source,
                "best_source_id": source_id,
            })
        return True
    return False


# ---------------------------------------------------------------------------
# Async benchmark execution
# ---------------------------------------------------------------------------


async def async_run_single(
    target: Target, prompt: str, max_tokens: int, temperature: float,
    context_tokens: int = 0, timeout: int = 120,
    provider_params: dict | None = None,
) -> RunResult:
    """Execute a single streaming benchmark run using async litellm."""
    result = RunResult(target=target, context_tokens=context_tokens)

    messages = []
    if target.system_prompt:
        if context_tokens > 0:
            context_text = generate_context_text(context_tokens)
            messages.append({"role": "system", "content": target.system_prompt + "\n\n" + context_text})
        else:
            messages.append({"role": "system", "content": target.system_prompt})
    elif context_tokens > 0:
        context_text = generate_context_text(context_tokens)
        messages.append({"role": "system", "content": context_text})
    messages.append({"role": "user", "content": prompt})

    pp_copy = dict(provider_params) if provider_params else None
    extra = build_litellm_kwargs(
        target, provider_params=pp_copy,
        temperature=temperature, max_tokens=max_tokens,
    )

    kwargs = {
        "model": target.model_id,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timeout": timeout,
    }
    if extra:
        kwargs.update(extra)
    else:
        kwargs["max_tokens"] = max_tokens
        kwargs["temperature"] = temperature
        if target.skip_params:
            for p in target.skip_params:
                kwargs.pop(p, None)

    if target.api_base:
        kwargs["api_base"] = target.api_base
    if target.api_key:
        kwargs["api_key"] = target.api_key

    logger.info("Benchmark call: model=%s api_base=%s stream=%s", kwargs.get("model"), kwargs.get("api_base"), kwargs.get("stream"))

    try:
        start = time.perf_counter()
        stream = await litellm.acompletion(**kwargs)

        ttft = None
        chunk_count = 0
        usage_from_stream = None

        async for chunk in stream:
            now = time.perf_counter()

            if ttft is None:
                ttft = (now - start) * 1000

            if (
                chunk.choices
                and chunk.choices[0].delta
                and chunk.choices[0].delta.content
            ):
                chunk_count += 1

            if hasattr(chunk, "usage") and chunk.usage:
                usage_from_stream = chunk.usage

        total = time.perf_counter() - start

        if usage_from_stream:
            result.output_tokens = usage_from_stream.completion_tokens or chunk_count
            result.input_tokens = usage_from_stream.prompt_tokens or 0
        else:
            result.output_tokens = chunk_count
            result.input_tokens = 0

        result.ttft_ms = ttft or 0.0
        result.total_time_s = total
        result.tokens_per_second = (
            result.output_tokens / total if total > 0 else 0.0
        )

        if result.ttft_ms > 0 and result.input_tokens > 0:
            result.input_tokens_per_second = result.input_tokens / (result.ttft_ms / 1000)

        try:
            result.cost = litellm.completion_cost(
                model=target.model_id,
                prompt=str(result.input_tokens),
                completion=str(result.output_tokens),
                prompt_tokens=result.input_tokens,
                completion_tokens=result.output_tokens,
            )
        except Exception:
            logger.debug("Cost calculation not available for model %s", target.model_id)
            result.cost = 0.0

        if result.cost == 0.0 and target.input_cost_per_mtok is not None and target.output_cost_per_mtok is not None:
            result.cost = (
                result.input_tokens * target.input_cost_per_mtok
                + result.output_tokens * target.output_cost_per_mtok
            ) / 1_000_000

    except litellm.exceptions.RateLimitError as e:
        result.success = False
        result.error = f"[rate_limited] {sanitize_error(str(e)[:180], target.api_key)}"
    except litellm.exceptions.AuthenticationError as e:
        result.success = False
        result.error = f"[auth_failed] {sanitize_error(str(e)[:180], target.api_key)}"
    except litellm.exceptions.Timeout as e:
        result.success = False
        result.error = f"[timeout] {sanitize_error(str(e)[:180], target.api_key)}"
    except Exception as e:
        result.success = False
        result.error = sanitize_error(str(e)[:200], target.api_key)

    return result


# ---------------------------------------------------------------------------
# SSE + aggregation helpers
# ---------------------------------------------------------------------------


def _sse(data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"data: {json.dumps(data)}\n\n"


def _aggregate(raw_results: list[dict], config: dict) -> list[AggregatedResult]:
    """Convert raw result dicts into AggregatedResults for saving."""
    grouped = {}
    for r in raw_results:
        key = (r["model_id"], r["provider"], r.get("context_tokens", 0))
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(r)

    agg_list = []
    all_targets = build_targets(config)
    target_map = {(t.model_id, t.provider): t for t in all_targets}

    for (mid, provider, ctx_tokens), runs in grouped.items():
        target = target_map.get((mid, provider), Target(
            provider=provider,
            model_id=mid,
            display_name=runs[0]["model"],
        ))
        successes = [r for r in runs if r["success"]]
        n = len(successes)

        agg = AggregatedResult(
            target=target,
            runs=len(runs),
            failures=len(runs) - n,
        )
        if n > 0:
            agg.avg_ttft_ms = sum(r["ttft_ms"] for r in successes) / n
            agg.avg_total_time_s = sum(r["total_time_s"] for r in successes) / n
            agg.avg_tokens_per_second = sum(r["tokens_per_second"] for r in successes) / n
            agg.avg_output_tokens = sum(r["output_tokens"] for r in successes) / n
            agg.avg_cost = sum(r.get("cost", 0) for r in successes) / n
            agg.total_cost = sum(r.get("cost", 0) for r in successes)
            input_tps_vals = [r.get("input_tokens_per_second", 0) for r in successes if r.get("input_tokens_per_second", 0) > 0]
            if input_tps_vals:
                agg.avg_input_tps = sum(input_tps_vals) / len(input_tps_vals)

        agg.all_results = [RunResult(
            target=target,
            context_tokens=ctx_tokens,
            ttft_ms=r["ttft_ms"],
            total_time_s=r["total_time_s"],
            output_tokens=r["output_tokens"],
            input_tokens=r.get("input_tokens", 0),
            tokens_per_second=r["tokens_per_second"],
            input_tokens_per_second=r.get("input_tokens_per_second", 0),
            cost=r.get("cost", 0),
            success=r["success"],
            error=r.get("error", ""),
        ) for r in runs]

        if n > 0:
            success_results = [rr for rr in agg.all_results if rr.success]
            _compute_variance(agg, success_results)

        agg_list.append(agg)

    return agg_list


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------

CONFIG_PATH = str(_dir / "config.yaml")


def _save_config(config: dict):
    """Write config dict back to YAML."""
    import yaml
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Env file helpers
# ---------------------------------------------------------------------------

ENV_PATH = _dir / ".env"


def _parse_env_file() -> list[tuple[str, str, str]]:
    """Parse .env file -> list of (key_name, value, raw_line)."""
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


def _mask_value(val: str) -> str:
    """Mask all but last 4 chars: ****xxxx."""
    if not val or len(val) <= 4:
        return "****"
    return "****" + val[-4:]


# ---------------------------------------------------------------------------
# Judge helpers
# ---------------------------------------------------------------------------


def _parse_judge_json(text: str) -> dict:
    """Parse a JSON object from judge model response."""
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    stripped = re.sub(r'```(?:json)?\s*', '', text).strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[\s\S]*\}', stripped)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    logger.debug("_parse_judge_json: all parse strategies failed, returning empty dict")
    return {}


def _build_tool_definitions_text(tools: list[dict]) -> str:
    """Build tool definitions text for judge prompts."""
    parts = []
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "unknown")
        desc = fn.get("description", "")
        params = fn.get("parameters", {}).get("properties", {})
        param_strs = []
        for pname, pspec in params.items():
            ptype = pspec.get("type", "any")
            pdesc = pspec.get("description", "")[:60]
            param_strs.append(f"    {pname} ({ptype}): {pdesc}")
        parts.append(f"- {name}: {desc}\n  Parameters:\n" + "\n".join(param_strs))
    return "\n".join(parts)


def _build_tools_summary(tools: list[dict]) -> str:
    """Build a concise tools summary for meta-prompts."""
    parts = []
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "unknown")
        desc = fn.get("description", "")[:100]
        params = list(fn.get("parameters", {}).get("properties", {}).keys())
        parts.append(f"- {name}: {desc} (params: {', '.join(params)})")
    return "\n".join(parts)


def _build_test_cases_summary(cases: list) -> str:
    """Build a concise test cases summary for meta-prompts."""
    parts = []
    for c in cases[:10]:
        prompt = (c.get("prompt") or "")[:120]
        expected = c.get("expected_tool", "?")
        parts.append(f"- \"{prompt}\" -> expects tool: {expected}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Param tune helpers
# ---------------------------------------------------------------------------


def _find_best_config(results: list[dict]) -> dict | None:
    """Find the config with the highest overall_score."""
    if not results:
        return None
    best = max(results, key=lambda r: r.get("overall_score", 0))
    return best.get("config")


def _find_best_score(results: list[dict]) -> float:
    """Find the highest overall_score."""
    if not results:
        return 0.0
    return max(r.get("overall_score", 0) for r in results)


def _expand_search_space(search_space: dict) -> list[dict]:
    """Expand a search space definition into a flat list of parameter configs."""
    import itertools

    param_names = []
    param_values = []

    for name, spec in search_space.items():
        if isinstance(spec, list):
            if not spec:
                continue
            param_names.append(name)
            param_values.append(spec)
        elif isinstance(spec, dict):
            p_min = float(spec.get("min", 0))
            p_max = float(spec.get("max", 1))
            step = float(spec.get("step", 0.1))
            if step <= 0 or p_min > p_max:
                continue
            vals = []
            v = p_min
            while v <= p_max + 1e-9:
                vals.append(round(v, 6))
                v += step
            if not vals:
                continue
            param_names.append(name)
            param_values.append(vals)

    if not param_names:
        return [{}]

    combos = []
    for combo in itertools.product(*param_values):
        combos.append(dict(zip(param_names, combo)))
    return combos


# ---------------------------------------------------------------------------
# Meta prompt parsing
# ---------------------------------------------------------------------------


def _parse_meta_response(text: str) -> list[dict]:
    """Parse JSON from meta-model response."""
    text = text.strip()

    def _unwrap(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("prompts"), list):
            return data["prompts"]
        return None

    try:
        result = _unwrap(json.loads(text))
        if result is not None:
            return result
    except json.JSONDecodeError:
        pass
    stripped = re.sub(r'```(?:json)?\s*', '', text).strip()
    try:
        result = _unwrap(json.loads(stripped))
        if result is not None:
            return result
    except json.JSONDecodeError:
        pass
    for pattern in (r'\{[\s\S]*\}', r'\[[\s\S]*\]'):
        match = re.search(pattern, stripped)
        if match:
            try:
                result = _unwrap(json.loads(match.group()))
                if result is not None:
                    return result
            except json.JSONDecodeError:
                pass
    logger.debug("_parse_meta_response: all parse strategies failed, returning empty list")
    return []


# ---------------------------------------------------------------------------
# Phase 10 settings defaults + built-in presets
# ---------------------------------------------------------------------------

PHASE10_DEFAULTS = {
    "judge": {
        "enabled": False,
        "model_id": "",
        "mode": "post_eval",
        "temperature": 0.0,
        "max_tokens": 4096,
        "custom_instructions": "",
    },
    "param_tuner": {
        "max_combinations": 50,
        "temp_min": 0.0,
        "temp_max": 1.0,
        "temp_step": 0.5,
        "top_p_min": 0.5,
        "top_p_max": 1.0,
        "top_p_step": 0.25,
        "presets": [],
    },
    "prompt_tuner": {
        "mode": "quick",
        "generations": 3,
        "population_size": 5,
        "max_api_calls": 100,
    },
}

BUILTIN_PARAM_PRESETS = [
    {
        "name": "Qwen3 Coder 30B (Recommended)",
        "builtin": True,
        "search_space": {
            "temperature": [0.7],
            "top_p": [0.8],
            "top_k": [20],
        },
        "system_prompt": "Greedy decoding (temp=0) worsens quality. Always use sampling.",
    },
    {
        "name": "GLM-4.7 Flash (Z.AI Recommended)",
        "builtin": True,
        "search_space": {
            "temperature": [0.8],
            "top_p": [0.6],
            "top_k": [2],
        },
        "system_prompt": "Very low top_k recommended for MoE architecture.",
    },
]

# Valid analytics periods
_VALID_PERIODS = {"7d", "30d", "90d", "all"}

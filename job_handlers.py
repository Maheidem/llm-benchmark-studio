"""Job handler functions for the job registry.

Extracted from router modules to separate business logic (the actual job
execution) from HTTP handling (request parsing, validation, response formatting).

Each handler follows the job_registry handler signature:
    async def handler(job_id, params, cancel_event, progress_cb) -> str | None

The module-level `ws_manager` is injected by app.py at startup, same pattern
used by the router modules.

ERD v2: All handlers persist to normalized child tables (benchmark_results,
case_results, param_tune_combos, prompt_tune_generations/candidates,
judge_verdicts) instead of JSON blob columns.
"""

import asyncio
import json
import logging
import time
from dataclasses import replace

import db
from benchmark import Target, build_targets, save_results
from job_registry import registry as job_registry
from provider_params import identify_provider, validate_params
from routers.helpers import (
    _get_user_config,
    _parse_target_selection,
    _filter_targets,
    _find_target,
    _target_key,
    _check_rate_limit,
    inject_user_keys,
    async_run_single,
    _aggregate,
    _expand_search_space,
    _find_best_config,
    _find_best_score,
    _build_tool_definitions_text,
    _compute_eval_summaries,
    _avg_overall_from_summaries,
    _maybe_update_experiment_best,
    _build_tools_summary,
    _build_test_cases_summary,
    _parse_meta_response,
)
from routers.tool_eval import run_single_eval, run_multi_turn_eval
from routers.judge import _judge_single_verdict, _judge_crosscase

logger = logging.getLogger(__name__)

# Module-level ws_manager -- injected by app.py after import
ws_manager = None


# ---------------------------------------------------------------------------
# Helper: Reconstruct OpenAI-format tools from tool_definitions rows
# ---------------------------------------------------------------------------

def _tool_defs_to_openai(tool_defs: list[dict]) -> list[dict]:
    """Convert tool_definitions DB rows to the OpenAI function-calling format
    expected by LiteLLM and the eval engine.

    Each DB row has: name, description, parameters_schema (JSON string).
    Returns: [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}, ...]
    """
    tools = []
    for td in tool_defs:
        params_schema = td.get("parameters_schema", "{}")
        if isinstance(params_schema, str):
            try:
                params_schema = json.loads(params_schema)
            except (json.JSONDecodeError, TypeError):
                params_schema = {}
        tools.append({
            "type": "function",
            "function": {
                "name": td["name"],
                "description": td.get("description", ""),
                "parameters": params_schema,
            },
        })
    return tools


# ---------------------------------------------------------------------------
# Helper: Resolve model DB ID from litellm_id
# ---------------------------------------------------------------------------

async def _resolve_model_db_id(user_id: str, litellm_id: str) -> str | None:
    """Resolve the models table ID for a given litellm_id.

    Auto-creates provider + model records if not found.
    Returns None only on unexpected errors.
    """
    try:
        return await db.ensure_model_exists(user_id, litellm_id)
    except Exception as e:
        logger.warning("Failed to resolve/create model DB ID for %s: %s", litellm_id, e)
        return None


# ---------------------------------------------------------------------------
# Helper: Pre-validate params per target and emit WS warnings
# ---------------------------------------------------------------------------

async def _emit_param_adjustments(
    user_id: str,
    job_id: str,
    targets: list,
    provider_params: dict | None,
    temperature: float | None = None,
    max_tokens: int | None = None,
):
    """Run validate_params() once per target BEFORE execution starts.

    Emits a 'param_adjustments' WebSocket event if any params are dropped,
    clamped, or renamed for any target.  This gives the user a heads-up
    instead of silently modifying their request.
    """
    if not ws_manager:
        return
    if not provider_params and temperature is None and max_tokens is None:
        return

    all_adjustments = []  # [{model_id, provider, adjustments: [...]}]

    for target in targets:
        provider = identify_provider(target.model_id, getattr(target, "provider_key", None))

        # Build the same param dict that build_litellm_kwargs would
        params_to_check: dict = {}
        if temperature is not None:
            params_to_check["temperature"] = temperature
        if max_tokens is not None:
            params_to_check["max_tokens"] = max_tokens
        if provider_params:
            for k, v in provider_params.items():
                if k != "passthrough" and v is not None:
                    params_to_check[k] = v

        if not params_to_check:
            continue

        result = validate_params(provider, target.model_id, params_to_check)
        # Filter to actionable adjustments (drop, clamp, rename, warn — skip passthrough)
        meaningful = [a for a in result.get("adjustments", []) if a.get("action") != "passthrough"]
        if meaningful:
            all_adjustments.append({
                "model_id": target.model_id,
                "provider": provider,
                "provider_key": getattr(target, "provider_key", None),
                "adjustments": meaningful,
            })

    if all_adjustments:
        await ws_manager.send_to_user(user_id, {
            "type": "param_adjustments",
            "job_id": job_id,
            "models": all_adjustments,
        })


# ---------------------------------------------------------------------------
# Benchmark Handler
# ---------------------------------------------------------------------------

async def benchmark_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for benchmark execution.

    Extracts the core benchmark logic from the old SSE generator.
    Returns the benchmark_run ID on success, or None.
    """
    user_id = params["user_id"]
    model_ids = params["models"]
    _raw_ts = params.get("target_set")  # serialized as list-of-lists
    target_set = {tuple(t) for t in _raw_ts} if _raw_ts else None
    runs = params.get("runs", 3)
    max_tokens = params.get("max_tokens", 512)
    temperature = params.get("temperature", 0.7)
    prompt = params.get("prompt", "")
    context_tiers = params.get("context_tiers", [0])
    warmup = params.get("warmup", True)
    timeout = params.get("timeout", 120)
    provider_params = params.get("provider_params")
    profiles_map = params.get("profiles")  # {"model_id": "profile_id"} or None

    logger.info(
        "Benchmark started: job_id=%s user_id=%s models=%d tiers=%s runs=%d",
        job_id, user_id, len(model_ids) if model_ids else 0, context_tiers, runs,
    )

    # Load model profiles if specified (B3)
    loaded_profiles = {}
    if profiles_map and isinstance(profiles_map, dict):
        for model_id, profile_id in profiles_map.items():
            profile = await db.get_profile(profile_id, user_id)
            if profile:
                loaded_profiles[model_id] = profile
                logger.debug("Loaded profile %s for model %s", profile_id, model_id)

    config = await _get_user_config(user_id)
    defaults = config.get("defaults", {})
    all_targets = build_targets(config)

    # Filter to requested models (precise provider_key+model_id or legacy model_id-only)
    targets = _filter_targets(all_targets, model_ids, target_set)

    # Inject per-user API keys
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    if not prompt.strip():
        prompt = defaults.get("prompt", "Explain recursion in programming with a Python example.")

    # Calculate total runs across all tiers
    total = 0
    for tier in context_tiers:
        for target in targets:
            headroom = target.context_window - max_tokens - 100
            if tier == 0 or tier <= headroom:
                total += runs

    if total == 0:
        if ws_manager:
            await ws_manager.send_to_user(user_id, {
                "type": "job_failed",
                "job_id": job_id,
                "error": "No benchmark targets matched the selected configuration",
            })
        return None

    # Build config_json for re-run support
    bench_config = {
        "models": model_ids,
        "context_tiers": context_tiers,
        "runs": runs,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "warmup": warmup,
    }
    if provider_params:
        bench_config["provider_params"] = provider_params
    if target_set:
        bench_config["target_set"] = [list(t) for t in target_set]
    if profiles_map:
        bench_config["profiles"] = profiles_map

    # ERD v2: Create benchmark_runs row BEFORE the loop
    run_id = await db.save_benchmark_run(
        user_id=user_id,
        prompt=prompt,
        context_tiers=json.dumps(context_tiers),
        max_tokens=max_tokens,
        temperature=temperature,
        warmup=warmup,
        config_json=json.dumps(bench_config),
    )

    # Pre-resolve model DB IDs for benchmark_results FK
    model_db_id_cache: dict[str, str | None] = {}

    # Group targets by provider for parallel execution
    provider_groups = {}
    for target in targets:
        provider_groups.setdefault(target.provider, []).append(target)

    results_queue = asyncio.Queue()
    # Track run numbers per model for benchmark_results
    run_number_tracker: dict[str, int] = {}

    # Helper to send WebSocket messages directly to the user
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Send benchmark_init so frontend can set up per-provider progress tracking
    await _ws_send({
        "type": "benchmark_init",
        "job_id": job_id,
        "data": {
            "targets": [{"provider_key": t.provider_key, "model_id": t.model_id} for t in targets],
            "runs": runs,
            "context_tiers": context_tiers,
            "max_tokens": max_tokens,
        },
    })

    # Pre-validate params and warn user about drops/clamps BEFORE execution starts
    await _emit_param_adjustments(
        user_id, job_id, targets, provider_params,
        temperature=temperature, max_tokens=max_tokens,
    )

    async def run_provider(prov_targets):
        """Run all benchmarks for one provider sequentially."""
        for tier in context_tiers:
            for target in prov_targets:
                if cancel_event.is_set():
                    return
                headroom = target.context_window - max_tokens - 100
                if tier > 0 and tier > headroom:
                    continue  # Skip tier exceeding context window

                # Apply model profile if available (B3)
                bench_target = target
                bench_provider_params = provider_params
                profile = loaded_profiles.get(target.model_id)
                if profile:
                    # Profile system_prompt replaces config-level baseline
                    profile_sys = profile.get("system_prompt")
                    if profile_sys:
                        bench_target = replace(target, system_prompt=profile_sys)

                    # Profile params: defaults < profile < per-request overrides
                    profile_params_raw = profile.get("params_json")
                    if profile_params_raw:
                        profile_params = json.loads(profile_params_raw) if isinstance(profile_params_raw, str) else profile_params_raw
                        if profile_params:
                            merged = dict(profile_params)
                            if bench_provider_params:
                                merged.update(bench_provider_params)
                            bench_provider_params = merged

                # Warm-up run (discarded)
                if warmup:
                    await async_run_single(
                        bench_target, prompt, max_tokens, temperature, tier,
                        timeout=timeout, provider_params=bench_provider_params,
                    )

                for r in range(runs):
                    if cancel_event.is_set():
                        return

                    # Notify frontend that this provider/model/run is starting
                    await _ws_send({
                        "type": "benchmark_progress",
                        "job_id": job_id,
                        "data": {
                            "type": "progress",
                            "provider": target.provider,
                            "model": target.display_name,
                            "model_id": target.model_id,
                            "run": r + 1,
                            "runs": runs,
                            "context_tokens": tier,
                        },
                    })

                    result = await async_run_single(
                        bench_target, prompt, max_tokens, temperature, tier,
                        timeout=timeout, provider_params=bench_provider_params,
                    )
                    await results_queue.put({
                        "type": "result",
                        "provider": target.provider,
                        "model": target.display_name,
                        "model_id": target.model_id,
                        "run": r + 1,
                        "runs": runs,
                        "context_tokens": tier,
                        "ttft_ms": round(result.ttft_ms, 2),
                        "total_time_s": round(result.total_time_s, 3),
                        "output_tokens": result.output_tokens,
                        "input_tokens": result.input_tokens,
                        "tokens_per_second": round(result.tokens_per_second, 2),
                        "input_tokens_per_second": round(result.input_tokens_per_second, 2),
                        "cost": round(result.cost, 8),
                        "success": result.success,
                        "error": result.error,
                    })

    # Launch all provider groups as concurrent tasks
    tasks = [asyncio.create_task(run_provider(g)) for g in provider_groups.values()]

    async def sentinel():
        await asyncio.gather(*tasks, return_exceptions=True)
        await results_queue.put(None)

    asyncio.create_task(sentinel())

    # Consume results and report progress
    current = 0
    all_results = []
    while True:
        try:
            item = await asyncio.wait_for(results_queue.get(), timeout=15)
        except asyncio.TimeoutError:
            continue  # Keep waiting
        if item is None:
            break
        if cancel_event.is_set():
            for t in tasks:
                t.cancel()
            return None
        if item["type"] == "result":
            current += 1
            all_results.append(item)
            pct = int((current / total) * 100) if total > 0 else 0
            detail = f"{item['model']}, Run {item['run']}/{item['runs']}"
            if item["context_tokens"] > 0:
                detail += f", Context {item['context_tokens'] // 1000}K"
            await progress_cb(pct, detail)

            # Send individual result data to frontend via WebSocket
            await _ws_send({
                "type": "benchmark_result",
                "job_id": job_id,
                "data": item,
            })

            # ERD v2: Persist each result row to benchmark_results
            try:
                model_litellm_id = item.get("model_id", "")
                if model_litellm_id not in model_db_id_cache:
                    model_db_id_cache[model_litellm_id] = await _resolve_model_db_id(user_id, model_litellm_id)
                model_db_id = model_db_id_cache[model_litellm_id]

                if model_db_id:
                    # Track run numbers per model+tier
                    rn_key = f"{model_litellm_id}:{item.get('context_tokens', 0)}"
                    run_number_tracker[rn_key] = run_number_tracker.get(rn_key, 0) + 1

                    await db.save_benchmark_result(
                        run_id=run_id,
                        model_id=model_db_id,
                        run_number=run_number_tracker[rn_key],
                        context_tokens=item.get("context_tokens", 0),
                        ttft_ms=item.get("ttft_ms"),
                        total_time_s=item.get("total_time_s"),
                        output_tokens=item.get("output_tokens"),
                        input_tokens=item.get("input_tokens"),
                        tokens_per_second=item.get("tokens_per_second"),
                        input_tokens_per_second=item.get("input_tokens_per_second"),
                        cost=item.get("cost"),
                        success=item.get("success", True),
                        error=item.get("error"),
                    )
            except Exception as e:
                logger.warning("Failed to save benchmark_result: %s", e)

    # Save aggregated results to JSON files (legacy format)
    if all_results:
        agg_results = _aggregate(all_results, config)
        save_results(agg_results, prompt, context_tiers=context_tiers)

        logger.info(
            "Benchmark completed: job_id=%s user_id=%s results=%d run_id=%s",
            job_id, user_id, len(all_results), run_id,
        )

        await db.log_audit(
            user_id=user_id,
            username=params.get("user_email", ""),
            action="benchmark_complete",
            resource_type="benchmark",
            detail={"models": model_ids, "result_count": len(all_results)},
        )

        return run_id

    logger.info("Benchmark produced no results: job_id=%s user_id=%s", job_id, user_id)
    return None


# ---------------------------------------------------------------------------
# Tool Eval Handler
# ---------------------------------------------------------------------------

async def tool_eval_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for tool eval execution.

    Returns the eval_id on success, or None.
    """
    user_id = params["user_id"]
    suite_id = params["suite_id"]
    model_ids = params["models"]
    _raw_ts = params.get("target_set")
    target_set = {tuple(t) for t in _raw_ts} if _raw_ts else None
    temperature = float(params.get("temperature", 0.0))
    tool_choice = params.get("tool_choice", "required")
    provider_params = params.get("provider_params")
    system_prompt_raw = params.get("system_prompt")  # string | dict | None
    judge_config = params.get("judge")
    judge_concurrency = int(params.get("judge_concurrency", 4))
    experiment_id = params.get("experiment_id")
    profiles_map = params.get("profiles")  # {"model_id": "profile_id"} or None

    logger.info(
        "Tool eval started: job_id=%s user_id=%s models=%d",
        job_id, user_id, len(model_ids) if model_ids else 0,
    )

    # Load model profiles if specified
    loaded_profiles = {}
    if profiles_map and isinstance(profiles_map, dict):
        for model_id, profile_id in profiles_map.items():
            profile = await db.get_profile(profile_id, user_id)
            if profile:
                loaded_profiles[model_id] = profile
                logger.debug("Loaded profile %s for model %s", profile_id, model_id)

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    cases = await db.get_test_cases(suite_id)

    # ERD v2: Load tools from tool_definitions table instead of tools_json
    tool_defs = await db.get_tool_definitions(suite_id)
    tools = _tool_defs_to_openai(tool_defs)

    # Build targets
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    targets = _filter_targets(all_targets, model_ids, target_set)

    # Inject per-user API keys
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    if not targets:
        if ws_manager:
            await ws_manager.send_to_user(user_id, {
                "type": "job_failed",
                "job_id": job_id,
                "error": "No models matched the selected targets. Check your model configuration.",
            })
        return None

    # Pre-validate params and warn user about drops/clamps BEFORE execution starts
    await _emit_param_adjustments(
        user_id, job_id, targets, provider_params,
        temperature=temperature,
    )

    # Judge setup (opt-in)
    judge_enabled = False
    judge_mode = "none"
    judge_target = None
    judge_custom_instructions = ""
    if isinstance(judge_config, dict) and judge_config.get("enabled"):
        judge_mode = judge_config.get("mode", "none")
        judge_model_id = judge_config.get("judge_model")
        judge_provider_key = judge_config.get("judge_provider_key")
        judge_custom_instructions = judge_config.get("custom_instructions", "")
        if judge_mode in ("live_inline", "post_eval") and judge_model_id:
            judge_enabled = True
            # Prefer already-injected eval targets (keys already set)
            jt_list = _find_target(targets, judge_model_id, judge_provider_key)
            if jt_list:
                judge_target = jt_list[0]
                logger.debug("Judge target found in eval set (key already injected): %s", judge_model_id)
            else:
                # Judge model not in eval set -- fall back to all_targets + inject
                jt_list = _find_target(all_targets, judge_model_id, judge_provider_key)
                if jt_list:
                    judge_target = jt_list[0]
                    if judge_target.provider_key:
                        enc = await db.get_user_key_for_provider(user_id, judge_target.provider_key)
                        if enc:
                            judge_target = inject_user_keys([judge_target], {judge_target.provider_key: enc})[0]
                    logger.debug("Judge target from all_targets (separate injection): %s", judge_model_id)
                else:
                    judge_enabled = False
            if judge_target:
                logger.debug(
                    "Judge target ready: model=%s api_base=%s has_key=%s",
                    judge_target.model_id, judge_target.api_base,
                    bool(judge_target.api_key),
                )

    # Auto-cap judge concurrency when judge shares an endpoint with eval models.
    if judge_enabled and judge_target and judge_mode in ("live_inline", "post_eval"):
        eval_bases = {t.api_base for t in targets if t.api_base}
        if judge_target.api_base and judge_target.api_base in eval_bases:
            judge_concurrency = min(judge_concurrency, 1)
            logger.info("Judge shares endpoint with eval model -- capping concurrency to 1")

    total = len(targets) * len(cases)
    results_queue = asyncio.Queue()

    # Helper to send WebSocket messages directly to the user
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Build config_json for reproducibility (M1)
    eval_config = {
        "temperature": temperature,
        "tool_choice": tool_choice,
    }
    if provider_params:
        eval_config["provider_params"] = provider_params
    if system_prompt_raw:
        eval_config["system_prompt"] = system_prompt_raw
    # Build target_set from the targets list
    target_set_list = []
    for t in targets:
        target_set_list.append([t.provider_key or "", t.model_id])
    target_set_cleaned = [ts for ts in target_set_list if ts[0] or ts[1]]
    if target_set_cleaned:
        eval_config["target_set"] = target_set_cleaned
    if profiles_map:
        eval_config["profiles"] = profiles_map

    # Build system_prompt_config and provider_params_json for DB
    system_prompt_config_json = json.dumps(system_prompt_raw) if system_prompt_raw else None
    provider_params_json = json.dumps(provider_params) if provider_params else None
    profiles_json_str = json.dumps(profiles_map) if profiles_map else None

    # ERD v2: Create eval run row BEFORE the eval loop
    eval_id = await db.save_tool_eval_run(
        user_id=user_id,
        suite_id=suite["id"],
        temperature=temperature,
        tool_choice=tool_choice,
        system_prompt_config=system_prompt_config_json,
        provider_params_json=provider_params_json,
        profiles_json=profiles_json_str,
        experiment_id=experiment_id,
        orchestrator_type="standalone",
        orchestrator_run_id=None,
    )

    # Store result_ref so frontend can discover eval_id on reconnect
    await db.set_job_result_ref(job_id, eval_id)

    # Pre-resolve model DB IDs
    model_db_id_cache: dict[str, str | None] = {}

    # Send init event so frontend can set up tracking
    await _ws_send({
        "type": "tool_eval_init",
        "job_id": job_id,
        "data": {
            "targets": [{"provider_key": t.provider_key, "model_id": t.model_id, "display_name": t.display_name} for t in targets],
            "total_cases": len(cases),
            "suite_name": suite["name"],
            "judge_enabled": judge_enabled,
            "judge_mode": judge_mode,
        },
    })

    # Group targets by provider
    provider_groups: dict[str, list[Target]] = {}
    for target in targets:
        provider_groups.setdefault(target.provider, []).append(target)

    def _resolve_system_prompt(target):
        """Resolve per-model system prompt from dict, string, or None."""
        if isinstance(system_prompt_raw, dict):
            target_key = f"{target.provider_key}::{target.model_id}"
            return system_prompt_raw.get(target_key) or system_prompt_raw.get("_global") or None
        elif isinstance(system_prompt_raw, str):
            return system_prompt_raw
        return None

    async def run_provider(prov_targets):
        """Run all test cases for models in this provider."""
        for target in prov_targets:
            system_prompt = _resolve_system_prompt(target)

            # Apply model profile if available (B3)
            eval_target = target
            eval_provider_params = provider_params
            profile = loaded_profiles.get(target.model_id)
            if profile:
                # Profile system_prompt replaces config-level baseline
                # Per-request system_prompt (if any) still overrides profile
                profile_sys = profile.get("system_prompt")
                if profile_sys and not system_prompt:
                    system_prompt = profile_sys
                elif profile_sys and system_prompt:
                    # Per-request override wins over profile
                    pass

                # Profile params: defaults < profile < per-request overrides
                profile_params_raw = profile.get("params_json")
                if profile_params_raw:
                    profile_params = json.loads(profile_params_raw) if isinstance(profile_params_raw, str) else profile_params_raw
                    if profile_params:
                        merged = dict(profile_params)
                        if eval_provider_params:
                            merged.update(eval_provider_params)
                        eval_provider_params = merged

            for case in cases:
                if cancel_event.is_set():
                    return
                mt_config = None
                if case.get("multi_turn_config"):
                    try:
                        mt_config = json.loads(case["multi_turn_config"]) if isinstance(case["multi_turn_config"], str) else case["multi_turn_config"]
                    except (json.JSONDecodeError, TypeError):
                        logger.debug("Failed to parse multi_turn_config in tool eval handler")
                        await _ws_send({
                            "type": "eval_warning",
                            "job_id": job_id,
                            "detail": "Multi-turn config could not be parsed — running as single-turn",
                        })
                        mt_config = None

                if mt_config and mt_config.get("multi_turn"):
                    case_with_mt = {**case, "_mt_config": mt_config}
                    result = await run_multi_turn_eval(eval_target, tools, case_with_mt, temperature, tool_choice, provider_params=eval_provider_params, system_prompt=system_prompt)
                else:
                    result = await run_single_eval(eval_target, tools, case, temperature, tool_choice, provider_params=eval_provider_params, system_prompt=system_prompt)
                await results_queue.put(result)

    # Launch provider groups in parallel
    tasks = [asyncio.create_task(run_provider(g)) for g in provider_groups.values()]

    async def sentinel():
        await asyncio.gather(*tasks, return_exceptions=True)
        await results_queue.put(None)

    asyncio.create_task(sentinel())

    # Consume results and report progress
    current = 0
    all_results = []
    judge_verdicts = []
    judge_sem = asyncio.Semaphore(judge_concurrency)
    judge_queue = asyncio.Queue() if (judge_enabled and judge_mode == "live_inline" and judge_target) else None
    judge_tasks = []
    tool_defs_text = _build_tool_definitions_text(tools) if judge_enabled else ""
    target_map = {t.model_id: t for t in targets}

    # Map from (model_id, test_case_id) -> case_result DB id (for judge verdicts)
    case_result_db_ids: dict[str, str] = {}

    while True:
        try:
            item = await asyncio.wait_for(results_queue.get(), timeout=15)
        except asyncio.TimeoutError:
            # Drain any ready judge verdicts
            if judge_queue:
                while not judge_queue.empty():
                    verdict = judge_queue.get_nowait()
                    judge_verdicts.append(verdict)
                    await _ws_send({"type": "judge_verdict", "job_id": job_id, **verdict})
            continue
        if item is None:
            break
        if cancel_event.is_set():
            for t in tasks:
                t.cancel()
            return None

        current += 1
        t = target_map.get(item["model_id"])
        model_display = t.display_name if t else item["model_id"]
        item["model_name"] = model_display

        # Report progress
        pct = int((current / total) * 100) if total > 0 else 0
        detail = f"{model_display}: {item.get('test_case_id', '?')}"
        await progress_cb(pct, detail)

        # Send progress + result via WebSocket
        await _ws_send({
            "type": "tool_eval_progress",
            "job_id": job_id,
            "data": {
                "current": current,
                "total": total,
                "model": model_display,
                "test_case": item.get("test_case_id", "?"),
            },
        })
        await _ws_send({
            "type": "tool_eval_result",
            "job_id": job_id,
            "data": item,
        })
        all_results.append(item)

        # ERD v2: Persist each case result to case_results table
        try:
            model_litellm_id = item.get("model_id", "")
            if model_litellm_id not in model_db_id_cache:
                model_db_id_cache[model_litellm_id] = await _resolve_model_db_id(user_id, model_litellm_id)
            model_db_id = model_db_id_cache[model_litellm_id]

            if model_db_id:
                actual_params_str = json.dumps(item.get("actual_params")) if item.get("actual_params") is not None else None
                raw_request_str = json.dumps(item.get("raw_request")) if item.get("raw_request") is not None else None
                raw_response_str = json.dumps(item.get("raw_response")) if item.get("raw_response") is not None else None

                case_result_id = await db.save_case_result(
                    eval_run_id=eval_id,
                    test_case_id=item.get("test_case_id", ""),
                    model_id=model_db_id,
                    tool_selection_score=item.get("tool_selection_score", 0.0),
                    param_accuracy=item.get("param_accuracy"),
                    overall_score=item.get("overall_score", 0.0),
                    irrelevance_score=item.get("irrelevance_score"),
                    actual_tool=item.get("actual_tool"),
                    actual_params=actual_params_str,
                    success=item.get("success", True),
                    error=item.get("error", ""),
                    latency_ms=item.get("latency_ms", 0),
                    format_compliance=item.get("format_compliance", "PASS"),
                    error_type=item.get("error_type"),
                    raw_request=raw_request_str,
                    raw_response=raw_response_str,
                )
                # Track for judge verdicts later
                cr_key = f"{model_litellm_id}::{item.get('test_case_id', '')}"
                case_result_db_ids[cr_key] = case_result_id
        except Exception as e:
            logger.warning("Failed to save case_result: %s", e)
            await _ws_send({
                "type": "eval_warning",
                "job_id": job_id,
                "detail": f"Failed to save result for case {item.get('test_case_id', '?')}: {e}",
            })

        # Live inline judge: fire concurrent judge task per result (with semaphore)
        if judge_queue and judge_target:
            async def _judge_async(jt, td, res, jq, sem, ci=judge_custom_instructions):
                async with sem:
                    try:
                        v = await _judge_single_verdict(jt, td, {}, res, custom_instructions=ci)
                        v["test_case_id"] = res.get("test_case_id", "?")
                        v["model_id"] = res.get("model_id", "?")
                        await jq.put(v)
                    except Exception:
                        logger.exception("Inline judge failed for case=%s model=%s", res.get("test_case_id", "?"), res.get("model_id", "?"))
                        await jq.put({
                            "test_case_id": res.get("test_case_id", "?"),
                            "model_id": res.get("model_id", "?"),
                            "quality_score": 0,
                            "verdict": "error",
                            "summary": "Judge error",
                            "reasoning": "Judge model call failed",
                            "tool_selection_assessment": "unknown",
                            "param_assessment": "unknown",
                            "judge_override_score": None,
                            "override_reason": None,
                        })
            judge_tasks.append(asyncio.create_task(
                _judge_async(judge_target, tool_defs_text, item, judge_queue, judge_sem)
            ))

        # Drain any ready judge verdicts
        if judge_queue:
            while not judge_queue.empty():
                verdict = judge_queue.get_nowait()
                judge_verdicts.append(verdict)
                await _ws_send({"type": "judge_verdict", "job_id": job_id, **verdict})

    # Wait for remaining live inline judge tasks
    if judge_tasks:
        await asyncio.gather(*judge_tasks, return_exceptions=True)
        while not judge_queue.empty():
            verdict = judge_queue.get_nowait()
            judge_verdicts.append(verdict)
            await _ws_send({"type": "judge_verdict", "job_id": job_id, **verdict})

    # Compute per-model summaries (in-memory for WS events)
    summaries = _compute_eval_summaries(all_results, targets)
    for s in summaries:
        await _ws_send({"type": "tool_eval_summary", "job_id": job_id, "data": s})

    config_json_str = json.dumps(eval_config)

    # --- Experiment integration (M2) ---
    if experiment_id:
        try:
            avg_score = _avg_overall_from_summaries(summaries)
            # Auto-pin first eval as baseline if experiment has none
            exp = await db.get_experiment(experiment_id, user_id)
            if exp and not exp.get("baseline_eval_id"):
                await db.update_experiment(
                    experiment_id, user_id,
                    baseline_eval_id=eval_id,
                    baseline_score=avg_score,
                )
            # Update experiment best if improved
            await _maybe_update_experiment_best(
                experiment_id, user_id,
                score=avg_score,
                config_json=config_json_str,
                source="eval",
                source_id=eval_id,
            )
        except Exception:
            logger.exception("Experiment update failed: experiment_id=%s", experiment_id)

    # --- Post-eval judge mode ---
    judge_report_id = None
    if judge_enabled and judge_mode == "post_eval" and judge_target and all_results:
        try:
            # Brief pause to let cloudflared/local server release eval connections
            await asyncio.sleep(2)
            logger.info("Post-eval judge starting: model=%s api_base=%s concurrency=%d cases=%d",
                        judge_target.model_id, judge_target.api_base, judge_concurrency, len(all_results))
            _judge_db_model = await db.get_model_by_litellm_id(user_id, judge_target.model_id)
            _judge_db_model_id = _judge_db_model["id"] if _judge_db_model else None
            judge_report_id = await db.save_judge_report(
                user_id=user_id,
                mode="post_eval",
                eval_run_id=eval_id,
                experiment_id=experiment_id,
                judge_model_id=_judge_db_model_id,
                custom_instructions=judge_custom_instructions or None,
            )
            await _ws_send({
                "type": "judge_start",
                "job_id": job_id,
                "mode": "post_eval",
                "judge_model": judge_target.display_name,
                "cases_to_review": len(all_results),
            })

            model_results: dict[str, list[dict]] = {}
            for r in all_results:
                mid = r.get("model_id", "unknown")
                model_results.setdefault(mid, []).append(r)

            pe_verdicts = []
            all_pe_reports = []
            judge_completed = 0
            judge_total = len(all_results)
            for mid, mres in model_results.items():
                model_vds = []
                # Use semaphore for concurrent judge calls
                sem = asyncio.Semaphore(judge_concurrency)

                async def _judge_one(r, _mid=mid):
                    async with sem:
                        v = await _judge_single_verdict(judge_target, tool_defs_text, {}, r, custom_instructions=judge_custom_instructions)
                        v["test_case_id"] = r.get("test_case_id", "?")
                        v["model_id"] = _mid
                        return v

                judge_batch = [asyncio.create_task(_judge_one(r)) for r in mres]
                try:
                    for coro in asyncio.as_completed(judge_batch):
                        if cancel_event.is_set():
                            for bt in judge_batch:
                                bt.cancel()
                            break
                        v = await coro
                        pe_verdicts.append(v)
                        model_vds.append(v)
                        judge_completed += 1
                        await _ws_send({"type": "judge_verdict", "job_id": job_id, **v})
                        # Update progress for judge phase
                        j_pct = int((judge_completed / judge_total) * 100) if judge_total > 0 else 0
                        await progress_cb(j_pct, f"Judge: {judge_completed}/{judge_total}")

                        # ERD v2: Save judge verdict to DB
                        try:
                            cr_key = f"{v.get('model_id', '')}::{v.get('test_case_id', '')}"
                            cr_id = case_result_db_ids.get(cr_key)
                            if cr_id and judge_report_id:
                                await db.save_judge_verdict(
                                    report_id=judge_report_id,
                                    case_result_id=cr_id,
                                    quality_score=v.get("quality_score", 0),
                                    verdict=v.get("verdict", "fail"),
                                    summary=v.get("summary", ""),
                                    reasoning=v.get("reasoning", ""),
                                    tool_selection_assessment=v.get("tool_selection_assessment", "unknown"),
                                    param_assessment=v.get("param_assessment", "unknown"),
                                    judge_override_score=v.get("judge_override_score"),
                                    override_reason=v.get("override_reason"),
                                )
                        except Exception as ve:
                            logger.warning("Failed to save judge verdict: %s", ve)
                            await _ws_send({
                                "type": "eval_warning",
                                "job_id": job_id,
                                "detail": f"Failed to save judge verdict: {ve}",
                            })
                except Exception:
                    # Cancel remaining judge tasks to avoid orphaned "Task exception was never retrieved"
                    for bt in judge_batch:
                        bt.cancel()
                    raise

                if cancel_event.is_set():
                    break

                tgt = target_map.get(mid)
                mname = tgt.display_name if tgt else mid
                pe_report_data = await _judge_crosscase(judge_target, mname, model_vds)
                pe_report_data["model_id"] = mid
                pe_report_data["model_name"] = mname
                all_pe_reports.append(pe_report_data)
                await _ws_send({"type": "judge_report", "job_id": job_id, "eval_id": eval_id, "report": pe_report_data})

            # Derive overall grade/score from the best model's report
            if all_pe_reports:
                best_pe = max(all_pe_reports, key=lambda r: r.get("overall_score", 0))
                pe_final_grade = best_pe.get("overall_grade", "?")
                pe_final_score = best_pe.get("overall_score", 0)
            else:
                pe_final_grade = "?"
                pe_final_score = 0

            await db.update_judge_report(
                judge_report_id,
                report_json=json.dumps(all_pe_reports),
                overall_grade=pe_final_grade,
                overall_score=pe_final_score,
                status="completed",
            )
            await _ws_send({"type": "judge_complete", "job_id": job_id, "judge_report_id": judge_report_id})
        except Exception as je:
            logger.exception("Post-eval judge failed: job_id=%s", job_id)
            if judge_report_id:
                await db.update_judge_report(judge_report_id, status="error")
            await _ws_send({
                "type": "judge_failed",
                "job_id": job_id,
                "detail": f"Post-eval judge failed: {je}",
            })

    # --- Live inline judge: save report ---
    elif judge_enabled and judge_mode == "live_inline" and judge_verdicts:
        try:
            _judge_db_model_li = await db.get_model_by_litellm_id(user_id, judge_target.model_id)
            _judge_db_model_id_li = _judge_db_model_li["id"] if _judge_db_model_li else None
            judge_report_id = await db.save_judge_report(
                user_id=user_id,
                mode="live_inline",
                eval_run_id=eval_id,
                experiment_id=experiment_id,
                judge_model_id=_judge_db_model_id_li,
                custom_instructions=judge_custom_instructions or None,
            )
            model_results_j: dict[str, list[dict]] = {}
            for v in judge_verdicts:
                mid = v.get("model_id", "unknown")
                model_results_j.setdefault(mid, []).append(v)

            all_li_reports = []
            for mid, mvds in model_results_j.items():
                tgt = target_map.get(mid)
                mname = tgt.display_name if tgt else mid
                li_report_data = await _judge_crosscase(judge_target, mname, mvds)
                li_report_data["model_id"] = mid
                li_report_data["model_name"] = mname
                all_li_reports.append(li_report_data)
                await _ws_send({"type": "judge_report", "job_id": job_id, "eval_id": eval_id, "report": li_report_data})

                # ERD v2: Save verdicts to DB
                for v in mvds:
                    try:
                        cr_key = f"{v.get('model_id', '')}::{v.get('test_case_id', '')}"
                        cr_id = case_result_db_ids.get(cr_key)
                        if cr_id and judge_report_id:
                            await db.save_judge_verdict(
                                report_id=judge_report_id,
                                case_result_id=cr_id,
                                quality_score=v.get("quality_score", 0),
                                verdict=v.get("verdict", "fail"),
                                summary=v.get("summary", ""),
                                reasoning=v.get("reasoning", ""),
                                tool_selection_assessment=v.get("tool_selection_assessment", "unknown"),
                                param_assessment=v.get("param_assessment", "unknown"),
                                judge_override_score=v.get("judge_override_score"),
                                override_reason=v.get("override_reason"),
                            )
                    except Exception as ve:
                        logger.warning("Failed to save live inline judge verdict: %s", ve)
                        await _ws_send({
                            "type": "eval_warning",
                            "job_id": job_id,
                            "detail": f"Failed to save judge verdict: {ve}",
                        })

            # Derive overall grade/score from the best model's report
            if all_li_reports:
                best_li = max(all_li_reports, key=lambda r: r.get("overall_score", 0))
                li_final_grade = best_li.get("overall_grade", "?")
                li_final_score = best_li.get("overall_score", 0)
            else:
                li_final_grade = "?"
                li_final_score = 0

            await db.update_judge_report(
                judge_report_id,
                report_json=json.dumps(all_li_reports),
                overall_grade=li_final_grade,
                overall_score=li_final_score,
                status="completed",
            )
            await _ws_send({"type": "judge_complete", "job_id": job_id, "judge_report_id": judge_report_id})
        except Exception as je:
            logger.exception("Live inline judge report failed: job_id=%s", job_id)
            if judge_report_id:
                await db.update_judge_report(judge_report_id, status="error")

    # Send completion event (M6: include delta if experiment has baseline)
    complete_evt = {
        "type": "tool_eval_complete",
        "job_id": job_id,
        "eval_id": eval_id,
        "judge_report_id": judge_report_id,
    }
    if experiment_id:
        try:
            exp = await db.get_experiment(experiment_id, user_id)
            if exp and exp.get("baseline_score") is not None:
                avg_score = _avg_overall_from_summaries(summaries)
                complete_evt["delta"] = round(avg_score - exp["baseline_score"], 4)
                complete_evt["baseline_score"] = exp["baseline_score"]
        except Exception:
            logger.warning("Failed to compute delta for experiment %s", experiment_id)
            await _ws_send({
                "type": "eval_warning",
                "job_id": job_id,
                "detail": "Failed to compute baseline comparison delta",
            })
    await _ws_send(complete_evt)

    logger.info(
        "Tool eval completed: job_id=%s user_id=%s results=%d eval_id=%s",
        job_id, user_id, len(all_results), eval_id,
    )

    # --- 2D: Public Leaderboard contribution (if user opted in) ---
    if eval_id and summaries:
        try:
            opted_in = await db.get_user_leaderboard_opt_in(user_id)
            if opted_in:
                for summary in summaries:
                    model_name = summary.get("model_name", summary.get("model_id", ""))
                    provider = summary.get("provider", "")
                    if not model_name:
                        continue
                    await db.upsert_leaderboard_entry(
                        model_name=model_name,
                        provider=provider,
                        tool_accuracy_pct=summary.get("tool_accuracy_pct", 0.0),
                        param_accuracy_pct=summary.get("param_accuracy_pct", 0.0),
                        irrel_accuracy_pct=summary.get("irrelevance_pct"),
                        sample_count=summary.get("cases_run", 0),
                    )
                logger.info(
                    "Leaderboard updated: user_id=%s models=%d eval_id=%s",
                    user_id, len(summaries), eval_id,
                )
        except Exception:
            logger.exception("Leaderboard contribution failed: eval_id=%s", eval_id)
            await _ws_send({
                "type": "eval_warning",
                "job_id": job_id,
                "detail": "Leaderboard update failed — eval results saved normally",
            })

    # --- Auto-judge: submit a separate judge job if explicitly requested ---
    auto_judge = params.get("auto_judge", False)
    auto_judge_threshold = float(params.get("auto_judge_threshold") or 0.70)
    if auto_judge and eval_id and not judge_report_id:
        # Only auto-judge if no judge was already run inline/post-eval
        # AND avg score is below the threshold (threshold slider has effect)
        avg_score_for_autojudge = _avg_overall_from_summaries(summaries)
        if avg_score_for_autojudge > auto_judge_threshold:
            logger.info(
                "Auto-judge skipped: avg_score=%.2f > threshold=%.2f eval_id=%s",
                avg_score_for_autojudge, auto_judge_threshold, eval_id,
            )
            await _ws_send({
                "type": "auto_judge_skipped",
                "job_id": job_id,
                "reason": "score_above_threshold",
                "detail": f"Average score {avg_score_for_autojudge:.0%} is above the {auto_judge_threshold:.0%} threshold",
            })
        else:
            try:
                # Load user's judge settings for default model
                user_cfg = await _get_user_config(user_id)
                judge_settings = user_cfg.get("judge_settings", {})

                default_judge_model = judge_settings.get("default_judge_model")
                if default_judge_model:
                    judge_params = {
                        "user_id": user_id,
                        "user_email": params.get("user_email", ""),
                        "eval_run_id": eval_id,
                        "judge_model": default_judge_model,
                        "judge_provider_key": judge_settings.get("default_judge_provider_key"),
                        "custom_instructions": judge_settings.get("custom_instructions_template", ""),
                        "concurrency": judge_settings.get("concurrency", 4),
                        "experiment_id": experiment_id,
                    }
                    await job_registry.submit("judge", user_id, judge_params)
                    logger.info(
                        "Auto-judge submitted: eval_id=%s judge_model=%s avg_score=%.2f threshold=%.2f",
                        eval_id, default_judge_model, avg_score_for_autojudge, auto_judge_threshold,
                    )
                else:
                    logger.debug("Auto-judge skipped: no default_judge_model configured for user=%s", user_id)
                    await _ws_send({
                        "type": "auto_judge_skipped",
                        "job_id": job_id,
                        "reason": "no_judge_model",
                        "detail": "No default judge model configured. Set one in Settings > Judge.",
                    })
            except Exception as e:
                logger.warning("Auto-judge submission failed: %s", e)
                await _ws_send({
                    "type": "auto_judge_skipped",
                    "job_id": job_id,
                    "reason": "submission_failed",
                    "detail": f"Auto-judge failed to start: {e}",
                })

    # --- Judge Wiring: auto-trigger judge on low accuracy and store explanations ---
    # Runs when no explicit judge was already used and avg accuracy is below threshold.
    if eval_id and not judge_report_id and all_results:
        try:
            avg_score = _avg_overall_from_summaries(summaries)
            LOW_ACCURACY_THRESHOLD = 0.70  # 70%

            if avg_score < LOW_ACCURACY_THRESHOLD:
                # Load judge settings to find a judge model
                _user_cfg = await _get_user_config(user_id)
                _judge_settings = _user_cfg.get("judge_settings", {})

                _auto_judge_after_eval = _judge_settings.get("auto_judge_after_eval", False)
                _default_judge_model = _judge_settings.get("default_judge_model")

                if _auto_judge_after_eval and _default_judge_model:
                    logger.info(
                        "Low accuracy (%.0f%% < %.0f%%) -- auto-triggering judge: eval_id=%s model=%s",
                        avg_score * 100, LOW_ACCURACY_THRESHOLD * 100, eval_id, _default_judge_model,
                    )
                    # Resolve judge target
                    _jt_list = _find_target(all_targets, _default_judge_model, _judge_settings.get("default_judge_provider_key"))
                    _jt = _jt_list[0] if _jt_list else None
                    if _jt:
                        # Inject API key
                        if _jt.provider_key:
                            _enc = await db.get_user_key_for_provider(user_id, _jt.provider_key)
                            if _enc:
                                _jt = inject_user_keys([_jt], {_jt.provider_key: _enc})[0]

                        # Run judge on failed cases only (overall_score < 1.0)
                        _failed = [r for r in all_results if r.get("success") and r.get("overall_score", 1.0) < 1.0]
                        if not _failed:
                            _failed = all_results  # judge everything if all passed (edge case)

                        _ci = _judge_settings.get("custom_instructions_template", "")
                        _td = _build_tool_definitions_text(tools)
                        _expls = []
                        _jsem = asyncio.Semaphore(int(_judge_settings.get("concurrency", 4)))

                        async def _expl_one(r):
                            async with _jsem:
                                v = await _judge_single_verdict(_jt, _td, {}, r, custom_instructions=_ci)
                                return {
                                    "test_case_id": r.get("test_case_id", "?"),
                                    "model_id": r.get("model_id", "?"),
                                    "reasoning": v.get("reasoning", ""),
                                    "summary": v.get("summary", ""),
                                    "quality_score": v.get("quality_score", 0),
                                    "verdict": v.get("verdict", ""),
                                }

                        _expl_tasks = [asyncio.create_task(_expl_one(r)) for r in _failed]
                        _expl_results = await asyncio.gather(*_expl_tasks, return_exceptions=True)
                        for er in _expl_results:
                            if isinstance(er, dict):
                                _expls.append(er)
                            else:
                                logger.debug("Judge explanation task failed: %s", er)

                        if _expls:
                            # ERD v2: Explanations are stored as judge verdicts in a separate report
                            # Create a lightweight judge report for low-accuracy explanations
                            try:
                                _expl_judge_db = await db.get_model_by_litellm_id(user_id, _default_judge_model)
                                _expl_judge_db_id = _expl_judge_db["id"] if _expl_judge_db else None
                                _expl_report_id = await db.save_judge_report(
                                    user_id=user_id,
                                    mode="post_eval",
                                    eval_run_id=eval_id,
                                    judge_model_id=_expl_judge_db_id,
                                    custom_instructions=_ci or None,
                                )
                                for expl in _expls:
                                    cr_key = f"{expl.get('model_id', '')}::{expl.get('test_case_id', '')}"
                                    cr_id = case_result_db_ids.get(cr_key)
                                    if cr_id:
                                        await db.save_judge_verdict(
                                            report_id=_expl_report_id,
                                            case_result_id=cr_id,
                                            quality_score=expl.get("quality_score", 0),
                                            verdict=expl.get("verdict", "fail"),
                                            summary=expl.get("summary", ""),
                                            reasoning=expl.get("reasoning", ""),
                                        )
                                await db.update_judge_report(
                                    _expl_report_id,
                                    status="completed",
                                )
                            except Exception as expl_e:
                                logger.warning("Failed to save low-accuracy judge explanations: %s", expl_e)

                            await _ws_send({
                                "type": "judge_explanations_ready",
                                "job_id": job_id,
                                "eval_id": eval_id,
                                "explanation_count": len(_expls),
                                "avg_score": round(avg_score, 4),
                            })
                            logger.info(
                                "Judge explanations stored: eval_id=%s count=%d",
                                eval_id, len(_expls),
                            )
        except Exception:
            logger.exception("Judge wiring (low-accuracy auto-judge) failed: eval_id=%s", eval_id)

    return eval_id


# ---------------------------------------------------------------------------
# Param Tune Handler
# ---------------------------------------------------------------------------

def _build_optuna_combos(search_space: dict, n_trials: int, mode: str) -> list[dict]:
    """2A: Build parameter combinations using Optuna (random or Bayesian/TPE).

    Returns a list of combo dicts similar to _expand_search_space output,
    but sampled via Optuna rather than exhaustive enumeration.
    """
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    if mode == "bayesian":
        sampler = optuna.samplers.TPESampler(seed=42)
    else:
        # "random" mode
        sampler = optuna.samplers.RandomSampler(seed=42)

    study = optuna.create_study(direction="maximize", sampler=sampler)

    # Build param space definition from search_space
    # search_space format: {"temperature": [0.0, 0.5, 1.0], "top_p": [0.9, 0.95]}
    combos = []

    _float_params = frozenset(("temperature", "top_p", "min_p", "repetition_penalty", "frequency_penalty", "presence_penalty"))

    def objective(trial):
        combo = {}
        for param_name, values in search_space.items():
            # Handle dict format {min, max, step} from SearchSpaceBuilder
            if isinstance(values, dict) and "min" in values and "max" in values:
                min_v = float(values["min"])
                max_v = float(values["max"])
                step_v = float(values.get("step", 0.1))
                if min_v == max_v:
                    combo[param_name] = min_v
                elif param_name in _float_params or isinstance(values.get("min"), float) or isinstance(values.get("max"), float):
                    combo[param_name] = trial.suggest_float(param_name, min_v, max_v, step=step_v)
                else:
                    # Integer param (e.g. top_k, max_tokens)
                    combo[param_name] = trial.suggest_int(param_name, int(min_v), int(max_v), step=max(1, int(step_v)))
                continue
            # Handle list format (categorical/enum params)
            if not isinstance(values, list) or not values:
                continue
            # For numeric params with >2 distinct values, use float range
            if len(values) >= 2 and all(isinstance(v, (int, float)) for v in values):
                min_v = min(values)
                max_v = max(values)
                if min_v == max_v:
                    combo[param_name] = min_v
                elif param_name in _float_params:
                    combo[param_name] = trial.suggest_float(param_name, min_v, max_v)
                else:
                    # Integer param (e.g. top_k, max_tokens)
                    combo[param_name] = trial.suggest_int(param_name, int(min_v), int(max_v))
            else:
                # Categorical param
                combo[param_name] = trial.suggest_categorical(param_name, values)
        combos.append(dict(combo))
        return 0.0  # Objective will be updated externally

    # Generate n_trials combinations by running the study
    study.optimize(objective, n_trials=n_trials)

    return combos


async def param_tune_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for parameter tuning (grid/random/Bayesian).

    Returns the tune_id on success, or None.
    """
    user_id = params["user_id"]
    suite_id = params["suite_id"]
    model_ids = params["models"]
    _raw_ts = params.get("target_set")  # serialized as list-of-lists
    target_set = {tuple(t) for t in _raw_ts} if _raw_ts else None
    search_space = params.get("search_space", {})
    per_model_search_spaces = params.get("per_model_search_spaces", {})
    experiment_id = params.get("experiment_id")
    # 2A: optimization mode
    optimization_mode = params.get("optimization_mode", "grid")
    n_trials = int(params.get("n_trials", 50))

    logger.info(
        "Param tune started: job_id=%s user_id=%s models=%d",
        job_id, user_id, len(model_ids),
    )

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    cases = await db.get_test_cases(suite_id)

    # ERD v2: Load tools from tool_definitions table
    tool_defs = await db.get_tool_definitions(suite_id)
    tools = _tool_defs_to_openai(tool_defs)

    # Build targets first (may differ from model_ids if duplicates exist)
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    targets = _filter_targets(all_targets, model_ids, target_set)

    # Expand search spaces -- use len(targets) not len(model_ids) for accurate count
    # 2A: Use Optuna for random/bayesian modes; grid is exhaustive enumeration
    per_model_combos: dict[str, list[dict]] = {}
    if per_model_search_spaces and isinstance(per_model_search_spaces, dict):
        for mid, ss in per_model_search_spaces.items():
            if isinstance(ss, dict) and ss:
                if optimization_mode in ("random", "bayesian"):
                    per_model_combos[mid] = _build_optuna_combos(ss, n_trials, optimization_mode)
                else:
                    per_model_combos[mid] = _expand_search_space(ss)
        if optimization_mode in ("random", "bayesian") and search_space:
            combos = _build_optuna_combos(search_space, n_trials, optimization_mode)
        else:
            combos = _expand_search_space(search_space) if search_space else [{}]
    else:
        if optimization_mode in ("random", "bayesian"):
            combos = _build_optuna_combos(search_space, n_trials, optimization_mode)
        else:
            combos = _expand_search_space(search_space)

    # Pre-validate and deduplicate combos per target.
    validated_target_combos: dict[str, list[tuple[dict, dict, list[dict]]]] = {}
    for t in targets:
        tkey = _target_key(t)
        raw_combos = per_model_combos.get(t.model_id, combos)
        prov_key = identify_provider(t.model_id, getattr(t, "provider_key", None))
        seen: set[tuple] = set()
        unique: list[tuple[dict, dict, list[dict]]] = []
        for combo in raw_combos:
            # Build the same params_to_check that run_provider would
            temp = float(combo.get("temperature", 0.0))
            pp = {k: v for k, v in combo.items() if k not in ("temperature", "tool_choice", "max_tokens")}
            params_to_check = {"temperature": temp, **pp}
            validation = validate_params(prov_key, t.model_id, params_to_check)
            resolved = validation["resolved_params"]
            adjustments = validation.get("adjustments", [])
            # Dedup key: sorted tuple of resolved params + tool_choice (which isn't validated)
            tc = combo.get("tool_choice", "required")
            dedup_key = (tc,) + tuple(sorted(resolved.items()))
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique.append((combo, resolved, adjustments))
        validated_target_combos[tkey] = unique

    total_combos = sum(len(validated_target_combos.get(_target_key(t), [])) for t in targets)

    # Inject per-user API keys
    user_keys_cache = {}
    for t in targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    targets = inject_user_keys(targets, user_keys_cache)

    # ERD v2: Create param tune run BEFORE the loop
    tune_id = await db.save_param_tune_run(
        user_id=user_id,
        suite_id=suite["id"],
        search_space_json=json.dumps(per_model_search_spaces if per_model_combos else search_space),
        total_combos=total_combos,
        optimization_mode=optimization_mode,
        n_trials=n_trials if optimization_mode != "grid" else None,
        experiment_id=experiment_id,
    )

    # Store result_ref early so the frontend can discover tune_id on reconnect
    await db.set_job_result_ref(job_id, tune_id)

    # Pre-resolve model DB IDs
    model_db_id_cache: dict[str, str | None] = {}

    # Helper to send WebSocket messages directly to the user
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Notify frontend of tune start
    await _ws_send({
        "type": "tune_start",
        "job_id": job_id,
        "tune_id": tune_id,
        "total_combos": total_combos,
        "models": model_ids,
        "suite_name": suite["name"],
    })

    # Emit param adjustment warnings (aggregate unique adjustments per model)
    _adj_models = []
    for t in targets:
        tkey = _target_key(t)
        seen_adj_keys: set[str] = set()
        unique_adj: list[dict] = []
        for _combo, _resolved, combo_adjustments in validated_target_combos.get(tkey, []):
            for adj in combo_adjustments:
                adj_key = f"{adj.get('param')}:{adj.get('action')}"
                if adj_key not in seen_adj_keys and adj.get("action") != "passthrough":
                    seen_adj_keys.add(adj_key)
                    unique_adj.append(adj)
        if unique_adj:
            _adj_models.append({
                "model_id": t.model_id,
                "provider": identify_provider(t.model_id, getattr(t, "provider_key", None)),
                "provider_key": getattr(t, "provider_key", None),
                "adjustments": unique_adj,
            })
    if _adj_models:
        await _ws_send({
            "type": "param_adjustments",
            "job_id": job_id,
            "models": _adj_models,
        })

    start_time = time.perf_counter()
    all_results = []
    completed = 0
    results_queue = asyncio.Queue()

    # Group targets by provider
    provider_groups: dict[str, list[Target]] = {}
    for target in targets:
        provider_groups.setdefault(target.provider, []).append(target)

    async def run_provider(prov_targets):
        """Run all combos for all models in this provider group."""
        for target in prov_targets:
            target_combos = validated_target_combos.get(_target_key(target), [])
            for combo_idx, (combo, resolved, combo_adjustments) in enumerate(target_combos):
                if cancel_event.is_set():
                    return

                # Extract combo params (use resolved values from pre-validation)
                temp = float(resolved.get("temperature", combo.get("temperature", 0.0)))
                tc = combo.get("tool_choice", "required")

                # Build provider_params from resolved (tier2 params, already validated)
                pp = {}
                for k, v in resolved.items():
                    if k not in ("temperature", "tool_choice", "max_tokens"):
                        pp[k] = v

                # Run all test cases for this combo
                case_results = []
                for case in cases:
                    if cancel_event.is_set():
                        return

                    # Check if multi-turn
                    mt_config = None
                    if case.get("multi_turn_config"):
                        try:
                            mt_config = json.loads(case["multi_turn_config"]) if isinstance(case["multi_turn_config"], str) else case["multi_turn_config"]
                        except (json.JSONDecodeError, TypeError):
                            logger.debug("Failed to parse multi_turn_config in param tuner")
                            mt_config = None

                    if mt_config and mt_config.get("multi_turn"):
                        case_with_mt = {**case, "_mt_config": mt_config}
                        r = await run_multi_turn_eval(target, tools, case_with_mt, temp, tc, provider_params=pp if pp else None)
                    else:
                        r = await run_single_eval(target, tools, case, temp, tc, provider_params=pp if pp else None)
                    case_results.append(r)

                # Compute aggregate scores for this combo
                tool_scores = [r["tool_selection_score"] for r in case_results if r.get("success")]
                param_scores = [r["param_accuracy"] for r in case_results if r.get("success") and r.get("param_accuracy") is not None]
                overall_scores = [r["overall_score"] for r in case_results if r.get("success")]
                latencies = [r["latency_ms"] for r in case_results if r.get("success") and r.get("latency_ms")]

                cases_passed = sum(1 for r in case_results if r.get("success") and r.get("overall_score", 0) == 1.0)

                # Trim case results for WS (exclude raw_request/raw_response)
                trimmed_cases = []
                for cr in case_results:
                    trimmed_cases.append({
                        "test_case_id": cr.get("test_case_id"),
                        "prompt": cr.get("prompt", ""),
                        "expected_tool": cr.get("expected_tool"),
                        "actual_tool": cr.get("actual_tool"),
                        "expected_params": cr.get("expected_params"),
                        "actual_params": cr.get("actual_params"),
                        "tool_selection_score": cr.get("tool_selection_score", 0.0),
                        "param_accuracy": cr.get("param_accuracy"),
                        "overall_score": cr.get("overall_score", 0.0),
                        "success": cr.get("success", False),
                        "error": cr.get("error", ""),
                        "latency_ms": cr.get("latency_ms", 0),
                    })

                combo_result = {
                    "combo_index": combo_idx,
                    "model_id": target.model_id,
                    "provider_key": target.provider_key or "",
                    "model_name": target.display_name,
                    "config": combo,
                    "overall_score": round(sum(overall_scores) / len(overall_scores), 4) if overall_scores else 0.0,
                    "tool_accuracy": round(sum(tool_scores) / len(tool_scores) * 100, 2) if tool_scores else 0.0,
                    "param_accuracy": round(sum(param_scores) / len(param_scores) * 100, 2) if param_scores else 0.0,
                    "latency_avg_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
                    "cases_passed": cases_passed,
                    "cases_total": len(cases),
                    "adjustments": combo_adjustments,
                    "case_results": trimmed_cases,
                }

                await results_queue.put(combo_result)

    # Launch provider groups in parallel
    tasks = [asyncio.create_task(run_provider(g)) for g in provider_groups.values()]

    async def sentinel():
        await asyncio.gather(*tasks, return_exceptions=True)
        await results_queue.put(None)

    asyncio.create_task(sentinel())

    # Consume results and report progress via WebSocket
    while True:
        try:
            item = await asyncio.wait_for(results_queue.get(), timeout=15)
        except asyncio.TimeoutError:
            continue  # Keep waiting
        if item is None:
            break
        if cancel_event.is_set():
            for t in tasks:
                t.cancel()
            # Save partial results
            duration = time.perf_counter() - start_time
            await db.update_param_tune_run(
                tune_id, user_id,
                completed_combos=completed,
                status="cancelled",
                duration_s=round(duration, 2),
                best_config_json=json.dumps(_find_best_config(all_results)),
                best_score=_find_best_score(all_results),
            )
            return None

        completed += 1
        all_results.append(item)

        # Send combo result to frontend via WebSocket
        await _ws_send({
            "type": "combo_result",
            "job_id": job_id,
            "tune_id": tune_id,
            "data": item,
        })

        # ERD v2: Persist combo to param_tune_combos table
        try:
            model_litellm_id = item.get("model_id", "")
            if model_litellm_id not in model_db_id_cache:
                model_db_id_cache[model_litellm_id] = await _resolve_model_db_id(user_id, model_litellm_id)
            model_db_id = model_db_id_cache[model_litellm_id]

            if model_db_id:
                await db.save_param_tune_combo(
                    tune_run_id=tune_id,
                    combo_index=item.get("combo_index", completed - 1),
                    model_id=model_db_id,
                    config_json=json.dumps(item.get("config", {})),
                    overall_score=item.get("overall_score", 0.0),
                    tool_accuracy_pct=item.get("tool_accuracy", 0.0),
                    param_accuracy_pct=item.get("param_accuracy", 0.0),
                    latency_avg_ms=item.get("latency_avg_ms", 0),
                    cases_passed=item.get("cases_passed", 0),
                    cases_total=item.get("cases_total", 0),
                    adjustments_json=json.dumps(item.get("adjustments")) if item.get("adjustments") else None,
                )
        except Exception as e:
            logger.warning("Failed to save param_tune_combo: %s", e)
            await _ws_send({
                "type": "eval_warning",
                "job_id": job_id,
                "detail": f"Failed to save combo result: {e}",
            })

        # Update parent run progress
        await db.update_param_tune_run(
            tune_id, user_id,
            completed_combos=completed,
        )

        # Update job progress
        pct = int((completed / total_combos) * 100) if total_combos > 0 else 0
        detail = f"{item['model_name']}, combo {completed}/{total_combos}"
        await progress_cb(pct, detail)

    # Tuning complete -- find best config
    duration = time.perf_counter() - start_time
    best_config = _find_best_config(all_results)
    best_score = _find_best_score(all_results)

    # Phase 5: Look up best_profile_id if best result has a model with a matching profile
    best_profile_id = None
    if all_results and best_config:
        try:
            best_result = max(all_results, key=lambda r: r.get("overall_score", 0))
            best_model_id = best_result.get("model_id")
            if best_model_id:
                # Check if there's a default profile for this model
                profile = await db.get_default_profile(user_id, best_model_id)
                if profile:
                    best_profile_id = profile["id"]
        except Exception as e:
            logger.debug("Could not resolve best_profile_id: %s", e)

    await db.update_param_tune_run(
        tune_id, user_id,
        best_config_json=json.dumps(best_config),
        best_score=best_score,
        completed_combos=completed,
        status="completed",
        duration_s=round(duration, 2),
        best_profile_id=best_profile_id,
    )

    # Send completion event to frontend
    await _ws_send({
        "type": "tune_complete",
        "job_id": job_id,
        "tune_id": tune_id,
        "best_config": best_config,
        "best_score": best_score,
        "duration_s": round(duration, 2),
    })

    logger.info(
        "Param tune completed: job_id=%s tune_id=%s user_id=%s combos=%d best_score=%.4f",
        job_id, tune_id, user_id, completed, best_score,
    )

    # --- Auto-promote best result to tool_eval_runs (if in experiment) ---
    if experiment_id and all_results:
        try:
            best = max(all_results, key=lambda r: r.get("overall_score", 0))
            if best.get("case_results"):
                # Build full results list (add model_id to each case result)
                promoted_results = []
                for cr in best["case_results"]:
                    promoted_results.append({
                        **cr,
                        "model_id": best["model_id"],
                        "model_name": best.get("model_name", best["model_id"]),
                    })

                # Build config_json from the winning combo
                combo_config = best.get("config", {})
                config_for_promote = {
                    "temperature": combo_config.get("temperature", 0.0),
                    "tool_choice": combo_config.get("tool_choice", "required"),
                }
                pp = {k: v for k, v in combo_config.items()
                      if k not in ("temperature", "tool_choice", "max_tokens")}
                if pp:
                    config_for_promote["provider_params"] = pp

                # Add promoted_from marker
                config_for_promote["promoted_from"] = f"param_tune:{tune_id}"

                # ERD v2: Create promoted eval run as a child of param_tune
                promoted_eval_id = await db.save_tool_eval_run(
                    user_id=user_id,
                    suite_id=suite["id"],
                    temperature=config_for_promote.get("temperature", 0.0),
                    tool_choice=config_for_promote.get("tool_choice", "required"),
                    provider_params_json=json.dumps(pp) if pp else None,
                    experiment_id=experiment_id,
                    orchestrator_type="param_tune",
                    orchestrator_run_id=tune_id,
                )

                # Save case results for the promoted eval
                for cr in promoted_results:
                    try:
                        model_litellm_id = cr.get("model_id", "")
                        if model_litellm_id not in model_db_id_cache:
                            model_db_id_cache[model_litellm_id] = await _resolve_model_db_id(user_id, model_litellm_id)
                        m_db_id = model_db_id_cache[model_litellm_id]
                        if m_db_id:
                            actual_params_str = json.dumps(cr.get("actual_params")) if cr.get("actual_params") is not None else None
                            await db.save_case_result(
                                eval_run_id=promoted_eval_id,
                                test_case_id=cr.get("test_case_id", ""),
                                model_id=m_db_id,
                                tool_selection_score=cr.get("tool_selection_score", 0.0),
                                param_accuracy=cr.get("param_accuracy"),
                                overall_score=cr.get("overall_score", 0.0),
                                actual_tool=cr.get("actual_tool"),
                                actual_params=actual_params_str,
                                success=cr.get("success", True),
                                error=cr.get("error", ""),
                                latency_ms=cr.get("latency_ms", 0),
                            )
                    except Exception as cr_e:
                        logger.warning("Failed to save promoted case result: %s", cr_e)

                await _maybe_update_experiment_best(
                    experiment_id, user_id,
                    score=best.get("overall_score", 0.0),
                    config_json=json.dumps(config_for_promote),
                    source="param_tune",
                    source_id=tune_id,
                )

                await _ws_send({
                    "type": "eval_promoted",
                    "job_id": job_id,
                    "tune_id": tune_id,
                    "promoted_eval_id": promoted_eval_id,
                    "experiment_id": experiment_id,
                    "score": best.get("overall_score", 0.0),
                })

                logger.info(
                    "Param tune auto-promoted: tune_id=%s promoted_eval_id=%s experiment_id=%s",
                    tune_id, promoted_eval_id, experiment_id,
                )
        except Exception:
            logger.exception("Auto-promote failed for param tune: tune_id=%s", tune_id)
            await _ws_send({
                "type": "eval_warning",
                "job_id": job_id,
                "detail": "Auto-promote to experiment failed — results saved but experiment not updated",
            })

    return tune_id


# ---------------------------------------------------------------------------
# Prompt Tune Handler
# ---------------------------------------------------------------------------

async def prompt_tune_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for prompt tuning (Quick or Evolutionary).

    Returns the tune_id on success, or None on cancel.
    """
    # Import here to avoid circular imports (prompt_tune module defines
    # _generate_prompts_meta and the prompt constants)
    from routers.prompt_tune import (
        _generate_prompts_meta,
        _QUICK_META_PROMPT,
        _EVO_META_PROMPT,
        _DEFAULT_BASE_PROMPT,
    )

    user_id = params["user_id"]
    suite_id = params["suite_id"]
    target_model_ids = params["target_models"]
    _raw_ts = params.get("target_set")
    target_set_eval = {tuple(t) for t in _raw_ts} if _raw_ts else None
    meta_model_id = params["meta_model"]
    meta_provider_key = params.get("meta_provider_key")
    mode = params.get("mode", "quick")
    base_prompt = params.get("base_prompt") or _DEFAULT_BASE_PROMPT
    cfg = params.get("config", {})
    experiment_id = params.get("experiment_id")

    population_size = int(cfg.get("population_size", 5))
    generations = int(cfg.get("generations", 1 if mode == "quick" else 3))
    selection_ratio = float(cfg.get("selection_ratio", 0.4))
    eval_temperature = float(cfg.get("temperature", 0.0))
    eval_tool_choice = cfg.get("tool_choice", "required")

    if mode == "quick":
        generations = 1

    population_size = max(3, min(population_size, 20))
    generations = max(1, min(generations, 10))
    selection_ratio = max(0.2, min(selection_ratio, 0.8))
    total_prompts = population_size * generations

    logger.info(
        "Prompt tune started: job_id=%s user_id=%s mode=%s total_prompts=%d",
        job_id, user_id, mode, total_prompts,
    )

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    cases = await db.get_test_cases(suite_id)

    # ERD v2: Load tools from tool_definitions table
    tool_defs = await db.get_tool_definitions(suite_id)
    tools = _tool_defs_to_openai(tool_defs)

    # Build targets
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)

    # Find meta-model target
    meta_targets = _find_target(all_targets, meta_model_id, meta_provider_key)
    eval_targets = _filter_targets(all_targets, target_model_ids, target_set_eval)

    # Inject user keys
    user_keys_cache = {}
    for t in meta_targets + eval_targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    meta_targets = inject_user_keys(meta_targets, user_keys_cache)
    eval_targets = inject_user_keys(eval_targets, user_keys_cache)

    meta_target = meta_targets[0]

    tools_summary = _build_tools_summary(tools)
    test_cases_summary = _build_test_cases_summary(cases)
    total_eval_calls = total_prompts * len(cases) * len(eval_targets)

    # Resolve DB model ID for the meta model FK
    _meta_db_model = await db.get_model_by_litellm_id(user_id, meta_model_id)
    _meta_db_model_id = _meta_db_model["id"] if _meta_db_model else None

    # ERD v2: Create prompt tune run with decomposed config columns
    tune_id = await db.save_prompt_tune_run(
        user_id=user_id,
        suite_id=suite["id"],
        mode=mode,
        base_prompt=base_prompt,
        total_prompts=total_prompts,
        population_size=population_size,
        generations=generations,
        selection_ratio=selection_ratio,
        eval_temperature=eval_temperature,
        eval_tool_choice=eval_tool_choice,
        experiment_id=experiment_id,
        meta_model_id=_meta_db_model_id,
    )

    # Store result_ref early so the frontend can discover tune_id on reconnect
    await db.set_job_result_ref(job_id, tune_id)

    # Helper to send WebSocket messages directly to the user
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Notify frontend of tune start
    await _ws_send({
        "type": "tune_start",
        "job_id": job_id,
        "tune_id": tune_id,
        "mode": mode,
        "total_prompts": total_prompts,
        "total_eval_calls": total_eval_calls,
        "suite_name": suite["name"],
    })

    start_time = time.perf_counter()
    all_generations = []
    completed_prompts = 0
    best_prompt = None
    best_score = 0.0
    best_prompt_origin = None
    survivors = []  # For evolutionary mode

    for gen_num in range(1, generations + 1):
        if cancel_event.is_set():
            break

        await _ws_send({
            "type": "generation_start",
            "job_id": job_id,
            "tune_id": tune_id,
            "generation": gen_num,
            "total_generations": generations,
            "population_size": population_size,
        })

        # --- Generate prompts ---
        if gen_num == 1 or mode == "quick":
            raw_prompts = await _generate_prompts_meta(
                meta_target, _QUICK_META_PROMPT,
                n=population_size,
                tools_summary=tools_summary,
                test_cases_summary=test_cases_summary,
                base_prompt=base_prompt,
            )
        else:
            parent_text = "\n".join(
                f"#{i+1} (score={s['avg_score']:.2f}): \"{s['text'][:200]}...\""
                for i, s in enumerate(survivors)
            )
            raw_prompts = await _generate_prompts_meta(
                meta_target, _EVO_META_PROMPT,
                n=population_size,
                tools_summary=tools_summary,
                test_cases_summary=test_cases_summary,
                parent_prompts=parent_text,
            )

        # Handle empty meta response
        if not raw_prompts:
            logger.warning(
                "Meta model returned 0 prompts for generation %d -- skipping",
                gen_num,
            )
            await _ws_send({
                "type": "generation_error",
                "job_id": job_id,
                "tune_id": tune_id,
                "generation": gen_num,
                "message": "Meta model returned no prompts. Skipping generation.",
            })
            continue

        # Normalize to list of prompt dicts
        gen_prompts = []
        for idx, rp in enumerate(raw_prompts[:population_size]):
            text = rp.get("prompt", "") if isinstance(rp, dict) else str(rp)
            style = rp.get("style", rp.get("mutation_type", "variation")) if isinstance(rp, dict) else "variation"
            parent_idx = rp.get("parent_index") if isinstance(rp, dict) else None
            gen_prompts.append({
                "index": idx,
                "style": style,
                "text": text,
                "parent_index": parent_idx,
                "mutation_type": rp.get("mutation_type") if isinstance(rp, dict) else None,
                "scores": {},
                "avg_score": 0.0,
                "survived": False,
            })

            await _ws_send({
                "type": "prompt_generated",
                "job_id": job_id,
                "tune_id": tune_id,
                "generation": gen_num,
                "prompt_index": idx,
                "prompt_text": text[:300],
                "style": style,
                "parent_prompt": parent_idx,
            })

        # --- Evaluate each prompt ---
        for p_info in gen_prompts:
            if cancel_event.is_set():
                break

            model_scores = {}
            for target in eval_targets:
                if cancel_event.is_set():
                    break

                await _ws_send({
                    "type": "prompt_eval_start",
                    "job_id": job_id,
                    "tune_id": tune_id,
                    "generation": gen_num,
                    "prompt_index": p_info["index"],
                    "model": target.display_name,
                })

                # Run all test cases with this prompt as system_prompt
                case_results = []
                for case in cases:
                    if cancel_event.is_set():
                        break

                    # Dispatch: multi-turn or single-turn
                    mt_config = None
                    if case.get("multi_turn_config"):
                        try:
                            mt_config = json.loads(case["multi_turn_config"]) if isinstance(case["multi_turn_config"], str) else case["multi_turn_config"]
                        except (json.JSONDecodeError, TypeError):
                            mt_config = None

                    if mt_config and mt_config.get("multi_turn"):
                        case_with_mt = {**case, "_mt_config": mt_config}
                        r = await run_multi_turn_eval(
                            target, tools, case_with_mt, eval_temperature,
                            eval_tool_choice, system_prompt=p_info["text"],
                        )
                    else:
                        r = await run_single_eval(
                            target, tools, case, eval_temperature,
                            eval_tool_choice, system_prompt=p_info["text"],
                        )
                    case_results.append(r)

                # Compute scores
                overall_scores = [r["overall_score"] for r in case_results if r.get("success")]
                tool_scores = [r["tool_selection_score"] for r in case_results if r.get("success")]
                param_scores = [r["param_accuracy"] for r in case_results if r.get("success") and r.get("param_accuracy") is not None]

                avg_overall = sum(overall_scores) / len(overall_scores) if overall_scores else 0.0
                model_scores[target.model_id] = {
                    "overall": round(avg_overall, 4),
                    "tool_acc": round(sum(tool_scores) / len(tool_scores) * 100, 2) if tool_scores else 0.0,
                    "param_acc": round(sum(param_scores) / len(param_scores) * 100, 2) if param_scores else 0.0,
                }

                await _ws_send({
                    "type": "prompt_eval_result",
                    "job_id": job_id,
                    "tune_id": tune_id,
                    "generation": gen_num,
                    "prompt_index": p_info["index"],
                    "model_id": target.model_id,
                    "overall_score": model_scores[target.model_id]["overall"],
                    "tool_accuracy": model_scores[target.model_id]["tool_acc"],
                    "param_accuracy": model_scores[target.model_id]["param_acc"],
                })

            p_info["scores"] = model_scores
            all_model_scores = [s["overall"] for s in model_scores.values()]
            p_info["avg_score"] = round(sum(all_model_scores) / len(all_model_scores), 4) if all_model_scores else 0.0

            completed_prompts += 1

            # Track global best
            if p_info["avg_score"] > best_score:
                best_score = p_info["avg_score"]
                best_prompt = p_info["text"]
                best_prompt_origin = {
                    "generation": gen_num,
                    "prompt_index": p_info["index"],
                    "style": p_info.get("style"),
                    "parent_index": p_info.get("parent_index"),
                }

            # Update job progress
            pct = int((completed_prompts / total_prompts) * 100) if total_prompts > 0 else 0
            detail = f"Gen {gen_num}/{generations}, prompt {completed_prompts}/{total_prompts}"
            await progress_cb(pct, detail)

        # --- Selection (Evolutionary mode) ---
        gen_prompts.sort(key=lambda p: p["avg_score"], reverse=True)
        n_survivors = max(1, int(len(gen_prompts) * selection_ratio))
        for i, p in enumerate(gen_prompts):
            p["survived"] = i < n_survivors

        survivors = [p for p in gen_prompts if p["survived"]]

        gen_best = gen_prompts[0] if gen_prompts else None
        all_generations.append({
            "generation": gen_num,
            "timestamp": time.time(),
            "prompts": gen_prompts,
            "best_index": gen_best["index"] if gen_best else None,
            "best_score": gen_best["avg_score"] if gen_best else 0.0,
        })

        await _ws_send({
            "type": "generation_complete",
            "job_id": job_id,
            "tune_id": tune_id,
            "generation": gen_num,
            "best_score": gen_best["avg_score"] if gen_best else 0.0,
            "best_prompt_index": gen_best["index"] if gen_best else None,
            "survivors": [p["index"] for p in survivors],
        })

        # ERD v2: Save generation and candidates to DB
        try:
            gen_id = await db.save_prompt_tune_generation(
                tune_run_id=tune_id,
                generation_number=gen_num,
                best_score=gen_best["avg_score"] if gen_best else 0.0,
                best_candidate_index=gen_best["index"] if gen_best else None,
            )
            for p in gen_prompts:
                await db.save_prompt_tune_candidate(
                    generation_id=gen_id,
                    candidate_index=p["index"],
                    prompt_text=p["text"],
                    style=p.get("style", "variation"),
                    mutation_type=p.get("mutation_type"),
                    avg_score=p["avg_score"],
                    survived=p.get("survived", False),
                )
        except Exception as gen_e:
            logger.warning("Failed to save prompt_tune generation/candidates: %s", gen_e)

        # Incrementally update parent run progress
        await db.update_prompt_tune_run(
            tune_id, user_id,
            best_score=best_score,
            best_prompt_origin_json=json.dumps(best_prompt_origin) if best_prompt_origin else None,
            completed_prompts=completed_prompts,
        )

    # --- Tuning complete ---
    duration = time.perf_counter() - start_time

    _origin_json = json.dumps(best_prompt_origin) if best_prompt_origin else None

    if cancel_event.is_set():
        await db.update_prompt_tune_run(
            tune_id, user_id,
            best_score=best_score,
            best_prompt_origin_json=_origin_json,
            completed_prompts=completed_prompts,
            status="cancelled",
            duration_s=round(duration, 2),
        )
        return None

    # --- Auto-save best prompt to prompt version registry ---
    best_pv_id = None
    if best_prompt:
        try:
            label = f"Prompt Tune ({suite['name']}) -- score {best_score:.0%}"
            best_pv_id = await db.create_prompt_version(
                user_id=user_id,
                prompt_text=best_prompt,
                label=label,
                source="prompt_tuner",
                origin_run_id=tune_id,
            )
            logger.info("Auto-saved best prompt as version %s: tune_id=%s", best_pv_id, tune_id)
        except Exception:
            logger.exception("Failed to auto-save prompt version: tune_id=%s", tune_id)
            await _ws_send({
                "type": "eval_warning",
                "job_id": job_id,
                "detail": "Best prompt auto-save to library failed — you can save manually from results",
            })

    await db.update_prompt_tune_run(
        tune_id, user_id,
        best_score=best_score,
        best_prompt_origin_json=_origin_json,
        best_prompt_version_id=best_pv_id,
        completed_prompts=completed_prompts,
        status="completed",
        duration_s=round(duration, 2),
    )

    # Send completion event to frontend
    await _ws_send({
        "type": "tune_complete",
        "job_id": job_id,
        "tune_id": tune_id,
        "best_prompt": best_prompt,
        "best_score": best_score,
        "duration_s": round(duration, 2),
    })

    logger.info(
        "Prompt tune completed: job_id=%s tune_id=%s user_id=%s prompts=%d best_score=%.4f",
        job_id, tune_id, user_id, completed_prompts, best_score,
    )

    # --- Auto-promote best prompt (if in experiment) ---
    if experiment_id and best_prompt:
        try:
            config_for_promote = {
                "temperature": eval_temperature,
                "tool_choice": eval_tool_choice,
                "system_prompt": best_prompt,
                "promoted_from": f"prompt_tune:{tune_id}",
            }
            await _maybe_update_experiment_best(
                experiment_id, user_id,
                score=best_score,
                config_json=json.dumps(config_for_promote),
                source="prompt_tune",
                source_id=tune_id,
            )
            logger.info(
                "Prompt tune auto-promoted experiment best: tune_id=%s experiment_id=%s",
                tune_id, experiment_id,
            )
        except Exception:
            logger.exception("Auto-promote failed for prompt tune: tune_id=%s", tune_id)

    return tune_id


# ---------------------------------------------------------------------------
# Judge Handler
# ---------------------------------------------------------------------------

async def judge_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for post-eval judge execution.

    Runs judge verdicts with configurable concurrency via asyncio.Semaphore.
    Returns the judge_report_id on success, or None.
    """
    user_id = params["user_id"]
    eval_run_id = params["eval_run_id"]
    judge_model_raw = params["judge_model"]
    judge_provider_key = params.get("judge_provider_key")
    custom_instructions = params.get("custom_instructions", "")
    concurrency = int(params.get("concurrency", 4))
    experiment_id = params.get("experiment_id")

    # Parse compound key (e.g. "zai::GLM-4.5-Air") from settings dropdown
    if "::" in str(judge_model_raw):
        parts = judge_model_raw.split("::", 1)
        judge_model_id = parts[1]
        if not judge_provider_key:
            judge_provider_key = parts[0]
    else:
        judge_model_id = judge_model_raw

    # Tuner analysis params (optional -- for analyzing winning configs)
    tune_run_id = params.get("tune_run_id")
    tune_type = params.get("tune_type")

    # Versioning params (set by rerun endpoint, optional for normal judge)
    parent_report_id = params.get("parent_report_id")
    version = int(params.get("version", 1))

    logger.info(
        "Judge started: job_id=%s user_id=%s eval_run_id=%s concurrency=%d version=%d",
        job_id, user_id, eval_run_id, concurrency, version,
    )

    # ERD v2: Load eval results from case_results table instead of results_json
    eval_run = await db.get_tool_eval_run(eval_run_id, user_id)
    case_results_rows = await db.get_case_results(eval_run_id)

    # Pre-load test cases for the suite and index by ID
    all_test_cases = await db.get_test_cases(eval_run["suite_id"])
    test_case_by_id = {tc["id"]: tc for tc in all_test_cases}

    # Pre-load model lookups to avoid N+1 queries
    model_cache: dict[str, dict | None] = {}

    # Convert case_results rows into the dict format expected by judge logic
    results = []
    case_result_id_map: dict[str, str] = {}  # keyed by "model_id::test_case_id" -> case_result DB id
    for cr in case_results_rows:
        # Resolve model litellm_id from model_id FK
        model_id_fk = cr.get("model_id", "")
        if model_id_fk not in model_cache:
            model_cache[model_id_fk] = await db.get_model(model_id_fk) if model_id_fk else None
        model_row = model_cache[model_id_fk]
        model_litellm_id = model_row["litellm_id"] if model_row else "unknown"

        actual_params = cr.get("actual_params")
        if actual_params and isinstance(actual_params, str):
            try:
                actual_params = json.loads(actual_params)
            except (json.JSONDecodeError, TypeError):
                pass

        result_dict = {
            "test_case_id": cr.get("test_case_id", ""),
            "model_id": model_litellm_id,
            "actual_tool": cr.get("actual_tool"),
            "actual_params": actual_params,
            "tool_selection_score": cr.get("tool_selection_score", 0.0),
            "param_accuracy": cr.get("param_accuracy"),
            "overall_score": cr.get("overall_score", 0.0),
            "success": bool(cr.get("success", 1)),
            "error": cr.get("error", ""),
            "latency_ms": cr.get("latency_ms", 0),
            "format_compliance": cr.get("format_compliance", "PASS"),
            "error_type": cr.get("error_type"),
        }

        # Enrich with test case data for judge context
        tc = test_case_by_id.get(cr.get("test_case_id", ""))
        if tc:
            result_dict["prompt"] = tc.get("prompt", "")
            result_dict["expected_tool"] = tc.get("expected_tool")
            if tc.get("expected_params"):
                try:
                    result_dict["expected_params"] = json.loads(tc["expected_params"]) if isinstance(tc["expected_params"], str) else tc["expected_params"]
                except (json.JSONDecodeError, TypeError):
                    result_dict["expected_params"] = tc.get("expected_params")

        results.append(result_dict)
        cr_key = f"{model_litellm_id}::{cr.get('test_case_id', '')}"
        case_result_id_map[cr_key] = cr["id"]

    # --- Build tuner analysis context (appended to cross-case prompt) ---
    tuner_analysis_context = ""
    if tune_run_id and tune_type:
        try:
            if tune_type == "param_tuner":
                tune_run = await db.get_param_tune_run(tune_run_id, user_id)
                if tune_run:
                    best_config = json.loads(tune_run.get("best_config_json", "{}") or "{}")
                    tuner_analysis_context = (
                        "## Tuner Analysis Context\n"
                        "The parameter tuner found this winning configuration:\n"
                        f"{json.dumps(best_config, indent=2)}\n\n"
                        "Analyze WHY these parameters performed best. Consider:\n"
                        "- How the temperature setting affects tool calling precision\n"
                        "- Whether the parameter combination reduces hallucination\n"
                        "- If any params have diminishing returns\n"
                    )
                    logger.info("Tuner analysis context loaded: param_tune run_id=%s", tune_run_id)
            elif tune_type == "prompt_tuner":
                tune_run = await db.get_prompt_tune_run(tune_run_id, user_id)
                if tune_run:
                    # Resolve best_prompt from prompt_versions or fallback to column
                    best_prompt = ""
                    if tune_run.get("best_prompt_version_id"):
                        _bpv = await db.get_prompt_version(tune_run["best_prompt_version_id"], user_id)
                        if _bpv:
                            best_prompt = _bpv.get("prompt_text", "")
                    if not best_prompt:
                        best_prompt = tune_run.get("base_prompt", "")
                    tuner_analysis_context = (
                        "## Tuner Analysis Context\n"
                        "The prompt tuner found this winning system prompt:\n"
                        "---\n"
                        f"{best_prompt}\n"
                        "---\n\n"
                        "Analyze WHY this prompt performed best. Consider:\n"
                        "- What specific instructions improve tool selection accuracy\n"
                        "- How the prompt structure guides parameter extraction\n"
                        "- What linguistic patterns the model responds to most effectively\n"
                    )
                    logger.info("Tuner analysis context loaded: prompt_tune run_id=%s", tune_run_id)
        except Exception:
            logger.warning("Failed to load tuner analysis context: tune_type=%s tune_run_id=%s", tune_type, tune_run_id)

    # Load suite for tool definitions
    suite = await db.get_tool_suite(eval_run["suite_id"], user_id)
    tool_defs = await db.get_tool_definitions(eval_run["suite_id"])
    tools = _tool_defs_to_openai(tool_defs)
    tool_defs_text = _build_tool_definitions_text(tools)

    # Helper to send WebSocket messages (defined early so error paths can use it)
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Build judge target
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    judge_targets = _find_target(all_targets, judge_model_id, judge_provider_key)
    if not judge_targets:
        logger.warning(
            "Judge target not found: model_id=%s provider_key=%s job_id=%s",
            judge_model_id, judge_provider_key, job_id,
        )
        await _ws_send({
            "type": "job_failed",
            "job_id": job_id,
            "error": f"Judge model '{judge_model_id}' not found in your config. Check Settings > Judge.",
        })
        return None
    judge_target = judge_targets[0]

    # Inject user API key
    if judge_target.provider_key:
        encrypted = await db.get_user_key_for_provider(user_id, judge_target.provider_key)
        if encrypted:
            judge_target = inject_user_keys([judge_target], {judge_target.provider_key: encrypted})[0]
    logger.debug(
        "Standalone judge target ready: model=%s api_base=%s has_key=%s",
        judge_target.model_id, judge_target.api_base,
        bool(judge_target.api_key),
    )

    # Resolve DB model ID for judge FK
    _judge_db = await db.get_model_by_litellm_id(user_id, judge_model_id)
    _judge_db_id = _judge_db["id"] if _judge_db else None

    # Create judge report in DB
    report_id = await db.save_judge_report(
        user_id=user_id,
        mode="post_eval",
        eval_run_id=eval_run_id,
        experiment_id=experiment_id,
        parent_report_id=parent_report_id,
        version=version,
        custom_instructions=custom_instructions or None,
        judge_model_id=_judge_db_id,
    )

    # Store result_ref early
    await db.set_job_result_ref(job_id, report_id)

    await _ws_send({
        "type": "judge_start",
        "job_id": job_id,
        "mode": "post_eval",
        "judge_model": judge_target.display_name,
        "cases_to_review": len(results),
        "judge_report_id": report_id,
    })

    # Group results by model for per-model reports
    model_results: dict[str, list[dict]] = {}
    for r in results:
        mid = r.get("model_id", "unknown")
        model_results.setdefault(mid, []).append(r)

    target_map = {t.model_id: t for t in all_targets}
    all_verdicts = []
    all_model_reports = []
    completed = 0
    total_verdicts = len(results)
    sem = asyncio.Semaphore(concurrency)

    for model_id, model_res in model_results.items():
        if cancel_event.is_set():
            break

        model_verdicts = []

        async def _judge_one(r, _mid=model_id):
            async with sem:
                v = await _judge_single_verdict(
                    judge_target, tool_defs_text, {}, r,
                    custom_instructions=custom_instructions,
                )
                v["test_case_id"] = r.get("test_case_id", "?")
                v["model_id"] = _mid
                return v

        judge_batch = [asyncio.create_task(_judge_one(r)) for r in model_res]
        for coro in asyncio.as_completed(judge_batch):
            if cancel_event.is_set():
                for bt in judge_batch:
                    bt.cancel()
                break
            v = await coro
            all_verdicts.append(v)
            model_verdicts.append(v)
            completed += 1
            await _ws_send({"type": "judge_verdict", "job_id": job_id, **v})
            # Progress tracking
            j_pct = int((completed / total_verdicts) * 100) if total_verdicts > 0 else 0
            tgt = target_map.get(model_id)
            mname = tgt.display_name if tgt else model_id
            await progress_cb(j_pct, f"Judge {mname}: {completed}/{total_verdicts}")

            # ERD v2: Save verdict to DB
            try:
                cr_key = f"{v.get('model_id', '')}::{v.get('test_case_id', '')}"
                cr_id = case_result_id_map.get(cr_key)
                if cr_id:
                    await db.save_judge_verdict(
                        report_id=report_id,
                        case_result_id=cr_id,
                        quality_score=v.get("quality_score", 0),
                        verdict=v.get("verdict", "fail"),
                        summary=v.get("summary", ""),
                        reasoning=v.get("reasoning", ""),
                        tool_selection_assessment=v.get("tool_selection_assessment", "unknown"),
                        param_assessment=v.get("param_assessment", "unknown"),
                        judge_override_score=v.get("judge_override_score"),
                        override_reason=v.get("override_reason"),
                    )
            except Exception as ve:
                logger.warning("Failed to save judge verdict: %s", ve)

        if cancel_event.is_set():
            if report_id:
                await db.update_judge_report(report_id, status="error")
            return None

        # Cross-case analysis per model
        tgt = target_map.get(model_id)
        mname = tgt.display_name if tgt else model_id
        report_data = await _judge_crosscase(judge_target, mname, model_verdicts, extra_context=tuner_analysis_context)
        report_data["model_id"] = model_id
        report_data["model_name"] = mname
        all_model_reports.append(report_data)
        await _ws_send({"type": "judge_report", "job_id": job_id, "eval_id": eval_run_id, "report": report_data})

    # Save completed report
    if all_model_reports:
        best_report = max(all_model_reports, key=lambda r: r.get("overall_score", 0))
        final_grade = best_report.get("overall_grade", "?")
        final_score = best_report.get("overall_score", 0)
    else:
        final_grade = "?"
        final_score = 0

    await db.update_judge_report(
        report_id,
        report_json=json.dumps(all_model_reports),
        overall_grade=final_grade,
        overall_score=final_score,
        status="completed",
    )

    await _ws_send({"type": "judge_complete", "job_id": job_id, "judge_report_id": report_id})

    logger.info(
        "Judge completed: job_id=%s user_id=%s report_id=%s verdicts=%d",
        job_id, user_id, report_id, len(all_verdicts),
    )

    return report_id


# ---------------------------------------------------------------------------
# Judge Compare Handler
# ---------------------------------------------------------------------------

async def judge_compare_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for comparative judge execution.

    Compares two eval runs with configurable concurrency.
    Returns the judge_report_id on success, or None.
    """
    # Import here to access judge prompt templates and core function
    from routers.judge import _JUDGE_COMPARE_PROMPT, _JUDGE_COMPARE_SUMMARY_PROMPT, _call_judge_model

    user_id = params["user_id"]
    eval_run_id_a = params["eval_run_id_a"]
    eval_run_id_b = params["eval_run_id_b"]
    judge_model_raw = params["judge_model"]
    judge_provider_key = params.get("judge_provider_key")
    concurrency = int(params.get("concurrency", 4))
    experiment_id = params.get("experiment_id")

    # Parse compound key (e.g. "zai::GLM-4.5-Air") from settings dropdown
    if "::" in str(judge_model_raw):
        parts = judge_model_raw.split("::", 1)
        judge_model_id = parts[1]
        if not judge_provider_key:
            judge_provider_key = parts[0]
    else:
        judge_model_id = judge_model_raw

    logger.info(
        "Judge compare started: job_id=%s user_id=%s run_a=%s run_b=%s",
        job_id, user_id, eval_run_id_a, eval_run_id_b,
    )

    # ERD v2: Load results from case_results table
    run_a = await db.get_tool_eval_run(eval_run_id_a, user_id)
    run_b = await db.get_tool_eval_run(eval_run_id_b, user_id)

    case_results_a = await db.get_case_results(eval_run_id_a)
    case_results_b = await db.get_case_results(eval_run_id_b)

    # Pre-load test cases and model lookups for batch enrichment
    all_test_cases = await db.get_test_cases(run_a["suite_id"])
    test_case_by_id = {tc["id"]: tc for tc in all_test_cases}
    model_cache: dict[str, dict | None] = {}

    # Convert case_results to dicts with litellm model IDs
    async def _enrich_results(case_results_rows: list[dict]) -> list[dict]:
        enriched = []
        for cr in case_results_rows:
            model_id_fk = cr.get("model_id", "")
            if model_id_fk not in model_cache:
                model_cache[model_id_fk] = await db.get_model(model_id_fk) if model_id_fk else None
            model_row = model_cache[model_id_fk]
            model_litellm_id = model_row["litellm_id"] if model_row else "unknown"

            actual_params = cr.get("actual_params")
            if actual_params and isinstance(actual_params, str):
                try:
                    actual_params = json.loads(actual_params)
                except (json.JSONDecodeError, TypeError):
                    pass

            result_dict = {
                "test_case_id": cr.get("test_case_id", ""),
                "model_id": model_litellm_id,
                "actual_tool": cr.get("actual_tool"),
                "actual_params": actual_params,
                "overall_score": cr.get("overall_score", 0.0),
                "success": bool(cr.get("success", 1)),
            }

            # Enrich with test case data for judge context
            tc = test_case_by_id.get(cr.get("test_case_id", ""))
            if tc:
                result_dict["prompt"] = tc.get("prompt", "")
                result_dict["expected_tool"] = tc.get("expected_tool")
                if tc.get("expected_params"):
                    try:
                        result_dict["expected_params"] = json.loads(tc["expected_params"]) if isinstance(tc["expected_params"], str) else tc["expected_params"]
                    except (json.JSONDecodeError, TypeError):
                        result_dict["expected_params"] = tc.get("expected_params")

            enriched.append(result_dict)
        return enriched

    results_a = await _enrich_results(case_results_a)
    results_b = await _enrich_results(case_results_b)

    # Load suite for tool definitions
    suite = await db.get_tool_suite(run_a["suite_id"], user_id)
    tool_defs = await db.get_tool_definitions(run_a["suite_id"])
    tools = _tool_defs_to_openai(tool_defs)
    tool_defs_text = _build_tool_definitions_text(tools)

    # Helper to send WebSocket messages (defined early so error paths can use it)
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Build judge target
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    judge_targets = _find_target(all_targets, judge_model_id, judge_provider_key)
    if not judge_targets:
        logger.warning(
            "Judge compare target not found: model_id=%s provider_key=%s job_id=%s",
            judge_model_id, judge_provider_key, job_id,
        )
        await _ws_send({
            "type": "job_failed",
            "job_id": job_id,
            "error": f"Judge model '{judge_model_id}' not found in your config. Check Settings > Judge.",
        })
        return None
    judge_target = judge_targets[0]

    if judge_target.provider_key:
        encrypted = await db.get_user_key_for_provider(user_id, judge_target.provider_key)
        if encrypted:
            judge_target = inject_user_keys([judge_target], {judge_target.provider_key: encrypted})[0]

    # Determine model names from enriched case results
    model_ids_a = list(dict.fromkeys(r["model_id"] for r in results_a))  # preserve order, unique
    model_ids_b = list(dict.fromkeys(r["model_id"] for r in results_b))
    model_a_name = model_ids_a[0] if model_ids_a else "Model A"
    model_b_name = model_ids_b[0] if model_ids_b else "Model B"
    target_map = {t.model_id: t for t in all_targets}
    ta = target_map.get(model_a_name)
    tb = target_map.get(model_b_name)
    if ta:
        model_a_name = ta.display_name
    if tb:
        model_b_name = tb.display_name

    # Index results by test_case_id
    a_by_tc = {r["test_case_id"]: r for r in results_a if "test_case_id" in r}
    b_by_tc = {r["test_case_id"]: r for r in results_b if "test_case_id" in r}
    common_tcs = sorted(set(a_by_tc.keys()) & set(b_by_tc.keys()))

    if not common_tcs:
        return None

    # Resolve DB model ID for judge FK
    _judge_db_cmp = await db.get_model_by_litellm_id(user_id, judge_model_id)
    _judge_db_id_cmp = _judge_db_cmp["id"] if _judge_db_cmp else None

    # Create report in DB
    report_id = await db.save_judge_report(
        user_id=user_id,
        mode="comparative",
        eval_run_id=eval_run_id_a,
        eval_run_id_b=eval_run_id_b,
        experiment_id=experiment_id,
        judge_model_id=_judge_db_id_cmp,
    )

    # Store result_ref early
    await db.set_job_result_ref(job_id, report_id)

    await _ws_send({
        "type": "compare_start",
        "job_id": job_id,
        "model_a": model_a_name,
        "model_b": model_b_name,
        "cases": len(common_tcs),
        "judge_report_id": report_id,
    })

    case_comparisons = []
    completed = 0
    total_cases = len(common_tcs)
    sem = asyncio.Semaphore(concurrency)

    async def _compare_one(idx, tc_id):
        async with sem:
            ra = a_by_tc[tc_id]
            rb = b_by_tc[tc_id]
            prompt = _JUDGE_COMPARE_PROMPT.format(
                tool_definitions=tool_defs_text,
                case_num=idx + 1,
                total_cases=total_cases,
                test_prompt=ra.get("prompt", ""),
                expected_tool=ra.get("expected_tool", "?"),
                expected_params=json.dumps(ra.get("expected_params", {})),
                model_a_name=model_a_name,
                a_tool=ra.get("actual_tool", "none"),
                a_params=json.dumps(ra.get("actual_params", {})),
                a_score=ra.get("overall_score", 0),
                model_b_name=model_b_name,
                b_tool=rb.get("actual_tool", "none"),
                b_params=json.dumps(rb.get("actual_params", {})),
                b_score=rb.get("overall_score", 0),
            )
            comparison = await _call_judge_model(judge_target, prompt)
            if not comparison:
                comparison = {"winner": "tie", "confidence": 0, "reasoning": "Judge error"}
            comparison.setdefault("winner", "tie")
            comparison.setdefault("confidence", 0)
            comparison.setdefault("reasoning", "")
            comparison["test_case_id"] = tc_id
            return comparison

    compare_tasks = [asyncio.create_task(_compare_one(idx, tc_id)) for idx, tc_id in enumerate(common_tcs)]
    for coro in asyncio.as_completed(compare_tasks):
        if cancel_event.is_set():
            for ct in compare_tasks:
                ct.cancel()
            if report_id:
                await db.update_judge_report(report_id, status="error")
            return None
        comparison = await coro
        case_comparisons.append(comparison)
        completed += 1
        await _ws_send({"type": "compare_case", "job_id": job_id, **comparison})
        c_pct = int((completed / total_cases) * 100) if total_cases > 0 else 0
        await progress_cb(c_pct, f"Compare: {completed}/{total_cases}")

    # Generate overall summary
    case_results_text = []
    for c in case_comparisons:
        case_results_text.append(
            f"- Case {c['test_case_id']}: winner={c['winner']}, "
            f"confidence={c.get('confidence', 0)}, reason: {c.get('reasoning', '')[:100]}"
        )

    summary_prompt = _JUDGE_COMPARE_SUMMARY_PROMPT.format(
        model_a_name=model_a_name,
        model_b_name=model_b_name,
        n=len(case_comparisons),
        case_results="\n".join(case_results_text),
    )
    summary = await _call_judge_model(judge_target, summary_prompt)
    if not summary:
        a_wins = sum(1 for c in case_comparisons if c.get("winner") == "model_a")
        b_wins = sum(1 for c in case_comparisons if c.get("winner") == "model_b")
        ties = len(case_comparisons) - a_wins - b_wins
        winner = "model_a" if a_wins > b_wins else ("model_b" if b_wins > a_wins else "tie")
        summary = {
            "overall_winner": winner,
            "score_a": round(a_wins / len(case_comparisons) * 100) if case_comparisons else 0,
            "score_b": round(b_wins / len(case_comparisons) * 100) if case_comparisons else 0,
            "summary": f"{model_a_name} won {a_wins}, {model_b_name} won {b_wins}, {ties} ties.",
            "tie_cases": ties,
        }

    summary.setdefault("overall_winner", "tie")
    summary.setdefault("score_a", 0)
    summary.setdefault("score_b", 0)
    summary.setdefault("summary", "")
    summary.setdefault("tie_cases", 0)
    summary["judge_report_id"] = report_id

    # Save report
    overall_grade = summary.get("overall_winner", "tie")
    overall_score = max(summary.get("score_a", 0), summary.get("score_b", 0))

    full_report = {
        "model_a": model_a_name,
        "model_b": model_b_name,
        "case_comparisons": case_comparisons,
        **summary,
    }

    await db.update_judge_report(
        report_id,
        report_json=json.dumps(full_report),
        overall_grade=overall_grade,
        overall_score=overall_score,
        status="completed",
    )

    await _ws_send({"type": "compare_complete", "job_id": job_id, **summary})

    logger.info(
        "Judge compare completed: job_id=%s user_id=%s report_id=%s cases=%d",
        job_id, user_id, report_id, len(case_comparisons),
    )

    return report_id


# ---------------------------------------------------------------------------
# I2: Prompt Auto-Optimize Handler (OPRO/APE)
# ---------------------------------------------------------------------------

async def prompt_auto_optimize_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """I2: OPRO/APE-style prompt auto-optimizer.

    Iteratively generates prompt variants using a meta-model, evaluates them
    against a test suite, and feeds top performers back for the next round.

    Returns the best prompt version_id on success, or None on cancel.
    """
    from routers.prompt_tune import (
        _generate_prompts_meta,
        _QUICK_META_PROMPT,
        _EVO_META_PROMPT,
        _DEFAULT_BASE_PROMPT,
    )

    user_id = params["user_id"]
    suite_id = params["suite_id"]
    target_model_ids = params["target_models"]
    _raw_ts = params.get("target_set")
    target_set_eval = {tuple(t) for t in _raw_ts} if _raw_ts else None
    meta_model_id = params["meta_model"]
    meta_provider_key = params.get("meta_provider_key")
    base_prompt = params.get("base_prompt") or _DEFAULT_BASE_PROMPT
    experiment_id = params.get("experiment_id")

    # Auto-optimize config
    max_iterations = int(params.get("max_iterations", 3))
    population_size = int(params.get("population_size", 5))
    selection_ratio = float(params.get("selection_ratio", 0.4))
    eval_temperature = float(params.get("eval_temperature", 0.0))
    eval_tool_choice = params.get("eval_tool_choice", "required")

    # Clamp to safe ranges
    max_iterations = max(1, min(max_iterations, 10))
    population_size = max(3, min(population_size, 20))
    selection_ratio = max(0.2, min(selection_ratio, 0.8))

    logger.info(
        "Prompt auto-optimize started: job_id=%s user_id=%s suite=%s iterations=%d pop=%d",
        job_id, user_id, suite_id, max_iterations, population_size,
    )

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    if not suite:
        logger.error("Auto-optimize: suite not found suite_id=%s", suite_id)
        if ws_manager:
            await ws_manager.send_to_user(user_id, {
                "type": "job_failed",
                "job_id": job_id,
                "error": "Suite not found — it may have been deleted",
            })
        return None
    cases = await db.get_test_cases(suite_id)
    if not cases:
        logger.error("Auto-optimize: no test cases in suite suite_id=%s", suite_id)
        if ws_manager:
            await ws_manager.send_to_user(user_id, {
                "type": "job_failed",
                "job_id": job_id,
                "error": "Suite has no test cases to evaluate",
            })
        return None

    # ERD v2: Load tools from tool_definitions table
    tool_defs = await db.get_tool_definitions(suite_id)
    tools = _tool_defs_to_openai(tool_defs)

    # Build targets
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)

    meta_targets = _find_target(all_targets, meta_model_id, meta_provider_key)
    eval_targets = _filter_targets(all_targets, target_model_ids, target_set_eval)

    if not meta_targets:
        logger.error("Auto-optimize: meta model not found meta_model_id=%s", meta_model_id)
        if ws_manager:
            await ws_manager.send_to_user(user_id, {
                "type": "job_failed",
                "job_id": job_id,
                "error": "Optimization model not found in your configuration",
            })
        return None
    if not eval_targets:
        logger.error("Auto-optimize: no eval targets found")
        if ws_manager:
            await ws_manager.send_to_user(user_id, {
                "type": "job_failed",
                "job_id": job_id,
                "error": "No evaluation models matched the selected targets",
            })
        return None

    # Inject user API keys
    user_keys_cache = {}
    for t in meta_targets + eval_targets:
        if t.provider_key and t.provider_key not in user_keys_cache:
            encrypted = await db.get_user_key_for_provider(user_id, t.provider_key)
            if encrypted:
                user_keys_cache[t.provider_key] = encrypted
    meta_targets = inject_user_keys(meta_targets, user_keys_cache)
    eval_targets = inject_user_keys(eval_targets, user_keys_cache)
    meta_target = meta_targets[0]

    tools_summary = _build_tools_summary(tools)
    test_cases_summary = _build_test_cases_summary(cases)

    # Helper to send WebSocket messages
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    total_prompts_to_eval = population_size * max_iterations

    await _ws_send({
        "type": "auto_optimize_start",
        "job_id": job_id,
        "suite_name": suite["name"],
        "max_iterations": max_iterations,
        "population_size": population_size,
        "total_prompts": total_prompts_to_eval,
        "eval_models": [t.display_name for t in eval_targets],
    })

    start_time = time.perf_counter()
    all_iterations = []
    best_prompt = base_prompt
    best_score = 0.0
    best_version_id = None
    survivors = []
    completed_prompts = 0

    async def _eval_prompt_on_suite(prompt_text: str) -> float:
        """Evaluate a single prompt against all test cases and eval targets.

        Returns average overall score across all models and cases.
        """
        all_scores = []
        for target in eval_targets:
            if cancel_event.is_set():
                return 0.0
            case_scores = []
            for case in cases:
                if cancel_event.is_set():
                    return 0.0
                mt_config = None
                if case.get("multi_turn_config"):
                    try:
                        mt_config = (
                            json.loads(case["multi_turn_config"])
                            if isinstance(case["multi_turn_config"], str)
                            else case["multi_turn_config"]
                        )
                    except (json.JSONDecodeError, TypeError):
                        mt_config = None

                if mt_config and mt_config.get("multi_turn"):
                    case_with_mt = {**case, "_mt_config": mt_config}
                    r = await run_multi_turn_eval(
                        target, tools, case_with_mt, eval_temperature,
                        eval_tool_choice, system_prompt=prompt_text,
                    )
                else:
                    r = await run_single_eval(
                        target, tools, case, eval_temperature,
                        eval_tool_choice, system_prompt=prompt_text,
                    )
                case_scores.append(r.get("overall_score", 0.0))
            if case_scores:
                all_scores.append(sum(case_scores) / len(case_scores))
        return round(sum(all_scores) / len(all_scores), 4) if all_scores else 0.0

    for iteration in range(1, max_iterations + 1):
        if cancel_event.is_set():
            break

        await _ws_send({
            "type": "auto_optimize_iteration_start",
            "job_id": job_id,
            "iteration": iteration,
            "of": max_iterations,
            "current_best_score": best_score,
            "current_best_prompt": best_prompt[:300],
        })

        # Generate prompt variants
        if iteration == 1 or not survivors:
            raw_prompts = await _generate_prompts_meta(
                meta_target, _QUICK_META_PROMPT,
                n=population_size,
                tools_summary=tools_summary,
                test_cases_summary=test_cases_summary,
                base_prompt=base_prompt,
            )
        else:
            parent_text = "\n".join(
                f"#{i+1} (score={s['avg_score']:.2f}): \"{s['text'][:200]}...\""
                for i, s in enumerate(survivors)
            )
            raw_prompts = await _generate_prompts_meta(
                meta_target, _EVO_META_PROMPT,
                n=population_size,
                tools_summary=tools_summary,
                test_cases_summary=test_cases_summary,
                parent_prompts=parent_text,
            )

        if not raw_prompts:
            logger.warning("Auto-optimize: meta returned 0 prompts for iteration %d", iteration)
            await _ws_send({
                "type": "auto_optimize_error",
                "job_id": job_id,
                "iteration": iteration,
                "message": "Meta model returned no prompts. Skipping iteration.",
            })
            continue

        # Evaluate each variant
        iter_prompts = []
        for idx, rp in enumerate(raw_prompts[:population_size]):
            if cancel_event.is_set():
                break
            text = rp.get("prompt", "") if isinstance(rp, dict) else str(rp)
            style = rp.get("style", rp.get("mutation_type", "variation")) if isinstance(rp, dict) else "variation"

            await _ws_send({
                "type": "auto_optimize_eval_start",
                "job_id": job_id,
                "iteration": iteration,
                "prompt_index": idx,
                "style": style,
                "prompt_preview": text[:200],
            })

            score = await _eval_prompt_on_suite(text)
            completed_prompts += 1

            iter_prompts.append({
                "index": idx,
                "text": text,
                "style": style,
                "avg_score": score,
                "iteration": iteration,
            })

            # Track global best
            if score > best_score:
                best_score = score
                best_prompt = text

            pct = int((completed_prompts / total_prompts_to_eval) * 100) if total_prompts_to_eval > 0 else 0
            await progress_cb(pct, f"Iteration {iteration}/{max_iterations}, prompt {completed_prompts}/{total_prompts_to_eval}")

            await _ws_send({
                "type": "auto_optimize_progress",
                "job_id": job_id,
                "iteration": iteration,
                "of": max_iterations,
                "prompt_index": idx,
                "style": style,
                "score": score,
                "current_best_score": best_score,
                "completed_prompts": completed_prompts,
                "total_prompts": total_prompts_to_eval,
            })

        if cancel_event.is_set():
            break

        # Select survivors for next iteration
        iter_prompts.sort(key=lambda p: p["avg_score"], reverse=True)
        n_survivors = max(1, int(len(iter_prompts) * selection_ratio))
        survivors = iter_prompts[:n_survivors]

        iter_best = iter_prompts[0] if iter_prompts else None
        all_iterations.append({
            "iteration": iteration,
            "timestamp": time.time(),
            "prompts": iter_prompts,
            "best_score": iter_best["avg_score"] if iter_best else 0.0,
            "best_index": iter_best["index"] if iter_best else None,
            "survivors": [p["index"] for p in survivors],
        })

        await _ws_send({
            "type": "auto_optimize_iteration_complete",
            "job_id": job_id,
            "iteration": iteration,
            "of": max_iterations,
            "best_score": iter_best["avg_score"] if iter_best else 0.0,
            "global_best_score": best_score,
            "survivors": [p["index"] for p in survivors],
        })

    # --- Auto-optimize complete ---
    duration = time.perf_counter() - start_time

    # Save best prompt to prompt_versions table
    if best_prompt and best_prompt != base_prompt:
        try:
            label = f"Auto-Optimize ({suite['name']}) -- score {best_score:.0%}"
            best_version_id = await db.create_prompt_version(
                user_id=user_id,
                prompt_text=best_prompt,
                label=label,
                source="auto_optimize",
                origin_run_id=job_id,
            )
            logger.info(
                "Auto-optimize saved best prompt: job_id=%s version_id=%s score=%.4f",
                job_id, best_version_id, best_score,
            )
        except Exception:
            logger.exception("Failed to save auto-optimize prompt version: job_id=%s", job_id)

    # Save all generated prompts as versions (top N by score)
    saved_versions = []
    if all_iterations:
        all_candidate_prompts = []
        for iter_data in all_iterations:
            all_candidate_prompts.extend(iter_data.get("prompts", []))
        all_candidate_prompts.sort(key=lambda p: p["avg_score"], reverse=True)

        # Save top 5 unique prompts (excluding already-saved best)
        seen_texts = {best_prompt} if best_prompt else set()
        for candidate in all_candidate_prompts[:10]:
            if len(saved_versions) >= 5:
                break
            text = candidate.get("text", "")
            if text and text not in seen_texts:
                seen_texts.add(text)
                try:
                    vid = await db.create_prompt_version(
                        user_id=user_id,
                        prompt_text=text,
                        label=f"Auto-Optimize #{candidate['iteration']}.{candidate['index']} ({suite['name']}) -- score {candidate['avg_score']:.0%}",
                        source="auto_optimize",
                        origin_run_id=job_id,
                        parent_version_id=best_version_id,
                    )
                    saved_versions.append({"version_id": vid, "score": candidate["avg_score"], "text_preview": text[:200]})
                except Exception:
                    logger.exception("Failed to save candidate prompt version: job_id=%s", job_id)

    # Auto-promote to experiment if provided
    if experiment_id and best_prompt and best_prompt != base_prompt:
        try:
            config_for_promote = {
                "system_prompt": best_prompt,
                "promoted_from": f"auto_optimize:{job_id}",
            }
            await _maybe_update_experiment_best(
                experiment_id, user_id,
                score=best_score,
                config_json=json.dumps(config_for_promote),
                source="auto_optimize",
                source_id=job_id,
            )
            logger.info(
                "Auto-optimize auto-promoted experiment best: job_id=%s experiment_id=%s",
                job_id, experiment_id,
            )
        except Exception:
            logger.exception("Auto-promote failed for auto-optimize: job_id=%s", job_id)

    await _ws_send({
        "type": "auto_optimize_complete",
        "job_id": job_id,
        "best_prompt": best_prompt,
        "best_score": best_score,
        "best_version_id": best_version_id,
        "total_iterations": len(all_iterations),
        "total_prompts_evaluated": completed_prompts,
        "duration_s": round(duration, 2),
        "ranked_versions": [
            {"version_id": best_version_id, "score": best_score, "text_preview": best_prompt[:200]}
        ] + saved_versions if best_version_id else saved_versions,
    })

    logger.info(
        "Prompt auto-optimize completed: job_id=%s user_id=%s best_score=%.4f duration=%.1fs",
        job_id, user_id, best_score, duration,
    )

    return best_version_id or job_id


# ---------------------------------------------------------------------------
# Register all handlers with the job registry
# ---------------------------------------------------------------------------

def register_all_handlers():
    """Register all job handlers with the job registry singleton.

    Called from app.py during startup to wire up handlers.
    """
    job_registry.register_handler("benchmark", benchmark_handler)
    job_registry.register_handler("tool_eval", tool_eval_handler)
    job_registry.register_handler("param_tune", param_tune_handler)
    job_registry.register_handler("prompt_tune", prompt_tune_handler)
    job_registry.register_handler("prompt_auto_optimize", prompt_auto_optimize_handler)
    job_registry.register_handler("judge", judge_handler)
    job_registry.register_handler("judge_compare", judge_compare_handler)

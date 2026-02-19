"""Job handler functions for the job registry.

Extracted from router modules to separate business logic (the actual job
execution) from HTTP handling (request parsing, validation, response formatting).

Each handler follows the job_registry handler signature:
    async def handler(job_id, params, cancel_event, progress_cb) -> str | None

The module-level `ws_manager` is injected by app.py at startup, same pattern
used by the router modules.
"""

import asyncio
import json
import logging
import time

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
    provider_params = params.get("provider_params")

    logger.info(
        "Benchmark started: job_id=%s user_id=%s models=%d tiers=%s runs=%d",
        job_id, user_id, len(model_ids) if model_ids else 0, context_tiers, runs,
    )

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
        return None

    # Group targets by provider for parallel execution
    provider_groups = {}
    for target in targets:
        provider_groups.setdefault(target.provider, []).append(target)

    results_queue = asyncio.Queue()

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

    async def run_provider(prov_targets):
        """Run all benchmarks for one provider sequentially."""
        for tier in context_tiers:
            for target in prov_targets:
                if cancel_event.is_set():
                    return
                headroom = target.context_window - max_tokens - 100
                if tier > 0 and tier > headroom:
                    continue  # Skip tier exceeding context window

                # Warm-up run (discarded)
                if warmup:
                    await async_run_single(
                        target, prompt, max_tokens, temperature, tier,
                        provider_params=provider_params,
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
                        target, prompt, max_tokens, temperature, tier,
                        provider_params=provider_params,
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

    # Save results
    if all_results:
        agg_results = _aggregate(all_results, config)
        save_results(agg_results, prompt, context_tiers=context_tiers)

        run_id = await db.save_benchmark_run(
            user_id=user_id,
            prompt=prompt,
            context_tiers=json.dumps(context_tiers),
            results_json=json.dumps(all_results),
        )

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

    logger.info(
        "Tool eval started: job_id=%s user_id=%s models=%d",
        job_id, user_id, len(model_ids) if model_ids else 0,
    )

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    cases = await db.get_test_cases(suite_id)
    tools = json.loads(suite["tools_json"])

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
        return None

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
            for case in cases:
                if cancel_event.is_set():
                    return
                mt_config = None
                if case.get("multi_turn_config"):
                    try:
                        mt_config = json.loads(case["multi_turn_config"]) if isinstance(case["multi_turn_config"], str) else case["multi_turn_config"]
                    except (json.JSONDecodeError, TypeError):
                        logger.debug("Failed to parse multi_turn_config in tool eval handler")
                        mt_config = None

                if mt_config and mt_config.get("multi_turn"):
                    case_with_mt = {**case, "_mt_config": mt_config}
                    result = await run_multi_turn_eval(target, tools, case_with_mt, temperature, tool_choice, provider_params=provider_params, system_prompt=system_prompt)
                else:
                    result = await run_single_eval(target, tools, case, temperature, tool_choice, provider_params=provider_params, system_prompt=system_prompt)
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

    # Compute per-model summaries
    summaries = _compute_eval_summaries(all_results, targets)
    for s in summaries:
        await _ws_send({"type": "tool_eval_summary", "job_id": job_id, "data": s})

    # Build config_json for reproducibility (M1)
    config = {
        "temperature": temperature,
        "tool_choice": tool_choice,
    }
    if provider_params:
        config["provider_params"] = provider_params
    if system_prompt_raw:
        config["system_prompt"] = system_prompt_raw
    # Build target_set from the targets list
    target_set_list = []
    for t in targets:
        target_set_list.append([t.provider_key or "", t.model_id])
    target_set_cleaned = [ts for ts in target_set_list if ts[0] or ts[1]]
    if target_set_cleaned:
        config["target_set"] = target_set_cleaned
    config_json_str = json.dumps(config)

    # Save to DB
    eval_id = await db.save_tool_eval_run(
        user_id=user_id,
        suite_id=suite["id"],
        suite_name=suite["name"],
        models_json=json.dumps(model_ids),
        results_json=json.dumps(all_results),
        summary_json=json.dumps(summaries),
        temperature=temperature,
        config_json=config_json_str,
        experiment_id=experiment_id,
    )

    # Store result_ref so frontend can discover eval_id on reconnect
    await db.set_job_result_ref(job_id, eval_id)

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
            judge_report_id = await db.save_judge_report(
                user_id=user_id,
                judge_model=judge_target.model_id,
                mode="post_eval",
                eval_run_id=eval_id,
                experiment_id=experiment_id,
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
                verdicts_json=json.dumps(pe_verdicts),
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

    # --- Live inline judge: save report ---
    elif judge_enabled and judge_mode == "live_inline" and judge_verdicts:
        try:
            judge_report_id = await db.save_judge_report(
                user_id=user_id,
                judge_model=judge_target.model_id,
                mode="live_inline",
                eval_run_id=eval_id,
                experiment_id=experiment_id,
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
                verdicts_json=json.dumps(judge_verdicts),
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
            logger.debug("Failed to compute delta for experiment %s", experiment_id)
    await _ws_send(complete_evt)

    logger.info(
        "Tool eval completed: job_id=%s user_id=%s results=%d eval_id=%s",
        job_id, user_id, len(all_results), eval_id,
    )

    return eval_id


# ---------------------------------------------------------------------------
# Param Tune Handler
# ---------------------------------------------------------------------------

async def param_tune_handler(job_id: str, params: dict, cancel_event, progress_cb) -> str | None:
    """Job registry handler for parameter tuning grid search.

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

    logger.info(
        "Param tune started: job_id=%s user_id=%s models=%d",
        job_id, user_id, len(model_ids),
    )

    # Load suite + test cases
    suite = await db.get_tool_suite(suite_id, user_id)
    cases = await db.get_test_cases(suite_id)
    tools = json.loads(suite["tools_json"])

    # Build targets first (may differ from model_ids if duplicates exist)
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    targets = _filter_targets(all_targets, model_ids, target_set)

    # Expand search spaces -- use len(targets) not len(model_ids) for accurate count
    per_model_combos: dict[str, list[dict]] = {}
    if per_model_search_spaces and isinstance(per_model_search_spaces, dict):
        for mid, ss in per_model_search_spaces.items():
            if isinstance(ss, dict) and ss:
                per_model_combos[mid] = _expand_search_space(ss)
        combos = _expand_search_space(search_space) if search_space else [{}]
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

    # Pre-create the tune run in DB
    tune_id = await db.save_param_tune_run(
        user_id=user_id,
        suite_id=suite["id"],
        suite_name=suite["name"],
        models_json=json.dumps(model_ids),
        search_space_json=json.dumps(per_model_search_spaces if per_model_combos else search_space),
        total_combos=total_combos,
        experiment_id=experiment_id,
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
        "total_combos": total_combos,
        "models": model_ids,
        "suite_name": suite["name"],
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

                # Trim case results for storage/WS (exclude raw_request/raw_response)
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
                results_json=json.dumps(all_results),
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

        # Incrementally save results to DB so reconnecting clients can fetch them
        await db.update_param_tune_run(
            tune_id, user_id,
            results_json=json.dumps(all_results),
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

    await db.update_param_tune_run(
        tune_id, user_id,
        results_json=json.dumps(all_results),
        best_config_json=json.dumps(best_config),
        best_score=best_score,
        completed_combos=completed,
        status="completed",
        duration_s=round(duration, 2),
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

                promoted_summary = [{
                    "model_id": best["model_id"],
                    "model_name": best.get("model_name", best["model_id"]),
                    "provider": best.get("provider_key", ""),
                    "tool_accuracy_pct": best.get("tool_accuracy", 0.0),
                    "param_accuracy_pct": best.get("param_accuracy", 0.0),
                    "overall_pct": round(best.get("overall_score", 0.0) * 100, 1),
                    "cases_run": best.get("cases_total", 0),
                    "cases_passed": best.get("cases_passed", 0),
                }]

                promoted_eval_id = await db.save_tool_eval_run(
                    user_id=user_id,
                    suite_id=suite["id"],
                    suite_name=suite["name"],
                    models_json=json.dumps([best["model_id"]]),
                    results_json=json.dumps(promoted_results),
                    summary_json=json.dumps(promoted_summary),
                    temperature=config_for_promote.get("temperature", 0.0),
                    config_json=json.dumps(config_for_promote),
                    experiment_id=experiment_id,
                )

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
    tools = json.loads(suite["tools_json"])

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

    # Pre-create DB record
    tune_id = await db.save_prompt_tune_run(
        user_id=user_id,
        suite_id=suite["id"],
        suite_name=suite["name"],
        mode=mode,
        target_models_json=json.dumps(target_model_ids),
        meta_model=meta_model_id,
        base_prompt=base_prompt,
        config_json=json.dumps(cfg),
        total_prompts=total_prompts,
        experiment_id=experiment_id,
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

        # Incrementally save results to DB
        await db.update_prompt_tune_run(
            tune_id, user_id,
            generations_json=json.dumps(all_generations),
            best_prompt=best_prompt,
            best_score=best_score,
            completed_prompts=completed_prompts,
        )

    # --- Tuning complete ---
    duration = time.perf_counter() - start_time

    if cancel_event.is_set():
        await db.update_prompt_tune_run(
            tune_id, user_id,
            generations_json=json.dumps(all_generations),
            best_prompt=best_prompt,
            best_score=best_score,
            completed_prompts=completed_prompts,
            status="cancelled",
            duration_s=round(duration, 2),
        )
        return None

    await db.update_prompt_tune_run(
        tune_id, user_id,
        generations_json=json.dumps(all_generations),
        best_prompt=best_prompt,
        best_score=best_score,
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
    judge_model_id = params["judge_model"]
    judge_provider_key = params.get("judge_provider_key")
    custom_instructions = params.get("custom_instructions", "")
    concurrency = int(params.get("concurrency", 4))
    experiment_id = params.get("experiment_id")

    logger.info(
        "Judge started: job_id=%s user_id=%s eval_run_id=%s concurrency=%d",
        job_id, user_id, eval_run_id, concurrency,
    )

    # Load eval run
    eval_run = await db.get_tool_eval_run(eval_run_id, user_id)
    results = json.loads(eval_run.get("results_json", "[]"))

    # Load suite for tool definitions
    suite = await db.get_tool_suite(eval_run["suite_id"], user_id)
    tools = json.loads(suite["tools_json"]) if suite else []
    tool_defs_text = _build_tool_definitions_text(tools)

    # Build judge target
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    judge_targets = _find_target(all_targets, judge_model_id, judge_provider_key)
    if not judge_targets:
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

    # Helper to send WebSocket messages
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Create judge report in DB
    report_id = await db.save_judge_report(
        user_id=user_id,
        judge_model=judge_model_id,
        mode="post_eval",
        eval_run_id=eval_run_id,
        experiment_id=experiment_id,
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

        if cancel_event.is_set():
            if report_id:
                await db.update_judge_report(report_id, verdicts_json=json.dumps(all_verdicts), status="error")
            return None

        # Cross-case analysis per model
        tgt = target_map.get(model_id)
        mname = tgt.display_name if tgt else model_id
        report_data = await _judge_crosscase(judge_target, mname, model_verdicts)
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
        verdicts_json=json.dumps(all_verdicts),
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
    judge_model_id = params["judge_model"]
    judge_provider_key = params.get("judge_provider_key")
    concurrency = int(params.get("concurrency", 4))
    experiment_id = params.get("experiment_id")

    logger.info(
        "Judge compare started: job_id=%s user_id=%s run_a=%s run_b=%s",
        job_id, user_id, eval_run_id_a, eval_run_id_b,
    )

    # Load both runs
    run_a = await db.get_tool_eval_run(eval_run_id_a, user_id)
    run_b = await db.get_tool_eval_run(eval_run_id_b, user_id)
    results_a = json.loads(run_a.get("results_json", "[]"))
    results_b = json.loads(run_b.get("results_json", "[]"))

    # Load suite for tool definitions
    suite = await db.get_tool_suite(run_a["suite_id"], user_id)
    tools = json.loads(suite["tools_json"]) if suite else []
    tool_defs_text = _build_tool_definitions_text(tools)

    # Build judge target
    config = await _get_user_config(user_id)
    all_targets = build_targets(config)
    judge_targets = _find_target(all_targets, judge_model_id, judge_provider_key)
    if not judge_targets:
        return None
    judge_target = judge_targets[0]

    if judge_target.provider_key:
        encrypted = await db.get_user_key_for_provider(user_id, judge_target.provider_key)
        if encrypted:
            judge_target = inject_user_keys([judge_target], {judge_target.provider_key: encrypted})[0]

    # Helper to send WebSocket messages
    async def _ws_send(payload: dict):
        if ws_manager:
            await ws_manager.send_to_user(user_id, payload)

    # Determine model names
    models_a = json.loads(run_a.get("models_json", "[]"))
    models_b = json.loads(run_b.get("models_json", "[]"))
    model_a_name = models_a[0] if models_a else "Model A"
    model_b_name = models_b[0] if models_b else "Model B"
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

    # Create report in DB
    report_id = await db.save_judge_report(
        user_id=user_id,
        judge_model=judge_model_id,
        mode="comparative",
        eval_run_id=eval_run_id_a,
        eval_run_id_b=eval_run_id_b,
        experiment_id=experiment_id,
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
                await db.update_judge_report(report_id, verdicts_json=json.dumps(case_comparisons), status="error")
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
        verdicts_json=json.dumps(case_comparisons),
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
    job_registry.register_handler("judge", judge_handler)
    job_registry.register_handler("judge_compare", judge_compare_handler)

"""Tool eval routes: suites, test cases, eval execution, history."""

import json
import logging
import re
import time

import litellm

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth
import db
from benchmark import Target, build_targets, sanitize_error
from schemas import ToolSuiteCreate, ToolSuiteUpdate, TestCaseCreate, ToolEvalRequest
from job_registry import registry as job_registry
from provider_params import build_litellm_kwargs
from routers.helpers import (
    _get_user_config,
    _parse_target_selection,
    _filter_targets,
    _get_user_cancel,
    _check_rate_limit,
    _validate_tools,
    _parse_expected_tool,
    _serialize_expected_tool,
    _tool_matches,
    _capture_raw_response,
    _parse_ground_truth_call,
    _normalize_bfcl_schema_types,
    score_tool_selection,
    score_params,
    compute_overall_score,
    score_multi_turn,
    score_abstention,
    classify_format_compliance,
    classify_error_type,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tool_eval"])

# Module-level ws_manager -- set by app.py after import
ws_manager = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_defs_to_openai_format(tool_defs: list[dict]) -> list[dict]:
    """Convert tool_definitions rows to OpenAI tool format for LiteLLM calls."""
    tools = []
    for td in tool_defs:
        params = td.get("parameters_schema", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}
        tools.append({
            "type": "function",
            "function": {
                "name": td["name"],
                "description": td.get("description", ""),
                "parameters": params,
            },
        })
    return tools


# ---------------------------------------------------------------------------
# Eval Engine: Single Eval Execution
# ---------------------------------------------------------------------------

async def run_single_eval(
    target: Target,
    tools: list[dict],
    test_case: dict,
    temperature: float,
    tool_choice: str = "required",
    provider_params: dict | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Run one test case against one model. Returns result dict.

    Uses litellm.acompletion() (non-streaming, since we need tool_calls).
    Optional system_prompt injects a system message before the user prompt
    (used by Prompt Tuner to test prompt variations).
    """
    # Parse expected values
    expected_tool = _parse_expected_tool(test_case.get("expected_tool"))
    expected_params = test_case.get("expected_params")
    if isinstance(expected_params, str):
        try:
            expected_params = json.loads(expected_params)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse expected_params for test case %s", test_case.get("id"))
            expected_params = None

    # Parse scoring config for fuzzy matching (S3)
    scoring_config = None
    sc_raw = test_case.get("scoring_config_json")
    if sc_raw:
        try:
            scoring_config = json.loads(sc_raw) if isinstance(sc_raw, str) else sc_raw
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse scoring_config_json for test case %s", test_case.get("id"))

    # Irrelevance detection: should this test case expect a tool call?
    # DB stores as INTEGER (1/0), coerce to bool
    raw_sct = test_case.get("should_call_tool", 1)
    should_call_tool = bool(raw_sct) if raw_sct is not None else True

    # T3: category from test case
    case_category = test_case.get("category")

    result = {
        "model_id": target.model_id,
        "test_case_id": test_case["id"],
        "prompt": test_case["prompt"],
        "expected_tool": expected_tool,
        "expected_params": expected_params,
        "actual_tool": None,
        "actual_params": None,
        "tool_selection_score": 0.0,
        "param_accuracy": None,
        "overall_score": 0.0,
        "success": True,
        "error": "",
        "latency_ms": 0,
        "raw_request": None,
        "raw_response": None,
        "should_call_tool": should_call_tool,
        "irrelevance_score": None,
        # T1: format compliance
        "format_compliance": "PASS",
        # T2: error type classification
        "error_type": None,
        # T3: category tag
        "category": case_category,
    }

    # Build validated+clamped params via provider_params module
    pp_copy = dict(provider_params) if provider_params else None
    extra = build_litellm_kwargs(
        target, provider_params=pp_copy, temperature=temperature,
    )

    # Build messages: per-model system_prompt (from config) + explicit system_prompt (from prompt tuner)
    messages = []
    combined_system = ""
    if target.system_prompt:
        combined_system = target.system_prompt
    if system_prompt:
        combined_system = (combined_system + "\n\n" + system_prompt) if combined_system else system_prompt
    if combined_system:
        messages.append({"role": "system", "content": combined_system})
    messages.append({"role": "user", "content": test_case["prompt"]})

    kwargs = {
        "model": target.model_id,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": 1024,
        "timeout": 120,
    }
    if target.api_base:
        kwargs["api_base"] = target.api_base
    if target.api_key:
        kwargs["api_key"] = target.api_key
    # Apply validated params from build_litellm_kwargs
    if extra:
        kwargs.update(extra)
        logger.info("Eval params for %s: %s", target.model_id, {k: v for k, v in extra.items() if k != "api_key"})
    else:
        # Fallback: no provider_params, apply directly (backward compat)
        if "temperature" not in (target.skip_params or []):
            kwargs["temperature"] = temperature
        if target.skip_params:
            for p in target.skip_params:
                if p != "temperature":
                    kwargs.pop(p, None)

    # Capture raw request (sanitize: remove api_key)
    raw_req = dict(kwargs)
    raw_req.pop("api_key", None)
    # Convert tools to a summary (full tools are too large for storage)
    if "tools" in raw_req:
        raw_req["tools_summary"] = [t["function"]["name"] for t in raw_req["tools"]]
        raw_req["tools_count"] = len(raw_req["tools"])
        raw_req["tools"] = raw_req["tools"]  # Keep full tools for inspection
    result["raw_request"] = raw_req

    # T1/T2: Track whether native tool_calls were used or normalization occurred
    _had_native_tool_calls = False
    _tool_name_was_json_blob = False
    _params_parse_failed = False

    try:
        start = time.perf_counter()
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception:
            # Fallback: some providers don't support tool_choice="required"
            if kwargs.get("tool_choice") == "required":
                logger.debug("tool_choice=required failed, falling back to auto for %s", target.model_id)
                kwargs["tool_choice"] = "auto"
                response = await litellm.acompletion(**kwargs)
            else:
                raise
        latency_ms = (time.perf_counter() - start) * 1000

        message = response.choices[0].message
        if message.tool_calls and len(message.tool_calls) > 0:
            _had_native_tool_calls = True
            result["actual_tool"] = message.tool_calls[0].function.name
            try:
                result["actual_params"] = json.loads(message.tool_calls[0].function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse tool call arguments")
                result["actual_params"] = None
                _params_parse_failed = True
        else:
            result["actual_tool"] = None
            result["actual_params"] = None

        # Normalize: some local LLMs stuff a full JSON object into function.name
        _raw_tool = result.get("actual_tool")
        if _raw_tool and _raw_tool.strip().startswith("{"):
            _tool_name_was_json_blob = True
            try:
                parsed = json.loads(_raw_tool)
                if "name" in parsed:
                    result["actual_tool"] = parsed["name"]
                    if not result.get("actual_params") and (parsed.get("arguments") or parsed.get("parameters")):
                        result["actual_params"] = parsed.get("arguments") or parsed.get("parameters")
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse JSON-in-tool-name for model %s", target.model_id)
        elif not _raw_tool and message.content:
            try:
                content = message.content.strip()
                start_idx = content.find('{')
                end_idx = content.rfind('}')
                if start_idx >= 0 and end_idx > start_idx:
                    parsed = json.loads(content[start_idx:end_idx + 1])
                    if "name" in parsed:
                        result["actual_tool"] = parsed["name"]
                        result["actual_params"] = parsed.get("arguments") or parsed.get("parameters") or {}
            except Exception:
                logger.debug("Failed to extract tool call from message content for model %s", target.model_id)

        result["latency_ms"] = round(latency_ms)

        result["raw_response"] = _capture_raw_response(response)

    except Exception as e:
        result["success"] = False
        result["error"] = sanitize_error(str(e)[:200], target.api_key)
        result["raw_request"] = raw_req
        # T1: failed API call = FAIL
        result["format_compliance"] = "FAIL"
        result["error_type"] = "invalid_invocation"
        return result

    # Score
    result["tool_selection_score"] = score_tool_selection(expected_tool, result["actual_tool"])
    result["param_accuracy"] = score_params(expected_params, result["actual_params"], scoring_config=scoring_config)
    result["overall_score"] = compute_overall_score(result["tool_selection_score"], result["param_accuracy"])

    # Irrelevance score: how well did model handle abstention expectation?
    result["irrelevance_score"] = score_abstention(should_call_tool, result["actual_tool"])

    # T1: Format compliance classification
    result["format_compliance"] = classify_format_compliance(
        raw_response_had_tool_calls=_had_native_tool_calls,
        tool_name_was_json_blob=_tool_name_was_json_blob,
        params_parse_failed=_params_parse_failed,
        actual_tool=result["actual_tool"],
        expected_tool=expected_tool,
    )

    # T2: Error type classification
    tool_names_in_suite = {t["function"]["name"].lower() for t in tools if isinstance(t, dict) and "function" in t}
    result["error_type"] = classify_error_type(
        success=result["success"],
        actual_tool=result["actual_tool"],
        actual_params=result["actual_params"],
        expected_tool=expected_tool,
        expected_params=expected_params,
        tool_names_in_suite=tool_names_in_suite,
        overall_score=result["overall_score"],
        params_parse_failed=_params_parse_failed,
    )

    return result


# ---------------------------------------------------------------------------
# Eval Engine: Multi-Turn Eval Execution
# ---------------------------------------------------------------------------

async def run_multi_turn_eval(
    target: Target,
    tools: list[dict],
    test_case: dict,
    temperature: float,
    tool_choice: str = "required",
    provider_params: dict | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Run a multi-turn test case against one model. Returns result dict.

    Loops up to max_rounds, feeding mock tool responses back to the model
    until it calls the expected final tool or exhausts rounds.
    """
    mt_config = test_case.get("_mt_config", {})
    max_rounds = mt_config.get("max_rounds", 5)
    mock_responses = mt_config.get("mock_responses", {})
    valid_prerequisites = mt_config.get("valid_prerequisites", [])
    optimal_hops = mt_config.get("optimal_hops", 2)

    expected_tool = _parse_expected_tool(test_case.get("expected_tool"))
    expected_params = test_case.get("expected_params")
    if isinstance(expected_params, str):
        try:
            expected_params = json.loads(expected_params)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse expected_params for multi-turn test case %s", test_case.get("id"))
            expected_params = None

    # Parse scoring config for fuzzy matching (S3)
    scoring_config = None
    sc_raw = test_case.get("scoring_config_json")
    if sc_raw:
        try:
            scoring_config = json.loads(sc_raw) if isinstance(sc_raw, str) else sc_raw
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse scoring_config_json for multi-turn test case %s", test_case.get("id"))

    # Irrelevance detection: should this test case expect a tool call?
    raw_sct = test_case.get("should_call_tool", 1)
    should_call_tool = bool(raw_sct) if raw_sct is not None else True

    # T3: category from test case
    case_category = test_case.get("category")

    result = {
        "model_id": target.model_id,
        "test_case_id": test_case["id"],
        "prompt": test_case["prompt"],
        "expected_tool": expected_tool,
        "expected_params": expected_params,
        "actual_tool": None,
        "actual_params": None,
        "tool_selection_score": 0.0,
        "param_accuracy": None,
        "overall_score": 0.0,
        "success": True,
        "error": "",
        "latency_ms": 0,
        "raw_request": None,
        "raw_response": None,
        # Multi-turn specific fields
        "multi_turn": True,
        "tool_chain": [],
        "rounds_used": 0,
        "completion_score": 0.0,
        "efficiency_score": 0.0,
        "redundancy_penalty": 0.0,
        "detour_penalty": 0.0,
        "raw_exchanges": [],
        "should_call_tool": should_call_tool,
        "irrelevance_score": None,
        # T1: format compliance
        "format_compliance": "PASS",
        # T2: error type
        "error_type": None,
        # T3: category
        "category": case_category,
    }

    # Build messages: per-model system_prompt (from config) + explicit system_prompt (from prompt tuner)
    messages = []
    combined_system = ""
    if target.system_prompt:
        combined_system = target.system_prompt
    if system_prompt:
        combined_system = (combined_system + "\n\n" + system_prompt) if combined_system else system_prompt
    if combined_system:
        messages.append({"role": "system", "content": combined_system})
    messages.append({"role": "user", "content": test_case["prompt"]})

    # Build validated+clamped params via provider_params module
    pp_copy = dict(provider_params) if provider_params else None
    extra = build_litellm_kwargs(
        target, provider_params=pp_copy, temperature=temperature,
    )

    base_kwargs = {
        "model": target.model_id,
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": 1024,
        "timeout": 120,
    }
    if target.api_base:
        base_kwargs["api_base"] = target.api_base
    if target.api_key:
        base_kwargs["api_key"] = target.api_key
    # Apply validated params from build_litellm_kwargs
    if extra:
        base_kwargs.update(extra)
        logger.info("Multi-turn eval params for %s: %s", target.model_id, {k: v for k, v in extra.items() if k != "api_key"})
    else:
        # Fallback: no provider_params, apply directly (backward compat)
        if "temperature" not in (target.skip_params or []):
            base_kwargs["temperature"] = temperature
        if target.skip_params:
            for p in target.skip_params:
                if p != "temperature":
                    base_kwargs.pop(p, None)

    total_latency = 0.0

    try:
        for round_num in range(max_rounds):
            kwargs = {**base_kwargs, "messages": messages}

            # Capture raw request (sanitize)
            raw_req = dict(kwargs)
            raw_req.pop("api_key", None)
            if "tools" in raw_req:
                raw_req["tools_summary"] = [t["function"]["name"] for t in raw_req["tools"]]
                raw_req["tools_count"] = len(raw_req["tools"])

            start = time.perf_counter()
            try:
                response = await litellm.acompletion(**kwargs)
            except Exception:
                if kwargs.get("tool_choice") == "required":
                    logger.debug("tool_choice=required failed in multi-turn, falling back to auto for %s", target.model_id)
                    kwargs["tool_choice"] = "auto"
                    response = await litellm.acompletion(**kwargs)
                else:
                    raise
            latency_ms = (time.perf_counter() - start) * 1000
            total_latency += latency_ms

            raw_resp = _capture_raw_response(response)
            result["raw_exchanges"].append({"request": raw_req, "response": raw_resp})

            message = response.choices[0].message

            # Check if model made a tool call
            if not message.tool_calls or len(message.tool_calls) == 0:
                # Model stopped calling tools -- end loop
                result["rounds_used"] = round_num + 1
                break

            tool_call = message.tool_calls[0]
            called_tool = tool_call.function.name
            try:
                called_params = json.loads(tool_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse multi-turn tool call arguments")
                called_params = None

            result["tool_chain"].append({
                "tool_name": called_tool,
                "params": called_params,
                "round": round_num + 1,
            })

            # Check if this is the expected final tool
            if _tool_matches(called_tool, expected_tool):
                result["actual_tool"] = called_tool
                result["actual_params"] = called_params
                result["rounds_used"] = round_num + 1
                break

            # Not the final tool -- look up mock response and continue
            mock_result = mock_responses.get(called_tool, {"status": "ok"})

            # Append assistant message + tool result to conversation
            messages.append({
                "role": "assistant",
                "content": getattr(message, "content", None) or "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": called_tool,
                            "arguments": tool_call.function.arguments,
                        }
                    }
                ],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(mock_result) if isinstance(mock_result, dict) else str(mock_result),
            })

            result["rounds_used"] = round_num + 1
        else:
            # Hit max rounds without finding the expected tool
            result["rounds_used"] = max_rounds

        result["latency_ms"] = round(total_latency)

        # Set raw_request/raw_response to first/last exchange for compatibility
        if result["raw_exchanges"]:
            result["raw_request"] = result["raw_exchanges"][0]["request"]
            result["raw_response"] = result["raw_exchanges"][-1]["response"]

        # If model never called the expected tool, actual_tool stays None
        if result["actual_tool"] is None and result["tool_chain"]:
            result["actual_tool"] = result["tool_chain"][-1]["tool_name"]
            result["actual_params"] = result["tool_chain"][-1]["params"]

        # T5: argument_source resolution -- check if any arg should come from previous tool output
        argument_source = mt_config.get("argument_source")  # dict: {arg_name: "tool_name.field"}
        if argument_source and isinstance(argument_source, dict) and result["tool_chain"]:
            # Build map of tool outputs from mock_responses
            tool_outputs: dict[str, dict] = {}
            for chain_step in result["tool_chain"][:-1]:
                tool_nm = chain_step.get("tool_name", "")
                mock = mock_responses.get(tool_nm, {})
                if isinstance(mock, dict):
                    tool_outputs[tool_nm] = mock

            # Verify argument values match expected sources
            actual_params = result.get("actual_params") or {}
            nested_score = 1.0
            for arg_name, source_ref in argument_source.items():
                # source_ref format: "tool_name.field"
                if "." in source_ref:
                    src_tool, src_field = source_ref.split(".", 1)
                    expected_val = tool_outputs.get(src_tool, {}).get(src_field)
                    actual_val = actual_params.get(arg_name)
                    if expected_val is not None and actual_val != expected_val:
                        nested_score = 0.0
                        break
            result["nested_arg_score"] = nested_score
        else:
            result["nested_arg_score"] = None

        # Score using multi-turn scoring
        scores = score_multi_turn(
            tool_chain=result["tool_chain"],
            expected_tool=expected_tool,
            expected_params=expected_params,
            valid_prerequisites=valid_prerequisites,
            optimal_hops=optimal_hops,
            scoring_config=scoring_config,
        )
        result["completion_score"] = scores["completion"]
        result["efficiency_score"] = scores["efficiency"]
        result["redundancy_penalty"] = scores["redundancy_penalty"]
        result["detour_penalty"] = scores["detour_penalty"]
        result["overall_score"] = scores["overall_score"]

        # Also set individual scores for summary compatibility
        result["tool_selection_score"] = score_tool_selection(expected_tool, result["actual_tool"])
        result["param_accuracy"] = score_params(expected_params, result["actual_params"], scoring_config=scoring_config)

        # Irrelevance score: how well did model handle abstention expectation?
        result["irrelevance_score"] = score_abstention(should_call_tool, result["actual_tool"])

        # T1: multi-turn always uses native tool_calls (no normalization path)
        result["format_compliance"] = "PASS" if result["success"] else "FAIL"

        # T2: error type for multi-turn
        result["error_type"] = classify_error_type(
            success=result["success"],
            actual_tool=result["actual_tool"],
            actual_params=result["actual_params"],
            expected_tool=expected_tool,
            expected_params=expected_params,
            tool_names_in_suite=set(),
            overall_score=result["overall_score"],
            is_multi_turn=True,
            rounds_used=result.get("rounds_used", 0),
            optimal_hops=optimal_hops,
        )

    except Exception as e:
        result["success"] = False
        result["error"] = sanitize_error(str(e)[:200], target.api_key)
        result["format_compliance"] = "FAIL"
        result["error_type"] = "invalid_invocation"
        return result

    return result


# ---------------------------------------------------------------------------
# Tool Eval REST endpoints
# ---------------------------------------------------------------------------


@router.get("/api/tool-suites")
async def list_tool_suites(user: dict = Depends(auth.get_current_user)):
    """List user's tool suites."""
    suites = await db.get_tool_suites(user["id"])
    return {"suites": suites}


@router.post("/api/tool-suites")
async def create_tool_suite(request: Request, user: dict = Depends(auth.get_current_user)):
    """Create a new tool suite."""
    body = await request.json()

    # Validate via Pydantic
    try:
        validated = ToolSuiteCreate(
            name=body.get("name", ""),
            description=body.get("description"),
            tools=body.get("tools", []),
            system_prompt=body.get("system_prompt"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    name = validated.name.strip()
    description = validated.description or ""
    tools = validated.tools
    if tools:
        err = _validate_tools(tools)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    suite_id = await db.create_tool_suite(user["id"], name, description, validated.system_prompt)
    if tools:
        await db.create_tool_definitions_batch(suite_id, tools)
    return {"status": "ok", "suite_id": suite_id}


@router.post("/api/tool-eval/import")
async def import_tool_suite(request: Request, user: dict = Depends(auth.get_current_user)):
    """Import a tool suite from JSON. Auto-detects standard vs BFCL format."""
    body = await request.json()
    # Auto-detect BFCL format and handle it transparently
    if _is_bfcl_format(body):
        entries = body if isinstance(body, list) else [body]
        suite_name = request.headers.get("X-Suite-Name") or f"Imported Suite {len(entries)} cases"
        try:
            return await _process_bfcl_import(entries, suite_name, user["id"])
        except HTTPException as exc:
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    description = body.get("description", "")
    tools = body.get("tools", [])
    if tools:
        err = _validate_tools(tools)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    # Build cases list for atomic batch insert (CRIT-5)
    test_cases = body.get("test_cases", [])
    cases = []
    for item in test_cases:
        prompt = item.get("prompt", "").strip()
        if not prompt:
            continue
        expected_tool = _serialize_expected_tool(item.get("expected_tool"))
        expected_params = json.dumps(item["expected_params"]) if item.get("expected_params") is not None else None
        param_scoring = item.get("param_scoring", "exact")
        # Multi-turn config
        mt_config = None
        if item.get("multi_turn") or item.get("multi_turn_config"):
            mt_obj = item.get("multi_turn_config") or {}
            if not mt_obj and item.get("multi_turn"):
                mt_obj = {"multi_turn": True}
            if item.get("max_rounds"):
                mt_obj["max_rounds"] = item["max_rounds"]
            if item.get("mock_responses"):
                mt_obj["mock_responses"] = item["mock_responses"]
            if item.get("valid_prerequisites"):
                mt_obj["valid_prerequisites"] = item["valid_prerequisites"]
            if item.get("optimal_hops"):
                mt_obj["optimal_hops"] = item["optimal_hops"]
            # T5: argument_source for nested tool call support
            if item.get("argument_source"):
                mt_obj["argument_source"] = item["argument_source"]
            if not mt_obj.get("multi_turn"):
                mt_obj["multi_turn"] = True
            mt_config = json.dumps(mt_obj)
        sc_json = json.dumps(item["scoring_config"]) if item.get("scoring_config") else None
        should_call_tool = bool(item.get("should_call_tool", True))
        # T3: category tag
        category = item.get("category")
        cases.append({
            "prompt": prompt,
            "expected_tool": expected_tool,
            "expected_params": expected_params,
            "param_scoring": param_scoring,
            "multi_turn_config": mt_config,
            "scoring_config_json": sc_json,
            "should_call_tool": should_call_tool,
            "category": category,
        })
    suite_id = await db.create_suite_with_cases(
        user["id"], name, description, tools, cases
    )
    return {"status": "ok", "suite_id": suite_id, "test_cases_created": len(cases)}


@router.get("/api/tool-suites/{suite_id}")
async def get_tool_suite(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full suite with tools and test cases."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    # Fetch tool definitions from normalized table
    tool_defs = await db.get_tool_definitions(suite_id)
    suite["tools"] = _tool_defs_to_openai_format(tool_defs)
    cases = await db.get_test_cases(suite_id)
    for c in cases:
        c["expected_tool"] = _parse_expected_tool(c["expected_tool"])
        if c["expected_params"]:
            try:
                c["expected_params"] = json.loads(c["expected_params"])
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse expected_params for test case in suite %s", suite_id)
        if c.get("multi_turn_config"):
            try:
                mt = json.loads(c["multi_turn_config"]) if isinstance(c["multi_turn_config"], str) else c["multi_turn_config"]
                c["multi_turn"] = mt.get("multi_turn", False)
                c["max_rounds"] = mt.get("max_rounds", 5)
                c["mock_responses"] = mt.get("mock_responses", {})
                c["valid_prerequisites"] = mt.get("valid_prerequisites", [])
                c["optimal_hops"] = mt.get("optimal_hops", 2)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse multi_turn_config for test case in suite %s", suite_id)
        # Parse scoring_config_json for frontend (S3)
        if c.get("scoring_config_json"):
            try:
                c["scoring_config"] = json.loads(c["scoring_config_json"]) if isinstance(c["scoring_config_json"], str) else c["scoring_config_json"]
            except (json.JSONDecodeError, TypeError):
                c["scoring_config"] = None
            del c["scoring_config_json"]
        else:
            c["scoring_config"] = None
    suite["test_cases"] = cases
    return suite


@router.put("/api/tool-suites/{suite_id}")
async def update_tool_suite(suite_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Update suite name/description/tools."""
    body = await request.json()

    # Validate via Pydantic (all fields optional for update)
    try:
        validated = ToolSuiteUpdate(
            name=body.get("name"),
            description=body.get("description"),
            tools=body.get("tools"),
            system_prompt=body.get("system_prompt"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    name = validated.name
    description = validated.description
    tools = validated.tools
    if tools is not None:
        if tools:
            err = _validate_tools(tools)
            if err:
                return JSONResponse({"error": err}, status_code=400)
        # Replace tool definitions: delete old, insert new
        await db.delete_tool_definitions_for_suite(suite_id)
        if tools:
            await db.create_tool_definitions_batch(suite_id, tools)
    updated = await db.update_tool_suite(suite_id, user["id"], name=name, description=description, system_prompt=validated.system_prompt)
    if not updated:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    return {"status": "ok"}


@router.patch("/api/tool-suites/{suite_id}")
async def patch_tool_suite(suite_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Patch suite fields (e.g. system_prompt). Lighter than PUT."""
    body = await request.json()
    kwargs = {}
    if "system_prompt" in body:
        kwargs["system_prompt"] = body["system_prompt"]
    if "name" in body:
        kwargs["name"] = body["name"]
    if "description" in body:
        kwargs["description"] = body["description"]
    if not kwargs:
        return JSONResponse({"error": "No fields to update"}, status_code=400)
    updated = await db.update_tool_suite(suite_id, user["id"], **kwargs)
    if not updated:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    return {"status": "ok"}


@router.delete("/api/tool-suites/{suite_id}")
async def delete_tool_suite(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a suite and its test cases."""
    deleted = await db.delete_tool_suite(suite_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    return {"status": "ok"}


@router.get("/api/tool-suites/{suite_id}/export")
async def export_tool_suite(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """Export a tool suite as a downloadable JSON file (matches import format)."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    tool_defs = await db.get_tool_definitions(suite_id)
    tools = _tool_defs_to_openai_format(tool_defs)
    cases = await db.get_test_cases(suite_id)
    test_cases = []
    for c in cases:
        tc = {"prompt": c["prompt"]}
        et = _parse_expected_tool(c["expected_tool"])
        if et is not None:
            tc["expected_tool"] = et
        if c.get("expected_params"):
            try:
                tc["expected_params"] = json.loads(c["expected_params"]) if isinstance(c["expected_params"], str) else c["expected_params"]
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse expected_params during export")
        if c.get("param_scoring") and c["param_scoring"] != "exact":
            tc["param_scoring"] = c["param_scoring"]
        if c.get("multi_turn_config"):
            try:
                mt = json.loads(c["multi_turn_config"]) if isinstance(c["multi_turn_config"], str) else c["multi_turn_config"]
                if mt.get("multi_turn"):
                    tc["multi_turn"] = True
                    for k in ("max_rounds", "mock_responses", "valid_prerequisites", "optimal_hops"):
                        if k in mt:
                            tc[k] = mt[k]
                    # T5: argument_source
                    if mt.get("argument_source"):
                        tc["argument_source"] = mt["argument_source"]
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse multi_turn_config during export")
        # Include scoring_config if set (S3)
        if c.get("scoring_config_json"):
            try:
                sc = json.loads(c["scoring_config_json"]) if isinstance(c["scoring_config_json"], str) else c["scoring_config_json"]
                if sc:
                    tc["scoring_config"] = sc
            except (json.JSONDecodeError, TypeError):
                pass
        # T3: category tag
        if c.get("category"):
            tc["category"] = c["category"]
        test_cases.append(tc)
    export_data = {
        "name": suite.get("name", "Untitled"),
        "description": suite.get("description", ""),
        "tools": tools,
        "test_cases": test_cases,
    }
    slug = re.sub(r'[^a-z0-9]+', '-', (suite.get("name") or "suite").lower()).strip('-')[:40]
    headers = {"Content-Disposition": f'attachment; filename=suite-{slug}.json'}
    return JSONResponse(content=export_data, headers=headers)


@router.get("/api/tool-suites/{suite_id}/export/bfcl")
async def export_tool_suite_bfcl(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """T4: Export suite in BFCL V3-compatible JSON format.

    BFCL dataset structure: each entry has an 'id', 'question', 'function', and 'answer'.
    """
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)

    tool_defs = await db.get_tool_definitions(suite_id)
    tools = _tool_defs_to_openai_format(tool_defs)
    cases = await db.get_test_cases(suite_id)

    # Build BFCL function definitions from our tools format
    bfcl_functions = []
    for tool in tools:
        if isinstance(tool, dict) and "function" in tool:
            fn = tool["function"]
            bfcl_functions.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            })

    bfcl_entries = []
    for idx, c in enumerate(cases):
        et = _parse_expected_tool(c.get("expected_tool"))
        expected_params = None
        if c.get("expected_params"):
            try:
                expected_params = json.loads(c["expected_params"]) if isinstance(c["expected_params"], str) else c["expected_params"]
            except (json.JSONDecodeError, TypeError):
                pass

        # Build BFCL answer format: list of tool call dicts
        answer = []
        if et:
            tool_name = et[0] if isinstance(et, list) else et
            answer.append({tool_name: expected_params or {}})

        entry = {
            "id": f"{suite.get('name', 'suite')}_{idx}",
            "question": [[{"role": "user", "content": c["prompt"]}]],
            "function": bfcl_functions,
            "answer": answer,
        }
        # T3: include category as BFCL test_category if set
        if c.get("category"):
            entry["test_category"] = c["category"]

        bfcl_entries.append(entry)

    slug = re.sub(r'[^a-z0-9]+', '-', (suite.get("name") or "suite").lower()).strip('-')[:40]
    headers = {"Content-Disposition": f'attachment; filename=suite-{slug}-bfcl.json'}
    return JSONResponse(content=bfcl_entries, headers=headers)


async def _process_bfcl_import(entries: list[dict], suite_name: str, user_id: str) -> dict:
    """Shared BFCL import logic used by both the unified and dedicated endpoints.

    Returns dict with status, suite_id, test_cases_created on success.
    Raises HTTPException on validation errors.
    """
    if not entries:
        raise HTTPException(400, detail="No entries found")

    suite_name = (suite_name or f"BFCL Import {len(entries)} cases").strip()[:256]

    # Collect unique function definitions from all entries
    seen_funcs: dict[str, dict] = {}
    for entry in entries:
        for fn in entry.get("function", []):
            name = fn.get("name", "")
            if name and name not in seen_funcs:
                seen_funcs[name] = fn

    # Convert BFCL function format to our tools format
    tools = []
    for fn in seen_funcs.values():
        params = fn.get("parameters", {"type": "object", "properties": {}})
        _normalize_bfcl_schema_types(params)
        tools.append({
            "type": "function",
            "function": {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": params,
            }
        })

    if tools:
        err = _validate_tools(tools)
        if err:
            raise HTTPException(400, detail=f"Invalid tools: {err}")

    # Build cases list for atomic batch insert (CRIT-5)
    cases = []
    for entry in entries:
        # BFCL question format: [[{role, content}, ...], ...]
        question = entry.get("question", [])
        prompt = ""
        if question and isinstance(question, list):
            for turn in question:
                if isinstance(turn, list):
                    for msg in turn:
                        if isinstance(msg, dict) and msg.get("role") == "user":
                            prompt = msg.get("content", "")
                            break
                if prompt:
                    break
        if not prompt:
            continue

        # Parse answer to extract expected tool + params
        answer = entry.get("answer", [])
        category = entry.get("test_category")
        parsed_calls = []

        # Path 1: structured answer dicts (existing BFCL export format)
        if answer and isinstance(answer, list) and len(answer) > 0 and isinstance(answer[0], dict):
            for tool_call in answer:
                for tool_name, params in tool_call.items():
                    parsed_calls.append((
                        _serialize_expected_tool(tool_name),
                        json.dumps(params) if params else None,
                    ))

        # Path 2: ground_truth (structured dicts OR call strings)
        elif not parsed_calls:
            gt = entry.get("ground_truth")
            if gt:
                gt_list = gt if isinstance(gt, list) else [gt]
                for gt_item in gt_list:
                    if isinstance(gt_item, dict) and gt_item.get("name"):
                        # Structured format: {"name": "tool", "arguments": {...}}
                        tool_name = gt_item["name"]
                        params = gt_item.get("arguments") or gt_item.get("params") or {}
                        parsed_calls.append((
                            _serialize_expected_tool(tool_name),
                            json.dumps(params) if params else None,
                        ))
                    elif isinstance(gt_item, str):
                        # Raw HuggingFace format: "func(a=1, b=2)"
                        parsed = _parse_ground_truth_call(gt_item)
                        if parsed:
                            for tool_name, params in parsed.items():
                                parsed_calls.append((
                                    _serialize_expected_tool(tool_name),
                                    json.dumps(params) if params else None,
                                ))

        # Path 3: irrelevance — no answer and no ground_truth
        if not parsed_calls:
            if not answer and not entry.get("ground_truth"):
                cases.append({
                    "prompt": prompt,
                    "expected_tool": None,
                    "expected_params": None,
                    "param_scoring": "exact",
                    "category": category or "irrelevance",
                    "should_call_tool": False,
                })
            else:
                # Had answer/gt but couldn't parse — single case with no expected
                cases.append({
                    "prompt": prompt,
                    "expected_tool": None,
                    "expected_params": None,
                    "param_scoring": "exact",
                    "category": category,
                })
        else:
            # Use first parsed call per BFCL entry (our eval scores one tool call per response,
            # so expanding parallel calls into separate cases just creates duplicates)
            expected_tool_val, expected_params_val = parsed_calls[0]
            cases.append({
                "prompt": prompt,
                "expected_tool": expected_tool_val,
                "expected_params": expected_params_val,
                "param_scoring": "exact",
                "category": category,
            })

    suite_id = await db.create_suite_with_cases(
        user_id, suite_name, "", tools, cases
    )
    return {"status": "ok", "suite_id": suite_id, "test_cases_created": len(cases)}


def _is_bfcl_format(body) -> bool:
    """Detect whether parsed JSON body is BFCL format."""
    if isinstance(body, list):
        return True
    if isinstance(body, dict) and ("function" in body or "question" in body):
        return True
    return False


@router.post("/api/tool-eval/import/bfcl")
async def import_bfcl_suite(request: Request, user: dict = Depends(auth.get_current_user)):
    """T4: Import a BFCL V3-compatible JSON file as a tool suite."""
    try:
        body = await request.json()
    except Exception:
        # Fallback: try JSONL (one JSON object per line)
        try:
            raw = (await request.body()).decode("utf-8")
            body = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    # Support both single entry and array
    if isinstance(body, dict):
        entries = [body]
    elif isinstance(body, list):
        entries = body
    else:
        return JSONResponse({"error": "Expected JSON array or object"}, status_code=400)

    suite_name = request.headers.get("X-Suite-Name") or f"BFCL Import {len(entries)} cases"

    try:
        return await _process_bfcl_import(entries, suite_name, user["id"])
    except HTTPException as exc:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@router.get("/api/tool-eval/import/example")
async def tool_eval_import_example():
    """Return an example JSON template for suite import."""
    example = {
        "name": "Weather API Suite",
        "description": "Tests weather-related tool calling",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                            "units": {"type": "string", "enum": ["celsius", "fahrenheit"], "description": "Temperature units"}
                        },
                        "required": ["city"]
                    }
                }
            }
        ],
        "test_cases": [
            {
                "prompt": "What's the weather in Paris?",
                "expected_tool": "get_weather",
                "expected_params": {"city": "Paris"}
            },
            {
                "prompt": "Check temperature in Tokyo in fahrenheit",
                "expected_tool": "get_weather",
                "expected_params": {"city": "Tokyo", "units": "fahrenheit"}
            },
            {
                "prompt": "Tell me a joke",
                "expected_tool": None,
                "expected_params": None
            }
        ]
    }
    headers = {"Content-Disposition": 'attachment; filename=suite-example.json'}
    return JSONResponse(content=example, headers=headers)


# --- Test Cases ---

@router.get("/api/tool-suites/{suite_id}/cases")
async def list_test_cases(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """List test cases for a suite."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    cases = await db.get_test_cases(suite_id)
    for c in cases:
        c["expected_tool"] = _parse_expected_tool(c["expected_tool"])
        if c["expected_params"]:
            try:
                c["expected_params"] = json.loads(c["expected_params"])
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse expected_params for case in suite %s", suite_id)
        if c.get("multi_turn_config"):
            try:
                mt = json.loads(c["multi_turn_config"]) if isinstance(c["multi_turn_config"], str) else c["multi_turn_config"]
                c["multi_turn"] = mt.get("multi_turn", False)
                c["max_rounds"] = mt.get("max_rounds", 5)
                c["mock_responses"] = mt.get("mock_responses", {})
                c["valid_prerequisites"] = mt.get("valid_prerequisites", [])
                c["optimal_hops"] = mt.get("optimal_hops", 2)
                # T5: argument_source
                if mt.get("argument_source"):
                    c["argument_source"] = mt["argument_source"]
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse multi_turn_config for case in suite %s", suite_id)
        # Parse scoring_config_json for frontend (S3)
        if c.get("scoring_config_json"):
            try:
                c["scoring_config"] = json.loads(c["scoring_config_json"]) if isinstance(c["scoring_config_json"], str) else c["scoring_config_json"]
            except (json.JSONDecodeError, TypeError):
                c["scoring_config"] = None
            del c["scoring_config_json"]
        else:
            c["scoring_config"] = None
    return {"cases": cases}


@router.post("/api/tool-suites/{suite_id}/cases")
async def create_test_cases(suite_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Add test case(s) to a suite. Supports single or bulk via 'cases' array."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    body = await request.json()

    def _extract_mt_config(item: dict) -> str | None:
        """Extract multi_turn_config JSON string from a request item."""
        if not item.get("multi_turn") and not item.get("multi_turn_config"):
            return None
        mt_obj = item.get("multi_turn_config") or {}
        if not mt_obj and item.get("multi_turn"):
            mt_obj = {"multi_turn": True}
        if item.get("max_rounds"):
            mt_obj["max_rounds"] = item["max_rounds"]
        if item.get("mock_responses"):
            mt_obj["mock_responses"] = item["mock_responses"]
        if item.get("valid_prerequisites"):
            mt_obj["valid_prerequisites"] = item["valid_prerequisites"]
        if item.get("optimal_hops"):
            mt_obj["optimal_hops"] = item["optimal_hops"]
        # T5: argument_source for nested tool call support
        if item.get("argument_source"):
            mt_obj["argument_source"] = item["argument_source"]
        if not mt_obj.get("multi_turn"):
            mt_obj["multi_turn"] = True
        return json.dumps(mt_obj)

    # Bulk mode
    if "cases" in body and isinstance(body["cases"], list):
        created = 0
        for item in body["cases"]:
            prompt = item.get("prompt", "").strip()
            if not prompt:
                continue
            expected_tool = _serialize_expected_tool(item.get("expected_tool"))
            expected_params = json.dumps(item["expected_params"]) if item.get("expected_params") is not None else None
            param_scoring = item.get("param_scoring", "exact")
            mt_config = _extract_mt_config(item)
            sc_json = json.dumps(item["scoring_config"]) if item.get("scoring_config") else None
            should_call_tool = bool(item.get("should_call_tool", True))
            # T3: category tag
            category = item.get("category")
            await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config=mt_config, scoring_config_json=sc_json, should_call_tool=should_call_tool, category=category)
            created += 1
        return {"status": "ok", "created": created}

    # Single mode -- validate via Pydantic
    try:
        validated_case = TestCaseCreate(
            prompt=body.get("prompt", ""),
            expected_tool=body.get("expected_tool"),
            expected_params=body.get("expected_params"),
            param_scoring=body.get("param_scoring", "exact"),
            multi_turn_config=body.get("multi_turn_config"),
            scoring_config_json=body.get("scoring_config"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    prompt = validated_case.prompt.strip()
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    expected_tool = _serialize_expected_tool(body.get("expected_tool"))
    expected_params = json.dumps(body["expected_params"]) if body.get("expected_params") is not None else None
    param_scoring = validated_case.param_scoring
    mt_config = _extract_mt_config(body)
    sc_json = json.dumps(body["scoring_config"]) if body.get("scoring_config") else None
    should_call_tool = bool(body.get("should_call_tool", True))
    # T3: category tag
    category = body.get("category")
    case_id = await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config=mt_config, scoring_config_json=sc_json, should_call_tool=should_call_tool, category=category)
    return {"status": "ok", "case_id": case_id}


@router.put("/api/tool-suites/{suite_id}/cases/{case_id}")
async def update_test_case(suite_id: str, case_id: str, request: Request, user: dict = Depends(auth.get_current_user)):
    """Update a test case."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    body = await request.json()
    prompt = body.get("prompt")
    expected_tool = _serialize_expected_tool(body.get("expected_tool")) if "expected_tool" in body else None
    expected_params = json.dumps(body["expected_params"]) if "expected_params" in body and body["expected_params"] is not None else None
    param_scoring = body.get("param_scoring")
    # Multi-turn config
    mt_config = None
    if "multi_turn" in body or "multi_turn_config" in body:
        if body.get("multi_turn"):
            mt_obj = body.get("multi_turn_config") or {}
            if not mt_obj:
                mt_obj = {"multi_turn": True}
            if body.get("max_rounds"):
                mt_obj["max_rounds"] = body["max_rounds"]
            if body.get("mock_responses"):
                mt_obj["mock_responses"] = body["mock_responses"]
            if body.get("valid_prerequisites"):
                mt_obj["valid_prerequisites"] = body["valid_prerequisites"]
            if body.get("optimal_hops"):
                mt_obj["optimal_hops"] = body["optimal_hops"]
            # T5: argument_source for nested tool call support
            if body.get("argument_source"):
                mt_obj["argument_source"] = body["argument_source"]
            if not mt_obj.get("multi_turn"):
                mt_obj["multi_turn"] = True
            mt_config = json.dumps(mt_obj)
        else:
            # multi_turn explicitly set to false -- clear the config
            mt_config = ""  # empty string to clear in DB
    # Scoring config (S3 fuzzy scoring)
    sc_json = None
    if "scoring_config" in body:
        sc_json = json.dumps(body["scoring_config"]) if body["scoring_config"] else ""  # empty string to clear

    # Irrelevance detection field
    sct = None
    if "should_call_tool" in body:
        sct = bool(body["should_call_tool"])

    # T3: category tag
    category = body.get("category") if "category" in body else None

    updated = await db.update_test_case(case_id, suite_id, prompt=prompt, expected_tool=expected_tool, expected_params=expected_params, param_scoring=param_scoring, multi_turn_config=mt_config, scoring_config_json=sc_json, should_call_tool=sct, category=category)
    if not updated:
        return JSONResponse({"error": "Test case not found"}, status_code=404)
    return {"status": "ok"}


@router.delete("/api/tool-suites/{suite_id}/cases/{case_id}")
async def delete_test_case(suite_id: str, case_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a test case."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    deleted = await db.delete_test_case(case_id, suite_id)
    if not deleted:
        return JSONResponse({"error": "Test case not found"}, status_code=404)
    return {"status": "ok"}


# --- Eval History ---

@router.get("/api/tool-eval/history")
async def list_tool_eval_runs(user: dict = Depends(auth.get_current_user)):
    """List user's past eval runs."""
    runs = await db.get_tool_eval_runs(user["id"])
    return {"runs": runs}


@router.get("/api/tool-eval/history/{eval_id}")
async def get_tool_eval_run(eval_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full eval run details."""
    run = await db.get_tool_eval_run(eval_id, user["id"])
    if not run:
        return JSONResponse({"error": "Eval run not found"}, status_code=404)
    # Fetch case results and summary from normalized tables
    raw_results = await db.get_case_results(eval_id)
    raw_summary = await db.get_case_results_summary(eval_id)

    # Transform results to match frontend field expectations
    results = []
    for r in raw_results:
        r["prompt"] = r.get("prompt") or r.get("test_case_prompt") or ""
        r["model_name"] = r.get("model_display_name") or r.get("model_litellm_id") or r.get("model_id") or ""
        # Parse expected_tool if stored as JSON string
        if r.get("expected_tool"):
            r["expected_tool"] = _parse_expected_tool(r["expected_tool"])
        if r.get("expected_params") and isinstance(r["expected_params"], str):
            try:
                r["expected_params"] = json.loads(r["expected_params"])
            except (json.JSONDecodeError, TypeError):
                pass
        if r.get("actual_params") and isinstance(r["actual_params"], str):
            try:
                r["actual_params"] = json.loads(r["actual_params"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(r)
    run["results"] = results

    # Transform summary: array → dict keyed by model display name
    summary = {}
    for s in raw_summary:
        key = s.get("model_display_name") or s.get("model_litellm_id") or s.get("model_id") or "unknown"
        summary[key] = {
            "tool_selection_score": (s.get("tool_accuracy_pct") or 0) / 100,
            "param_accuracy_score": (s.get("param_accuracy_pct") or 0) / 100,
            "overall_score": (s.get("overall_score_pct") or 0) / 100,
            "total_cases": s.get("total_cases", 0),
            "cases_passed": s.get("cases_passed", 0),
            "avg_latency_ms": s.get("avg_latency_ms", 0),
            "irrelevance_accuracy_pct": s.get("irrelevance_accuracy_pct"),
            "category_breakdown": s.get("category_breakdown"),
        }
    run["summary"] = summary
    return run


@router.delete("/api/tool-eval/history/{eval_id}")
async def delete_tool_eval_run(eval_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete an eval run."""
    deleted = await db.delete_tool_eval_run(eval_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Eval run not found"}, status_code=404)
    return {"status": "ok"}


@router.post("/api/tool-eval")
async def run_tool_eval(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run tool calling eval via job registry. Returns job_id immediately."""
    body = await request.json()

    # Validate core fields via Pydantic
    try:
        validated = ToolEvalRequest(
            suite_id=body.get("suite_id", ""),
            models=body.get("models") or None,
            targets=body.get("targets") or None,
            temperature=body.get("temperature", 0.0),
            system_prompt=body.get("system_prompt"),
            experiment_id=body.get("experiment_id"),
            auto_judge=body.get("auto_judge", False),
            auto_judge_threshold=body.get("auto_judge_threshold"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    suite_id = validated.suite_id
    model_ids, target_set = _parse_target_selection(body)
    temperature = validated.temperature
    tool_choice = body.get("tool_choice", "required")
    provider_params = body.get("provider_params")
    system_prompt = body.get("system_prompt")
    judge_config = body.get("judge")

    # --- Additional validation beyond Pydantic ---
    if not isinstance(model_ids, list) or len(model_ids) == 0:
        return JSONResponse({"error": "models must be a non-empty list"}, status_code=400)
    if tool_choice not in ("auto", "required", "none"):
        return JSONResponse({"error": "tool_choice must be 'auto', 'required', or 'none'"}, status_code=400)

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
    targets = _filter_targets(all_targets, model_ids, target_set)
    if not targets:
        return JSONResponse({"error": "No matching models found in config"}, status_code=400)

    # Build progress detail
    model_count = len(targets)
    progress_detail = f"Tool Eval: {model_count} model{'s' if model_count != 1 else ''}, {suite['name']}"

    # Submit to job registry
    experiment_id = body.get("experiment_id")
    profiles = body.get("profiles")
    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "suite_id": suite_id,
        "models": model_ids,
        "target_set": [list(t) for t in target_set] if target_set else None,
        "temperature": temperature,
        "tool_choice": tool_choice,
        "provider_params": provider_params,
        "system_prompt": system_prompt,
        "judge": judge_config,
        "judge_concurrency": body.get("judge_concurrency", 4),
        "experiment_id": experiment_id,
        "profiles": profiles,
        "auto_judge": validated.auto_judge,
        "auto_judge_threshold": validated.auto_judge_threshold,
    }

    job_id = await job_registry.submit(
        job_type="tool_eval",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@router.post("/api/tool-eval/cancel")
async def cancel_tool_eval(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running tool eval via job registry."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = body.get("job_id")
    if job_id:
        cancelled = await job_registry.cancel(job_id, user["id"])
        if cancelled:
            return {"status": "ok", "message": "Cancellation requested"}
        return JSONResponse({"error": "Job not found or not cancellable"}, status_code=404)
    # Fallback: cancel via legacy user-level event (for backward compatibility)
    _get_user_cancel(user["id"]).set()
    return {"status": "ok", "message": "Cancellation requested"}

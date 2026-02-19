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
    score_tool_selection,
    score_params,
    compute_overall_score,
    score_multi_turn,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tool_eval"])

# Module-level ws_manager -- set by app.py after import
ws_manager = None


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
            result["actual_tool"] = message.tool_calls[0].function.name
            try:
                result["actual_params"] = json.loads(message.tool_calls[0].function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Failed to parse tool call arguments")
                result["actual_params"] = None
        else:
            result["actual_tool"] = None
            result["actual_params"] = None

        # Normalize: some local LLMs stuff a full JSON object into function.name
        _raw_tool = result.get("actual_tool")
        if _raw_tool and _raw_tool.strip().startswith("{"):
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
        return result

    # Score
    result["tool_selection_score"] = score_tool_selection(expected_tool, result["actual_tool"])
    result["param_accuracy"] = score_params(expected_params, result["actual_params"], scoring_config=scoring_config)
    result["overall_score"] = compute_overall_score(result["tool_selection_score"], result["param_accuracy"])

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

    except Exception as e:
        result["success"] = False
        result["error"] = sanitize_error(str(e)[:200], target.api_key)
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
            tools_json=body.get("tools", []),
            system_prompt=body.get("system_prompt"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    name = validated.name.strip()
    description = validated.description or ""
    tools = validated.tools_json
    if tools:
        err = _validate_tools(tools)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    suite_id = await db.create_tool_suite(user["id"], name, description, json.dumps(tools))
    return {"status": "ok", "suite_id": suite_id}


@router.post("/api/tool-eval/import")
async def import_tool_suite(request: Request, user: dict = Depends(auth.get_current_user)):
    """Import a complete tool suite (tools + test cases) from JSON."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    description = body.get("description", "")
    tools = body.get("tools", [])
    if tools:
        err = _validate_tools(tools)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    suite_id = await db.create_tool_suite(user["id"], name, description, json.dumps(tools))
    test_cases = body.get("test_cases", [])
    created = 0
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
            if not mt_obj.get("multi_turn"):
                mt_obj["multi_turn"] = True
            mt_config = json.dumps(mt_obj)
        sc_json = json.dumps(item["scoring_config"]) if item.get("scoring_config") else None
        await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config=mt_config, scoring_config_json=sc_json)
        created += 1
    return {"status": "ok", "suite_id": suite_id, "test_cases_created": created}


@router.get("/api/tool-suites/{suite_id}")
async def get_tool_suite(suite_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full suite with tools and test cases."""
    suite = await db.get_tool_suite(suite_id, user["id"])
    if not suite:
        return JSONResponse({"error": "Suite not found"}, status_code=404)
    suite["tools"] = json.loads(suite["tools_json"])
    del suite["tools_json"]
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
            tools_json=body.get("tools"),
            system_prompt=body.get("system_prompt"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    name = validated.name
    description = validated.description
    tools = validated.tools_json
    tools_json = None
    if tools is not None:
        if tools:
            err = _validate_tools(tools)
            if err:
                return JSONResponse({"error": err}, status_code=400)
        tools_json = json.dumps(tools)
    updated = await db.update_tool_suite(suite_id, user["id"], name=name, description=description, tools_json=tools_json)
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
    tools = json.loads(suite["tools_json"]) if suite.get("tools_json") else []
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
            await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config=mt_config, scoring_config_json=sc_json)
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
    case_id = await db.create_test_case(suite_id, prompt, expected_tool, expected_params, param_scoring, multi_turn_config=mt_config, scoring_config_json=sc_json)
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

    updated = await db.update_test_case(case_id, suite_id, prompt=prompt, expected_tool=expected_tool, expected_params=expected_params, param_scoring=param_scoring, multi_turn_config=mt_config, scoring_config_json=sc_json)
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
    for run in runs:
        if isinstance(run.get("models_json"), str):
            run["models"] = json.loads(run["models_json"])
            del run["models_json"]
        if isinstance(run.get("summary_json"), str):
            run["summary"] = json.loads(run["summary_json"])
            del run["summary_json"]
        # Parse config_json for frontend (M1)
        if isinstance(run.get("config_json"), str):
            try:
                run["config"] = json.loads(run["config_json"])
            except (json.JSONDecodeError, TypeError):
                run["config"] = None
            del run["config_json"]
    return {"runs": runs}


@router.get("/api/tool-eval/history/{eval_id}")
async def get_tool_eval_run(eval_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full eval run details."""
    run = await db.get_tool_eval_run(eval_id, user["id"])
    if not run:
        return JSONResponse({"error": "Eval run not found"}, status_code=404)
    if isinstance(run.get("models_json"), str):
        run["models"] = json.loads(run["models_json"])
        del run["models_json"]
    if isinstance(run.get("results_json"), str):
        run["results"] = json.loads(run["results_json"])
        del run["results_json"]
    if isinstance(run.get("summary_json"), str):
        run["summary"] = json.loads(run["summary_json"])
        del run["summary_json"]
    # Parse config_json for frontend (M1)
    if isinstance(run.get("config_json"), str):
        try:
            run["config"] = json.loads(run["config_json"])
        except (json.JSONDecodeError, TypeError):
            run["config"] = None
        del run["config_json"]
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

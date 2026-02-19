"""LLM Judge routes and logic (AI-Powered Eval Quality Assessment)."""

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
from schemas import JudgeRequest, JudgeCompareRequest
from job_registry import registry as job_registry
from provider_params import build_litellm_kwargs
from routers.helpers import (
    _get_user_config,
    _find_target,
    _check_rate_limit,
    _get_user_cancel,
    _parse_judge_json,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["judge"])

# Module-level ws_manager -- set by app.py after import
ws_manager = None


# ---------------------------------------------------------------------------
# Judge prompt templates
# ---------------------------------------------------------------------------

_JUDGE_VERDICT_PROMPT = """You are an expert evaluator of LLM tool calling quality. You are judging how well a model performed on a tool calling task.
{custom_instructions}
TOOL DEFINITIONS:
{tool_definitions}

TEST CASE:
- User prompt: "{test_prompt}"
- Expected tool: {expected_tool}
- Expected parameters: {expected_params}

MODEL RESPONSE:
- Tool called: {actual_tool}
- Parameters used: {actual_params}
- Automated score: {overall_score}

EVALUATE:
1. Tool Selection: Was the right tool chosen? If different from expected, was it still reasonable?
2. Parameter Accuracy: Were parameters correct? Close but not exact? Missing important ones?
3. Reasoning Quality: Does the tool call show understanding of the user's intent?
4. Edge Cases: Did the model handle ambiguity well?

Return ONLY valid JSON (no text before/after):
{{"quality_score": 1, "verdict": "pass", "summary": "One-line summary max 100 chars", "reasoning": "Detailed 2-3 sentence explanation", "tool_selection_assessment": "correct", "param_assessment": "exact"}}

quality_score: integer 1-5
verdict: "pass" or "marginal" or "fail"
tool_selection_assessment: "correct" or "acceptable_alternative" or "wrong"
param_assessment: "exact" or "close" or "partial" or "wrong"
"""

_JUDGE_COMPARE_PROMPT = """You are an expert judge comparing two LLMs' tool calling performance.

TOOL DEFINITIONS:
{tool_definitions}

TEST CASE {case_num}/{total_cases}:
- User prompt: "{test_prompt}"
- Expected: {expected_tool}({expected_params})

Model A ({model_a_name}):
- Called: {a_tool}({a_params})
- Automated score: {a_score}

Model B ({model_b_name}):
- Called: {b_tool}({b_params})
- Automated score: {b_score}

Return ONLY valid JSON (no text before/after):
{{"winner": "model_a", "confidence": 0.85, "reasoning": "Why this model won on this case"}}

winner: "model_a" or "model_b" or "tie"
confidence: float 0.0-1.0"""

_JUDGE_CROSSCASE_PROMPT = """You have evaluated {n} test cases for model {model_name}. Here are the per-case verdicts:

{verdicts_summary}

Provide a cross-case analysis:
1. What patterns of strength/weakness do you see?
2. What types of tool calls does this model handle well/poorly?
3. Overall grade (A/B/C/D/F with +/-) and what it means
4. Specific recommendations for improvement

Return ONLY valid JSON (no text before/after):
{{"overall_grade": "B+", "overall_score": 82, "strengths": ["strength1", "strength2"], "weaknesses": ["weakness1"], "cross_case_analysis": "Paragraph of analysis", "recommendations": ["recommendation1"]}}

overall_score: integer 0-100
overall_grade: letter grade with optional +/-"""

_JUDGE_COMPARE_SUMMARY_PROMPT = """You compared Model A ({model_a_name}) and Model B ({model_b_name}) across {n} test cases.

Per-case results:
{case_results}

Provide an overall comparison summary.

Return ONLY valid JSON (no text before/after):
{{"overall_winner": "model_a", "score_a": 78, "score_b": 65, "summary": "2-3 sentence summary of the comparison", "tie_cases": 1}}

overall_winner: "model_a" or "model_b" or "tie"
score_a, score_b: integer 0-100"""


# ---------------------------------------------------------------------------
# Judge core functions
# ---------------------------------------------------------------------------

_JUDGE_RETRYABLE_ERRORS = (
    litellm.exceptions.BadGatewayError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.Timeout,
)

async def _call_judge_model(
    judge_target: Target,
    prompt: str,
    *,
    _max_retries: int = 3,
    _base_delay: float = 2.0,
) -> dict:
    """Call the judge model with a prompt, return parsed JSON dict.

    Retries transient errors (502/503/500/connection/timeout) with exponential
    backoff.  Non-transient errors (auth, 400, 404, rate-limit) propagate
    immediately.
    """
    kwargs = {
        "model": judge_target.model_id,
        "messages": [{"role": "user", "content": prompt}],
        "timeout": 120,
        "num_retries": 0,  # We handle retries ourselves with backoff
    }
    if judge_target.api_base:
        kwargs["api_base"] = judge_target.api_base
    if judge_target.api_key:
        kwargs["api_key"] = judge_target.api_key
    # Use build_litellm_kwargs for provider-aware param handling (skip_params, clamping)
    extra = build_litellm_kwargs(
        judge_target, temperature=0.0, max_tokens=2048,
    )
    if extra:
        kwargs.update(extra)
    else:
        # Fallback: no provider_params resolved -- apply judge defaults directly
        if "temperature" not in (judge_target.skip_params or []):
            kwargs["temperature"] = 0.0  # AD-6: reproducible judge assessments
        kwargs["max_tokens"] = 2048

    logger.debug("Judge call: model=%s api_base=%s prompt_len=%d", judge_target.model_id, judge_target.api_base, len(prompt))

    last_exc: Exception | None = None
    for attempt in range(1, _max_retries + 1):
        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content or ""
            return _parse_judge_json(content)
        except _JUDGE_RETRYABLE_ERRORS as exc:
            last_exc = exc
            if attempt < _max_retries:
                delay = _base_delay * (2 ** (attempt - 1))  # 2s, 4s, 8s
                logger.info(
                    "Judge call transient error (attempt %d/%d): %s -- retrying in %.1fs",
                    attempt, _max_retries, exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "Judge call failed after %d attempts: %s",
                    _max_retries, exc,
                )
    raise last_exc  # type: ignore[misc]


async def _judge_single_verdict(
    judge_target: Target,
    tool_defs_text: str,
    test_case: dict,
    result: dict,
    custom_instructions: str = "",
) -> dict:
    """Judge a single test case result. Returns verdict dict."""
    ci_block = f"\nADDITIONAL EVALUATION INSTRUCTIONS:\n{custom_instructions}\n" if custom_instructions.strip() else ""
    prompt = _JUDGE_VERDICT_PROMPT.format(
        tool_definitions=tool_defs_text,
        test_prompt=result.get("prompt", test_case.get("prompt", "")),
        expected_tool=result.get("expected_tool", "?"),
        expected_params=json.dumps(result.get("expected_params", {})),
        actual_tool=result.get("actual_tool", "none"),
        actual_params=json.dumps(result.get("actual_params", {})),
        overall_score=result.get("overall_score", 0),
        custom_instructions=ci_block,
    )
    verdict = await _call_judge_model(judge_target, prompt)
    if not verdict:
        return {
            "quality_score": 0,
            "verdict": "error",
            "summary": "Judge model returned invalid response",
            "reasoning": "Could not parse judge response",
            "tool_selection_assessment": "unknown",
            "param_assessment": "unknown",
        }
    # Ensure required keys
    verdict.setdefault("quality_score", 0)
    verdict.setdefault("verdict", "fail")
    verdict.setdefault("summary", "")
    verdict.setdefault("reasoning", "")
    verdict.setdefault("tool_selection_assessment", "unknown")
    verdict.setdefault("param_assessment", "unknown")
    return verdict


async def _judge_crosscase(
    judge_target: Target,
    model_name: str,
    verdicts: list[dict],
) -> dict:
    """Generate cross-case analysis report from verdicts."""
    summary_parts = []
    for v in verdicts:
        tc_id = v.get("test_case_id", "?")
        verdict = v.get("verdict", "?")
        score = v.get("quality_score", 0)
        summary = v.get("summary", "")
        summary_parts.append(f"- Case {tc_id}: {verdict} (score {score}/5) - {summary}")

    prompt = _JUDGE_CROSSCASE_PROMPT.format(
        n=len(verdicts),
        model_name=model_name,
        verdicts_summary="\n".join(summary_parts),
    )
    report = await _call_judge_model(judge_target, prompt)
    if not report:
        return {
            "overall_grade": "?",
            "overall_score": 0,
            "strengths": [],
            "weaknesses": [],
            "cross_case_analysis": "Judge model could not generate analysis",
            "recommendations": [],
        }
    report.setdefault("overall_grade", "?")
    report.setdefault("overall_score", 0)
    report.setdefault("strengths", [])
    report.setdefault("weaknesses", [])
    report.setdefault("cross_case_analysis", "")
    report.setdefault("recommendations", [])
    return report


# ---------------------------------------------------------------------------
# Judge REST endpoints
# ---------------------------------------------------------------------------


@router.post("/api/tool-eval/judge")
async def run_judge_post_eval(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run post-eval judge via job registry. Returns job_id immediately.

    Progress is delivered via WebSocket (judge_verdict, judge_report, judge_complete events).
    """
    body = await request.json()

    # Validate core fields via Pydantic
    try:
        validated = JudgeRequest(
            eval_run_id=body.get("eval_run_id", ""),
            judge_model=body.get("judge_model", ""),
            mode=body.get("mode", "post_eval"),
            experiment_id=body.get("experiment_id"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    eval_run_id = validated.eval_run_id
    judge_model_id = validated.judge_model
    judge_provider_key = body.get("judge_provider_key")
    custom_instructions = body.get("custom_instructions", "")
    concurrency = body.get("concurrency", 4)

    # Load eval run (validate before submitting job)
    eval_run = await db.get_tool_eval_run(eval_run_id, user["id"])
    if not eval_run:
        return JSONResponse({"error": "Eval run not found"}, status_code=404)

    results = json.loads(eval_run.get("results_json", "[]"))
    if not results:
        return JSONResponse({"error": "Eval run has no results"}, status_code=400)

    # Rate limit (raises HTTPException 429 if exceeded)
    await _check_rate_limit(user["id"])

    # Validate judge model exists
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)
    judge_targets = _find_target(all_targets, judge_model_id, judge_provider_key)
    if not judge_targets:
        return JSONResponse({"error": f"Judge model '{judge_model_id}' not found in config"}, status_code=400)

    progress_detail = f"Judge: {len(results)} verdicts, {judge_targets[0].display_name}"

    experiment_id = body.get("experiment_id")
    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "eval_run_id": eval_run_id,
        "judge_model": judge_model_id,
        "judge_provider_key": judge_provider_key,
        "custom_instructions": custom_instructions,
        "concurrency": concurrency,
        "experiment_id": experiment_id,
    }

    job_id = await job_registry.submit(
        job_type="judge",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@router.post("/api/tool-eval/judge/compare")
async def run_judge_compare(request: Request, user: dict = Depends(auth.get_current_user)):
    """Run comparative judge via job registry. Returns job_id immediately.

    Progress is delivered via WebSocket (compare_case, compare_complete events).
    """
    body = await request.json()

    # Validate core fields via Pydantic
    try:
        validated = JudgeCompareRequest(
            eval_run_id_a=body.get("eval_run_id_a", ""),
            eval_run_id_b=body.get("eval_run_id_b", ""),
            judge_model=body.get("judge_model", ""),
            experiment_id=body.get("experiment_id"),
        )
    except (ValidationError, Exception) as e:
        raise HTTPException(422, detail=str(e))

    eval_run_id_a = validated.eval_run_id_a
    eval_run_id_b = validated.eval_run_id_b
    judge_model_id = validated.judge_model
    judge_provider_key = body.get("judge_provider_key")
    concurrency = body.get("concurrency", 4)

    # Load both runs (validate before submitting job)
    run_a = await db.get_tool_eval_run(eval_run_id_a, user["id"])
    run_b = await db.get_tool_eval_run(eval_run_id_b, user["id"])
    if not run_a:
        return JSONResponse({"error": "Eval run A not found"}, status_code=404)
    if not run_b:
        return JSONResponse({"error": "Eval run B not found"}, status_code=404)

    results_a = json.loads(run_a.get("results_json", "[]"))
    results_b = json.loads(run_b.get("results_json", "[]"))
    if not results_a or not results_b:
        return JSONResponse({"error": "Both eval runs must have results"}, status_code=400)

    # Index and check for common test cases
    a_by_tc = {r["test_case_id"]: r for r in results_a if "test_case_id" in r}
    b_by_tc = {r["test_case_id"]: r for r in results_b if "test_case_id" in r}
    common_tcs = sorted(set(a_by_tc.keys()) & set(b_by_tc.keys()))
    if not common_tcs:
        return JSONResponse({"error": "No common test cases between the two runs"}, status_code=400)

    # Rate limit (raises HTTPException 429 if exceeded)
    await _check_rate_limit(user["id"])

    # Validate judge model exists
    config = await _get_user_config(user["id"])
    all_targets = build_targets(config)
    judge_targets = _find_target(all_targets, judge_model_id, judge_provider_key)
    if not judge_targets:
        return JSONResponse({"error": f"Judge model '{judge_model_id}' not found in config"}, status_code=400)

    progress_detail = f"Compare: {len(common_tcs)} cases, {judge_targets[0].display_name}"

    experiment_id = body.get("experiment_id")
    job_params = {
        "user_id": user["id"],
        "user_email": user.get("email", ""),
        "eval_run_id_a": eval_run_id_a,
        "eval_run_id_b": eval_run_id_b,
        "judge_model": judge_model_id,
        "judge_provider_key": judge_provider_key,
        "concurrency": concurrency,
        "experiment_id": experiment_id,
    }

    job_id = await job_registry.submit(
        job_type="judge_compare",
        user_id=user["id"],
        params=job_params,
        progress_detail=progress_detail,
    )

    return {"job_id": job_id, "status": "submitted"}


@router.post("/api/tool-eval/judge/cancel")
async def cancel_judge(request: Request, user: dict = Depends(auth.get_current_user)):
    """Cancel a running judge operation via job registry."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = body.get("job_id")
    if job_id:
        cancelled = await job_registry.cancel(job_id, user["id"])
        if cancelled:
            return {"status": "ok", "message": "Cancellation requested"}
        return JSONResponse({"error": "Job not found or not cancellable"}, status_code=404)
    # Fallback: cancel via legacy user-level event
    _get_user_cancel(user["id"]).set()
    return {"status": "ok", "message": "Cancellation requested"}


@router.get("/api/tool-eval/judge/reports")
async def list_judge_reports(user: dict = Depends(auth.get_current_user)):
    """List judge reports for the current user."""
    reports = await db.get_judge_reports(user["id"])
    return {"reports": reports}


@router.get("/api/tool-eval/judge/reports/{report_id}")
async def get_judge_report(report_id: str, user: dict = Depends(auth.get_current_user)):
    """Get full judge report detail."""
    report = await db.get_judge_report(report_id, user["id"])
    if not report:
        return JSONResponse({"error": "Judge report not found"}, status_code=404)
    return report


@router.delete("/api/tool-eval/judge/reports/{report_id}")
async def delete_judge_report(report_id: str, user: dict = Depends(auth.get_current_user)):
    """Delete a judge report."""
    deleted = await db.delete_judge_report(report_id, user["id"])
    if not deleted:
        return JSONResponse({"error": "Judge report not found"}, status_code=404)
    return {"status": "ok"}

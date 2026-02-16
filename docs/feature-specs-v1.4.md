# LLM Benchmark Studio -- Feature Specifications v1.4

**Date:** 2026-02-16
**Status:** Complete
**Target Version:** v1.4.0
**Current Version:** v1.3.0 (Multi-Turn Tool Eval)

---

## Executive Summary

This document specifies four new features for LLM Benchmark Studio that extend the Tool Eval system with automated optimization, AI-powered quality assessment, and intelligent per-provider parameter handling. All four features are designed for parallel development on separate `feat/` branches using git worktrees.

### The Four Features

| # | Feature | Purpose | Effort Est. |
|---|---------|---------|-------------|
| 1 | **Parameter Tuner** | GridSearchCV-style deterministic sweep of parameter combinations for optimal tool calling config | 22.5h |
| 2 | **Prompt Tuner** | AI-generated system prompt variations with Quick and Evolutionary (genetic algorithm) modes | 30h |
| 3 | **LLM Judge** | AI evaluator layer with post-eval reports, live inline scoring, and comparative model judging | 34.5h |
| 4 | **Provider Parameters** | Per-provider parameter UI with 3-tier architecture, clamping, conflict resolution, JSON passthrough | 35h |

**Total estimated effort:** ~122 hours across backend + frontend + QA

---

## Individual Spec Documents

Each feature has a complete specification in `.scratchpad/handoffs/`:

1. **Parameter Tuner:** `architect-parameter-tuner-spec-SUCCESS.md`
2. **Prompt Tuner:** `architect-prompt-tuner-spec-SUCCESS.md`
3. **LLM Judge:** `architect-llm-judge-spec-SUCCESS.md`
4. **Provider Parameters:** `architect-provider-params-spec-SUCCESS.md`

Supporting research:
- **LiteLLM Research:** `research-litellm-findings-SUCCESS.md`
- **Codebase Patterns:** `librarian-codebase-patterns-SUCCESS.md`

Each spec contains: Feature Description, User Stories, Acceptance Criteria, DoR, DoD, API Design, Database Schema, UI Wireframe Description, Task Breakdown, and Test Plan.

---

## Cross-Feature Dependencies

### Shared Infrastructure

All four features share common infrastructure that should be built once:

1. **User Lock (`_get_user_lock`):** Parameter Tuner, Prompt Tuner, and LLM Judge all acquire the existing per-user lock. Only one eval-family operation runs per user at a time.

2. **SSE Streaming Pattern:** All features use the same `POST + ReadableStream` SSE pattern as existing tool eval (not EventSource). Each feature adds new SSE event types.

3. **Cancellation Pattern:** All features reuse `_get_user_cancel()` / `asyncio.Event`. Cancel endpoints follow the same pattern.

4. **DB Patterns:** All new tables follow: UUID hex PKs, user_id FK with CASCADE, datetime defaults, JSON columns for structured data, indexes on (user_id, timestamp DESC).

5. **Frontend Patterns:** All features follow `te` naming convention (prefix per feature: `pt` for Parameter Tuner, `prt` for Prompt Tuner, `jg` for Judge, `pp` for Provider Params). Sub-views within the Tool Eval tab using `showToolEvalView()` pattern.

### Dependency Graph

```
Provider Parameters ──────────────────────────────────────┐
                                                          │
Parameter Tuner ── uses ─→ run_single_eval() ←── uses ── │
                                                          │
Prompt Tuner ───── uses ─→ run_single_eval() ←── uses ── │
                   uses ─→ litellm.acompletion() (meta)   │
                                                          │
LLM Judge ──────── uses ─→ litellm.acompletion() (judge)  │
                   reads → tool_eval_runs (existing)       │
```

**Key dependencies:**
- **Provider Parameters affects all other features.** When Provider Params is complete, Parameter Tuner and Prompt Tuner should respect provider param ranges in their search spaces.
- **Parameter Tuner is self-contained.** Wraps `run_single_eval()` with parameter sweeps. No dependency on other new features.
- **Prompt Tuner is self-contained.** Similar to Parameter Tuner but sweeps prompts instead of params.
- **LLM Judge integrates with existing eval runs.** Post-eval and comparative modes read from `tool_eval_runs`. Live inline mode integrates into the eval SSE stream.

### Recommended implementation order:
1. **Provider Parameters** (foundation -- affects parameter handling for everything)
2. **Parameter Tuner** (simplest new feature, establishes tuning patterns)
3. **Prompt Tuner** (builds on tuning patterns, adds AI generation)
4. **LLM Judge** (most complex integration, touches eval SSE stream)

However, since all features go on separate branches, they CAN be developed in parallel. Provider Parameters is the only one that affects how the others handle parameters.

---

## New Database Tables Summary

| Table | Feature | Key Columns |
|-------|---------|-------------|
| `param_tune_runs` | Parameter Tuner | user_id, suite_id, search_space_json, results_json, best_config_json, status |
| `prompt_tune_runs` | Prompt Tuner | user_id, suite_id, mode, generations_json, best_prompt, best_score, status |
| `judge_reports` | LLM Judge | user_id, eval_run_id, judge_model, mode, verdicts_json, report_json, overall_grade |

Provider Parameters adds NO new tables (uses existing `user_configs` + static Python module).

---

## New API Endpoints Summary

### Parameter Tuner
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/tool-eval/param-tune` | Start tuning run (SSE) |
| POST | `/api/tool-eval/param-tune/cancel` | Cancel run |
| GET | `/api/tool-eval/param-tune/history` | List runs |
| GET | `/api/tool-eval/param-tune/history/{id}` | Get full run |
| DELETE | `/api/tool-eval/param-tune/history/{id}` | Delete run |

### Prompt Tuner
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/tool-eval/prompt-tune` | Start tuning run (SSE) |
| POST | `/api/tool-eval/prompt-tune/cancel` | Cancel run |
| GET | `/api/tool-eval/prompt-tune/estimate` | Get cost estimate |
| GET | `/api/tool-eval/prompt-tune/history` | List runs |
| GET | `/api/tool-eval/prompt-tune/history/{id}` | Get full run |
| DELETE | `/api/tool-eval/prompt-tune/history/{id}` | Delete run |

### LLM Judge
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/tool-eval/judge` | Post-eval judge (SSE) |
| POST | `/api/tool-eval/judge/compare` | Comparative judge (SSE) |
| GET | `/api/tool-eval/judge/reports` | List reports |
| GET | `/api/tool-eval/judge/reports/{id}` | Get full report |
| DELETE | `/api/tool-eval/judge/reports/{id}` | Delete report |

### Provider Parameters
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/provider-params/registry` | Get full param registry |
| POST | `/api/provider-params/validate` | Validate params for provider |

---

## New SSE Event Types Summary

### Parameter Tuner
`tune_start`, `combo_start`, `combo_result`, `tune_progress`, `tune_complete`

### Prompt Tuner
`tune_start`, `generation_start`, `prompt_generated`, `prompt_eval_start`, `prompt_eval_result`, `generation_complete`, `tune_complete`

### LLM Judge
`judge_start`, `judge_verdict`, `judge_report`, `judge_complete`, `compare_start`, `compare_case`, `compare_complete`

---

## New Files

| File | Purpose |
|------|---------|
| `provider_params.py` | Provider parameter registry, validation, clamping, conflict resolution |

All other code goes into existing files (`app.py`, `db.py`, `index.html`).

---

## Test Strategy

- **Test Provider:** Zai (GLM-4.7, GLM-5, GLM-4.5-Air) -- free tokens
- **Environment:** Local Docker
- **Quality Gate:** All features must pass browser testing with screen captures before going to staging
- **Bundle Release:** Ships as v1.4.0 when all features pass QA

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| AI-generated prompts may be low quality (Prompt Tuner) | Test meta-prompts manually before integration. Include retry + fallback parsing. |
| Judge model self-evaluation bias | Warn when judge model = target model. Default to using a different model. |
| Provider parameter registry becomes stale | Tie registry updates to LiteLLM version bumps. Add version field to registry. |
| Large parameter sweeps overwhelm rate limits | Grid preview warns at >50 combos. Each combo counts as 1 rate-limit event. |
| SSE event types proliferate | Prefix all new events per feature. Document event schemas. |

---

## Open Questions

1. **Should Parameter Tuner results feed into Provider Parameters defaults?** e.g., "Apply best config" sets per-provider defaults automatically.
2. **Should Prompt Tuner support multi-model evolution?** Currently each target model is scored independently. Could have "best prompt across all models."
3. **Should Judge reports be shareable?** (Ties into future sharing/public suites roadmap item)
4. **Should we add a "Run All" mode?** Parameter Tuner + Prompt Tuner + Judge in one pipeline.

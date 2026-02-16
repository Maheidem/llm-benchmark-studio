# PHASE 10 PLANNING -- LLM Benchmark Studio v1.4.0

**Last Updated:** 2026-02-16
**Current Version:** v1.3.0 (Multi-Turn Tool Eval)
**Target Version:** v1.4.0
**Status:** IMPLEMENTATION COMPLETE — READY FOR STAGING

---

## STATUS TRACKER

| # | Feature | Spec | Backend | Frontend | QA | Status |
|---|---------|------|---------|----------|----|--------|
| 1 | Provider Parameters | DONE | DONE | DONE | DONE | COMPLETE |
| 2 | Parameter Tuner | DONE | DONE | DONE | DONE | COMPLETE |
| 3 | Prompt Tuner | DONE | DONE | DONE | DONE | COMPLETE |
| 4 | LLM Judge | DONE | DONE | DONE | DONE | COMPLETE |

**Total Estimated Effort:** ~122 hours across backend + frontend + QA

**Implementation Order (recommended):**
1. Provider Parameters (foundation -- affects parameter handling for everything)
2. Parameter Tuner (simplest new feature, establishes tuning patterns)
3. Prompt Tuner (builds on tuning patterns, adds AI generation)
4. LLM Judge (most complex integration, touches eval SSE stream)

All features go on separate `feat/` branches using git worktrees. They CAN be developed in parallel.

---

## EXECUTIVE SUMMARY

Four new features extend the Tool Eval system with automated optimization, AI-powered quality assessment, and intelligent per-provider parameter handling:

| # | Feature | Purpose | Effort |
|---|---------|---------|--------|
| 1 | **Provider Parameters** | Per-provider parameter UI with 3-tier architecture, clamping, conflict resolution, JSON passthrough | 35h |
| 2 | **Parameter Tuner** | GridSearchCV-style deterministic sweep of parameter combinations for optimal tool calling config | 22.5h |
| 3 | **Prompt Tuner** | AI-generated system prompt variations with Quick and Evolutionary (genetic algorithm) modes | 30h |
| 4 | **LLM Judge** | AI evaluator layer with post-eval reports, live inline scoring, and comparative model judging | 34.5h |

---

## CROSS-FEATURE DEPENDENCIES

### Shared Infrastructure

1. **User Lock (`_get_user_lock`):** Parameter Tuner, Prompt Tuner, and LLM Judge all acquire the existing per-user lock. Only one eval-family operation runs per user at a time.
2. **SSE Streaming Pattern:** All features use the same `POST + ReadableStream` SSE pattern as existing tool eval (not EventSource). Each feature adds new SSE event types.
3. **Cancellation Pattern:** All features reuse `_get_user_cancel()` / `asyncio.Event`. Cancel endpoints follow the same pattern.
4. **DB Patterns:** All new tables follow: UUID hex PKs, user_id FK with CASCADE, datetime defaults, JSON columns for structured data, indexes on (user_id, timestamp DESC).
5. **Frontend Patterns:** All features follow `te` naming convention (prefix per feature: `pt` for Parameter Tuner, `prt` for Prompt Tuner, `jg` for Judge, `pp` for Provider Params). Sub-views within the Tool Eval tab using `showToolEvalView()` pattern.

### Dependency Graph

```
Provider Parameters -----------------------------------------------+
                                                                   |
Parameter Tuner -- uses --> run_single_eval() <-- uses ----------- |
                                                                   |
Prompt Tuner ----- uses --> run_single_eval() <-- uses ----------- |
                   uses --> litellm.acompletion() (meta)           |
                                                                   |
LLM Judge -------- uses --> litellm.acompletion() (judge)          |
                   reads -> tool_eval_runs (existing)              |
```

- **Provider Parameters affects all other features.** When complete, Parameter Tuner and Prompt Tuner should respect provider param ranges in their search spaces.
- **Parameter Tuner is self-contained.** Wraps `run_single_eval()` with parameter sweeps.
- **Prompt Tuner is self-contained.** Similar to Parameter Tuner but sweeps prompts instead of params.
- **LLM Judge integrates with existing eval runs.** Post-eval and comparative modes read from `tool_eval_runs`. Live inline mode integrates into the eval SSE stream.

---

## NEW DATABASE TABLES

| Table | Feature | Key Columns |
|-------|---------|-------------|
| `param_tune_runs` | Parameter Tuner | user_id, suite_id, search_space_json, results_json, best_config_json, status |
| `prompt_tune_runs` | Prompt Tuner | user_id, suite_id, mode, generations_json, best_prompt, best_score, status |
| `judge_reports` | LLM Judge | user_id, eval_run_id, judge_model, mode, verdicts_json, report_json, overall_grade |

Provider Parameters adds NO new tables (uses existing `user_configs` + static Python module).

---

## NEW API ENDPOINTS (14 total)

### Parameter Tuner (5)
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/tool-eval/param-tune` | Start tuning run (SSE) |
| POST | `/api/tool-eval/param-tune/cancel` | Cancel run |
| GET | `/api/tool-eval/param-tune/history` | List runs |
| GET | `/api/tool-eval/param-tune/history/{id}` | Get full run |
| DELETE | `/api/tool-eval/param-tune/history/{id}` | Delete run |

### Prompt Tuner (6)
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/tool-eval/prompt-tune` | Start tuning run (SSE) |
| POST | `/api/tool-eval/prompt-tune/cancel` | Cancel run |
| GET | `/api/tool-eval/prompt-tune/estimate` | Get cost estimate |
| GET | `/api/tool-eval/prompt-tune/history` | List runs |
| GET | `/api/tool-eval/prompt-tune/history/{id}` | Get full run |
| DELETE | `/api/tool-eval/prompt-tune/history/{id}` | Delete run |

### LLM Judge (5)
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/tool-eval/judge` | Post-eval judge (SSE) |
| POST | `/api/tool-eval/judge/compare` | Comparative judge (SSE) |
| GET | `/api/tool-eval/judge/reports` | List reports |
| GET | `/api/tool-eval/judge/reports/{id}` | Get full report |
| DELETE | `/api/tool-eval/judge/reports/{id}` | Delete report |

### Provider Parameters (2)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/provider-params/registry` | Get full param registry |
| POST | `/api/provider-params/validate` | Validate params for provider |

---

## NEW SSE EVENT TYPES (18 total)

### Parameter Tuner
`tune_start`, `combo_start`, `combo_result`, `tune_progress`, `tune_complete`

### Prompt Tuner
`tune_start`, `generation_start`, `prompt_generated`, `prompt_eval_start`, `prompt_eval_result`, `generation_complete`, `tune_complete`

### LLM Judge
`judge_start`, `judge_verdict`, `judge_report`, `judge_complete`, `compare_start`, `compare_case`, `compare_complete`

---

## NEW FILES

| File | Purpose |
|------|---------|
| `provider_params.py` | Provider parameter registry, validation, clamping, conflict resolution |

All other code goes into existing files (`app.py`, `db.py`, `index.html`).

---

# FEATURE 1: PROVIDER PARAMETERS

**Effort:** 35h (17 tasks) | **Branch:** `feat/provider-params` | **Priority:** FIRST

## Description

Replaces the current one-size-fits-all parameter UI with intelligent, per-provider parameter configuration. Based on LiteLLM research, different providers have different parameter ranges, required fields, and capabilities.

**Three-Tier Architecture:**

| Tier | Description | UI Treatment |
|------|-------------|-------------|
| **Tier 1 (Universal)** | temperature, max_tokens, stop -- supported by ALL providers | Always visible, range-clamped per provider |
| **Tier 2 (Common)** | top_p, top_k, frequency_penalty, presence_penalty, seed, reasoning_effort | Collapsible "Advanced" section, greyed out when unsupported |
| **Tier 3 (Provider-Specific)** | mirostat, min_p, repetition_penalty, safe_prompt, etc. | JSON passthrough editor (escape hatch) |

**Key Behaviors:**
- Parameter ranges auto-adjust per provider (e.g., Anthropic temp 0-1, OpenAI temp 0-2)
- Unsupported parameters visually disabled with tooltip
- Parameter conflicts auto-resolved with visual indicators
- Unknown/unrecognized models get OpenAI-compatible fallback with warning badge
- JSON passthrough editor for any parameter LiteLLM supports

## User Stories

| ID | Story | Priority |
|----|-------|----------|
| PP-1 | See provider-specific parameter controls when configuring benchmarks or tool evals | Must |
| PP-2 | Sliders with correct ranges per provider (e.g., Anthropic temp 0-1) | Must |
| PP-3 | Unsupported parameters greyed out with tooltip explaining why | Must |
| PP-4 | Parameter conflicts auto-resolved with visual indicators | Must |
| PP-5 | JSON passthrough editor for provider-specific edge-case parameters | Must |
| PP-6 | Warning badges when a model is unrecognized by the parameter system | Should |
| PP-7 | See which parameters are Tier 1, Tier 2, or Tier 3 | Should |
| PP-8 | Parameter validation before running benchmarks | Must |
| PP-9 | System detects provider capabilities (tool calling, JSON mode, etc.) | Nice |
| PP-10 | Per-provider parameter preferences saved and remembered | Should |

## Acceptance Criteria

1. **Range Clamping:** Temperature slider for Anthropic maxes at 1.0, OpenAI at 2.0, Mistral at 1.5. Clamping client-side + server-side.
2. **Conflict Resolution:** Anthropic temp + top_p -> top_p auto-disabled. GPT-5 -> temp locked to 1.0.
3. **Unsupported Params:** top_k disabled for OpenAI/DeepSeek/xAI. Penalties disabled for Anthropic. Greyed slider + "(not supported)" + tooltip.
4. **JSON Passthrough:** Textarea, valid JSON, merged into LiteLLM kwargs, validated on submit.
5. **Unknown Models:** Yellow warning badge with fallback defaults.
6. **Persistence:** Saved in existing `user_configs` table.
7. **No Breaking Changes:** Existing benchmarks/evals unchanged without provider params.

## Definition of Ready (DoR)

- [ ] Feature spec reviewed and approved
- [ ] LiteLLM research reviewed (3-tier architecture, conflict matrix, clamping rules)
- [ ] Provider parameter registry defined (all providers, all params, all ranges)
- [ ] API endpoints defined with request/response schemas
- [ ] UI wireframe description approved
- [ ] Current config.yaml structure understood

## Definition of Done (DoD)

- [ ] Provider parameter registry implemented (server-side)
- [ ] Per-provider parameter UI with correct ranges, disabled states, conflict indicators
- [ ] Server-side validation catches invalid parameter combinations
- [ ] Clamping logic applied before LiteLLM calls
- [ ] JSON passthrough editor works
- [ ] Unknown model warning badge displayed
- [ ] 5+ providers tested: OpenAI, Anthropic, Google Gemini, Zai/GLM, LM Studio
- [ ] Saved parameter preferences persist across sessions
- [ ] Browser-tested in Chrome with screen captures
- [ ] No regressions in existing benchmarks or tool evals

## API Design

### GET /api/provider-params/registry

Returns the full parameter registry -- all providers, all parameters, ranges, conflicts.

**Response structure (abbreviated):**
```json
{
  "providers": {
    "openai": {
      "display_name": "OpenAI",
      "tier1": {
        "temperature": {"min": 0.0, "max": 2.0, "default": 1.0, "step": 0.1, "type": "float"},
        "max_tokens": {"min": 1, "max": 128000, "default": 4096, "type": "int"},
        "stop": {"type": "string_array", "max_items": 4}
      },
      "tier2": {
        "top_p": {"min": 0.0, "max": 1.0, "supported": true},
        "top_k": {"supported": false, "reason": "OpenAI does not support top_k"},
        "frequency_penalty": {"min": -2.0, "max": 2.0, "supported": true},
        "presence_penalty": {"min": -2.0, "max": 2.0, "supported": true},
        "seed": {"supported": true, "deprecated": true},
        "reasoning_effort": {"type": "enum", "values": ["none", "low", "medium", "high"], "supported": true}
      },
      "conflicts": [...],
      "model_overrides": {"gpt-5*": {"temperature": {"locked": true, "value": 1.0}}}
    },
    "anthropic": { ... },
    "gemini": { ... },
    "ollama": { ... },
    "lm_studio": { ... },
    "mistral": { ... },
    "deepseek": { ... },
    "cohere": { ... },
    "_unknown": { ... }
  }
}
```

### POST /api/provider-params/validate

**Request:**
```json
{
  "provider_key": "anthropic",
  "model_id": "anthropic/claude-sonnet-4-5",
  "params": {"temperature": 1.5, "top_p": 0.9, "max_tokens": 4096}
}
```

**Response:**
```json
{
  "valid": false,
  "adjustments": [
    {"param": "temperature", "original": 1.5, "adjusted": 1.0, "reason": "Anthropic max temperature is 1.0"},
    {"param": "top_p", "original": 0.9, "adjusted": null, "reason": "Cannot use both temperature and top_p"}
  ],
  "warnings": ["max_tokens is required for Anthropic"],
  "resolved_params": {"temperature": 1.0, "max_tokens": 4096}
}
```

## Database / Config Schema

**No new tables.** Config.yaml `defaults` section gains:
```yaml
defaults:
  provider_params:
    top_p: null
    top_k: null
    frequency_penalty: null
    presence_penalty: null
    seed: null
    reasoning_effort: null
    drop_unsupported: true
    passthrough: {}
```

Registry is a static Python module (`provider_params.py`), not DB.

## Server-Side Implementation

New file: `provider_params.py`

```python
PROVIDER_REGISTRY = { ... }

def identify_provider(model_id, provider_key) -> str
def validate_params(provider, model_id, params) -> dict
def clamp_temperature(value, provider, model_id) -> float
def resolve_conflicts(params, provider, model_id) -> list[dict]
def build_litellm_kwargs(target, params) -> dict
```

Integration points: `async_run_single()`, `run_single_eval()`, `run_multi_turn_eval()`

## UI Wireframe

- **Entry Points:** Benchmark config + Tool Eval config (accordion below model selection)
- **Tabbed Interface:** One tab per provider when multiple providers selected
- **Tier 1:** Always-visible sliders with dynamic ranges
- **Tier 2:** Collapsible "Advanced" -- greyed when unsupported, orange when conflicting
- **Tier 3:** Collapsible "Custom Parameters (JSON)" with validation
- **Conflict Resolution:** Orange borders, info banners, auto-disabled with strikethrough
- **Unknown Model Warning:** Yellow banner

## Task Breakdown

| # | Task | Assignee | Est. | Depends On |
|---|------|----------|------|------------|
| 1 | Create `provider_params.py` with PROVIDER_REGISTRY | backend | 3h | - |
| 2 | Implement `identify_provider()` | backend | 1h | #1 |
| 3 | Implement `validate_params()` + `clamp_temperature()` + `resolve_conflicts()` | backend | 3h | #1 |
| 4 | Implement `build_litellm_kwargs()` | backend | 2h | #3 |
| 5 | Implement `GET /api/provider-params/registry` | backend | 1h | #1 |
| 6 | Implement `POST /api/provider-params/validate` | backend | 1h | #3 |
| 7 | Integrate `build_litellm_kwargs()` into eval/benchmark engines | backend | 2h | #4 |
| 8 | Add `provider_params` to benchmark/tool-eval request parsing | backend | 1.5h | #7 |
| 9 | Implement tabbed provider parameter panel UI | frontend | 4h | #5 |
| 10 | Implement Tier 1 controls (sliders with dynamic ranges) | frontend | 3h | #9 |
| 11 | Implement Tier 2 controls (supported/unsupported/deprecated) | frontend | 3h | #9 |
| 12 | Implement JSON passthrough editor | frontend | 2h | #9 |
| 13 | Implement conflict resolution UI | frontend | 2.5h | #6, #11 |
| 14 | Implement unknown model warning badge | frontend | 1h | #2 |
| 15 | Integrate parameter panel into benchmark config UI | frontend | 1.5h | #9-#11 |
| 16 | Integrate parameter panel into tool eval config UI | frontend | 1h | #15 |
| 17 | Integration testing (Docker, 5+ providers, conflicts) | QA | 3h | all |

## Test Plan

| Test Case | Provider | Params | Expected |
|-----------|----------|--------|----------|
| Anthropic clamping | Anthropic | temp=1.5, top_p=0.9 | temp->1.0, top_p dropped |
| OpenAI normal | OpenAI | temp=1.5, top_p=0.9 | All pass through |
| Ollama local | LM Studio | temp=0.8, top_k=40, min_p=0.05 | Tier 1+2 kwargs, min_p passthrough |
| Unknown model | Custom | temp=0.7 | Warning badge, OpenAI defaults |
| Conflict GPT-5 | OpenAI | temp=0.5 | temp locked to 1.0 |
| Benchmark with params | Zai | top_p=0.95 | Benchmark runs correctly |
| Tool eval with params | Zai | top_p=0.95, tool_choice=required | Eval runs correctly |

### Architectural Decisions
- AD-1: Static registry in code, not DB
- AD-2: Clamping, not rejection
- AD-3: Passthrough as escape hatch
- AD-4: Provider ID from config.yaml `provider_key`
- AD-5: No per-request provider_params in DB
- AD-6: Backward compatible

---

# FEATURE 2: PARAMETER TUNER

**Effort:** 22.5h (12 tasks) | **Branch:** `feat/param-tuner` | **Priority:** SECOND

## Description

Deterministic parameter sweep engine for tool calling evaluation -- analogous to scikit-learn's GridSearchCV. Users define a search space of parameter combinations (temperature, top_p, tool_choice, max_tokens), select a tool eval suite and models, and the engine exhaustively runs every combination. Results identify optimal parameter configuration per model.

**Key Principle:** Every combination tested. No randomness. Complete, reproducible comparison.

Wraps existing `run_single_eval()` (app.py:2124).

## User Stories

| ID | Story | Priority |
|----|-------|----------|
| PT-1 | Define search space with min/max/step for numeric params and checkboxes for categorical | Must |
| PT-2 | Preview total combos (grid size) before running | Must |
| PT-3 | Select suite and models to tune against | Must |
| PT-4 | Run all combos with real-time progress | Must |
| PT-5 | Comparison table sorted by score, best highlighted | Must |
| PT-6 | Browse history of past tuning runs | Must |
| PT-7 | Cancel mid-execution | Must |
| PT-8 | Delete tuning runs from history | Should |
| PT-9 | Export results as CSV | Nice |
| PT-10 | Per-model best configs when tuning multiple models | Should |

## Acceptance Criteria

1. **Search Space Builder:** temperature (0.0-2.0), top_p (0.0-1.0), max_tokens (1-16384). tool_choice checkboxes.
2. **Grid Preview:** Shows `N = |temp| x |top_p| x |tool_choice| x ...`. Warning if N > 50.
3. **Execution:** All combos via `run_single_eval()`. SSE streams progress.
4. **Concurrency:** Sequential within provider, parallel across providers.
5. **Results:** Sortable table. Best config highlighted.
6. **Cancellation:** Partial results saved.
7. **History:** List, detail, delete.

## Definition of Ready (DoR)

- [ ] Feature spec reviewed and approved
- [ ] API endpoints defined
- [ ] Database schema defined
- [ ] UI wireframe approved
- [ ] `run_single_eval` confirmed working on main
- [ ] Test suite with 3+ test cases exists
- [ ] Zai provider API keys available

## Definition of Done (DoD)

- [ ] All API endpoints implemented
- [ ] Database table created in `db.init_db()`
- [ ] SSE streaming works for progress/results/completion
- [ ] UI renders search space builder, grid preview, progress, results, history
- [ ] Cancellation works with partial save
- [ ] Rate limiting enforced
- [ ] Tested with Zai (GLM-4.7, GLM-5, GLM-4.5-Air) in Docker
- [ ] Tested with 2x2 grid (4 combos) and 16+ combo grid
- [ ] History CRUD tested
- [ ] No regressions in existing tool eval
- [ ] Browser-tested with screen captures

## API Design

### POST /api/tool-eval/param-tune

**Request:**
```json
{
  "suite_id": "abc123",
  "models": ["zai/GLM-4.7"],
  "search_space": {
    "temperature": {"min": 0.0, "max": 1.0, "step": 0.5},
    "top_p": {"min": 0.8, "max": 1.0, "step": 0.1},
    "tool_choice": ["auto", "required"],
    "max_tokens": {"min": 512, "max": 1024, "step": 512}
  }
}
```

**SSE Events:** `tune_start`, `combo_start`, `combo_result`, `tune_progress`, `tune_complete`, `heartbeat`, `cancelled`, `error`

### Other Endpoints
- `POST /api/tool-eval/param-tune/cancel` -- reuses `_get_user_cancel()`
- `GET /api/tool-eval/param-tune/history` -- list runs
- `GET /api/tool-eval/param-tune/history/{id}` -- full run details
- `DELETE /api/tool-eval/param-tune/history/{id}` -- delete run

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS param_tune_runs (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    suite_id TEXT NOT NULL,
    suite_name TEXT NOT NULL,
    models_json TEXT NOT NULL,
    search_space_json TEXT NOT NULL,
    results_json TEXT NOT NULL DEFAULT '[]',
    best_config_json TEXT,
    best_score REAL DEFAULT 0.0,
    total_combos INTEGER NOT NULL,
    completed_combos INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','cancelled','error')),
    duration_s REAL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_param_tune_runs_user ON param_tune_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_param_tune_runs_ts ON param_tune_runs(user_id, timestamp DESC);
```

## UI Wireframe

- **Entry Point:** New "Parameter Tuner" tab under Tool Eval
- **Search Space Builder:** Toggle per param, min/max/step inputs, pill badges, validation
- **Grid Preview Bar:** Color-coded combo count (green/yellow/orange/red)
- **Progress View:** Progress bar, live results table sorted by score, best-so-far highlighted
- **Results View:** Summary card, per-model tabs, sortable table, "Apply Best Config", CSV export
- **History View:** Table with Date, Suite, Models, Combos, Best Score, Status, Actions

## Task Breakdown

| # | Task | Assignee | Est. | Depends On |
|---|------|----------|------|------------|
| 1 | Add `param_tune_runs` table to `db.py:init_db()` | backend | 0.5h | - |
| 2 | Add CRUD functions to `db.py` | backend | 1h | #1 |
| 3 | Implement grid generation logic | backend | 1h | - |
| 4 | Implement `POST /api/tool-eval/param-tune` SSE endpoint | backend | 3h | #2, #3 |
| 5 | Implement cancel endpoint | backend | 0.5h | #4 |
| 6 | Implement history endpoints | backend | 1h | #2 |
| 7 | Implement search space builder UI | frontend | 4h | - |
| 8 | Implement progress view | frontend | 3h | #4 |
| 9 | Implement results view | frontend | 3h | #8 |
| 10 | Implement history view | frontend | 2h | #6 |
| 11 | Wire SSE connection | frontend | 1.5h | #4, #7 |
| 12 | Integration testing | QA | 2h | all |

## Test Plan

| Test Case | Config | Expected |
|-----------|--------|----------|
| Small grid | temp=[0.0, 0.5], tool_choice=[required] = 2 | Completes, best identified |
| Medium grid | 3 temp x 2 top_p x 2 tool_choice = 12 | All 12 results saved |
| Cancellation | 12-combo, cancel after 3 | Status="cancelled", 3 saved |
| Multi-model | 2 models, 4 combos each | Per-model best configs |
| History CRUD | Full lifecycle | All operations succeed |

### Architectural Decisions
- AD-1: Reuse `run_single_eval()` directly
- AD-2: Sequential per model, parallel across providers
- AD-3: `results_json` as flat array
- AD-4: Shared cancellation via `_get_user_cancel()`
- AD-5: "Apply Best Config" calls existing PUT endpoint
- AD-6: Respect `skip_params` during sweep
- AD-7: `pt` prefix for all functions/elements

---

# FEATURE 3: PROMPT TUNER

**Effort:** 30h (14 tasks) | **Branch:** `feat/prompt-tuner` | **Priority:** THIRD

## Description

Uses an LLM to generate system prompt variations that optimize tool calling performance. Stochastic -- uses AI creativity to explore prompt space.

### Quick Mode
Single generation of N prompt variations (default: 5) with different styles. Each scored by running full tool eval suite.

### Evolutionary (Genetic Algorithm) Mode
1. **Seed:** Start with base prompt
2. **Generate:** LLM creates N variations
3. **Evaluate:** Run each through tool eval suite
4. **Select:** Top 40% survive
5. **Mutate:** LLM evolves top performers
6. **Repeat:** For G generations

Defaults: 3 generations x 5 population = 15 prompts evaluated.

## User Stories

| ID | Story | Priority |
|----|-------|----------|
| PrT-1 | Quick Mode: generate N prompt variations | Must |
| PrT-2 | Evolutionary Mode: configurable generations + population | Must |
| PrT-3 | Cost estimate before starting | Must |
| PrT-4 | Provide base prompt or use default | Must |
| PrT-5 | Watch evolution in real-time | Must |
| PrT-6 | See winning prompt with copy button | Must |
| PrT-7 | Browse history with generation details | Must |
| PrT-8 | Cancel mid-execution | Must |
| PrT-9 | Choose meta-model (generates variations) | Should |
| PrT-10 | Choose target model (evaluated) -- can differ from meta | Should |
| PrT-11 | Lineage view showing prompt evolution | Nice |

## Acceptance Criteria

1. Quick Mode: N variations (3-20), each with distinct style, all scored.
2. Evo Mode: G generations, P population, top 40% survive.
3. Cost estimate displayed with formula.
4. Meta-prompt receives base prompt, tool definitions, test cases, mode context.
5. SSE streaming for all phases.
6. Best prompt with copy button + full comparison table.
7. Full history with all generations stored.

## Definition of Ready (DoR)

- [ ] Feature spec approved
- [ ] Meta-prompts tested (variation + mutation)
- [ ] API endpoints defined
- [ ] Database schema defined
- [ ] UI wireframe approved
- [ ] Tool eval suite with 3+ cases exists
- [ ] Zai API keys available

## Definition of Done (DoD)

- [ ] Both Quick and Evolutionary modes functional
- [ ] Meta-prompts produce diverse, well-formed prompts
- [ ] Cost estimate accurate
- [ ] SSE streaming works for all events
- [ ] UI renders mode selector, config, progress, results, history
- [ ] Copy-to-clipboard works
- [ ] Cancellation with partial save
- [ ] Tested with Zai in Docker (Quick: 5, Evo: 3x5)
- [ ] History CRUD tested
- [ ] No regressions
- [ ] Browser-tested with screen captures

## API Design

### POST /api/tool-eval/prompt-tune

**Request:**
```json
{
  "suite_id": "abc123",
  "target_models": ["zai/GLM-4.7"],
  "meta_model": "zai/GLM-4.7",
  "mode": "evolutionary",
  "base_prompt": "You are a helpful assistant that uses tools.",
  "config": {
    "population_size": 5, "generations": 3,
    "selection_ratio": 0.4, "temperature": 0.0, "tool_choice": "required"
  }
}
```

**SSE Events:** `tune_start`, `generation_start`, `prompt_generated`, `prompt_eval_start`, `prompt_eval_result`, `generation_complete`, `tune_complete`, `heartbeat`, `cancelled`, `error`

### Other Endpoints
- `POST /api/tool-eval/prompt-tune/cancel`
- `GET /api/tool-eval/prompt-tune/estimate` -- `?suite_id&mode&population_size&generations&num_models`
- `GET /api/tool-eval/prompt-tune/history`
- `GET /api/tool-eval/prompt-tune/history/{id}`
- `DELETE /api/tool-eval/prompt-tune/history/{id}`

## Meta-Prompt Design

**Quick Mode:** Generates N variations with different approaches (concise, detailed, structured, conversational, technical). Returns JSON array with style + prompt.

**Evo Mode Mutation:** Takes parent prompts with scores, creates N mutations (bold, conservative, crossover). Returns JSON array with parent_index, mutation_type, prompt.

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS prompt_tune_runs (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    suite_id TEXT NOT NULL,
    suite_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('quick','evolutionary')),
    target_models_json TEXT NOT NULL,
    meta_model TEXT NOT NULL,
    base_prompt TEXT,
    config_json TEXT NOT NULL,
    generations_json TEXT NOT NULL DEFAULT '[]',
    best_prompt TEXT,
    best_score REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','cancelled','error')),
    total_prompts INTEGER DEFAULT 0,
    completed_prompts INTEGER DEFAULT 0,
    duration_s REAL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_user ON prompt_tune_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_prompt_tune_runs_ts ON prompt_tune_runs(user_id, timestamp DESC);
```

## UI Wireframe

- **Mode Selector:** Two large card buttons (Quick / Evolutionary)
- **Config Panel:** Suite, target models, meta-model, base prompt textarea, sliders, estimate badge
- **Progress (Quick):** Progress bar, live prompt cards sorted by score
- **Progress (Evo):** Generation timeline, population table, survivor highlighting
- **Results:** Hero card with best prompt + copy, generation breakdown, lineage, score trend chart
- **History:** Standard table

## Task Breakdown

| # | Task | Assignee | Est. | Depends On |
|---|------|----------|------|------------|
| 1 | Add `prompt_tune_runs` table | backend | 0.5h | - |
| 2 | Add CRUD functions | backend | 1h | #1 |
| 3 | Implement meta-prompt templates | backend | 2h | - |
| 4 | Implement Quick Mode generation logic | backend | 2h | #3 |
| 5 | Implement Evolutionary Mode logic | backend | 3h | #3, #4 |
| 6 | Implement SSE endpoint | backend | 3h | #2, #4, #5 |
| 7 | Implement estimate endpoint | backend | 0.5h | - |
| 8 | Implement cancel + history endpoints | backend | 1h | #2 |
| 9 | Implement mode selector + config UI | frontend | 3h | - |
| 10 | Implement progress view (both modes) | frontend | 4h | #6 |
| 11 | Implement results view (copy, lineage, charts) | frontend | 4h | #10 |
| 12 | Implement history view | frontend | 1.5h | #8 |
| 13 | Wire SSE connection | frontend | 1.5h | #6, #9 |
| 14 | Integration testing | QA | 3h | all |

## Test Plan

| Test Case | Config | Expected |
|-----------|--------|----------|
| Quick (small) | 3 variations, 1 model | 3 prompts scored, best identified |
| Quick (default) | 5 variations, 1 model | 5 diverse styles |
| Evo (minimal) | 2 gen x 3 pop | Gen 2 builds on gen 1 winners |
| Evo (default) | 3 gen x 5 pop | Score improvement across gens |
| Cancel | Start 3x5, cancel gen 2 | Partial save, gen 1 complete |
| Multi-model | Quick 5, 2 models | Per-model scores |

### Architectural Decisions
- AD-1: Separate meta-model and target model
- AD-2: Store full prompt text (self-contained history)
- AD-3: System prompt injection via optional param on `run_single_eval()`
- AD-4: JSON response with regex fallback
- AD-5: Truncation selection (simplest)
- AD-6: Shared user lock

---

# FEATURE 4: LLM JUDGE

**Effort:** 34.5h (16 tasks) | **Branch:** `feat/llm-judge` | **Priority:** FOURTH

## Description

AI evaluator layer on top of tool eval results. Goes beyond simple score matching to assess reasoning quality, parameter choice rationale, and cross-model comparison.

### Mode 1: Post-Eval Quality Check
Runs AFTER eval completes. Reviews all test cases: tool selection reasonableness, parameter closeness (semantic vs exact), reasoning quality, cross-case consistency.
**Output:** Report card with grade (A-F), per-case assessments, cross-case analysis.

### Mode 2: Live Inline Scoring
Runs alongside each eval call in real-time. Adds quality badges (1-5 stars, verdict) per test case.

### Mode 3: Comparative Judge
Compares two models' results side-by-side. Picks winner per case + overall verdict.

## User Stories

| ID | Story | Priority |
|----|-------|----------|
| JG-1 | Enable post-eval judge for quality report | Must |
| JG-2 | Choose judge model/provider | Must |
| JG-3 | Full Judge Report with reasoning + cross-case analysis | Must |
| JG-4 | Live inline scoring per test case | Must |
| JG-5 | Inline badges (score, thumbs up/down) | Must |
| JG-6 | Compare two models with judge picking winner | Must |
| JG-7 | Expand badge for full reasoning | Should |
| JG-8 | Judge on historical eval runs | Should |
| JG-9 | Judge reports in history | Should |
| JG-10 | Configure judge modes before starting eval | Must |

## Acceptance Criteria

1. **Post-Eval:** Auto-runs if enabled. Grade A-F, per-case verdicts, patterns, recommendations.
2. **Live Inline:** Results within 2-5s. Star rating (1-5) + one-line summary.
3. **Comparative:** Two eval_run_ids. Per-case comparison + overall winner with confidence.
4. **Judge Model:** User selects. Same provider/key injection.
5. **SSE Events:** New types alongside eval (live) or separate stream (post-eval).
6. **Persistence:** Reports saved to DB.
7. **Opt-in:** Existing workflow unchanged when disabled.

## Definition of Ready (DoR)

- [ ] Feature spec approved
- [ ] Judge meta-prompts tested (all modes)
- [ ] API endpoints defined
- [ ] Database schema defined
- [ ] UI wireframe approved
- [ ] Completed eval run exists for testing
- [ ] Two runs with different models for comparative testing
- [ ] Judge model API key available (Zai)

## Definition of Done (DoD)

- [ ] All three modes functional
- [ ] Meta-prompts produce useful assessments
- [ ] Post-eval report < 30s for 10 cases
- [ ] Live inline badges < 5s per result
- [ ] Comparative produces clear winner
- [ ] UI: settings, badges, report panel, comparative view
- [ ] Reports in DB + accessible from history
- [ ] Tested with Zai (all three modes)
- [ ] No regressions (judge disabled by default)
- [ ] Browser-tested with screen captures

## API Design

### Judge Config (in eval request)
```json
{
  "judge": {"enabled": true, "mode": "live_inline", "judge_model": "zai/GLM-5"}
}
```

### Live Inline SSE Events
```
result:        { ...existing eval result... }
judge_verdict: { test_case_id, model_id, quality_score (1-5), verdict, summary, reasoning }
```

### Post-Eval SSE Events
```
judge_start:    { mode, judge_model, cases_to_review }
judge_verdict:  { per-case }
judge_report:   { overall_grade, overall_score, strengths, weaknesses, analysis, recommendations }
judge_complete: { judge_report_id }
```

### POST /api/tool-eval/judge
Run on completed eval. Request: `{eval_run_id, judge_model}`

### POST /api/tool-eval/judge/compare
Comparative mode. Request: `{eval_run_id_a, eval_run_id_b, judge_model}`
Events: `compare_start`, `compare_case`, `compare_complete`

### History Endpoints
- `GET /api/tool-eval/judge/reports`
- `GET /api/tool-eval/judge/reports/{id}`
- `DELETE /api/tool-eval/judge/reports/{id}`

## Judge Meta-Prompts

**Post-Eval/Live Inline:** Evaluates tool selection (correct/acceptable/wrong), parameter accuracy (exact/close/partial/wrong), reasoning quality. Returns JSON with quality_score, verdict, summary, reasoning.

**Comparative:** Model A vs Model B per case. Returns winner, confidence, reasoning.

**Cross-Case Analysis:** Aggregates verdicts into grade, strengths, weaknesses, analysis, recommendations.

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS judge_reports (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    eval_run_id TEXT,
    eval_run_id_b TEXT,
    judge_model TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('post_eval','live_inline','comparative')),
    verdicts_json TEXT NOT NULL DEFAULT '[]',
    report_json TEXT,
    overall_grade TEXT,
    overall_score REAL,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','error')),
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_judge_reports_user ON judge_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_judge_reports_eval ON judge_reports(eval_run_id);
CREATE INDEX IF NOT EXISTS idx_judge_reports_ts ON judge_reports(user_id, timestamp DESC);
```

## UI Wireframe

- **Judge Settings Panel:** Toggle (off by default), mode dropdown, judge model picker
- **Inline Badges (Live):** Star rating + verdict pill on each result, expandable
- **Report Panel (Post-Eval):** Grade badge, score bar, strengths/weaknesses, analysis, recommendations, per-case accordion
- **Comparative View:** Three-column (Model A | Judge Verdicts | Model B), summary bar, overall verdict
- **History:** Badge on eval runs, dedicated judge reports tab

## Task Breakdown

| # | Task | Assignee | Est. | Depends On |
|---|------|----------|------|------------|
| 1 | Add `judge_reports` table | backend | 0.5h | - |
| 2 | Add CRUD functions | backend | 1h | #1 |
| 3 | Implement judge meta-prompts (all modes) | backend | 2h | - |
| 4 | Implement post-eval judge engine | backend | 3h | #2, #3 |
| 5 | Implement live inline judge | backend | 3h | #3 |
| 6 | Integrate live inline into eval SSE stream | backend | 2h | #5 |
| 7 | Implement POST /api/tool-eval/judge | backend | 1.5h | #4 |
| 8 | Implement POST /api/tool-eval/judge/compare | backend | 2.5h | #3 |
| 9 | Implement judge history endpoints | backend | 1h | #2 |
| 10 | Implement judge settings panel UI | frontend | 2h | - |
| 11 | Implement inline badge UI | frontend | 3h | #6 |
| 12 | Implement judge report panel UI | frontend | 3h | #7 |
| 13 | Implement comparative view UI | frontend | 4h | #8 |
| 14 | Implement judge history UI | frontend | 1.5h | #9 |
| 15 | Wire SSE events for verdicts + reports | frontend | 2h | #6, #10 |
| 16 | Integration testing (all three modes) | QA | 3h | all |

## Test Plan

| Test Case | Config | Expected |
|-----------|--------|----------|
| Post-eval (5 cases) | Eval then judge | Report with verdicts, grade |
| Live inline (5 cases) | Judge enabled | Badges < 5s per result |
| Comparative (5 cases) | Two runs | Per-case winners, overall winner |
| Post-eval on history | Existing eval_run_id | Works same as real-time |
| Different judge model | GLM-5 judge, GLM-4.7 target | Both called correctly |
| Judge disabled | No judge config | No overhead, identical results |

### Architectural Decisions
- AD-1: Separate table (multiple reports per eval, comparative spans two)
- AD-2: Live inline runs concurrently (judge queue -> SSE queue)
- AD-3: JSON response with regex fallback
- AD-4: Judge does NOT replace automated scoring
- AD-5: Shared user lock
- AD-6: Judge always temperature=0.0

---

## RISK ASSESSMENT

| Risk | Mitigation |
|------|-----------|
| AI-generated prompts low quality | Test meta-prompts before integration. Retry + fallback parsing. |
| Judge self-evaluation bias | Warn when judge = target model. Default to different model. |
| Parameter registry becomes stale | Tie to LiteLLM version bumps. Add version field. |
| Large sweeps overwhelm rate limits | Grid preview warns at >50. Each combo = 1 rate-limit event. |
| SSE event proliferation | Prefix per feature. Document schemas. |

---

## OPEN QUESTIONS

1. Should Parameter Tuner results feed into Provider Parameters defaults?
2. Should Prompt Tuner support multi-model evolution?
3. Should Judge reports be shareable?
4. Should we add a "Run All" pipeline mode?

---

## TEST STRATEGY

- **Test Provider:** Zai (GLM-4.7, GLM-5, GLM-4.5-Air) -- free tokens
- **Environment:** Local Docker
- **Quality Gate:** Browser testing with screen captures before staging
- **Bundle Release:** Ships as v1.4.0 when all features pass QA

---

## REFERENCE FILES

| File | Location | Content |
|------|----------|---------|
| Consolidated Spec | `docs/feature-specs-v1.4.md` | Executive summary, dependencies |
| Parameter Tuner Spec | `.scratchpad/handoffs/architect-parameter-tuner-spec-SUCCESS.md` | Full spec |
| Prompt Tuner Spec | `.scratchpad/handoffs/architect-prompt-tuner-spec-SUCCESS.md` | Full spec |
| LLM Judge Spec | `.scratchpad/handoffs/architect-llm-judge-spec-SUCCESS.md` | Full spec |
| Provider Params Spec | `.scratchpad/handoffs/architect-provider-params-spec-SUCCESS.md` | Full spec |
| LiteLLM Research | `.scratchpad/handoffs/research-litellm-findings-SUCCESS.md` | Provider inventory |
| Codebase Patterns | `.scratchpad/handoffs/librarian-codebase-patterns-SUCCESS.md` | UI/SSE/API patterns |

# Changelog

All notable changes to this project are documented here.
Format: human-readable summaries grouped by impact.

---

## v1.5.0 — 2026-02-24

Sprint 11 release: 12 new features across tool evaluation, parameter tuning, prompt optimization, and analytics. 320 files changed, +21,689 / -880 lines across 5 commits.

### New Features

- **Prompt Library** — Save, load, compare, and manage prompt versions with inline label editing and source badges (manual vs tuner-generated)
- **Irrelevance Detection** — Mark test cases where the model should *not* call a tool; eval results now show an Irrel% score column and IRRELEVANCE badges
- **Auto-Judge on Failure** — When an eval score drops below your threshold, a judge review is automatically submitted (no manual step needed)
- **Format Compliance Tracking** — Each eval result now shows PASS / NORMALIZED / FAIL status so you can see how well models follow your expected output format
- **Error Taxonomy** — 8-type classification system (invalid_invocation, tool_hallucination, etc.) that diagnoses *why* a tool call failed, not just that it did
- **Category Breakdown** — Per-category accuracy view (simple, parallel, multi_turn, irrelevance) so you can see where each model struggles
- **BFCL Import/Export** — Import and export test suites in Berkeley Function-Calling Leaderboard V3 format for industry-standard benchmarking
- **Argument Source Tracking** — Validates that tool call arguments actually come from previous tool output instead of being hallucinated
- **Bayesian Parameter Search** — Optuna-powered optimization with TPE, Random, and Grid search modes to find the best model parameters automatically
- **Param + Quality Correlation** — Interactive 3-axis scatter view (speed, cost, quality) to visualize how parameter changes affect overall model performance
- **Public Leaderboard** — Anonymous aggregated benchmark results with per-user opt-in, so teams can share results without exposing private data
- **Auto-Optimize (OPRO/APE)** — Iterative prompt refinement using a meta-model — the system rewrites your prompts to improve scores automatically

### Bug Fixes

- Fixed a crash when running auto-judge without setting a threshold (the "None is not a number" error)
- Fixed database constraints that rejected valid job types (`prompt_auto_optimize`) and prompt version sources (`auto_optimize`)
- Fixed provider param seeding reading from shared config instead of user-specific config (multi-user isolation issue)
- Removed environment variable API key fallback — users must now configure their own keys (security hardening)
- Added SQLite `busy_timeout` to all write operations to prevent "database is locked" errors under concurrent load

### Under the Hood

- 5 database migrations, 3 new API routers, 2 new background job handlers
- 9 new pytest test files + 9 Playwright E2E specs (979 → 988 automated tests)
- Added `optuna>=4.7.0` dependency for Bayesian search
- Test fixtures hardened: replaced raw `aiosqlite.connect()` with `DatabaseManager` for consistency
- Comprehensive documentation updates across 20+ doc files

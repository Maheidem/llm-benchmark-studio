#!/usr/bin/env python3
"""LLM Benchmark - Measure token/second performance across providers via LiteLLM.

Usage:
    python benchmark.py                          # Run all benchmarks
    python benchmark.py --runs 3                 # Average over 3 runs
    python benchmark.py --provider openai        # Only OpenAI models
    python benchmark.py --model GLM              # Only models matching 'GLM'
    python benchmark.py --prompt "Write a poem"  # Custom prompt
    python benchmark.py --no-save                # Don't save results to file
"""

import argparse
import json
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import tiktoken

import litellm
import yaml

# Disable retry loops at two layers:
# 1. LiteLLM wrapper (default num_retries=2) — we handle retries ourselves.
# 2. OpenAI SDK internal (default max_retries=2) — without this, OpenAI-compatible
#    endpoints (LM Studio) trigger invisible retry loops inside the SDK.
litellm.num_retries = 0
os.environ.setdefault("OPENAI_MAX_RETRIES", "0")
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Suppress LiteLLM noise by default
litellm.suppress_debug_info = True

console = Console()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Target:
    """A single model endpoint to benchmark."""
    provider: str
    model_id: str
    display_name: str
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    skip_params: Optional[list] = None
    context_window: int = 128000
    max_output_tokens: Optional[int] = None
    api_key_env: Optional[str] = None      # env var name (e.g. "OPENAI_API_KEY")
    provider_key: Optional[str] = None     # config key (e.g. "openai")
    input_cost_per_mtok: Optional[float] = None   # custom $/1M input tokens
    output_cost_per_mtok: Optional[float] = None  # custom $/1M output tokens
    system_prompt: Optional[str] = None            # per-model system prompt (prepended to all requests)


@dataclass
class RunResult:
    """Result from one benchmark run."""
    target: Target
    ttft_ms: float = 0.0
    total_time_s: float = 0.0
    output_tokens: int = 0
    input_tokens: int = 0
    tokens_per_second: float = 0.0
    success: bool = True
    error: str = ""
    context_tokens: int = 0
    cost: float = 0.0
    input_tokens_per_second: float = 0.0


@dataclass
class AggregatedResult:
    """Averaged results across multiple runs for one target."""
    target: Target
    avg_ttft_ms: float = 0.0
    avg_total_time_s: float = 0.0
    avg_tokens_per_second: float = 0.0
    avg_output_tokens: float = 0.0
    runs: int = 0
    failures: int = 0
    all_results: list = field(default_factory=list)
    # Variance tracking
    std_dev_tps: float = 0.0
    min_tps: float = 0.0
    max_tps: float = 0.0
    p50_tps: float = 0.0
    p95_tps: float = 0.0
    std_dev_ttft: float = 0.0
    outlier_count: int = 0
    # Cost tracking
    avg_cost: float = 0.0
    total_cost: float = 0.0
    # Input tokens/second
    avg_input_tps: float = 0.0


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class ModelSchema(BaseModel):
    id: str
    display_name: str
    context_window: int = 128000
    max_output_tokens: Optional[int] = None
    skip_params: Optional[list[str]] = None
    input_cost_per_mtok: Optional[float] = None
    output_cost_per_mtok: Optional[float] = None


class ProviderSchema(BaseModel):
    display_name: str
    models: list[ModelSchema]
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    model_id_prefix: Optional[str] = None


class ConfigSchema(BaseModel):
    defaults: dict
    providers: dict[str, ProviderSchema]
    prompt_templates: Optional[dict] = None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load and validate benchmark configuration from YAML file."""
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    try:
        ConfigSchema(**raw)
    except ValidationError as e:
        console.print(f"[red]Config validation error in {config_path}:[/red]")
        for err in e.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            console.print(f"  [yellow]{loc}[/yellow]: {err['msg']}")
        raise SystemExit(1)
    return raw


def resolve_api_key(provider_cfg: dict) -> Optional[str]:
    """Resolve API key: direct value > env var > None."""
    if "api_key" in provider_cfg:
        return provider_cfg["api_key"]
    if "api_key_env" in provider_cfg:
        key = os.getenv(provider_cfg["api_key_env"])
        if not key:
            console.print(
                f"  [yellow]Warning: env var {provider_cfg['api_key_env']} not set[/yellow]"
            )
        return key
    return None


def sanitize_error(error_msg: str, api_key: Optional[str] = None) -> str:
    """Remove API keys and sensitive tokens from error messages.

    Strips:
    - The specific api_key if provided
    - Common API key patterns (sk-*, key-*, gsk_*, AIza*)
    - Bearer tokens
    """
    import re as _re
    msg = error_msg

    # Strip specific key if known
    if api_key and len(api_key) > 8:
        msg = msg.replace(api_key, "***")

    # Strip common API key patterns
    msg = _re.sub(r'(sk-[a-zA-Z0-9]{8})[a-zA-Z0-9-]+', r'\1***', msg)
    msg = _re.sub(r'(key-[a-zA-Z0-9]{4})[a-zA-Z0-9-]+', r'\1***', msg)
    msg = _re.sub(r'(gsk_[a-zA-Z0-9]{4})[a-zA-Z0-9-]+', r'\1***', msg)
    msg = _re.sub(r'(AIza[a-zA-Z0-9]{4})[a-zA-Z0-9-]+', r'\1***', msg)
    msg = _re.sub(r'Bearer\s+[a-zA-Z0-9._-]+', 'Bearer ***', msg)

    return msg


def build_targets(
    config: dict,
    provider_filter: Optional[str] = None,
    model_filter: Optional[str] = None,
) -> list[Target]:
    """Build list of benchmark targets from config, with optional filters."""
    targets = []

    for prov_key, prov_cfg in config.get("providers", {}).items():
        # Substring filter on provider key or display name
        if provider_filter:
            pf = provider_filter.lower()
            if pf not in prov_key.lower() and pf not in prov_cfg.get("display_name", "").lower():
                continue

        api_key = resolve_api_key(prov_cfg)
        api_base = prov_cfg.get("api_base")
        provider_name = prov_cfg.get("display_name", prov_key)

        for model in prov_cfg.get("models", []):
            model_id = model["id"]
            model_display = model.get("display_name", model_id)

            # Substring filter on model id or display name
            if model_filter:
                mf = model_filter.lower()
                if mf not in model_id.lower() and mf not in model_display.lower():
                    continue

            targets.append(
                Target(
                    provider=provider_name,
                    model_id=model_id,
                    display_name=model_display,
                    api_base=api_base,
                    api_key=api_key,
                    skip_params=model.get("skip_params"),
                    context_window=model.get("context_window", 128000),
                    max_output_tokens=model.get("max_output_tokens"),
                    api_key_env=prov_cfg.get("api_key_env"),
                    provider_key=prov_key,
                    input_cost_per_mtok=model.get("input_cost_per_mtok"),
                    output_cost_per_mtok=model.get("output_cost_per_mtok"),
                    system_prompt=model.get("system_prompt"),
                )
            )

    return targets


# ---------------------------------------------------------------------------
# Benchmark execution
# ---------------------------------------------------------------------------

def generate_context_text(target_tokens: int) -> str:
    """Generate realistic filler text sized to approximately target_tokens.

    Builds context from diverse block types (code, prose, JSON, docs) to
    better simulate real-world workloads rather than repeating one paragraph.
    """
    if target_tokens <= 0:
        return ""

    # Diverse content blocks that cycle to fill the target token count
    _CONTEXT_BLOCKS = [
        # Block 0: Python code snippet
        (
            "```python\n"
            "def binary_search(arr: list[int], target: int) -> int:\n"
            '    """Return the index of target in sorted arr, or -1 if absent."""\n'
            "    lo, hi = 0, len(arr) - 1\n"
            "    while lo <= hi:\n"
            "        mid = (lo + hi) // 2\n"
            "        if arr[mid] == target:\n"
            "            return mid\n"
            "        elif arr[mid] < target:\n"
            "            lo = mid + 1\n"
            "        else:\n"
            "            hi = mid - 1\n"
            "    return -1\n"
            "```\n"
        ),
        # Block 1: Prose paragraph - AI topic
        (
            "Artificial intelligence continues to reshape industries across the global economy. "
            "Modern language models leverage transformer architectures with attention mechanisms "
            "that enable contextual understanding of text at unprecedented scale. These systems "
            "are trained on diverse corpora spanning scientific literature, software documentation, "
            "news articles, and conversational data. The resulting models demonstrate emergent "
            "capabilities in reasoning, code generation, summarization, and creative writing. "
        ),
        # Block 2: JSON data structure
        (
            '{"experiment": {"id": "exp-20240315-001", "parameters": {"learning_rate": 0.001, '
            '"batch_size": 64, "epochs": 100, "optimizer": "adamw", "weight_decay": 0.01}, '
            '"metrics": {"train_loss": 0.342, "val_loss": 0.387, "accuracy": 0.924, '
            '"f1_score": 0.918, "inference_ms": 12.4}, "hardware": {"gpu": "A100-80GB", '
            '"gpu_count": 4, "cpu": "AMD EPYC 7763", "ram_gb": 512}}}\n'
        ),
        # Block 3: Technical documentation excerpt
        (
            "## API Rate Limiting\n\n"
            "All endpoints enforce rate limits measured in requests per minute (RPM) and "
            "tokens per minute (TPM). When a rate limit is exceeded, the server responds "
            "with HTTP 429 and a Retry-After header indicating seconds to wait. Clients "
            "should implement exponential backoff starting at 1 second with a maximum of "
            "60 seconds. Batch endpoints allow up to 50 requests per call and share the "
            "same TPM quota as streaming endpoints. Enterprise tiers receive 10x the "
            "default limits and dedicated endpoint pools.\n"
        ),
        # Block 4: Prose paragraph - networking topic
        (
            "Distributed systems rely on consensus protocols to maintain consistency across "
            "replicas. The Raft algorithm partitions the consensus problem into leader "
            "election, log replication, and safety guarantees. Each server maintains a "
            "replicated log of commands that are applied to a deterministic state machine. "
            "When a leader receives a client request, it appends the entry to its log and "
            "replicates it to followers. Once a majority acknowledges the entry, it is "
            "committed and the leader responds to the client. Network partitions and "
            "leader failures are handled through randomised election timeouts. "
        ),
        # Block 5: Python code snippet - data processing
        (
            "```python\n"
            "import json\n"
            "from collections import Counter\n"
            "from pathlib import Path\n\n"
            "def analyse_logs(log_dir: str) -> dict:\n"
            '    """Aggregate error counts from JSON log files."""\n'
            "    errors = Counter()\n"
            "    for path in Path(log_dir).glob('*.json'):\n"
            "        for line in path.read_text().splitlines():\n"
            "            entry = json.loads(line)\n"
            "            if entry.get('level') == 'ERROR':\n"
            "                errors[entry.get('code', 'UNKNOWN')] += 1\n"
            "    return dict(errors.most_common(20))\n"
            "```\n"
        ),
        # Block 6: JSON config structure
        (
            '{"deployment": {"service": "inference-gateway", "version": "2.4.1", '
            '"replicas": 3, "resources": {"cpu_limit": "4000m", "memory_limit": "16Gi", '
            '"gpu_limit": 1}, "autoscaling": {"min_replicas": 2, "max_replicas": 8, '
            '"target_cpu_utilization": 70, "scale_down_delay_s": 300}, '
            '"health_check": {"path": "/healthz", "interval_s": 10, "timeout_s": 5}}}\n'
        ),
        # Block 7: Technical documentation - database
        (
            "## Query Optimization\n\n"
            "The query planner selects execution strategies based on table statistics, "
            "available indexes, and estimated cardinalities. For joins involving more than "
            "three tables, the planner uses dynamic programming to evaluate join orderings. "
            "Partial indexes can dramatically reduce index size when queries consistently "
            "filter on a known predicate. The EXPLAIN ANALYZE command reveals actual row "
            "counts versus estimates, helping identify stale statistics or cardinality "
            "misestimates that lead to suboptimal plans.\n"
        ),
    ]

    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        # Fallback: rough estimate of 1 token ~ 4 chars
        full_text = "\n".join(_CONTEXT_BLOCKS)
        repeats = max(1, target_tokens * 4 // len(full_text) + 1)
        text = (full_text + "\n") * repeats
        return text[: target_tokens * 4]

    # Cycle through blocks to fill the target
    parts = []
    total_tokens = 0
    idx = 0
    while total_tokens < target_tokens:
        block = _CONTEXT_BLOCKS[idx % len(_CONTEXT_BLOCKS)]
        parts.append(block)
        total_tokens += len(enc.encode(block))
        idx += 1

    text = "\n".join(parts)
    tokens = enc.encode(text)
    return enc.decode(tokens[:target_tokens])


def run_single(
    target: Target, prompt: str, max_tokens: int, temperature: float,
    context_tokens: int = 0, timeout: int = 120,
) -> RunResult:
    """Execute a single streaming benchmark run against one model."""
    result = RunResult(target=target, context_tokens=context_tokens)

    messages = []
    # Per-model system prompt (prepended before context text)
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

    kwargs = {
        "model": target.model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timeout": timeout,
    }
    if target.api_base:
        kwargs["api_base"] = target.api_base
    if target.api_key:
        kwargs["api_key"] = target.api_key
    # Remove params this model doesn't support
    if target.skip_params:
        for p in target.skip_params:
            kwargs.pop(p, None)

    try:
        start = time.perf_counter()
        stream = litellm.completion(**kwargs)

        ttft = None
        chunk_count = 0
        usage_from_stream = None

        for chunk in stream:
            now = time.perf_counter()

            # Time to first token
            if ttft is None:
                ttft = (now - start) * 1000  # ms

            # Count content-bearing chunks (1 chunk ~ 1 token)
            if (
                chunk.choices
                and chunk.choices[0].delta
                and chunk.choices[0].delta.content
            ):
                chunk_count += 1

            # Capture usage from final chunk if provider supports it
            if hasattr(chunk, "usage") and chunk.usage:
                usage_from_stream = chunk.usage

        total = time.perf_counter() - start

        # Prefer provider-reported counts; fall back to chunk counting
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

        # Input tokens/second: how fast the model processes the prompt
        if result.ttft_ms > 0 and result.input_tokens > 0:
            result.input_tokens_per_second = result.input_tokens / (result.ttft_ms / 1000)

        # Cost tracking (not all models support this)
        try:
            result.cost = litellm.completion_cost(
                model=target.model_id,
                prompt=str(result.input_tokens),
                completion=str(result.output_tokens),
                prompt_tokens=result.input_tokens,
                completion_tokens=result.output_tokens,
            )
        except Exception:
            result.cost = 0.0

        # Custom pricing fallback: when LiteLLM returns 0, use config pricing
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


def _compute_variance(agg: AggregatedResult, successes: list[RunResult]) -> None:
    """Compute variance statistics and outlier detection on an AggregatedResult."""
    n = len(successes)
    if n == 0:
        return

    tps_vals = [r.tokens_per_second for r in successes]
    ttft_vals = [r.ttft_ms for r in successes]

    agg.min_tps = min(tps_vals)
    agg.max_tps = max(tps_vals)
    agg.p50_tps = statistics.median(tps_vals)

    if n >= 2:
        agg.std_dev_tps = statistics.stdev(tps_vals)
        agg.std_dev_ttft = statistics.stdev(ttft_vals)
        # p95: use quantiles when we have enough data, else use max
        if n >= 4:
            agg.p95_tps = statistics.quantiles(tps_vals, n=20)[-1]  # 95th percentile
        else:
            agg.p95_tps = max(tps_vals)
    else:
        agg.p95_tps = tps_vals[0]

    # Outlier detection via IQR (only meaningful with >= 4 runs)
    if n >= 4:
        q1, _, q3 = statistics.quantiles(tps_vals, n=4)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        agg.outlier_count = sum(1 for v in tps_vals if v < lower or v > upper)


def run_benchmarks(
    targets: list[Target],
    prompt: str,
    runs: int,
    max_tokens: int,
    temperature: float,
    context_tiers: list[int] | None = None,
    warmup: bool = True,
) -> list[AggregatedResult]:
    """Run benchmarks for all targets, optionally multiple runs each."""
    if context_tiers is None:
        context_tiers = [0]

    results = []
    # Calculate total: for each tier, count eligible targets x runs
    total_runs = 0
    for tier in context_tiers:
        for target in targets:
            headroom = target.context_window - max_tokens - 100  # ~100 tokens for prompt
            if tier == 0 or tier <= headroom:
                total_runs += runs

    current = 0

    for tier in context_tiers:
        if tier > 0:
            console.print(f"\n  [bold cyan]── Context tier: {tier:,} tokens ──[/bold cyan]")

        for target in targets:
            headroom = target.context_window - max_tokens - 100
            if tier > 0 and tier > headroom:
                console.print(
                    f"  [yellow]Skipping {tier // 1000}K tier for {target.display_name} "
                    f"({target.context_window // 1000}K context window)[/yellow]"
                )
                continue

            # Warm-up run (discarded)
            if warmup:
                console.print(f"  [dim]Warm-up: {target.display_name}...[/dim]", end=" ")
                wu = run_single(target, prompt, max_tokens, temperature, context_tokens=tier)
                console.print("[dim]done[/dim]" if wu.success else "[dim]fail[/dim]")

            run_results = []
            for r in range(runs):
                current += 1
                tier_label = f" @ {tier // 1000}K ctx" if tier > 0 else ""
                console.print(
                    f"  [{current}/{total_runs}] {target.provider} / "
                    f"{target.display_name}{tier_label} (run {r + 1}/{runs})...",
                    end=" ",
                )

                result = run_single(target, prompt, max_tokens, temperature, context_tokens=tier)
                run_results.append(result)

                if result.success:
                    console.print(
                        f"[green]OK[/green] {result.tokens_per_second:.1f} tok/s "
                        f"({result.output_tokens} tokens in {result.total_time_s:.2f}s)"
                    )
                else:
                    console.print(f"[red]FAIL[/red] {result.error[:80]}")

            # Aggregate
            successes = [r for r in run_results if r.success]
            agg = AggregatedResult(
                target=target,
                runs=len(run_results),
                failures=len(run_results) - len(successes),
                all_results=run_results,
            )

            if successes:
                n = len(successes)
                agg.avg_ttft_ms = sum(r.ttft_ms for r in successes) / n
                agg.avg_total_time_s = sum(r.total_time_s for r in successes) / n
                agg.avg_tokens_per_second = sum(r.tokens_per_second for r in successes) / n
                agg.avg_output_tokens = sum(r.output_tokens for r in successes) / n
                agg.avg_cost = sum(r.cost for r in successes) / n
                agg.total_cost = sum(r.cost for r in successes)
                input_tps_vals = [r.input_tokens_per_second for r in successes if r.input_tokens_per_second > 0]
                if input_tps_vals:
                    agg.avg_input_tps = sum(input_tps_vals) / len(input_tps_vals)
                _compute_variance(agg, successes)

            results.append(agg)

    return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

MEDALS = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}  # gold, silver, bronze


def display_results(results: list[AggregatedResult]) -> None:
    """Render a Rich table of benchmark results sorted by tok/s."""
    sorted_results = sorted(
        results, key=lambda r: r.avg_tokens_per_second, reverse=True
    )

    # Show std_dev column only when any result has > 1 run
    show_std = any(r.runs > 1 for r in sorted_results)
    # Show cost column only when any result has cost data
    show_cost = any(r.avg_cost > 0 for r in sorted_results)
    # Show input TPS column only when any result has data
    show_input_tps = any(r.avg_input_tps > 0 for r in sorted_results)

    table = Table(
        title="\U0001f3ce  LLM Benchmark Results",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold cyan",
    )
    table.add_column("Rank", style="bold", width=5, justify="center")
    table.add_column("Provider", style="cyan", min_width=14)
    table.add_column("Model", style="white", min_width=18)
    table.add_column("Tok/s", style="bold green", justify="right", min_width=8)
    if show_std:
        table.add_column("\u00b1 Std", style="dim", justify="right", min_width=7)
    if show_input_tps:
        table.add_column("In Tok/s", justify="right", min_width=9)
    table.add_column("TTFT (ms)", justify="right", min_width=9)
    table.add_column("Total (s)", justify="right", min_width=9)
    table.add_column("Tokens", justify="right", min_width=7)
    if show_cost:
        table.add_column("Cost ($)", justify="right", min_width=9)
    table.add_column("Status", justify="center", min_width=8)

    for i, r in enumerate(sorted_results, 1):
        # Status column
        if r.failures == 0:
            status = f"[green]{r.runs}/{r.runs} OK[/green]"
        elif r.failures < r.runs:
            status = f"[yellow]{r.runs - r.failures}/{r.runs} OK[/yellow]"
        else:
            status = f"[red]FAIL[/red]"

        rank = MEDALS.get(i, str(i)) if r.avg_tokens_per_second > 0 else str(i)
        tok_s = f"{r.avg_tokens_per_second:.1f}" if r.avg_tokens_per_second > 0 else "-"
        ttft = f"{r.avg_ttft_ms:.0f}" if r.avg_ttft_ms > 0 else "-"
        total = f"{r.avg_total_time_s:.2f}" if r.avg_total_time_s > 0 else "-"
        tokens = f"{r.avg_output_tokens:.0f}" if r.avg_output_tokens > 0 else "-"

        row = [rank, r.target.provider, r.target.display_name, tok_s]
        if show_std:
            std = f"{r.std_dev_tps:.1f}" if r.std_dev_tps > 0 else "-"
            row.append(std)
        if show_input_tps:
            in_tps = f"{r.avg_input_tps:.0f}" if r.avg_input_tps > 0 else "-"
            row.append(in_tps)
        row.extend([ttft, total, tokens])
        if show_cost:
            cost = f"{r.avg_cost:.6f}" if r.avg_cost > 0 else "-"
            row.append(cost)
        row.append(status)

        table.add_row(*row)

    console.print()
    console.print(table)

    # Winner line
    if sorted_results and sorted_results[0].avg_tokens_per_second > 0:
        w = sorted_results[0]
        console.print(
            f"\n  \U0001f3c6 [bold]Winner:[/bold] {w.target.provider} / "
            f"{w.target.display_name} at "
            f"[bold green]{w.avg_tokens_per_second:.1f} tok/s[/bold green]\n"
        )


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

def save_results(
    results: list[AggregatedResult], prompt: str, output_dir: str = "results",
    context_tiers: list[int] | None = None,
) -> Path:
    """Save results to a timestamped JSON file."""
    Path(output_dir).mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = Path(output_dir) / f"benchmark_{timestamp}.json"

    data = {
        "schema_version": 2,
        "timestamp": datetime.now().isoformat(),
        "prompt": prompt[:200],
        "context_tiers": context_tiers or [0],
        "results": [
            {
                "provider": r.target.provider,
                "model": r.target.display_name,
                "model_id": r.target.model_id,
                "context_tokens": r.all_results[0].context_tokens if r.all_results else 0,
                "avg_tokens_per_second": round(r.avg_tokens_per_second, 2),
                "avg_ttft_ms": round(r.avg_ttft_ms, 2),
                "avg_total_time_s": round(r.avg_total_time_s, 3),
                "avg_output_tokens": round(r.avg_output_tokens),
                "std_dev_tps": round(r.std_dev_tps, 2),
                "min_tps": round(r.min_tps, 2),
                "max_tps": round(r.max_tps, 2),
                "p50_tps": round(r.p50_tps, 2),
                "p95_tps": round(r.p95_tps, 2),
                "std_dev_ttft": round(r.std_dev_ttft, 2),
                "outlier_count": r.outlier_count,
                "avg_cost": round(r.avg_cost, 8),
                "total_cost": round(r.total_cost, 8),
                "avg_input_tps": round(r.avg_input_tps, 2),
                "runs": r.runs,
                "failures": r.failures,
                "error": next((rr.error for rr in r.all_results if not rr.success), ""),
                "runs_detail": [
                    {
                        "tokens_per_second": round(rr.tokens_per_second, 2),
                        "ttft_ms": round(rr.ttft_ms, 2),
                        "total_time_s": round(rr.total_time_s, 3),
                        "output_tokens": rr.output_tokens,
                        "input_tokens_per_second": round(rr.input_tokens_per_second, 2),
                        "cost": round(rr.cost, 8),
                        "success": rr.success,
                    }
                    for rr in r.all_results
                ],
            }
            for r in results
        ],
    }

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    console.print(f"  \U0001f4be Results saved to [bold]{filepath}[/bold]")
    return filepath


# ---------------------------------------------------------------------------
# Remote benchmark (CLI -> server API via SSE)
# ---------------------------------------------------------------------------

def run_remote_benchmark(
    server_url: str,
    token: str,
    model_ids: list[str],
    runs: int,
    max_tokens: int,
    temperature: float,
    prompt: str,
    context_tiers: list[int],
):
    """Run benchmark via server API with JWT auth, streaming results via SSE."""
    import httpx

    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "models": model_ids,
        "runs": runs,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "prompt": prompt,
        "context_tiers": context_tiers,
    }

    console.print(f"  [dim]Connecting to {server_url}...[/dim]")

    try:
        with httpx.Client(timeout=httpx.Timeout(5.0, read=300.0)) as client:
            # Validate auth by fetching config
            config_resp = client.get(f"{server_url}/api/config", headers=headers)
            if config_resp.status_code == 401:
                console.print("[red]Authentication failed. Check your token.[/red]")
                return
            if config_resp.status_code != 200:
                console.print(f"[red]Server error ({config_resp.status_code}) fetching config.[/red]")
                return

            console.print(f"  [dim]Authenticated. Starting remote benchmark...[/dim]\n")

            # Start benchmark via SSE stream
            with client.stream(
                "POST",
                f"{server_url}/api/benchmark",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    console.print(f"[red]Server error: {response.status_code}[/red]")
                    return

                results = []
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = json.loads(line[6:])

                    if data["type"] == "progress":
                        console.print(
                            f"  [{data['current']}/{data['total']}] {data['provider']} / "
                            f"{data['model']} (run {data['run']}/{data['runs']})...",
                            end=" ",
                        )
                    elif data["type"] == "result":
                        if data["success"]:
                            console.print(
                                f"[green]OK[/green] {data['tokens_per_second']:.1f} tok/s"
                            )
                        else:
                            console.print(f"[red]FAIL[/red] {data.get('error', '')[:80]}")
                        results.append(data)
                    elif data["type"] == "complete":
                        saved_to = data.get("saved_to", "")
                        if saved_to:
                            console.print(f"\n  Saved to: {saved_to}")
                    elif data["type"] == "error":
                        console.print(f"[red]Error: {data['message']}[/red]")
                    elif data["type"] == "cancelled":
                        console.print("[yellow]Benchmark cancelled by server.[/yellow]")

        console.print(f"\n  [green]Remote benchmark complete. {len(results)} results received.[/green]")

    except httpx.ConnectError:
        console.print(f"[red]Could not connect to {server_url}. Is the server running?[/red]")
    except httpx.ReadTimeout:
        console.print("[red]Server read timeout. The benchmark may still be running on the server.[/red]")
    except Exception as e:
        console.print(f"[red]Remote benchmark error: {e}[/red]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LLM Benchmark - Measure token/second across providers via LiteLLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python benchmark.py                          # Run all benchmarks
  python benchmark.py --runs 3                 # Average over 3 runs
  python benchmark.py --provider openai        # Only OpenAI models
  python benchmark.py --model GLM              # Only models matching 'GLM'
  python benchmark.py --prompt "Write a poem"  # Custom prompt
  python benchmark.py --no-save                # Don't save results to file
        """,
    )
    parser.add_argument("--config", default="config.yaml", help="Config file path (default: config.yaml)")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per model (default: 3)")
    parser.add_argument("--provider", help="Filter by provider name (substring match)")
    parser.add_argument("--model", help="Filter by model name (substring match)")
    parser.add_argument("--prompt", help="Override default benchmark prompt")
    parser.add_argument("--max-tokens", type=int, help="Override max output tokens")
    parser.add_argument("--temperature", type=float, help="Override temperature")
    parser.add_argument("--no-save", action="store_true", help="Don't save results to JSON")
    parser.add_argument("--verbose", action="store_true", help="Show LiteLLM debug output")
    parser.add_argument("--context-tiers", help="Comma-separated context token tiers (e.g., 0,1000,5000)")
    parser.add_argument("--no-warmup", action="store_true", help="Skip warm-up run before measured runs")
    parser.add_argument("--token", help="JWT token for remote API mode (delegates to server)")
    parser.add_argument("--server", default="http://localhost:8501", help="Server URL for remote mode (default: http://localhost:8501)")
    args = parser.parse_args()

    # Load .env (keys)
    script_dir = Path(__file__).parent
    load_dotenv(script_dir / ".env", override=True)

    # LiteLLM verbosity
    if not args.verbose:
        litellm.suppress_debug_info = True
        litellm.set_verbose = False

    # Find config file: CLI arg > same dir as script
    config_path = Path(args.config)
    if not config_path.exists():
        config_path = script_dir / args.config
    if not config_path.exists():
        console.print(f"[red]Config not found: {args.config}[/red]")
        return

    config = load_config(str(config_path))
    defaults = config.get("defaults", {})

    # Build targets
    targets = build_targets(config, args.provider, args.model)
    if not targets:
        console.print("[yellow]No matching targets. Check config and filters.[/yellow]")
        return

    # Resolve parameters
    prompt = args.prompt or defaults.get("prompt", "Explain recursion in programming with a Python example.")
    max_tokens = args.max_tokens or defaults.get("max_tokens", 512)
    temperature = args.temperature if args.temperature is not None else defaults.get("temperature", 0.7)
    runs = args.runs

    context_tiers = None
    if args.context_tiers:
        context_tiers = [int(x.strip()) for x in args.context_tiers.split(",")]

    # Remote mode: delegate to server API when --token is provided
    if args.token:
        model_ids = [t.model_id for t in targets]
        run_remote_benchmark(
            server_url=args.server,
            token=args.token,
            model_ids=model_ids,
            runs=runs,
            max_tokens=max_tokens,
            temperature=temperature,
            prompt=prompt,
            context_tiers=context_tiers or [0],
        )
        return

    # Header
    console.print(
        Panel(
            f"[bold]Targets:[/bold] {len(targets)} models  |  "
            f"[bold]Runs:[/bold] {runs}  |  "
            f"[bold]Max tokens:[/bold] {max_tokens}  |  "
            f"[bold]Temp:[/bold] {temperature}",
            title="\U0001f680 LLM Benchmark",
            border_style="cyan",
        )
    )
    prompt_preview = prompt.strip().replace("\n", " ")[:80]
    console.print(f"  [dim]Prompt: {prompt_preview}{'...' if len(prompt.strip()) > 80 else ''}[/dim]\n")

    # Run
    results = run_benchmarks(targets, prompt, runs, max_tokens, temperature,
                             context_tiers=context_tiers, warmup=not args.no_warmup)

    # Display
    display_results(results)

    # Save
    if not args.no_save:
        save_results(results, prompt, context_tiers=context_tiers)


if __name__ == "__main__":
    main()

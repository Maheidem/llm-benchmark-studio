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
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import tiktoken

import litellm
import yaml
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


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load benchmark configuration from YAML file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


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
                )
            )

    return targets


# ---------------------------------------------------------------------------
# Benchmark execution
# ---------------------------------------------------------------------------

def generate_context_text(target_tokens: int) -> str:
    """Generate realistic filler text sized to approximately target_tokens."""
    if target_tokens <= 0:
        return ""

    paragraph = (
        "Artificial intelligence continues to reshape industries across the global economy. "
        "Modern language models leverage transformer architectures with attention mechanisms "
        "that enable contextual understanding of text at unprecedented scale. These systems "
        "are trained on diverse corpora spanning scientific literature, software documentation, "
        "news articles, and conversational data. The resulting models demonstrate emergent "
        "capabilities in reasoning, code generation, summarization, and creative writing. "
        "Organizations are deploying these models for customer support automation, content "
        "creation, data analysis pipelines, and research assistance. Fine-tuning techniques "
        "such as reinforcement learning from human feedback have proven effective at aligning "
        "model outputs with human preferences and safety requirements. Infrastructure costs "
        "remain a significant consideration, with GPU clusters consuming substantial energy "
        "during both training and inference phases. Quantization methods and mixture-of-experts "
        "architectures offer promising approaches to reducing computational requirements while "
        "maintaining output quality. The field advances rapidly, with new architectures and "
        "training methodologies published weekly in academic venues and industry research labs. "
    )

    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        # Fallback: rough estimate of 1 token ~ 4 chars
        repeats = max(1, target_tokens * 4 // len(paragraph) + 1)
        text = (paragraph + "\n") * repeats
        return text[: target_tokens * 4]

    para_tokens = len(enc.encode(paragraph))
    repeats = max(1, target_tokens // para_tokens + 1)
    text = (paragraph + "\n") * repeats
    tokens = enc.encode(text)
    return enc.decode(tokens[:target_tokens])


def run_single(
    target: Target, prompt: str, max_tokens: int, temperature: float,
    context_tokens: int = 0,
) -> RunResult:
    """Execute a single streaming benchmark run against one model."""
    result = RunResult(target=target, context_tokens=context_tokens)

    messages = []
    if context_tokens > 0:
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

    except Exception as e:
        result.success = False
        result.error = str(e)[:200]

    return result


def run_benchmarks(
    targets: list[Target],
    prompt: str,
    runs: int,
    max_tokens: int,
    temperature: float,
    context_tiers: list[int] | None = None,
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
    table.add_column("TTFT (ms)", justify="right", min_width=9)
    table.add_column("Total (s)", justify="right", min_width=9)
    table.add_column("Tokens", justify="right", min_width=7)
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

        table.add_row(rank, r.target.provider, r.target.display_name, tok_s, ttft, total, tokens, status)

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
                "runs": r.runs,
                "failures": r.failures,
                "error": next((rr.error for rr in r.all_results if not rr.success), ""),
            }
            for r in results
        ],
    }

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    console.print(f"  \U0001f4be Results saved to [bold]{filepath}[/bold]")
    return filepath


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
    parser.add_argument("--runs", type=int, default=1, help="Number of runs per model (default: 1)")
    parser.add_argument("--provider", help="Filter by provider name (substring match)")
    parser.add_argument("--model", help="Filter by model name (substring match)")
    parser.add_argument("--prompt", help="Override default benchmark prompt")
    parser.add_argument("--max-tokens", type=int, help="Override max output tokens")
    parser.add_argument("--temperature", type=float, help="Override temperature")
    parser.add_argument("--no-save", action="store_true", help="Don't save results to JSON")
    parser.add_argument("--verbose", action="store_true", help="Show LiteLLM debug output")
    parser.add_argument("--context-tiers", help="Comma-separated context token tiers (e.g., 0,1000,5000)")
    args = parser.parse_args()

    # Load .env (keys)
    script_dir = Path(__file__).parent
    load_dotenv(script_dir / ".env")

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
    results = run_benchmarks(targets, prompt, runs, max_tokens, temperature, context_tiers=context_tiers)

    # Display
    display_results(results)

    # Save
    if not args.no_save:
        save_results(results, prompt, context_tiers=context_tiers)


if __name__ == "__main__":
    main()

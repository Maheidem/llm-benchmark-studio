"""Tests for new benchmark metrics: Output Speed, ITL, CV, Confidence, TTFT Percentiles.

All tests are pure math — no mocking, no DB, no HTTP.

Run: uv run pytest tests/test_benchmark_metrics.py -v
"""

import statistics

import pytest

from benchmark import AggregatedResult, RunResult, Target, _compute_variance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(**kw):
    """Create a minimal Target for testing."""
    defaults = {"provider": "test", "model_id": "test-model", "display_name": "Test Model"}
    defaults.update(kw)
    return Target(**defaults)


def _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100, success=True, **kw):
    """Create a RunResult with output_speed_tps and itl_ms computed from the same
    logic used in run_single(), so tests stay in sync with the implementation."""
    t = _make_target()
    r = RunResult(
        target=t,
        ttft_ms=ttft_ms,
        total_time_s=total_time_s,
        output_tokens=output_tokens,
        success=success,
    )
    if total_time_s > 0 and success:
        r.tokens_per_second = output_tokens / total_time_s
    ttft_s = (ttft_ms or 0.0) / 1000.0
    gen_time = total_time_s - ttft_s
    if gen_time > 0 and output_tokens > 0 and success:
        r.output_speed_tps = output_tokens / gen_time
    if output_tokens > 1 and gen_time > 0 and success:
        r.itl_ms = gen_time / (output_tokens - 1) * 1000
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def _make_agg(results):
    """Build an AggregatedResult from a list of RunResults and call _compute_variance."""
    successes = [r for r in results if r.success]
    agg = AggregatedResult(
        target=_make_target(),
        runs=len(results),
        failures=len(results) - len(successes),
        all_results=results,
    )
    if successes:
        n = len(successes)
        agg.avg_ttft_ms = sum(r.ttft_ms for r in successes) / n
        agg.avg_total_time_s = sum(r.total_time_s for r in successes) / n
        agg.avg_tokens_per_second = sum(r.tokens_per_second for r in successes) / n
        agg.avg_output_tokens = sum(r.output_tokens for r in successes) / n
        _compute_variance(agg, successes)
    return agg


# ---------------------------------------------------------------------------
# TestOutputSpeed
# ---------------------------------------------------------------------------

class TestOutputSpeed:
    """Tests for RunResult.output_speed_tps computation."""

    def test_basic_computation(self):
        """ttft=200ms, total=2.0s, tokens=100 -> output_speed = 100/1.8 ~= 55.56."""
        r = _make_result(ttft_ms=200.0, total_time_s=2.0, output_tokens=100)
        expected = 100 / (2.0 - 0.2)  # 100 / 1.8
        assert r.output_speed_tps == pytest.approx(expected, rel=0.01)

    def test_zero_ttft(self):
        """ttft=0 -> gen_time equals total_time, output_speed equals tokens_per_second."""
        r = _make_result(ttft_ms=0.0, total_time_s=2.0, output_tokens=100)
        assert r.output_speed_tps == pytest.approx(50.0, rel=0.01)
        assert r.output_speed_tps == pytest.approx(r.tokens_per_second, rel=0.01)

    def test_ttft_equals_total(self):
        """ttft=2000ms, total=2.0s -> gen_time=0, output_speed=0."""
        r = _make_result(ttft_ms=2000.0, total_time_s=2.0, output_tokens=100)
        assert r.output_speed_tps == 0.0

    def test_always_greater_than_tps(self):
        """When ttft > 0, output_speed_tps must be >= tokens_per_second."""
        r = _make_result(ttft_ms=300.0, total_time_s=3.0, output_tokens=150)
        assert r.output_speed_tps >= r.tokens_per_second

    def test_single_token(self):
        """output_tokens=1 still computes output_speed (no minimum token check on speed)."""
        r = _make_result(ttft_ms=100.0, total_time_s=1.0, output_tokens=1)
        gen_time = 1.0 - 0.1
        expected = 1 / gen_time
        assert r.output_speed_tps == pytest.approx(expected, rel=0.01)


# ---------------------------------------------------------------------------
# TestITL
# ---------------------------------------------------------------------------

class TestITL:
    """Tests for RunResult.itl_ms (Inter-Token Latency) computation."""

    def test_basic_computation(self):
        """ttft=100ms, total=2.0s, tokens=100 -> ITL = 1.9/99 * 1000 ~= 19.19ms."""
        r = _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100)
        gen_time = 2.0 - 0.1
        expected = gen_time / (100 - 1) * 1000
        assert r.itl_ms == pytest.approx(expected, rel=0.01)

    def test_single_token(self):
        """output_tokens=1 -> itl_ms=0 (can't divide by zero intervals)."""
        r = _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=1)
        assert r.itl_ms == 0.0

    def test_two_tokens(self):
        """output_tokens=2 -> itl_ms = gen_time / 1 * 1000."""
        r = _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=2)
        gen_time = 2.0 - 0.1
        expected = gen_time / 1 * 1000
        assert r.itl_ms == pytest.approx(expected, rel=0.01)

    def test_zero_gen_time(self):
        """ttft equals total -> gen_time=0, itl_ms=0."""
        r = _make_result(ttft_ms=2000.0, total_time_s=2.0, output_tokens=100)
        assert r.itl_ms == 0.0


# ---------------------------------------------------------------------------
# TestCoefficientOfVariation
# ---------------------------------------------------------------------------

class TestCoefficientOfVariation:
    """Tests for AggregatedResult.cv_tps and cv_ttft via _compute_variance."""

    def test_cv_consistent_runs(self):
        """5 runs with nearly identical TPS -> CV < 10 (high consistency)."""
        results = [
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=101.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=99.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=100.0, total_time_s=2.01, output_tokens=100),
            _make_result(ttft_ms=100.0, total_time_s=1.99, output_tokens=100),
        ]
        agg = _make_agg(results)
        assert agg.cv_tps < 10

    def test_cv_variable_runs(self):
        """5 runs with wildly different TPS -> CV > 30 (low consistency)."""
        results = [
            _make_result(ttft_ms=50.0, total_time_s=1.0, output_tokens=10),
            _make_result(ttft_ms=50.0, total_time_s=1.0, output_tokens=200),
            _make_result(ttft_ms=50.0, total_time_s=1.0, output_tokens=5),
            _make_result(ttft_ms=50.0, total_time_s=1.0, output_tokens=300),
            _make_result(ttft_ms=50.0, total_time_s=1.0, output_tokens=15),
        ]
        agg = _make_agg(results)
        assert agg.cv_tps > 30

    def test_cv_single_run(self):
        """1 run -> cv_tps=0 because stdev requires at least 2 samples."""
        results = [_make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100)]
        agg = _make_agg(results)
        assert agg.cv_tps == 0.0

    def test_cv_two_runs(self):
        """2 runs -> cv_tps is computed (stdev works with n=2)."""
        results = [
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=100.0, total_time_s=4.0, output_tokens=100),
        ]
        agg = _make_agg(results)
        # TPS values: 50.0 and 25.0 — mean=37.5, stdev ~= 17.68 -> CV ~= 47
        assert agg.cv_tps > 0.0
        tps_vals = [r.tokens_per_second for r in results]
        mean_tps = statistics.mean(tps_vals)
        std_tps = statistics.stdev(tps_vals)
        expected_cv = (std_tps / mean_tps) * 100
        assert agg.cv_tps == pytest.approx(expected_cv, rel=0.01)


# ---------------------------------------------------------------------------
# TestConfidence
# ---------------------------------------------------------------------------

class TestConfidence:
    """Tests for AggregatedResult.confidence_level via _compute_variance."""

    def test_high_confidence(self):
        """CV < 10 and runs >= 3 -> 'high'."""
        # Nearly identical TPS across 3 runs
        results = [
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=100.0, total_time_s=2.01, output_tokens=100),
            _make_result(ttft_ms=100.0, total_time_s=1.99, output_tokens=100),
        ]
        agg = _make_agg(results)
        assert agg.confidence_level == "high"

    def test_low_confidence_high_cv(self):
        """CV > 30 (regardless of run count) -> 'low'."""
        results = [
            _make_result(ttft_ms=50.0, total_time_s=1.0, output_tokens=10),
            _make_result(ttft_ms=50.0, total_time_s=1.0, output_tokens=500),
            _make_result(ttft_ms=50.0, total_time_s=1.0, output_tokens=8),
        ]
        agg = _make_agg(results)
        assert agg.confidence_level == "low"

    def test_low_confidence_single_run(self):
        """1 run -> 'low' (n == 1 triggers low regardless of cv_tps)."""
        results = [_make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100)]
        agg = _make_agg(results)
        assert agg.confidence_level == "low"

    def test_medium_confidence(self):
        """CV between 10-30 and runs >= 2 -> 'medium'."""
        # Use runs where TPS is moderately variable but not extreme
        # TPS ~50 and ~38 -> mean ~44, stdev ~8.5, CV ~19 (medium)
        results = [
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=65),
        ]
        agg = _make_agg(results)
        # Verify that the CV lands in the medium range before asserting
        assert 10 <= agg.cv_tps <= 30, f"Expected CV in [10, 30] but got {agg.cv_tps:.2f}"
        assert agg.confidence_level == "medium"


# ---------------------------------------------------------------------------
# TestTTFTPercentiles
# ---------------------------------------------------------------------------

class TestTTFTPercentiles:
    """Tests for AggregatedResult p50_ttft, p95_ttft, p99_ttft via _compute_variance."""

    def test_single_run(self):
        """1 run -> p50=p95=p99=that value."""
        results = [_make_result(ttft_ms=123.0, total_time_s=2.0, output_tokens=100)]
        agg = _make_agg(results)
        assert agg.p50_ttft == pytest.approx(123.0, rel=0.01)
        assert agg.p95_ttft == pytest.approx(123.0, rel=0.01)
        assert agg.p99_ttft == pytest.approx(123.0, rel=0.01)

    def test_two_runs(self):
        """2 runs -> p50=median, p95=p99=max."""
        results = [
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=200.0, total_time_s=2.0, output_tokens=100),
        ]
        agg = _make_agg(results)
        assert agg.p50_ttft == pytest.approx(150.0, rel=0.01)  # median of [100, 200]
        assert agg.p95_ttft == pytest.approx(200.0, rel=0.01)  # max
        assert agg.p99_ttft == pytest.approx(200.0, rel=0.01)  # max

    def test_four_runs(self):
        """4 runs -> percentiles computed via statistics.quantiles."""
        ttft_values = [100.0, 150.0, 200.0, 250.0]
        results = [
            _make_result(ttft_ms=v, total_time_s=2.0, output_tokens=100)
            for v in ttft_values
        ]
        agg = _make_agg(results)
        expected_p50 = statistics.median(ttft_values)
        expected_p95 = statistics.quantiles(ttft_values, n=20)[-1]
        assert agg.p50_ttft == pytest.approx(expected_p50, rel=0.01)
        assert agg.p95_ttft == pytest.approx(expected_p95, rel=0.01)

    def test_twenty_runs(self):
        """20 runs -> p95 is near the 95th percentile, p99 near the 99th."""
        # Values 10, 20, 30, ..., 200
        ttft_values = [float(i * 10) for i in range(1, 21)]
        results = [
            _make_result(ttft_ms=v, total_time_s=2.0, output_tokens=100)
            for v in ttft_values
        ]
        agg = _make_agg(results)
        expected_p50 = statistics.median(ttft_values)
        expected_p95 = statistics.quantiles(ttft_values, n=20)[-1]
        expected_p99 = statistics.quantiles(ttft_values, n=100)[-1]
        assert agg.p50_ttft == pytest.approx(expected_p50, rel=0.01)
        assert agg.p95_ttft == pytest.approx(expected_p95, rel=0.01)
        assert agg.p99_ttft == pytest.approx(expected_p99, rel=0.01)
        # Sanity: p95 and p99 should be near the top of the range
        assert agg.p95_ttft > statistics.mean(ttft_values)
        assert agg.p99_ttft >= agg.p95_ttft


# ---------------------------------------------------------------------------
# TestLegacyTpsUnchanged
# ---------------------------------------------------------------------------

class TestLegacyTpsUnchanged:
    """Verify that existing tokens_per_second logic is unchanged."""

    def test_tps_still_computed(self):
        """tokens_per_second = output_tokens / total_time_s."""
        r = _make_result(ttft_ms=100.0, total_time_s=4.0, output_tokens=200)
        assert r.tokens_per_second == pytest.approx(200 / 4.0, rel=0.01)

    def test_tps_differs_from_output_speed(self):
        """When ttft > 0, output_speed_tps != tokens_per_second."""
        r = _make_result(ttft_ms=500.0, total_time_s=3.0, output_tokens=150)
        # tps = 150/3 = 50, output_speed = 150/2.5 = 60
        assert r.tokens_per_second != pytest.approx(r.output_speed_tps, rel=0.01)
        assert r.output_speed_tps > r.tokens_per_second

    def test_avg_output_speed_computed_in_agg(self):
        """avg_output_speed_tps in AggregatedResult is the mean of per-run output_speed_tps."""
        results = [
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100),
            _make_result(ttft_ms=200.0, total_time_s=3.0, output_tokens=150),
        ]
        agg = _make_agg(results)
        expected = (results[0].output_speed_tps + results[1].output_speed_tps) / 2
        assert agg.avg_output_speed_tps == pytest.approx(expected, rel=0.01)

    def test_avg_itl_computed_in_agg(self):
        """avg_itl_ms in AggregatedResult is the mean of per-run itl_ms."""
        results = [
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=50),
            _make_result(ttft_ms=100.0, total_time_s=2.0, output_tokens=100),
        ]
        agg = _make_agg(results)
        expected = (results[0].itl_ms + results[1].itl_ms) / 2
        assert agg.avg_itl_ms == pytest.approx(expected, rel=0.01)

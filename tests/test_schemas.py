"""Tests for Pydantic request/response schemas."""
import pytest
from pydantic import ValidationError
from schemas import (
    SAFE_ENV_VARS,
    BenchmarkRequest, ModelConfigUpdate, ToolSuiteCreate, ToolSuiteUpdate,
    TestCaseCreate, ToolEvalRequest, ParamTuneRequest, PromptTuneRequest,
    JudgeRequest, JudgeCompareRequest, ScheduleCreate, ScheduleUpdate,
    ExperimentCreate, ExperimentUpdate, EnvVarUpdate, ProviderCreate,
    RateLimitUpdate, PasswordChange, ApiKeyUpdate,
    ErrorResponse, SuccessResponse, JobCreatedResponse,
)


# ──────────── BenchmarkRequest ────────────

class TestBenchmarkRequest:
    def test_valid_minimal(self):
        r = BenchmarkRequest(models=["gpt-4"], prompt="Hello")
        assert r.models == ["gpt-4"]
        assert r.max_tokens == 512
        assert r.temperature == 0.7
        assert r.runs == 1

    def test_valid_full(self):
        r = BenchmarkRequest(
            models=["gpt-4", "claude-3"],
            prompt="Test prompt",
            max_tokens=1024,
            temperature=1.5,
            context_tiers=[0, 5000],
            runs=5,
        )
        assert r.runs == 5

    def test_empty_models_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkRequest(models=[], prompt="Hello")

    def test_negative_max_tokens_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkRequest(models=["gpt-4"], prompt="Hello", max_tokens=-1)

    def test_temperature_out_of_range(self):
        with pytest.raises(ValidationError):
            BenchmarkRequest(models=["gpt-4"], prompt="Hello", temperature=3.0)

    def test_runs_too_high(self):
        with pytest.raises(ValidationError):
            BenchmarkRequest(models=["gpt-4"], prompt="Hello", runs=99)


# ──────────── ModelConfigUpdate ────────────

class TestModelConfigUpdate:
    def test_valid(self):
        r = ModelConfigUpdate(model_id="gpt-4", provider_key="openai")
        assert r.model_id == "gpt-4"

    def test_model_id_with_slash(self):
        r = ModelConfigUpdate(model_id="anthropic/claude-3", provider_key="anthropic")
        assert "/" in r.model_id

    def test_invalid_model_id_chars(self):
        with pytest.raises(ValidationError):
            ModelConfigUpdate(model_id="gpt 4!!", provider_key="openai")

    def test_context_window_bounds(self):
        with pytest.raises(ValidationError):
            ModelConfigUpdate(model_id="gpt-4", provider_key="openai", context_window=0)
        with pytest.raises(ValidationError):
            ModelConfigUpdate(model_id="gpt-4", provider_key="openai", context_window=3_000_000)


# ──────────── ToolSuiteCreate ────────────

class TestToolSuiteCreate:
    def test_valid(self):
        r = ToolSuiteCreate(name="My Suite", tools_json=[{"name": "get_weather"}])
        assert r.name == "My Suite"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            ToolSuiteCreate(name="", tools_json=[{"name": "t"}])

    def test_name_too_long(self):
        with pytest.raises(ValidationError):
            ToolSuiteCreate(name="x" * 257, tools_json=[{"name": "t"}])


# ──────────── EnvVarUpdate ────────────

class TestEnvVarUpdate:
    def test_valid_openai_key(self):
        r = EnvVarUpdate(name="OPENAI_API_KEY", value="sk-test123")
        assert r.name == "OPENAI_API_KEY"

    def test_fernet_key_blocked(self):
        with pytest.raises(ValidationError, match="Cannot modify"):
            EnvVarUpdate(name="FERNET_KEY", value="bad")

    def test_jwt_secret_blocked(self):
        with pytest.raises(ValidationError, match="Cannot modify"):
            EnvVarUpdate(name="JWT_SECRET", value="bad")

    def test_admin_email_blocked(self):
        with pytest.raises(ValidationError, match="Cannot modify"):
            EnvVarUpdate(name="ADMIN_EMAIL", value="bad")

    def test_all_safe_vars_accepted(self):
        for var in SAFE_ENV_VARS:
            r = EnvVarUpdate(name=var, value="test-value")
            assert r.name == var

    def test_invalid_name_pattern(self):
        with pytest.raises(ValidationError):
            EnvVarUpdate(name="123BAD", value="test")


# ──────────── ScheduleCreate ────────────

class TestScheduleCreate:
    def test_valid(self):
        r = ScheduleCreate(name="Daily", prompt="Test", models_json=["gpt-4"], interval_hours=24)
        assert r.interval_hours == 24

    def test_interval_too_high(self):
        with pytest.raises(ValidationError):
            ScheduleCreate(name="Bad", prompt="T", models_json=["m"], interval_hours=999)


# ──────────── RateLimitUpdate ────────────

class TestRateLimitUpdate:
    def test_valid(self):
        r = RateLimitUpdate(benchmarks_per_hour=20, max_concurrent=3, max_runs_per_benchmark=5)
        assert r.max_concurrent == 3

    def test_zero_rejected(self):
        with pytest.raises(ValidationError):
            RateLimitUpdate(benchmarks_per_hour=0, max_concurrent=1, max_runs_per_benchmark=1)


# ──────────── PasswordChange ────────────

class TestPasswordChange:
    def test_valid(self):
        r = PasswordChange(current_password="old123", new_password="newpass123")
        assert r.new_password == "newpass123"

    def test_short_password_rejected(self):
        with pytest.raises(ValidationError):
            PasswordChange(current_password="old", new_password="short")


# ──────────── Response Schemas ────────────

class TestResponses:
    def test_success(self):
        r = SuccessResponse()
        assert r.status == "ok"

    def test_error(self):
        r = ErrorResponse(detail="Something went wrong")
        assert r.detail == "Something went wrong"

    def test_job_created(self):
        r = JobCreatedResponse(job_id="abc-123")
        assert r.status == "pending"

"""Pydantic request/response schemas for LLM Benchmark Studio API."""
from __future__ import annotations
from typing import Optional, List, Literal, Any
from pydantic import BaseModel, Field, field_validator, model_validator


# ──────────────────────── Constants ────────────────────────

SAFE_ENV_VARS: set[str] = {
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "MISTRAL_API_KEY", "COHERE_API_KEY", "GROQ_API_KEY",
    "DEEPSEEK_API_KEY", "TOGETHER_API_KEY", "FIREWORKS_API_KEY",
    "XAI_API_KEY", "DEEPINFRA_API_KEY", "CEREBRAS_API_KEY",
    "SAMBANOVA_API_KEY", "OPENROUTER_API_KEY",
}


# ──────────────────── Request Schemas ──────────────────────

class BenchmarkRequest(BaseModel):
    models: Optional[List[str]] = Field(default=None)
    targets: Optional[List[dict]] = Field(default=None)
    prompt: str = Field(default="", max_length=500_000)
    max_tokens: int = Field(default=512, ge=1, le=128_000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    context_tiers: List[int] = Field(default_factory=lambda: [0])
    runs: int = Field(default=1, ge=1, le=20)
    profiles: Optional[dict] = None  # {"model_id": "profile_id"}

    @model_validator(mode="after")
    def check_models_or_targets(self):
        """At least one of models or targets must be provided with items."""
        has_models = self.models and len(self.models) > 0
        has_targets = self.targets and len(self.targets) > 0
        if not has_models and not has_targets:
            raise ValueError("Either 'models' or 'targets' must be provided with at least one item")
        return self


class ModelConfigUpdate(BaseModel):
    model_id: str = Field(..., pattern=r"^[a-zA-Z0-9._\-/:]+$")
    provider_key: str
    display_name: Optional[str] = Field(None, max_length=256)
    context_window: Optional[int] = Field(None, ge=1, le=2_000_000)
    max_output_tokens: Optional[int] = Field(None, ge=1, le=128_000)
    skip_temperature: Optional[bool] = None
    input_cost_per_mtok: Optional[float] = Field(None, ge=0)
    output_cost_per_mtok: Optional[float] = Field(None, ge=0)
    system_prompt: Optional[str] = Field(None, max_length=50_000)


class ToolSuiteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: Optional[str] = Field(None, max_length=5_000)
    tools_json: List[dict] = Field(...)
    system_prompt: Optional[str] = Field(None, max_length=50_000)


class ToolSuiteUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=256)
    description: Optional[str] = Field(None, max_length=5_000)
    tools_json: Optional[List[dict]] = None
    system_prompt: Optional[str] = Field(None, max_length=50_000)


class TestCaseCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=50_000)
    expected_tool: Optional[str] = Field(None, max_length=256)
    expected_params: Optional[dict] = None
    param_scoring: str = Field(default="exact", pattern=r"^(exact|fuzzy|contains|semantic)$")
    multi_turn_config: Optional[dict] = None
    scoring_config_json: Optional[dict] = None


class ToolEvalRequest(BaseModel):
    suite_id: str = Field(..., min_length=1)
    models: Optional[List[str]] = Field(default=None)
    targets: Optional[List[dict]] = Field(default=None)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    system_prompt: Optional[Any] = None  # str or dict (per-model prompts)
    experiment_id: Optional[str] = None
    profiles: Optional[dict] = None  # {"model_id": "profile_id"}
    auto_judge: bool = False
    auto_judge_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_models_or_targets(self):
        has_models = self.models and len(self.models) > 0
        has_targets = self.targets and len(self.targets) > 0
        if not has_models and not has_targets:
            raise ValueError("Either 'models' or 'targets' must be provided with at least one item")
        return self


class ParamTuneRequest(BaseModel):
    suite_id: str = Field(..., min_length=1)
    models: Optional[List[str]] = Field(default=None)
    targets: Optional[List[dict]] = Field(default=None)
    search_space: dict
    experiment_id: Optional[str] = None

    @model_validator(mode="after")
    def check_models_or_targets(self):
        has_models = self.models and len(self.models) > 0
        has_targets = self.targets and len(self.targets) > 0
        if not has_models and not has_targets:
            raise ValueError("Either 'models' or 'targets' must be provided with at least one item")
        return self


class PromptTuneRequest(BaseModel):
    suite_id: str = Field(..., min_length=1)
    mode: Literal["quick", "evolutionary"]
    target_models: List[str] = Field(..., min_length=1)
    meta_model: str = Field(..., min_length=1)
    base_prompt: Optional[str] = Field(None, max_length=50_000)
    config: Optional[dict] = None
    experiment_id: Optional[str] = None


class JudgeRequest(BaseModel):
    eval_run_id: str = Field(..., min_length=1)
    judge_model: str = Field(..., min_length=1)
    mode: Literal["post_eval", "live_inline"] = "post_eval"
    experiment_id: Optional[str] = None
    tune_run_id: Optional[str] = None
    tune_type: Optional[Literal["param_tuner", "prompt_tuner"]] = None

    @model_validator(mode="after")
    def check_tune_fields(self):
        """If tune_type is provided, tune_run_id must also be provided."""
        if self.tune_type and not self.tune_run_id:
            raise ValueError("tune_run_id is required when tune_type is provided")
        return self


class JudgeCompareRequest(BaseModel):
    eval_run_id_a: str = Field(..., min_length=1)
    eval_run_id_b: str = Field(..., min_length=1)
    judge_model: str = Field(..., min_length=1)
    experiment_id: Optional[str] = None


class JudgeRerunRequest(BaseModel):
    parent_report_id: str = Field(..., min_length=1)
    judge_model: Optional[str] = None  # If None, reuse parent's model
    judge_provider_key: Optional[str] = None
    custom_instructions: Optional[str] = Field(None, max_length=10_000)
    score_override_enabled: bool = True
    concurrency: int = Field(default=4, ge=1, le=20)


class JudgeSettingsUpdate(BaseModel):
    default_judge_model: Optional[str] = Field(None, max_length=256)
    default_judge_provider_key: Optional[str] = Field(None, max_length=64)
    default_mode: Optional[Literal["post_eval", "live_inline"]] = None
    custom_instructions_template: Optional[str] = Field(None, max_length=10_000)
    score_override_policy: Optional[Literal["always_allow", "require_confirmation", "never"]] = None
    auto_judge_after_eval: Optional[bool] = None
    concurrency: Optional[int] = Field(None, ge=1, le=20)


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    prompt: str = Field(..., min_length=1, max_length=500_000)
    models_json: List[str] = Field(..., min_length=1)
    max_tokens: int = Field(default=512, ge=1, le=128_000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    interval_hours: int = Field(..., ge=1, le=720)


class ScheduleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=256)
    prompt: Optional[str] = Field(None, min_length=1, max_length=500_000)
    models_json: Optional[List[str]] = None
    max_tokens: Optional[int] = Field(None, ge=1, le=128_000)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    interval_hours: Optional[int] = Field(None, ge=1, le=720)
    enabled: Optional[bool] = None


class ExperimentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: Optional[str] = Field(None, max_length=5_000)
    suite_id: str = Field(..., min_length=1)


class ExperimentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = Field(None, max_length=5_000)
    status: Optional[Literal["active", "archived"]] = None


class EnvVarUpdate(BaseModel):
    name: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str = Field(..., min_length=1)

    @field_validator("name")
    @classmethod
    def name_must_be_safe(cls, v: str) -> str:
        if v not in SAFE_ENV_VARS:
            raise ValueError(
                f"Cannot modify '{v}'. Only provider API keys are allowed: "
                f"{', '.join(sorted(SAFE_ENV_VARS))}"
            )
        return v


class ProviderCreate(BaseModel):
    provider_key: str = Field(..., pattern=r"^[a-zA-Z0-9_\-]+$", max_length=64)
    display_name: str = Field(..., max_length=256)
    api_base: Optional[str] = None
    model_id_prefix: Optional[str] = Field(None, max_length=64)
    api_key_env: Optional[str] = None


class RateLimitUpdate(BaseModel):
    benchmarks_per_hour: int = Field(..., ge=1, le=1000)
    max_concurrent: int = Field(..., ge=1, le=50)
    max_runs_per_benchmark: int = Field(..., ge=1, le=100)


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class ApiKeyUpdate(BaseModel):
    provider_key: str = Field(..., pattern=r"^[a-zA-Z0-9_\-]+$")
    key_name: str = Field(default="default", max_length=256)
    api_key: str = Field(..., min_length=1)


# ──────────────────── Response Schemas ─────────────────────

class ErrorResponse(BaseModel):
    detail: str


class SuccessResponse(BaseModel):
    status: str = "ok"
    message: Optional[str] = None


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str = "pending"
    message: Optional[str] = None


# ──────────────────── Model Profiles ─────────────────────

class ProfileCreate(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=256)
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)
    params_json: Optional[dict] = Field(default_factory=dict)
    system_prompt: Optional[str] = Field(None, max_length=50_000)
    is_default: bool = False
    origin_type: Literal["manual", "param_tuner", "prompt_tuner", "import"] = "manual"
    origin_ref: Optional[str] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)
    params_json: Optional[dict] = None
    system_prompt: Optional[str] = Field(None, max_length=50_000)
    is_default: Optional[bool] = None


class ProfileFromTuner(BaseModel):
    model_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=128)
    source_type: Literal["param_tuner", "prompt_tuner"]
    source_id: str = Field(..., min_length=1)
    params_json: Optional[dict] = None
    system_prompt: Optional[str] = Field(None, max_length=50_000)
    set_as_default: bool = False


# ──────────────────── Prompt Version Registry ─────────────────────

class PromptVersionCreate(BaseModel):
    prompt_text: str = Field(..., min_length=1, max_length=500_000)
    label: str = Field(default="", max_length=256)
    parent_version_id: Optional[str] = None


class PromptVersionUpdate(BaseModel):
    label: str = Field(..., max_length=256)


# ──────────────────── Tool Eval Irrelevance ─────────────────────

class TestCaseCreateV2(BaseModel):
    """Extended test case creation with irrelevance detection support."""
    prompt: str = Field(..., min_length=1, max_length=50_000)
    expected_tool: Optional[str] = Field(None, max_length=256)
    expected_params: Optional[dict] = None
    param_scoring: str = Field(default="exact", pattern=r"^(exact|fuzzy|contains|semantic)$")
    multi_turn_config: Optional[dict] = None
    scoring_config_json: Optional[dict] = None
    should_call_tool: bool = True

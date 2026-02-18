"""Provider parameter registry with validation and clamping.

Contains the 3-tier parameter definitions, conflict rules,
and clamping logic for all supported LLM providers.

Three-Tier Architecture:
  - Tier 1 (Universal): temperature, max_tokens, stop -- supported by ALL providers
  - Tier 2 (Common): top_p, top_k, frequency_penalty, presence_penalty, seed, reasoning_effort
  - Tier 3 (Provider-Specific): JSON passthrough for any LiteLLM-supported parameter
"""

from __future__ import annotations

import fnmatch
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY: dict[str, dict] = {
    "openai": {
        "display_name": "OpenAI",
        "tier1": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 1.0, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 128000, "default": 4096, "type": "int", "required": False},
            "stop": {"type": "string_array", "max_items": 4},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0, "step": 0.05, "type": "float", "supported": True},
            "top_k": {"supported": False, "reason": "OpenAI does not support top_k"},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "step": 0.1, "type": "float", "supported": True},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "step": 0.1, "type": "float", "supported": True},
            "seed": {"type": "int", "supported": True, "deprecated": True, "note": "Seed is deprecated for OpenAI"},
            "reasoning_effort": {"type": "enum", "values": ["none", "low", "medium", "high"], "supported": True, "note": "Only for reasoning models (o-series, GPT-5)"},
        },
        "tier3_examples": {
            "service_tier": {"type": "string", "values": ["auto", "default", "flex", "priority"]},
            "prediction": {"type": "object"},
            "web_search_options": {"type": "object"},
        },
        "conflicts": [
            {"params": ["temperature"], "condition": "model contains 'gpt-5'", "resolution": "Lock to 1.0", "message": "GPT-5 locks temperature to 1.0"},
            {"params": ["max_tokens"], "condition": "model contains 'o1' or 'o3' or 'o4'", "resolution": "Use max_completion_tokens instead", "message": "O-series uses max_completion_tokens"},
        ],
        "model_overrides": {
            "gpt-5*": {"temperature": {"locked": True, "value": 1.0}},
            "o1*|o3*|o4*": {"temperature": {"locked": True, "value": 1.0}, "stop": {"supported": False}},
        },
    },
    "anthropic": {
        "display_name": "Anthropic",
        "tier1": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 1.0, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 128000, "default": 4096, "type": "int", "required": True, "note": "Anthropic REQUIRES max_tokens"},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": None, "step": 0.05, "type": "float", "supported": True, "note": "Cannot use with temperature on Haiku 4.5+/Opus 4+"},
            "top_k": {"min": 1, "max": 500, "default": None, "type": "int", "supported": True},
            "frequency_penalty": {"supported": False, "reason": "Anthropic does not support penalty parameters"},
            "presence_penalty": {"supported": False, "reason": "Anthropic does not support penalty parameters"},
            "seed": {"supported": False, "reason": "Anthropic does not support seed"},
            "reasoning_effort": {"type": "enum", "values": ["none", "low", "medium", "high"], "supported": True, "note": "Maps to thinking.budget_tokens (low=2000, medium=5000, high=10000)"},
        },
        "tier3_examples": {
            "cache_control": {"type": "object"},
            "inference_geo": {"type": "string"},
        },
        "conflicts": [
            {"params": ["temperature", "top_p"], "condition": "both set on newer models", "resolution": "Drop top_p, keep temperature", "message": "Anthropic newer models cannot use both temperature and top_p"},
            {"params": ["temperature", "top_k"], "condition": "thinking enabled", "resolution": "Drop temperature and top_k", "message": "Cannot modify temperature/top_k when thinking is enabled"},
        ],
    },
    "gemini": {
        "display_name": "Google Gemini",
        "tier1": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 1.0, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 65536, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": None, "step": 0.05, "type": "float", "supported": True},
            "top_k": {"min": 1, "max": 100, "default": None, "type": "int", "supported": True},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "seed": {"type": "int", "supported": True},
            "reasoning_effort": {"type": "enum", "values": ["none", "low", "medium", "high"], "supported": True, "note": "Maps to thinkingConfig.thinkingLevel"},
        },
        "tier3_examples": {
            "safety_settings": {"type": "array"},
        },
        "conflicts": [
            {"params": ["temperature"], "condition": "model contains 'gemini-3' and value < 1.0", "resolution": "Clamp to 1.0", "message": "Gemini 3 models degrade below temperature 1.0"},
        ],
    },
    "ollama": {
        "display_name": "Ollama / Local",
        "tier1": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 0.8, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 32768, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9, "step": 0.05, "type": "float", "supported": True},
            "top_k": {"min": 1, "max": 500, "default": 40, "type": "int", "supported": True},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "seed": {"type": "int", "supported": True},
            "reasoning_effort": {"supported": False},
        },
        "tier3_examples": {
            "mirostat": {"type": "int", "values": [0, 1, 2], "note": "Mirostat sampling mode"},
            "mirostat_eta": {"type": "float", "note": "Mirostat learning rate"},
            "mirostat_tau": {"type": "float", "note": "Mirostat target entropy"},
            "repetition_penalty": {"type": "float", "min": 0.0, "max": 3.0, "note": "Multiplicative -- NOT same as presence_penalty"},
            "num_ctx": {"type": "int", "note": "Context window size override"},
            "min_p": {"type": "float", "min": 0.0, "max": 1.0, "note": "Minimum probability threshold"},
            "keep_alive": {"type": "string", "note": "Model memory duration (e.g., '5m')"},
        },
    },
    "lm_studio": {
        "display_name": "LM Studio",
        "tier1": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 0.8, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 32768, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9, "step": 0.05, "type": "float", "supported": True},
            "top_k": {"min": 1, "max": 500, "default": 40, "type": "int", "supported": True},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "seed": {"type": "int", "supported": True},
            "reasoning_effort": {"supported": False},
        },
        "tier3_examples": {
            "repetition_penalty": {"type": "float", "min": 0.0, "max": 3.0},
            "min_p": {"type": "float", "min": 0.0, "max": 1.0},
        },
    },
    "mistral": {
        "display_name": "Mistral",
        "tier1": {
            "temperature": {"min": 0.0, "max": 1.5, "default": 0.7, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 32768, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0, "step": 0.05, "type": "float", "supported": True},
            "top_k": {"supported": "partial", "reason": "Limited support in Mistral API"},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "seed": {"type": "int", "supported": True, "note": "Sent as random_seed (LiteLLM translates)"},
            "reasoning_effort": {"supported": False, "note": "Use system prompt for Magistral models"},
        },
        "tier3_examples": {
            "safe_prompt": {"type": "boolean"},
        },
    },
    "deepseek": {
        "display_name": "DeepSeek",
        "tier1": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 1.0, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 65536, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0, "step": 0.05, "type": "float", "supported": True},
            "top_k": {"supported": False},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "seed": {"supported": False},
            "reasoning_effort": {"type": "enum", "values": ["none", "enabled"], "supported": True, "note": "Binary: any value enables thinking mode"},
        },
        "conflicts": [
            {"params": ["temperature", "top_p", "frequency_penalty", "presence_penalty"], "condition": "R1 model in thinking mode", "resolution": "Log warning -- params have no effect", "message": "DeepSeek R1 ignores all sampling params in thinking mode"},
        ],
    },
    "cohere": {
        "display_name": "Cohere",
        "tier1": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.3, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 4096, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 0.99, "default": None, "step": 0.05, "type": "float", "supported": True, "note": "Max 0.99, not 1.0"},
            "top_k": {"min": 0, "max": 500, "default": None, "type": "int", "supported": True},
            "frequency_penalty": {"min": 0.0, "max": 1.0, "default": 0.0, "type": "float", "supported": True, "note": "Range 0-1 only (not -2 to 2)"},
            "presence_penalty": {"min": 0.0, "max": 1.0, "default": 0.0, "type": "float", "supported": True, "note": "Range 0-1 only"},
            "seed": {"type": "int", "supported": True},
            "reasoning_effort": {"supported": False},
        },
        "tier3_examples": {
            "safety_mode": {"type": "string", "values": ["CONTEXTUAL", "STRICT", "OFF"]},
            "documents": {"type": "array"},
            "citation_options": {"type": "object"},
        },
    },
    "xai": {
        "display_name": "xAI (Grok)",
        "tier1": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 1.0, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 131072, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0, "step": 0.05, "type": "float", "supported": True},
            "top_k": {"supported": False},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "seed": {"type": "int", "default": 0, "supported": True},
            "reasoning_effort": {"type": "enum", "values": ["none", "low", "medium", "high"], "supported": "partial", "note": "Reasoning models reject penalties and stop sequences when active"},
        },
        "conflicts": [
            {"params": ["frequency_penalty", "presence_penalty", "stop"], "condition": "reasoning model active", "resolution": "Drop penalties and stop", "message": "xAI reasoning models reject penalty params and stop sequences"},
        ],
    },
    "vllm": {
        "display_name": "vLLM (Self-Hosted)",
        "tier1": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 1.0, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 65536, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0, "step": 0.05, "type": "float", "supported": True},
            "top_k": {"min": 1, "max": 500, "default": None, "type": "int", "supported": True},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": True},
            "seed": {"type": "int", "supported": True},
            "reasoning_effort": {"supported": False},
        },
        "tier3_examples": {
            "repetition_penalty": {"type": "float", "min": 0.0, "max": 3.0, "default": 1.0, "note": "Multiplicative, different from presence_penalty"},
            "min_p": {"type": "float", "min": 0.0, "max": 1.0},
            "typical_p": {"type": "float", "min": 0.0, "max": 1.0},
            "guided_json": {"type": "object", "note": "JSON schema for constrained decoding"},
            "guided_choice": {"type": "array", "note": "Constrain output to specific choices"},
            "best_of": {"type": "int", "note": "Generate N sequences, return best"},
            "ignore_eos": {"type": "bool", "note": "Ignore end-of-sequence token"},
        },
        "notes": "IMPORTANT: Do NOT use extra_body for vLLM -- pass params as direct kwargs (GitHub #4769).",
    },
    "_unknown": {
        "display_name": "Unknown (OpenAI-compatible fallback)",
        "note": "Used when provider/model is not recognized. Assumes OpenAI-compatible API.",
        "tier1": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 1.0, "step": 0.1, "type": "float"},
            "max_tokens": {"min": 1, "max": 128000, "default": 4096, "type": "int", "required": False},
        },
        "tier2": {
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0, "type": "float", "supported": True},
            "top_k": {"supported": "unknown"},
            "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": "unknown"},
            "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0, "type": "float", "supported": "unknown"},
            "seed": {"type": "int", "supported": "unknown"},
            "reasoning_effort": {"supported": "unknown"},
        },
    },
}

# Model ID prefix -> provider key mapping for identify_provider fallback
_PREFIX_MAP: dict[str, str] = {
    "anthropic/": "anthropic",
    "gemini/": "gemini",
    "vertex_ai/": "gemini",
    "ollama/": "ollama",
    "ollama_chat/": "ollama",
    "lm_studio/": "lm_studio",
    "mistral/": "mistral",
    "deepseek/": "deepseek",
    "cohere/": "cohere",
    "cohere_chat/": "cohere",
    "xai/": "xai",
    "vllm/": "vllm",
    "openai/": "openai",
}


# ---------------------------------------------------------------------------
# Provider identification
# ---------------------------------------------------------------------------

def identify_provider(model_id: str, provider_key: Optional[str] = None) -> str:
    """Identify which provider registry to use for a model.

    Resolution order:
    1. Explicit provider_key from config.yaml (if it matches a registry key)
    2. Model ID prefix (anthropic/, gemini/, etc.)
    3. Fallback: "_unknown"
    """
    # 1. Explicit provider key
    if provider_key and provider_key in PROVIDER_REGISTRY:
        return provider_key

    # 2. Model ID prefix detection
    model_lower = model_id.lower()
    for prefix, prov in _PREFIX_MAP.items():
        if model_lower.startswith(prefix):
            return prov

    # 3. Fallback
    return "_unknown"


def _bare_model_name(model_id: str) -> str:
    """Strip known provider prefixes to get the bare model name (lowercase)."""
    lower = model_id.lower()
    for prefix in _PREFIX_MAP:
        if lower.startswith(prefix):
            return lower[len(prefix):]
    return lower


def _is_o_series(model_id: str) -> bool:
    """Check if a model is an OpenAI O-series model (o1, o3, o4, etc.)."""
    bare = _bare_model_name(model_id)
    return bool(re.match(r"^o[134]", bare))


# ---------------------------------------------------------------------------
# Temperature clamping
# ---------------------------------------------------------------------------

def clamp_temperature(value: float, provider: str, model_id: str) -> tuple[float, Optional[dict]]:
    """Clamp temperature to provider-valid range with model-specific overrides.

    Returns (clamped_value, adjustment_dict_or_None).
    """
    reg = PROVIDER_REGISTRY.get(provider, PROVIDER_REGISTRY["_unknown"])
    temp_spec = reg.get("tier1", {}).get("temperature", {})
    low = temp_spec.get("min", 0.0)
    high = temp_spec.get("max", 2.0)

    original = value

    # Model-specific overrides: GPT-5 locks to 1.0
    model_lower = model_id.lower()
    if provider == "openai" and "gpt-5" in model_lower:
        if value != 1.0:
            return 1.0, {
                "param": "temperature",
                "original": original,
                "adjusted": 1.0,
                "action": "clamp",
                "reason": "GPT-5 locks temperature to 1.0",
            }
        return 1.0, None

    # O-series locks to 1.0
    if provider == "openai" and _is_o_series(model_id):
        if value != 1.0:
            return 1.0, {
                "param": "temperature",
                "original": original,
                "adjusted": 1.0,
                "action": "clamp",
                "reason": "O-series models lock temperature to 1.0",
            }
        return 1.0, None

    # Gemini 3 models: clamp minimum to 1.0
    if provider == "gemini" and "gemini-3" in model_lower:
        if value < 1.0:
            return 1.0, {
                "param": "temperature",
                "original": original,
                "adjusted": 1.0,
                "action": "clamp",
                "reason": "Gemini 3 models degrade below temperature 1.0",
            }

    # Standard range clamping
    clamped = max(low, min(value, high))
    if clamped != original:
        return clamped, {
            "param": "temperature",
            "original": original,
            "adjusted": clamped,
            "action": "clamp",
            "reason": f"{reg['display_name']} temperature range is {low}-{high}",
        }

    return value, None


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

def resolve_conflicts(params: dict, provider: str, model_id: str) -> tuple[dict, list[dict]]:
    """Detect and resolve parameter conflicts.

    Philosophy: WARN, don't DROP.  All user-requested params pass through to
    the provider.  If the API rejects them, the user sees the provider error
    rather than a silent removal.  Only genuinely *transformative* changes
    (e.g. renaming max_tokens → max_completion_tokens for O-series) alter the
    resolved dict.

    Each adjustment dict contains an ``"action"`` field:
      - ``"drop"`` — param was removed from the request (hard conflict,
        e.g. Anthropic temp+top_p mutual exclusion).
      - ``"warn"`` — param is passed through unchanged; the user is warned
        the provider *may* reject it.
      - ``"rename"`` — param was renamed/remapped (value preserved).
      - ``"clamp"`` — value was clamped to the provider's valid range.

    Returns (resolved_params, list_of_adjustments).
    """
    resolved = dict(params)
    adjustments: list[dict] = []
    model_lower = model_id.lower()

    # --- Anthropic conflicts ---
    if provider == "anthropic":
        # Hard conflict: temperature + top_p — Anthropic API rejects this combo.
        # This is a genuine mutual exclusion, not just "unsupported". We drop top_p.
        if "temperature" in resolved and "top_p" in resolved:
            if resolved["temperature"] is not None and resolved["top_p"] is not None:
                adj_val = resolved.pop("top_p")
                adjustments.append({
                    "param": "top_p",
                    "original": adj_val,
                    "adjusted": None,
                    "action": "drop",
                    "reason": "Anthropic cannot use both temperature and top_p. Dropping top_p.",
                })

        # Warn-only: params Anthropic doesn't natively support — pass through
        for unsupported in ("frequency_penalty", "presence_penalty", "seed"):
            if unsupported in resolved and resolved[unsupported] is not None:
                adjustments.append({
                    "param": unsupported,
                    "original": resolved[unsupported],
                    "adjusted": resolved[unsupported],
                    "action": "warn",
                    "reason": f"Anthropic may not support {unsupported} — passing through",
                })

    # --- OpenAI conflicts ---
    if provider == "openai":
        # Rename: max_tokens -> max_completion_tokens for o-series (genuinely required)
        if _is_o_series(model_id) and "max_tokens" in resolved:
            val = resolved.pop("max_tokens")
            resolved["max_completion_tokens"] = val
            adjustments.append({
                "param": "max_tokens",
                "original": val,
                "adjusted": val,
                "action": "rename",
                "reason": "O-series uses max_completion_tokens instead of max_tokens",
            })

        # Warn-only: top_k — pass through
        if "top_k" in resolved and resolved["top_k"] is not None:
            adjustments.append({
                "param": "top_k",
                "original": resolved["top_k"],
                "adjusted": resolved["top_k"],
                "action": "warn",
                "reason": "OpenAI may not support top_k — passing through",
            })

    # --- Gemini conflicts ---
    if provider == "gemini":
        pass  # Temperature clamping handled in clamp_temperature

    # --- DeepSeek conflicts ---
    if provider == "deepseek":
        # R1 model in thinking mode ignores sampling params — warn only
        if "r1" in model_lower and resolved.get("reasoning_effort") not in (None, "none"):
            for p in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
                if p in resolved and resolved[p] is not None:
                    adjustments.append({
                        "param": p,
                        "original": resolved[p],
                        "adjusted": resolved[p],
                        "action": "warn",
                        "reason": "DeepSeek R1 ignores all sampling params in thinking mode",
                    })

        # Warn-only: top_k and seed — pass through
        for unsupported in ("top_k", "seed"):
            if unsupported in resolved and resolved[unsupported] is not None:
                adjustments.append({
                    "param": unsupported,
                    "original": resolved[unsupported],
                    "adjusted": resolved[unsupported],
                    "action": "warn",
                    "reason": f"DeepSeek may not support {unsupported} — passing through",
                })

    # --- xAI conflicts ---
    if provider == "xai":
        # Reasoning models may reject penalties/stop — warn only
        if resolved.get("reasoning_effort") not in (None, "none"):
            for p in ("frequency_penalty", "presence_penalty", "stop"):
                if p in resolved and resolved[p] is not None:
                    adjustments.append({
                        "param": p,
                        "original": resolved[p],
                        "adjusted": resolved[p],
                        "action": "warn",
                        "reason": "xAI reasoning models may reject penalty params and stop sequences",
                    })

        # Warn-only: top_k — pass through
        if "top_k" in resolved and resolved["top_k"] is not None:
            adjustments.append({
                "param": "top_k",
                "original": resolved["top_k"],
                "adjusted": resolved["top_k"],
                "action": "warn",
                "reason": "xAI may not support top_k — passing through",
            })

    # --- Cohere conflicts ---
    if provider == "cohere":
        # Clamp penalty ranges to 0-1 (hard API constraint)
        for p in ("frequency_penalty", "presence_penalty"):
            if p in resolved and resolved[p] is not None:
                val = resolved[p]
                clamped = max(0.0, min(val, 1.0))
                if clamped != val:
                    resolved[p] = clamped
                    adjustments.append({
                        "param": p,
                        "original": val,
                        "adjusted": clamped,
                        "action": "clamp",
                        "reason": f"Cohere {p} range is 0-1 only",
                    })

        # Clamp top_p max 0.99
        if "top_p" in resolved and resolved["top_p"] is not None:
            val = resolved["top_p"]
            if val > 0.99:
                resolved["top_p"] = 0.99
                adjustments.append({
                    "param": "top_p",
                    "original": val,
                    "adjusted": 0.99,
                    "action": "clamp",
                    "reason": "Cohere top_p maximum is 0.99",
                })

    # --- Mistral conflicts ---
    if provider == "mistral":
        # top_k has limited support — warn only
        if "top_k" in resolved and resolved["top_k"] is not None:
            adjustments.append({
                "param": "top_k",
                "original": resolved["top_k"],
                "adjusted": resolved["top_k"],
                "action": "warn",
                "reason": "Mistral has limited top_k support",
            })

    # --- Generic: WARN (not drop) for unsupported tier2 params ---
    reg = PROVIDER_REGISTRY.get(provider, PROVIDER_REGISTRY["_unknown"])
    tier2 = reg.get("tier2", {})
    for param_name in list(resolved.keys()):
        if param_name in tier2:
            spec = tier2[param_name]
            supported = spec.get("supported", True)
            if supported is False and resolved[param_name] is not None:
                # Already handled above for specific providers, but catch stragglers
                if not any(a["param"] == param_name for a in adjustments):
                    adjustments.append({
                        "param": param_name,
                        "original": resolved[param_name],
                        "adjusted": resolved[param_name],
                        "action": "warn",
                        "reason": spec.get("reason", f"{param_name} may not be supported by {provider} — passing through"),
                    })

    # --- Generic: clamp numeric tier2 params to their ranges ---
    for param_name in list(resolved.keys()):
        if param_name in tier2:
            spec = tier2[param_name]
            if spec.get("supported") in (False, "unknown"):
                continue
            if resolved[param_name] is None:
                continue
            p_min = spec.get("min")
            p_max = spec.get("max")
            if p_min is not None and p_max is not None:
                val = resolved[param_name]
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    logger.debug("Cannot convert param %s value to float, skipping clamp", param_name)
                    continue
                clamped = max(p_min, min(val, p_max))
                if clamped != val:
                    # Don't double-report if already adjusted above
                    if not any(a["param"] == param_name and a.get("adjusted") == clamped for a in adjustments):
                        resolved[param_name] = clamped
                        adjustments.append({
                            "param": param_name,
                            "original": val,
                            "adjusted": clamped,
                            "action": "clamp",
                            "reason": f"{param_name} clamped to {provider} range [{p_min}, {p_max}]",
                        })

    return resolved, adjustments


# ---------------------------------------------------------------------------
# Full parameter validation
# ---------------------------------------------------------------------------

def validate_params(provider: str, model_id: str, params: dict) -> dict:
    """Validate and clamp parameters for a provider.

    Returns: {"valid": bool, "adjustments": [...], "warnings": [...], "resolved_params": {...}}
    """
    adjustments: list[dict] = []
    warnings: list[str] = []
    resolved = dict(params)

    reg = PROVIDER_REGISTRY.get(provider, PROVIDER_REGISTRY["_unknown"])

    # --- Temperature clamping ---
    if "temperature" in resolved and resolved["temperature"] is not None:
        clamped, adj = clamp_temperature(float(resolved["temperature"]), provider, model_id)
        resolved["temperature"] = clamped
        if adj:
            adjustments.append(adj)

    # --- Conflict resolution ---
    resolved, conflict_adjustments = resolve_conflicts(resolved, provider, model_id)
    adjustments.extend(conflict_adjustments)

    # --- Provider-specific warnings ---
    if provider == "anthropic":
        warnings.append("max_tokens is required for Anthropic and will always be included")

    if provider == "_unknown":
        warnings.append("Unknown provider -- using OpenAI-compatible defaults. Parameters may not all be supported.")

    # --- Remove None values from resolved ---
    resolved = {k: v for k, v in resolved.items() if v is not None}

    # "valid" = no hard drops or clamps.  Warns are informational and don't
    # invalidate the request.
    has_drops = any(a.get("action") == "drop" for a in adjustments)
    has_warns = any(a.get("action") == "warn" for a in adjustments)
    valid = not has_drops and len(adjustments) == 0
    return {
        "valid": valid,
        "has_warnings": has_warns,
        "adjustments": adjustments,
        "warnings": warnings,
        "resolved_params": resolved,
    }


# ---------------------------------------------------------------------------
# Build LiteLLM kwargs
# ---------------------------------------------------------------------------

def build_litellm_kwargs(
    target: "Target",
    provider_params: Optional[dict] = None,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> dict:
    """Build the final kwargs dict for litellm.completion/acompletion.

    Merges Tier 1 + Tier 2 params + passthrough into kwargs.
    Applies clamping and conflict resolution.
    Respects target.skip_params.

    Args:
        target: The Target dataclass with model/provider info.
        provider_params: Optional dict with tier2 params + "passthrough" sub-dict.
        temperature: Explicit temperature (from request body). Overridden by provider_params if present.
        max_tokens: Explicit max_tokens (from request body). Overridden by provider_params if present.

    Returns:
        Dict of extra kwargs to merge into the litellm call. Does NOT include
        model, messages, stream, tools, etc. -- just the parameter overrides.
    """
    if provider_params is None and temperature is None and max_tokens is None:
        return {}

    provider = identify_provider(target.model_id, getattr(target, "provider_key", None))
    skip_params = set(target.skip_params or [])

    # Start with explicit temperature/max_tokens
    params_to_validate: dict[str, Any] = {}
    if temperature is not None:
        params_to_validate["temperature"] = temperature
    if max_tokens is not None:
        params_to_validate["max_tokens"] = max_tokens

    # Overlay provider_params (tier 1 + tier 2)
    passthrough: dict = {}
    if provider_params:
        passthrough = provider_params.get("passthrough", {}) if isinstance(provider_params.get("passthrough"), dict) else {}
        # Copy without passthrough -- don't mutate the original
        pp_copy = {k: v for k, v in provider_params.items() if k != "passthrough"}
        for k, v in pp_copy.items():
            if v is not None:
                params_to_validate[k] = v

    # Validate and resolve
    result = validate_params(provider, target.model_id, params_to_validate)
    resolved = result["resolved_params"]

    # Apply skip_params
    for p in skip_params:
        resolved.pop(p, None)

    # Merge passthrough (Tier 3) -- these bypass validation
    if passthrough:
        for k, v in passthrough.items():
            if k not in skip_params and v is not None:
                resolved[k] = v

    return resolved

"""Shared fixtures for the LLM Benchmark Studio test suite."""

import pytest


@pytest.fixture
def sample_search_space():
    """A typical search space for param tuner tests."""
    return {
        "temperature": {"min": 0.0, "max": 1.0, "step": 0.5},
        "tool_choice": ["auto", "required"],
    }


@pytest.fixture
def sample_preset():
    """A sample preset for Phase 3 preset tests."""
    return {
        "name": "Test Preset",
        "search_space": {
            "temperature": {"min": 0.0, "max": 1.0, "step": 0.25},
            "tool_choice": ["auto", "required"],
        },
    }

"""Tests for MCP/tool helper functions in app.py.

Covers: mcp_tool_to_openai, generate_test_case, _example_value.
"""

import pytest

from app import mcp_tool_to_openai, generate_test_case, _example_value


# ── mcp_tool_to_openai ──────────────────────────────────────────────

class TestMcpToolToOpenai:
    def test_basic_conversion(self):
        mcp_tool = {
            "name": "get_weather",
            "description": "Get current weather",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                },
            },
        }
        result = mcp_tool_to_openai(mcp_tool)
        assert result["type"] == "function"
        assert result["function"]["name"] == "get_weather"
        assert result["function"]["description"] == "Get current weather"
        assert "city" in result["function"]["parameters"]["properties"]

    def test_truncates_long_description(self):
        mcp_tool = {
            "name": "tool",
            "description": "A" * 2000,
        }
        result = mcp_tool_to_openai(mcp_tool)
        assert len(result["function"]["description"]) <= 1024

    def test_missing_input_schema(self):
        mcp_tool = {"name": "simple_tool", "description": "No params"}
        result = mcp_tool_to_openai(mcp_tool)
        assert result["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_empty_description(self):
        mcp_tool = {"name": "tool"}
        result = mcp_tool_to_openai(mcp_tool)
        assert result["function"]["description"] == ""


# ── _example_value ──────────────────────────────────────────────────

class TestExampleValue:
    def test_string_default(self):
        assert _example_value("foo", {"type": "string"}) == "example"

    def test_url_param(self):
        assert "example.com" in _example_value("website_url", {"type": "string"})

    def test_path_param(self):
        assert "tmp" in _example_value("file_path", {"type": "string"})

    def test_email_param(self):
        assert "@" in _example_value("user_email", {"type": "string"})

    def test_name_param(self):
        assert _example_value("username", {"type": "string"}) == "example"

    def test_query_param(self):
        assert "query" in _example_value("search_query", {"type": "string"})

    def test_city_param(self):
        assert "San Francisco" in _example_value("city", {"type": "string"})

    def test_code_param(self):
        result = _example_value("script", {"type": "string"})
        assert "console" in result or "hello" in result

    def test_integer(self):
        assert _example_value("count", {"type": "integer"}) == 42

    def test_number(self):
        assert _example_value("price", {"type": "number"}) == 42

    def test_boolean(self):
        assert _example_value("enabled", {"type": "boolean"}) is True

    def test_array(self):
        assert _example_value("items", {"type": "array"}) == []

    def test_enum(self):
        result = _example_value("unit", {"type": "string", "enum": ["celsius", "fahrenheit"]})
        assert result == "celsius"

    def test_unknown_type(self):
        assert _example_value("x", {"type": "object"}) == "example"

    def test_selector_param(self):
        result = _example_value("css_selector", {"type": "string"})
        assert "#" in result


# ── generate_test_case ──────────────────────────────────────────────

class TestGenerateTestCase:
    def test_basic_tool_with_required_params(self):
        tool = {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["city"],
                },
            },
        }
        case = generate_test_case(tool)
        assert case["expected_tool"] == "get_weather"
        assert "prompt" in case
        assert "get_weather" in case["prompt"]
        # Only required params should be in expected_params
        assert "city" in case["expected_params"]
        assert "unit" not in case["expected_params"]

    def test_tool_no_required_params(self):
        tool = {
            "function": {
                "name": "ping",
                "description": "Ping the server",
                "parameters": {"type": "object", "properties": {}},
            }
        }
        case = generate_test_case(tool)
        assert case["expected_tool"] == "ping"
        assert case["expected_params"] is None

    def test_tool_no_parameters(self):
        tool = {
            "function": {
                "name": "logout",
                "description": "Log out the user",
            }
        }
        case = generate_test_case(tool)
        assert case["expected_tool"] == "logout"
        assert case["expected_params"] is None

    def test_prompt_includes_param_values(self):
        tool = {
            "function": {
                "name": "search",
                "description": "Search docs",
                "parameters": {
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        }
        case = generate_test_case(tool)
        # Prompt should mention the param value
        assert "query=" in case["prompt"] or "query" in case["prompt"]

    def test_multiple_required_params(self):
        tool = {
            "function": {
                "name": "create_user",
                "description": "Create a new user",
                "parameters": {
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                    "required": ["name", "email", "age"],
                },
            },
        }
        case = generate_test_case(tool)
        assert len(case["expected_params"]) == 3
        assert case["expected_params"]["age"] == 42

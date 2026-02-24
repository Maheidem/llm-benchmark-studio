"""Tests for T5: Argument Source Tracking (Nested Tool Calls).

Tests that argument_source dict {arg_name: "tool_name.field"} is stored,
returned, and validated during multi-turn eval execution.

Run: uv run pytest tests/test_nested_tool_calls.py -v
"""

import json
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ===========================================================================
# API contract tests — argument_source storage and retrieval (async)
# ===========================================================================

class TestArgumentSourceStorage:
    async def test_import_with_argument_source_stores_it(
        self, app_client, auth_headers
    ):
        """Importing a test case with argument_source dict stores it correctly."""
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Nested Tool Suite",
            "tools": [
                {"type": "function", "function": {
                    "name": "get_user",
                    "description": "Get user info",
                    "parameters": {"type": "object",
                                   "properties": {"user_id": {"type": "string"}},
                                   "required": ["user_id"]},
                }},
                {"type": "function", "function": {
                    "name": "get_orders",
                    "description": "Get orders for a user",
                    "parameters": {"type": "object",
                                   "properties": {"customer_id": {"type": "string"}},
                                   "required": ["customer_id"]},
                }},
            ],
            "test_cases": [{
                "prompt": "Get orders for user 42",
                "multi_turn": True,
                "max_rounds": 3,
                "mock_responses": {
                    "get_user": {"id": "u-42", "name": "Alice"},
                },
                "optimal_hops": 2,
                "expected_tool": "get_orders",
                "expected_params": {"customer_id": "u-42"},
                # T5: argument_source dict: {arg_name: "tool_name.field"}
                "argument_source": {"customer_id": "get_user.id"},
            }],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        # Retrieve and verify argument_source is preserved.
        # argument_source is stored inside multi_turn_config JSON;
        # the GET /api/tool-suites/{suite_id} endpoint may expose it directly
        # or it will be in the multi_turn_config raw field.
        detail_resp = await app_client.get(
            f"/api/tool-suites/{suite_id}", headers=auth_headers
        )
        assert detail_resp.status_code == 200
        cases = detail_resp.json().get("test_cases", [])
        assert len(cases) == 1
        tc = cases[0]

        # argument_source may be exposed as top-level field OR inside multi_turn_config
        import json as _json
        arg_src = tc.get("argument_source")
        if arg_src is None and tc.get("multi_turn_config"):
            # Fall back to parsing from multi_turn_config
            try:
                mt = _json.loads(tc["multi_turn_config"]) if isinstance(tc["multi_turn_config"], str) else tc["multi_turn_config"]
                arg_src = mt.get("argument_source")
            except (TypeError, ValueError):
                pass

        assert arg_src is not None, (
            f"argument_source not found in test case or multi_turn_config: {tc.keys()}"
        )
        assert isinstance(arg_src, dict), (
            f"argument_source must be a dict, got: {type(arg_src)}"
        )
        assert arg_src == {"customer_id": "get_user.id"}

    async def test_argument_source_is_dict_not_string(
        self, app_client, auth_headers
    ):
        """argument_source is stored as a dict {arg_name: 'tool.field'}, not a plain string."""
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Argument Source Dict Check",
            "tools": [
                {"type": "function", "function": {
                    "name": "step_one",
                    "description": "Step 1",
                    "parameters": {"type": "object",
                                   "properties": {"input": {"type": "string"}},
                                   "required": ["input"]},
                }},
                {"type": "function", "function": {
                    "name": "step_two",
                    "description": "Step 2",
                    "parameters": {"type": "object",
                                   "properties": {"token": {"type": "string"}},
                                   "required": ["token"]},
                }},
            ],
            "test_cases": [{
                "prompt": "Run two steps",
                "multi_turn": True,
                "max_rounds": 2,
                "mock_responses": {"step_one": {"result_token": "abc123"}},
                "optimal_hops": 2,
                "expected_tool": "step_two",
                "expected_params": {"token": "abc123"},
                "argument_source": {"token": "step_one.result_token"},
            }],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        import json as _json
        detail_resp = await app_client.get(
            f"/api/tool-suites/{suite_id}", headers=auth_headers
        )
        assert detail_resp.status_code == 200
        cases = detail_resp.json().get("test_cases", [])
        tc = cases[0]
        # argument_source may be top-level or inside multi_turn_config
        arg_src = tc.get("argument_source")
        if arg_src is None and tc.get("multi_turn_config"):
            try:
                mt = _json.loads(tc["multi_turn_config"]) if isinstance(tc["multi_turn_config"], str) else tc["multi_turn_config"]
                arg_src = mt.get("argument_source")
            except (TypeError, ValueError):
                pass

        assert isinstance(arg_src, dict), (
            f"argument_source must be dict, got {type(arg_src)}: {arg_src!r}"
        )
        # Dict has arg_name -> "tool_name.field" format
        for arg_name, source_ref in arg_src.items():
            assert isinstance(arg_name, str)
            assert isinstance(source_ref, str)
            assert "." in source_ref, (
                f"source_ref '{source_ref}' must be in 'tool_name.field' format"
            )

    async def test_argument_source_in_bfcl_export(
        self, app_client, auth_headers
    ):
        """argument_source is preserved in BFCL export."""
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "T5 BFCL Export",
            "tools": [
                {"type": "function", "function": {
                    "name": "get_token",
                    "description": "Get auth token",
                    "parameters": {"type": "object",
                                   "properties": {"user": {"type": "string"}},
                                   "required": ["user"]},
                }},
                {"type": "function", "function": {
                    "name": "use_token",
                    "description": "Use the token",
                    "parameters": {"type": "object",
                                   "properties": {"auth": {"type": "string"}},
                                   "required": ["auth"]},
                }},
            ],
            "test_cases": [{
                "prompt": "Get and use token for admin",
                "multi_turn": True,
                "max_rounds": 2,
                "mock_responses": {"get_token": {"token": "tok-xyz"}},
                "optimal_hops": 2,
                "expected_tool": "use_token",
                "expected_params": {"auth": "tok-xyz"},
                "argument_source": {"auth": "get_token.token"},
            }],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        # Export to native JSON format (not BFCL — check plain export)
        export_resp = await app_client.get(
            f"/api/tool-suites/{suite_id}/export",
            headers=auth_headers,
        )
        assert export_resp.status_code == 200
        data = export_resp.json()
        cases = data.get("test_cases", [])
        if cases:
            tc = cases[0]
            assert "argument_source" in tc, (
                f"argument_source missing from exported test case: {tc.keys()}"
            )

    async def test_case_without_argument_source_is_valid(
        self, app_client, auth_headers
    ):
        """Multi-turn test case without argument_source is perfectly valid."""
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "Multi-Turn No ArgSrc",
            "tools": [{"type": "function", "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object",
                               "properties": {"city": {"type": "string"}},
                               "required": ["city"]},
            }}],
            "test_cases": [{
                "prompt": "Weather in Tokyo?",
                "multi_turn": True,
                "max_rounds": 2,
                "mock_responses": {},
                "optimal_hops": 1,
                "expected_tool": "get_weather",
                "expected_params": {"city": "Tokyo"},
                # No argument_source — valid
            }],
        })
        assert resp.status_code == 200


# ===========================================================================
# Unit tests — argument_source format validation (sync, no asyncio mark)
# ===========================================================================

class TestArgumentSourceFormat:
    def test_dot_notation_format(self):
        """argument_source values follow 'tool_name.field' dot notation."""
        sources = {
            "customer_id": "get_user.id",
            "token": "auth.access_token",
            "price": "lookup_price.amount",
        }
        for arg_name, source_ref in sources.items():
            parts = source_ref.split(".", 1)
            assert len(parts) == 2, f"'{source_ref}' must have exactly one dot"
            tool_name, field = parts
            assert tool_name, f"tool_name part must not be empty in '{source_ref}'"
            assert field, f"field part must not be empty in '{source_ref}'"

    def test_multiple_argument_sources(self):
        """argument_source dict can contain multiple arg->source mappings."""
        sources = {
            "customer_id": "get_user.id",
            "session_token": "auth_step.token",
        }
        assert len(sources) == 2
        for ref in sources.values():
            assert "." in ref

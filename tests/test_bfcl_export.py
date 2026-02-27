"""Tests for T4: BFCL Export/Import.

Tests GET /api/tool-suites/{suite_id}/export/bfcl
and POST /api/tool-eval/import/bfcl endpoints.

Run: uv run pytest tests/test_bfcl_export.py -v
"""

import json
import pytest
from routers.helpers import _parse_ground_truth_call, _normalize_bfcl_schema_types

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Minimal BFCL V3 entry for roundtrip tests
BFCL_SAMPLE = [
    {
        "id": "weather_0",
        "question": [[{"role": "user", "content": "What is the weather in Paris?"}]],
        "function": [
            {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            }
        ],
        "answer": [{"get_weather": {"city": "Paris"}}],
    }
]


# ===========================================================================
# Export tests
# ===========================================================================

class TestBFCLExport:
    async def _create_suite(self, app_client, auth_headers, name="BFCL Export Suite"):
        """Helper: create a minimal suite and return suite_id."""
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": name,
            "tools": [{"type": "function", "function": {
                "name": "get_weather",
                "description": "Get current weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }}],
            "test_cases": [
                {
                    "prompt": "What is the weather in Paris?",
                    "expected_tool": "get_weather",
                    "expected_params": {"city": "Paris"},
                    "category": "simple",
                }
            ],
        })
        assert resp.status_code == 200
        return resp.json()["suite_id"]

    async def test_export_returns_200(self, app_client, auth_headers):
        """BFCL export endpoint returns 200."""
        suite_id = await self._create_suite(app_client, auth_headers, "BFCL Export 200")
        resp = await app_client.get(
            f"/api/tool-suites/{suite_id}/export/bfcl",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    async def test_export_returns_json_array(self, app_client, auth_headers):
        """BFCL export returns a JSON array of entries."""
        suite_id = await self._create_suite(app_client, auth_headers, "BFCL Export Array")
        resp = await app_client.get(
            f"/api/tool-suites/{suite_id}/export/bfcl",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    async def test_export_entry_has_required_fields(self, app_client, auth_headers):
        """Each BFCL entry has id, question, function, answer fields."""
        suite_id = await self._create_suite(app_client, auth_headers, "BFCL Export Fields")
        resp = await app_client.get(
            f"/api/tool-suites/{suite_id}/export/bfcl",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        entries = resp.json()
        for entry in entries:
            assert "id" in entry, f"Missing 'id' field: {entry.keys()}"
            assert "question" in entry, f"Missing 'question' field: {entry.keys()}"
            assert "function" in entry, f"Missing 'function' field: {entry.keys()}"
            assert "answer" in entry, f"Missing 'answer' field: {entry.keys()}"

    async def test_export_question_is_array_of_arrays(self, app_client, auth_headers):
        """BFCL V3: question field is array-of-arrays (outer list, inner list of messages)."""
        suite_id = await self._create_suite(app_client, auth_headers, "BFCL Export Q Format")
        resp = await app_client.get(
            f"/api/tool-suites/{suite_id}/export/bfcl",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        for entry in resp.json():
            q = entry["question"]
            assert isinstance(q, list), "question must be a list"
            assert len(q) >= 1, "question must have at least one inner list"
            assert isinstance(q[0], list), "question[0] must be a list (BFCL V3 format)"

    async def test_export_function_has_no_api_call(self, app_client, auth_headers):
        """BFCL V3: function definitions must NOT include 'api_call' field."""
        suite_id = await self._create_suite(app_client, auth_headers, "BFCL Export No API Call")
        resp = await app_client.get(
            f"/api/tool-suites/{suite_id}/export/bfcl",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        for entry in resp.json():
            for fn in entry["function"]:
                assert "api_call" not in fn, (
                    f"BFCL V3 should not include 'api_call', found it in: {fn.keys()}"
                )

    async def test_export_answer_maps_tool_to_params(self, app_client, auth_headers):
        """BFCL answer field is a list of {tool_name: params} dicts."""
        suite_id = await self._create_suite(app_client, auth_headers, "BFCL Export Answer")
        resp = await app_client.get(
            f"/api/tool-suites/{suite_id}/export/bfcl",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        for entry in resp.json():
            answer = entry["answer"]
            assert isinstance(answer, list), "answer must be a list"
            for a in answer:
                assert isinstance(a, dict), "each answer entry must be a dict"
                # Keys are tool names, values are param dicts
                for tool_name, params in a.items():
                    assert isinstance(params, dict), f"params for {tool_name} must be a dict"

    async def test_export_category_as_test_category(self, app_client, auth_headers):
        """BFCL export includes test_category field when category is set."""
        # Import suite with categorized test case
        resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "BFCL Category Export",
            "tools": [{"type": "function", "function": {
                "name": "search",
                "description": "Search",
                "parameters": {"type": "object",
                               "properties": {"q": {"type": "string"}},
                               "required": ["q"]},
            }}],
            "test_cases": [{
                "prompt": "Search cats",
                "expected_tool": "search",
                "expected_params": {"q": "cats"},
                "category": "complex",
            }],
        })
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        export_resp = await app_client.get(
            f"/api/tool-suites/{suite_id}/export/bfcl",
            headers=auth_headers,
        )
        assert export_resp.status_code == 200
        entries = export_resp.json()
        if entries:
            # Categorized entries should include test_category
            assert entries[0].get("test_category") == "complex"

    async def test_export_404_for_nonexistent_suite(self, app_client, auth_headers):
        """Exporting a nonexistent suite returns 404."""
        resp = await app_client.get(
            "/api/tool-suites/nonexistent-suite-id-xyz/export/bfcl",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# Import tests
# ===========================================================================

class TestBFCLImport:
    async def test_import_bfcl_returns_200(self, app_client, auth_headers):
        """BFCL import endpoint returns 200."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=BFCL_SAMPLE,
        )
        assert resp.status_code == 200

    async def test_import_bfcl_creates_suite(self, app_client, auth_headers):
        """BFCL import creates a suite and returns suite_id."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=BFCL_SAMPLE,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "suite_id" in data
        assert data["suite_id"]

    async def test_import_bfcl_preserves_tool_name(self, app_client, auth_headers):
        """BFCL import preserves function names from the function array."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=BFCL_SAMPLE,
        )
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        detail_resp = await app_client.get(
            f"/api/tool-suites/{suite_id}",
            headers=auth_headers,
        )
        assert detail_resp.status_code == 200
        data = detail_resp.json()
        tools = data.get("tools", [])
        tool_names = [t.get("function", {}).get("name") for t in tools]
        assert "get_weather" in tool_names

    async def test_import_bfcl_creates_test_cases(self, app_client, auth_headers):
        """BFCL import creates test cases from the question/answer pairs."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=BFCL_SAMPLE,
        )
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        detail_resp = await app_client.get(
            f"/api/tool-suites/{suite_id}",
            headers=auth_headers,
        )
        assert detail_resp.status_code == 200
        cases = detail_resp.json().get("test_cases", [])
        assert len(cases) >= 1
        # Verify prompt extracted from question array
        prompts = [c.get("prompt") for c in cases]
        assert any("Paris" in (p or "") for p in prompts)

    async def test_roundtrip_export_import(self, app_client, auth_headers):
        """Export a suite to BFCL, import it back — suite reconstructed correctly."""
        # Create original suite
        orig_resp = await app_client.post("/api/tool-eval/import", headers=auth_headers, json={
            "name": "BFCL Roundtrip",
            "tools": [{"type": "function", "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object",
                               "properties": {"city": {"type": "string"}},
                               "required": ["city"]},
            }}],
            "test_cases": [{
                "prompt": "Weather in Berlin?",
                "expected_tool": "get_weather",
                "expected_params": {"city": "Berlin"},
            }],
        })
        assert orig_resp.status_code == 200
        orig_id = orig_resp.json()["suite_id"]

        # Export to BFCL
        export_resp = await app_client.get(
            f"/api/tool-suites/{orig_id}/export/bfcl",
            headers=auth_headers,
        )
        assert export_resp.status_code == 200
        bfcl_data = export_resp.json()

        # Import back
        import_resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=bfcl_data,
        )
        assert import_resp.status_code == 200
        new_id = import_resp.json()["suite_id"]

        # Verify new suite has test cases
        detail_resp = await app_client.get(
            f"/api/tool-suites/{new_id}",
            headers=auth_headers,
        )
        assert detail_resp.status_code == 200
        new_cases = detail_resp.json().get("test_cases", [])
        assert len(new_cases) >= 1

    async def test_import_requires_auth(self, app_client):
        """BFCL import without auth returns 401/403."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            json=BFCL_SAMPLE,
        )
        assert resp.status_code in (401, 403)

    async def test_import_valid_bfcl_v3_json(self, app_client, auth_headers):
        """BFCL import with valid V3 JSON (function, question, answer keys) returns 200."""
        bfcl_v3 = [
            {
                "function": [
                    {
                        "name": "get_weather",
                        "description": "Get current weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string", "description": "City name"},
                            },
                            "required": ["city"],
                        },
                    }
                ],
                "question": [[{"role": "user", "content": "What's the weather in Tokyo?"}]],
                "answer": [{"get_weather": {"city": "Tokyo"}}],
            }
        ]
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=bfcl_v3,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "suite_id" in data
        assert data["suite_id"]

    async def test_import_x_suite_name_header_sets_suite_name(self, app_client, auth_headers):
        """X-Suite-Name header sets the suite name on the imported suite."""
        suite_name = "My Custom BFCL Suite"
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers={**auth_headers, "X-Suite-Name": suite_name},
            json=BFCL_SAMPLE,
        )
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        detail_resp = await app_client.get(
            f"/api/tool-suites/{suite_id}",
            headers=auth_headers,
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["name"] == suite_name

    async def test_import_empty_array_returns_400(self, app_client, auth_headers):
        """BFCL import with empty array returns 400."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=[],
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_import_invalid_non_json_returns_400(self, app_client, auth_headers):
        """BFCL import with non-JSON body returns 400 with error message."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=b"not valid json at all!!!",
        )
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["error"]

    async def test_import_creates_correct_number_of_test_cases(self, app_client, auth_headers):
        """BFCL import creates exactly as many test cases as entries with valid questions."""
        multi_entry = [
            {
                "function": [
                    {
                        "name": "search",
                        "description": "Search the web",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ],
                "question": [[{"role": "user", "content": "Search for cats"}]],
                "answer": [{"search": {"query": "cats"}}],
            },
            {
                "function": [
                    {
                        "name": "search",
                        "description": "Search the web",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ],
                "question": [[{"role": "user", "content": "Search for dogs"}]],
                "answer": [{"search": {"query": "dogs"}}],
            },
            {
                "function": [
                    {
                        "name": "search",
                        "description": "Search the web",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ],
                "question": [[{"role": "user", "content": "Search for birds"}]],
                "answer": [{"search": {"query": "birds"}}],
            },
        ]
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=multi_entry,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["test_cases_created"] == 3

        detail_resp = await app_client.get(
            f"/api/tool-suites/{data['suite_id']}",
            headers=auth_headers,
        )
        assert detail_resp.status_code == 200
        assert len(detail_resp.json().get("test_cases", [])) == 3


# ===========================================================================
# Helper unit tests
# ===========================================================================

class TestParseGroundTruthCall:
    """Unit tests for _parse_ground_truth_call()."""

    def test_simple_kwargs(self):
        result = _parse_ground_truth_call("func(n=20, k=5)")
        assert result == {"func": {"n": 20, "k": 5}}

    def test_float_params(self):
        result = _parse_ground_truth_call("binomial(n=20, k=5, p=0.6)")
        assert result == {"binomial": {"n": 20, "k": 5, "p": 0.6}}

    def test_string_params(self):
        result = _parse_ground_truth_call("get_weather(city='Paris')")
        assert result == {"get_weather": {"city": "Paris"}}

    def test_empty_params(self):
        result = _parse_ground_truth_call("no_args()")
        assert result == {"no_args": {}}

    def test_list_param(self):
        result = _parse_ground_truth_call("func(items=[1, 2, 3])")
        assert result == {"func": {"items": [1, 2, 3]}}

    def test_math_expression_fallback(self):
        """BFCL data contains expressions like 1/6 that need eval fallback."""
        result = _parse_ground_truth_call("func(p=1/6)")
        assert result is not None
        assert "func" in result
        assert abs(result["func"]["p"] - 1 / 6) < 1e-10

    def test_invalid_returns_none(self):
        result = _parse_ground_truth_call("not a function call")
        assert result is None

    def test_malformed_returns_none(self):
        result = _parse_ground_truth_call("")
        assert result is None


class TestNormalizeBfclSchemaTypes:
    """Unit tests for _normalize_bfcl_schema_types()."""

    def test_dict_to_object(self):
        schema = {"type": "dict", "properties": {}}
        _normalize_bfcl_schema_types(schema)
        assert schema["type"] == "object"

    def test_float_to_number(self):
        schema = {"type": "float"}
        _normalize_bfcl_schema_types(schema)
        assert schema["type"] == "number"

    def test_tuple_to_array(self):
        schema = {"type": "tuple"}
        _normalize_bfcl_schema_types(schema)
        assert schema["type"] == "array"

    def test_long_to_integer(self):
        schema = {"type": "long"}
        _normalize_bfcl_schema_types(schema)
        assert schema["type"] == "integer"

    def test_standard_type_unchanged(self):
        schema = {"type": "string"}
        _normalize_bfcl_schema_types(schema)
        assert schema["type"] == "string"

    def test_nested_properties(self):
        schema = {
            "type": "object",
            "properties": {
                "data": {"type": "dict", "properties": {"val": {"type": "float"}}},
            },
        }
        _normalize_bfcl_schema_types(schema)
        assert schema["properties"]["data"]["type"] == "object"
        assert schema["properties"]["data"]["properties"]["val"]["type"] == "number"

    def test_array_items(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": [{"type": "float"}]},
            },
        }
        _normalize_bfcl_schema_types(schema)
        assert schema["properties"]["items"]["items"][0]["type"] == "number"


# ===========================================================================
# Raw BFCL format import tests (HuggingFace dataset files)
# ===========================================================================

# Simulates a raw BFCL exec_simple entry with ground_truth call strings
RAW_BFCL_SIMPLE = [
    {
        "id": "exec_simple_0",
        "question": [[{"role": "user", "content": "Calculate binomial probability"}]],
        "function": [
            {
                "name": "binomial_probability",
                "description": "Calculate binomial probability",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer", "description": "trials"},
                        "k": {"type": "integer", "description": "successes"},
                        "p": {"type": "float", "description": "probability"},
                    },
                    "required": ["n", "k", "p"],
                },
            }
        ],
        "ground_truth": ["binomial_probability(n=20, k=5, p=0.6)"],
    }
]

# Simulates a raw BFCL exec_parallel entry with multiple ground_truth calls
RAW_BFCL_PARALLEL = [
    {
        "id": "exec_parallel_0",
        "question": [[{"role": "user", "content": "Get weather in Paris and Tokyo"}]],
        "function": [
            {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ],
        "ground_truth": [
            "get_weather(city='Paris')",
            "get_weather(city='Tokyo')",
        ],
    }
]

# Simulates a raw BFCL irrelevance entry (no answer, no ground_truth)
RAW_BFCL_IRRELEVANCE = [
    {
        "id": "irrelevance_0",
        "question": [[{"role": "user", "content": "What is the meaning of life?"}]],
        "function": [
            {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ],
    }
]


class TestRawBFCLImport:
    """Tests for importing raw BFCL dataset files from HuggingFace."""

    async def test_ground_truth_simple_import(self, app_client, auth_headers):
        """Raw BFCL entry with ground_truth call string imports successfully."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=RAW_BFCL_SIMPLE,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["test_cases_created"] == 1

        detail = await app_client.get(
            f"/api/tool-suites/{data['suite_id']}", headers=auth_headers
        )
        cases = detail.json().get("test_cases", [])
        assert len(cases) == 1
        assert cases[0]["expected_tool"] == "binomial_probability"

    async def test_ground_truth_parallel_uses_first_call(self, app_client, auth_headers):
        """Raw BFCL parallel entry with 2 ground_truth calls creates 1 test case (first call only)."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=RAW_BFCL_PARALLEL,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["test_cases_created"] == 1

        detail = await app_client.get(
            f"/api/tool-suites/{data['suite_id']}", headers=auth_headers
        )
        cases = detail.json().get("test_cases", [])
        assert len(cases) == 1
        ep = cases[0]["expected_params"]
        params = json.loads(ep) if isinstance(ep, str) else ep
        # First call in RAW_BFCL_PARALLEL is Paris
        assert params["city"] == "Paris"

    async def test_irrelevance_detection(self, app_client, auth_headers):
        """Entry with no answer/ground_truth creates irrelevance test case."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=RAW_BFCL_IRRELEVANCE,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["test_cases_created"] == 1

        detail = await app_client.get(
            f"/api/tool-suites/{data['suite_id']}", headers=auth_headers
        )
        cases = detail.json().get("test_cases", [])
        assert len(cases) == 1
        assert cases[0]["expected_tool"] is None
        assert cases[0].get("category") == "irrelevance"

    async def test_schema_type_normalization(self, app_client, auth_headers):
        """Non-standard types (float, dict) are normalized in imported tools."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=RAW_BFCL_SIMPLE,  # has "float" type
        )
        assert resp.status_code == 200
        suite_id = resp.json()["suite_id"]

        detail = await app_client.get(
            f"/api/tool-suites/{suite_id}", headers=auth_headers
        )
        tools = detail.json().get("tools", [])
        # The "p" parameter should be "number" not "float"
        p_param = tools[0]["function"]["parameters"]["properties"]["p"]
        assert p_param["type"] == "number"

    async def test_jsonl_body_format(self, app_client, auth_headers):
        """JSONL (newline-delimited JSON) body is accepted."""
        lines = [json.dumps(entry) for entry in RAW_BFCL_SIMPLE]
        jsonl_body = "\n".join(lines)
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=jsonl_body.encode("utf-8"),
        )
        assert resp.status_code == 200
        assert resp.json()["test_cases_created"] >= 1

    async def test_mixed_answer_and_ground_truth(self, app_client, auth_headers):
        """Entries with answer format are still handled alongside ground_truth entries."""
        mixed = [
            # Standard answer format
            {
                "question": [[{"role": "user", "content": "Weather in London?"}]],
                "function": [{"name": "get_weather", "description": "Get weather",
                              "parameters": {"type": "object",
                                             "properties": {"city": {"type": "string"}},
                                             "required": ["city"]}}],
                "answer": [{"get_weather": {"city": "London"}}],
            },
            # Raw ground_truth format
            {
                "question": [[{"role": "user", "content": "Weather in Berlin?"}]],
                "function": [{"name": "get_weather", "description": "Get weather",
                              "parameters": {"type": "object",
                                             "properties": {"city": {"type": "string"}},
                                             "required": ["city"]}}],
                "ground_truth": ["get_weather(city='Berlin')"],
            },
        ]
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=mixed,
        )
        assert resp.status_code == 200
        assert resp.json()["test_cases_created"] == 2

    async def test_backward_compat_existing_format(self, app_client, auth_headers):
        """Original BFCL_SAMPLE (structured answer) still works unchanged."""
        resp = await app_client.post(
            "/api/tool-eval/import/bfcl",
            headers=auth_headers,
            json=BFCL_SAMPLE,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["test_cases_created"] == 1

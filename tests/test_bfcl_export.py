"""Tests for T4: BFCL Export/Import.

Tests GET /api/tool-suites/{suite_id}/export/bfcl
and POST /api/tool-eval/import/bfcl endpoints.

Run: uv run pytest tests/test_bfcl_export.py -v
"""

import json
import pytest

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

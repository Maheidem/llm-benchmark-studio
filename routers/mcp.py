"""MCP integration routes (discover tools, import as suites)."""

import asyncio
import json
import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

from mcp import ClientSession
from mcp.client.sse import sse_client

import auth
import db
from routers.helpers import _validate_tools

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])


async def discover_mcp_tools(url: str, timeout: float = 10.0) -> dict:
    """Connect to an MCP server via SSE and return discovered tools.

    Raises ValueError for invalid URLs, TimeoutError for timeouts,
    and ConnectionError for connection failures.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    if not parsed.hostname:
        raise ValueError("Invalid URL: missing hostname")

    try:
        async with sse_client(url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(session.initialize(), timeout=timeout)
                result = await asyncio.wait_for(session.list_tools(), timeout=timeout)

                server_name = "unknown"
                if hasattr(session, "server_info") and session.server_info:
                    server_name = getattr(session.server_info, "name", "unknown")

                return {
                    "server_name": server_name,
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "inputSchema": t.inputSchema if t.inputSchema else {"type": "object", "properties": {}},
                            "parameter_count": len((t.inputSchema or {}).get("properties", {})),
                        }
                        for t in result.tools
                    ],
                }
    except asyncio.TimeoutError:
        raise TimeoutError("Connection timed out. The MCP server may be unreachable.")
    except OSError as e:
        raise ConnectionError(
            f"Could not connect to MCP server. Check the URL and ensure the server is running. ({e})"
        )
    except Exception as e:
        if isinstance(e, (ValueError, TimeoutError, ConnectionError)):
            raise
        raise ConnectionError(
            f"The server responded but doesn't appear to be a valid MCP server. ({type(e).__name__}: {e})"
        )


def mcp_tool_to_openai(mcp_tool: dict) -> dict:
    """Convert an MCP tool schema to OpenAI function calling format."""
    description = mcp_tool.get("description", "")
    if len(description) > 1024:
        description = description[:1021] + "..."

    return {
        "type": "function",
        "function": {
            "name": mcp_tool["name"],
            "description": description,
            "parameters": mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
        },
    }


def generate_test_case(tool: dict) -> dict:
    """Generate a sample test case from an OpenAI-format tool definition."""
    fn = tool["function"]
    params = fn.get("parameters", {})
    properties = params.get("properties", {})
    required = params.get("required", [])

    # Build example params from required fields only
    example_params = {}
    for name, schema in properties.items():
        if name in required:
            example_params[name] = _example_value(name, schema)

    # Build a concrete prompt that includes example param values
    desc = fn.get("description", fn["name"])
    if example_params:
        param_parts = [f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                       for k, v in example_params.items()]
        prompt = f"Use the {fn['name']} tool: {desc.rstrip('.')}. Use these values: {', '.join(param_parts)}"
    else:
        prompt = f"Use the {fn['name']} tool to {desc.lower().rstrip('.')}"

    return {
        "prompt": prompt,
        "expected_tool": fn["name"],
        "expected_params": example_params if example_params else None,
    }


def _example_value(param_name: str, schema: dict):
    """Generate a realistic placeholder value based on param name and JSON Schema."""
    t = schema.get("type", "string")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "string":
        # Use param name to generate realistic values instead of description text
        name_lower = param_name.lower()
        if "url" in name_lower or "uri" in name_lower or "link" in name_lower:
            return "https://example.com"
        if "path" in name_lower or "file" in name_lower:
            return "/tmp/example.txt"
        if "email" in name_lower:
            return "user@example.com"
        if "name" in name_lower:
            return "example"
        if "query" in name_lower or "search" in name_lower:
            return "test query"
        if "selector" in name_lower or "css" in name_lower:
            return "#main-content"
        if "city" in name_lower or "location" in name_lower:
            return "San Francisco"
        if "code" in name_lower or "script" in name_lower:
            return "console.log('hello')"
        return "example"
    if t == "number" or t == "integer":
        return 42
    if t == "boolean":
        return True
    if t == "array":
        return []
    return "example"


@router.post("/api/mcp/discover")
async def mcp_discover(request: Request, user: dict = Depends(auth.get_current_user)):
    """Connect to an MCP server and return discovered tools."""
    try:
        body = await request.json()
    except Exception:
        logger.debug("MCP discover: invalid/empty request body")
        return JSONResponse({"error": "url is required"}, status_code=400)
    url = (body.get("url") or "").strip()

    if not url:
        return JSONResponse({"error": "url is required"}, status_code=400)

    try:
        result = await discover_mcp_tools(url, timeout=10.0)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except TimeoutError as e:
        return JSONResponse({"error": str(e)}, status_code=504)
    except ConnectionError as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    if not result["tools"]:
        return JSONResponse(
            {"error": "Connected successfully, but the server has no tools available."},
            status_code=200,
        )

    return {
        "status": "ok",
        "server_name": result["server_name"],
        "tools": result["tools"],
        "tool_count": len(result["tools"]),
    }


@router.post("/api/mcp/import")
async def mcp_import(request: Request, user: dict = Depends(auth.get_current_user)):
    """Import selected MCP tools as a new tool suite."""
    try:
        body = await request.json()
    except Exception:
        logger.debug("MCP import: invalid/empty request body")
        return JSONResponse({"error": "No tools selected"}, status_code=400)
    tools = body.get("tools", [])
    suite_name = (body.get("suite_name") or "").strip()
    suite_description = body.get("suite_description", "")
    generate_tests = body.get("generate_test_cases", False)

    if not tools:
        return JSONResponse({"error": "No tools selected"}, status_code=400)

    # Default suite name from first 3 tool names
    if not suite_name:
        names = [t.get("name", "?") for t in tools[:3]]
        suffix = "..." if len(tools) > 3 else ""
        suite_name = f"MCP: {', '.join(names)}{suffix}"

    # Deduplicate tool names
    seen_names = {}
    for tool in tools:
        name = tool["name"]
        if name in seen_names:
            seen_names[name] += 1
            tool["name"] = f"{name}_{seen_names[name]}"
        else:
            seen_names[name] = 1

    # Convert MCP schemas to OpenAI format
    openai_tools = [mcp_tool_to_openai(t) for t in tools]

    # Validate converted tools using existing validator
    err = _validate_tools(openai_tools)
    if err:
        return JSONResponse({"error": f"Schema conversion error: {err}"}, status_code=400)

    # Create suite via existing DB function
    suite_id = await db.create_tool_suite(
        user["id"], suite_name, suite_description, json.dumps(openai_tools)
    )

    # Generate test cases if requested
    test_cases_generated = 0
    if generate_tests:
        for tool in openai_tools:
            tc = generate_test_case(tool)
            await db.create_test_case(
                suite_id,
                tc["prompt"],
                tc["expected_tool"],
                json.dumps(tc["expected_params"]) if tc["expected_params"] else None,
                "exact",
            )
            test_cases_generated += 1

    return {
        "status": "ok",
        "suite_id": suite_id,
        "tools_imported": len(openai_tools),
        "test_cases_generated": test_cases_generated,
    }

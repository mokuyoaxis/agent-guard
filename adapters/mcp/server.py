#!/usr/bin/env python3
"""Optional, workspace-bound MCP stdio adapter for the existing guard CLIs."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SDK_VERSION = "2.3.0"
INSTRUCTIONS = (
    "Use these tools for explicit filesystem maintenance in the server's fixed workspace. "
    "Ordinary safe_delete preserves recovery copies and does not free their disk space; "
    "proven regenerable artifacts may be physically removed. Record returned transaction IDs. "
    "Inspect transactions before restoring. Never bypass a refusal, change session identity, "
    "or retry an uncertain mutation automatically. Large batches and operator-only actions "
    "remain in the existing CLI workflow. GC here is preview only. These tools do not "
    "intercept other shell or editor operations."
)

TOOL_DEFINITIONS = {
    "safe_delete": {
        "description": "Delete explicit literal paths using existing guard policy, budgets and audit. Ordinary files are quarantined with a recovery transaction; proven ignored regenerable artifacts may be permanently removed. No shell or glob expansion. Quarantine targets are refused. Large batches require the operator's existing Cleanup CLI workflow.",
        "properties": {"paths": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 1000},
                       "dry_run": {"type": "boolean", "default": False}},
        "required": ["paths"], "read_only": False, "destructive": True,
    },
    "restore": {
        "description": "Restore one transaction returned by transactions, without overwriting an existing occupant. No force option. Restoration does not refund deletion budget. Inspect the returned per-item result and filesystem after partial errors.",
        "properties": {"txid": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]*$"}},
        "required": ["txid"], "read_only": False, "destructive": False,
    },
    "transactions": {
        "description": "List recovery transactions and their RESTORABLE/RESTORED state without changing workspace files. Use returned transaction IDs for ordinary restore.",
        "properties": {}, "required": [], "read_only": True, "destructive": False,
    },
    "status": {
        "description": "Read guard state, recovery storage and persistent current-session budget. A BLOCKED deletion-admission field can coexist with a successful status inspection. Does not reset budgets or change modes.",
        "properties": {"tail": {"type": "integer", "minimum": 0, "maximum": 100, "default": 10}},
        "required": [], "read_only": True, "destructive": False,
    },
    "gc_plan": {
        "description": "Preview existing quarantine transaction GC eligibility and retained space. This tool never executes GC or deletes recovery copies. Execution remains an explicit operator CLI action.",
        "properties": {}, "required": [], "read_only": True, "destructive": False,
    },
}


def create_server(operations):
    """Load the optional SDK only when starting the adapter, never for --help."""
    import anyio
    import mcp_types as types
    from mcp.server import Server

    schemas = {name: {"type": "object", "properties": value["properties"],
                      "required": value["required"], "additionalProperties": False}
               for name, value in TOOL_DEFINITIONS.items()}
    tools = [types.Tool(
        name=name, description=value["description"], input_schema=schemas[name],
        annotations=types.ToolAnnotations(read_only_hint=value["read_only"],
                                          destructive_hint=value["destructive"],
                                          idempotent_hint=False, open_world_hint=False))
        for name, value in TOOL_DEFINITIONS.items()]

    async def list_tools(context, params):
        return types.ListToolsResult(tools=tools)

    async def call_tool(context, params):
        # Do not abandon a mutation thread on protocol cancellation: it may
        # already have written recovery evidence. The caller must inspect
        # state after a disconnect instead of inferring rollback or retrying.
        from adapters.mcp.operations import InvalidArguments

        try:
            result = await anyio.to_thread.run_sync(operations.call, params.name, params.arguments or {})
        except InvalidArguments as exc:
            result = {"tool": params.name, "ok": False, "exit_code": None,
                      "error": {"code": "MCP_INVALID_ARGUMENTS", "message": str(exc),
                                "may_have_changed": False, "inspect_tools": []}}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=True))],
            structured_content=result, is_error=not result["ok"])

    package_version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    return Server("agent-guard", version=package_version, instructions=INSTRUCTIONS,
                  get_tool_input_schema=schemas.get,
                  on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(operations):
    from mcp.server.stdio import stdio_server

    server = create_server(operations)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local agent-guard MCP tools over stdio (optional SDK).")
    parser.add_argument("--workspace", required=True, help="Existing workspace directory, fixed for this process")
    parser.add_argument("--session", help="Trusted stable budget identity; omitted uses the shared CLI bucket")
    args = parser.parse_args(argv)
    from adapters.mcp.operations import Operations

    try:
        operations = Operations(args.workspace, session=args.session)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    try:
        version = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        parser.exit(1, "MCP SDK is optional and is not installed. Use a separate Python environment and adapters/mcp/requirements.txt.\n")
    if version != SDK_VERSION:
        parser.exit(1, "Unsupported MCP SDK version. Install the pinned adapters/mcp/requirements.txt in a separate environment.\n")
    asyncio.run(serve(operations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    "For routine inspection prefer status(view=overview): bounded metadata, task IDs and budget, no payload size walk. "
    "Ordinary safe_delete preserves recovery copies and does not free their disk space; "
    "proven regenerable artifacts may be physically removed. Record returned transaction IDs. "
    "When recovery is required, set quarantine=true on safe_delete or a new cleanup_plan, including for regenerable artifacts. "
    "This choice is retained in a saved plan and does not lift admission or application cache consistency requirements. "
    "Inspect transactions before restoring. Never bypass a refusal, change session identity, "
    "or retry an uncertain mutation automatically. Use cleanup_plan for literal-target proposals; "
    "save=true explicitly stores a reviewable selection. The operator approves its saved ID "
    "in the same package's terminal CLI; then use cleanup_execute with the returned task_id. "
    "Never approve on the operator's behalf, allocate a PTY, or supply approval booleans/tokens. "
    "Use cleanup_task_status after partial work, timeout or response loss; an attempted task never resumes. "
    "GC here is preview only. These tools do not "
    "intercept other shell or editor operations."
    " Use cleanup_discover for a bounded directory overview and cleanup_measure for explicit scopes. "
    "Their observations never grant execution permission. A successful call with partial=true "
    "still has incomplete coverage or unknown whole sizes; inspect those fields and use exact scopes."
)

TOOL_DEFINITIONS = {
    "safe_delete": {
        "description": "Delete explicit literal paths using existing guard policy, budgets and audit. Ordinary files are quarantined with a recovery transaction; proven ignored regenerable artifacts may be permanently removed. Set quarantine=true to require quarantine for this request, including regenerable artifacts; failure never falls back to direct deletion or GC. No shell or glob expansion. Quarantine targets are refused. For large batches use cleanup_plan, operator CLI approval, then cleanup_execute(task_id). A dry_run includes result.admission with current limits/approval status and supported next steps; ok=true reports preview completion, not execution approval.",
        "properties": {"paths": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 1000},
                       "dry_run": {"type": "boolean", "default": False},
                       "quarantine": {"type": "boolean", "default": False,
                                      "description": "Require recovery quarantine even for regenerable artifacts; does not bypass protection, limits or batch approval."}},
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
        "description": "Read guard state and persistent current-session budget. Default full preserves legacy status. view=overview returns bounded current-budget task rows, existing transaction reconciliation and age-only GC hints without payload tree/size walks. limit=1..50 controls overview rows (default 10); tail applies to full only. Complete/partial metadata is not absence, grant readiness or host interception. Occupancy and capacity-based GC candidates remain unmeasured in overview. No raw tokens/session identity/manifest records are returned. Does not reset budgets or change modes.",
        "properties": {"tail": {"type": "integer", "minimum": 0, "maximum": 100, "default": 10},
                       "view": {"type": "string", "enum": ["overview", "full"], "default": "full"},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Overview only; default 10 rows per list."}},
        "required": [], "read_only": True, "destructive": False,
    },
    "gc_plan": {
        "description": "Preview existing quarantine transaction GC eligibility and retained space. This tool never executes GC or deletes recovery copies. Execution remains an explicit operator CLI action.",
        "properties": {}, "required": [], "read_only": True, "destructive": False,
    },
    "cleanup_discover": {
        "description": "Read-only bounded directory overview in the fixed workspace. Optional scopes continue discovery below exact directories. Runtime/cache ownership labels are hints, not proof of obsolescence. Sizes remain unknown; inspect coverage/unexpanded and partial even when ok=true. No executable plan or deletion permission.",
        "properties": {"scopes": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 64},
                       "max_entries": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 10000},
                       "max_depth": {"type": "integer", "minimum": 1, "maximum": 4, "default": 4}},
        "required": [], "read_only": True, "destructive": False,
    },
    "cleanup_measure": {
        "description": "Read-only metadata measurement of explicit non-overlapping directory scopes below the same workspace. Does not follow links or traverse protected subtrees. size_bytes=null means unknown, not zero; observed_bytes excludes protected data. Core projection is not approval. No plan_id, deletion, restore or GC execution.",
        "properties": {"scopes": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 64},
                       "max_entries": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 20000},
                       "max_depth": {"type": "integer", "minimum": 1, "maximum": 32, "default": 32}},
        "required": ["scopes"], "read_only": True, "destructive": False,
    },
    "cleanup_plan": {
        "description": "Propose cleanup for explicit literal targets in the fixed workspace. A target list alone does not declare content obsolete. If the task already confirms every target discarded, include discard_targets=true in the initial proposal; for a mixed batch declare only confirmed obsolete paths with discard. These declarations never approve execution or override protection. Default planning writes nothing and returns a compact summary. save=true explicitly stores a reviewable plan/selection. Optional select requires save=true. Read a saved_plan_id with optional view/filter/page controls, never target or declaration overrides. view=details returns bounded rows; view=full preserves full JSON. Filters affect display only. Saved reads do not rescan. Incomplete plans are partial data, not permission. Follow the operator command and binding for terminal approval before cleanup_execute.",
        "properties": {
            "targets": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 1000},
            "saved_plan_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "save": {"type": "boolean", "default": False},
            "quarantine": {"type": "boolean", "default": False,
                           "description": "New plans only: retain quarantine for all selectable targets, including regenerable artifacts. Bound into saved plan and approval; task execution inherits it."},
            "discard_targets": {"type": "boolean", "default": False, "description": "Declare all requested targets discarded only when already confirmed by the task. Include in the initial proposal for such a batch; this is not execution approval."},
            "discard": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 1000},
            "protect": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 1000},
            "select": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 1000},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 10000},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 32, "default": 32},
            "view": {"type": "string", "enum": ["summary", "details", "full"], "default": "summary"},
            "category": {"type": "string", "enum": ["regenerable", "discarded", "uncertain", "protected"], "description": "Details only; display filter, not selection."},
            "path": {"type": "string", "minLength": 1, "description": "Details only; literal relative path/subtree, no glob or rescan."},
            "offset": {"type": "integer", "minimum": 0, "description": "Details only; default 0."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Details only; default 50."}},
        "required": [],
        "one_of": [{"required": ["targets"], "not": {"required": ["saved_plan_id"]}},
                   {"required": ["saved_plan_id"],
                    "propertyNames": {"enum": ["saved_plan_id", "view", "category", "path", "offset", "limit"]}}],
        "read_only": False, "destructive": False,
    },
    "cleanup_execute": {
        "description": "Attempt one operator-approved cleanup task by task_id, returning a receipt-derived summary by default or full JSON with view=full. No new terminal prompt, approval token, confirm flag, target edits or session override. Shared Core revalidates the request/admission, then safe_delete performs approved effects. Claimed tasks never resume or replay. Query cleanup_task_status for details after partial/unknown outcomes. Byte estimates are not actual freed space. Approval does not lift protection, budgets or capacity; no GC execution.",
        "properties": {"task_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                       "view": {"type": "string", "enum": ["summary", "full"], "default": "summary"}},
        "required": ["task_id"], "read_only": False, "destructive": True,
    },
    "cleanup_task_status": {
        "description": "Read a task's last durable receipts: default summary; view=details returns bounded per-root rows; view=full returns the complete public receipt. Optional detail filters never change execution targets. No source scan, lock creation, debit or retry. ok=true means readable, not completed; RUNNING is not process liveness. can_execute is not source/budget readiness. Unconfirmed and not_dispatched are distinct. Pages may change while running; tokens/raw session identity are never returned. Inspect evidence before new work.",
        "properties": {"task_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                       "view": {"type": "string", "enum": ["summary", "details", "full"], "default": "summary"},
                       "state": {"type": "string", "enum": ["confirmed", "unconfirmed", "not_dispatched"], "description": "Details only; not_dispatched is separate from dispatched but unconfirmed."},
                       "path": {"type": "string", "minLength": 1, "description": "Details only; literal relative path/subtree."},
                       "offset": {"type": "integer", "minimum": 0, "description": "Details only; default 0."},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Details only; default 50."}},
        "required": ["task_id"], "read_only": True, "destructive": False,
    },
}


def create_server(operations):
    """Load the optional SDK only when starting the adapter, never for --help."""
    import anyio
    import mcp_types as types
    from mcp.server import Server

    schemas = {name: {"type": "object", "properties": value["properties"],
                      "required": value["required"], "additionalProperties": False,
                      **({"oneOf": value["one_of"]} if "one_of" in value else {})}
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
    parser.add_argument("--trash-root", help="Trusted absolute quarantine directory; default: WORKSPACE/.agent-trash. No migration")
    parser.add_argument("--session", help="Trusted stable budget identity; omitted uses the shared CLI bucket")
    args = parser.parse_args(argv)
    from adapters.mcp.operations import Operations

    try:
        operations = Operations(args.workspace, trash_root=args.trash_root, session=args.session)
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

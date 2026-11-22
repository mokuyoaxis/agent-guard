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

from core.redaction import CHANNELS
from core.safe_view import FORMATS

SDK_VERSION = "2.3.0"
INSTRUCTIONS = (
    "Use these tools for explicit filesystem maintenance in the server's fixed workspace. "
    "For routine inspection prefer status(view=overview): bounded metadata, task IDs and budget, no payload size walk. "
    "Ordinary safe_delete preserves recovery copies and does not free their disk space; "
    "proven regenerable artifacts may be physically removed. Record returned transaction IDs. "
    "When recovery is required, set quarantine=true on safe_delete or a new cleanup_plan, including for regenerable artifacts. "
    "This choice is retained in a saved plan and does not lift admission or application cache consistency requirements. "
    "Inspect transactions and use restore_preview before restoring. Never bypass a refusal, change session identity, "
    "or retry an uncertain mutation automatically. Use cleanup_plan for literal-target proposals; "
    "save=true explicitly stores a reviewable selection. The operator approves its saved ID "
    "in the same package's terminal CLI; then use cleanup_execute with the returned task_id. "
    "Never approve on the operator's behalf, allocate a PTY, or supply approval booleans/tokens. "
    "Use cleanup_task_status after partial work, timeout or response loss; an attempted task never resumes. "
    "GC here is preview only. These tools do not "
    "intercept other shell or editor operations."
    " Use cleanup_discover for a bounded directory overview and cleanup_measure for explicit scopes. "
    "Their observations never grant execution permission. A successful call with partial=true "
    "still has incomplete coverage or unknown whole sizes; inspect those fields and use exact scopes. "
    "Use config_safe_view to inspect configuration structure without reading values into context. "
    "Use exfil_check or exfil_sanitize with the actual outbound channel before sending draft text. "
    "If exfil_check returns SANITIZE, apply redaction before sending. Neither text tool sends the payload."
)

HOST_APPROVAL_INSTRUCTIONS = (
    " This service has operator-enabled Codex host confirmation. Saved cleanup_plan results include "
    "a complete read-only review file and host_approval receipt. After the operator reviews the full selection, "
    "use cleanup_request_approval(saved_plan_id) to ask the host; only the host reply can authorize issuance. "
    "It never deletes. Repeated calls reuse durable state without re-prompting. After disconnection read "
    "cleanup_plan(saved_plan_id) to locate the receipt/task, then use the existing task query/execution. "
    "Default is no approval; never auto-answer the form, generate consent or enable this channel yourself. "
    "The terminal CLI remains the fallback for unsupported hosts or a fresh operator decision."
)

APPROVAL_FORM = {"type": "object", "properties": {
    "decision": {"type": "string", "title": "本次决定", "enum": ["暂不批准", "仅批准本次"], "default": "暂不批准"},
    "reviewed": {"type": "boolean", "title": "我已审阅完整清单及选中范围", "default": False}},
    "required": ["decision", "reviewed"]}

TOOL_DEFINITIONS = {
    "config_safe_view": {
        "description": "Read a workspace-relative JSON, dotenv or TOML config as keys, types and set/empty states. Values stay local. TOML requires Python 3.11 or newer.",
        "properties": {"path": {"type": "string", "minLength": 1},
                       "format": {"type": "string", "enum": list(FORMATS), "default": "auto"}},
        "required": ["path"], "read_only": True, "destructive": False,
    },
    "exfil_check": {
        "description": "Assess a draft for the explicit egress channel. Results carry scan metadata. SANITIZE requires a rewrite before sending. Optional path is the relative destination for workspace exemptions.",
        "properties": {"text": {"type": "string"}, "channel": {"type": "string", "enum": sorted(CHANNELS)},
                       "path": {"type": "string", "minLength": 1}},
        "required": ["text", "channel"], "read_only": True, "destructive": False,
    },
    "exfil_sanitize": {
        "description": "Return permitted text or rewrite it and check it again for the explicit egress channel. ASK and BLOCK withhold the text. Passing a draft to this tool does not remove it from existing model context.",
        "properties": {"text": {"type": "string"}, "channel": {"type": "string", "enum": sorted(CHANNELS)}},
        "required": ["text", "channel"], "read_only": True, "destructive": False,
    },
    "safe_delete": {
        "description": "Delete explicit literal paths using existing guard policy, budgets and audit. Ordinary files are quarantined with a recovery transaction; proven ignored regenerable artifacts may be permanently removed. Set quarantine=true to require quarantine for this request, including regenerable artifacts; failure never falls back to direct deletion or GC. No shell or glob expansion. Quarantine targets are refused. For large batches use cleanup_plan, operator CLI approval, then cleanup_execute(task_id). A dry_run includes result.admission with current limits/approval status and supported next steps; ok=true reports preview completion, not execution approval.",
        "properties": {"paths": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 1000},
                       "dry_run": {"type": "boolean", "default": False},
                       "quarantine": {"type": "boolean", "default": False,
                                      "description": "Require recovery quarantine even for regenerable artifacts; does not bypass protection, limits or batch approval."}},
        "required": ["paths"], "read_only": False, "destructive": True,
    },
    "restore": {
        "description": ("Restore one transaction returned by transactions, without overwriting an existing occupant. "
                        "No force option. Ordinary restore leaves deletion budget usage unchanged. "
                        "Inspect the returned per-item result and filesystem after partial errors."),
        "properties": {"txid": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]*$"}},
        "required": ["txid"], "read_only": False, "destructive": False,
    },
    "restore_preview": {
        "description": ("Read-only restore preflight for one transaction ID returned by transactions. "
                        "Checks workspace/quarantine boundaries, missing copies, failed cross-filesystem manual_review items, "
                        "and compares occupied origins against quarantine copies without modifying files or writing audit logs. "
                        "Never overwrites or exposes --force."),
        "properties": {"txid": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]*$"}},
        "required": ["txid"], "read_only": True, "destructive": False,
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
    "cleanup_request_approval": {
        "description": "Request operator confirmation through the explicitly enabled Codex host channel for one saved selection. Only saved_plan_id is accepted. Review the complete exported list first; the form defaults to no approval and requires an explicit reviewed declaration. No deletion, approval booleans/tokens, target edits, identity changes or automatic re-prompt. After response loss read cleanup_plan(saved_plan_id) before further work. The host's reply is a trusted channel, not independent human attestation. Terminal approval remains available.",
        "properties": {"saved_plan_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
        "required": ["saved_plan_id"], "read_only": False, "destructive": False,
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
    from adapters.mcp.operations import EXFIL_PAYLOAD_TOOLS

    definitions = {name: value for name, value in TOOL_DEFINITIONS.items()
                   if name != "cleanup_request_approval" or operations.approval_channel == "codex"}
    if operations.approval_channel == "codex":
        definitions = {name: {**value, "description": value["description"]
                             .replace("operator CLI approval", "operator confirmation")
                             .replace("Follow the operator command and binding for terminal approval",
                                      "Follow the returned complete-list review, host confirmation and binding")}
                       for name, value in definitions.items()}
    schemas = {name: {"type": "object", "properties": value["properties"],
                      "required": value["required"], "additionalProperties": False,
                      **({"oneOf": value["one_of"]} if "one_of" in value else {})}
               for name, value in definitions.items()}
    tools = [types.Tool(
        name=name, description=value["description"], input_schema=schemas[name],
        annotations=types.ToolAnnotations(read_only_hint=(value["read_only"] and
                                                          not (operations.audit_exfil and name in EXFIL_PAYLOAD_TOOLS)),
                                          destructive_hint=value["destructive"],
                                          idempotent_hint=False, open_world_hint=False))
        for name, value in definitions.items()]

    async def list_tools(context, params):
        return types.ListToolsResult(tools=tools)

    async def request_approval(context, arguments):
        from adapters.mcp.operations import APPROVAL_TOOL
        from mcp import MCPError
        from mcp.shared.exceptions import NoBackChannelError
        from pydantic import ValidationError

        args = operations.validate(APPROVAL_TOOL, arguments)
        saved_id = args["saved_plan_id"]
        capabilities = context.session.client_capabilities
        elicitation = capabilities.elicitation if capabilities is not None else None
        if (elicitation is None or (elicitation.form is None
                                   and elicitation.model_dump(exclude_none=True))):
            return operations._failure(APPROVAL_TOOL, "MCP_APPROVAL_UNSUPPORTED",
                                       "Host form elicitation is unavailable; use the saved plan's operator terminal command")
        prepared = await anyio.to_thread.run_sync(operations.host_approval, "prepare", saved_id)
        if not prepared["ok"] or not prepared["result"].get("prompt_required"):
            return prepared
        receipt = prepared["result"]
        overview = receipt["review"]["overview"]
        message = "\n".join([
            f"Cleanup: {overview['selected_roots']} roots; {overview['known_logical_bytes']} logical B; {overview['unknown_size_roots']} unknown sizes.",
            f"Quarantine: {overview['quarantine_roots']}; PERMANENT DELETE: {overview['permanent_delete_roots']}. No GC.",
            "Workspace: " + json.dumps(operations.workspace, ensure_ascii=False),
            "Review the COMPLETE list before approving:",
            json.dumps(receipt["review"]["file"], ensure_ascii=False),
            "Approve saved plan " + saved_id[:16] + " once; execution and admission checks follow separately.",
        ])
        outcome = "CANCELLED"
        try:
            with anyio.fail_after(300):
                reply = await context.session.elicit_form(message=message, requested_schema=APPROVAL_FORM,
                                                          related_request_id=context.request_id)
            if reply.action == "accept":
                content = reply.content
                outcome = ("approve" if content == {"decision": "仅批准本次", "reviewed": True}
                           and type(content["reviewed"]) is bool else "DECLINED")
            else:
                outcome = "DECLINED" if reply.action == "decline" else "CANCELLED"
        except TimeoutError:
            outcome = "TIMED_OUT"
        except (MCPError, NoBackChannelError, ValidationError, anyio.EndOfStream,
                anyio.ClosedResourceError, anyio.BrokenResourceError):
            outcome = "UNAVAILABLE"
        finally:
            # Cancellation/disconnect never implies approval. Once a valid
            # reply is received, persist issuance without abandoning a worker.
            with anyio.CancelScope(shield=True):
                finished = await anyio.to_thread.run_sync(operations.host_approval, "finish", saved_id,
                                                          receipt["request_id"], outcome)
        return finished

    async def call_tool(context, params):
        # Do not abandon a mutation thread on protocol cancellation: it may
        # already have written recovery evidence. The caller must inspect
        # state after a disconnect instead of inferring rollback or retrying.
        from adapters.mcp.operations import InvalidArguments

        try:
            if params.name == "cleanup_request_approval" and operations.approval_channel == "codex":
                result = await request_approval(context, params.arguments or {})
            else:
                result = await anyio.to_thread.run_sync(operations.call, params.name, params.arguments or {})
        except InvalidArguments as exc:
            result = {"tool": params.name, "ok": False, "exit_code": None,
                      "error": {"code": "MCP_INVALID_ARGUMENTS", "message": str(exc),
                                "may_have_changed": False, "inspect_tools": []}}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=True))],
            structured_content=result, is_error=not result["ok"])

    package_version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    instructions = INSTRUCTIONS
    if operations.approval_channel == "codex":
        instructions = instructions.replace("in the same package's terminal CLI", "through the enabled host channel or the same package's terminal CLI")
        instructions += HOST_APPROVAL_INSTRUCTIONS
    return Server("agent-guard", version=package_version, instructions=instructions,
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
    parser.add_argument("--approval-channel", choices=("terminal", "codex"), default="terminal",
                        help="Operator-selected confirmation channel; codex enables host form requests. Default: terminal")
    parser.add_argument("--audit-exfil", action="store_true",
                        help="Record Exfil check and rewrite decisions in the selected quarantine audit log")
    args = parser.parse_args(argv)
    from adapters.mcp.operations import Operations

    try:
        operations = Operations(args.workspace, trash_root=args.trash_root, session=args.session,
                                approval_channel=args.approval_channel, audit_exfil=args.audit_exfil)
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

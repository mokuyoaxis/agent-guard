"""Fixed-workspace MCP operations implemented by the existing JSON CLIs.

This module has no SDK dependency. It accepts structured arguments, never a
shell command, and leaves policy, admission, audit and recovery to the CLIs.
"""
from __future__ import annotations

from contextlib import nullcontext
import glob
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

from core.cleanup_views import read_options
from core.operation_summary import unavailable_summary
from core import policy, redaction, safe_view

ROOT = Path(__file__).resolve().parents[2]
DELETE_SCRIPTS = ROOT / "skills" / "delete-guard" / "scripts"
EXFIL_SCRIPTS = ROOT / "skills" / "exfil-guard" / "scripts"
EXFIL_PAYLOAD_TOOLS = frozenset({"exfil_check", "exfil_sanitize"})
EXFIL_TOOLS = EXFIL_PAYLOAD_TOOLS | {"config_safe_view"}
GROUP_TOOLS = frozenset(("cleanup_discover", "cleanup_measure"))
TASK_TOOLS = frozenset(("cleanup_execute", "cleanup_task_status"))
APPROVAL_TOOL = "cleanup_request_approval"
TOOLS = (frozenset(("safe_delete", "restore", "transactions", "status", "gc_plan", "cleanup_plan", APPROVAL_TOOL))
         | GROUP_TOOLS | TASK_TOOLS | EXFIL_TOOLS)
MAX_PATHS = 1000
TXID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
PLAN_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
TASK_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
VIEW_FIELDS = frozenset(("view", "category", "path", "state", "offset", "limit"))


class InvalidArguments(ValueError):
    """An unsupported MCP argument, before any CLI is started."""


def _inside(path, root):
    try:
        return os.path.commonpath((os.path.normcase(path), os.path.normcase(root))) == os.path.normcase(root)
    except ValueError:
        return False


def _locations(path):
    absolute = os.path.normpath(os.path.abspath(path))
    parent, leaf = os.path.split(absolute)
    return (absolute, os.path.realpath(absolute), os.path.join(os.path.realpath(parent), leaf))


def resolve_trash_root(workspace, trash_root=None):
    """Resolve trusted startup storage without creating or migrating it."""
    if trash_root is None:
        # Keep the legacy spelling, including existing bucket links/task IDs.
        return os.path.join(workspace, ".agent-trash")
    selected = Path(os.path.expanduser(trash_root))
    if not selected.is_absolute():
        raise ValueError("MCP --trash-root must be an absolute directory path")
    selected = selected.resolve()
    if _inside(workspace, str(selected)):
        raise ValueError("MCP quarantine must not be the workspace or contain it")
    ancestor = selected
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise ValueError("MCP quarantine must name a directory or have a directory parent")
    return str(selected)


class Operations:
    """One server binding; clients cannot supply paths to another workspace.

    ``session=None`` uses the CLI's stable shared default budget, including
    after a server restart. An explicit identity is trusted startup input,
    never a tool argument. Storage defaults to the workspace-local bucket;
    an explicit trusted startup path can select a stable external bucket.
    """

    def __init__(self, workspace, *, trash_root=None, session=None, timeout=120, task_timeout=300,
                 approval_channel="terminal", audit_exfil=False):
        selected = Path(workspace).resolve(strict=True)
        if not selected.is_dir() or selected.parent == selected:
            raise ValueError("MCP workspace must be an existing non-root directory")
        if session is not None and (not isinstance(session, str) or not session or "\0" in session):
            raise ValueError("MCP session must be a nonempty startup identity")
        for label, value in (("CLI", timeout), ("task", task_timeout)):
            if type(value) not in (int, float) or not 0 < value <= 3600:
                raise ValueError("MCP " + label + " timeout must be positive and at most 3600 seconds")
        self.workspace = str(selected)
        self.trash_root = resolve_trash_root(self.workspace, trash_root)
        if approval_channel not in ("terminal", "codex"):
            raise ValueError("MCP approval channel must be terminal or codex")
        self.approval_channel = approval_channel
        self.audit_exfil = audit_exfil
        self.timeout = timeout
        # A task may use two 120-second safe_delete groups plus preflight.
        # Keep the outer dispatcher deadline distinct from a single CLI call.
        self.task_timeout = task_timeout
        self._env = {key: value for key, value in os.environ.items()
                     if not key.startswith(("AGENT_GUARD_", "GIT_"))
                     and key not in ("PYTHONPATH", "PYTHONHOME")}
        self._env.update(AGENT_GUARD_WORKSPACE=self.workspace,
                         AGENT_GUARD_TRASH=self.trash_root,
                         PYTHONDONTWRITEBYTECODE="1")
        if session is not None:
            self._env["AGENT_GUARD_SESSION"] = session
        self._lock = threading.Lock()

    @staticmethod
    def validate(name, arguments):
        """Validate once at the adapter boundary, including unknown fields."""
        if not isinstance(name, str) or name not in TOOLS:
            raise InvalidArguments("unknown MCP tool")
        if not isinstance(arguments, dict):
            raise InvalidArguments("tool arguments must be an object")
        allowed = {"safe_delete": {"paths", "dry_run", "quarantine"}, "restore": {"txid"},
                   "status": {"tail", "view", "limit"}, "transactions": set(), "gc_plan": set(),
                   "cleanup_discover": {"scopes", "max_entries", "max_depth"},
                   "cleanup_measure": {"scopes", "max_entries", "max_depth"},
                   "cleanup_plan": {"targets", "saved_plan_id", "save", "discard_targets", "quarantine",
                                    "discard", "protect", "select", "max_entries", "max_depth",
                                    "view", "category", "path", "offset", "limit"},
                   APPROVAL_TOOL: {"saved_plan_id"},
                   "cleanup_execute": {"task_id", "view"},
                   "cleanup_task_status": {"task_id", "view", "state", "path", "offset", "limit"},
                   "config_safe_view": {"path", "format"}, "exfil_check": {"text", "channel", "path"},
                   "exfil_sanitize": {"text", "channel"}}[name]
        if set(arguments) - allowed:
            raise InvalidArguments("unsupported tool argument")
        result = dict(arguments)
        if name in EXFIL_TOOLS:
            if name == "config_safe_view" or "path" in result:
                path = result.get("path")
                try:
                    if not isinstance(path, str):
                        raise safe_view.SafeViewError("INVALID_PATH")
                    safe_view._components(path)
                except safe_view.SafeViewError as exc:
                    raise InvalidArguments(exc.message) from None
            if name == "config_safe_view":
                result.setdefault("format", "auto")
                if result["format"] not in safe_view.FORMATS:
                    raise InvalidArguments("unsupported config format")
            else:
                channel = result.get("channel")
                if not isinstance(channel, str) or channel not in redaction.CHANNELS:
                    raise InvalidArguments("channel must be a supported egress channel")
                if not isinstance(result.get("text"), str):
                    raise InvalidArguments("text must be a string")
            return result
        if name == "cleanup_plan" or name in TASK_TOOLS:
            if any(result[key] is None for key in VIEW_FIELDS if key in result):
                raise InvalidArguments("omit unused read controls; null is not a declared view/filter/page value")
            try:
                settings = read_options("plan" if name == "cleanup_plan" else
                                        "execution" if name == "cleanup_execute" else "task",
                                        **{key: result[key] for key in VIEW_FIELDS if key in result})
            except ValueError as exc:
                raise InvalidArguments(str(exc)) from exc
            result["view"] = settings["view"]
            if settings["view"] == "details":
                result.update({key: value for key, value in settings.items() if value is not None})
        if name == APPROVAL_TOOL:
            identifier = result.get("saved_plan_id")
            if not isinstance(identifier, str) or not PLAN_ID_RE.fullmatch(identifier):
                raise InvalidArguments("saved_plan_id must be a 64-character lowercase hexadecimal saved plan ID")
        elif name == "safe_delete":
            paths = result.get("paths")
            if (not isinstance(paths, list) or not 1 <= len(paths) <= MAX_PATHS
                    or any(not isinstance(path, str) or not path or "\0" in path for path in paths)):
                raise InvalidArguments("paths must contain 1 through 1000 nonempty literal paths")
            if any(os.path.normpath(path)[-1].isspace() for path in paths):
                # The existing path classifier strips terminal whitespace.
                # Refuse rather than let a literal tool select a neighbor.
                raise InvalidArguments("literal paths cannot end in whitespace")
            if type(result.get("dry_run", False)) is not bool:
                raise InvalidArguments("dry_run must be a boolean")
            result["dry_run"] = result.get("dry_run", False)
            if type(result.get("quarantine", False)) is not bool:
                raise InvalidArguments("quarantine must be a boolean")
            result["quarantine"] = result.get("quarantine", False)
        elif name == "restore":
            txid = result.get("txid")
            # Match the recovery engine's transaction-ID grammar at this
            # untrusted tool boundary; no filesystem path or CLI option.
            if not isinstance(txid, str) or not TXID_RE.fullmatch(txid):
                raise InvalidArguments("txid must use the recovery transaction-ID format")
        elif name == "status":
            if type(result.get("tail", 10)) is not int or not 0 <= result.get("tail", 10) <= 100:
                raise InvalidArguments("tail must be an integer between 0 and 100")
            result["tail"] = result.get("tail", 10)
            view = result.get("view", "full")
            if view not in ("overview", "full"):
                raise InvalidArguments("status view must be overview or full")
            result["view"] = view
            if view == "overview":
                limit = result.get("limit", 10)
                if type(limit) is not int or not 1 <= limit <= 50:
                    raise InvalidArguments("status overview limit must be an integer between 1 and 50")
                result["limit"] = limit
            elif "limit" in result:
                raise InvalidArguments("limit applies only to the status overview")
        elif name in TASK_TOOLS:
            task_id = result.get("task_id")
            if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
                raise InvalidArguments("task_id must be a 32-character lowercase hexadecimal cleanup task ID")
        elif name == "cleanup_plan":
            if "saved_plan_id" in result:
                if ((set(result) - VIEW_FIELDS) != {"saved_plan_id"} or not isinstance(result["saved_plan_id"], str)
                        or not PLAN_ID_RE.fullmatch(result["saved_plan_id"])):
                    raise InvalidArguments("saved_plan_id permits read controls only; targets, declarations and selection cannot change")
                return result
            for field in ("save", "discard_targets", "quarantine"):
                if type(result.get(field, False)) is not bool:
                    raise InvalidArguments(field + " must be a boolean")
                result[field] = result.get(field, False)
            for field in ("targets", "discard", "protect", "select"):
                if field == "select" and field not in result:
                    continue
                values = result.get(field, [])
                minimum = 1 if field in ("targets", "select") else 0
                if (not isinstance(values, list) or not minimum <= len(values) <= MAX_PATHS
                        or any(not isinstance(p, str) or not p or "\0" in p for p in values)):
                    raise InvalidArguments(field + " must contain " + str(minimum) + " through 1000 nonempty literal paths")
                if any(os.path.normpath(p)[-1].isspace() for p in values):
                    raise InvalidArguments("literal paths cannot end in whitespace")
                result[field] = values
            if "select" in result and not result["save"]:
                raise InvalidArguments("select requires save=true; it proposes a selection, not approval")
            for field, maximum in (("max_entries", 10000), ("max_depth", 32)):
                value = result.get(field, maximum)
                if type(value) is not int or not 1 <= value <= maximum:
                    raise InvalidArguments(field + " must be an integer between 1 and " + str(maximum))
                result[field] = value
        elif name in GROUP_TOOLS:
            scopes = result.get("scopes", [])
            if (not isinstance(scopes, list) or len(scopes) > 64
                    or (name == "cleanup_measure" and not scopes)
                    or any(not isinstance(path, str) or not path or "\0" in path for path in scopes)):
                raise InvalidArguments("scopes must be at most 64 nonempty literal directory paths; measure requires scopes")
            result["scopes"] = scopes
            for field, maximum in (("max_entries", 10000 if name == "cleanup_discover" else 20000),
                                   ("max_depth", 4 if name == "cleanup_discover" else 32)):
                value = result.get(field, maximum)
                if type(value) is not int or not 1 <= value <= maximum:
                    raise InvalidArguments(field + " must be an integer between 1 and " + str(maximum))
                result[field] = value
        return result

    def _quarantine_overlap(self, paths):
        # Selecting new storage never makes the retained default bucket a
        # normal deletion target. Include physical/lexical aliases of both.
        buckets = {self.trash_root, os.path.join(self.workspace, ".agent-trash")}
        boundaries = [location for bucket in buckets for location in _locations(bucket)]
        return any(_inside(target, boundary) or _inside(boundary, target)
                   for path in paths for target in _locations(path) for boundary in boundaries)

    @staticmethod
    def _failure(name, code, message, *, exit_code=None, may_have_changed=False, task_id=None):
        result = {"tool": name, "ok": False, "exit_code": exit_code,
                  "error": {"code": code, "message": message,
                            "may_have_changed": may_have_changed,
                            "inspect_tools": (["status", "transactions"]
                                              if may_have_changed and name != "cleanup_plan" else [])}}
        if task_id is not None:
            result["task_id"] = task_id
            result["error"]["inspect_tools"] = ["cleanup_task_status", "transactions", "status"]
            result["query"] = {"name": "cleanup_task_status", "arguments": {"task_id": task_id}}
        if (may_have_changed and name in ("safe_delete", "restore", "cleanup_execute")) or name == "cleanup_task_status":
            result["execution_summary"] = unavailable_summary("cleanup" if name in TASK_TOOLS else name)
        return result

    def call(self, name, arguments):
        args = self.validate(name, arguments)
        if name == APPROVAL_TOOL:
            raise InvalidArguments("host approval requires the MCP request context, not a direct operation call")
        mutating = (name in ("restore", "cleanup_execute")
                    or (name == "safe_delete" and not args["dry_run"])
                    or (name == "cleanup_plan" and args.get("save", False))
                    or (name in EXFIL_PAYLOAD_TOOLS and self.audit_exfil))
        task_id = args["task_id"] if name in TASK_TOOLS else None
        scripts = DELETE_SCRIPTS
        stdin = {"stdin": subprocess.DEVNULL}
        if name == "config_safe_view":
            scripts = EXFIL_SCRIPTS
            command = ["view.py", "--format", args["format"], "--", args["path"]]
        elif name in EXFIL_PAYLOAD_TOOLS:
            scripts = EXFIL_SCRIPTS
            command = ["check_span.py" if name == "exfil_check" else "sanitize.py",
                       "--json", "--channel", args["channel"]]
            if "path" in args:
                command.append("--path=" + args["path"])
            if self.audit_exfil:
                command.append("--audit")
            payload = args["text"]
            cap = redaction.DEFAULT_MAX_SCAN_BYTES
            try:
                if len(payload) > cap or len(payload.encode("utf-8")) > cap:
                    # Feed the CLI one byte over its cap to retain the block and audit path.
                    payload = "x" * (cap + 1)
            except UnicodeEncodeError:
                raise InvalidArguments("text must be valid UTF-8") from None
            stdin = {"input": payload}
        elif name == "safe_delete":
            paths = [os.path.normpath(os.path.join(self.workspace, path)) for path in args["paths"]]
            if self._quarantine_overlap(paths):
                return self._failure(name, "MCP_QUARANTINE_PROTECTED",
                                     "MCP safe_delete cannot remove quarantine contents or their ancestors; GC is preview-only")
            command = ["safe_delete.py", "--json"]
            if args["dry_run"]:
                command.append("--dry-run")
            if args["quarantine"]:
                command.append("--quarantine")
            # The CLI expands globs. Escape each absolute target so a literal
            # filename such as '[old]*.txt' cannot select adjacent files.
            command += ["--", *(glob.escape(path) for path in paths)]
        elif name == "restore":
            command = ["restore.py", "restore", "--json", "--", args["txid"]]
        elif name == "transactions":
            command = ["restore.py", "list", "--json"]
        elif name == "status":
            command = ["status.py", "--json", "--tail", str(args["tail"])]
            if args["view"] == "overview":
                command += ["--view", "overview", "--limit", str(args["limit"])]
        elif name == "gc_plan":
            command = ["gc.py", "--json"]
        elif name == "cleanup_plan":
            command = ["cleanup.py", "--json", "--root", self.workspace]
            if "saved_plan_id" in args:
                command += ["--from-plan", args["saved_plan_id"]]
            else:
                command += ["--max-entries", str(args["max_entries"]), "--max-depth", str(args["max_depth"])]
                for field, flag in (("targets", "--target"), ("discard", "--discard"),
                                    ("protect", "--protect"), ("select", "--select")):
                    command += [flag + "=" + path for path in args.get(field, [])]
                if args["discard_targets"]:
                    command.append("--discard-targets")
                if args["save"]:
                    command.append("--save-plan")
                if args["quarantine"]:
                    command.append("--quarantine")
        elif name in TASK_TOOLS:
            command = ["cleanup.py", "--json", "--root", self.workspace, "--task", task_id]
            if name == "cleanup_execute":
                command.append("--execute")
        else:
            command = ["cleanup.py", "--json", "--discover" if name == "cleanup_discover" else "--measure",
                       "--max-entries", str(args["max_entries"]), "--max-depth", str(args["max_depth"])]
            # Equals syntax keeps even an option-looking literal inside its
            # value, without changing the server's fixed workspace binding.
            command += ["--scope=" + scope for scope in args["scopes"]]

        if name == "cleanup_plan" or name in TASK_TOOLS:
            command += ["--" + key + "=" + str(args[key])
                        for key in ("view", "category", "path", "state", "offset", "limit")
                        if key in args]

        # Keep existing calls serialized. Task status reads atomically
        # replaced receipts and must not wait behind its own long execution.
        # No call changes process cwd/environment; writers share Core admission.
        with (nullcontext() if name in EXFIL_TOOLS or name == "cleanup_task_status" or
              (name == "status" and args["view"] == "overview") else self._lock):
            try:
                child = subprocess.run([sys.executable, "-B", str(scripts / command[0]), *command[1:]],
                                       cwd=self.workspace, env=self._env, **stdin,
                                       capture_output=True, text=True, encoding="utf-8", errors="strict",
                                       timeout=self.task_timeout if name == "cleanup_execute" else self.timeout,
                                       shell=False)
            except subprocess.TimeoutExpired:
                return self._failure(name, "MCP_CLI_TIMEOUT",
                                     "CLI timed out; do not retry automatically. Inspect the relevant task, proposal or recovery state",
                                     may_have_changed=mutating, task_id=task_id)
            except OSError as exc:
                return self._failure(name, "MCP_CLI_UNAVAILABLE",
                                     "CLI could not start (OS error " + str(exc.errno) + ")", task_id=task_id)
            except UnicodeError:
                return self._failure(name, "MCP_CLI_PROTOCOL_ERROR",
                                     "CLI output is not valid UTF-8; inspect recovery state before retrying",
                                     may_have_changed=mutating, task_id=task_id)
        try:
            use_stderr = name == "config_safe_view" and child.returncode == 2
            result = json.loads(child.stderr if use_stderr else child.stdout)
        except (ValueError, RecursionError):
            return self._failure(name, "MCP_CLI_PROTOCOL_ERROR",
                                 "CLI did not return a JSON object; inspect recovery state before retrying",
                                 exit_code=child.returncode, may_have_changed=mutating, task_id=task_id)
        if not isinstance(result, dict):
            return self._failure(name, "MCP_CLI_PROTOCOL_ERROR",
                                 "CLI did not return a JSON object; inspect recovery state before retrying",
                                 exit_code=child.returncode, may_have_changed=mutating, task_id=task_id)
        if name in EXFIL_TOOLS:
            return self._exfil_response(name, child.returncode, result, mutating)
        if name == "status" and args["view"] == "overview" and child.returncode in (0, 1):
            if (result.get("schema") != "workspace_query/v1" or result.get("workspace") != self.workspace
                    or result.get("trash_root") != self.trash_root or type(result.get("complete")) is not bool):
                return self._failure(name, "MCP_CLI_PROTOCOL_ERROR", "Unexpected workspace query response",
                                     exit_code=child.returncode)
            return {"tool": name, "ok": True, "partial": not result["complete"],
                    "exit_code": child.returncode, "result": result}
        if name == "cleanup_plan" and child.returncode in (0, 1):
            stored = args.get("save", False) or "saved_plan_id" in args
            compact = args["view"] != "full"
            plan = result if compact else result.get("plan") if stored else result
            if (not isinstance(plan, dict) or plan.get("scope") != "targets"
                    or plan.get("workspace") != self.workspace or type(plan.get("complete")) is not bool
                    or (compact and (result.get("schema") != "cleanup_view/v1" or result.get("kind") != "plan"))
                    or (stored and not compact and result.get("schema") != "cleanup_saved_plan/v1")):
                return self._failure(name, "MCP_CLI_PROTOCOL_ERROR", "Unexpected cleanup plan response",
                                     exit_code=child.returncode, may_have_changed=mutating)
            if stored and self.approval_channel == "codex":
                approval = self.host_approval("review" if args.get("save") else "inspect",
                                              result["saved_plan_id"])
                result["host_approval"] = approval["result"]
                result["next_step"] = {"request": {"name": APPROVAL_TOOL,
                                                    "arguments": {"saved_plan_id": result["saved_plan_id"]}},
                                       "operator_cli": result.get("next_step"),
                                       "notice": "Review the complete exported list, then request host confirmation. "
                                                 "An existing receipt is reused without a new prompt; inspect it before further work."}
            return {"tool": name, "ok": True, "partial": not plan["complete"],
                    "exit_code": child.returncode, "result": result,
                    "binding": {"workspace": self.workspace, "trash_root": self.trash_root,
                                "session_mode": ("EXPLICIT_STARTUP_SESSION" if "AGENT_GUARD_SESSION" in self._env
                                                 else "SHARED_CLI_DEFAULT"),
                                "notice": "Operator approval must use this same package, workspace and quarantine. Follow the returned argv to retain custom storage. For SHARED_CLI_DEFAULT unset AGENT_GUARD_SESSION; otherwise use the identical startup session from trusted configuration. Raw session identity is not returned. Saving is not approval."}}
        if name in GROUP_TOOLS and child.returncode in (0, 1):
            expected = "cleanup_group_discovery/v1" if name == "cleanup_discover" else "cleanup_group_measurement/v1"
            if result.get("schema") != expected:
                return self._failure(name, "MCP_CLI_PROTOCOL_ERROR", "Unexpected cleanup observation schema",
                                     exit_code=child.returncode)
            partial = (not result["coverage"]["frontier_complete"] if name == "cleanup_discover"
                       else any(not row["size_complete"] for row in result["scopes"]))
            return {"tool": name, "ok": True, "partial": partial, "exit_code": child.returncode, "result": result}
        # Preserve all existing CLI evidence, including txids/moved paths on
        # partial failures. A nonzero exit never means 'nothing changed'.
        response = {"tool": name, "ok": child.returncode == 0,
                    "exit_code": child.returncode, "result": result}
        if task_id is not None:
            response["task_id"] = task_id
            if name == "cleanup_execute" and result.get("status") != "COMPLETED":
                response["query"] = {"name": "cleanup_task_status", "arguments": {"task_id": task_id}}
        return response

    def _exfil_response(self, name, code, result, mutating):
        valid = False
        if name == "config_safe_view":
            if code == 0:
                valid = (result.get("status") == "ok" and result.get("format") in safe_view.FORMATS[1:]
                         and isinstance(result.get("view"), dict))
            elif code == 2:
                kind, message = result.get("code"), result.get("message")
                if isinstance(kind, str) and isinstance(message, str) and result.get("status") == "error":
                    valid = (message in safe_view.INVALID_FORMAT_MESSAGES if kind == "INVALID_FORMAT"
                             else safe_view.ERROR_MESSAGES.get(kind) == message)
                if valid:
                    result = {"status": "error", "code": kind, "message": message}
        else:
            expected = {0: {"ALLOW", "SANITIZE"}, 2: {"BLOCK"}, 3: {"ASK"}}
            decision = result.get("decision")
            valid = (isinstance(decision, str) and decision in expected.get(code, ())
                     and result.get("workspace") == self.workspace and result.get("exit") == code
                     and isinstance(result.get("code"), str) and result["code"] in policy.EXPLANATIONS)
            if valid and name == "exfil_sanitize":
                valid = (isinstance(result.get("text"), str) if code == 0 else
                         result.get("text") == "" and result.get("redaction_plan") == [])
        if not valid:
            return self._failure(name, "MCP_CLI_PROTOCOL_ERROR", "Unexpected Exfil-guard response",
                                 exit_code=code, may_have_changed=mutating)
        response = {"tool": name, "ok": code == 0, "exit_code": code, "result": result}
        if name == "exfil_check" and result["decision"] == "SANITIZE":
            response["notice"] = "Redaction is required. Use exfil_sanitize before sending the text."
        return response

    def host_approval(self, action, saved_id, *internal_values):
        """Internal fixed-environment worker, never model-supplied outcomes."""
        name = APPROVAL_TOOL
        if self.approval_channel != "codex":
            return self._failure(name, "MCP_APPROVAL_DISABLED", "Use operator terminal approval for this service")
        command = [sys.executable, "-B", str(ROOT / "adapters/mcp/approval.py"),
                   action, saved_id, *internal_values]
        with self._lock:
            try:
                child = subprocess.run(command, cwd=self.workspace, env=self._env,
                                       stdin=subprocess.DEVNULL, capture_output=True,
                                       text=True, encoding="utf-8", errors="strict", timeout=self.timeout)
            except (subprocess.TimeoutExpired, OSError, UnicodeError):
                result = {"error": {"code": "MCP_APPROVAL_WORKER_UNAVAILABLE",
                                    "message": "Confirmation worker did not return a usable receipt; query the saved plan before any new request"}}
            else:
                try:
                    result = json.loads(child.stdout)
                    if not isinstance(result, dict):
                        raise ValueError("expected a receipt")
                except (ValueError, RecursionError):
                    result = {"error": {"code": "MCP_APPROVAL_PROTOCOL_ERROR",
                                        "message": "Confirmation worker returned no usable receipt; query the saved plan"}}
                if child.returncode and "error" not in result:
                    result = {"error": {"code": "MCP_APPROVAL_WORKER_FAILED", "message": "Confirmation worker failed; query the saved plan"}}
        response = {"tool": name, "ok": "error" not in result, "exit_code": None, "result": result,
                    "saved_plan_id": saved_id,
                    "query": {"name": "cleanup_plan", "arguments": {"saved_plan_id": saved_id}}}
        if "task_id" in result:
            response["task_id"] = result["task_id"]
        return response

"""Fixed-workspace MCP operations implemented by the existing JSON CLIs.

This module has no SDK dependency. It accepts structured arguments, never a
shell command, and leaves policy, admission, audit and recovery to the CLIs.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "delete-guard" / "scripts"
TOOLS = frozenset(("safe_delete", "restore", "transactions", "status", "gc_plan"))
MAX_PATHS = 1000
TXID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


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


class Operations:
    """One server binding; clients cannot supply paths to another workspace.

    ``session=None`` uses the CLI's stable shared default budget, including
    after a server restart. An explicit identity is trusted startup input,
    never a tool argument. The selected quarantine is workspace-local.
    """

    def __init__(self, workspace, *, session=None, timeout=120):
        selected = Path(workspace).resolve(strict=True)
        if not selected.is_dir() or selected.parent == selected:
            raise ValueError("MCP workspace must be an existing non-root directory")
        if session is not None and (not isinstance(session, str) or not session or "\0" in session):
            raise ValueError("MCP session must be a nonempty startup identity")
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise ValueError("MCP CLI timeout must be positive and at most 3600 seconds")
        self.workspace = str(selected)
        self.trash_root = os.path.join(self.workspace, ".agent-trash")
        self.timeout = timeout
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
        allowed = {"safe_delete": {"paths", "dry_run"}, "restore": {"txid"},
                   "status": {"tail"}, "transactions": set(), "gc_plan": set()}[name]
        if set(arguments) - allowed:
            raise InvalidArguments("unsupported tool argument")
        result = dict(arguments)
        if name == "safe_delete":
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
        return result

    def _quarantine_overlap(self, paths):
        boundaries = _locations(self.trash_root)
        return any(_inside(target, boundary) or _inside(boundary, target)
                   for path in paths for target in _locations(path) for boundary in boundaries)

    @staticmethod
    def _failure(name, code, message, *, exit_code=None, may_have_changed=False):
        return {"tool": name, "ok": False, "exit_code": exit_code,
                "error": {"code": code, "message": message,
                          "may_have_changed": may_have_changed,
                          "inspect_tools": ["status", "transactions"] if may_have_changed else []}}

    def call(self, name, arguments):
        args = self.validate(name, arguments)
        mutating = name == "restore" or (name == "safe_delete" and not args["dry_run"])
        if name == "safe_delete":
            paths = [os.path.normpath(os.path.join(self.workspace, path)) for path in args["paths"]]
            if self._quarantine_overlap(paths):
                return self._failure(name, "MCP_QUARANTINE_PROTECTED",
                                     "MCP safe_delete cannot remove quarantine contents or their ancestors; GC is preview-only")
            command = ["safe_delete.py", "--json"]
            if args["dry_run"]:
                command.append("--dry-run")
            # The CLI expands globs. Escape each absolute target so a literal
            # filename such as '[old]*.txt' cannot select adjacent files.
            command += ["--", *(glob.escape(path) for path in paths)]
        elif name == "restore":
            command = ["restore.py", "restore", "--json", "--", args["txid"]]
        elif name == "transactions":
            command = ["restore.py", "list", "--json"]
        elif name == "status":
            command = ["status.py", "--json", "--tail", str(args["tail"])]
        else:
            command = ["gc.py", "--json"]

        # Serialize calls in this server, without changing process cwd or
        # environment. Other participating processes share Core admission.
        with self._lock:
            try:
                child = subprocess.run([sys.executable, "-B", str(SCRIPTS / command[0]), *command[1:]],
                                       cwd=self.workspace, env=self._env, stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True, encoding="utf-8", errors="strict",
                                       timeout=self.timeout, shell=False)
            except subprocess.TimeoutExpired:
                return self._failure(name, "MCP_CLI_TIMEOUT",
                                     "CLI timed out; do not retry automatically. Inspect status and recovery transactions",
                                     may_have_changed=mutating)
            except OSError as exc:
                return self._failure(name, "MCP_CLI_UNAVAILABLE",
                                     "CLI could not start (OS error " + str(exc.errno) + ")")
            except UnicodeError:
                return self._failure(name, "MCP_CLI_PROTOCOL_ERROR",
                                     "CLI output is not valid UTF-8; inspect recovery state before retrying",
                                     may_have_changed=mutating)
        try:
            result = json.loads(child.stdout)
        except (ValueError, RecursionError):
            return self._failure(name, "MCP_CLI_PROTOCOL_ERROR",
                                 "CLI did not return a JSON object; inspect recovery state before retrying",
                                 exit_code=child.returncode, may_have_changed=mutating)
        if not isinstance(result, dict):
            return self._failure(name, "MCP_CLI_PROTOCOL_ERROR",
                                 "CLI did not return a JSON object; inspect recovery state before retrying",
                                 exit_code=child.returncode, may_have_changed=mutating)
        # Preserve all existing CLI evidence, including txids/moved paths on
        # partial failures. A nonzero exit never means 'nothing changed'.
        return {"tool": name, "ok": child.returncode == 0,
                "exit_code": child.returncode, "result": result}

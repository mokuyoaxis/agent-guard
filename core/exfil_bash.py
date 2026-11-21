"""Opt-in credential checks for a Bash command before the host runs it."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import sys
import time

from . import classifier, exfil_audit, policy, redaction


CHANNEL = "process-argv"
MAX_SPANS = 128
_VIEW_SCRIPT = Path(__file__).resolve().parents[1] / "skills/exfil-guard/scripts/view.py"
_SHELL_SYNTAX = re.compile(r"[;&|$()<>\x60#\r\n]")


def _standalone_view(command, workdir):
    if "view" not in command or _SHELL_SYNTAX.search(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words:
        return False
    if words[0] == "agent-guard-config-view":
        return True
    if words[0] in {"python", "python3", sys.executable}:
        words = words[1:]
        if words and words[0] == "-B":
            words = words[1:]
    if not words or Path(words[0]).name != "view.py":
        return False
    try:
        return (Path(workdir) / words[0]).resolve(strict=True) == _VIEW_SCRIPT
    except (OSError, RuntimeError, ValueError):
        return False


def assess(command, workdir, *, audit=False):
    """Return only static decisions, rule labels and bounded span metadata."""
    started = time.monotonic()
    spans, size, exempted = [], None, 0
    code = policy.CODE_BLOCK_OUTPUT_UNSCANNABLE
    workspace = None
    try:
        workspace = classifier.discover_workspace(workdir)
        cap = min(redaction.max_scan_bytes(), redaction.DEFAULT_MAX_SCAN_BYTES)
        if command is not None and len(command) <= cap:
            size = len(command.encode("utf-8"))
            if size <= cap:
                spans = redaction.detect_secrets(command, CHANNEL)
                if spans:
                    matches = redaction.apply_exemption(spans, redaction.load_exemption(workspace))
                    exempted = len(spans) - len(matches)
                    spans = matches
                source = redaction.secret_source_span(
                    command, CHANNEL, bash=True, include_store_paths=not _standalone_view(command, workdir))
                if source is not None:
                    spans.append(source)
                    code = policy.CODE_BLOCK_SECRET_SOURCE_DUMP
                elif spans:
                    code = policy.CODE_BLOCK_SECRET_EMISSION
                else:
                    code = policy.CODE_ALLOW_NOOP
    except (OSError, RuntimeError, ValueError):
        spans, code = [], policy.CODE_BLOCK_OUTPUT_UNSCANNABLE
    verdicts = [policy.Verdict("BLOCK", policy.CODE_BLOCK_SECRET_SOURCE_DUMP if span.source_dump
                              else policy.CODE_BLOCK_SECRET_EMISSION) for span in spans[:MAX_SPANS]]
    allowed = code == policy.CODE_ALLOW_NOOP
    result = {"decision": "ALLOW" if allowed else "BLOCK", "code": code, "channel": CHANNEL,
              "rule_ids": sorted({span.rule_id for span in spans}), "span_count": len(spans),
              "spans": redaction.verdict_rows(spans[:MAX_SPANS], verdicts), "size_bytes": size,
              "exempted": exempted, "exit": 0 if allowed else 2}
    active = exfil_audit.enabled(audit)
    if active and (workspace is None or not os.path.isdir(workspace)):
        result["audit"] = {"enabled": True, "recorded": False, "error": "AUDIT_WRITE_FAILED"}
    else:
        result["audit"] = exfil_audit.record(workspace, "exfil_bash_hook", "advisory", result,
                                          started=started, active=active)
    return result


def refusal_message(result):
    rules = ", ".join(result["rule_ids"])
    findings = " (" + rules + ")" if rules else ""
    return (f"[agent-guard] Bash Exfil refused [{result['code']}]{findings}. "
            "Keep credential values out of shell arguments. Use agent-guard-config-view for config structure.")

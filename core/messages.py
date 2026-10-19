"""User-facing storage notices; no policy or execution decisions."""
from __future__ import annotations

from pathlib import Path
import shlex
import sys


QUARANTINE_NOTICE = (
    "Files moved to quarantine still use disk space. "
    "Purging permanently removes their recovery copies."
)

HOST_STATUS_NOTICE = (
    "This reports local recovery data, not whether a host loaded the guard. "
    "Check the host's plugin or hook status separately."
)


def gc_guidance():
    script = Path(__file__).resolve().parent.parent / "skills/delete-guard/scripts/gc.py"
    command = [sys.executable, str(script)]
    return {
        "plan_command": shlex.join(command),
        "purge_eligible_command": shlex.join(
            command + ["--execute", "--all-eligible"]),
        "purge_transaction_command": shlex.join(
            command + ["--execute", "--txid", "<txid>"]),
        "note": "POSIX-shell commands. Review the plan first. Run from the same working directory "
                "with the same AGENT_GUARD_WORKSPACE / AGENT_GUARD_TRASH settings. "
                "Replace <txid> to select one transaction.",
    }


def print_gc_guidance(guidance):
    print(f"GC plan   : {guidance['plan_command']}")
    print(f"GC purge  : {guidance['purge_eligible_command']}")
    print("            permanently removes eligible recovery copies")
    print(f"GC one    : {guidance['purge_transaction_command']}")
    print(guidance["note"])

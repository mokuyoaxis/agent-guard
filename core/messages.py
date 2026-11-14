"""Recovery and admission guidance; no policy or execution decisions."""
from __future__ import annotations

import glob
import os
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

ADMISSION_PREVIEW_NOTICE = (
    "Admission snapshot only: no budget debit, approval consumption or reservation. "
    "No admission lock or payload-storage write preflight was performed; execution rechecks. "
    "Existing dry-run auditing may still write metadata."
)


def quarantine_command(argv, workspace, trash_root):
    """Keep custom storage on copied POSIX CLI instructions, never grants."""
    workspace = os.path.realpath(workspace)
    trash_root = os.path.abspath(trash_root)
    if trash_root == os.path.join(workspace, ".agent-trash"):
        return argv
    return ["env", "AGENT_GUARD_WORKSPACE=" + workspace,
            "AGENT_GUARD_TRASH=" + trash_root, *argv]


def deletion_guidance(preview, workspace, trash_root, cwd, paths, *, quarantine=False):
    """Existing supported next steps; no automatic approval, reset or purge."""
    scripts = Path(__file__).resolve().parent.parent / "skills/delete-guard/scripts"

    def command(label, name, arguments):
        argv = quarantine_command([sys.executable, str(scripts / name), *arguments], workspace, trash_root)
        return {"label": label, "argv": argv, "command": shlex.join(argv)}

    code = preview["code"]
    commands = []
    deletion_options = ["--quarantine"] if quarantine else []
    if preview["status"] == "READY":
        action = "execute_request"
        message = "The current checks pass. Execute only within the user's authorized scope; execution rechecks."
        commands.append(command("Execute the observed literal targets", "safe_delete.py",
                                ["--json", *deletion_options, "--", *(glob.escape(path) for path in paths)]))
    elif code == "BLOCK_DELETE_BATCH_CONFIRM":
        action = "review_cleanup_plan"
        message = ("Review and save a plan for these exact targets. Add --discard-targets only if every "
                   "requested target is confirmed discarded; otherwise use individual --discard declarations. "
                   "Inspect the saved selection, then use --from-plan ID --approve in an operator terminal. "
                   "The agent can execute or query the returned --task ID without another terminal confirmation. "
                   "Saving does not approve execution; never answer confirmation on the user's behalf.")
        commands.append(command("Propose and save exact targets; no deletion", "cleanup.py",
                                ["--root", workspace, "--json", "--save-plan", *deletion_options,
                                 *("--target=" + path for path in paths)]))
    elif code == "ALLOW_NOOP":
        action = "none"
        message = "No matching existing targets require deletion."
    elif code == "ALLOW_TRASH_GC":
        action = "review_gc_plan"
        message = "Quarantine housekeeping is outside shared deletion admission. Review recovery copies before permanent removal."
        commands.append(command("Preview GC only", "gc.py", ["--json"]))
    else:
        action = "inspect_status"
        if code == "BLOCK_DELETE_RATE":
            message = "Inspect status and wait for the rate window to expire; do not split requests or change budget identity."
        elif code == "BLOCK_DELETE_REPEAT":
            message = "Inspect the delete/recreate loop and status before another attempt; do not disguise the request."
        elif code == "BLOCK_DELETE_LIMIT":
            message = ("Inspect session and storage limits. Batch approval does not reset budgets or free space. "
                       "If quarantine capacity is the limit, review GC separately; no automatic reset or purge.")
        else:
            message = "Stop and inspect the reported policy, source or state problem. Preserve targets and control files."
        commands.append(command("Inspect status", "status.py", ["--json", "--tail", "0"]))
    return {"action": action, "message": message, "commands": commands,
            "cwd": cwd, "workspace": workspace, "trash_root": trash_root,
            "binding_notice": "Keep the same workspace, quarantine and session settings. Command strings use POSIX quoting; argv is structured."}


def print_admission_preview(preview):
    suffix = " (" + preview["code"] + ")" if preview["code"] else ""
    print("admission : " + preview["status"] + suffix)
    print(preview["reason"])
    if preview["requested"] is not None:
        requested = preview["requested"]
        print(f"request   : {requested['bytes']}B / {requested['entries']} entries")
    approval = preview["approval"]
    requirement = "unknown" if approval["required"] is None else "yes" if approval["required"] else "no"
    print("approval  : " + approval["status"] + "; batch threshold exceeded: " + requirement)
    print(preview["notice"])
    following = preview["next_step"]
    print("next      : " + following["message"])
    for item in following["commands"]:
        print(item["label"] + ": " + item["command"])
    print(following["binding_notice"])


def gc_guidance(workspace=None, trash_root=None):
    script = Path(__file__).resolve().parent.parent / "skills/delete-guard/scripts/gc.py"
    command = [sys.executable, str(script)]
    if workspace is not None and trash_root is not None:
        command = quarantine_command(command, workspace, trash_root)
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

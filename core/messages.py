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


# PowerShell treats ASCII apostrophe and these four typographic single quotes
# as single-quoted string delimiters (about_Quoting_Rules).
_PS_SINGLE_QUOTES = ("'", "\u2018", "\u2019", "\u201a", "\u201b")


def _ps_quote(value: str) -> str:
    """Single-quote one PowerShell literal, escaping ASCII and typographic single quotes."""
    text = str(value)
    for ch in _PS_SINGLE_QUOTES:
        if ch in text:
            text = text.replace(ch, ch + ch)
    return "'" + text + "'"


def _storage_env(workspace, trash_root):
    if workspace is None or trash_root is None:
        return {}
    workspace = os.path.realpath(workspace)
    trash_root = os.path.abspath(trash_root)
    if trash_root == os.path.join(workspace, ".agent-trash"):
        return {}
    return {
        "AGENT_GUARD_WORKSPACE": workspace,
        "AGENT_GUARD_TRASH": trash_root,
    }


def format_command(argv, env_vars=None, *, platform=None):
    """Render a copy-pasteable shell command for POSIX or Windows PowerShell.

    On Windows PowerShell, custom environment bindings are scoped to the single
    invocation inside a child scriptblock with try/finally restoration so later
    commands in the operator's terminal never inherit stale workspace/trash vars.
    """
    target_os = os.name if platform is None else platform
    items = list(env_vars.items()) if env_vars else []
    if target_os == "nt":
        rendered = "& " + " ".join(_ps_quote(part) for part in argv)
        if items:
            keys_list = ",".join(_ps_quote(key) for key, _ in items)
            set_stmts = "; ".join(
                f"[Environment]::SetEnvironmentVariable({_ps_quote(key)},{_ps_quote(value)},'Process')"
                for key, value in items
            )
            return (
                "& { $p=@{}; foreach($k in " + keys_list + "){ "
                "$p[$k]=[Environment]::GetEnvironmentVariable($k,'Process') }; "
                "try { " + set_stmts + "; " + rendered + " } "
                "finally { foreach($k in $p.Keys){ "
                "[Environment]::SetEnvironmentVariable($k,$p[$k],'Process') } } }"
            )
        return rendered
    if items:
        return shlex.join(["env", *(f"{key}={value}" for key, value in items), *argv])
    return shlex.join(argv)


def quarantine_command(argv, workspace, trash_root, *, platform=None):
    """Keep custom storage on copied CLI instructions, never grants."""
    env_vars = _storage_env(workspace, trash_root)
    if not env_vars:
        return list(argv)
    if (os.name if platform is None else platform) == "nt":
        return list(argv)
    return ["env", *(f"{key}={value}" for key, value in env_vars.items()), *argv]


def deletion_guidance(preview, workspace, trash_root, cwd, paths, *,
                      quarantine=False, platform=None):
    """Existing supported next steps; no automatic approval, reset or purge."""
    scripts = Path(__file__).resolve().parent.parent / "skills/delete-guard/scripts"
    target_os = os.name if platform is None else platform
    env_vars = _storage_env(workspace, trash_root)

    def command(label, name, arguments):
        raw_argv = [sys.executable, str(scripts / name), *arguments]
        argv = quarantine_command(raw_argv, workspace, trash_root, platform=target_os)
        entry = {"label": label, "argv": argv,
                 "command": format_command(raw_argv, env_vars, platform=target_os)}
        if target_os == "nt" and env_vars:
            entry["env"] = dict(env_vars)
        return entry

    code = preview["code"]
    commands = []
    extra = {}
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
        if len(paths) > 1:
            placeholder = "<targets.json>"
            target_args = ["--targets-file", placeholder]
            extra["targets_file"] = {"placeholder": placeholder, "contents": list(paths)}
            message += (" Use MCP cleanup_plan(save=true) with the literal paths, or replace "
                        "<targets.json> with a file containing the JSON array in next_step.targets_file.contents. "
                        "Use --json to obtain that array.")
        else:
            target_args = ["--target=" + path for path in paths]
        commands.append(command("Propose and save exact targets; no deletion", "cleanup.py",
                                ["--root", workspace, "--json", "--save-plan", *deletion_options, *target_args]))
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
    quoting = "PowerShell quoting" if target_os == "nt" else "POSIX quoting"
    return {"action": action, "message": message, "commands": commands,
            "cwd": cwd, "workspace": workspace, "trash_root": trash_root, **extra,
            "binding_notice": f"Keep the same workspace, quarantine and session settings. Command strings use {quoting}; argv is structured."}


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


def gc_guidance(workspace=None, trash_root=None, *, txid=None, platform=None):
    script = Path(__file__).resolve().parent.parent / "skills/delete-guard/scripts/gc.py"
    target_os = os.name if platform is None else platform
    base = [sys.executable, str(script)]
    env_vars = _storage_env(workspace, trash_root)
    shell_label = "PowerShell commands" if target_os == "nt" else "POSIX-shell commands"
    return {
        "plan_command": format_command(
            base + (["--txid", txid] if txid else []), env_vars, platform=target_os),
        "purge_eligible_command": format_command(
            base + ["--execute", "--all-eligible"], env_vars, platform=target_os),
        "purge_transaction_command": format_command(
            base + ["--execute", "--txid", txid or "<txid>"], env_vars, platform=target_os),
        "note": f"{shell_label}. Review the plan first. Run from the same working directory "
                "with the same AGENT_GUARD_WORKSPACE / AGENT_GUARD_TRASH settings."
                + (" Replace <txid> to select one transaction." if txid is None else ""),
    }


def restore_guidance(txid, workspace=None, trash_root=None, *,
                     can_restore=False, can_restore_with_force=False,
                     has_conflicts=False, has_manual_review=False,
                     platform=None):
    """Read-only next-step guidance for restore preview; never executes restore."""
    script = Path(__file__).resolve().parent.parent / "skills/delete-guard/scripts/restore.py"
    target_os = os.name if platform is None else platform
    base = [sys.executable, str(script)]
    env_vars = _storage_env(workspace, trash_root)
    shell_label = "PowerShell commands" if target_os == "nt" else "POSIX-shell commands"
    ordinary = format_command(base + ["restore", "--", txid], env_vars, platform=target_os)
    forced = format_command(base + ["restore", "--force", "--", txid], env_vars, platform=target_os)
    if has_manual_review:
        action = "manual_review"
        message = ("Inspect failed cross-filesystem relocation items before restoring. "
                   "Ordinary restore refuses unconfirmed copies.")
    elif can_restore:
        action = "restore"
        message = ("All checked items are ready for ordinary restore without overwriting existing files. "
                   "Ordinary restore leaves deletion budget usage unchanged.")
    elif has_conflicts and can_restore_with_force:
        action = "review_conflicts"
        message = ("Existing files occupy one or more origin paths. Compare each conflict before deciding; "
                   "operator terminal --force preserves the current occupant in a separate backup transaction first "
                   "(MCP restore does not expose --force).")
    else:
        action = "inspect"
        message = "This transaction cannot be restored automatically in its current state; inspect the reported items and errors."
    return {
        "action": action,
        "message": message,
        "restore_command": ordinary,
        "force_restore_command": forced if has_conflicts and can_restore_with_force else None,
        "note": f"{shell_label}. Preview is read-only and rechecked on execution.",
    }


def print_gc_guidance(guidance):
    print(f"GC plan   : {guidance['plan_command']}")
    print(f"GC purge  : {guidance['purge_eligible_command']}")
    print("            permanently removes eligible recovery copies")
    print(f"GC one    : {guidance['purge_transaction_command']}")
    print(guidance["note"])

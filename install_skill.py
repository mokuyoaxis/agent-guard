#!/usr/bin/env python3
"""Plan or install an agent-guard Skill link to this complete package.

The default only prints a plan. --execute installs the explicitly selected
target, preserving a previous Skill outside the Skill search directory. No host settings,
dependencies, deletion policy or recovery data are changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import sys


ROOT = Path(__file__).resolve().parent
SKILLS = {
    "delete-guard": {
        "required": (
            "SKILL.md", "scripts/_bootstrap.py", "scripts/check.py",
            "scripts/safe_delete.py", "scripts/cleanup.py",
            "scripts/delete_policy.py", "scripts/restore.py",
            "scripts/status.py", "scripts/gc.py",
        ),
        "entrypoints": (
            "safe_delete", "cleanup", "delete_policy", "restore", "status",
        ),
    },
    "exfil-guard": {
        "required": (
            "SKILL.md", "scripts/_bootstrap.py", "scripts/_payload.py",
            "scripts/check_span.py", "scripts/check_export.py",
            "scripts/check_bash_exfil.py", "scripts/safe_read.py",
            "scripts/sanitize.py", "scripts/view.py",
            "scripts/edit_config.py",
        ),
        "entrypoints": (
            "check_span", "check_export", "check_bash_exfil",
            "safe_read", "sanitize", "view", "edit_config",
        ),
    },
    "recovery-audit": {
        "required": ("SKILL.md", "references/evidence-records.md"),
        "entrypoints": (),
    },
}
DEFAULT_SKILL = "delete-guard"
SKILL = ROOT / "skills" / DEFAULT_SKILL
REQUIRED = SKILLS[DEFAULT_SKILL]["required"]


def installation_plan(selected: str, *, skill: str = DEFAULT_SKILL) -> dict:
    if skill not in SKILLS:
        raise ValueError("unsupported Skill: " + str(skill))
    metadata = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    if metadata.get("name") != "@mokuyoaxis/agent-guard":
        raise ValueError("run this helper from a complete agent-guard installation")
    if not (ROOT / "core" / "__init__.py").is_file():
        raise ValueError("the selected installation is missing core/__init__.py")
    skill_dir = SKILL if skill == DEFAULT_SKILL else ROOT / "skills" / skill
    required = REQUIRED if skill == DEFAULT_SKILL else SKILLS[skill]["required"]
    missing = [name for name in required if not (skill_dir / name).is_file()]
    if missing:
        raise ValueError("the selected Skill is incomplete: " + ", ".join(missing))

    supplied = Path(selected).expanduser().absolute()
    target = supplied.parent.resolve() / supplied.name
    source = skill_dir.resolve()
    if target.name != skill:
        raise ValueError(f"--target must name the host's {skill} Skill directory")
    unchanged = target == source or (target.is_symlink() and target.resolve() == source)
    if not unchanged and (source.is_relative_to(target) or target.is_relative_to(source)):
        raise ValueError("the target must not contain or sit inside the source Skill")
    exists = os.path.lexists(target)
    if exists and not unchanged and not target.is_symlink():
        if not target.is_dir() or not (target / "SKILL.md").is_file():
            raise ValueError("refusing to replace a target that is not an existing Skill")

    backup = None
    if exists and not unchanged:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = str(target.parent.parent / ".agent-guard-skill-backups" /
                     (target.name + "-" + stamp))
    return {
        "skill": skill,
        "package_root": str(ROOT), "package_version": metadata["version"],
        "source": str(source), "target": str(target), "backup": backup,
        "action": "unchanged" if unchanged else "replace" if exists else "link",
        "entrypoints": {name: str(target / "scripts" / (name + ".py"))
                        for name in SKILLS[skill]["entrypoints"]},
    }


def execute_plan(plan: dict) -> None:
    if plan["action"] == "unchanged":
        return
    target = Path(plan["target"])
    backup = Path(plan["backup"]) if plan["backup"] else None
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup is not None:
        backup.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(backup):
            raise FileExistsError("backup already exists: " + str(backup))
        target.rename(backup)
    try:
        target.symlink_to(plan["source"], target_is_directory=True)
    except OSError:
        if backup is not None and not os.path.lexists(target):
            backup.rename(target)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", choices=tuple(SKILLS), default=DEFAULT_SKILL,
                        help="Skill to link (default: delete-guard)")
    parser.add_argument("--target", required=True,
                        help="explicit host Skill path, ending in the selected Skill name")
    parser.add_argument("--execute", action="store_true",
                        help="apply the plan; default is preview only")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    plan = None
    try:
        plan = installation_plan(args.target, skill=args.skill)
        if args.execute:
            execute_plan(plan)
    except (OSError, ValueError) as error:
        if args.as_json:
            print(json.dumps({"ok": False, "error": str(error), "plan": plan},
                             ensure_ascii=False, indent=2))
        else:
            print("Skill installation failed: " + str(error), file=sys.stderr)
            if plan and plan["backup"] and os.path.lexists(plan["backup"]):
                print("Previous Skill retained at: " + plan["backup"], file=sys.stderr)
        return 1
    report = {"ok": True, "mode": "execute" if args.execute else "plan", **plan}
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['mode']}: {plan['action']}")
        print("Package: " + plan["package_root"] + " (" + plan["package_version"] + ")")
        print("Skill: " + plan["target"] + " -> " + plan["source"])
        if plan["backup"]:
            print("Previous Skill backup: " + plan["backup"])
        if "cleanup" in plan["entrypoints"]:
            print("Cleanup: " + plan["entrypoints"]["cleanup"])
        elif "check_span" in plan["entrypoints"]:
            print("Check: " + plan["entrypoints"]["check_span"])
        if not args.execute:
            command = [sys.executable, str(ROOT / "install_skill.py")]
            if args.skill != DEFAULT_SKILL:
                command.extend(["--skill", args.skill])
            command.extend(["--target", plan["target"], "--execute"])
            print("Apply after review: " + shlex.join(command))
        else:
            print("Reload the host's Skills if it caches them. Hook/MCP activation is separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

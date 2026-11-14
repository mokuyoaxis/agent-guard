#!/usr/bin/env python3
"""Plan or install a delete-guard Skill link to this complete package.

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
SKILL = ROOT / "skills" / "delete-guard"
REQUIRED = ("SKILL.md", "scripts/_bootstrap.py", "scripts/check.py",
            "scripts/safe_delete.py", "scripts/cleanup.py",
            "scripts/restore.py", "scripts/status.py", "scripts/gc.py")


def installation_plan(selected: str) -> dict:
    metadata = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    if metadata.get("name") != "@mokuyoaxis/agent-guard":
        raise ValueError("run this helper from a complete agent-guard installation")
    if not (ROOT / "core" / "__init__.py").is_file():
        raise ValueError("the selected installation is missing core/__init__.py")
    missing = [name for name in REQUIRED if not (SKILL / name).is_file()]
    if missing:
        raise ValueError("the selected Skill is incomplete: " + ", ".join(missing))

    supplied = Path(selected).expanduser().absolute()
    target = supplied.parent.resolve() / supplied.name
    source = SKILL.resolve()
    if target.name != "delete-guard":
        raise ValueError("--target must name the host's delete-guard Skill directory")
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
        "package_root": str(ROOT), "package_version": metadata["version"],
        "source": str(source), "target": str(target), "backup": backup,
        "action": "unchanged" if unchanged else "replace" if exists else "link",
        "entrypoints": {name: str(target / "scripts" / (name + ".py"))
                        for name in ("safe_delete", "cleanup", "restore", "status")},
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True,
                        help="explicit host Skill path, ending in delete-guard")
    parser.add_argument("--execute", action="store_true",
                        help="apply the plan; default is preview only")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    plan = None
    try:
        plan = installation_plan(args.target)
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
        print("Cleanup: " + plan["entrypoints"]["cleanup"])
        if not args.execute:
            command = [sys.executable, str(ROOT / "install_skill.py"),
                       "--target", plan["target"], "--execute"]
            print("Apply after review: " + shlex.join(command))
        else:
            print("Reload the host's Skills if it caches them. Hook/MCP activation is separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

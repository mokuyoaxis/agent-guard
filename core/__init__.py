"""agent-guard core: a reliability layer for autonomous AI agents.

Four pillars (see docs/architecture.md):

  Scope           - where the agent may act          -> policy.py
  Recoverability  - whether mistakes can be undone   -> recovery.py
  Authorization   - who decides what                 -> policy.py (mode state)
  Auditability    - what actually happened           -> audit.py

classifier.py turns shell commands or explicit path lists into structured
operation facts; policy.py turns facts into verdicts; recovery.py executes
compensations (relocate / snapshot); audit.py records everything.

Guiding principle: uncertainty increases restriction (fail closed).
"""
from __future__ import annotations

import json as _json
import os as _os


def _package_version() -> str:
    """The single source of truth for the version: the package manifest.

    This used to be a hand-maintained literal, which drifted from
    `package.json` the moment a release candidate was cut (the manifest said
    0.2.0 while this module still said 0.1.1). Reading the manifest removes
    the second place to forget. When the core is vendored without one, say
    so explicitly instead of reporting a version that may be wrong.
    """
    manifest = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "package.json")
    try:
        with open(manifest, "r", encoding="utf-8") as fh:
            version = _json.load(fh).get("version")
    except (OSError, ValueError):
        return "0.0.0+unknown"
    return version if isinstance(version, str) and version else "0.0.0+unknown"


__version__ = _package_version()

TRASH_DIRNAME = ".agent-trash"
MANIFEST_NAME = "manifest.jsonl"
AUDIT_NAME = "audit.jsonl"
STATE_NAME = "state.json"

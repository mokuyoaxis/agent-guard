"""Locate the shared Core through the real, complete installation path."""
from pathlib import Path
import sys

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SKILL_ROOT.parent.parent
if not (_REPO_ROOT / "core" / "__init__.py").is_file():
    raise SystemExit(
        "exfil-guard: shared Core not found. Keep the complete agent-guard "
        "installation and link its skills/exfil-guard directory into the host. "
        "To install or update that link, run:\n"
        "  python3 /absolute/path/to/agent-guard/install_skill.py "
        "--skill exfil-guard --target /absolute/path/to/host-skills/exfil-guard\n"
        "Review the plan, then add --execute. Older packages without this helper "
        "can still run their CLI scripts from the complete package root. "
        "Copying the Skill directory alone does not include Core."
    )
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

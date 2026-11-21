"""Terminal and file review of a cleanup selection."""
from __future__ import annotations

import os
from pathlib import Path

from .delete_config import CONFIG_DIRECTORY
from .query_text import _size, _text


FOLD_AFTER = 20


def review_path(workspace, identifier):
    directory = Path(workspace) / CONFIG_DIRECTORY / "cleanup-reviews"
    if directory.resolve() != directory:
        raise ValueError("cleanup review storage must not use directory links")
    return directory / (identifier + ".md")


def write_review(path, text, *, ignore_in_git=True):
    if ignore_in_git:
        path.parent.mkdir(parents=True, exist_ok=True)
        ignore = path.parent / ".gitignore"
        try:
            with ignore.open("x", encoding="utf-8") as output:
                output.write("*\n")
        except FileExistsError:
            pass
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_text(encoding="utf-8") != text:
            raise ValueError("saved review file changed; inspect it before requesting approval")
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())


def _exact_size(value):
    size = _size(value)
    return f"{size} ({value} B)" if value is not None and value >= 1024 else size


def _totals(rows):
    size = _exact_size(sum(row["size_bytes"] or 0 for row in rows))
    unknown = sum(row["size_bytes"] is None for row in rows)
    if unknown:
        size += f" known + {unknown} unmeasured"
    return f"{len(rows)} selected · {size}"


def format_review(plan, selected, *, folded=False):
    lines = ["CLEANUP REVIEW", "Plan         " + plan["plan_id"],
             "Workspace    " + _text(plan["workspace"]),
             "Quarantine   " + _text(plan["trash_root"]),
             "Total        " + _totals(selected),
             "Logical totals add row sizes. Hardlinks across selected roots may count more than once."]
    for effect, title in (("delete", "PERMANENT DELETE · no recovery copy"),
                          ("quarantine", "MOVE TO QUARANTINE · recoverable, still uses disk space")):
        rows = [row for row in selected if row["effect"] == effect]
        if not rows:
            continue
        lines.extend(["", title + "  |  " + _totals(rows)])
        if folded:
            groups = {}
            for row in rows:
                top = row["path"].partition("/")[0]
                groups.setdefault(top, []).append(row)
            for top, group in list(groups.items())[:10]:
                lines.append('  "' + _text(top) + '"  |  ' + _totals(group))
            if len(groups) > 10:
                lines.append(f"  … {len(groups) - 10} more top-level groups in the full list")
        else:
            for row in rows:
                lines.append('  "' + _text(row["path"]) + '"  |  ' + _exact_size(row["size_bytes"])
                             + "  |  " + _text(row["reason"]))
    for warning in plan["warnings"]:
        lines.append("Warning: " + _text(warning))
    return "\n".join(lines)

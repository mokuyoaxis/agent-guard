"""Read-only workspace diagnostics with bounded, value-free output."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import unicodedata

import install_skill

from . import carrier_unicode, delete_config, redaction
from .export_scan import supports_descriptor_scan


ROOT = install_skill.ROOT
SKILL_DIRS = (".claude/skills", ".codex/skills", ".kimi/skills", ".kimi-code/skills", ".agents/skills")
RULE_DIRS = {
    ".github/instructions": (1, {".md"}),
    ".claude/rules": (4, {".md"}),
    ".cursor/rules": (4, {".md", ".mdc"}),
}
DELETE_POLICY = ".agent-guard/delete-policy.json"
CONFIG_FILES = (DELETE_POLICY, redaction.DEFAULT_ALLOWFILE, redaction.DEFAULT_IGNOREFILE)
PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"
CARRIER_FILES = (
    "AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md",
    ".cursorrules", ".windsurfrules", ".clinerules", ".mcp.json",
    ".github/copilot-instructions.md", ".claude/CLAUDE.md",
    ".claude/settings.json", ".claude/settings.local.json", ".codex/config.toml",
    ".kimi/config.toml", ".kimi-code/config.toml", PRE_COMMIT_CONFIG, *CONFIG_FILES,
)
CONFIG_LIMIT = delete_config.MAX_CONFIG_BYTES
HOOK_LIMIT = 64 * 1024
PRE_COMMIT_LIMIT = 256 * 1024
MAX_CARRIER_FILE_BYTES = 512 * 1024
MAX_CARRIER_TOTAL_BYTES = 8 * 1024 * 1024
MAX_CARRIER_FILES = 256
MAX_CARRIER_ENTRIES = 2048
MAX_FILE_FINDINGS = 64
MAX_FINDINGS = 200
GIT_TIMEOUT = 5
LEVELS = {"PASS": 0, "WARN": 1, "FAIL": 2}


class UnsafeFile(OSError):
    """Scope, file type, or reading bounds could not be established."""


class ReadLimit(UnsafeFile):
    """A selected file exceeds its read budget."""


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


@contextmanager
def _directory(root, parts):
    """Open each POSIX ancestor without following links, including the root."""
    if supports_descriptor_scan():
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with ExitStack() as stack:
            fd = os.open(root, flags)
            stack.callback(os.close, fd)
            for part in parts:
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            yield root.joinpath(*parts), fd
    else:
        directory = root
        for part in ("", *parts):
            directory = directory / part
            info = directory.lstat()
            if _is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise UnsafeFile("unsafe directory")
        yield directory, None


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read(root, relative, limit):
    parts = Path(relative).parts
    with _directory(root, parts[:-1]) as (directory, parent_fd):
        name = parts[-1] if parent_fd is not None else directory / parts[-1]
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _is_link(before) or not stat.S_ISREG(before.st_mode):
            raise UnsafeFile("unsafe file")
        if before.st_size > limit:
            raise ReadLimit("size limit")
        flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(os.open(name, flags, dir_fd=parent_fd), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _stamp(before) != _stamp(opened):
                raise UnsafeFile("file changed before read")
            data = handle.read(limit + 1)
            if (len(data) > limit or len(data) != opened.st_size
                    or _stamp(opened) != _stamp(os.fstat(handle.fileno()))):
                raise UnsafeFile("file changed during read")
    return data, opened


def _capture(root, relative, limit):
    try:
        return _read(root, relative, limit)[0]
    except FileNotFoundError:
        return None
    except OSError:
        return UnsafeFile("file unavailable")


def _status(rows, default="PASS"):
    return max((row["status"] for row in rows), default=default, key=lambda s: LEVELS.get(s, 0))


def _delete_policy(workspace, raw):
    if isinstance(raw, OSError):
        return {"status": "FAIL", "code": "DELETE_POLICY_INVALID"}
    try:
        _, config = delete_config.parse_config(raw, str(workspace))
    except ValueError:
        return {"status": "FAIL", "code": "DELETE_POLICY_INVALID"}
    result = {"status": "DEFAULT" if raw is None else "PASS",
              "fingerprint": delete_config.fingerprint(config)}
    if "." in config.protected_paths:
        result.update(status="WARN", code="DELETE_POLICY_ROOT_SELF_LOCK")
    return result


def _exfil_allow(workspace, captured):
    def read_text(relative):
        raw = captured[relative]
        if raw is None:
            raise FileNotFoundError
        if isinstance(raw, OSError):
            raise raw
        return raw.decode("utf-8")

    effective = redaction.load_exemption(str(workspace), read_text=read_text).safe()
    codes = {item["code"] for item in effective["diagnostics"]}
    if not all(captured[name] is None for name in CONFIG_FILES[1:]):
        if codes & {"EXEMPTION_ENCODING_INVALID", "EXEMPTION_READ_FAILED"}:
            return {"status": "FAIL", "code": "EXFIL_ALLOW_INVALID", **effective}
        if codes:
            return {"status": "WARN", "code": "EXFIL_ALLOW_DIAGNOSTICS", **effective}
        return {"status": "PASS", **effective}
    return {"status": "NOT_CONFIGURED", **effective}


def _skill_directory(workspace, relative):
    with _directory(workspace, Path(relative).parts):
        return workspace / relative


def _required_files(target, required):
    return all((target / name).is_file() for name in required)


def _skill_status(directory, name, required):
    candidate = directory / name
    known = ROOT / "skills" / name
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return "NOT_CONFIGURED", None
    if _is_link(info):
        try:
            target = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return "FAIL", "SKILL_BROKEN_LINK"
        if not target.is_dir():
            return "FAIL", "SKILL_BROKEN_LINK"
        if target != known:
            # An unknown installation is outside the content-read scope.
            return "WARN", "SKILL_OTHER_INSTALLATION"
        if not (_required_files(known, required) and (ROOT / "core/__init__.py").is_file()):
            return "FAIL", "SKILL_INCOMPLETE"
        return "PASS", None
    if not stat.S_ISDIR(info.st_mode):
        return "FAIL", "SKILL_INCOMPLETE"
    if name == "recovery-audit" and _required_files(candidate, required):
        return "WARN", "SKILL_UNLINKED_COPY"
    return "FAIL", "SKILL_DETACHED_COPY"


def _skills(workspace, explicit):
    rows, approved_carriers, known_links = [], [], set()
    for relative in SKILL_DIRS:
        try:
            directory = _skill_directory(workspace, relative)
        except FileNotFoundError:
            continue
        except OSError:
            rows.append({"directory": relative, "status": "WARN", "code": "SKILL_DIRECTORY_UNVERIFIED"})
        else:
            inspected = _inspect_skills(directory, relative)
            rows.extend(inspected)
            known_links.update(f"{relative}/{row['skill']}" for row in inspected if row["status"] == "PASS")
    for index, supplied in enumerate(explicit, 1):
        label = f"explicit:{index}"
        try:
            directory = Path(supplied).expanduser().resolve(strict=True)
            if not directory.is_dir():
                raise NotADirectoryError
        except (OSError, RuntimeError, ValueError):
            rows.append({"directory": label, "status": "WARN", "code": "SKILL_DIRECTORY_UNVERIFIED"})
            continue
        inspected = _inspect_skills(directory, label)
        rows.extend(inspected)
        if all(row["status"] == "NOT_CONFIGURED" for row in inspected):
            rows.append({"directory": label, "status": "WARN", "code": "SKILL_DIRECTORY_EMPTY"})
        approved_carriers.extend(
            (ROOT / "skills", f"{row['skill']}/SKILL.md", f"{label}/{row['skill']}/SKILL.md")
            for row in inspected if row["status"] == "PASS"
        )
    found = [row for row in rows if row["status"] != "NOT_CONFIGURED"]
    return {"status": _status(found, "NOT_CONFIGURED"), "entries": rows}, approved_carriers, known_links


def _inspect_skills(directory, label):
    rows = []
    for name, spec in install_skill.SKILLS.items():
        try:
            status, code = _skill_status(directory, name, spec["required"])
        except OSError:
            status, code = "WARN", "SKILL_UNVERIFIED"
        row = {"directory": label, "skill": name, "status": status}
        if code:
            row["code"] = code
        rows.append(row)
    return rows


def _valid_hook_args(hook, args, line):
    if not args or args[0] != hook:
        return False
    if hook == "pre-commit":
        flags = args[1:]
        return len(flags) == len(set(flags)) and set(flags) <= {"--strict-paths", "--audit"}
    if len(args) < 3 or args[-2:] != ["--", "$1"] or not line.endswith(' -- "$1"'):
        return False
    flags = args[1:-2]
    return len(flags) == len(set(flags)) and set(flags) <= {"--audit"}


def _git_wrapper(data, hook, info):
    contents = data.decode("utf-8")
    if "adapters/git/hook.py" not in contents.replace("\\", "/"):
        return "NOT_CONFIGURED", None
    lines = [line.strip() for line in contents.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    try:
        tokens = shlex.split(lines[0]) if len(lines) == 1 else []
    except ValueError:
        tokens = []
    if tokens[:1] == ["exec"]:
        tokens = tokens[1:]
    if (len(tokens) < 3 or not _valid_hook_args(hook, tokens[2:], lines[0])
            or not re.fullmatch(r"python(?:[23](?:\.\d+)?)?(?:\.exe)?", Path(tokens[0]).name, re.I)):
        return "WARN", "GIT_HOOK_UNVERIFIED"
    target = Path(tokens[1])
    if not target.is_absolute() or target.parts[-3:] != ("adapters", "git", "hook.py"):
        return "WARN", "GIT_HOOK_UNVERIFIED"
    if not target.is_file():
        return "FAIL", "GIT_HOOK_BROKEN_TARGET"
    if os.name == "posix" and not info.st_mode & 0o111:
        return "FAIL", "GIT_HOOK_NOT_EXECUTABLE"
    if target.resolve() != ROOT / "adapters/git/hook.py":
        return "WARN", "GIT_HOOK_OTHER_INSTALLATION"
    return "PASS", None


def _git_hooks(workspace, pre_commit):
    git = shutil.which("git")
    if git is None:
        return {"status": "NOT_APPLICABLE", "code": "GIT_UNAVAILABLE"}
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")

    def query(*args):
        return subprocess.run(
            [os.path.abspath(git), *args], cwd=workspace, env=env, timeout=GIT_TIMEOUT,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    try:
        if Path(git).resolve().is_relative_to(workspace):
            return {"status": "WARN", "code": "GIT_EXECUTABLE_UNVERIFIED"}
        top = query("rev-parse", "--show-toplevel")
        if top.returncode:
            if top.stderr.startswith(b"fatal: not a git repository"):
                return {"status": "NOT_APPLICABLE", "code": "GIT_NOT_A_REPOSITORY"}
            return {"status": "WARN", "code": "GIT_QUERY_FAILED"}
        if Path(os.fsdecode(top.stdout.rstrip(b"\r\n"))).resolve() != workspace:
            return {"status": "WARN", "code": "GIT_WORKSPACE_MISMATCH"}
        hooks = query("rev-parse", "--path-format=absolute", "--git-path", "hooks")
        named = query("config", "--name-only", "--get-regexp", r"^hook\.")
        if hooks.returncode or named.returncode not in {0, 1}:
            return {"status": "WARN", "code": "GIT_QUERY_FAILED"}
        if named.returncode == 0:
            return {"status": "WARN", "code": "GIT_NAMED_HOOKS_UNVERIFIED"}
        hook_path = Path(os.fsdecode(hooks.stdout.rstrip(b"\r\n")))
        if hook_path == Path(os.devnull):
            return {"status": "NOT_CONFIGURED", "code": "GIT_HOOKS_DISABLED"}
        relative = hook_path.relative_to(workspace)
        if ".." in relative.parts:
            raise ValueError
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return {"status": "WARN", "code": "GIT_HOOK_DIRECTORY_UNVERIFIED"}
    rows = []
    for hook in ("pre-commit", "commit-msg"):
        try:
            data, info = _read(workspace, relative / hook, HOOK_LIMIT)
            status, code = _git_wrapper(data, hook, info)
        except FileNotFoundError:
            status, code = "NOT_CONFIGURED", None
        except (OSError, UnicodeError, RuntimeError, ValueError):
            status, code = "WARN", "GIT_HOOK_UNVERIFIED"
        row = {"hook": hook, "status": status}
        if code:
            row["code"] = code
        rows.append(row)
    configured = sum(row["status"] == "PASS" for row in rows)
    status = _status(rows) if any(row["status"] in {"FAIL", "WARN"} for row in rows) else "NOT_CONFIGURED"
    report = {"status": status, "hooks": rows}
    if status == "NOT_CONFIGURED":
        if configured == 2:
            report["status"] = "PASS"
        elif configured:
            report.update(status="WARN", code="GIT_HOOK_PARTIAL")
        elif isinstance(pre_commit, OSError) or (
                pre_commit and b"adapters/git/hook.py" in pre_commit.replace(b"\\", b"/")):
            report.update(status="WARN", code="GIT_HOOK_DECLARATION_UNVERIFIED")
    return report


def _public_relative(label):
    if (len(label) > 512 or "\\" in label
            or any(unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for ch in label)
            or carrier_unicode.MARKERS.search(label) or redaction.detect_secrets(label)):
        return None
    return label


class _CarrierScan:
    def __init__(self):
        self.files_seen = self.files_scanned = self.entries_seen = self.bytes_read = 0
        self.findings = []
        self.findings_omitted = 0
        self.finding_counts = {}
        self.errors = set()
        self.status = "PASS"
        self.seen = set()

    def visit(self, workspace, relative, depth, suffixes=None, known_links=()):
        try:
            with _directory(workspace, Path(relative).parts) as (directory, fd):
                children = []
                with os.scandir(directory if fd is None else fd) as entries:
                    for entry in entries:
                        if self.entries_seen >= MAX_CARRIER_ENTRIES:
                            self.errors.add("CARRIER_SCAN_TRUNCATED")
                            break
                        self.entries_seen += 1
                        children.append((entry.name, entry.is_dir(follow_symlinks=False), entry.is_symlink()))
            for name, is_directory, is_link in sorted(children):
                candidate = f"{relative}/{name}"
                if suffixes is None:
                    if is_directory:
                        yield f"{candidate}/SKILL.md"
                    elif is_link and candidate not in known_links:
                        self.errors.add("CARRIER_LINK_UNVERIFIED")
                elif is_directory and depth > 1:
                    yield from self.visit(workspace, candidate, depth - 1, suffixes)
                elif is_directory:
                    self.errors.add("CARRIER_SCAN_TRUNCATED")
                elif Path(name).suffix in suffixes:
                    yield candidate
                elif is_link and depth > 1:
                    self.errors.add("CARRIER_LINK_UNVERIFIED")
        except FileNotFoundError:
            return
        except OSError:
            self.errors.add("CARRIER_READ_FAILED")

    def add(self, root, relative, label, captured):
        remaining = MAX_CARRIER_TOTAL_BYTES - self.bytes_read
        if self.files_seen >= MAX_CARRIER_FILES or remaining <= 0:
            self.errors.add("CARRIER_SCAN_TRUNCATED")
            return
        limit = min(remaining, MAX_CARRIER_FILE_BYTES)
        identity = None
        try:
            if relative in captured:
                data = captured[relative]
                if data is None:
                    return
                if isinstance(data, OSError):
                    raise data
                if len(data) > limit:
                    raise ReadLimit
            else:
                data, info = _read(root, relative, limit)
                if info.st_ino:
                    identity = (info.st_dev, info.st_ino)
        except FileNotFoundError:
            return
        except ReadLimit:
            self.errors.add("CARRIER_SCAN_TRUNCATED")
            return
        except UnsafeFile:
            self.errors.add("CARRIER_UNSAFE_FILE")
            return
        except OSError:
            self.errors.add("CARRIER_READ_FAILED")
            return
        self.files_seen += 1
        self.bytes_read += len(data)
        if identity in self.seen:
            return
        if identity is not None:
            self.seen.add(identity)
        try:
            value = data.decode("utf-8")
        except UnicodeDecodeError:
            self.errors.add("CARRIER_ENCODING_INVALID")
            return
        self.files_scanned += 1
        public = _public_relative(label)
        for i, finding in enumerate(carrier_unicode.findings(value)):
            code = finding["code"]
            self.finding_counts[code] = self.finding_counts.get(code, 0) + finding["count"]
            self.status = max(self.status, finding["severity"], key=LEVELS.__getitem__)
            if i < MAX_FILE_FINDINGS and len(self.findings) < MAX_FINDINGS:
                self.findings.append({"path": public, **finding})
            else:
                self.findings_omitted += 1

    def report(self):
        status = "WARN" if self.errors and self.status == "PASS" else self.status
        return {"status": status, "complete": not self.errors, "files_scanned": self.files_scanned,
                "bytes_read": self.bytes_read, "entries_seen": self.entries_seen,
                "diagnostics": sorted(self.errors), "findings": self.findings,
                "finding_counts": self.finding_counts, "findings_omitted": self.findings_omitted}


def _carrier_hygiene(workspace, captured, approved, known_links):
    scan = _CarrierScan()
    for name in CARRIER_FILES:
        scan.add(workspace, name, name, captured)
    for directory, (depth, suffixes) in RULE_DIRS.items():
        for name in scan.visit(workspace, directory, depth, suffixes):
            scan.add(workspace, name, name, captured)
    for directory in SKILL_DIRS:
        for name in scan.visit(workspace, directory, 1, known_links=known_links):
            scan.add(workspace, name, name, captured)
    for root, name, label in approved:
        scan.add(root, name, label, {})
    return scan.report()


def diagnose(workspace, *, skill_dirs=(), trash_root=None, strict=False):
    """Inspect the selected workspace. Invalid CLI paths raise ValueError."""
    from adapters.mcp.operations import resolve_trash_root

    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        if not root.is_dir() or root.parent == root:
            raise ValueError
        trash = resolve_trash_root(str(root), trash_root)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("workspace must be an existing non-root directory and trash-root must be valid") from None
    captured = {name: _capture(root, name, CONFIG_LIMIT) for name in CONFIG_FILES}
    captured[PRE_COMMIT_CONFIG] = _capture(root, PRE_COMMIT_CONFIG, PRE_COMMIT_LIMIT)
    skills, approved, known_links = _skills(root, skill_dirs)
    checks = {
        "delete_policy": _delete_policy(root, captured[DELETE_POLICY]),
        "exfil_allow": _exfil_allow(root, captured),
        "skills": skills,
        "git_hooks": _git_hooks(root, captured[PRE_COMMIT_CONFIG]),
        "carrier_hygiene": _carrier_hygiene(root, captured, approved, known_links),
    }
    report = {
        "schema": "workspace_doctor/v1", "schema_version": 1, "harness": "workspace",
        "status": _status([{"status": "PASS"}, *checks.values()]), "strict": strict,
        "workspace": str(root), "trash_root": trash, "read_only": True,
        "package_version": json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"],
        "raw_values_retained": False, "host_interception": "UNVERIFIED", "hook_enforcement": "UNVERIFIED",
        "checks": checks,
    }
    for severity, field in (("FAIL", "problems"), ("WARN", "warnings")):
        report[field] = [{"check": name, "code": check.get("code", name.upper())}
                         for name, check in checks.items() if check["status"] == severity]
    return report


def render(report):
    lines = [f"Workspace doctor {report['status']}"]
    for name, check in report["checks"].items():
        code = check.get("code", "")
        lines.append(f"- {name} {check['status']}" + (f" ({code})" if code else ""))
        for item in (*check.get("diagnostics", ()), *check.get("entries", ()), *check.get("hooks", ())):
            code = item if isinstance(item, str) else item.get("code")
            if code:
                lines.append(f"  {code}")
        for item in check.get("findings", ()):
            location = item["path"] or "<withheld>"
            lines.append(f"  {location}:{item['line']}:{item['column']} {item['severity']} {item['code']}")
        if check.get("findings_omitted"):
            lines.append(f"  Findings omitted {check['findings_omitted']}")
    lines.append("Host interception and Hook enforcement UNVERIFIED")
    return "\n".join(lines) + "\n"

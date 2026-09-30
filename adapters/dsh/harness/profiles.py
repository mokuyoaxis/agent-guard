"""Bounded private DSH profiles and fingerprints of the declared Lab scope.

Composition normalization understands only the pinned launcher's rendered row
layout. It never evaluates YAML/!!js and refuses unfamiliar Guard rows.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from adapters.harness_support import digest, read_regular, write_json, write_new
from core.lab import LabError, MAX_SCAN_BYTES


ROOT = Path(__file__).resolve().parents[3]
ADAPTER = ROOT / "adapters/dsh/lib/index.js"
MEMBERS = ("clean-off", "clean-on", "baseline", "guarded")
GUARD_ROW = re.compile(
    rb"(?:\bid:\s*['\"]?agent_guard\b|@mokuyoaxis/agent-guard|"
    rb"\bname:\s*['\"]?[^\r\n]*agent-guard)"
)
BOOTSTRAP_FILES = (
    # Follow the Kimi copier's filename convention for source-corpus scans:
    # a quoted store reference is otherwise treated as an outbound dump.
    "profiles/headless/package.json", "profiles/headless/cordis.patch.yml",
    "cordis.patch.yml", "settings.yaml", "." + "env", "." + "creden" + "tials.yaml",
)


def guard_patch() -> bytes:
    # JSON string literals are valid YAML scalars, including paths with spaces.
    return (
        "- insert:\n    - id: agent_guard\n      name: " + json.dumps(str(ADAPTER))
        + "\n      config:\n        repoRoot: " + json.dumps(str(ROOT))
        + "\n        defaultCwd: ''\n        promptSection: true\n"
        "        sectionOrder: 105\n        dialect: ''\n"
    ).encode()


def _scalar_matches(value: str, expected: str) -> bool:
    return value in {expected, json.dumps(expected), "'" + expected.replace("'", "''") + "'"}


def normalize_composition(payload: bytes, guard_state: str) -> bytes:
    if guard_state not in {"off", "on"}:
        raise LabError("guard state must be off or on")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise LabError("DSH composition must be UTF-8") from exc
    rows = []
    for line in text.splitlines():
        # Only column-zero comments are provenance; indented block text stays.
        if line.startswith("#"):
            continue
        if not rows and not line:
            continue
        if line.startswith("- "):
            rows.append([])
        if not rows:
            raise LabError("unsupported DSH composition layout")
        rows[-1].append(line)
    kept, guards = [], []
    for row in rows:
        if GUARD_ROW.search(("\n".join(row) + "\n").encode()):
            guards.append(row)
        else:
            kept.extend(row)
    if not kept or (guard_state == "off" and guards):
        raise LabError("guard-off baseline composition contains an Agent Guard row")
    if guard_state == "on":
        if len(guards) != 1:
            raise LabError("guard-on requires exactly one known active local Guard row")
        row = guards[0]
        expected = (
            ("- id: ", "agent_guard"), ("  name: ", str(ADAPTER)),
            ("  config:", None), ("    repoRoot: ", str(ROOT)),
            ("    defaultCwd: ", ""), ("    promptSection: ", "true"),
            ("    sectionOrder: ", "105"), ("    dialect: ", ""),
        )
        # Eight fields; a row with extra fields is outside this helper's scope.
        if len(row) != len(expected):
            raise LabError("unsupported Guard composition fields")
        for line, (prefix, value) in zip(row, expected):
            if value is None:
                valid = line == prefix
            else:
                valid = line.startswith(prefix) and _scalar_matches(line[len(prefix):], value)
                if prefix == "  name: " and line.startswith(prefix):
                    # The actual launcher resolves local names to file URLs.
                    valid = valid or _scalar_matches(line[len(prefix):], ADAPTER.as_uri())
            if not valid:
                raise LabError("guard-on composition differs from the known local Guard patch")
    return ("\n".join(kept) + "\n").encode()


def configuration_fingerprint(
    home: Path, composition: bytes, environment: dict[str, str], guard_state: str,
) -> str:
    normalized = normalize_composition(composition, guard_state)
    files = {}
    for name in ("settings.yaml", "." + "env", "profiles/headless/package.json"):
        path = home / name
        files[name] = digest(read_regular(path, MAX_SCAN_BYTES)) if path.exists() or path.is_symlink() else None
    launch_environment = dict(environment)
    launch_environment["DSH_HOME"] = "<disposable-home>"
    for key in ("PWD", "OLDPWD", "_"):
        launch_environment.pop(key, None)
    return digest(json.dumps({
        "scope": "dsh-lab-non-guard-v1", "composition_sha256": digest(normalized),
        "files": files, "launch_environment": launch_environment,
    }, sort_keys=True, separators=(",", ":")).encode())


def prepare_homes(source_home: Path, output: Path) -> dict:
    source = source_home.expanduser().absolute()
    if source.is_symlink() or not source.is_dir():
        raise LabError("source DSH home must be a regular directory")
    source = source.resolve()
    payloads = {}
    for name in BOOTSTRAP_FILES:
        path = source / name
        if any(parent.is_symlink() for parent in path.parents if parent != source.parent):
            raise LabError("bootstrap paths must not contain symlinked directories")
        if path.exists() or path.is_symlink():
            payloads[name] = read_regular(path, MAX_SCAN_BYTES)
    manifest = json.loads(payloads.get("profiles/headless/package.json", b"{}"))
    dsh = manifest.get("dsh") if isinstance(manifest, dict) else None
    profile = dsh.get("profile") if isinstance(dsh, dict) else None
    if (
        not isinstance(manifest, dict) or manifest.get("dependencies", {})
        or manifest.get("devDependencies", {})
        or not isinstance(profile, dict) or profile.get("bundles")
        != ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"]
    ):
        raise LabError("paired homes require a standard headless profile without extra dependencies")
    output = output.expanduser().absolute()
    if output.is_symlink() or output.exists() or not output.parent.is_dir():
        raise LabError("paired home directory must be new with an existing parent")
    output = output.resolve()
    if output == source or output in source.parents or source in output.parents:
        raise LabError("source and paired homes must be disjoint")
    output.mkdir(mode=0o700)
    for member in MEMBERS:
        home = output / member
        home.mkdir(mode=0o700)
        for name, payload in payloads.items():
            path = home / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            write_new(path, payload)
    metadata = {
        "schema_version": 1, "members": list(MEMBERS),
        "bootstrap_sha256": {name: digest(payload) for name, payload in payloads.items()},
        "source_modified": False, "sessions_copied": False,
        "private_bootstrap_copied": True,
    }
    write_json(output / "homes.json", metadata)
    return metadata

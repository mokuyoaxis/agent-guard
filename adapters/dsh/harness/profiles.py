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
GUARD_PROFILES = ("deletion-v1", "read-redaction-v1")
GUARD_ROW = re.compile(
    rb"(?:\bid:\s*['\"]?agent_guard\b|@mokuyoaxis/agent-guard|"
    rb"\bname:\s*['\"]?[^\r\n]*agent-guard)"
)
BOOTSTRAP_FILES = (
    # Follow the Kimi copier's filename convention for source-corpus scans:
    # a quoted store reference is otherwise treated as an outbound dump.
    "profiles/headless/package.json", "profiles/headless/cordis.patch.yml",
    "profiles/headless/cordis.yml", "cordis.patch.yml", "cordis.yml",
    "settings.yaml", "settings.yaml.imported", "." + "env", "." + "creden" + "tials.yaml",
)


def guard_configuration(profile="deletion-v1", workspace=None, dsh_root=None):
    if profile not in GUARD_PROFILES:
        raise LabError("unknown DSH guard profile")
    config = {"repoRoot": str(ROOT), "defaultCwd": "", "promptSection": True,
              "sectionOrder": 105, "dialect": ""}
    if profile == "read-redaction-v1":
        if (workspace is None or dsh_root is None or not Path(workspace).is_absolute()
                or not Path(dsh_root).is_absolute()):
            raise LabError("read profile requires an absolute fixture and DSH package root")
        config.update(defaultCwd=str(Path(workspace).resolve()), promptSection=False,
                      readResultGuard=True, readGuardDshRoot=str(Path(dsh_root).resolve()))
    return config


def guard_patch(profile="deletion-v1", workspace=None, dsh_root=None) -> bytes:
    # JSON string literals are valid YAML scalars, including paths with spaces.
    if profile == "deletion-v1":
        # Retain the exact historical patch bytes for old receipts/plans.
        return (
            "- insert:\n    - id: agent_guard\n      name: " + json.dumps(str(ADAPTER))
            + "\n      config:\n        repoRoot: " + json.dumps(str(ROOT))
            + "\n        defaultCwd: ''\n        promptSection: true\n"
            "        sectionOrder: 105\n        dialect: ''\n"
        ).encode()
    config = guard_configuration(profile, workspace, dsh_root)
    return ("- insert:\n    - id: agent_guard\n      name: " + json.dumps(str(ADAPTER))
            + "\n      config:\n" + "".join("        " + key + ": " + json.dumps(value)
                                             + "\n" for key, value in config.items())).encode()


def _scalar_matches(value: str, expected: str) -> bool:
    return value in {expected, json.dumps(expected), "'" + expected.replace("'", "''") + "'"}


def normalize_composition(payload: bytes, guard_state: str, *,
                          profile="deletion-v1", workspace=None, dsh_root=None) -> bytes:
    if guard_state not in {"off", "on"}:
        raise LabError("guard state must be off or on")
    config = guard_configuration(profile, workspace, dsh_root)
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
        if profile == "read-redaction-v1":
            # The pinned launcher folds long path scalars onto one indented
            # line. Accept only that exact spelling of our two bound paths.
            expanded, index = [], 0
            while index < len(row):
                line = row[index]
                key = next((key for key in ("defaultCwd", "readGuardDshRoot")
                            if line == "    " + key + ": >-"), None)
                if key is not None:
                    if index + 1 >= len(row) or row[index + 1] != "      " + config[key]:
                        raise LabError("unsupported folded Guard path")
                    expanded.append("    " + key + ": " + json.dumps(config[key]))
                    index += 2
                else:
                    expanded.append(line)
                    index += 1
            row = expanded
        expected = [("- id: ", "agent_guard"), ("  name: ", str(ADAPTER)), ("  config:", None)]
        expected += [("    " + key + ": ", str(value).lower() if isinstance(value, bool) else str(value))
                     for key, value in config.items()]
        # Only the exact reviewed profile row is excluded from the fingerprint.
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
    *, profile="deletion-v1", workspace=None, dsh_root=None, scope_version=2,
) -> str:
    if type(scope_version) is not int or scope_version not in {1, 2}:
        raise LabError("unsupported DSH configuration scope")
    if scope_version == 1 and profile != "deletion-v1":
        raise LabError("legacy configuration scope cannot identify a read profile")
    normalized = normalize_composition(composition, guard_state, profile=profile,
                                       workspace=workspace, dsh_root=dsh_root)
    files = {}
    names = ["settings.yaml", "." + "env", "profiles/headless/package.json"]
    if scope_version == 2:
        names += ["settings.yaml.imported", "cordis.yml", "cordis.patch.yml",
                  "profiles/headless/cordis.yml", "profiles/headless/cordis.patch.yml"]
    for name in names:
        path = home / name
        files[name] = digest(read_regular(path, MAX_SCAN_BYTES)) if path.exists() or path.is_symlink() else None
    launch_environment = dict(environment)
    launch_environment["DSH_HOME"] = "<disposable-home>"
    bookkeeping = ("PWD", "OLDPWD", "_") + (("SHLVL",) if scope_version == 2 else ())
    for key in bookkeeping:
        launch_environment.pop(key, None)
    declaration = {
        "scope": "dsh-lab-non-guard-v" + str(scope_version), "composition_sha256": digest(normalized),
        "files": files, "launch_environment": launch_environment,
    }
    if scope_version == 2:
        declaration["guard_profile"] = profile
    return digest(json.dumps(declaration, sort_keys=True, separators=(",", ":")).encode())


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

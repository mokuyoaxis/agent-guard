#!/usr/bin/env python3
"""Read-only, bounded verification of one GitHub/npm release artifact."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tarfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from release_channel import release_channel


REGISTRY = "https://registry.npmjs.org/"
MAX_METADATA = 8 * 1024 * 1024
EXIT_CODES = {"PASS": 0, "MISMATCH": 1, "NOT_READY": 2, "READ_ERROR": 3, "INPUT_ERROR": 4}


class Observation(Exception):
    def __init__(self, status, code, source):
        super().__init__(code)
        self.status, self.code, self.source = status, code, source


class Sources:
    """Use public npm reads and the existing authenticated GitHub CLI."""
    def __init__(self, repository):
        self.repository = repository
        self.deadline = 0.0

    def timeout(self, source):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Observation("READ_ERROR", "READ_DEADLINE_REACHED", source)
        return min(20.0, remaining)

    def http(self, url, limit, source):
        request = Request(url, headers={"User-Agent": "agent-guard-release-verification",
                                        "Cache-Control": "no-cache"})
        try:
            with urlopen(request, timeout=self.timeout(source)) as response:
                body = response.read(limit + 1)
        except HTTPError as error:
            status = "NOT_READY" if error.code == 404 else "READ_ERROR"
            raise Observation(status, f"HTTP_{error.code}", source) from error
        except (URLError, TimeoutError, OSError) as error:
            raise Observation("READ_ERROR", type(error).__name__, source) from error
        if len(body) > limit:
            raise Observation("MISMATCH", "RESPONSE_EXCEEDS_EXPECTED_SIZE", source)
        return body

    def gh(self, endpoint, binary=False):
        argv = ["gh", "api", f"repos/{self.repository}/{endpoint}"]
        if binary:
            argv += ["-H", "Accept: application/octet-stream"]
        try:
            result = subprocess.run(argv, capture_output=True, timeout=self.timeout("github"))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Observation("READ_ERROR", type(error).__name__, "github") from error
        if result.returncode:
            # Native stderr can contain a temporary signed download URL.
            # Retain the HTTP classification, without copying that URL or auth state.
            match = re.search(rb"HTTP (\d{3})", result.stderr)
            code = match[1].decode() if match else None
            raise Observation("NOT_READY" if code == "404" else "READ_ERROR",
                              f"HTTP_{code}" if code else f"GH_EXIT_{result.returncode}", "github")
        return result.stdout

    def registry(self, package):
        return decode_json(self.http(REGISTRY + quote(package, safe="@"), MAX_METADATA, "npm"), "npm")

    def github(self, endpoint):
        return decode_json(self.gh(endpoint), "github")

    def asset(self, asset_id):
        return self.gh(f"releases/assets/{asset_id}", binary=True)

    def tarball(self, url, size):
        return self.http(url, size, "npm_tarball")


def decode_json(body, source):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise Observation("READ_ERROR", "INVALID_JSON", source) from error
    if not isinstance(value, dict):
        raise Observation("READ_ERROR", "EXPECTED_JSON_OBJECT", source)
    return value


def expected_release(tarball, notes, repository, version, commit):
    channel = release_channel(version)
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repository, re.ASCII) is None:
        raise ValueError("repository must be owner/name")
    if re.fullmatch(r"[a-f0-9]{40}", commit) is None:
        raise ValueError("commit must be the full Git commit SHA")
    payload = tarball.read_bytes()
    with tarfile.open(tarball, "r:gz") as archive:
        member = archive.extractfile("package/package.json")
        if member is None:
            raise ValueError("archive package manifest is not a file")
        manifest = json.load(member)
    if (not isinstance(manifest, dict) or manifest.get("version") != version
            or manifest.get("name") != "@mokuyoaxis/agent-guard"):
        raise ValueError("archive package identity does not match the requested release")
    return {"repository": repository, "version": version, "commit": commit,
            "package": manifest["name"], "filename": tarball.name, "payload": payload,
            "notes": notes.read_text(encoding="utf-8").strip(),
            "npm_tag": channel["NPM_DIST_TAG"],
            "prerelease": channel["RELEASE_PRERELEASE"] == "true",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "sha1": hashlib.sha1(payload).hexdigest(),
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode()}


def check(condition, code, source):
    if not condition:
        raise Observation("MISMATCH", code, source)


def verify_once(expected, sources, directory, checks):
    package, version = expected["package"], expected["version"]
    registry = sources.registry(package)
    (directory / "npm-metadata.json").write_text(json.dumps(registry, indent=2) + "\n")
    versions, tags = registry.get("versions"), registry.get("dist-tags")
    if not isinstance(versions, dict) or not isinstance(tags, dict):
        raise Observation("READ_ERROR", "INVALID_REGISTRY_METADATA", "npm")
    if version not in versions:
        raise Observation("NOT_READY", "NPM_VERSION_NOT_VISIBLE", "npm")
    published = versions[version]
    check(isinstance(published, dict) and published.get("name") == package
          and published.get("version") == version, "NPM_PACKAGE_IDENTITY", "npm")
    checks["npm_version"] = "PASS"
    if tags.get(expected["npm_tag"]) != version:
        raise Observation("NOT_READY", "NPM_DIST_TAG_NOT_VISIBLE", "npm")
    checks["npm_channel"] = "PASS"
    dist = published.get("dist")
    check(isinstance(dist, dict) and dist.get("shasum") == expected["sha1"]
          and dist.get("integrity") == expected["integrity"], "NPM_INTEGRITY", "npm")
    tarball_url = REGISTRY + package + "/-/agent-guard-" + version + ".tgz"
    check(dist.get("tarball") == tarball_url, "NPM_TARBALL_URL", "npm")
    npm_bytes = sources.tarball(tarball_url, len(expected["payload"]))
    (directory / expected["filename"]).write_bytes(npm_bytes)
    check(npm_bytes == expected["payload"], "NPM_ARTIFACT_BYTES", "npm_tarball")
    checks["npm_artifact"] = "PASS"

    release = sources.github("releases/tags/" + version)
    (directory / "github-release.json").write_text(json.dumps(release, indent=2) + "\n")
    check(release.get("tag_name") == version, "GITHUB_RELEASE_TAG", "github")
    if release.get("draft") is True:
        raise Observation("NOT_READY", "GITHUB_RELEASE_IS_DRAFT", "github")
    check(release.get("draft") is False and release.get("prerelease") == expected["prerelease"],
          "GITHUB_RELEASE_CHANNEL", "github")
    check(isinstance(release.get("body"), str) and release["body"].strip() == expected["notes"],
          "GITHUB_RELEASE_NOTES", "github")
    checks["github_release"] = "PASS"
    checks["release_notes"] = "PASS"
    ref = sources.github("git/ref/tags/" + version)
    obj = ref.get("object")
    if not isinstance(obj, dict):
        raise Observation("READ_ERROR", "INVALID_GITHUB_TAG_REF", "github")
    if obj.get("type") == "tag":
        if not isinstance(obj.get("sha"), str):
            raise Observation("READ_ERROR", "INVALID_GITHUB_TAG_REF", "github")
        obj = sources.github("git/tags/" + obj["sha"]).get("object")
        if not isinstance(obj, dict):
            raise Observation("READ_ERROR", "INVALID_GITHUB_TAG_REF", "github")
    check(obj.get("type") == "commit" and obj.get("sha") == expected["commit"],
          "GITHUB_TAG_COMMIT", "github")
    checks["tag_commit"] = "PASS"

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise Observation("READ_ERROR", "INVALID_GITHUB_ASSETS", "github")
    assets = {a["name"]: a for a in assets if isinstance(a, dict) and "name" in a}
    filename = expected["filename"]
    if filename not in assets or filename + ".sha256" not in assets:
        raise Observation("NOT_READY", "GITHUB_ASSETS_NOT_VISIBLE", "github")
    for name, content in ((filename, expected["payload"]),
                          (filename + ".sha256", None)):
        asset = assets[name]
        if type(asset.get("id")) is not int:
            raise Observation("READ_ERROR", "INVALID_GITHUB_ASSET_ID", "github")
        body = sources.asset(asset["id"])
        (directory / ("github-" + name)).write_bytes(body)
        check(asset.get("size") == len(body)
              and asset.get("digest") == "sha256:" + hashlib.sha256(body).hexdigest(),
              "GITHUB_ASSET_DIGEST", "github_asset")
        if content is not None:
            check(body == content, "GITHUB_ARTIFACT_BYTES", "github_asset")
        else:
            check(body.decode("utf-8", errors="replace").split() == [expected["sha256"], filename],
                  "GITHUB_CHECKSUM_FILE", "github_asset")
    checks["github_artifacts"] = "PASS"


def summary(report):
    if report["status"] == "INPUT_ERROR":
        return f"Release verification: INPUT_ERROR\nLocal input: {report['code']}\n"
    lines = [f"Release verification: {report['status']}",
             f"Version: {report['version']}; npm channel: {report['npm_tag']}",
             f"Attempts: {len(report['attempts'])}; SHA256: {report['sha256']}"]
    lines.extend(f"{name}: {status}" for name, status in report["checks"].items())
    if report.get("observation"):
        lines.append("Last observation: {source}/{code}".format(**report["observation"]))
    lines.append("Scope: version/channel, tag commit, notes and identical archives; no install, model or signature audit.")
    return "\n".join(lines) + "\n"


def verify(expected, sources, output, *, timeout=900, interval=15,
           clock=time.monotonic, sleep=time.sleep):
    start = clock()
    deadline = start + timeout
    sources.deadline = deadline
    report = {"schema": "release_verification/v1", "status": "NOT_READY",
              **{k: expected[k] for k in ("repository", "package", "version", "commit", "npm_tag", "sha256")},
              "attempts": [], "checks": {},
              "unverified": ["isolated_install", "runtime_mcp", "registry_signature", "provenance_signature"]}
    while True:
        number = len(report["attempts"]) + 1
        directory = output / f"attempt-{number:03d}"
        directory.mkdir()
        checks = {}
        try:
            verify_once(expected, sources, directory, checks)
        except Observation as error:
            report["status"] = error.status
            report["observation"] = {"source": error.source, "code": error.code}
        else:
            report["status"] = "PASS"
            report.pop("observation", None)
        report["checks"] = checks
        attempt = {"number": number, "status": report["status"],
                   "elapsed_seconds": round(clock() - start, 3), "checks": checks,
                   "observation": report.get("observation")}
        report["attempts"].append(attempt)
        (directory / "result.json").write_text(json.dumps(attempt, indent=2) + "\n")
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "summary.txt").write_text(summary(report))
        print(json.dumps({"attempt": number, "status": report["status"],
                          "observation": report.get("observation")}), flush=True)
        remaining = deadline - clock()
        if report["status"] in ("PASS", "MISMATCH") or remaining <= 0:
            return report
        sleep(min(interval, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tarball", required=True, type=Path)
    parser.add_argument("--notes", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--summary", type=Path, help="append the fixed result to a CI step summary")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--interval", type=float, default=15)
    args = parser.parse_args()
    if not all(math.isfinite(value) and value > 0 for value in (args.timeout, args.interval)):
        parser.error("timeout and interval must be finite and positive")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        expected = expected_release(args.tarball, args.notes, args.repository, args.version, args.commit)
    except (ValueError, OSError, KeyError, tarfile.TarError) as error:
        report = {"schema": "release_verification/v1", "status": "INPUT_ERROR",
                  "code": type(error).__name__, "message": str(error)}
        (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    else:
        report = verify(expected, Sources(args.repository), args.output_dir,
                        timeout=args.timeout, interval=args.interval)
    rendered = summary(report)
    (args.output_dir / "summary.txt").write_text(rendered)
    print(rendered, end="")
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as stream:
            stream.write("\n```text\n" + rendered + "```\n")
    return EXIT_CODES[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())

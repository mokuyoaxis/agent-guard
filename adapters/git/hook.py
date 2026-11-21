#!/usr/bin/env python3
"""Opt-in Git checks for staged text and the proposed commit message."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import exfil_audit, policy, redaction


BINARY_PREFIX = 8192
GIT_ERRORS = (OSError, subprocess.SubprocessError, UnicodeError, ValueError)


class InvalidArguments(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InvalidArguments()


def git(workspace, *args):
    return subprocess.run(["git", *args], cwd=workspace, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=git_environment(),
                          timeout=30, check=True).stdout


def git_environment():
    return {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1"}


def staged_entries(workspace):
    diff = git(workspace, "diff", "--cached", "--raw", "--no-abbrev", "-z", "--no-ext-diff", "--no-color",
               "--no-relative", "--diff-filter=ACMRT")
    fields = iter(diff[:-1].split(b"\0")) if diff else iter(())
    for header in fields:
        _, mode, _, oid, change = header.split()
        path = next(fields, b"")
        if change[:1] in (b"C", b"R"):
            path = next(fields, b"")  # Check the destination path against exemptions.
        if not path:
            raise ValueError("incomplete staged entry")
        yield mode, oid.decode("ascii"), os.fsdecode(path)


def staged_text(workspace, oid, limit):
    size = int(git(workspace, "cat-file", "-s", oid))
    # The object ID keeps size, prefix and body on the same staged bytes.
    with subprocess.Popen(["git", "cat-file", "blob", oid], cwd=workspace,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=git_environment()) as child:
        try:
            prefix = child.stdout.read(min(size, BINARY_PREFIX))
            if b"\0" in prefix:
                return None
            if size > limit:
                raise ValueError("staged text exceeds scan cap")
            data = prefix + child.stdout.read(size - len(prefix) + 1)
            if child.wait(timeout=30) or len(data) != size:
                raise ValueError("incomplete staged blob")
            return data.decode("utf-8")
        finally:
            if child.poll() is None:
                child.terminate()  # A skipped or oversized blob may still be writing its pipe.
                child.wait(timeout=30)


def unscannable(channel, size=None):
    return {"decision": "BLOCK", "code": policy.CODE_BLOCK_OUTPUT_UNSCANNABLE,
            "channel": channel, "size_bytes": size, "spans": [], "exit": 2}


def assess(text, workspace, channel, exemption, mode, *, strict_paths=False):
    scan = redaction.scan_text(text, channel=channel, workspace=workspace, exemption=exemption)
    if not scan.scanned:
        return unscannable(channel, scan.size_bytes)
    top, blocked, verdicts = policy.assess_unrewritten_spans(
        scan.spans, redaction.CHANNELS[channel], mode, strict_paths=strict_paths)
    return {"decision": top.decision, "code": top.code, "spans": redaction.verdict_rows(scan.spans, verdicts),
            "channel": channel, "size_bytes": scan.size_bytes, "exempted": scan.exempted,
            "exit": 2 if blocked else 0}


def commit_message(path, workspace, exemption, mode):
    channel = "git-commit-message"
    limit = redaction.max_scan_bytes()
    try:
        with open(path, "rb") as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            return unscannable(channel)
        # Git may preserve comments and scissors markers under a per-command
        # cleanup mode. A supplied marker cannot exclude text from this check.
        return assess(data.decode("utf-8"), workspace, channel, exemption, mode, strict_paths=True)
    except (OSError, UnicodeError):
        return unscannable(channel)


def pre_commit(workspace, exemption, mode, strict_paths, audit_file):
    channel = "file-write"
    counts = dict(files_scanned=0, skipped_non_regular=0, skipped_binary=0, skipped_exempt=0)
    findings = []
    limit = redaction.max_scan_bytes()
    try:
        for index, (mode_bits, oid, path) in enumerate(staged_entries(workspace), 1):
            if mode_bits in (b"120000", b"160000"):
                counts["skipped_non_regular"] += 1
                continue
            if exemption.path_exempt(path, workspace):
                counts["skipped_exempt"] += 1
                continue
            try:
                if mode_bits not in (b"100644", b"100755"):
                    raise ValueError("unsupported staged mode")
                content = staged_text(workspace, oid, limit)
                if content is None:
                    counts["skipped_binary"] += 1
                    continue
                counts["files_scanned"] += 1
                result = assess(content, workspace, channel, exemption, mode, strict_paths=strict_paths)
            except GIT_ERRORS:
                result = unscannable(channel)
            if result["decision"] != "ALLOW":
                findings.append({**result, "entry": index})
                if result["exit"]:
                    audit_file(result)
    except GIT_ERRORS:
        findings.append(unscannable(channel))
    # Preserve the code of the strongest refusal, even if some other paths only warned.
    blocked = [row for row in findings if row["exit"]]
    selected = blocked or findings
    top = policy.worst([policy.Verdict(row["decision"], row["code"]) for row in selected]) if selected else (
        policy.Verdict("ALLOW", policy.CODE_ALLOW_NOOP))
    return {**counts, "findings": findings, "decision": top.decision, "code": top.code,
            "channel": channel, "span_count": sum(len(finding["spans"]) for finding in findings),
            "exit": 2 if blocked else 0}


def main():
    started = time.monotonic()
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="hook", required=True)
    staged = commands.add_parser("pre-commit")
    staged.add_argument("--strict-paths", action="store_true")
    message = commands.add_parser("commit-msg")
    message.add_argument("message_file")
    for command in (staged, message):
        command.add_argument("--audit", action="store_true")
        command.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args()
    except InvalidArguments:
        print("exfil-guard Git hook: invalid arguments", file=sys.stderr)
        return 2
    channel = "git-commit-message" if args.hook == "commit-msg" else "file-write"
    active = exfil_audit.enabled(args.audit)
    audit_failed = False

    def write_audit(row):
        nonlocal audit_failed
        receipt = exfil_audit.record(workspace, "git_" + args.hook.replace("-", "_"), "complete", row,
                                     started=started, active=active)
        audit_failed |= "error" in receipt
        return receipt

    try:
        workspace = os.fsdecode(git(None, "rev-parse", "--show-toplevel").rstrip(b"\r\n"))
    except GIT_ERRORS:
        result = unscannable(channel)
    else:
        exemption = redaction.load_exemption(workspace)
        mode = policy.load_mode(exfil_audit.trash_root(workspace))["mode"]
        result = (commit_message(args.message_file, workspace, exemption, mode) if args.hook == "commit-msg"
                  else pre_commit(workspace, exemption, mode, args.strict_paths, write_audit))
        result["audit"] = write_audit(result)
        if audit_failed:
            result["audit"] = {"enabled": True, "recorded": False, "error": "AUDIT_WRITE_FAILED"}
            print(exfil_audit.WRITE_WARNING, file=sys.stderr)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    elif result["exit"] or result["decision"] != "ALLOW":
        disposition = "refused" if result["exit"] else "warning"
        print(f"exfil-guard {args.hook} {disposition} [{result['code']}]", file=sys.stderr)
        for finding in result.get("findings", [result]):
            if "entry" in finding:
                print(f"  staged entry {finding['entry']} [{finding['code']}]", file=sys.stderr)
            for span in finding["spans"]:
                print(f"  {span['rule_id']} [{span['start']},{span['end']}] {span['code']}", file=sys.stderr)
    return result["exit"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("exfil-guard Git hook: could not inspect commit input", file=sys.stderr)
        sys.exit(2)

"""Bounded, read-only inspection of an explicit workspace export set."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import unicodedata

from . import TRASH_DIRNAME, exfil_audit, policy, redaction


DEFAULT_MAX_FILES = 5000
MAX_FILES = 20000
MAX_FILE_BYTES = redaction.DEFAULT_MAX_SCAN_BYTES
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PATHS_FILE_BYTES = 1024 * 1024
MAX_DEPTH = 64
MAX_FINDINGS = 200
MAX_SPANS = 128
BINARY_PREFIX = 8192
CONTROL_DIRECTORIES = {".git", TRASH_DIRNAME}


def supports_descriptor_scan():
    return (
        hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NONBLOCK")
        and {os.open, os.stat} <= os.supports_dir_fd
        and os.scandir in os.supports_fd
    )


class Unscannable(Exception):
    def __init__(self, reason, *, truncated=False):
        super().__init__(reason)
        self.reason = reason
        self.truncated = truncated


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _public_path(parts):
    if parts is None:
        return None
    relative = "/".join(parts) or "."
    if (len(relative) > 1024 or "\\" in relative
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in relative)
            or redaction.detect_secrets(relative)):
        return None
    return relative


class ExportScan:
    def __init__(self, workspace, channel, *, max_files, max_file_bytes, max_total_bytes, strict_paths):
        self.workspace_input = Path(os.path.abspath(workspace))
        self.workspace = self.workspace_input
        self.workspace_ready = False
        self.channel = channel
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.strict_paths = strict_paths
        self.mode = policy.MODE_NORMAL
        self.exemption = redaction.Exemption()
        self.counts = dict(files_scanned=0, files_blocked=0, files_warned=0, skipped_binary=0,
                           skipped_symlinks=0, skipped_exempt=0, skipped_control_directories=0)
        self.complete = True
        self.truncated = False
        self.findings = []
        self.spans = []
        self.span_count = self.exempted = self.bytes_read = self.files_seen = self.entries_seen = 0
        self.refusal = self.warning = None

    def _parts(self, path):
        candidate = Path(path)
        if not path or "\0" in path or ".." in candidate.parts:
            raise Unscannable("outside_workspace")
        if candidate.is_absolute():
            for root in (self.workspace_input, self.workspace):
                if candidate.is_relative_to(root):
                    return candidate.relative_to(root).parts
            raise Unscannable("outside_workspace")
        return candidate.parts

    @contextmanager
    def _directory(self, name, parent=None):
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def _parent(self, parent, parts):
        # Open each ancestor by descriptor so a changed path cannot redirect
        # a later open to content outside the workspace.
        if not parts:
            yield parent
            return
        name, *rest = parts
        if stat.S_ISLNK(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
            raise Unscannable("unsafe_symlink")
        with self._directory(name, parent) as descriptor, self._parent(descriptor, rest) as target:
            yield target

    @contextmanager
    def _regular(self, parent, name, observed):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(name, flags, dir_fd=parent), "rb", buffering=0) as stream:
            current = os.fstat(stream.fileno())
            if not stat.S_ISREG(current.st_mode) or _stamp(observed) != _stamp(current):
                raise Unscannable("file_changed")
            yield stream
            end = os.fstat(stream.fileno())
            linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _stamp(current) != _stamp(end) or _stamp(end) != _stamp(linked):
                raise Unscannable("file_changed")

    def _read_control(self, root, path, limit):
        parts = self._parts(path)
        if not parts or len(parts) > MAX_DEPTH:
            raise Unscannable("invalid_control_file")
        with self._parent(root, parts[:-1]) as parent:
            name = parts[-1]
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise Unscannable("invalid_control_file")
            with self._regular(parent, name, info) as stream:
                data = stream.read(info.st_size)
                if len(data) != info.st_size:
                    raise Unscannable("file_changed")
        return data.decode("utf-8")

    def _read_exemption(self, root, relative):
        try:
            return self._read_control(root, relative, MAX_PATHS_FILE_BYTES)
        except Unscannable:
            raise OSError("unreadable exemption") from None

    def _read_payload(self, parent, name, info):
        prefix_size = min(info.st_size, BINARY_PREFIX)
        remaining = self.max_total_bytes - self.bytes_read
        if prefix_size > remaining:
            raise Unscannable("total_byte_limit", truncated=True)
        with self._regular(parent, name, info) as stream:
            prefix = stream.read(prefix_size)
            self.bytes_read += len(prefix)
            if len(prefix) != prefix_size:
                raise Unscannable("file_changed")
            if b"\0" in prefix:
                return None
            if info.st_size > self.max_file_bytes:
                raise Unscannable("file_byte_limit", truncated=True)
            if info.st_size > remaining:
                raise Unscannable("total_byte_limit", truncated=True)
            rest = stream.read(info.st_size - len(prefix))
            self.bytes_read += len(rest)
            if len(prefix) + len(rest) != info.st_size:
                raise Unscannable("file_changed")
        return (prefix + rest).decode("utf-8")

    def _finding(self, parts, verdict, denied, reason, spans=(), verdicts=()):
        if denied:
            self.counts["files_blocked"] += 1
            self.refusal = policy.worst([self.refusal, verdict]) if self.refusal else verdict
        else:
            self.counts["files_warned"] += 1
            self.warning = policy.worst([self.warning, verdict]) if self.warning else verdict
        self.span_count += len(spans)
        available = MAX_SPANS - len(self.spans)
        rows = redaction.verdict_rows(spans[:available], verdicts[:available])
        self.spans.extend(rows)
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append({"path": _public_path(parts), "decision": verdict.decision,
                                  "code": verdict.code, "reason": reason, "spans": rows,
                                  "span_count": len(spans)})

    def _unscannable(self, parts, failure):
        self.complete = False
        self.truncated |= failure.truncated
        self._finding(parts, policy.Verdict("BLOCK", policy.CODE_BLOCK_OUTPUT_UNSCANNABLE), True, failure.reason)

    def _scan_file(self, parent, parts, info):
        name = parts[-1]
        if redaction.secret_store_name(name):
            source = redaction.SpanSpec(name, 0, len(name), redaction.RULE_SECRET_SOURCE, redaction.FAMILY_SECRET,
                                        redaction.CONFIDENCE_DETERMINISTIC, "env-var", self.channel.name,
                                        source_dump=True)
            verdict = policy.Verdict("BLOCK", policy.CODE_BLOCK_SECRET_SOURCE_DUMP)
            self._finding(parts, verdict, True, "secret_store_name", [source], [verdict])
            return
        text = self._read_payload(parent, name, info)
        if text is None:
            self.counts["skipped_binary"] += 1
            return
        scan = redaction.scan_text(text, channel=self.channel.name, workspace=str(self.workspace),
                                   max_bytes=self.max_file_bytes, exemption=self.exemption)
        if not scan.scanned:
            raise Unscannable("scan_failed")
        self.counts["files_scanned"] += 1
        self.exempted += scan.exempted
        top, denied, verdicts = policy.assess_unrewritten_spans(
            scan.spans, self.channel, self.mode, strict_paths=self.strict_paths)
        if top.decision != "ALLOW":
            self._finding(parts, top, denied, "text_policy", scan.spans, verdicts)

    def _skip_link(self, parts):
        # Resolve only metadata to classify the link. File bytes are opened
        # exclusively through the descriptor walk, which never follows links.
        try:
            destination = self.workspace.joinpath(*parts).resolve(strict=True)
            destination.relative_to(self.workspace)
        except (OSError, RuntimeError, ValueError):
            raise Unscannable("unsafe_symlink") from None
        if not (destination.is_dir() or destination.is_file()):
            raise Unscannable("unsafe_symlink")
        self.counts["skipped_symlinks"] += 1

    def _walk(self, parent, parts, *, direct, exempt=False):
        try:
            self.entries_seen += 1
            if len(parts) > MAX_DEPTH or self.entries_seen > max(64, self.max_files * 4):
                raise Unscannable("directory_limit", truncated=True)
            name = parts[-1] if parts else "."
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if not direct and name in CONTROL_DIRECTORIES:
                    self.counts["skipped_control_directories"] += 1
                    return
                exempt = exempt or self.exemption.path_exempt(str(self.workspace.joinpath(*parts)), str(self.workspace))
                with self._directory(name, parent) as descriptor, os.scandir(descriptor) as children:
                    for child in children:
                        self._walk(descriptor, (*parts, child.name), direct=False, exempt=exempt)
                        if self.truncated:
                            return
                return
            self.files_seen += 1
            if self.files_seen > self.max_files:
                raise Unscannable("file_count_limit", truncated=True)
            if stat.S_ISLNK(info.st_mode):
                self._skip_link(parts)
            elif not stat.S_ISREG(info.st_mode):
                raise Unscannable("non_regular_file")
            elif exempt or self.exemption.path_exempt(str(self.workspace.joinpath(*parts)), str(self.workspace)):
                self.counts["skipped_exempt"] += 1
            else:
                self._scan_file(parent, parts, info)
        except Unscannable as exc:
            self._unscannable(parts, exc)
        except UnicodeError:
            self._unscannable(parts, Unscannable("invalid_utf8"))
        except OSError:
            self._unscannable(parts, Unscannable("unreadable"))

    def scan(self, paths, paths_file):
        try:
            try:
                self.workspace = self.workspace_input.resolve(strict=True)
            except (OSError, ValueError, RuntimeError):
                raise Unscannable("unreadable_workspace") from None
            if not supports_descriptor_scan():
                raise Unscannable("unsupported_platform")
            if self.workspace == self.workspace.parent:
                raise Unscannable("outside_workspace")
            with self._directory(self.workspace) as root:
                self.workspace_ready = True
                if self.channel is None:
                    raise Unscannable("unknown_channel")
                if paths_file is not None:
                    try:
                        listing = self._read_control(root, paths_file, MAX_PATHS_FILE_BYTES)
                    except (OSError, UnicodeError, Unscannable):
                        raise Unscannable("invalid_paths_file") from None
                    paths = (*paths, *(line for line in listing.splitlines() if line))
                if not paths:
                    raise Unscannable("empty_targets")
                self.exemption = redaction.load_exemption(
                    str(self.workspace), read_text=lambda relative: self._read_exemption(root, relative))
                self.mode = policy.load_mode(exfil_audit.trash_root(self.workspace))["mode"]
                for index, candidate in enumerate(paths, 1):
                    if index > self.max_files:
                        raise Unscannable("file_count_limit", truncated=True)
                    parts = None
                    try:
                        parts = self._parts(candidate)
                        if len(parts) > MAX_DEPTH:
                            raise Unscannable("directory_limit", truncated=True)
                        with self._parent(root, parts[:-1]) as parent:
                            self._walk(parent, parts, direct=True)
                    except Unscannable as exc:
                        self._unscannable(parts, exc)
                    except OSError:
                        self._unscannable(parts, Unscannable("unreadable"))
                    if self.truncated:
                        break
        except Unscannable as exc:
            self._unscannable(None, exc)
        except OSError:
            self._unscannable(None, Unscannable("unreadable_workspace"))

    def result(self):
        top = self.refusal or self.warning or policy.Verdict("ALLOW", policy.CODE_ALLOW_NOOP)
        return {
            "schema": "exfil_export_scan/v1", "workspace": ".",
            "channel": self.channel.name if self.channel else None,
            "decision": top.decision, "code": top.code, "exit": 2 if self.refusal else 0,
            "complete": self.complete, "truncated": self.truncated, "counts": self.counts,
            "findings": self.findings, "omitted_findings": self.counts["files_blocked"] + self.counts["files_warned"] - len(self.findings),
            "span_count": self.span_count, "omitted_spans": self.span_count - len(self.spans),
            "size_bytes": self.bytes_read, "exemption": self.exemption.safe(), "exempted": self.exempted,
        }

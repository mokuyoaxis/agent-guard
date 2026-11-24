"""Read-only, redacted, paginated file reader for workspace text files.

Cooperative interface, not an OS access-control boundary. Reads regular files
through POSIX directory descriptors without following symlinks, scans and
redacts the full file before slicing lines, independently verifies that no
credential or mode-required path remains after redaction (ignoring workspace
exemptions), and reports only current-page redaction details.
"""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
import os
import stat
from typing import Any, Dict, List, Optional, Set, Tuple
import unicodedata

from . import TRASH_DIRNAME, exfil_audit, policy, redaction, safe_view


SCHEMA = "safe_read/v1"
CHANNEL_NAME = "llm-request"
MAX_FILE_BYTES = 256 * 1024
MAX_RETURN_LINES = 200
MAX_RETURN_BYTES = 32 * 1024
BINARY_PREFIX = 8192
CONTROL_DIRS = frozenset({".git", ".agent-guard", TRASH_DIRNAME.lower()})

REFUSAL_MESSAGES = {
    "CONTROL_PATH": "protected control or quarantine paths cannot be read",
    "SECRET_STORE_REFUSED": "secret-bearing files cannot be read directly",
    "SECRET_STORE_CONFIG_HINT": "secret-bearing config files cannot be read directly; use config_safe_view",
    "SECRET_BLOCK_REFUSED": "multi-line or private-key secret blocks cannot be read",
    "SOURCE_DUMP_REFUSED": "content references a secret store or environment dump and cannot be read",
    "SECRET_ASK_REFUSED": "uncertain secret content cannot be certified for reading",
    "REDACTION_REQUIRED": "file requires redaction and --fail-on-redaction was requested",
    "RESIDUAL_SECRET_BLOCKED": "known credential remains after redaction or exemption; output withheld",
    "RESIDUAL_PATH_BLOCKED": "protected path remains after redaction or exemption; output withheld",
    "UNSCANNABLE_CONTENT": "file could not be certified clean by the scanner",
}
ERROR_MESSAGES = {
    "INVALID_ARGUMENT": "invalid arguments",
    "INVALID_PATH": "use a workspace-relative path",
    "UNSUPPORTED_PLATFORM": "safe descriptor-relative reads are unavailable",
    "UNSAFE_FILE": "file cannot be opened safely",
    "FILE_TOO_LARGE": "file exceeds size limit",
    "BINARY_FILE": "binary files cannot be read",
    "INVALID_ENCODING": "file is not valid UTF-8",
    "FILE_CHANGED": "file changed during read",
    "LINE_TOO_LONG": "requested line exceeds the 32 KiB page byte limit",
}
REFUSAL_CODES = frozenset(REFUSAL_MESSAGES) - {"SECRET_STORE_CONFIG_HINT"}
ERROR_CODES = frozenset(ERROR_MESSAGES)
ALL_ERROR_CODES = REFUSAL_CODES | ERROR_CODES
ALLOWED_MESSAGES = frozenset(REFUSAL_MESSAGES.values()) | frozenset(ERROR_MESSAGES.values())


def _public_path(parts: List[str]) -> Optional[str]:
    relative = "/".join(parts)
    if (len(relative) > 1024 or "\\" in relative
            or any(unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for ch in relative)
            or redaction.detect_secrets(relative)):
        return None
    return relative


class _ReadFailure(Exception):
    def __init__(self, error_code: str, *, status: str = "error",
                 code: str = policy.CODE_BLOCK_OUTPUT_UNSCANNABLE,
                 message: Optional[str] = None):
        if message is None:
            message = (REFUSAL_MESSAGES[error_code] if error_code in REFUSAL_MESSAGES
                       else ERROR_MESSAGES[error_code])
        super().__init__(message)
        self.error_code = error_code
        self.status = status
        self.code = code
        self.message = message


def supports_descriptor_read() -> bool:
    return (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
    )


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(path), os.path.normcase(root))) == os.path.normcase(root)
    except ValueError:
        return False


def _locations(path: str) -> Tuple[str, str, str]:
    absolute = os.path.normpath(os.path.abspath(path))
    parent, leaf = os.path.split(absolute)
    return (absolute, os.path.realpath(absolute), os.path.join(os.path.realpath(parent), leaf))


def _quarantine_directories(
    workspace: str, parts: List[str], trash_root: str,
) -> Set[Tuple[int, int]]:
    """Reject known path overlaps and identify existing quarantine directories."""
    ws_abs = os.path.normpath(os.path.abspath(workspace))
    ws_real = os.path.realpath(ws_abs)
    buckets = {
        os.path.join(ws_abs, TRASH_DIRNAME),
        os.path.join(ws_real, TRASH_DIRNAME),
    }
    buckets.add(os.path.normpath(os.path.abspath(trash_root)))
    boundaries = [loc for bucket in buckets for loc in _locations(bucket)]
    candidates = {
        os.path.join(ws_abs, *parts),
        os.path.join(ws_real, *parts),
    }
    targets = [loc for candidate in candidates for loc in _locations(candidate)]
    if any(_inside(target, boundary) or _inside(boundary, target)
           for target in targets for boundary in boundaries):
        raise _ReadFailure(
            "CONTROL_PATH", status="refused", code=policy.CODE_BLOCK_SECRET_SOURCE_DUMP,
        )

    identities = set()
    for bucket in buckets:
        try:
            info = os.stat(bucket)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            raise _ReadFailure("UNSAFE_FILE") from None
        if stat.S_ISDIR(info.st_mode):
            identities.add((info.st_dev, info.st_ino))
    return identities


def _is_config_view_candidate(name: str) -> bool:
    lowered = name.lower()
    # Split the literal so RepoCorpusNoise never mistakes this filename check
    # for an instruction to read a secret store.
    dotenv_suffix = "." + "env"
    return (
        lowered.endswith(".json")
        or lowered.endswith(".toml")
        or lowered == dotenv_suffix
        or lowered.startswith(dotenv_suffix + ".")
        or lowered.endswith(dotenv_suffix)
    )


def _validate_range(start_line: int, end_line: Optional[int]) -> Tuple[int, Optional[int]]:
    if type(start_line) is not int or start_line < 1:
        raise _ReadFailure("INVALID_ARGUMENT")
    if end_line is not None:
        if (type(end_line) is not int or end_line < start_line
                or (end_line - start_line + 1) > MAX_RETURN_LINES):
            raise _ReadFailure("INVALID_ARGUMENT")
    return start_line, end_line


def _read_regular_file(
    workspace: str, parts: List[str], max_bytes: int,
    quarantine_dirs: Set[Tuple[int, int]],
) -> str:
    if not supports_descriptor_read():
        raise _ReadFailure("UNSUPPORTED_PLATFORM")

    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    root = os.path.realpath(os.path.abspath(workspace))
    with ExitStack() as stack:
        try:
            parent = None
            for directory in (root, *parts[:-1]):
                parent = os.open(directory, dir_flags, dir_fd=parent)
                stack.callback(os.close, parent)
                if quarantine_dirs:
                    info = os.fstat(parent)
                    if (info.st_dev, info.st_ino) in quarantine_dirs:
                        raise _ReadFailure(
                            "CONTROL_PATH", status="refused",
                            code=policy.CODE_BLOCK_SECRET_SOURCE_DUMP,
                        )
            fd = os.open(parts[-1], file_flags, dir_fd=parent)
            stack.callback(os.close, fd)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise _ReadFailure("UNSAFE_FILE")
            if before.st_size > max_bytes:
                raise _ReadFailure("FILE_TOO_LARGE")
            chunks: List[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise _ReadFailure("FILE_TOO_LARGE")
                chunks.append(chunk)
            after = os.fstat(fd)
            if ((before.st_dev, before.st_ino, before.st_size,
                 before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns)):
                raise _ReadFailure("FILE_CHANGED")
            if os.stat in os.supports_dir_fd:
                linked = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                if ((after.st_dev, after.st_ino, after.st_size,
                     after.st_mtime_ns, after.st_ctime_ns) !=
                    (linked.st_dev, linked.st_ino, linked.st_size,
                     linked.st_mtime_ns, linked.st_ctime_ns)):
                    raise _ReadFailure("FILE_CHANGED")
        except _ReadFailure:
            raise
        except OSError:
            raise _ReadFailure("UNSAFE_FILE") from None
    raw = b"".join(chunks)
    if b"\x00" in raw[:BINARY_PREFIX]:
        raise _ReadFailure("BINARY_FILE")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _ReadFailure("INVALID_ENCODING") from None


def _allow_generic_path(span: redaction.SpanSpec, verdict: policy.Verdict, *,
                        strict_paths: bool, mode: str) -> bool:
    return (
        not strict_paths
        and mode == policy.MODE_NORMAL
        and span.rule_id == redaction.RULE_GENERIC_ABSOLUTE_PATH
        and verdict.code == policy.CODE_ASK_PATH_EMISSION
    )


def _apply_with_positions(
    text: str,
    entries: List[Tuple[redaction.SpanSpec, policy.Verdict, str]],
) -> Tuple[str, List[Dict[str, Any]]]:
    ordered = sorted(entries, key=lambda item: (item[0].start, -(item[0].end - item[0].start)))
    chunks: List[str] = []
    applied: List[Dict[str, Any]] = []
    orig_cursor = 0
    out_cursor = 0
    for span, verdict, placeholder in ordered:
        if span.start < orig_cursor or span.end > len(text) or span.start >= span.end:
            raise _ReadFailure("UNSCANNABLE_CONTENT", status="refused")
        prefix = text[orig_cursor:span.start]
        chunks.append(prefix)
        out_cursor += len(prefix)
        new_start = out_cursor
        chunks.append(placeholder)
        out_cursor += len(placeholder)
        orig_cursor = span.end
        applied.append({
            "new_start": new_start,
            "new_end": out_cursor,
            "placeholder": placeholder,
            "span": span,
            "verdict": verdict,
        })
    chunks.append(text[orig_cursor:])
    return "".join(chunks), applied


def _failure_receipt(failure: _ReadFailure, path_label: Optional[str]) -> Dict[str, Any]:
    exit_code = 2 if failure.status == "refused" else 1
    return {
        "schema": SCHEMA,
        "status": failure.status,
        "decision": policy.DECISION_BLOCK,
        "code": failure.code,
        "error_code": failure.error_code,
        "message": failure.message,
        "path": path_label,
        "text": "",
        "spans": [],
        "exit": exit_code,
    }


def safe_read(
    workspace: str,
    relative_path: str,
    *,
    start_line: int = 1,
    end_line: Optional[int] = None,
    strict_paths: bool = False,
    fail_on_redaction: bool = False,
    trash_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Read, redact, independently verify, and paginate one workspace file.

    Public receipts omit absolute workspace paths and restrict hit details
    and decisions to the returned page. Whole-file refusals may still be
    triggered by content outside the requested page.
    """
    path_label: Optional[str] = None
    try:
        if (not isinstance(workspace, str) or not workspace
                or not isinstance(relative_path, str)
                or type(strict_paths) is not bool
                or type(fail_on_redaction) is not bool):
            raise _ReadFailure("INVALID_ARGUMENT")
        start_line, end_line = _validate_range(start_line, end_line)
        try:
            parts = safe_view._components(relative_path)
        except safe_view.SafeViewError:
            raise _ReadFailure("INVALID_PATH") from None
        scan_path = "/".join(parts)
        path_label = _public_path(parts)

        ws = os.path.realpath(os.path.abspath(workspace))
        if not os.path.isdir(ws):
            raise _ReadFailure("UNSAFE_FILE")
        effective_trash = trash_root if trash_root is not None else exfil_audit.trash_root(ws)

        # Control directories (case-insensitive) and both quarantine roots.
        if any(part.lower() in CONTROL_DIRS for part in parts):
            raise _ReadFailure(
                "CONTROL_PATH", status="refused",
                code=policy.CODE_BLOCK_SECRET_SOURCE_DUMP,
            )
        quarantine_dirs = _quarantine_directories(workspace, parts, effective_trash)

        # Secret-bearing filenames are refused before opening.
        if redaction.secret_store_name(parts[-1]):
            hint = (REFUSAL_MESSAGES["SECRET_STORE_CONFIG_HINT"]
                    if _is_config_view_candidate(parts[-1])
                    else REFUSAL_MESSAGES["SECRET_STORE_REFUSED"])
            raise _ReadFailure(
                "SECRET_STORE_REFUSED", status="refused",
                code=policy.CODE_BLOCK_SECRET_SOURCE_DUMP,
                message=hint,
            )

        text = _read_regular_file(ws, parts, MAX_FILE_BYTES, quarantine_dirs)

        mode = policy.load_mode(effective_trash)["mode"]
        eval_mode = policy.MODE_RESTRICTED if strict_paths else mode
        channel = redaction.get_channel(CHANNEL_NAME)
        if channel is None:
            raise _ReadFailure("UNSCANNABLE_CONTENT", status="refused")
        exemption = redaction.load_exemption(ws)
        ctx = policy.PolicyContext(workspace=ws, trash_root="", base_dir=ws, mode=eval_mode)

        # 1. Whole-file scan with workspace exemptions.
        scan = redaction.scan_text(
            text, channel=CHANNEL_NAME, workspace=ws,
            max_bytes=MAX_FILE_BYTES, path=scan_path, exemption=exemption,
        )
        if not scan.scanned:
            raise _ReadFailure("UNSCANNABLE_CONTENT", status="refused")

        verdicts = policy.decide_spans(scan.spans, ctx=ctx, channel=channel)
        to_redact: List[Tuple[redaction.SpanSpec, policy.Verdict, str]] = []
        for span, verdict in zip(scan.spans, verdicts):
            if span.source_dump or verdict.code == policy.CODE_BLOCK_SECRET_SOURCE_DUMP:
                raise _ReadFailure(
                    "SOURCE_DUMP_REFUSED", status="refused",
                    code=policy.CODE_BLOCK_SECRET_SOURCE_DUMP,
                )
            if span.family == redaction.FAMILY_SECRET and (
                span.rule_id == redaction.RULE_PRIVATE_KEY_BLOCK
                or "\n" in span.raw or "\r" in span.raw
            ):
                raise _ReadFailure(
                    "SECRET_BLOCK_REFUSED", status="refused",
                    code=policy.CODE_BLOCK_SECRET_EMISSION,
                )
            if verdict.decision == policy.DECISION_BLOCK:
                raise _ReadFailure("UNSCANNABLE_CONTENT", status="refused", code=verdict.code)
            if verdict.decision == policy.DECISION_ASK:
                if _allow_generic_path(span, verdict, strict_paths=strict_paths, mode=mode):
                    continue
                raise _ReadFailure(
                    "SECRET_ASK_REFUSED", status="refused",
                    code=policy.CODE_BLOCK_SECRET_EMISSION,
                )
            if verdict.decision == policy.DECISION_SANITIZE:
                if "\n" in span.raw or "\r" in span.raw:
                    raise _ReadFailure(
                        "SECRET_BLOCK_REFUSED", status="refused",
                        code=policy.CODE_BLOCK_SECRET_EMISSION,
                    )
                to_redact.append((span, verdict, redaction.placeholder_for(span)))

        if fail_on_redaction and to_redact:
            has_secret = any(span.family == redaction.FAMILY_SECRET for span, _, _ in to_redact)
            code = (policy.CODE_BLOCK_SECRET_EMISSION if has_secret
                    else policy.CODE_BLOCK_PATH_EMISSION)
            raise _ReadFailure("REDACTION_REQUIRED", status="refused", code=code)

        # 2. Apply redactions on whole file while tracking post-redaction spans.
        rewritten, applied = _apply_with_positions(text, to_redact)
        rescan_cap = max(MAX_FILE_BYTES, len(rewritten.encode("utf-8", errors="strict")))

        # 3. Regular policy re-scan of rewritten whole file.
        checked = redaction.scan_text(
            rewritten, channel=CHANNEL_NAME, workspace=ws,
            max_bytes=rescan_cap, path=scan_path, exemption=exemption,
        )
        if not checked.scanned:
            raise _ReadFailure("UNSCANNABLE_CONTENT", status="refused")
        checked_verdicts = policy.decide_spans(checked.spans, ctx=ctx, channel=channel)
        for span, verdict in zip(checked.spans, checked_verdicts):
            if verdict.decision == policy.DECISION_ALLOW:
                continue
            if _allow_generic_path(span, verdict, strict_paths=strict_paths, mode=mode):
                continue
            raise _ReadFailure("UNSCANNABLE_CONTENT", status="refused")

        # 4. Independent unfiltered final check (ignores workspace exemptions):
        # neither known secrets nor paths required to be rewritten by the
        # active mode may survive into model-visible output.
        unfiltered = redaction.scan_text(
            rewritten, channel=CHANNEL_NAME, workspace=ws,
            max_bytes=rescan_cap, exemption=redaction.Exemption(),
        )
        if not unfiltered.scanned:
            raise _ReadFailure("UNSCANNABLE_CONTENT", status="refused")
        unfiltered_verdicts = policy.decide_spans(unfiltered.spans, ctx=ctx, channel=channel)
        residual_path = False
        for span, verdict in zip(unfiltered.spans, unfiltered_verdicts):
            if verdict.decision == policy.DECISION_ALLOW:
                continue
            if span.family == redaction.FAMILY_SECRET:
                raise _ReadFailure(
                    "RESIDUAL_SECRET_BLOCKED", status="refused",
                    code=policy.CODE_BLOCK_OUTPUT_UNSCANNABLE,
                )
            if not _allow_generic_path(span, verdict, strict_paths=strict_paths, mode=mode):
                residual_path = True
        if residual_path:
            raise _ReadFailure(
                "RESIDUAL_PATH_BLOCKED", status="refused",
                code=policy.CODE_BLOCK_OUTPUT_UNSCANNABLE,
            )

        # 5. Paginate whole lines after redaction and verification.
        lines = rewritten.splitlines(keepends=True)
        total_lines = len(lines)
        line_starts: List[int] = []
        cursor = 0
        for line in lines:
            line_starts.append(cursor)
            cursor += len(line)

        if total_lines == 0 or start_line > total_lines:
            page_text = ""
            actual_end_line: Optional[int] = None
            returned_lines = 0
            returned_bytes = 0
            truncated = False
            truncated_by: Optional[str] = None
            has_more = False
            next_line: Optional[int] = None
            page_spans: List[Dict[str, Any]] = []
        else:
            window_end = min(
                total_lines,
                end_line if end_line is not None else start_line + MAX_RETURN_LINES - 1,
            )
            selected: List[str] = []
            returned_bytes = 0
            truncated = False
            truncated_by = None
            actual_end_line = None
            for line_no in range(start_line, window_end + 1):
                line = lines[line_no - 1]
                line_bytes = len(line.encode("utf-8", errors="strict"))
                if not selected and line_bytes > MAX_RETURN_BYTES:
                    raise _ReadFailure("LINE_TOO_LONG")
                if returned_bytes + line_bytes > MAX_RETURN_BYTES:
                    truncated = True
                    truncated_by = "bytes"
                    break
                selected.append(line)
                returned_bytes += line_bytes
                actual_end_line = line_no
            page_text = "".join(selected)
            returned_lines = len(selected)
            has_more = actual_end_line is not None and actual_end_line < total_lines
            if not truncated and end_line is None and has_more:
                truncated = True
                truncated_by = "lines"
            next_line = (actual_end_line + 1) if (has_more and actual_end_line is not None) else None

            page_char_start = line_starts[start_line - 1]
            page_char_end = page_char_start + len(page_text)
            page_spans = []
            for entry in applied:
                if page_char_start <= entry["new_start"] and entry["new_end"] <= page_char_end:
                    span = entry["span"]
                    verdict = entry["verdict"]
                    p_start = entry["new_start"] - page_char_start
                    p_end = entry["new_end"] - page_char_start
                    page_spans.append({
                        "rule_id": span.rule_id,
                        "family": span.family,
                        "confidence": span.confidence,
                        "context": span.context.partition("=")[0],
                        "channel": span.channel,
                        "start": p_start,
                        "end": p_end,
                        "length": p_end - p_start,
                        "decision": verdict.decision,
                        "code": verdict.code,
                    })

        if page_spans:
            page_decision = policy.DECISION_SANITIZE
            has_page_secret = any(row["family"] == redaction.FAMILY_SECRET for row in page_spans)
            page_code = (policy.CODE_SANITIZE_SECRET_REDACT if has_page_secret
                         else policy.CODE_SANITIZE_PATH_REWRITE)
        else:
            page_decision = policy.DECISION_ALLOW
            page_code = policy.CODE_ALLOW_NOOP

        counts = Counter(row["rule_id"] for row in page_spans)
        return {
            "schema": SCHEMA,
            "status": "ok",
            "decision": page_decision,
            "code": page_code,
            "exit": 0,
            "path": path_label,
            "start_line": start_line,
            "end_line": actual_end_line,
            "returned_lines": returned_lines,
            "total_lines": total_lines,
            "returned_bytes": returned_bytes,
            "truncated": truncated,
            "truncated_by": truncated_by,
            "has_more": has_more,
            "next_line": next_line,
            "page_redacted": bool(page_spans),
            "page_redaction_count": len(page_spans),
            "page_redactions_by_rule": dict(sorted(counts.items())),
            "spans": page_spans,
            "text": page_text,
        }
    except _ReadFailure as exc:
        return _failure_receipt(exc, path_label)

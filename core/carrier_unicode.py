"""Classify literal Unicode formatting in persistent instruction carriers."""
from __future__ import annotations

from functools import lru_cache
import gzip
from pathlib import Path
import re
import unicodedata


SEQUENCES = Path(__file__).resolve().parents[1] / "assets/workspace-doctor-unicode.txt.gz"
TAGS = r"\U000E0000-\U000E007F"
VARIANTS = r"\u180B-\u180D\u180F\uFE00-\uFE0F\U000E0100-\U000E01EF"
FORMATTING = r"\u00AD\u034F\u180E\u200B-\u200D\u2060-\u2064\uFEFF"
BIDI = r"\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069"
MARKERS = re.compile(f"[{TAGS}{VARIANTS}{FORMATTING}{BIDI}]")
RUNS = re.compile(
    f"(?P<tag>[{TAGS}]+)|(?P<variant>[{VARIANTS}]+)|"
    f"(?P<format>[{FORMATTING}]+)|(?P<bidi>[{BIDI}])"
)
ISOLATES = re.compile(r"[\u2066-\u2069]")


@lru_cache(maxsize=1)
def _tables():
    emoji, variants = set(), set()
    lengths, joining = {}, {}
    with gzip.open(SEQUENCES, "rt", encoding="ascii") as data:
        for line in data:
            if line.startswith("#"):
                continue
            kind, *points = line.split()
            if kind == "J":
                first, last = (int(point, 16) for point in points[:2])
                joining.update((chr(point), points[2]) for point in range(first, last + 1))
                continue
            sequence = "".join(chr(int(point, 16)) for point in points)
            if kind == "V":
                variants.add(sequence)
            else:
                emoji.add(sequence)
                lengths.setdefault(sequence[0], set()).add(len(sequence))
    return emoji, {ch: sorted(sizes, reverse=True) for ch, sizes in lengths.items()}, variants, joining


def _emoji_positions(line, emoji, lengths):
    accepted = set()
    start = 0
    while start < len(line):
        for length in lengths.get(line[start], ()):
            end = start + length
            if end <= len(line) and line[start:end] in emoji:
                accepted.update(range(start, end))
                start = end
                break
        else:
            start += 1
    return accepted


def _unpaired_isolates(line):
    starts, unmatched = [], set()
    for match in ISOLATES.finditer(line):
        if match[0] != "\u2069":
            starts.append(match.start())
        elif starts:
            starts.pop()
        else:
            unmatched.add(match.start())
    return unmatched.union(starts)


def _contextual_joiner(line, index, joining):
    if not 0 < index < len(line) - 1:
        return False
    if unicodedata.combining(line[index - 1]) == 9 and line[index + 1].isalpha():
        return True
    left, right = index - 1, index + 1
    while left >= 0 and joining.get(line[left]) == "T":
        left -= 1
    while right < len(line) and joining.get(line[right]) == "T":
        right += 1
    return (left >= 0 and right < len(line)
            and joining.get(line[left]) in {"D", "L"} and joining.get(line[right]) in {"D", "R"})


def _unaccepted_runs(start, stop, accepted):
    run_start = None
    for index in range(start, stop):
        if index in accepted:
            if run_start is not None:
                yield run_start, index - run_start
                run_start = None
        elif run_start is None:
            run_start = index
    if run_start is not None:
        yield run_start, stop - run_start


def _is_cjk_ideograph(char):
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FA1F
        or 0x30000 <= code <= 0x3347F
    )


def _line_findings(line, first_line):
    emoji, lengths, variants, joining = _tables()
    accepted = _emoji_positions(line, emoji, lengths)
    unpaired = _unpaired_isolates(line)
    for match in RUNS.finditer(line):
        start, stop = match.span()
        count = stop - start
        kind = match.lastgroup
        if kind == "tag":
            for index, size in _unaccepted_runs(start, stop, accepted):
                yield index, size, "FAIL", "UNICODE_TAG_SMUGGLING"
        elif kind == "variant":
            if count > 1:
                yield start, count, "FAIL", "UNICODE_VS_SMUGGLING"
            elif start not in accepted and (not start or line[start - 1:stop] not in variants):
                char = line[start]
                if "\U000E0100" <= char <= "\U000E01EF" and (
                        not start or not _is_cjk_ideograph(line[start - 1])):
                    yield start, count, "FAIL", "UNICODE_VS_SMUGGLING"
                else:
                    yield start, count, "WARN", "UNICODE_VARIATION_UNVERIFIED"
        elif kind == "format":
            for index in range(start, stop):
                char = line[index]
                if (char == "\uFEFF" and index == 0 and first_line
                        or char in "\u200C\u200D" and _contextual_joiner(line, index, joining)):
                    accepted.add(index)
            for index, size in _unaccepted_runs(start, stop, accepted):
                if size > 1:
                    yield index, size, "FAIL", "UNICODE_INVISIBLE_RUN"
                else:
                    yield index, size, "WARN", "UNICODE_INVISIBLE_FORMAT"
        else:
            char = line[start]
            if "\u202A" <= char <= "\u202E":
                yield start, 1, "FAIL", "UNICODE_BIDI_OVERRIDE"
            elif start in unpaired:
                yield start, 1, "FAIL", "UNICODE_BIDI_ISOLATE_UNPAIRED"
            elif "\u2066" <= char <= "\u2069":
                yield start, 1, "WARN", "UNICODE_BIDI_ISOLATE"
            else:
                yield start, 1, "WARN", "UNICODE_BIDI_MARK"


def findings(value):
    """Yield locations and fixed codes without retaining the matched characters."""
    if not MARKERS.search(value):
        return
    for number, line in enumerate(value.split("\n"), 1):
        if MARKERS.search(line):
            for index, count, severity, code in _line_findings(line, number == 1):
                yield {"line": number, "column": index + 1, "count": count,
                       "severity": severity, "code": code}

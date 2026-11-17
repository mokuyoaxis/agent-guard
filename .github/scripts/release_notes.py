#!/usr/bin/env python3
"""Render source-relative Markdown links for a tagged GitHub Release."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from release_channel import release_channel


ROOT = Path(__file__).resolve().parents[2]
INLINE = re.compile(r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^\s)]+)([^\n)]*\))")
REFERENCE = re.compile(r"^(\s*\[[^\]]+\]:\s*)(<[^>\n]+>|\S+)(.*)$")
CODE = re.compile(r"(`+).*?\1(?!`)")


def render_notes(notes: Path, root: Path, repository: str, version: str) -> str:
    release_channel(version)
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repository, re.ASCII) is None:
        raise ValueError("repository must be owner/name")
    root, notes = root.resolve(), notes.resolve()
    source = notes.read_text(encoding="utf-8")
    tracked = set(subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root).decode().split("\0"))

    def destination(token, image=False):
        angled = token.startswith("<")
        target = token[1:-1] if angled else token
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            return token
        path = unquote(url.path)
        local = ((root / path.lstrip("/")) if path.startswith("/")
                 else (notes.parent / path)).resolve()
        if not local.is_relative_to(root):
            raise ValueError(f"release link leaves the repository: {target}")
        relative = local.relative_to(root).as_posix()
        exists_in_tag = (relative in tracked if local.is_file() else
                         local.is_dir() and any(p.startswith(relative + "/") for p in tracked))
        if not exists_in_tag:
            raise ValueError(f"release link has no tracked target: {target}")
        prefix = (f"https://raw.githubusercontent.com/{repository}/{quote(version, safe='')}/"
                  if image else f"https://github.com/{repository}/blob/{quote(version, safe='')}/")
        rendered = prefix + quote(relative, safe="/")
        rendered = urlunsplit((*urlsplit(rendered)[:3], url.query, url.fragment))
        return "<" + rendered + ">" if angled else rendered

    def inline(text):
        parts = []
        cursor = 0
        while cursor < len(text):
            link = INLINE.search(text, cursor)
            code = CODE.search(text, cursor)
            if code is not None and (link is None or code.start() < link.start()):
                parts.extend((text[cursor:code.start()], code[0]))
                cursor = code.end()
            elif link is not None:
                parts.extend((text[cursor:link.start()], link[1],
                              destination(link[2], link[1].startswith("!")), link[3]))
                cursor = link.end()
            else:
                parts.append(text[cursor:])
                break
        return "".join(parts)

    result = []
    fence = None
    for line in source.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            result.append(line)
            continue
        if fence is None:
            reference = REFERENCE.match(line.rstrip("\r\n"))
            if reference:
                ending = line[len(line.rstrip("\r\n")):]
                line = reference[1] + destination(reference[2]) + reference[3] + ending
            else:
                line = inline(line)
        result.append(line)
    return "".join(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    notes = args.notes or args.root / "docs/releases" / f"release-notes-{args.version}.md"
    if args.output.resolve() == notes.resolve():
        parser.error("output must differ from the source release notes")
    try:
        rendered = render_notes(notes, args.root, args.repository, args.version)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"release notes: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Rendered release notes for {args.version}; tracked link targets checked")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fresh private synthetic fixtures for zero-model native read validation.

This is not a prompt-injection trial or an L1/L2 report. Generated values are
synthetic, never live authentication values. The private control file is a
sibling of the fixture directory, outside the agent-visible fixture tree.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path


def prepare(directory):
    root = Path(directory)
    control_path = root.with_name(root.name + ".control.json")
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    token = "ghp_" + secrets.token_hex(18)
    # Hex is also in the Base64 alphabet; it avoids accidental placeholder
    # substrings such as repeated x's while retaining an opaque synthetic body.
    key_body = secrets.token_hex(32)
    label = "PRIVATE" + " KEY"
    key = "-----BEGIN " + label + "-----\n" + key_body + "\n-----END " + label + "-----"
    ordinary = "AG_CANARY_" + secrets.token_hex(16)
    benign = "# 普通配置：保留中文和行号\nPORT=8080\nLOG_LEVEL=info\n"
    contents = {
        "benign.ini": benign,
        "redaction.ini": benign + "SERVICE_TOKEN=" + ordinary + "\nGITHUB_TOKEN=" + token +
                         "\n" + key + "\nFEATURE_ENABLED=true\n",
    }
    control = {"schema": 1, "fixture": "dsh-read-redaction-v1",
               "protected_values": [token, key_body], "ordinary_marker": ordinary,
               "files": list(contents)}
    for filename, content in contents.items():
        fd = os.open(str(root / filename), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    fd = os.open(str(control_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(control, stream, ensure_ascii=False)
        stream.write("\n")
    return {"fixture": control["fixture"], "files": len(contents), "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.directory)
    except Exception:
        parser.exit(1, "read fixture refused; raw diagnostics suppressed\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

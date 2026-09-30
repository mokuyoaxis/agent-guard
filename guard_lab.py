#!/usr/bin/env python3
"""Offline synthetic honeytoken lab for Agent Guard."""

from __future__ import annotations

import sys

from core.lab import cli_main, observer_cli_main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_observe":
        raise SystemExit(observer_cli_main(sys.argv[2:]))
    raise SystemExit(cli_main(sys.argv[1:]))

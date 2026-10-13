#!/usr/bin/env python3
"""Run the existing four zero-model Lab controls from this package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.lab import LabError, run_case  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="new output directory with an existing parent")
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    output.mkdir(mode=0o700)
    controls = []
    for case in ("clean", "mock-positive", "mock-injection", "snapshot-positive"):
        try:
            report = run_case(case, output / case)["report"]
        except LabError as error:
            controls.append({"case_id": case, "result": "INCONCLUSIVE",
                             "reason": str(error)})
            break
        controls.append({"case_id": case, "result": report["result"],
                         "criterion": report["criterion"]["status"],
                         "observer_health": report["observer"]["health"],
                         "security_outcome": report["security_outcome"]})
        if (report["result"] != "COMPLETE" or report["criterion"]["status"] != "PASS"
                or report["observer"]["health"] != "HEALTHY"):
            break
    complete = len(controls) == 4 and all(
        c["result"] == "COMPLETE" and c["criterion"] == "PASS"
        and c["observer_health"] == "HEALTHY"
        and c["security_outcome"] == "CALIBRATION_ONLY" for c in controls)
    summary = {"schema_version": 1, "model_usage": "NONE",
               "scope": "EXISTING_L0_INSTRUMENT_CONTROLS",
               "result": "COMPLETE" if complete else "INCONCLUSIVE", "controls": controls}
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    with (output / "summary.json").open("x", encoding="utf-8") as stream:
        stream.write(text)
    print(text, end="")
    return 0 if complete else 4


if __name__ == "__main__":
    raise SystemExit(main())

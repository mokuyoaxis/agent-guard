"""Test-only barrier after a real CLI mutation and before its MCP reply.

The barrier does not fabricate a result or interrupt a partially moved tree.
It makes the committed-but-unacknowledged window deterministic for tests.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.mcp.operations import Operations
from adapters.mcp.server import serve


class ReplyBarrier:
    def __init__(self, workspace, control):
        self.operations = Operations(workspace)
        self.control = Path(control)

    def call(self, name, arguments):
        result = self.operations.call(name, arguments)
        if name == "safe_delete" and result["ok"] and not arguments.get("dry_run", False):
            # Operations.call has returned from the actual subprocess, so its
            # relocation, accounting and audit have already happened.
            receipt = {"stage": "mutation_complete_before_reply",
                       "txid": result["result"]["txid"]}
            with (self.control / "committed.json").open("x") as stream:
                json.dump(receipt, stream)
            (self.control / "ready").touch()
            deadline = time.monotonic() + 30
            while not (self.control / "release").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("test reply barrier was not released")
                time.sleep(0.01)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    asyncio.run(serve(ReplyBarrier(args.workspace, args.control)))

"""Shared fixtures for agent-guard tests."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest


def git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


class RepoFixture(unittest.TestCase):
    """A throwaway git repository with a committed baseline."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="agent-guard-test-")
        if git_available():
            git = lambda *a: subprocess.run(  # noqa: E731
                ["git", "-C", self.root, *a], capture_output=True, text=True,
                check=True)
            git("init", "-q")
            git("config", "user.email", "test@local")
            git("config", "user.name", "test")
            git("config", "commit.gpgsign", "false")
            # Baseline commits must not leave background Git maintenance
            # racing tests that assert the complete fixture is read-only.
            git("config", "maintenance.auto", "false")
            git("config", "gc.auto", "0")
            with open(os.path.join(self.root, ".gitignore"), "w") as fh:
                fh.write("node_modules/\n*.log\n")
            os.makedirs(os.path.join(self.root, "src"))
            with open(os.path.join(self.root, "src", "main.py"), "w") as fh:
                fh.write("print('base')\n")
            git("add", "-A")
            git("commit", "-qm", "baseline")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # helpers -------------------------------------------------------------

    def write(self, rel: str, content: str = "x") -> str:
        path = os.path.join(self.root, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
        return path

    def ctx_kwargs(self, **overrides):
        from core.classifier import discover_workspace
        kwargs = dict(
            base_dir=self.root,
            workspace=discover_workspace(self.root),
            trash_root=os.path.join(self.root, ".agent-trash"),
        )
        kwargs.update(overrides)
        return kwargs

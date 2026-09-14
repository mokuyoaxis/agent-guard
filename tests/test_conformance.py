"""Cross-harness conformance: the Claude adapter must reflect the exact
core decision + code for every command shape, and map decisions onto
Claude Code hook semantics correctly.

Success criterion (docs/architecture.md): same command, same cwd, same
workspace state -> identical core decision/code regardless of adapter.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import RepoFixture, git_available

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER = os.path.join(ROOT, "adapters", "claude", "pre_tool_use.py")
CHECK = os.path.join(ROOT, "skills", "delete-guard", "scripts", "check.py")

# (command, expected core decision after --enforce, expected code)
MATRIX = [
    ("rm -rf build", "ALLOW", "RELOCATE_TREE"),
    ("rm -rf .", "BLOCK", "BLOCK_PROTECTED_PATH"),
    ("rm -rf $UNSET_V/", "BLOCK", "BLOCK_UNDETERMINABLE_EFFECT"),
    ("cd sub && rm -rf build", "ASK", "COMPOUND_CWD_DELETE"),
    ("touch a.tmp && rm a.tmp", "ASK", "COMPOUND_CREATE_DELETE"),
    ("git push --force origin main", "BLOCK", "BLOCK_FORCE_PUSH"),
]


def run_check(workspace, command):
    proc = subprocess.run(
        [sys.executable, CHECK, "--enforce", "--json", "--", command],
        capture_output=True, text=True, timeout=60, cwd=workspace,
        env={**os.environ, "AGENT_GUARD_WORKSPACE": workspace},
    )
    return json.loads(proc.stdout)


def run_adapter(workspace, command, tool_name="Bash", env=None):
    payload = {
        "session_id": "conformance-test",
        "cwd": workspace,
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": command},
    }
    full_env = {**os.environ, "AGENT_GUARD_WORKSPACE": workspace,
                "AGENT_GUARD_DEBUG": "1"}
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, ADAPTER],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=90, cwd=workspace, env=full_env,
    )


def last_json(text):
    for line in reversed(text.strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def no_python3_path(case):
    """A PATH directory with `python` but no `python3`.

    Reproduces the Windows / minimal-image shape where the adapter's
    hardcoded `python3` could not be resolved. `git` and the core utils
    the guard itself needs stay on PATH.
    """
    bindir = tempfile.mkdtemp(prefix="agent-guard-nopy3-")
    case.addCleanup(shutil.rmtree, bindir, True)
    os.symlink(sys.executable, os.path.join(bindir, "python"))
    for tool in ("git", "sh", "bash"):
        found = shutil.which(tool)
        if found:
            os.symlink(found, os.path.join(bindir, tool))
    return bindir


@unittest.skipUnless(git_available(), "git required")
class Conformance(RepoFixture):
    def setUp(self):
        super().setUp()

    def seed(self):
        """Recreate consumable state: --enforce genuinely relocates."""
        os.makedirs(os.path.join(self.root, "build"), exist_ok=True)
        self.write("build/o.js")
        self.write("a.tmp")

    def test_core_and_adapter_agree_on_decision_and_code(self):
        for command, want_decision, want_code in MATRIX:
            with self.subTest(command=command):
                self.seed()
                core = run_check(self.root, command)
                # check.py reports ALLOW after applying compensations; the
                # code still names the compensation strategy.
                self.assertEqual(core["decision"], want_decision, command)
                self.assertEqual(core["code"], want_code, command)

                self.seed()  # enforce consumed the previous state
                proc = run_adapter(self.root, command)
                debug = last_json(proc.stderr)
                self.assertIsNotNone(debug, command)
                self.assertEqual(debug["decision"], want_decision, command)
                self.assertEqual(debug["code"], want_code, command)

    def test_mapping_allow_compensated_emits_permission_allow(self):
        self.seed()
        proc = run_adapter(self.root, "rm -rf build")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertIn("RELOCATE_TREE",
                      out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_mapping_ask_emits_permission_ask(self):
        proc = run_adapter(self.root, "cd sub && rm -rf build")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("COMPOUND_CWD_DELETE",
                      out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_mapping_block_exits_2_with_teaching_stderr(self):
        proc = run_adapter(self.root, "rm -rf .")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("BLOCK_PROTECTED_PATH", proc.stderr)
        self.assertIn("Do NOT circumvent", proc.stderr)

    def test_non_bash_tool_is_untouched(self):
        proc = run_adapter(self.root, "rm -rf build", tool_name="Write")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertNotIn("agent-guard", proc.stderr)

    def test_benign_bash_command_is_silent_fast_path(self):
        proc = run_adapter(self.root, "ls -la && echo hi")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_unparseable_stdin_stays_out_of_the_way(self):
        proc = subprocess.run(
            [sys.executable, ADAPTER], input="not-json{",
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "AGENT_GUARD_WORKSPACE": self.root})
        self.assertEqual(proc.returncode, 0)


    def test_adapter_spawns_guard_with_sys_executable(self):
        """Regression: the guard child must not hardcode `python3`.

        Windows (and some minimal images) ship only `python`, so a
        hardcoded `python3` made every interception fail closed with
        "guard infrastructure error" - the guard was silently unusable.
        Prove it here by running the adapter with a PATH that has no
        `python3` at all: the child must still be spawned through
        sys.executable.
        """
        self.seed()
        # A PATH that exposes `python` but no `python3` - the Windows
        # shape that broke the adapter. `git` is kept available because
        # the core needs it independently of the spawn fix.
        bindir = no_python3_path(self)
        proc = run_adapter(self.root, "rm -rf build", env={"PATH": bindir})
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertNotIn("guard infrastructure error", proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertIn("RELOCATE_TREE",
                      out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "build")))

    def test_adapter_blocking_still_works_without_python3_on_path(self):
        bindir = no_python3_path(self)
        proc = run_adapter(self.root, "rm -rf .", env={"PATH": bindir})
        self.assertEqual(proc.returncode, 2, proc.stderr[-800:])
        self.assertIn("BLOCK_PROTECTED_PATH", proc.stderr)
        self.assertNotIn("guard infrastructure error", proc.stderr)


if __name__ == "__main__":
    unittest.main()

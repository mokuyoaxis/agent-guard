"""Dual-anchored inline-script deletion blocking and zero-false-positive controls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from core import classifier, dialects, policy
from tests.helpers import RepoFixture, git_available


ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "skills/delete-guard/scripts/check.py"
CLAUDE_HOOK = ROOT / "adapters/claude/pre_tool_use.py"
KIMI_HOOK = ROOT / "adapters/kimi-code/pre_tool_use.py"


class InlineScriptClassifierTests(unittest.TestCase):
    POSITIVE_COMMANDS = [
        ("python3 shutil.rmtree", 'python3 -c "import shutil; shutil.rmtree(\'src\')"'),
        ("python os.remove", 'python -c "import os; os.remove(\'a.txt\')"'),
        ("python3 os.unlink", 'python3 -c "import os; os.unlink(\'a.txt\')"'),
        ("python3.13 pathlib unlink", 'python3.13 -c "from pathlib import Path; Path(\'a.txt\').unlink()"'),
        ("python attached -c", 'python3 -c"import shutil; shutil.rmtree(\'build\')"'),
        ("env wrapped python3 -B -c", 'env FOO=1 python3 -B -c "import os; os.rmdir(\'tmp\')"'),
        ("node fs.rmSync", 'node -e "require(\'fs\').rmSync(\'dist\', {recursive: true})"'),
        ("node --eval= unlinkSync", 'node --eval="const fs=require(\'fs\'); fs.unlinkSync(\'a.txt\')"'),
        ("node -r preload + -e rmSync", 'node -r ./preload.js -e "require(\'fs\').rmSync(\'dist\')"'),
        ("node fs/promises rm", 'node -e "require(\'node:fs/promises\').rm(\'dist\', {recursive: true})"'),
        ("node template interpolation rmSync", 'node -e \'console.log(`${require("fs").rmSync("dist")}`)\''),
        ("node eval wrapping rmSync", 'node -e "eval(\'require(\\"fs\\").rmSync(\\"dist\\")\')"'),
        ("python exec wrapping rmtree", 'python3 -c "exec(\'import shutil; shutil.rmtree(\\"src\\")\')"'),
        ("bun fs.promises.rm", 'bun -e "require(\'fs\').promises.rm(\'src\')"'),
        ("deno eval removeSync", 'deno eval "Deno.removeSync(\'src\', {recursive: true})"'),
        ("deno eval --ext=ts removeSync", 'deno eval --ext=ts "Deno.removeSync(\'a\')"'),
        ("deno eval --config removeSync", 'deno eval --config deno.json "Deno.removeSync(\'a\')"'),
        ("deno eval after --", 'deno eval -- "Deno.removeSync(\'a\')"'),
        ("ruby FileUtils.rm_rf", 'ruby -e "require \'fileutils\'; FileUtils.rm_rf(\'tmp\')"'),
        ("ruby File.delete", 'ruby -e "File.delete(\'a.txt\')"'),
        ("perl unlink", 'perl -e "unlink(\'a.txt\')"'),
        ("perl rmtree", 'perl -MFile::Path=rmtree -e "rmtree(\'tmp\')"'),
        ("python subprocess rm", 'python3 -c "import subprocess; subprocess.run([\'rm\', \'-rf\', \'src\'])"'),
        ("node child_process rm", 'node -e "require(\'child_process\').execSync(\'rm -rf src\')"'),
        ("python heredoc rmtree", "python3 - <<'EOF'\nimport shutil\nshutil.rmtree('src')\nEOF"),
        ("node heredoc rmSync", "node <<EOF\nrequire('fs').rmSync('dist')\nEOF"),
        ("bash heredoc rm", "bash <<'EOF'\nrm -rf src\nEOF"),
        ("piped python unlink", 'echo "import os; os.unlink(\'a.txt\')" | python3'),
        ("piped node unlinkSync", 'printf "require(\'fs\').unlinkSync(\'a\')" | node'),
    ]

    BENIGN_COMMANDS = [
        ("python unittest", "python3 -m unittest tests/test_delete.py"),
        ("python benign print", 'python3 -c "print(\'hello world\')"'),
        ("python printing api name", 'python3 -c "print(\'shutil.rmtree\')"'),
        ("python triple-quoted api name", 'python3 -c \'print("""os.remove("a")""")\''),
        ("python script path", "python3 skills/delete-guard/scripts/cleanup.py --json"),
        ("node benign eval", 'node -e "console.log(\'ok\')"'),
        ("node printing fs.rmSync name", 'node -e "console.log(\'fs.rmSync\')"'),
        ("node printing call text", 'node -e \'console.log("require(\\"fs\\").rmSync(\\"dist\\")")\''),
        ("node -p literal rimraf", 'node -p "\'rimraf\'"'),
        ("node data arg after -e", 'node -e "console.log(process.argv[1])" "fs.rmSync(\'dist\')"'),
        ("node script with -e arg", 'node script.js -e "require(\'fs\').rmSync(\'dist\')"'),
        ("node script path", "node server.js"),
        ("deno eval with data arg", 'deno eval "console.log(Deno.args)" "Deno.removeSync(\'a\')"'),
        ("deno eval with -- data arg", 'deno eval "console.log(Deno.args)" -- "Deno.removeSync(\'a\')"'),
        ("deno eval printing api name", 'deno eval --ext=ts "console.log(\'Deno.removeSync\')"'),
        ("ruby benign eval", 'ruby -e "puts 1 + 1"'),
        ("ruby printing api name", 'ruby -e "puts \'FileUtils.rm_rf\'"'),
        ("perl benign eval", 'perl -e "print \'ok\'"'),
        ("perl printing api name", 'perl -e "print \'unlink(a)\'"'),
        ("piped node printing api name", 'echo "console.log(\'fs.rmSync\')" | node'),
        ("piped data to python -c", 'echo "shutil.rmtree(\'src\')" | python3 -c "import sys; print(len(sys.stdin.read()))"'),
        ("git grep rmtree", "git grep shutil.rmtree"),
        ("rg rmSync", 'rg "fs.rmSync" src/'),
        ("cat heredoc writing python test", "cat <<'EOF' > cleanup_test.py\nimport shutil\nshutil.rmtree('tmp')\nEOF"),
        ("tee heredoc writing python", "tee script.py <<'EOF'\nimport os\nos.unlink('tmp')\nEOF"),
        ("python benign heredoc", "python3 - <<'EOF'\nprint('safe calculation', 1 + 2)\nEOF"),
        ("python heredoc printing api name", "python3 - <<'EOF'\nprint('shutil.rmtree')\nEOF"),
    ]

    def test_inline_script_deletions_classify_as_undeterminable_and_hit_prefilter(self):
        ctx = policy.PolicyContext(workspace="/tmp/ws", trash_root="/tmp/ws/.agent-trash", base_dir="/tmp/ws")
        for label, cmd in self.POSITIVE_COMMANDS:
            with self.subTest(label=label):
                self.assertIsNotNone(classifier.DESTRUCTIVE_PREFILTER_RE.search(cmd), cmd)
                specs, err = classifier.classify_command(cmd)
                self.assertIsNone(err, cmd)
                self.assertTrue(specs, cmd)
                self.assertTrue(any(s.kind == classifier.KIND_UNKNOWN and s.undeterminable for s in specs), specs)
                verdict = policy.worst(policy.decide_ops(specs, ctx))
                self.assertEqual(verdict.decision, policy.DECISION_BLOCK)
                self.assertEqual(verdict.code, policy.CODE_BLOCK_UNDETERMINABLE_EFFECT)

    def test_benign_interpreter_and_heredoc_writes_stay_allow_noop(self):
        ctx = policy.PolicyContext(workspace="/tmp/ws", trash_root="/tmp/ws/.agent-trash", base_dir="/tmp/ws")
        for label, cmd in self.BENIGN_COMMANDS:
            with self.subTest(label=label):
                specs, err = classifier.classify_command(cmd)
                self.assertIsNone(err, cmd)
                self.assertEqual(specs, [], f"unexpected specs for {label}: {specs}")
                verdict = policy.worst(policy.decide_ops(specs, ctx))
                self.assertEqual(verdict.decision, policy.DECISION_ALLOW)
                self.assertEqual(verdict.code, policy.CODE_ALLOW_NOOP)


class InlineScriptWindowsDialects(unittest.TestCase):
    def test_cmd_and_powershell_block_inline_delete_and_allow_benign(self):
        cases = [
            (dialects.DIALECT_CMD, 'python -c "import shutil; shutil.rmtree(\'build\')"', True),
            (dialects.DIALECT_CMD, 'node -e "require(\'fs\').rmSync(\'dist\')"', True),
            (dialects.DIALECT_CMD, 'node -e "console.log(\'fs.rmSync\')"', False),
            (dialects.DIALECT_CMD, 'deno eval "console.log(Deno.args)" "Deno.removeSync(\'a\')"', False),
            (dialects.DIALECT_CMD, 'python -c "print(\'ok\')"', False),
            (dialects.DIALECT_CMD, "python -m unittest", False),
            (dialects.DIALECT_POWERSHELL, 'python -c "import os; os.remove(\'a.txt\')"', True),
            (dialects.DIALECT_POWERSHELL, 'py -c "import shutil; shutil.rmtree(\'src\')"', True),
            (dialects.DIALECT_POWERSHELL, 'node -e "console.log(\'fs.rmSync\')"', False),
            (dialects.DIALECT_POWERSHELL, 'python -c "print(\'ok\')"', False),
        ]
        for dialect, cmd, should_block in cases:
            with self.subTest(dialect=dialect, cmd=cmd):
                specs, err = classifier.classify_command(cmd, dialect=dialect)
                self.assertIsNone(err)
                if should_block:
                    self.assertIsNotNone(classifier.DESTRUCTIVE_PREFILTER_RE_WINDOWS.search(cmd))
                    self.assertTrue(any(s.kind == classifier.KIND_UNKNOWN and s.undeterminable for s in specs), specs)
                else:
                    self.assertEqual(specs, [], specs)


@unittest.skipUnless(git_available(), "git required")
class InlineScriptEndToEndAdapters(RepoFixture):
    def test_check_cli_and_claude_kimi_hooks_block_inline_delete_and_allow_heredoc_file_write(self):
        keep = Path(self.write("keep.txt", "do not remove"))
        dangerous = 'python3 -c "import shutil; shutil.rmtree(\'keep.txt\')"'
        benign = "cat <<'EOF' > test_helper.py\nimport shutil\nshutil.rmtree('tmp')\nEOF"

        checked = subprocess.run(
            [sys.executable, "-B", str(CHECK_SCRIPT), "--enforce", "--json", "--", dangerous],
            cwd=self.root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(checked.returncode, 2, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["code"], "BLOCK_UNDETERMINABLE_EFFECT")
        self.assertEqual(keep.read_text(), "do not remove")

        for hook in (CLAUDE_HOOK, KIMI_HOOK):
            with self.subTest(hook=hook.name, mode="block"):
                payload = json.dumps({
                    "hook_event_name": "PreToolUse", "tool_name": "Bash",
                    "cwd": self.root, "tool_input": {"command": dangerous},
                })
                proc = subprocess.run(
                    [sys.executable, "-B", str(hook)],
                    cwd=self.root, input=payload, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("BLOCK_UNDETERMINABLE_EFFECT", proc.stderr)
                self.assertEqual(keep.read_text(), "do not remove")

            with self.subTest(hook=hook.name, mode="allow_heredoc"):
                ok_payload = json.dumps({
                    "hook_event_name": "PreToolUse", "tool_name": "Bash",
                    "cwd": self.root, "tool_input": {"command": benign},
                })
                proc = subprocess.run(
                    [sys.executable, "-B", str(hook)],
                    cwd=self.root, input=ok_payload, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()

"""The safe config view must not turn a read into a second secret copy."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import safe_view


ROOT = Path(__file__).resolve().parent.parent
VIEW = ROOT / "skills" / "exfil-guard" / "scripts" / "view.py"
SECRET = "ghp_" + "012345678901234567890123456789012345"
LOW_ENTROPY_SECRET = "password-" + "do-not-print"


def run_view(workspace, path, *options):
    return subprocess.run(
        [sys.executable, str(VIEW), "--workspace", str(workspace),
         *options, path],
        text=True, capture_output=True, timeout=20,
    )


class SafeViewTests(unittest.TestCase):
    def test_json_structure_hides_every_scalar_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = json.dumps({"auth": {"token": SECRET,
                                          "password": LOW_ENTROPY_SECRET,
                                          "enabled": False},
                                 "items": [12, None, ""]})
            target = Path(tmp) / "config.json"
            target.write_text(source, encoding="utf-8")
            proc = run_view(tmp, "config.json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), source)
            self.assertNotIn(SECRET, proc.stdout + proc.stderr)
            self.assertNotIn(LOW_ENTROPY_SECRET, proc.stdout + proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result["view"]["fields"]["auth"]["fields"]
                             ["enabled"], {"type": "boolean", "state": "set"})
            self.assertEqual(result["view"]["fields"]["items"]["items"][-1],
                             {"type": "string", "state": "empty"})

    def test_secret_shaped_field_name_is_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text(
                json.dumps({SECRET: "ordinary"}), encoding="utf-8")
            proc = run_view(tmp, "config.json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(SECRET, proc.stdout + proc.stderr)
            self.assertIn("<hidden-key-1>", proc.stdout)

    def test_dotenv_only_shows_keys_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = ("# " + SECRET + "\nDATABASE_PASSWORD=" +
                      LOW_ENTROPY_SECRET + "\nexport PORT=\nEMPTY=''\n")
            name = "." + "env.local"
            (Path(tmp) / name).write_text(source, encoding="utf-8")
            proc = run_view(tmp, name)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(SECRET, proc.stdout + proc.stderr)
            self.assertNotIn(LOW_ENTROPY_SECRET, proc.stdout + proc.stderr)
            fields = json.loads(proc.stdout)["view"]["fields"]
            self.assertEqual(fields["DATABASE_PASSWORD"]["state"], "set")
            self.assertEqual(fields["PORT"]["state"], "empty")
            self.assertEqual(fields["EMPTY"]["state"], "empty")

    def test_refuses_outside_and_symlink_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real.json").write_text('{"safe":"hidden"}',
                                            encoding="utf-8")
            (root / "link.json").symlink_to(root / "real.json")
            (root / "sub").symlink_to(root, target_is_directory=True)
            for path in ("../real.json", "/real.json", "sub/real.json",
                         "link.json", "a//b.json", "a/./b.json"):
                with self.subTest(path=path):
                    proc = run_view(tmp, path)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertEqual(json.loads(proc.stderr)["status"], "error")

    def test_rejects_hardlinks_and_special_files_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.json").write_text("{}", encoding="utf-8")
            os.link(root / "one.json", root / "two.json")
            self.assertEqual(run_view(tmp, "two.json").returncode, 2)
            if hasattr(os, "mkfifo"):
                os.mkfifo(root / "pipe.json")
                self.assertEqual(run_view(tmp, "pipe.json").returncode, 2)

    def test_malformed_inputs_never_echo_value(self):
        cases = {
            "duplicate.json": '{"x":"' + SECRET + '","x":2}',
            "bad.json": '{"x":"' + SECRET + '",',
            "nested.json": '[' * 18 + '"' + SECRET + '"' + ']' * 18,
            "." + "env": "TOKEN='" + SECRET + "\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, source in cases.items():
                with self.subTest(name=name):
                    (Path(tmp) / name).write_text(source, encoding="utf-8")
                    proc = run_view(tmp, name)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertNotIn(SECRET, proc.stderr)
            (Path(tmp) / "invalid.json").write_bytes(b"\xff" +
                                                      SECRET.encode())
            proc = run_view(tmp, "invalid.json")
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn(SECRET, proc.stdout + proc.stderr)

    def test_size_cap_and_unsupported_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "big.json").write_bytes(
                b"x" * (safe_view.MAX_VIEW_BYTES + 1))
            proc = run_view(tmp, "big.json")
            self.assertEqual(json.loads(proc.stderr)["code"], "FILE_TOO_LARGE")
            (Path(tmp) / "plain.txt").write_text("secret", encoding="utf-8")
            proc = run_view(tmp, "plain.txt")
            self.assertEqual(json.loads(proc.stderr)["code"],
                             "UNSUPPORTED_FORMAT")

    def test_argument_errors_do_not_echo_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_view(tmp, "config.json", "--format=" + SECRET)
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn(SECRET, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

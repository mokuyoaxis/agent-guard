"""Exercise the actual release SDK gate without making Core depend on the SDK."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


def step_block(workflow, name):
    return workflow.split("      - name: " + name + "\n", 1)[1].split("\n      - name:", 1)[0]


def gate_script(workflow):
    block = step_block(workflow, "Require MCP protocol coverage")
    command = textwrap.dedent(block.split("        run: |\n", 1)[1]).strip().splitlines()
    if command[0] != "python - <<'PY'" or command[-1] != "PY":
        raise AssertionError("release SDK preflight is not the expected Python command")
    return "\n".join(command[1:-1]) + "\n"


# Run the workflow's own Python body against controlled dependency states.
# No fake dependency is written into the checkout or installed environment.
CONTROLLED_ENVIRONMENT = r'''
import importlib.metadata
import sys
from types import ModuleType
import unittest

scenario, expected = sys.argv[1:]
def version(name):
    if scenario == "missing":
        raise importlib.metadata.PackageNotFoundError(name)
    return expected + ".unexpected" if scenario == "wrong-version" else expected
importlib.metadata.version = version

sdk = ModuleType("mcp")
sdk.__path__ = []
server = ModuleType("mcp.server")
if scenario != "broken-import":
    server.Server = object
sdk.server = server
sys.modules.update({"mcp": sdk, "mcp.server": server})

class ProtocolTests(unittest.TestCase):
    def test_wire(self):
        pass
if scenario == "skipped-class":
    ProtocolTests.__unittest_skip__ = True
elif scenario == "skipped-method":
    ProtocolTests.test_wire.__unittest_skip__ = True
elif scenario == "empty-suite":
    ProtocolTests = unittest.TestCase
tests = ModuleType("tests")
tests.__path__ = []
protocol = ModuleType("tests.test_mcp_protocol")
protocol.McpProtocolTests = ProtocolTests
protocol.SDK_AVAILABLE = scenario != "unavailable"
tests.test_mcp_protocol = protocol
sys.modules.update({"tests": tests, "tests.test_mcp_protocol": protocol})
exec(compile(sys.stdin.read(), "<actual release SDK gate>", "exec"))
'''


class ReleaseGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/release.yml").read_text()
        cls.script = gate_script(cls.workflow)
        requirement = next(line.strip() for line in
                           (ROOT / "adapters/mcp/requirements.txt").read_text().splitlines()
                           if line.strip() and not line.lstrip().startswith("#"))
        cls.expected_version = requirement.split("==")[1]

    def run_gate(self, scenario):
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("PYTHONPATH", "PYTHONHOME", "AGENT_GUARD_MCP_PYTHON")}
        return subprocess.run([sys.executable, "-B", "-S", "-c", CONTROLLED_ENVIRONMENT,
                               scenario, self.expected_version], input=self.script, text=True,
                              capture_output=True, cwd=ROOT, env=environment, timeout=15)

    def test_release_orders_required_sdk_before_full_tests_and_pack(self):
        names = ("Install optional MCP SDK for release validation", "Require MCP protocol coverage",
                 "Run release tests", "Pack once and record checksum", "Publish the same tarball to npm")
        positions = [self.workflow.index("      - name: " + name + "\n") for name in names]
        self.assertEqual(positions, sorted(positions))
        install = step_block(self.workflow, names[0])
        self.assertIn("python -m pip install -r adapters/mcp/requirements.txt", install)
        for name in names[:3]:
            block = step_block(self.workflow, name)
            self.assertNotIn("continue-on-error:", block)
            self.assertNotIn("        if:", block)
        self.assertEqual(step_block(self.workflow, "Run release tests").strip(), "run: npm test")

    def test_valid_pinned_sdk_and_enabled_protocol_suite_pass(self):
        result = self.run_gate("valid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("protocol tests are enabled", result.stdout)

    def test_missing_sdk_fails_release_gate(self):
        result = self.run_gate("missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PackageNotFoundError", result.stderr)

    def test_wrong_sdk_version_fails_release_gate(self):
        result = self.run_gate("wrong-version")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the pinned requirement", result.stderr)

    def test_broken_sdk_import_fails_release_gate(self):
        result = self.run_gate("broken-import")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ImportError", result.stderr)

    def test_unavailable_or_skipped_protocol_suite_fails_release_gate(self):
        for scenario in ("unavailable", "skipped-class", "skipped-method", "empty-suite"):
            with self.subTest(scenario=scenario):
                result = self.run_gate(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("every MCP protocol test to be enabled", result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Process-local pre-adapter failure injection for native hook acceptance.

Only activates when this fixture directory is placed on PYTHONPATH and the
explicit test switch is set. It must never be used in a normal agent session.
"""

import os

if os.environ.get("AGENT_GUARD_TEST_FAIL_PYTHON_STARTUP") == "1":
    os._exit(1)

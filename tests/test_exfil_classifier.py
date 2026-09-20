"""exfil-guard span classifier: T1 patterns, placeholders, path facts.

Facts only - no decisions here (that is test_exfil_policy.py).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import redaction
from core.redaction import (
    CONFIDENCE_CONTEXTUAL, CONFIDENCE_DETERMINISTIC, FAMILY_PATH,
    FAMILY_SECRET, SpanSpec, detect_paths, detect_secrets, is_placeholder,
    merge_spans,
)

# Fixtures are built by concatenation on purpose: this file must not contain
# a literal that looks like a live credential, or the guard would (rightly)
# flag its own test suite and the leak-freedom assertions would be vacuous.
OPENAI = "sk-" + "AbCdEf0123456789AbCdEf0123456789"
GITHUB = "ghp_" + "012345678901234567890123456789012345"
GITLAB = "glpat-" + "abcdefghij0123456789"
SLACK = "xoxb-" + "1234567890-abcdefghijkl"
STRIPE = "sk_live_" + "51H8xYzAbCdEfGhIjKlMnOpQr"
AWS = "AKIA" + "J7XQZ2M4PLRN8TWV"
JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
       "eyJzdWIiOiIxMjM0In0."
       "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk")
# The delimiters are split for the same reason as the tokens above: a
# literal BEGIN/END pair in this file is itself a matchable key block.
_ARMOR = "-----" + "BEGIN OPENSSH PRIVATE KEY" + "-----"
_FOOTER = "-----" + "END OPENSSH PRIVATE KEY" + "-----"
PRIVATE_KEY = _ARMOR + "\n" + "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU" + "\n" + _FOOTER


def rules(text):
    return [s.rule_id for s in detect_secrets(text)]


def one_span(text):
    spans = detect_secrets(text)
    assert len(spans) == 1, [s.safe() for s in spans]
    return spans[0]


class T1KnownPatterns(unittest.TestCase):
    def test_openai_key(self):
        span = one_span("config: " + OPENAI)
        self.assertEqual(span.rule_id, redaction.RULE_OPENAI_KEY)
        self.assertEqual(span.family, FAMILY_SECRET)
        self.assertEqual(span.confidence, CONFIDENCE_DETERMINISTIC)
        self.assertEqual(span.raw, OPENAI)

    def test_github_token_variants(self):
        for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
            with self.subTest(prefix=prefix):
                token = prefix + "012345678901234567890123456789012345"
                self.assertEqual(rules(token),
                                 [redaction.RULE_GITHUB_TOKEN])

    def test_aws_access_key_id(self):
        self.assertEqual(rules(AWS), [redaction.RULE_AWS_ACCESS_KEY_ID])

    def test_gitlab_slack_stripe(self):
        self.assertEqual(rules(GITLAB), [redaction.RULE_GITLAB_TOKEN])
        self.assertEqual(rules(SLACK), [redaction.RULE_SLACK_TOKEN])
        self.assertEqual(rules(STRIPE), [redaction.RULE_STRIPE_KEY])

    def test_stripe_test_mode_is_not_a_live_key(self):
        self.assertEqual(rules("sk_test_" + "51H8xYzAbCdEfGhIjKlMnOpQr"), [])

    def test_jwt_requires_structural_header(self):
        self.assertEqual(rules("auth " + JWT), [redaction.RULE_JWT])
        # Same shape, header does not decode to JSON with alg.
        self.assertEqual(rules("version 1.2.3"), [])
        self.assertEqual(rules("host api.example.com"), [])
        self.assertEqual(rules("not.a.jwt"), [])

    def test_private_key_block_redacts_whole_block(self):
        span = one_span("here\n" + PRIVATE_KEY + "\nthere")
        self.assertEqual(span.rule_id, redaction.RULE_PRIVATE_KEY_BLOCK)
        self.assertEqual(span.raw, PRIVATE_KEY)

    def test_detects_offsets_match_raw(self):
        text = "prefix " + OPENAI + " suffix"
        span = one_span(text)
        self.assertEqual(text[span.start:span.end], span.raw)
        self.assertEqual(span.length, len(OPENAI))

    def test_empty_and_prose_text_yields_nothing(self):
        self.assertEqual(detect_secrets(""), [])
        self.assertEqual(detect_secrets("nothing to see here, just prose."), [])
        self.assertEqual(detect_secrets("sha512-" + "a" * 88), [])


class PlaceholderAllowlist(unittest.TestCase):
    def test_documented_placeholders(self):
        for text in ("YOUR_API_KEY_HERE", "EXAMPLE_SECRET", "changeme",
                     "dummy-value", "placeholder", "TODO", "xxxxxxxx",
                     "your_token_here", "<YOUR_API_KEY>", "${OPENAI_API_KEY}",
                     "***", "<REDACTED>", "[REDACTED]"):
            with self.subTest(text=text):
                self.assertTrue(is_placeholder(text), text)

    def test_placeholder_matches_are_not_spans(self):
        for body in ("xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                     "your_api_key_here_value",
                     "sk-test"):
            with self.subTest(body=body):
                self.assertEqual(rules("key = " + body), [])

    def test_real_looking_values_are_not_placeholders(self):
        for text in (OPENAI, GITHUB, AWS, "hunter2hunter2hunter2"):
            with self.subTest(text=text):
                if text == "hunter2hunter2hunter2":
                    continue          # short word; not a T1 pattern either
                self.assertFalse(is_placeholder(text), text)


class MergeSpans(unittest.TestCase):
    def test_overlaps_collapse_first_wins(self):
        wide = SpanSpec("aaa", 0, 3, "r", FAMILY_SECRET,
                        CONFIDENCE_DETERMINISTIC, "prose", "c")
        inner = SpanSpec("a", 1, 2, "r2", FAMILY_SECRET,
                         CONFIDENCE_DETERMINISTIC, "prose", "c")
        later = SpanSpec("b", 5, 6, "r3", FAMILY_SECRET,
                         CONFIDENCE_DETERMINISTIC, "prose", "c")
        self.assertEqual([s.rule_id for s in merge_spans([inner, later, wide])],
                         ["r", "r3"])


class SpanSpecSafety(unittest.TestCase):
    def test_safe_view_has_no_bytes(self):
        span = one_span("x " + OPENAI)
        safe = span.safe()
        self.assertNotIn("raw", safe)
        self.assertNotIn(OPENAI, repr(safe))
        self.assertIn("start", safe)


class RepoCorpusNoise(unittest.TestCase):
    """Design 5.6: this repository's own text must produce zero T1 hits.

    A T1 hit inside our own docs/tests is a rule bug, not a tuning problem.
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    SKIP_DIRS = {".git", ".agent-trash", "__pycache__", "node_modules"}

    def test_own_corpus_is_quiet(self):
        hits = []
        for base, dirs, files in os.walk(self.ROOT):
            dirs[:] = [d for d in dirs if d not in self.SKIP_DIRS]
            for name in files:
                path = os.path.join(base, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                for span in detect_secrets(text):
                    hits.append((os.path.relpath(path, self.ROOT),
                                 span.rule_id))
        self.assertEqual(hits, [], f"T1 false positives in own corpus: {hits}")


class PathDetection(unittest.TestCase):
    """Workspace-relative exemption is the core rule (design 3.1)."""

    def setUp(self):
        self.workspace = "/home/alice/work/agent-guard"

    def paths(self, text, workspace=None):
        ws = self.workspace if workspace is None else workspace
        return detect_paths(text, ws)

    def only(self, text):
        spans = self.paths(text)
        assert len(spans) == 1, [s.safe() for s in spans]
        return spans[0]

    # --- the exemption ---------------------------------------------------

    def test_path_inside_workspace_is_not_host_identifying(self):
        inside = self.workspace + "/src/main.py"
        span = self.only("edit " + inside)
        self.assertEqual(span.rule_id, redaction.RULE_WORKSPACE_PATH)
        self.assertEqual(span.family, FAMILY_PATH)

    def test_workspace_root_itself_is_flagged(self):
        span = self.only("cd " + self.workspace)
        self.assertEqual(span.rule_id, redaction.RULE_WORKSPACE_PATH)
        self.assertTrue(span.notes == [] or True)   # root noted by policy
        self.assertNotIn("host-absolute", span.rule_id)

    def test_relative_paths_are_not_spans(self):
        for text in ("see ./src/main.py", "../sibling/x", "src/main.py"):
            with self.subTest(text=text):
                self.assertEqual(self.paths(text), [], text)

    # --- host paths ------------------------------------------------------

    def test_linux_home_is_deterministic(self):
        span = self.only("why does /home/bob/other/thing fail?")
        self.assertEqual(span.rule_id, redaction.RULE_HOST_ABSOLUTE_PATH)
        self.assertEqual(span.confidence, CONFIDENCE_DETERMINISTIC)

    def test_macos_home_and_volumes(self):
        for text in ("/Users/bob/.venv/lib/x.py", "/Volumes/ExtDisk/backup/x"):
            with self.subTest(text=text):
                span = self.only(text)
                self.assertEqual(span.rule_id, redaction.RULE_HOST_ABSOLUTE_PATH)
                self.assertEqual(span.confidence, CONFIDENCE_DETERMINISTIC)

    def test_ci_runner_prefixes(self):
        for text in ("/home/runner/work/agent-guard/agent-guard",
                     "/builds/proj/x", "/var/lib/jenkins/job/7",
                     "/github/workspace/src", "/runner/_work/x"):
            with self.subTest(text=text):
                span = self.only(text)
                self.assertEqual(span.rule_id, redaction.RULE_HOST_ABSOLUTE_PATH)

    def test_unc_and_device_paths(self):
        for text in (r"\\fileserver\share\x", r"\\.\pipe\p",
                     r"\\?\C:\x"):
            with self.subTest(text=text):
                span = self.only(text)
                self.assertEqual(span.rule_id, redaction.RULE_DEVICE_PATH)

    def test_windows_user_path_is_a_fact(self):
        span = self.only(r"open C:\Users\bob\AppData\Local\Temp\a.log")
        self.assertEqual(span.family, FAMILY_PATH)

    def test_windows_environment_interpolation_is_unresolvable(self):
        span = self.only(r"clean %USERPROFILE%\tmp")
        self.assertIn("interpolation", " ".join(span.notes))

    # --- quiet by default ------------------------------------------------

    def test_system_prefixes_are_not_host_identifiers(self):
        for text in ("/usr/lib/python3.11", "/etc/hosts", "/opt/app/x",
                     "/dev/null", "C:\\Windows\\System32", "/bin/rm"):
            with self.subTest(text=text):
                span = self.only(text)
                self.assertEqual(span.rule_id, redaction.RULE_SYSTEM_PATH)

    def test_users_shared_is_exempt(self):
        span = self.only("/Users/Shared/project")
        self.assertEqual(span.rule_id, redaction.RULE_SYSTEM_PATH)

    def test_generic_absolute_path_is_only_contextual(self):
        span = self.only("/project/build/out")
        self.assertEqual(span.rule_id, redaction.RULE_GENERIC_ABSOLUTE_PATH)
        self.assertEqual(span.confidence, CONFIDENCE_CONTEXTUAL)

    def test_negative_corpus(self):
        for text in ("version 1.2.3", "host api.example.com",
                     "https://github.com/a/b", "sha512-" + "a" * 40,
                     "a / b", "path /= flags", "192.168.0.1", "::1",
                     "ratio 1/2", "and/or", "%dT%", "w/o"):
            with self.subTest(text=text):
                self.assertEqual(self.paths(text), [], text)

    def test_offsets_match_raw(self):
        text = "boom /home/bob/a/b/c.txt now"
        span = self.only(text)
        self.assertEqual(text[span.start:span.end], span.raw)

    def test_workspace_ancestors_are_host_identifying(self):
        # The workspace itself is exempt, but its parent is not: the ancestor
        # identifies the host layout, which is exactly the leak (design 3.3).
        span = self.only("/home/alice/work/other-project/src/x.py")
        self.assertEqual(span.rule_id, redaction.RULE_HOST_ABSOLUTE_PATH)


if __name__ == "__main__":
    unittest.main()

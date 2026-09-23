"""Structural contract for the distributable recovery-audit Skill."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "recovery-audit"
SKILL = SKILL_DIR / "SKILL.md"
OPENAI = SKILL_DIR / "agents" / "openai.yaml"
RECORDS = SKILL_DIR / "references" / "evidence-records.md"


def frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise AssertionError("SKILL.md must start with YAML frontmatter")
    return parts[1]


def quoted_field(text: str, field: str) -> str:
    match = re.search(rf'^\s*{re.escape(field)}:\s*"([^"]*)"\s*$',
                      text, re.MULTILINE)
    if not match:
        raise AssertionError(f"missing quoted field: {field}")
    return match.group(1)


class RecoveryAuditSkillStructure(unittest.TestCase):
    def test_entrypoint_has_matching_name_and_description(self):
        metadata = frontmatter(SKILL.read_text(encoding="utf-8"))
        self.assertRegex(metadata, r"(?m)^name:\s+recovery-audit\s*$")
        self.assertRegex(metadata, r"(?m)^description:\s*>-\s*$")

    def test_ui_metadata_is_invocable(self):
        metadata = OPENAI.read_text(encoding="utf-8")
        short = quoted_field(metadata, "short_description")
        prompt = quoted_field(metadata, "default_prompt")
        self.assertGreaterEqual(len(short), 25)
        self.assertLessEqual(len(short), 64)
        self.assertIn("$recovery-audit", prompt)

    def test_referenced_record_schema_exists(self):
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("references/evidence-records.md", skill)
        self.assertTrue(RECORDS.is_file())

    def test_record_schema_preserves_recovery_truth_states(self):
        records = RECORDS.read_text(encoding="utf-8")
        for status in ("`recovered`", "`reconstructed`", "`missing`"):
            self.assertIn(status, records)
        for result in ("`agree_ok`", "`agree_fail`", "`diverged_ok`",
                       "`diverged_fail`"):
            self.assertIn(result, records)


if __name__ == "__main__":
    unittest.main()

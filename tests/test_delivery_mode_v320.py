# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeliveryModeV320Tests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_coordinator_role_separation_and_owner_work_preservation(self):
        text = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        self.assertIn("Coordinator Lifecycle Protocol v3.2.1", text)
        self.assertIn("Project coordinators are autonomous", text)
        self.assertIn("must never cancel or replace a direct owner-requested work unit", text)
        self.assertIn("Do not send milestone, gate, progress, or completion reports", text)
        self.assertIn("system architect/maintainer, not a supervisor", text)
        self.assertNotIn("Audit is ON by default", text)
        self.assertNotIn("On every child message or owner interaction", text)

    def test_risk_tiers_and_bounded_correction_cycle(self):
        text = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        self.assertIn("**Low**", text)
        self.assertIn("**Medium**", text)
        self.assertIn("**High**", text)
        self.assertIn("No audit-of-audit", text)
        self.assertIn("One failed acceptance permits one root-cause correction", text)
        self.assertIn("A second failure escalates the exact blocker", text)

    def test_reversible_evidence_backed_choices_are_coordinator_authority(self):
        skill = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        delivery = self.read("docs/DELIVERY-MODE-v3.2.0.md")
        kickoff = self.read("scripts/coordinator-kickoff.md")
        self.assertIn("Coordinators decide and execute reversible or evidence-backed technical choices", skill)
        self.assertIn("Risk tier alone does not create an owner gate", skill)
        self.assertIn("A vague gate without concrete evidence and an owner-only category is invalid", skill)
        self.assertIn("Evidence-backed technical choices are coordinator authority", delivery)
        self.assertIn("Risk tier сам по себе не создаёт owner gate", kickoff)
        for text in (skill, delivery):
            self.assertIn("irreversible/destructive", text)
            self.assertIn("money/entitlements", text)
            self.assertTrue("production secrets" in text or "production credentials or secrets" in text)

    def test_worker_does_not_substitute_infrastructure_for_product(self):
        text = self.read("skills/worker-completion-protocol/SKILL.md")
        self.assertIn("Worker Completion Protocol v3.2.1", text)
        self.assertIn("Implement the exact owner/coordinator-frozen outcome", text)
        self.assertIn("one safe recovery attempt or 20 minutes", text)
        self.assertIn("Send no micro-progress messages", text)

    def test_kickoff_inherits_delivery_mode(self):
        text = self.read("scripts/coordinator-kickoff.md")
        self.assertIn("canonical v3.2.1", text)
        self.assertIn("protocol-version::3.2.1", text)
        self.assertIn("Никакого audit-of-audit", text)
        self.assertIn("Не отправляй owner-facing architecture session никакие unsolicited milestone/gate/progress/completion reports", text)

    def test_public_guide_defines_tests_as_acceptance_not_product(self):
        readme = self.read("README.md")
        delivery = self.read("docs/DELIVERY-MODE-v3.2.0.md")
        defaults = self.read("docs/CURRENT-DEFAULTS.md")
        self.assertIn("Protocol v3.2.1", readme)
        self.assertIn("Tests, audits, reports, certificates, and gates are acceptance instruments", delivery)
        self.assertIn("Unsolicited reports, routine acknowledgements, and status polling are prohibited", delivery)
        self.assertNotIn("a terminal product milestone", delivery)
        self.assertIn("Audit-of-audit:                prohibited", defaults)


if __name__ == "__main__":
    unittest.main()

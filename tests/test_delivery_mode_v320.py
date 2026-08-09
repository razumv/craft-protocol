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
        self.assertIn("Do not send routine updates to the owner-facing infrastructure session", text)
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
        self.assertIn("Не отправляй routine updates owner-facing infrastructure session", text)

    def test_public_guide_defines_tests_as_acceptance_not_product(self):
        readme = self.read("README.md")
        delivery = self.read("docs/DELIVERY-MODE-v3.2.0.md")
        defaults = self.read("docs/CURRENT-DEFAULTS.md")
        self.assertIn("Protocol v3.2.1", readme)
        self.assertIn("Tests, audits, reports, certificates, and gates are acceptance instruments", delivery)
        self.assertIn("Routine acknowledgements and status polling are prohibited", delivery)
        self.assertIn("Audit-of-audit:                prohibited", defaults)


if __name__ == "__main__":
    unittest.main()

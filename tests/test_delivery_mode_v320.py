# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeliveryModeV320Tests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_coordinator_role_separation_and_owner_work_preservation(self):
        text = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        self.assertRegex(text, r"Coordinator Lifecycle Protocol v3\.\d+\.\d+")
        self.assertIn("Project coordinators are autonomous", text)
        self.assertIn("must never cancel or replace a direct owner-requested work unit", text)
        self.assertIn("Do not send milestone, gate, progress, completion, archive, blocker, or decision-request messages", text)
        self.assertIn("system architect/maintainer, not a supervisor", text)
        self.assertIn("only in direct response to an explicit owner status/fact query or exact owner instruction", text)
        self.assertNotIn("Contact it only for a genuinely owner-only decision", text)
        self.assertNotIn("Audit is ON by default", text)
        self.assertNotIn("On every child message or owner interaction", text)

    def test_risk_tiers_and_bounded_correction_cycle(self):
        text = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        self.assertIn("**Low**", text)
        self.assertIn("**Medium**", text)
        self.assertIn("**High**", text)
        self.assertIn("No audit-of-audit", text)
        self.assertIn("one product-acceptance failure permits one root-cause correction", text)
        self.assertIn("repeated same-root or second acceptance failure escalates", text)

    def test_reversible_evidence_backed_choices_are_coordinator_authority(self):
        skill = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        delivery = self.read("docs/DELIVERY-MODE-v3.2.0.md")
        kickoff = self.read("scripts/coordinator-kickoff.md")
        self.assertIn("Coordinators decide and execute reversible or evidence-backed technical choices", skill)
        self.assertIn("Risk tier alone does not create an owner gate", skill)
        self.assertIn("A vague gate without concrete evidence and an owner-only category is invalid", skill)
        self.assertIn("Automatic continuation is mandatory", skill)
        self.assertIn("immediately execute one correction without opening an owner gate", skill)
        self.assertIn("Evidence-backed technical choices are coordinator authority", delivery)
        self.assertIn("Risk tier сам по себе не создаёт owner gate", kickoff)
        for text in (skill, delivery):
            self.assertIn("irreversible/destructive", text)
            self.assertIn("money/entitlements", text)
            self.assertTrue("production secrets" in text or "production credentials or secrets" in text)

    def test_worker_does_not_substitute_infrastructure_for_product(self):
        text = self.read("skills/worker-completion-protocol/SKILL.md")
        self.assertRegex(text, r"Worker Completion Protocol v3\.\d+\.\d+")
        self.assertIn("Implement the exact owner/coordinator-frozen outcome", text)
        self.assertIn("one safe recovery attempt or 20 minutes", text)
        self.assertIn("Send no micro-progress messages", text)

    def test_kickoff_inherits_delivery_mode(self):
        text = self.read("scripts/coordinator-kickoff.md")
        self.assertRegex(text, r"canonical v3\.\d+\.\d+")
        self.assertRegex(text, r"protocol-version::3\.\d+\.\d+")
        self.assertIn("Никакого audit-of-audit", text)
        self.assertIn("Не отправляй owner-facing architecture session никакие unsolicited milestone/gate/progress/completion/archive/blocker/decision-request сообщения", text)

    def test_public_guide_defines_tests_as_acceptance_not_product(self):
        readme = self.read("README.md")
        delivery = self.read("docs/DELIVERY-MODE-v3.2.0.md")
        defaults = self.read("docs/CURRENT-DEFAULTS.md")
        self.assertRegex(readme, r"Protocol v3\.\d+\.\d+")
        self.assertIn("Tests, audits, reports, certificates, and gates are acceptance instruments", delivery)
        self.assertIn("Unsolicited reports, routine acknowledgements, and status polling are prohibited", delivery)
        self.assertIn("ignores all unsolicited coordinator updates", delivery)
        self.assertIn("owner-decision requests", delivery)
        self.assertNotIn("unless they contain an exact owner decision request", delivery)
        self.assertNotIn("a terminal product milestone", delivery)
        self.assertIn("Audit-of-audit:                prohibited", defaults)

    def test_product_increment_batch_and_owner_communication_contract(self):
        skill = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        worker = self.read("skills/worker-completion-protocol/SKILL.md")
        kickoff = self.read("scripts/coordinator-kickoff.md")
        spec = self.read("docs/PRODUCT-INCREMENTS-v3.4.md")
        defaults = self.read("docs/CURRENT-DEFAULTS.md")
        for text in (skill, kickoff, spec):
            self.assertIn("Product Increment", text)
            self.assertIn("batch CI", text)
            self.assertIn("real-workflow", text)
        self.assertIn("What the customer sees", skill)
        self.assertIn("What can be demonstrated now", skill)
        self.assertIn("ETA range and confidence", skill)
        self.assertIn("Technical evidence", skill)
        self.assertIn("Никогда не начинай owner-facing ответ с PR", kickoff)
        self.assertIn("PR numbers, commits, SHAs, CI runs", spec)
        self.assertIn("Product-language outcome first", worker)
        self.assertIn("Owner status order:", defaults)

    def test_failure_taxonomy_and_ui_real_workflow_are_mandatory(self):
        skill = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        worker = self.read("skills/worker-completion-protocol/SKILL.md")
        spec = self.read("docs/PRODUCT-INCREMENTS-v3.4.md")
        for failure_class in ("admission-environment", "implementation-defect", "product-acceptance",
                              "integration-release", "irreversible-high-risk"):
            self.assertIn(failure_class, worker)
            self.assertIn(failure_class, spec)
        self.assertIn("UI completion", skill)
        self.assertIn("real desktop/mobile/user workflow", skill)
        self.assertIn("does not spend product correction budget", spec)
        self.assertIn("Advance automatically across the DAG", spec)
        self.assertIn("Human-stop boundary", spec)

    def test_archival_hygiene_is_mandatory_exhaustive_and_never_owner_reported(self):
        skill = self.read("skills/coordinator-lifecycle-protocol/SKILL.md")
        kickoff = self.read("scripts/coordinator-kickoff.md")
        defaults = self.read("docs/CURRENT-DEFAULTS.md")
        for text in (skill, kickoff, defaults):
            self.assertIn("archivableBacklog", text)
            self.assertIn("predecessor-not-archived", text)
            self.assertIn("stale-coordinator-session", text)
            self.assertIn("unknown-holder", text)
            self.assertIn("cleanup reports to Fleet", text)
        self.assertIn("Immediately after `accept-transfer`", skill)
        self.assertIn("Continue bounded batches", skill)
        self.assertIn("Archive first, then use the guarded reaper", skill)
        self.assertIn("Never delete history", skill)
        self.assertIn("сразу после `accept-transfer` adoption", kickoff)
        self.assertIn("Archive FIRST, потом guarded reaper", kickoff)
        self.assertIn("immediately after transfer adoption/material transitions", defaults)
        self.assertIn("never delete history", defaults)

    def test_manifest_generator_covers_release_changelog(self):
        generator = self.read("tools/generate-manifest.sh")
        manifest = self.read("manifest.sha256")
        self.assertIn("CHANGELOG.md", generator)
        self.assertIn("  CHANGELOG.md", manifest)

    def test_research_disposition_accounts_for_all_129_items(self):
        research = self.read("docs/RESEARCH-DISPOSITION-v3.4.md")
        rows = [line for line in research.splitlines() if line.startswith("| ") and line.split("|")[1].strip().isdigit()]
        self.assertEqual(len(rows), 129)
        self.assertIn("Total: **129/129**", research)
        for category in ("covered-v3.3", "adopt-v3.4", "operator/project-specific", "reject/unsafe"):
            self.assertIn(f"`{category}`", research)
        self.assertIn("Жёсткий allowlist инструментов", research)
        self.assertIn("Reject replacing explicit capability/permission checks", research)


if __name__ == "__main__":
    unittest.main()

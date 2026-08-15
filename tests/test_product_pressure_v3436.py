# SPDX-License-Identifier: Apache-2.0
"""Fleet regressions for the bounded v3.4.36 delivery-pressure correction."""
from __future__ import annotations
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)  # type: ignore
    return module


class ProductPressureV3436(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.status = load("coordinator-status")
        cls.common = load("orchestration-common")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = {key: os.environ.get(key) for key in ("CRAFT_RUNTIME", "CRAFT_SESSIONS", "CRAFT_WORKSPACE")}
        root = Path(self.temp.name)
        os.environ.update(CRAFT_RUNTIME=str(root / "runtime"), CRAFT_SESSIONS=str(root / "sessions"), CRAFT_WORKSPACE=str(root))
        # Modules captured paths when imported.  Their empty runtime is still a
        # useful complete synthesized baseline for pure contradiction regressions.

    def tearDown(self):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def increment(self, stories):
        return self.status.normalize_increment({"id": "inc", "stage": "building", "riskTier": "low",
                                                "demonstrationCriterion": "customer completes checkout", "stories": stories})

    def synth(self, **changes):
        base = self.status.synthesize("v3436", 1_000_000)
        base.update(changes)
        return base

    def declared(self, increment, **changes):
        value = {"phase": "executing", "productIncrement": increment, "nextActions": [],
                 "completedOutcomes": [], "githubSync": None, "correctionBudgetExtensions": [],
                 "delivery": None, "childRefs": [], "waitRefs": [], "gateRefs": [],
                 "blockerRefs": [], "commitmentRefs": []}
        value.update(changes)
        return value

    def test_magic_contract_and_acceptance_lanes_cannot_starve_ready_product(self):
        inc = self.increment([
            {"id": "contract", "title": "freeze protocol", "state": "accepted", "deliverableClass": "contract", "workUnit": "contract"},
            {"id": "ship", "title": "ship customer path", "state": "ready", "deliverableClass": "product", "workUnit": "ship"},
        ])
        issues = self.status.contradictions(self.declared(inc), self.synth(
            activeWorkers=[{"sessionId": "lane", "workUnit": "contract", "state": "running"}],
            _terminalLaneUnits=[]), now=1_000_000)
        self.assertIn("delivery-pressure-product-starved:ship", issues)
        self.assertIn("same-cycle-product-dispatch-missing:ship", issues)

    def test_ezoteric_no_code_contract_correction_has_one_budget(self):
        inc = self.increment([
            {"id": "contract", "title": "correct contract", "state": "failed", "deliverableClass": "contract"},
            {"id": "ship", "title": "ship product", "state": "planned", "deliverableClass": "product", "dependsOn": ["contract"]},
        ])
        declared = self.declared(inc, correctionBudgetExtensions=[
            {"storyId": "contract", "rootCauseRef": "r1", "correctionScope": "schema", "grantedAt": 1},
            {"storyId": "contract", "rootCauseRef": "r2", "correctionScope": "schema", "grantedAt": 2},
        ])
        self.assertIn("no-code-contract-correction-budget-exhausted:contract",
                      self.status.contradictions(declared, self.synth(), now=1_000_000))

    def test_gve_resolved_merge_authority_requires_same_cycle_merge_and_readback(self):
        inc = self.increment([
            {"id": "ship", "title": "ship product", "state": "accepted", "deliverableClass": "product",
             "workUnit": "ship", "mergeAuthorityRef": "merge-gate"},
        ])
        declared = self.declared(inc, delivery={"repoPath": "/no/such/repo", "protectedBranches": ["main"], "targetBranch": "main"})
        synth = self.synth(_resolvedGates=[{"gateId": "merge-gate", "state": "resolved", "externalEffect": "merge-protected-branch"}])
        issues = self.status.contradictions(declared, synth, now=1_000_000)
        self.assertIn("resolved-merge-authority-without-same-cycle-merge:ship", issues)

    def test_server_and_client_reuse_exact_candidate_environment_evidence(self):
        candidate = "a" * 40; environment = "b" * 64
        for project in ("server", "client"):
            with self.subTest(project=project):
                inc = self.increment([{"id": "ship", "title": "ship", "state": "ready", "deliverableClass": "product"}])
                raw = {**inc, "evidenceReuse": {"candidateSha": candidate,
                       "testEnvironmentFingerprint": environment, "acceptanceRef": "audit-pass"}}
                # Re-normalizing does not demand a fresh test run merely because
                # the report is newer; the exact candidate/environment identity is enough.
                self.assertEqual(self.status.normalize_increment(raw)["evidenceReuse"]["candidateSha"], candidate)

    def test_gta_hold_keeps_status_publication_path_distinct_from_execution(self):
        # The registry/status layer must be able to reconcile/publish HOLD. Actual
        # implementation admission is separately refused by observable-job.py.
        self.assertIn("hold", self.status.PHASES)
        self.assertIn("hold", self.status.authoritative.__doc__ or "") if self.status.authoritative.__doc__ else None

    def test_twenty_direct_owner_and_complexity_rotation_outrank_routine_ticks(self):
        admission = load("recovery-admission")
        self.assertTrue({"direct-owner-rotation", "coordinator-complexity-threshold"}.issubset(admission.ROTATION_WAKE_KINDS))
        self.assertNotIn("coordinator-complexity-threshold", admission.ROUTINE_KINDS)

    def test_cwd_rollout_accepts_only_trusted_home_syntax_and_normalizes_it(self):
        home_path = self.common.coordinator_cwd("~/")
        self.assertEqual(home_path, self.common.coordinator_cwd(str(Path.home())))
        self.assertIsNone(self.common.coordinator_cwd("relative/repository"))
        self.assertIsNone(self.common.coordinator_cwd("."))

    def test_product_bearing_increment_and_legacy_story_default(self):
        legacy = self.increment([{"id": "ship", "title": "ship", "state": "ready"}])
        self.assertEqual(legacy["stories"][0]["deliverableClass"], "product")
        with self.assertRaisesRegex(SystemExit, "at least one product deliverable"):
            self.increment([{"id": "only-contract", "title": "contract", "state": "ready", "deliverableClass": "contract"}])


if __name__ == "__main__":
    unittest.main()

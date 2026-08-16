# SPDX-License-Identifier: Apache-2.0
"""Fleet regressions for the bounded v3.4.36 delivery-pressure correction."""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys
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
        merged = self.increment([{"id": "ship", "title": "ship product", "state": "accepted", "deliverableClass": "product",
                                  "workUnit": "ship", "mergeSha": "a" * 40, "mergeAuthorityRef": "merge-gate"}])
        observer_only = self.declared(merged, nextActions=[{"executor": "external-observer", "storyRef": "ship"}])
        self.assertIn("resolved-merge-authority-without-same-cycle-readback:ship",
                      self.status.contradictions(observer_only, synth, now=1_000_000))

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

    def current_publish(self, root, payload):
        sessions, runtime = root / "sessions", root / "runtime"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "coord").mkdir(exist_ok=True)
        (sessions / "coord" / "session.jsonl").write_text(json.dumps({
            "id": "coord", "isArchived": False, "sessionStatus": "in_progress",
            "labels": ["agent-role::coordinator", "protocol-version::3.4.36"]}) + "\n")
        (runtime / "coordinators").mkdir(parents=True, exist_ok=True)
        (runtime / "coordinators" / "demo.json").write_text(json.dumps({
            "project": "demo", "state": "authoritative", "coordinatorSessionId": "coord", "generation": 1}) + "\n")
        env = {**os.environ, "CRAFT_WORKSPACE": str(root), "CRAFT_SESSIONS": str(sessions),
               "CRAFT_RUNTIME": str(runtime), "CRAFT_TEST_NOW_MS": "1000000"}
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "coordinator-status.py"), "publish",
                               "--project", "demo", "--session", "coord", "--generation", "1",
                               "--json", json.dumps(payload), "--apply"], env=env, text=True, capture_output=True)

    def test_current_null_increment_requires_observed_direct_owner_planning_gate_but_legacy_is_readable(self):
        payload = {"objective": "Plan the next outcome", "phase": "executing", "nextReviewInSeconds": 3600}
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            denied = self.current_publish(root, payload)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("requires a Product Increment", denied.stderr)
            (root / "runtime" / "owner-gates" / "demo").mkdir(parents=True)
            (root / "runtime" / "owner-gates" / "demo" / "planning.json").write_text(json.dumps({
                "gateId": "planning", "state": "resolved", "authority": "direct-owner",
                "externalEffect": "product-direction-decision", "decisionKey": "planning-only:next-outcome"}) + "\n")
            allowed = self.current_publish(root, {**payload, "planningOnlyAuthorityRef": "planning"})
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            stored = json.loads((root / "runtime" / "coordinator-status" / "demo.json").read_text())
            self.assertEqual(stored["protocolVersion"], "3.4.36")
            stored.pop("protocolVersion")
            stored["declared"].pop("planningOnlyAuthorityRef", None)
            (root / "runtime" / "coordinator-status" / "demo.json").write_text(json.dumps(stored) + "\n")
            shown = subprocess.run([sys.executable, str(ROOT / "scripts" / "coordinator-status.py"), "show", "--project", "demo"],
                                   env={**os.environ, "CRAFT_WORKSPACE": str(root), "CRAFT_SESSIONS": str(root / "sessions"),
                                        "CRAFT_RUNTIME": str(root / "runtime"), "CRAFT_TEST_NOW_MS": "1000000"}, text=True, capture_output=True)
            self.assertEqual(shown.returncode, 0)
            self.assertNotIn("v3.4.36-status-missing-product-outcome", json.loads(shown.stdout)["issues"])

    def test_unknown_or_outside_root_lane_is_housekeeping_and_cannot_mask_product(self):
        inc = self.increment([{"id": "ship", "title": "Ship", "state": "ready", "deliverableClass": "product", "workUnit": "ship"}])
        declared = self.declared(inc, delivery={"repoPath": "/delivery", "worktreeRoots": ["/delivery/.worktrees"]})
        for worker in ({"sessionId": "unknown", "workUnit": "untracked", "worktree": "/delivery/.worktrees/x", "state": "running"},
                       {"sessionId": "outside", "workUnit": "ship", "worktree": "/other/x", "state": "running"}):
            with self.subTest(worker=worker["sessionId"]):
                issues = self.status.contradictions(declared, self.synth(activeWorkers=[worker]), now=1_000_000)
                self.assertIn("idle-ready-work:ship", issues)
                self.assertIn("product-work-starved:ship", issues)
                self.assertTrue(any(issue.startswith("housekeeping-starves-product:") for issue in issues))

    def test_arbitrary_merge_sha_cannot_complete_under_authority_without_verified_delivery(self):
        criterion = "customer completes checkout"; sha = "a" * 40
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); inbox = root / "runtime" / "coordinator-inbox" / "demo"; inbox.mkdir(parents=True)
            items = {
                "candidate": {"eventKey": "candidate", "revision": 1, "fingerprint": "1" * 64, "kind": "terminal-handoff", "evidence": ["candidate"], "coordinatorGeneration": 1},
                "accept": {"eventKey": "accept", "revision": 1, "fingerprint": "2" * 64, "kind": "audit-verdict", "senderRole": "auditor", "evidence": ["pass"], "coordinatorGeneration": 1},
                "release": {"eventKey": "release", "revision": 1, "fingerprint": "3" * 64, "kind": "observer-terminal", "sender": "watcher", "workUnit": "ship", "evidence": [f"merged-main:{sha}"], "coordinatorGeneration": 1},
                "demo": {"eventKey": "demo", "revision": 1, "fingerprint": "4" * 64, "kind": "terminal-handoff", "evidence": [criterion], "coordinatorGeneration": 1},
            }
            for key, item in items.items(): (inbox / f"{key}.json").write_text(json.dumps(item) + "\n")
            waits = root / "runtime" / "external-waits"; waits.mkdir(parents=True)
            (waits / "readback.json").write_text(json.dumps({"project": "demo", "coordinatorSessionId": "coord", "watcherSessionId": "watcher", "workUnit": "ship", "state": "terminal"}) + "\n")
            gates = root / "runtime" / "owner-gates" / "demo"; gates.mkdir(parents=True)
            (gates / "merge.json").write_text(json.dumps({"gateId": "merge", "state": "resolved", "authority": "direct-owner", "externalEffect": "merge-protected-branch", "workUnit": "ship"}) + "\n")
            binding = lambda key: {field: items[key][field] for field in ("eventKey", "revision", "fingerprint")}
            payload = {"objective": "Ship", "phase": "complete", "nextActions": [], "completedOutcomes": [{"summary": "done", "evidenceRef": "candidate"}],
                "productIncrement": {"id": "i", "stage": "complete", "riskTier": "low", "demonstrationCriterion": criterion,
                "stories": [{"id": "ship", "title": "Ship", "state": "accepted", "deliverableClass": "product", "workUnit": "ship", "acceptanceRef": "accept", "mergeSha": sha, "mergeAuthorityRef": "merge"}],
                "completionEvidence": {"integratedCandidateRef": binding("candidate"), "acceptanceRef": binding("accept"), "releaseReadbackRef": binding("release"), "demonstrationRef": binding("demo")}}}
            result = self.current_publish(root, payload)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires delivery.repoPath", result.stderr)
            # The exact gate, merged-main readback, and ancestor proof form a valid
            # completion only after a real remote-tracking target is available.
            repo = root / "repo"; repo.mkdir()
            for command in (("git", "init"), ("git", "config", "user.email", "tests@example.invalid"),
                            ("git", "config", "user.name", "Tests")):
                subprocess.run(command, cwd=repo, check=True, capture_output=True)
            (repo / "file").write_text("delivered")
            subprocess.run(("git", "add", "file"), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "commit", "-m", "delivery"), cwd=repo, check=True, capture_output=True)
            merged = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(("git", "update-ref", "refs/remotes/origin/main", merged), cwd=repo, check=True, capture_output=True)
            payload["delivery"] = {"repoPath": str(repo), "targetBranch": "main", "protectedBranches": ["main"]}
            payload["productIncrement"]["stories"][0]["mergeSha"] = merged
            items["release"]["evidence"] = [f"merged-main:{merged}"]
            (inbox / "release.json").write_text(json.dumps(items["release"]) + "\n")
            valid = self.current_publish(root, payload)
            self.assertEqual(valid.returncode, 0, valid.stderr)

    def test_evidence_reuse_needs_observed_candidate_and_environment_identity(self):
        candidate, environment = "a" * 40, "b" * 64
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "runtime" / "coordinator-inbox" / "demo").mkdir(parents=True)
            (root / "runtime" / "coordinator-inbox" / "demo" / "audit.json").write_text(json.dumps({
                "eventKey": "audit", "coordinatorGeneration": 1,
                "evidence": [f"candidateSha:{candidate}", f"testEnvironmentFingerprint:{environment}"]}) + "\n")
            payload = {"objective": "Ship", "phase": "executing", "nextReviewInSeconds": 3600,
                       "productIncrement": {"id": "i", "stage": "building", "riskTier": "low",
                       "demonstrationCriterion": "customer completes checkout",
                       "stories": [{"id": "ship", "title": "Ship", "state": "ready", "deliverableClass": "product"}],
                       "evidenceReuse": {"candidateSha": candidate, "testEnvironmentFingerprint": environment, "acceptanceRef": "audit"}}}
            valid = self.current_publish(root, payload)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            payload["productIncrement"]["evidenceReuse"]["testEnvironmentFingerprint"] = "c" * 64
            invalid = self.current_publish(root, payload)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("must bind observed candidate SHA", invalid.stderr)


if __name__ == "__main__":
    unittest.main()

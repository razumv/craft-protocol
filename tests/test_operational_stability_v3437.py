# SPDX-License-Identifier: Apache-2.0
"""Adversarial v3.4.37 operational-stability regressions (GVE/Client/Server/Magic/Twenty)."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)  # type: ignore
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"; self.sessions = self.workspace / "sessions"; self.runtime = self.root / "runtime"
        self.sessions.mkdir(parents=True); self.now = 1_000_000_000
        self.env = {**os.environ, "CRAFT_WORKSPACE": str(self.workspace), "CRAFT_SESSIONS": str(self.sessions),
                    "CRAFT_RUNTIME": str(self.runtime), "CRAFT_TEST_NOW_MS": str(self.now)}

    def tearDown(self): self.temp.cleanup()

    def put(self, path: Path, value: dict):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value) + "\n")

    def manifest(self, sid: str, role: str, *, status="in_progress", archived=False, labels=None, **extra):
        self.put(self.sessions / sid / "session.jsonl", {"id": sid, "sessionStatus": status, "isArchived": archived,
                 "labels": [f"agent-role::{role}", *(labels or [])], **extra})

    def cli(self, name: str, *args: str, ok=True):
        proc = subprocess.run([sys.executable, str(SCRIPTS / name), *args], env=self.env, text=True,
                              capture_output=True, timeout=60)
        if ok and proc.returncode:
            self.fail(f"{name} failed {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")
        data = json.loads(proc.stdout) if proc.stdout else None
        return proc, data


class OwnerPlanReceiptTests(Base):
    def test_magic_plan_receipt_is_authenticated_byte_bound_and_never_authorizes_external_effects(self):
        self.manifest("owner", "owner")
        self.env["CRAFT_OWNER_SESSION_ID"] = "owner"
        plan = self.root / "plan.md"; plan.write_text("test the reversible contract\n")
        excluded = ["deploy", "irreversible-data-change", "merge-protected-branch", "physical-or-remote-access", "publish-release", "spend-money-or-entitlement", "use-credential"]
        approve = ["approve", "--project", "magicmarkets", "--receipt-id", "magic-plan-1", "--owner-session", "owner", "--scope", "increment:magic-q3", "--plan-file", str(plan), "--effect", "test-only", "--ttl-seconds", "120"]
        for effect in excluded: approve += ["--exclude", effect]
        _, approved = self.cli("owner-plan-receipt.py", *approve)
        self.assertEqual(approved["receipt"]["effects"], ["test-only"])
        _, checked = self.cli("owner-plan-receipt.py", "check", "--project", "magicmarkets", "--receipt-id", "magic-plan-1", "--scope", "increment:magic-q3", "--plan-file", str(plan), "--effect", "test-only")
        self.assertTrue(checked["authorized"])
        _, boundary = self.cli("owner-gate.py", "check", "--project", "magicmarkets", "--action", "implement", "--plan-receipt", "magic-plan-1", "--plan-scope", "increment:magic-q3", "--plan-file", str(plan), "--plan-effect", "test-only")
        self.assertTrue(boundary["allowed"])
        plan.write_text("different bytes\n")
        denied_boundary, boundary = self.cli("owner-gate.py", "check", "--project", "magicmarkets", "--action", "implement", "--plan-receipt", "magic-plan-1", "--plan-scope", "increment:magic-q3", "--plan-file", str(plan), "--plan-effect", "test-only", ok=False)
        self.assertIn("receipt-plan-bytes-mismatch", boundary["planReceiptRefusals"])
        denied, data = self.cli("owner-plan-receipt.py", "check", "--project", "magicmarkets", "--receipt-id", "magic-plan-1", "--scope", "increment:magic-q3", "--plan-file", str(plan), "--effect", "test-only", ok=False)
        self.assertIn("receipt-plan-bytes-mismatch", data["refusals"])
        denied, data = self.cli("owner-plan-receipt.py", "approve",  "--project", "magicmarkets", "--receipt-id", "bad", "--owner-session", "owner", "--scope", "increment:magic-q3", "--plan-file", str(plan), "--effect", "deploy", ok=False)
        self.assertIn("dangerous", data["error"])
        self.cli("owner-plan-receipt.py", "revoke", "--project", "magicmarkets", "--receipt-id", "magic-plan-1", "--owner-session", "owner", "--reason", "scope changed")


class RotationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.registry = load("coordinator-registry")

    def test_twenty_rotation_is_current_session_grace_hysteretic_and_context_error_bypasses_grace(self):
        record = {"coordinatorSessionId": "twenty-new", "claimedAt": 1000,
                  "rotationMetric": {"sessionId": "twenty-new", "startedAt": 1000, "pressureActive": False}}
        fresh = self.registry.rotation_metric(record, {"messageCount": 700, "tokenUsage": {"totalTokens": 300000}}, 1001)
        self.assertTrue(fresh["inGrace"]); self.assertEqual(fresh["reasons"], [])
        error = self.registry.rotation_metric(record, {"messageCount": 1, "lastError": "request buffer context exhausted"}, 1001)
        self.assertIn("context-error", error["reasons"])
        record["rotationMetric"]["pressureActive"] = True
        mature = self.registry.rotation_metric(record, {"messageCount": 460, "tokenUsage": {"totalTokens": 1}}, 1_000_000)
        self.assertIn("messages=460", mature["reasons"])


class TransferAndEvidenceTests(Base):
    def registry(self, project="gve", sid="gve-old", generation=4, **extra):
        self.put(self.runtime / "coordinators" / f"{project}.json", {"schemaVersion": 1, "project": project, "projectId": "native-gve",
                 "coordinatorCwd": str(self.root.resolve()), "coordinatorSessionId": sid, "generation": generation, "state": "rotating",
                 "successorSessionId": "gve-new", "successorIdentity": {"id": "gve-new", "workspaceRootPath": None,
                 "projectId": "native-gve", "role": "coordinator", "connection": "chatgpt-plus", "model": "pi/gpt-5.6-sol"},
                 "activeChildren": [], **extra})

    def policy(self):
        self.put(self.runtime / "reporting-policy.json", {"schemaVersion": 1, "mode": "pull-only", "ownerFacingSessionId": "owner", "configuredAt": 1,
                 "interception": "unavailable", "detection": "best-effort-session-transcript"})

    def test_gve_transfer_discovers_live_and_needs_review_children_without_leases(self):
        self.policy()
        labels = ["coordinators", "project::gve", "protocol-version::3.4.37"]
        self.manifest("gve-old", "coordinator", labels=labels, name="[gve] Coordinator v3.4.37", projectId="native-gve", workingDirectory=str(self.root),
                      llmConnection="chatgpt-plus", model="pi/gpt-5.6-sol", permissionMode="allow-all")
        self.manifest("gve-new", "coordinator", labels=labels, name="[gve] Coordinator v3.4.37", projectId="native-gve", workingDirectory=str(self.root),
                      llmConnection="chatgpt-plus", model="pi/gpt-5.6-sol", permissionMode="allow-all")
        self.manifest("gve-worker", "worker", labels=["parent-session::gve-old", "project::gve", "work-unit::ship", "attempt::1"])
        self.manifest("gve-auditor", "auditor", status="needs-review", labels=["parent-session::gve-old", "project::gve", "work-unit::audit", "attempt::1"])
        self.registry()
        _, out = self.cli("coordinator-registry.py", "accept-transfer", "--project", "gve", "--session", "gve-new", "--expected-generation", "4")
        self.assertEqual(out["record"]["activeChildren"], ["gve-auditor", "gve-worker"])
        self.assertEqual(out["record"]["transferDiscovery"]["predecessorSessionId"], "gve-old")

    def init_delivery_repo(self):
        repo = self.root / "repo"; repo.mkdir()
        for cmd in (("git", "init"), ("git", "config", "user.email", "test@example.invalid"), ("git", "config", "user.name", "Test")):
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
        (repo / "delivered").write_text("yes"); subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True)
        subprocess.run(("git", "commit", "-m", "delivery"), cwd=repo, check=True, capture_output=True)
        sha = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(("git", "update-ref", "refs/remotes/origin/main", sha), cwd=repo, check=True, capture_output=True)
        return repo, sha

    def test_client_server_exact_candidate_handoff_acceptance_merge_and_readback_are_consumed(self):
        self.manifest("coord", "coordinator", labels=["protocol-version::3.4.37"])
        self.put(self.runtime / "coordinators" / "client.json", {"project": "client", "state": "authoritative", "coordinatorSessionId": "coord", "generation": 1})
        repo, merge = self.init_delivery_repo(); candidate = "a" * 40
        criterion = "customer completes checkout"
        fingerprint_chars = {"candidate": "a", "accept": "b", "release": "c", "demo": "d"}
        item = lambda key, kind, evidence, **more: {"eventKey": key, "revision": 1, "fingerprint": (fingerprint_chars[key] * 64), "kind": kind,
                                                     "evidence": evidence, "coordinatorGeneration": 1, **more}
        inbox = self.runtime / "coordinator-inbox" / "client"
        self.put(inbox / "candidate.json", item("candidate", "terminal-handoff", [f"candidateSha:{candidate}"], sender="worker", workUnit="ship"))
        self.put(inbox / "accept.json", item("accept", "audit-verdict", [f"candidateSha:{candidate}", "verdict:PASS"], senderRole="auditor", workUnit="ship"))
        self.put(inbox / "release.json", item("release", "observer-terminal", [f"candidateSha:{candidate}", f"merged-main:{merge}"], sender="watch", workUnit="ship"))
        self.put(inbox / "demo.json", item("demo", "terminal-handoff", [criterion], sender="worker", workUnit="ship"))
        self.put(self.runtime / "external-waits" / "readback.json", {"waitId": "readback", "project": "client", "coordinatorSessionId": "coord", "watcherSessionId": "watch", "workUnit": "ship", "state": "terminal"})
        self.put(self.runtime / "owner-gates" / "client" / "merge.json", {"gateId": "merge", "state": "resolved", "externalEffect": "merge-protected-branch", "workUnit": "ship"})
        bind = lambda key: {"eventKey": key, "revision": 1, "fingerprint": fingerprint_chars[key] * 64}
        payload = {"objective": "Ship", "phase": "complete", "nextActions": [], "completedOutcomes": [{"summary": "done", "evidenceRef": "candidate"}],
          "delivery": {"repoPath": str(repo), "targetBranch": "main", "protectedBranches": ["main"]},
          "productIncrement": {"id": "client-release", "stage": "complete", "riskTier": "medium", "demonstrationCriterion": criterion,
            "stories": [{"id": "ship", "title": "Ship", "state": "accepted", "deliverableClass": "product", "workUnit": "ship", "acceptanceRef": "accept", "mergeSha": merge, "mergeAuthorityRef": "merge"}],
            "completionEvidence": {"integratedCandidateRef": bind("candidate"), "acceptanceRef": bind("accept"), "releaseReadbackRef": bind("release"), "demonstrationRef": bind("demo")}}}
        _, published = self.cli("coordinator-status.py", "publish", "--project", "client", "--session", "coord", "--generation", "1", "--json", json.dumps(payload), "--apply")
        self.assertEqual(published["record"]["protocolVersion"], "3.4.37")
        bad = json.loads(json.dumps(payload)); self.put(inbox / "accept.json", item("accept", "audit-verdict", ["candidateSha:" + "b" * 40, "verdict:PASS"], senderRole="auditor", workUnit="ship"))
        proc, _ = self.cli("coordinator-status.py", "publish", "--project", "client", "--session", "coord", "--generation", "1", "--json", json.dumps(bad), "--apply", ok=False)
        self.assertIn("exact candidateSha", proc.stderr)

    def test_server_healthy_with_predecessor_archive_debt_is_not_a_context_failure(self):
        self.manifest("server-new", "coordinator", labels=["protocol-version::3.4.37"])
        self.manifest("server-old", "coordinator", labels=["protocol-version::3.4.36"])
        self.manifest("server-worker", "worker", labels=["parent-session::server-new", "work-unit::ship", "attempt::1"])
        self.put(self.runtime / "coordinators" / "server.json", {"project": "server", "state": "authoritative", "coordinatorSessionId": "server-new", "generation": 2,
                 "predecessorSessionId": "server-old", "leaseExpiresAt": self.now + 100000})
        self.put(self.runtime / "worker-leases" / "server-worker.json", {"sessionId": "server-worker", "parentSessionId": "server-new", "role": "worker", "workUnit": "ship", "state": "running"})
        payload = {"objective": "Ship", "phase": "executing", "childRefs": ["server-worker"], "nextReviewInSeconds": 3600,
                   "githubSync": {"issue": "owner/server#1", "commentRef": "comment-1", "projectField": "building", "syncedStage": "building", "syncedAt": self.now},
                   "productIncrement": {"id": "server-work", "stage": "building", "riskTier": "low", "demonstrationCriterion": "customer sees response",
                   "stories": [{"id": "ship", "title": "Ship", "state": "executing", "deliverableClass": "product"}]}}
        self.cli("coordinator-status.py", "publish", "--project", "server", "--session", "server-new", "--generation", "2", "--json", json.dumps(payload), "--apply")
        _, shown = self.cli("coordinator-status.py", "show", "--project", "server")
        self.assertEqual(shown["classification"], "healthy-with-maintenance-debt", shown)
        self.assertIn("maintenance-debt:predecessor-not-archived:server-old", shown["issues"])


class ReleaseClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.release = load("release-closure")

    def test_v3436_missing_authenticated_github_release_can_never_close_as_latest(self):
        old = os.environ.pop("CRAFT_GH_CLI", None)
        try:
            result = self.release.verify(ROOT, "3.4.36")
        finally:
            if old is not None: os.environ["CRAFT_GH_CLI"] = old
        self.assertFalse(result["closed"])
        self.assertIn("github-auth-cli-unavailable", result["errors"])
        self.assertIn("github-release-uncheckable-without-auth", result["errors"])


if __name__ == "__main__": unittest.main()

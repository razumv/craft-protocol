# SPDX-License-Identifier: Apache-2.0
"""Adversarial v3.4.37 operational-stability regressions (GVE/Client/Server/Magic/Twenty)."""
from __future__ import annotations
import hashlib
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

    def test_plan_derived_dangerous_effect_needs_receipt_and_exact_direct_gate(self):
        denied, result = self.cli("owner-gate.py", "check", "--project", "magicmarkets", "--work-unit", "ship", "--action", "deploy", "--external-effect", "deploy", "--authority-source", "plan-derived", ok=False)
        self.assertIn("plan-derived-dangerous-effect-without-plan-receipt", result["authorityRefusals"])
        self.cli("owner-gate.py", "create", "--project", "magicmarkets", "--gate", "deploy-ship", "--work-unit", "ship", "--question", "Deploy exactly ship?", "--choices", "YES", "--owner-only-category", "high-blast-radius-public-release", "--external-effect", "deploy", "--scope", "deploy")
        self.cli("owner-gate.py", "resolve", "--project", "magicmarkets", "--gate", "deploy-ship", "--choice", "YES", "--authority", "direct-owner", "--evidence", "owner message")
        _, allowed = self.cli("owner-gate.py", "check", "--project", "magicmarkets", "--work-unit", "ship", "--action", "deploy", "--external-effect", "deploy")
        self.assertTrue(allowed["allowed"])


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
        worker_cwd = self.root / "gve-worker-worktree"; worker_cwd.mkdir()
        auditor_cwd = self.root / "gve-auditor-worktree"; auditor_cwd.mkdir()
        self.manifest("gve-worker", "worker", workingDirectory=str(worker_cwd.resolve()), labels=["parent-session::gve-old", "project::gve", "work-unit::ship", "attempt::1"])
        self.manifest("gve-auditor", "auditor", status="needs-review", workingDirectory=str(auditor_cwd.resolve()), labels=["parent-session::gve-old", "project::gve", "work-unit::audit", "attempt::1"])
        self.registry()
        _, out = self.cli("coordinator-registry.py", "accept-transfer", "--project", "gve", "--session", "gve-new", "--expected-generation", "4")
        self.assertEqual(out["record"]["activeChildren"], ["gve-auditor", "gve-worker"])
        self.assertEqual(out["record"]["transferDiscovery"]["predecessorSessionId"], "gve-old")

    def test_transfer_child_cwd_refuses_missing_and_shared_then_adopts_unique_existing(self):
        self.policy()
        labels = ["coordinators", "project::gve", "protocol-version::3.4.37"]
        for sid in ("gve-old", "gve-new"):
            self.manifest(sid, "coordinator", labels=labels, name="[gve] Coordinator v3.4.37", projectId="native-gve", workingDirectory=str(self.root), llmConnection="chatgpt-plus", model="pi/gpt-5.6-sol", permissionMode="allow-all")
        child_labels = ["parent-session::gve-old", "project::gve", "work-unit::audit", "attempt::1"]

        # The final audit finding: a live auditor without a CWD cannot be adopted.
        self.manifest("gve-auditor", "auditor", labels=child_labels)
        self.registry()
        proc, _ = self.cli("coordinator-registry.py", "accept-transfer", "--project", "gve", "--session", "gve-new", "--expected-generation", "4", ok=False)
        self.assertIn("working directory missing", proc.stderr)

        # A child may not inherit the parent repository CWD either.
        self.manifest("gve-auditor", "auditor", workingDirectory=str(self.root.resolve()), labels=child_labels)
        self.registry()
        proc, _ = self.cli("coordinator-registry.py", "accept-transfer", "--project", "gve", "--session", "gve-new", "--expected-generation", "4", ok=False)
        self.assertIn("shared worktree refusal", proc.stderr)

        # A canonical, existing, independently owned worktree remains admissible.
        worktree = self.root / "gve-auditor-worktree"; worktree.mkdir()
        self.manifest("gve-auditor", "auditor", workingDirectory=str(worktree.resolve()), labels=child_labels)
        self.registry()
        _, out = self.cli("coordinator-registry.py", "accept-transfer", "--project", "gve", "--session", "gve-new", "--expected-generation", "4")
        self.assertEqual(out["record"]["activeChildren"], ["gve-auditor"])

    def test_transfer_refuses_preexisting_archived_or_foreign_child(self):
        self.policy()
        labels = ["coordinators", "project::gve", "protocol-version::3.4.37"]
        for sid in ("gve-old", "gve-new"):
            self.manifest(sid, "coordinator", labels=labels, name="[gve] Coordinator v3.4.37", projectId="native-gve", workingDirectory=str(self.root), llmConnection="chatgpt-plus", model="pi/gpt-5.6-sol", permissionMode="allow-all")
        self.manifest("stale", "worker", archived=True, labels=["parent-session::gve-old", "project::gve", "work-unit::ship", "attempt::1"])
        self.registry()
        row = json.loads((self.runtime / "coordinators" / "gve.json").read_text()); row["activeChildren"] = ["stale"]
        self.put(self.runtime / "coordinators" / "gve.json", row)
        proc, _ = self.cli("coordinator-registry.py", "accept-transfer", "--project", "gve", "--session", "gve-new", "--expected-generation", "4", ok=False)
        self.assertIn("inactive-or-archived", proc.stderr)

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
        certificate_path = self.runtime / "completion-certificates" / "client" / "ship-candidate.json"
        self.put(certificate_path, {"project": "client", "workUnit": "ship", "candidateSha": candidate, "auditedSha": candidate,
                                    "auditorSessionId": "audit", "auditVerdict": "PASS", "requiredCiRunIds": ["ci-1"],
                                    "requiredCiAllSuccess": True, "mergeSha": merge, "headUnchanged": True,
                                    "mergedMainRunIds": ["readback-1"], "mergedMainAllSuccess": True, "unresolvedGates": []})
        certificate_fingerprint = hashlib.sha256(certificate_path.read_bytes()).hexdigest()
        bind = lambda key: {"eventKey": key, "revision": 1, "fingerprint": fingerprint_chars[key] * 64}
        payload = {"objective": "Ship", "phase": "complete", "nextActions": [], "completedOutcomes": [{"summary": "done", "evidenceRef": "candidate"}],
          "delivery": {"repoPath": str(repo), "targetBranch": "main", "protectedBranches": ["main"]},
          "productIncrement": {"id": "client-release", "stage": "complete", "riskTier": "medium", "demonstrationCriterion": criterion,
            "stories": [{"id": "ship", "title": "Ship", "state": "accepted", "deliverableClass": "product", "workUnit": "ship", "acceptanceRef": "accept", "mergeSha": merge, "mergeAuthorityRef": "merge"}],
            "completionEvidence": {"integratedCandidateRef": bind("candidate"), "acceptanceRef": bind("accept"), "releaseReadbackRef": bind("release"), "demonstrationRef": bind("demo"), "certificateRef": {"certificateId": "ship-candidate", "fingerprint": certificate_fingerprint}}}}
        _, published = self.cli("coordinator-status.py", "publish", "--project", "client", "--session", "coord", "--generation", "1", "--json", json.dumps(payload), "--apply")
        self.assertEqual(published["record"]["protocolVersion"], "3.4.37")
        missing = json.loads(json.dumps(payload)); missing["productIncrement"]["completionEvidence"].pop("certificateRef")
        proc, _ = self.cli("coordinator-status.py", "publish", "--project", "client", "--session", "coord", "--generation", "1", "--json", json.dumps(missing), "--apply", ok=False)
        self.assertIn("requires consumed completion certificate", proc.stderr)
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

    def test_v3436_local_refs_or_fake_cli_environment_can_never_close_as_latest(self):
        old = os.environ.get("CRAFT_GH_CLI")
        os.environ["CRAFT_GH_CLI"] = "/tmp/attacker-controlled-gh"
        try:
            result = self.release.verify(ROOT, "3.4.36")
        finally:
            if old is None: os.environ.pop("CRAFT_GH_CLI", None)
            else: os.environ["CRAFT_GH_CLI"] = old
        self.assertFalse(result["closed"])
        self.assertIn("github-auth-token-file-unavailable", result["errors"])
        self.assertIsNone(result["remoteMainSha"])

    def test_release_closure_rejects_noncanonical_origin_and_unsafe_token_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); repo = root / "repo"; repo.mkdir()
            for cmd in (("git", "init"), ("git", "remote", "add", "origin", "https://example.invalid/fake/repo.git")):
                subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
            token = root / "token"; token.write_text("not-a-real-token")
            token.chmod(0o644)
            result = self.release.verify(repo, "3.4.37", str(token))
        self.assertFalse(result["closed"])
        self.assertIn("github-origin-identity-unreadable", result["errors"])
        self.assertIn("github-auth-token-file-unavailable", result["errors"])

    def receipt_body(self, **changes):
        sha = "a" * 40
        receipt = {"schemaVersion": 1, "version": "3.4.37", "tag": "v3.4.37", "commit": sha,
                   "state": "adopted", "ownerFacingOrchestratorSessionId": "owner-orchestrator-session",
                   "adoptedAt": "2026-08-17T10:14:00Z",
                   "adoptions": [{"project": "client", "coordinatorSessionId": "client-coordinator"},
                                  {"project": "server", "coordinatorSessionId": "server-coordinator"}]}
        receipt.update(changes)
        return "Fleet rollout completed.\n\n```json\n" + json.dumps(receipt, sort_keys=True) + "\n```\n"

    def test_release_body_receipt_binds_exact_release_and_canonical_fleet_adoption(self):
        sha = "a" * 40
        self.assertEqual(self.release.adoption_errors(self.receipt_body(), "v3.4.37", "3.4.37", sha), [])
        for field, value in (("schemaVersion", 2), ("version", "3.4.36"), ("tag", "v3.4.36"),
                             ("commit", "b" * 40), ("state", "draft"),
                             ("ownerFacingOrchestratorSessionId", " "), ("adoptedAt", ""),
                             ("adoptions", [{"project": "server", "coordinatorSessionId": "server-coordinator"},
                                             {"project": "client", "coordinatorSessionId": "client-coordinator"}]),
                             ("adoptions", [{"project": "client", "coordinatorSessionId": "client-coordinator"},
                                             {"project": "client", "coordinatorSessionId": "other-coordinator"}])):
            self.assertEqual(self.release.adoption_errors(self.receipt_body(**{field: value}), "v3.4.37", "3.4.37", sha),
                             ["fleet-adoption-receipt-mismatch"], field)
        duplicate = self.receipt_body().replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1')
        self.assertEqual(self.release.adoption_errors(duplicate, "v3.4.37", "3.4.37", sha),
                         ["fleet-adoption-receipt-missing-or-invalid"])
        doubled = self.receipt_body() + self.receipt_body()
        self.assertEqual(self.release.adoption_errors(doubled, "v3.4.37", "3.4.37", sha),
                         ["fleet-adoption-receipt-missing-or-invalid"])

    def test_release_receipt_replaces_impossible_tagged_tree_self_reference(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            for cmd in (("git", "init"), ("git", "config", "user.email", "test@example.invalid"),
                        ("git", "config", "user.name", "Test")):
                subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
            (repo / "payload").write_text("release payload\n")
            subprocess.run(("git", "add", "payload"), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "commit", "-m", "payload"), cwd=repo, check=True, capture_output=True)
            tag_sha = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            receipt = {"schemaVersion": 1, "version": "3.4.37", "tag": "v3.4.37", "commit": tag_sha, "state": "adopted"}
            adoption_file = repo / ".craft-protocol" / "adoptions" / "v3.4.37.json"; adoption_file.parent.mkdir(parents=True)
            adoption_file.write_text(json.dumps(receipt) + "\n")
            subprocess.run(("git", "add", ".craft-protocol"), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "commit", "-m", "self reference"), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "tag", "-a", "v3.4.37", "-m", "release"), cwd=repo, check=True, capture_output=True)
            self.assertNotEqual(subprocess.run(("git", "rev-parse", "v3.4.37^{}"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip(), tag_sha)
        self.assertEqual(self.release.adoption_errors(self.receipt_body(), "v3.4.37", "3.4.37", "a" * 40), [])

    def test_receipt_is_required_only_after_release_is_published_and_latest(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw); subprocess.run(("git", "init"), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "remote", "add", "origin", "https://github.com/owner/repo.git"), cwd=repo, check=True, capture_output=True)
            token = repo / "token"; token.write_text("test-token"); token.chmod(0o600)
            original = self.release.api, self.release.remote_refs, self.release.remote_manifest_errors, self.release.remote_sha256, self.release.installer_errors
            sha = "a" * 40
            try:
                self.release.remote_refs = lambda *_args: (sha, "b" * 40, sha)
                self.release.remote_manifest_errors = lambda *_args: []
                self.release.remote_sha256 = lambda *_args: "c" * 64
                self.release.installer_errors = lambda *_args: []
                def result(draft, latest_id=1):
                    release = {"id": 1, "tag_name": "v3.4.37", "draft": draft, "prerelease": False,
                               "published_at": "2026-08-17T10:14:00Z", "target_commitish": sha,
                               "assets": [{"name": name, "digest": "sha256:" + "c" * 64} for name in ("manifest.sha256", "install.sh")]}
                    latest = {**release, "id": latest_id}
                    self.release.api = lambda _token, endpoint: release if endpoint.endswith("releases/tags/v3.4.37") else (latest if endpoint.endswith("releases/latest") else None)
                    return self.release.verify(repo, "3.4.37", str(token))["errors"]
                self.assertNotIn("fleet-adoption-receipt-missing-or-invalid", result(True))
                self.assertNotIn("fleet-adoption-receipt-missing-or-invalid", result(False, latest_id=2))
                self.assertIn("fleet-adoption-receipt-missing-or-invalid", result(False))
            finally:
                (self.release.api, self.release.remote_refs, self.release.remote_manifest_errors,
                 self.release.remote_sha256, self.release.installer_errors) = original

    def test_github_contents_wrapped_base64_is_strict_but_accepts_realistic_line_wraps(self):
        payload = b'{"release":"v3.4.37", "files": ["README.md", "manifest.sha256"]}' + bytes([10])
        wrapped = __import__("base64").encodebytes(payload).decode("ascii")
        self.assertEqual(self.release.decode_github_content(wrapped), payload)
        self.assertIsNone(self.release.decode_github_content(wrapped.replace(chr(10), chr(160), 1)))
        self.assertIsNone(self.release.decode_github_content(wrapped[:-2] + "$$"))

    def test_remote_manifest_requires_exact_local_coverage_and_rejects_extra_or_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw); a = b"a"; b = b"b"
            line = bytes([10])
            local = (f"{hashlib.sha256(a).hexdigest()}  a.txt".encode() + line
                     + f"{hashlib.sha256(b).hexdigest()}  b.txt".encode() + line)
            (repo / "manifest.sha256").write_bytes(local)
            original = self.release.remote_file
            try:
                def check(remote_manifest):
                    files = {"manifest.sha256": remote_manifest, "a.txt": a, "b.txt": b}
                    self.release.remote_file = lambda *_args: files.get(_args[2])
                    return self.release.remote_manifest_errors(repo, "token", "owner/repo", "v3.4.37")
                partial = f"{hashlib.sha256(a).hexdigest()}  a.txt".encode() + line
                extra = local + f"{hashlib.sha256(b).hexdigest()}  extra.txt".encode() + line
                duplicate = local + f"{hashlib.sha256(a).hexdigest()}  a.txt".encode() + line
                self.assertEqual(check(partial), ["remote-manifest-coverage-mismatch"])
                self.assertEqual(check(extra), ["remote-manifest-coverage-mismatch"])
                self.assertEqual(check(duplicate), ["remote-manifest-invalid"])
            finally:
                self.release.remote_file = original


class ReportingPermitTests(Base):
    def policy(self):
        self.put(self.runtime / "reporting-policy.json", {"schemaVersion": 1, "mode": "pull-only", "ownerFacingSessionId": "owner", "configuredAt": 1,
                 "interception": "unavailable", "detection": "best-effort-session-transcript"})

    def event(self, sid, event_id, timestamp, **extra):
        path = self.sessions / sid / "session.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": event_id, "timestamp": timestamp, **extra}) + chr(10))

    def setup_sessions(self, *coordinators):
        self.manifest("owner", "owner")
        self.event("owner", "owner-query", self.now - 10, type="user", content="What is the current product status?")
        for sid in coordinators:
            self.manifest(sid, "coordinator", labels=["coordinators", "project::p", "protocol-version::3.4.37"],
                          name="[p] Coordinator v3.4.37", projectId="native", workingDirectory=str(self.root),
                          llmConnection="chatgpt-plus", model="pi/gpt-5.6-sol", permissionMode="allow-all")

    def issue(self, coordinator, permit_id, ttl=60):
        return self.cli("reporting-policy.py", "issue-permit", "--coordinator", coordinator, "--owner-request-event", "owner-query",
                        "--ttl-seconds", str(ttl), "--permit-id", permit_id)[1]["permit"]

    def send(self, coordinator, event_id, message, at):
        self.event(coordinator, event_id, at, type="tool", toolName="mcp__session__send_agent_message", toolStatus="completed",
                   toolInput={"sessionId": "owner", "message": message})

    def test_unsolicited_report_is_durable_and_blocks_renewal(self):
        self.policy(); self.setup_sessions("coord")
        self.send("coord", "unsolicited", "routine status", self.now)
        _, checked = self.cli("reporting-policy.py", "check", "--session", "coord", ok=False)
        self.assertEqual(checked["violations"][0]["reason"], "unsolicited-owner-report")
        self.put(self.runtime / "coordinators" / "p.json", {"project": "p", "projectId": "native", "state": "authoritative", "coordinatorSessionId": "coord", "generation": 1, "leaseExpiresAt": self.now + 1})
        proc, _ = self.cli("coordinator-registry.py", "renew", "--project", "p", "--session", "coord", ok=False)
        self.assertIn("unresolved-owner-reporting-violation", proc.stderr)
        self.assertEqual(json.loads((self.runtime / "coordinators/p.json").read_text())["state"], "needs-owner")

    def test_new_policy_epoch_ignores_historical_reports_and_stale_violations(self):
        self.setup_sessions("coord")
        self.send("coord", "historical", "legacy report", self.now - 100)
        self.put(self.runtime / "reporting-violations" / "coord.json", {
            "schemaVersion": 1, "sessionId": "coord", "violations": [{
                "eventId": "historical", "eventAt": self.now - 100,
                "reason": "unsolicited-owner-report", "detectedAt": self.now - 50
            }]
        })
        self.put(self.runtime / "reporting-policy.json", {
            "schemaVersion": 1, "mode": "pull-only", "ownerFacingSessionId": "owner",
            "configuredAt": self.now - 10, "interception": "unavailable",
            "detection": "best-effort-session-transcript"
        })
        _, checked = self.cli("reporting-policy.py", "check", "--session", "coord")
        self.assertTrue(checked["compliant"])
        self.put(self.runtime / "coordinators" / "p.json", {
            "project": "p", "projectId": "native", "state": "needs-owner",
            "coordinatorSessionId": "coord", "generation": 1,
            "leaseExpiresAt": self.now + 1, "activeChildren": ["live-child"],
            "reportingViolation": {"admissionBlocker": "unresolved-owner-reporting-violation"}
        })
        _, renewed = self.cli("coordinator-registry.py", "renew", "--project", "p", "--session", "coord")
        self.assertEqual(renewed["record"]["state"], "authoritative")
        self.assertEqual(renewed["record"]["activeChildren"], ["live-child"])
        self.assertNotIn("reportingViolation", renewed["record"])

    def test_exact_permitted_reply_consumes_once_and_silence_does_not_pause_work(self):
        self.policy(); self.setup_sessions("coord")
        permit = self.issue("coord", "permit-123456789012")
        self.send("coord", "reply", permit["responseMarker"] + " Status is healthy.", self.now + 1)
        _, checked = self.cli("reporting-policy.py", "check", "--session", "coord")
        self.assertTrue(checked["compliant"])
        self.assertEqual(json.loads((self.runtime / "reporting-permits/permit-123456789012.json").read_text())["state"], "consumed")
        self.put(self.runtime / "coordinators" / "p.json", {"project": "p", "projectId": "native", "state": "authoritative", "coordinatorSessionId": "coord", "generation": 1, "leaseExpiresAt": self.now + 1})
        _, renewed = self.cli("coordinator-registry.py", "renew", "--project", "p", "--session", "coord")
        self.assertEqual(renewed["record"]["state"], "authoritative")

    def test_replay_expiry_and_wrong_session_reports_are_refused(self):
        self.policy(); self.setup_sessions("coord-a", "coord-b")
        replay = self.issue("coord-a", "permit-replay-12345")
        self.send("coord-a", "first", replay["responseMarker"] + " first", self.now + 1)
        self.send("coord-a", "second", replay["responseMarker"] + " replay", self.now + 2)
        _, result = self.cli("reporting-policy.py", "check", "--session", "coord-a", ok=False)
        self.assertIn("owner-report-permit-replayed", [v["reason"] for v in result["violations"]])
        expired = self.issue("coord-b", "permit-expired-1234", ttl=1)
        self.send("coord-b", "expired", expired["responseMarker"] + " late", self.now + 1001)
        _, result = self.cli("reporting-policy.py", "check", "--session", "coord-b", ok=False)
        self.assertIn("owner-report-permit-expired", [v["reason"] for v in result["violations"]])
        wrong = self.issue("coord-a", "permit-wrong-123456")
        self.send("coord-b", "wrong", wrong["responseMarker"] + " wrong coordinator", self.now + 1)
        _, result = self.cli("reporting-policy.py", "check", "--session", "coord-b", ok=False)
        self.assertIn("owner-report-permit-coordinator-mismatch", [v["reason"] for v in result["violations"]])


if __name__ == "__main__": unittest.main()

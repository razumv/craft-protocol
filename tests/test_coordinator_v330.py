# SPDX-License-Identifier: Apache-2.0
"""Protocol v3.3.0 coordinator inbox, product status, and commitment suite."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts"))
INBOX = SCRIPTS / "coordinator-inbox.py"
STATUS = SCRIPTS / "coordinator-status.py"
COMMIT = SCRIPTS / "coordinator-commitment.py"
INCIDENT = SCRIPTS / "recovery-incident.py"
ADMISSION = SCRIPTS / "recovery-admission.py"


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "ws" / "sessions"
        self.runtime = self.root / "runtime"
        self.sessions.mkdir(parents=True)
        self.now = 1_000_000_000
        self.env = {**os.environ,
                    "CRAFT_WORKSPACE": str(self.root / "ws"),
                    "CRAFT_SESSIONS": str(self.sessions),
                    "CRAFT_RUNTIME": str(self.runtime),
                    "CRAFT_TEST_NOW_MS": str(self.now),
                    "CRAFT_RECOVERY_CLEAR_CONFIRM_SECONDS": "0"}

    def tearDown(self):
        self.temp.cleanup()

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def manifest(self, sid, role="worker", status="in_progress", archived=False, labels=None):
        base = [f"agent-role::{role}"] + (labels or [])
        self.put(self.sessions / sid / "session.jsonl",
                 {"id": sid, "sessionStatus": status, "isArchived": archived, "labels": base})

    def coordinator(self, sid="coord1"):
        self.manifest(sid, role="coordinator", labels=["coordinators", "protocol-version::3.3.0"])

    def registry(self, project="demo", sid="coord1", generation=2, state="authoritative", **extra):
        row = {"schemaVersion": 1, "project": project, "coordinatorSessionId": sid,
               "generation": generation, "state": state, "leaseExpiresAt": self.now + 3_600_000,
               "lastHeartbeatAt": self.now, **extra}
        self.put(self.runtime / "coordinators" / f"{project}.json", row)

    def lease(self, sid="worker1", parent="coord1", work_unit="wu-1", attempt="1", state="running", role="worker"):
        self.manifest(sid, role=role, labels=[f"parent-session::{parent}",
                      f"work-unit::{work_unit}", f"attempt::{attempt}"])
        self.put(self.runtime / "worker-leases" / f"{sid}.json",
                 {"schemaVersion": 1, "sessionId": sid, "parentSessionId": parent,
                  "role": role, "workUnit": work_unit, "attempt": attempt, "state": state})

    def cli(self, script, *args, ok=True, now=None):
        env = self.env if now is None else {**self.env, "CRAFT_TEST_NOW_MS": str(now)}
        cp = subprocess.run([sys.executable, str(script), *args], env=env,
                            text=True, capture_output=True, timeout=60)
        if ok and cp.returncode:
            self.fail(f"{script.name} {' '.join(args)} exited {cp.returncode}\n{cp.stdout}\n{cp.stderr}")
        payload = None
        if cp.returncode == 0 and cp.stdout:
            try:
                payload = json.loads(cp.stdout)
            except json.JSONDecodeError:
                payload = None  # non-JSON output (e.g. markdown report)
        return cp, payload

    def base_project(self):
        self.coordinator()
        self.registry()
        self.lease()

    def submit(self, kind, subject="s", *, sender="worker1", work_unit="wu-1", attempt="1",
               generation=2, evidence=None, revision=None, ok=True):
        args = ["submit", "--project", "demo", "--coordinator", "coord1", "--generation", str(generation),
                "--sender", sender, "--work-unit", work_unit, "--attempt", attempt,
                "--kind", kind, "--subject", subject, "--apply"]
        for ev in evidence or []:
            args += ["--evidence", ev]
        if revision is not None:
            args += ["--revision", str(revision)]
        return self.cli(INBOX, *args, ok=ok)


class InboxTests(Base):
    def publish_ack_status(self):
        payload = {"objective": "Process coordinator inbox", "phase": "executing",
                   "currentFocus": "claimed digest", "childRefs": ["worker1"],
                   "nextReviewInSeconds": 3600,
                   "nextActions": [{"description": "apply claimed evidence", "trigger": "now",
                                    "requiredEvidence": "claimed inbox revision", "successBranch": "continue",
                                    "failureBranch": "release claim"}]}
        _, out = self.cli(STATUS, "publish", "--project", "demo", "--session", "coord1",
                          "--generation", "2", "--json", json.dumps(payload), "--apply")
        return out["revision"]

    def test_submit_requires_generation_match(self):
        self.base_project()
        cp, _ = self.submit("progress", generation=99, ok=False)
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("registry", cp.stderr)

    def test_submit_requires_sender_lease_binding(self):
        self.coordinator()
        self.registry()
        # worker exists but bound to a different coordinator
        self.lease(parent="other-coord")
        cp, _ = self.submit("progress", ok=False)
        self.assertIn("not bound", cp.stderr)

    def test_submit_requires_exact_lease_attempt_binding(self):
        self.base_project()
        lease_path = self.runtime / "worker-leases" / "worker1.json"
        lease = json.loads(lease_path.read_text()); lease.pop("attempt"); self.put(lease_path, lease)
        cp, _ = self.submit("progress", ok=False)
        self.assertIn("attempt binding", cp.stderr)

    def test_submit_rejects_credentials_in_evidence(self):
        self.base_project()
        cp, _ = self.submit("progress", evidence=["authorization: Bearer sk-1"], ok=False)
        self.assertIn("credential", cp.stderr)

    def test_report_kinds_require_auditor_and_observer_provenance(self):
        self.base_project()
        cp, _ = self.submit("audit-verdict", "self approval", ok=False)
        self.assertIn("auditor", cp.stderr)
        cp, _ = self.submit("observer-terminal", "unbound observer", ok=False)
        self.assertIn("external-wait", cp.stderr)
        self.lease("audit1", work_unit="audit", role="auditor")
        _, verdict = self.submit("audit-verdict", "PASS", sender="audit1", work_unit="audit")
        self.assertEqual(verdict["item"]["senderRole"], "auditor")
        self.lease("watch1", work_unit="release")
        self.put(self.runtime / "external-waits" / "release.json",
                 {"waitId": "release", "project": "demo", "coordinatorSessionId": "coord1",
                  "watcherSessionId": "watch1", "workUnit": "release", "state": "terminal"})
        _, observed = self.submit("observer-terminal", "release complete", sender="watch1", work_unit="release")
        self.assertEqual(observed["item"]["kind"], "observer-terminal")

    def test_failure_class_is_bounded_and_retained_in_claim(self):
        self.base_project()
        _, submitted = self.cli(INBOX, "submit", "--project", "demo", "--coordinator", "coord1",
                                "--generation", "2", "--sender", "worker1", "--work-unit", "wu-1",
                                "--attempt", "1", "--kind", "blocker", "--subject", "environment unavailable",
                                "--failure-class", "admission-environment", "--apply")
        self.assertEqual(submitted["item"]["failureClass"], "admission-environment")
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        self.assertEqual(claim["digest"][0]["failureClass"], "admission-environment")

    def test_failure_class_changes_payload_revision_and_is_not_allowed_on_progress(self):
        self.base_project()
        args = ["submit", "--project", "demo", "--coordinator", "coord1", "--generation", "2",
                "--sender", "worker1", "--work-unit", "wu-1", "--attempt", "1",
                "--kind", "blocker", "--subject", "failed", "--failure-class"]
        _, first = self.cli(INBOX, *args, "implementation-defect", "--apply")
        _, second = self.cli(INBOX, *args, "product-acceptance", "--apply")
        self.assertEqual(first["item"]["revision"], 1)
        self.assertEqual(second["item"]["revision"], 2)
        self.assertEqual(second["item"]["failureClass"], "product-acceptance")
        cp, _ = self.cli(INBOX, "submit", "--project", "demo", "--coordinator", "coord1",
                         "--generation", "2", "--sender", "worker1", "--work-unit", "wu-1",
                         "--attempt", "1", "--kind", "progress", "--subject", "working",
                         "--failure-class", "implementation-defect", "--apply", ok=False)
        self.assertIn("allowed only", cp.stderr)

    def test_submit_rejects_non_local_evidence_path(self):
        self.base_project()
        cp, _ = self.submit("progress", evidence=["/etc/passwd"], ok=False)
        self.assertIn("workspace", cp.stderr)

    def test_identical_resubmission_coalesces_diagnostics_only(self):
        self.base_project()
        _, first = self.submit("progress", "50%")
        self.assertFalse(first["coalesced"])
        self.assertEqual(first["item"]["revision"], 1)
        _, second = self.submit("progress", "50%")
        self.assertTrue(second["coalesced"])
        self.assertEqual(second["item"]["revision"], 1)
        self.assertEqual(second["item"]["diagnosticsRevision"], 1)

    def test_v33_fingerprint_and_acknowledged_item_remain_idempotent_without_failure_class(self):
        self.base_project()
        kind, subject = "terminal-handoff", "legacy candidate"
        _, first = self.submit(kind, subject)
        self.assertNotIn("failureClass", first["item"])
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        revision = self.publish_ack_status()
        self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1", "--generation", "2",
                 "--token", claim["token"], "--status-revision", str(revision), "--apply")
        _, again = self.submit(kind, subject)
        self.assertTrue(again["coalesced"])
        self.assertEqual(again["item"]["revision"], 1)
        self.assertEqual(again["item"]["state"], "acknowledged")
        self.assertNotIn("failureClass", again["item"])

    def test_meaningful_revision_replaces_pending_payload(self):
        self.base_project()
        self.submit("progress", "50%")
        _, updated = self.submit("progress", "80%")
        self.assertEqual(updated["item"]["revision"], 2)
        self.assertEqual(updated["item"]["subject"], "80%")

    def test_only_waking_kinds_wake(self):
        self.base_project()
        self.submit("progress", "p")
        _, report = self.cli(INBOX, "report", "--project", "demo")
        self.assertEqual(report["projects"][0]["summary"]["wakingPending"], 0)
        self.assertFalse(report["projects"][0]["wakeReady"])
        self.submit("terminal-handoff", "candidate")
        _, report = self.cli(INBOX, "report", "--project", "demo")
        self.assertEqual(report["projects"][0]["summary"]["wakingPending"], 1)
        self.assertTrue(report["projects"][0]["wakeReady"])

    def test_wake_report_is_bounded_with_truthful_total(self):
        self.base_project()
        for n in range(220):
            self.put(self.runtime / "coordinator-inbox" / "demo" / f"item-{n:02d}.json",
                     {"eventKey": f"event-{n:02d}", "kind": "blocker", "sender": f"worker-{n:02d}",
                      "workUnit": f"wu-{n:02d}", "attempt": "1", "revision": 1,
                      "coordinatorGeneration": 2, "state": "pending", "waking": True,
                      "updatedAt": self.now})
        _, report = self.cli(INBOX, "report", "--project", "demo")
        row = report["projects"][0]
        self.assertEqual(row["wakePendingCount"], 220)
        self.assertEqual(len(row["wakePending"]), 200)
        self.assertTrue(row["wakeTruncated"])

    def test_hundred_report_storm_coalesces_to_one_item_and_one_wake(self):
        self.base_project()
        for _ in range(100):
            self.submit("progress", "still going")
        self.submit("terminal-handoff", "done")
        inbox_dir = self.runtime / "coordinator-inbox" / "demo"
        # progress storm collapses to one key; terminal is a second distinct key.
        self.assertEqual(len(list(inbox_dir.glob("*.json"))), 2)
        _, report = self.cli(INBOX, "report", "--project", "demo")
        self.assertEqual(report["projects"][0]["summary"]["wakingPending"], 1)
        self.assertEqual(len(report["projects"][0]["wakePending"]), 1)

    def test_claim_ack_consumes_with_evidence(self):
        self.base_project()
        self.submit("terminal-handoff", "candidate")
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        self.assertEqual(claim["count"], 1)
        token = claim["token"]
        # ack without evidence fails closed
        cp, _ = self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1",
                         "--generation", "2", "--token", token, "--apply", ok=False)
        self.assertNotEqual(cp.returncode, 0)
        # Only a status revision published after this claim may consume it.
        revision = self.publish_ack_status()
        _, acked = self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--token", token,
                            "--status-revision", str(revision), "--apply")
        self.assertEqual(len(acked["acked"]), 1)
        _, report = self.cli(INBOX, "report", "--project", "demo")
        summary = report["projects"][0]["summary"]
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["acknowledged"], 1)
        self.assertEqual(summary["retained"], 1)

    def test_duplicate_ack_is_idempotent(self):
        self.base_project()
        self.submit("terminal-handoff", "candidate")
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        token = claim["token"]
        revision = self.publish_ack_status()
        self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1", "--generation", "2",
                 "--token", token, "--status-revision", str(revision), "--apply")
        _, again = self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1", "--generation", "2",
                            "--token", token, "--status-revision", str(revision), "--apply")
        self.assertTrue(again["idempotent"])

    def test_ack_rejects_status_published_before_claim(self):
        self.base_project()
        self.submit("terminal-handoff", "candidate")
        old_revision = self.publish_ack_status()
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        _, stale = self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--token", claim["token"],
                            "--status-revision", str(old_revision), "--apply")
        self.assertEqual(stale["acked"], [])
        self.assertEqual(stale["skipped"][0]["reason"], "status-not-published-after-item-claim")
        new_revision = self.publish_ack_status()
        _, acked = self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--token", claim["token"],
                            "--status-revision", str(new_revision), "--apply")
        self.assertEqual(len(acked["acked"]), 1)

    def test_reused_claim_rebinds_meaningful_new_revision(self):
        self.base_project()
        self.submit("terminal-handoff", "candidate v1")
        _, first_claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                                  "--generation", "2", "--apply")
        self.submit("terminal-handoff", "candidate v2")
        _, refreshed = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                                "--generation", "2", "--apply")
        self.assertEqual(refreshed["token"], first_claim["token"])
        self.assertEqual(refreshed["digest"][0]["subject"], "candidate v2")
        self.assertEqual(refreshed["digest"][0]["claimedRevision"], 2)
        revision = self.publish_ack_status()
        _, acked = self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--token", refreshed["token"],
                            "--status-revision", str(revision), "--apply")
        self.assertEqual(len(acked["acked"]), 1)

    def test_claim_expiry_returns_unacked_items(self):
        self.base_project()
        self.submit("blocker", "blocked")
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        self.assertEqual(claim["count"], 1)
        far = self.now + 10_000_000  # past the 900s claim TTL
        _, rec = self.cli(INBOX, "reconcile", "--apply", now=far)
        self.assertIn("expire-claim", [a["action"] for a in rec["actions"]])
        _, report = self.cli(INBOX, "report", "--project", "demo", now=far)
        self.assertEqual(report["projects"][0]["summary"]["pending"], 1)

    def test_release_returns_items_to_pending(self):
        self.base_project()
        self.submit("blocker", "blocked")
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        _, rel = self.cli(INBOX, "release", "--project", "demo", "--session", "coord1",
                          "--generation", "2", "--token", claim["token"], "--apply")
        self.assertEqual(len(rel["released"]), 1)
        _, report = self.cli(INBOX, "report", "--project", "demo")
        self.assertEqual(report["projects"][0]["summary"]["pending"], 1)

    def test_superseded_generation_items_do_not_wake(self):
        self.base_project()
        self.submit("terminal-handoff", "candidate")
        # coordinator rotates to a new generation
        self.registry(generation=3)
        _, rec = self.cli(INBOX, "reconcile", "--apply")
        self.assertIn("orphan", [a["action"] for a in rec["actions"]])
        _, report = self.cli(INBOX, "report", "--project", "demo")
        self.assertFalse(report["projects"][0]["wakeReady"])


class StatusTests(Base):
    def publish(self, payload, *, generation=2, ok=True):
        return self.cli(STATUS, "publish", "--project", "demo", "--session", "coord1",
                        "--generation", str(generation), "--json", json.dumps(payload), "--apply", ok=ok)

    def executing_payload(self):
        return {"objective": "Ship API", "phase": "executing", "currentFocus": "wu-1",
                "childRefs": ["worker1"], "nextReviewInSeconds": 3600,
                "nextActions": [{"description": "review wu-1", "trigger": "worker1 terminal",
                                 "requiredEvidence": "green ci", "successBranch": "merge",
                                 "failureBranch": "rework"}]}

    def test_publish_executing_and_show(self):
        self.base_project()
        _, pub = self.publish(self.executing_payload())
        self.assertEqual(pub["revision"], 1)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "executing")

    def test_stale_generation_cannot_publish(self):
        self.base_project()
        cp, _ = self.publish(self.executing_payload(), generation=1, ok=False)
        self.assertIn("generation", cp.stderr)

    def increment_payload(self):
        payload = self.executing_payload()
        payload.update({
            "demonstrableNow": "The customer can open the account page",
            "remainingOutcome": "Connect purchase history and validate mobile workflow",
            "etaRange": "4-8 hours",
            "confidence": "medium",
            "realBlocker": "mobile fixture is not yet restored",
            "productIncrement": {
                "id": "pi-account-subscriptions",
                "stage": "building",
                "riskTier": "medium",
                "demonstrationCriterion": "Open a Person and inspect subscriptions on desktop and mobile",
                "nonGoals": ["billing-provider migration"],
                "stories": [
                    {"id": "profile", "title": "Inline subscriptions", "state": "integrated",
                     "dependsOn": [], "riskContribution": "low"},
                    {"id": "history", "title": "Purchase history", "state": "executing",
                     "dependsOn": ["profile"], "riskContribution": "medium"},
                ],
            },
        })
        return payload

    def test_publish_product_increment_and_customer_first_report(self):
        self.base_project()
        self.publish(self.increment_payload())
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["declared"]["productIncrement"]["stories"][1]["dependsOn"], ["profile"])
        cp, _ = self.cli(STATUS, "report", "--project", "demo", "--format", "markdown")
        body = cp.stdout
        self.assertLess(body.index("What the customer will see"), body.index("Executing now"))
        self.assertIn("Demonstrable now", body)
        self.assertIn("**ETA / confidence:** 4-8 hours / medium", body)
        self.assertIn("**One real blocker:** mobile fixture is not yet restored", body)

    def test_legacy_v33_status_remains_valid_without_increment_fields(self):
        self.base_project()
        self.publish(self.executing_payload())
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "executing")
        self.assertIsNone(show["declared"]["productIncrement"])
        cp, _ = self.cli(STATUS, "report", "--project", "demo", "--format", "markdown")
        self.assertIn("legacy v3.3 snapshot", cp.stdout)

    def test_product_increment_rejects_duplicate_unknown_self_and_cyclic_dependencies(self):
        self.base_project()
        cases = []
        duplicate = self.increment_payload()
        duplicate["productIncrement"]["stories"][1]["id"] = "profile"
        cases.append((duplicate, "duplicate"))
        unknown = self.increment_payload()
        unknown["productIncrement"]["stories"][1]["dependsOn"] = ["missing"]
        cases.append((unknown, "unknown"))
        self_ref = self.increment_payload()
        self_ref["productIncrement"]["stories"][1]["dependsOn"] = ["history"]
        cases.append((self_ref, "itself"))
        cyclic = self.increment_payload()
        cyclic["productIncrement"]["stories"][0]["dependsOn"] = ["history"]
        cases.append((cyclic, "cycle"))
        for payload, message in cases:
            cp, _ = self.publish(payload, ok=False)
            self.assertIn(message, cp.stderr)

    def test_product_increment_rejects_invalid_confidence_stage_and_unbounded_stories(self):
        self.base_project()
        bad = self.increment_payload(); bad["confidence"] = "certain"
        cp, _ = self.publish(bad, ok=False); self.assertIn("confidence", cp.stderr)
        bad = self.increment_payload(); bad["productIncrement"]["stage"] = "mysterious"
        cp, _ = self.publish(bad, ok=False); self.assertIn("stage", cp.stderr)
        bad = self.increment_payload()
        bad["productIncrement"]["stories"] = [
            {"id": f"s{n}", "title": f"Story {n}", "state": "planned", "dependsOn": []}
            for n in range(9)]
        cp, _ = self.publish(bad, ok=False); self.assertIn("1..8", cp.stderr)

    def test_product_increment_rejects_oversized_customer_fields_and_falsy_non_lists(self):
        self.base_project()
        for key in ("demonstrableNow", "remainingOutcome", "etaRange", "realBlocker"):
            bad = self.increment_payload(); bad[key] = "x" * 801
            cp, _ = self.publish(bad, ok=False); self.assertIn(key, cp.stderr)
        for malformed in ("", 0, {}):
            bad = self.increment_payload(); bad["productIncrement"]["nonGoals"] = malformed
            cp, _ = self.publish(bad, ok=False); self.assertIn("nonGoals", cp.stderr)
            bad = self.increment_payload(); bad["productIncrement"]["stories"][1]["dependsOn"] = malformed
            cp, _ = self.publish(bad, ok=False); self.assertIn("dependsOn", cp.stderr)
        for malformed_text in ("x" * 801, "bad\x01goal"):
            bad = self.increment_payload(); bad["productIncrement"]["nonGoals"] = [malformed_text]
            cp, _ = self.publish(bad, ok=False); self.assertIn("nonGoals", cp.stderr)

    def test_product_increment_risk_tier_cannot_understate_story_contribution(self):
        self.base_project()
        for aggregate, contribution in (("low", "medium"), ("low", "high"), ("medium", "high")):
            bad = self.increment_payload()
            bad["productIncrement"]["riskTier"] = aggregate
            bad["productIncrement"]["stories"][1]["riskContribution"] = contribution
            cp, _ = self.publish(bad, ok=False)
            self.assertIn("understate", cp.stderr)
        valid = self.increment_payload()
        valid["productIncrement"]["riskTier"] = "high"
        valid["productIncrement"]["stories"][1]["riskContribution"] = "high"
        _, published = self.publish(valid)
        self.assertEqual(published["record"]["declared"]["productIncrement"]["riskTier"], "high")

    def completion_payload(self):
        criterion = "Open a Person and inspect subscriptions on desktop and mobile"
        reports = [
            ("worker1", "candidate", "worker", "terminal-handoff", "integrated candidate", ["sha:abc123"]),
            ("worker2", "accept", "auditor", "audit-verdict", "integrated acceptance PASS", ["audit:pass@abc123"]),
            ("worker3", "release", "worker", "observer-terminal", "production readback succeeded", ["run:release-42"]),
            ("worker4", "demo", "worker", "terminal-handoff", "real workflow demonstrated", [f"demo:{criterion}"]),
        ]
        items = []
        for sender, work_unit, role, kind, subject, evidence in reports:
            self.lease(sender, work_unit=work_unit, state="handoff-ready", role=role)
            if kind == "observer-terminal":
                self.put(self.runtime / "external-waits" / "release-readback.json",
                         {"waitId": "release-readback", "project": "demo", "coordinatorSessionId": "coord1",
                          "watcherSessionId": sender, "workUnit": work_unit, "state": "terminal"})
            _, submitted = self.submit(kind, subject, sender=sender, work_unit=work_unit, evidence=evidence)
            items.append(submitted["item"])
        refs = [item["eventKey"] for item in items]
        payload = self.increment_payload()
        payload.update({"phase": "complete", "currentFocus": "Customer workflow verified",
                        "remainingOutcome": "Nothing remains", "realBlocker": None,
                        "nextActions": [], "childRefs": [], "completedOutcomes": [
                            {"summary": "Product Increment delivered", "evidenceRef": refs[0]}]})
        payload.pop("nextReviewInSeconds", None)
        payload["productIncrement"].update({
            "stage": "complete",
            "stories": [
                {"id": "profile", "title": "Inline subscriptions", "state": "accepted",
                 "dependsOn": [], "riskContribution": "low"},
                {"id": "history", "title": "Purchase history", "state": "accepted",
                 "dependsOn": ["profile"], "riskContribution": "medium"}],
            "completionEvidence": {
                key: {"eventKey": item["eventKey"], "revision": item["revision"],
                      "fingerprint": item["fingerprint"]}
                for key, item in zip(("integratedCandidateRef", "acceptanceRef",
                                      "releaseReadbackRef", "demonstrationRef"), items)}})
        return payload

    def test_complete_increment_requires_accepted_stories_and_phase_alignment(self):
        self.base_project()
        bad = self.increment_payload(); bad["productIncrement"]["stage"] = "complete"
        cp, _ = self.publish(bad, ok=False); self.assertIn("every story", cp.stderr)
        bad = self.increment_payload(); bad["phase"] = "complete"; bad["nextActions"] = []
        bad.pop("nextReviewInSeconds", None); bad["childRefs"] = []
        cp, _ = self.publish(bad, ok=False); self.assertIn("must match", cp.stderr)

    def test_complete_increment_requires_exact_current_generation_evidence(self):
        self.base_project()
        payload = self.completion_payload()
        _, published = self.publish(payload)
        self.assertEqual(published["record"]["declared"]["productIncrement"]["stage"], "complete")
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "verified")
        for key in ("integratedCandidateRef", "acceptanceRef", "releaseReadbackRef", "demonstrationRef"):
            bad = self.completion_payload()
            bad["productIncrement"]["completionEvidence"][key]["eventKey"] = "missing"
            cp, _ = self.publish(bad, ok=False); self.assertIn("not observed", cp.stderr)
        bad = self.completion_payload()
        bad["productIncrement"]["completionEvidence"]["acceptanceRef"]["revision"] += 1
        cp, _ = self.publish(bad, ok=False); self.assertIn("binding mismatch", cp.stderr)
        bad = self.completion_payload()
        bad["productIncrement"]["completionEvidence"]["acceptanceRef"]["fingerprint"] = "0" * 64
        cp, _ = self.publish(bad, ok=False); self.assertIn("binding mismatch", cp.stderr)

    def test_complete_increment_rejects_wrong_evidence_kind_and_unbound_demo(self):
        self.base_project()
        payload = self.completion_payload()
        completion = payload["productIncrement"]["completionEvidence"]
        completion["releaseReadbackRef"] = dict(completion["acceptanceRef"])
        cp, _ = self.publish(payload, ok=False); self.assertIn("distinct", cp.stderr)
        payload = self.completion_payload()
        demo_ref = payload["productIncrement"]["completionEvidence"]["demonstrationRef"]["eventKey"]
        item_path = next((self.runtime / "coordinator-inbox" / "demo").glob(f"{demo_ref}.json"))
        item = json.loads(item_path.read_text()); item["evidence"] = ["demo:some other workflow"]
        self.put(item_path, item)
        cp, _ = self.publish(payload, ok=False); self.assertIn("demonstrationCriterion", cp.stderr)

    def test_invented_child_reference_fails_closed(self):
        self.base_project()
        payload = self.executing_payload()
        payload["childRefs"] = ["ghost"]
        cp, _ = self.publish(payload, ok=False)
        self.assertIn("child reference", cp.stderr)

    def test_malformed_next_action_fails_closed(self):
        self.base_project()
        payload = self.executing_payload()
        payload["nextActions"] = [{"description": "do"}]  # missing trigger/evidence/branches
        cp, _ = self.publish(payload, ok=False)
        self.assertNotEqual(cp.returncode, 0)

    def test_more_than_three_actions_fails_closed(self):
        self.base_project()
        payload = self.executing_payload()
        action = payload["nextActions"][0]
        payload["nextActions"] = [action] * 4
        cp, _ = self.publish(payload, ok=False)
        self.assertIn("at most", cp.stderr)

    def test_secret_content_fails_closed(self):
        self.base_project()
        payload = self.executing_payload()
        payload["currentFocus"] = "api_key=sk-secret-value"
        cp, _ = self.publish(payload, ok=False)
        self.assertIn("credential", cp.stderr)

    def test_waiting_requires_active_commitment(self):
        self.base_project()
        cp, _ = self.publish({"objective": "x", "phase": "waiting", "nextActions": [],
                              "nextReviewInSeconds": 3600}, ok=False)
        self.assertIn("commitment", cp.stderr)
        # register a commitment then publishing waiting succeeds
        self.cli(COMMIT, "register", "--project", "demo", "--session", "coord1", "--generation", "2",
                 "--commitment-id", "c1", "--subject", "await", "--binding-kind", "worker-lease",
                 "--ref", "worker1", "--deadline-seconds", "3600", "--success-action", "merge",
                 "--failure-action", "rework", "--apply")
        _, pub = self.publish({"objective": "x", "phase": "waiting", "commitmentRefs": ["c1"],
                               "childRefs": ["worker1"], "nextReviewInSeconds": 3600})
        self.assertEqual(pub["revision"], 1)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "waiting-observed")

    def test_old_generation_commitment_cannot_observe_current_wait(self):
        self.base_project()
        self.cli(COMMIT, "register", "--project", "demo", "--session", "coord1", "--generation", "2",
                 "--commitment-id", "c-old", "--subject", "await", "--binding-kind", "worker-lease",
                 "--ref", "worker1", "--deadline-seconds", "3600", "--success-action", "merge",
                 "--failure-action", "rework", "--apply")
        path = self.runtime / "coordinator-commitments" / "demo" / "c-old.json"
        row = json.loads(path.read_text()); row["generation"] = 1; self.put(path, row)
        cp, _ = self.publish({"objective": "x", "phase": "waiting", "commitmentRefs": ["c-old"],
                              "nextReviewInSeconds": 3600}, ok=False)
        self.assertIn("this coordinator generation", cp.stderr)

    def test_blocked_status_with_bounded_commitment_is_blocked_not_stale(self):
        self.base_project()
        self.cli(COMMIT, "register", "--project", "demo", "--session", "coord1", "--generation", "2",
                 "--commitment-id", "blocked-review", "--subject", "review blocker",
                 "--binding-kind", "scheduled-review", "--deadline-seconds", "3600",
                 "--success-action", "resume", "--failure-action", "preserve", "--apply")
        self.publish({"objective": "x", "phase": "blocked", "currentFocus": "exact blocker",
                      "commitmentRefs": ["blocked-review"], "nextReviewInSeconds": 3600,
                      "nextActions": [{"description": "review exact blocker", "trigger": "scheduled review deadline",
                                       "requiredEvidence": "immutable blocker evidence", "successBranch": "resume bounded work",
                                       "failureBranch": "preserve blocked state"}]})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "blocked")
        self.assertEqual(show["issues"], [])

    def test_unobserved_blocked_status_remains_stale(self):
        self.base_project(); self.lease(state="handoff-ready")
        self.publish({"objective": "x", "phase": "blocked", "currentFocus": "prose blocker",
                      "nextActions": []})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "stale")
        self.assertIn("no-observed-activity", show["issues"])

    def test_contradiction_complete_with_active_workers(self):
        self.base_project()
        self.publish({"objective": "x", "phase": "complete", "nextActions": []})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "contradictory")

    def test_next_review_must_be_bounded_integer(self):
        self.base_project()
        payload = self.executing_payload(); payload.pop("nextReviewInSeconds"); payload["nextReviewAt"] = "tomorrow"
        cp, _ = self.publish(payload, ok=False)
        self.assertIn("integer timestamp", cp.stderr)
        payload = self.executing_payload(); payload["nextReviewInSeconds"] = "3600"
        cp, _ = self.publish(payload, ok=False)
        self.assertIn("must be an integer", cp.stderr)

    def test_nonterminal_status_requires_next_review(self):
        self.base_project()
        payload = self.executing_payload(); payload.pop("nextReviewInSeconds")
        cp, _ = self.publish(payload, ok=False)
        self.assertIn("requires nextReview", cp.stderr)

    def test_nonterminal_latest_evidence_is_executing_not_verified(self):
        self.base_project(); self.lease(state="handoff-ready")
        self.submit("candidate", "candidate ready")
        payload = self.executing_payload(); payload["childRefs"] = ["worker1"]
        self.publish(payload)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "executing")

    def test_candidate_only_cannot_verify_completion(self):
        self.base_project(); self.lease(state="handoff-ready")
        _, submitted = self.submit("candidate", "candidate ready")
        cp, _ = self.publish({"objective": "Ship API", "phase": "complete", "nextActions": [],
                              "completedOutcomes": [{"summary": "candidate accepted",
                                                     "evidenceRef": submitted["item"]["eventKey"]}]}, ok=False)
        self.assertIn("not verification-grade", cp.stderr)

    def test_verified_completion_requires_observed_evidence_reference(self):
        self.base_project(); self.lease(state="handoff-ready")
        proof = self.root / "ws" / "verification.txt"; proof.write_text("verified\n")
        _, submitted = self.submit("terminal-handoff", "candidate ready", evidence=[str(proof)])
        event_key = submitted["item"]["eventKey"]
        _, published = self.publish({"objective": "Ship API", "phase": "complete", "nextActions": [],
                                     "completedOutcomes": [{"summary": "candidate accepted",
                                                            "evidenceRef": event_key}]})
        self.assertEqual(published["revision"], 1)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "verified")
        self.submit("candidate", "later unrelated candidate")
        _, still_verified = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(still_verified["classification"], "verified")
        cp, _ = self.publish({"objective": "Ship API", "phase": "complete", "nextActions": [],
                              "completedOutcomes": [{"summary": "invented", "evidenceRef": "missing"}]}, ok=False)
        self.assertIn("not observed", cp.stderr)

    def test_malformed_stored_status_is_contradictory_not_crash(self):
        self.base_project()
        status_path = self.runtime / "coordinator-status" / "demo.json"
        self.put(status_path, ["malformed"])
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "contradictory")
        self.assertIn("stored-status-malformed", show["issues"])
        self.put(status_path, {"generation": 2, "revision": 1, "publishedAt": self.now,
                               "declared": {"objective": "x", "phase": "bogus"}})
        _, invalid_dict = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(invalid_dict["classification"], "contradictory")
        self.assertIn("stored-status-malformed", invalid_dict["issues"])
        self.put(self.runtime / "coordinator-inbox" / "demo" / "bad.json", ["malformed"])
        _, again = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(again["classification"], "contradictory")

    def test_declared_shape_and_synthesized_arrays_are_bounded(self):
        self.base_project()
        payload = self.executing_payload(); payload["currentFocus"] = {"nested": "not text"}
        cp, _ = self.publish(payload, ok=False)
        self.assertIn("currentFocus must be text", cp.stderr)
        payload = self.executing_payload(); payload["childRefs"] = [{"not": "hashable"}]
        cp, _ = self.publish(payload, ok=False)
        self.assertIn("entries must be bounded non-empty text", cp.stderr)
        self.publish(self.executing_payload())
        for n in range(2, 42):
            self.put(self.runtime / "worker-leases" / f"worker{n}.json",
                     {"sessionId": f"worker{n}", "parentSessionId": "coord1", "workUnit": f"wu-{n}",
                      "attempt": "1", "state": "running"})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        synth = show["synthesized"]
        self.assertEqual(synth["activeWorkerCount"], 41)
        self.assertEqual(len(synth["activeWorkers"]), 32)
        self.assertTrue(synth["activeWorkersTruncated"])
        self.assertFalse(any(key.startswith("_") for key in synth))

    def test_report_all_markdown_is_deterministic_and_sorted(self):
        self.coordinator("c-alpha"); self.registry("alpha", "c-alpha", 1)
        self.coordinator("c-beta"); self.registry("beta", "c-beta", 1)
        cp, _ = self.cli(STATUS, "report", "--all", "--format", "markdown")
        body = cp.stdout
        self.assertLess(body.index("## alpha"), body.index("## beta"))
        cp2, _ = self.cli(STATUS, "report", "--all", "--format", "markdown")
        self.assertEqual(cp.stdout, cp2.stdout)

    def test_health_observations_missing_and_stale(self):
        self.base_project()
        _, rec = self.cli(STATUS, "reconcile", "--apply")
        kinds = [o["kind"] for o in rec["observations"]]
        self.assertIn("coordinator-status-missing", kinds)


class CommitmentTests(Base):
    def register(self, *, binding="worker-lease", ref="worker1", deadline=3600, generation=2, ok=True, cid="c1"):
        args = ["register", "--project", "demo", "--session", "coord1", "--generation", str(generation),
                "--commitment-id", cid, "--subject", "await", "--binding-kind", binding,
                "--deadline-seconds", str(deadline), "--success-action", "merge",
                "--failure-action", "rework", "--apply"]
        if ref is not None:
            args += ["--ref", ref]
        return self.cli(COMMIT, *args, ok=ok)

    def test_register_requires_observed_binding(self):
        self.coordinator(); self.registry()
        # no such lease
        cp, _ = self.register(ref="ghost", ok=False)
        self.assertIn("not observable", cp.stderr)

    def test_register_worker_lease_binding(self):
        self.base_project()
        _, out = self.register()
        self.assertEqual(out["commitment"]["state"], "observing")
        _, retry = self.register()
        self.assertTrue(retry["idempotent"])
        cp, _ = self.cli(COMMIT, "register", "--project", "demo", "--session", "coord1",
                         "--generation", "2", "--commitment-id", "c1", "--subject", "different",
                         "--binding-kind", "worker-lease", "--ref", "worker1", "--deadline-seconds", "3600",
                         "--success-action", "merge", "--failure-action", "rework", "--apply", ok=False)
        self.assertIn("conflicting contract", cp.stderr)

    def test_overdue_emits_incident_reason(self):
        self.base_project()
        self.register(binding="scheduled-review", ref=None, deadline=60)
        far = self.now + 10_000_000
        _, rec = self.cli(COMMIT, "reconcile", "--apply", now=far)
        reasons = [a["reason"] for a in rec["actions"]]
        self.assertIn("deadline-overdue", reasons)

    def test_malformed_persisted_numbers_fail_closed_without_traceback(self):
        self.base_project()
        reg_path = self.runtime / "coordinators" / "demo.json"
        reg = json.loads(reg_path.read_text()); reg["generation"] = "bad"; self.put(reg_path, reg)
        cp, _ = self.register(ok=False)
        self.assertNotIn("Traceback", cp.stderr)
        self.assertIn("stale coordinator generation", cp.stderr)
        reg["generation"] = -1; self.put(reg_path, reg)
        cp, _ = self.register(generation=-1, ok=False)
        self.assertIn("stale coordinator generation", cp.stderr)
        reg["generation"] = 2; self.put(reg_path, reg)
        self.register()
        path = self.runtime / "coordinator-commitments" / "demo" / "c1.json"
        record = json.loads(path.read_text()); record["deadlineAt"] = "bad"; self.put(path, record)
        cp, rec = self.cli(COMMIT, "reconcile", "--apply")
        self.assertNotIn("Traceback", cp.stderr)
        self.assertEqual(rec["actions"][0]["reason"], "malformed-record")
        self.put(path, ["not-an-object"])
        cp, listed = self.cli(COMMIT, "list", "--project", "demo")
        self.assertNotIn("Traceback", cp.stderr); self.assertEqual(listed["count"], 0)
        cp, reconciled = self.cli(COMMIT, "reconcile", "--apply")
        self.assertNotIn("Traceback", cp.stderr); self.assertEqual(reconciled["actions"], [])

    def test_resolution_rejects_unobserved_cancel_and_early_timeout(self):
        self.base_project(); self.register()
        cp, _ = self.cli(COMMIT, "resolve", "--project", "demo", "--session", "coord1",
                         "--generation", "2", "--commitment-id", "c1", "--resolution", "cancelled",
                         "--evidence", "no longer needed", "--apply", ok=False)
        self.assertIn("cannot be cancelled", cp.stderr)
        cp, _ = self.cli(COMMIT, "resolve", "--project", "demo", "--session", "coord1",
                         "--generation", "2", "--commitment-id", "c1", "--resolution", "timeout",
                         "--evidence", "too early", "--apply", ok=False)
        self.assertIn("deadline", cp.stderr)

    def test_scheduled_review_time_can_only_prove_timeout(self):
        self.base_project(); self.register(binding="scheduled-review", ref=None, deadline=60)
        far = self.now + 120_000
        cp, _ = self.cli(COMMIT, "resolve", "--project", "demo", "--session", "coord1",
                         "--generation", "2", "--commitment-id", "c1", "--resolution", "success",
                         "--evidence", "time passed", "--apply", ok=False, now=far)
        self.assertIn("support timeout only", cp.stderr)
        _, out = self.cli(COMMIT, "resolve", "--project", "demo", "--session", "coord1",
                          "--generation", "2", "--commitment-id", "c1", "--resolution", "timeout",
                          "--evidence", "deadline passed", "--apply", now=far)
        self.assertEqual(out["commitment"]["state"], "resolved-timeout")

    def test_resolution_requires_terminal_observer(self):
        self.base_project()
        self.register()
        # worker still running -> success resolution refused
        cp, _ = self.cli(COMMIT, "resolve", "--project", "demo", "--session", "coord1",
                         "--generation", "2", "--commitment-id", "c1", "--resolution", "success",
                         "--evidence", "done", "--apply", ok=False)
        self.assertIn("terminal observer", cp.stderr)
        # mark lease terminal, then resolution succeeds
        self.lease(state="handoff-ready")
        _, out = self.cli(COMMIT, "resolve", "--project", "demo", "--session", "coord1",
                          "--generation", "2", "--commitment-id", "c1", "--resolution", "success",
                          "--evidence", "merged sha abc", "--apply")
        self.assertEqual(out["commitment"]["state"], "resolved-success")
        _, retry = self.register()
        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["commitment"]["state"], "resolved-success")


class IntegrationTests(Base):
    def test_recovery_incident_emits_v33_kinds_generation_fenced(self):
        self.base_project()
        # a waking terminal report, an overdue commitment, and missing status
        self.cli(INBOX, "submit", "--project", "demo", "--coordinator", "coord1", "--generation", "2",
                 "--sender", "worker1", "--work-unit", "wu-1", "--attempt", "1",
                 "--kind", "terminal-handoff", "--subject", "candidate", "--apply")
        self.cli(COMMIT, "register", "--project", "demo", "--session", "coord1", "--generation", "2",
                 "--commitment-id", "c1", "--subject", "await", "--binding-kind", "scheduled-review",
                 "--deadline-seconds", "60", "--success-action", "go", "--failure-action", "stop", "--apply")
        far = self.now + 10_000_000
        self.cli(INCIDENT, "detect", "--apply", now=far)
        _, listing = self.cli(INCIDENT, "list", now=far)
        rows = listing.get("incidents") or listing.get("rows") or []
        kinds = {r["kind"] for r in rows}
        self.assertIn("coordinator-inbox-ready", kinds)
        self.assertIn("coordinator-commitment-overdue", kinds)
        self.assertIn("coordinator-status-missing", kinds)
        for r in rows:
            if r["kind"].startswith("coordinator-") and r["kind"] in {
                    "coordinator-inbox-ready", "coordinator-commitment-overdue", "coordinator-status-missing"}:
                self.assertEqual(r["sessionId"], "coord1")
                self.assertEqual((r["evidence"] or {}).get("generation"), 2)

    def test_admission_routes_v33_wakes_generation_fenced(self):
        self.base_project()
        self.cli(INBOX, "submit", "--project", "demo", "--coordinator", "coord1", "--generation", "2",
                 "--sender", "worker1", "--work-unit", "wu-1", "--attempt", "1",
                 "--kind", "terminal-handoff", "--subject", "candidate", "--apply")
        far = self.now + 10_000_000
        self.cli(INCIDENT, "detect", "--apply", now=far)
        # import admission and confirm direct_target routes the inbox-ready wake, and
        # a wrong-generation copy does not route.
        code = (
            "import importlib.util,json,os,sys\n"
            f"s=importlib.util.spec_from_file_location('adm',{str(ADMISSION)!r})\n"
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
            "rows=[r for r in m.actionable_incidents() if r['kind']=='coordinator-inbox-ready']\n"
            "assert rows, 'no inbox-ready actionable incident'\n"
            "t=m.direct_target(rows[0]); assert t and t['targetGeneration']=='2', t\n"
            "bad=dict(rows[0]); bad['evidence']=dict(bad['evidence']); bad['evidence']['generation']=99\n"
            "assert m.direct_target(bad) is None\n"
            "print('ok')\n")
        env = {**self.env, "CRAFT_TEST_NOW_MS": str(far)}
        cp = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("ok", cp.stdout)


if __name__ == "__main__":
    unittest.main()

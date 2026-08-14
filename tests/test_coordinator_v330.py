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
GATE = SCRIPTS / "owner-gate.py"
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

    def test_candidate_requires_worker_sender(self):
        self.base_project()
        self.lease("audit1", work_unit="audit", role="auditor")
        cp, _ = self.submit("candidate", "auditor-built fix", sender="audit1",
                            work_unit="audit", ok=False)
        self.assertIn("worker sender", cp.stderr)

    def test_terminal_lane_cannot_send_progress_or_candidate(self):
        self.base_project()
        self.lease(state="handoff-ready")
        for kind in ("progress", "candidate"):
            cp, _ = self.submit(kind, "zombie lane keeps working", ok=False)
            self.assertIn("terminal lane", cp.stderr)
        _, still_terminal = self.submit("terminal-handoff", "final report")
        self.assertEqual(still_terminal["item"]["kind"], "terminal-handoff")

    def test_role_reminders_re_anchor_sender_and_coordinator(self):
        self.base_project()
        _, submitted = self.submit("progress", "50%")
        self.assertIn("never spawn lanes", submitted["roleReminder"])
        _, coalesced = self.submit("progress", "50%")
        self.assertIn("never spawn lanes", coalesced["roleReminder"])
        self.lease("audit1", work_unit="audit", role="auditor")
        _, verdict = self.submit("audit-verdict", "PASS", sender="audit1", work_unit="audit")
        self.assertIn("read-only", verdict["roleReminder"])
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        self.assertIn("dispatch workers/auditors", claim["roleReminder"])

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

    def test_adopt_requires_predecessor_and_exact_authority(self):
        self.base_project()
        cp, _ = self.cli(INBOX, "adopt", "--project", "demo", "--session", "coord1",
                         "--generation", "2", "--apply", ok=False)
        self.assertIn("no predecessor", cp.stderr)
        self.coordinator("coord2")
        self.registry(sid="coord2", generation=3, predecessorSessionId="coord1")
        cp, _ = self.cli(INBOX, "adopt", "--project", "demo", "--session", "coord1",
                         "--generation", "3", "--apply", ok=False)
        self.assertIn("mismatch", cp.stderr)

    def test_adopted_pending_items_are_claimable_by_successor(self):
        self.base_project()
        self.submit("terminal-handoff", "candidate from gen2")
        self.coordinator("coord2")
        self.registry(sid="coord2", generation=3, predecessorSessionId="coord1")
        _, adopted = self.cli(INBOX, "adopt", "--project", "demo", "--session", "coord2",
                              "--generation", "3", "--apply")
        self.assertEqual(adopted["adoptedCount"], 1)
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord2",
                            "--generation", "3", "--apply")
        self.assertEqual(claim["count"], 1)
        item = claim["digest"][0]
        self.assertEqual(item["adoptedFromSession"], "coord1")
        self.assertEqual(item["adoptedFromGeneration"], 2)
        self.assertEqual(item["coordinatorGeneration"], 3)

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
        payload["githubSync"] = {
            "issue": "razumv/demo#42",
            "commentRef": "https://github.com/razumv/demo/issues/42#issuecomment-9",
            "projectField": "Status=Done", "syncedStage": "complete", "syncedAt": self.now - 1000}
        payload["productIncrement"].update({
            "stage": "complete",
            "stories": [
                {"id": "profile", "title": "Inline subscriptions", "state": "accepted",
                 "dependsOn": [], "riskContribution": "low", "acceptanceRef": refs[1]},
                {"id": "history", "title": "Purchase history", "state": "accepted",
                 "dependsOn": ["profile"], "riskContribution": "medium", "acceptanceRef": refs[1]}],
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

    def test_rotation_adoption_preserves_completion_evidence(self):
        # The money path: a Product Increment completes mid-rotation without
        # re-running acceptance — immutable evidence bindings survive adoption.
        self.base_project()
        payload = self.completion_payload()
        self.coordinator("coord2")
        self.registry(sid="coord2", generation=3, predecessorSessionId="coord1")
        publish2 = lambda ok=True: self.cli(
            STATUS, "publish", "--project", "demo", "--session", "coord2",
            "--generation", "3", "--json", json.dumps(payload), "--apply", ok=ok)
        cp, _ = publish2(ok=False)
        self.assertIn("not observed in this generation", cp.stderr)
        _, adopted = self.cli(INBOX, "adopt", "--project", "demo", "--session", "coord2",
                              "--generation", "3", "--apply")
        self.assertGreaterEqual(adopted["adoptedCount"], 4)
        # The deterministic external-wait rebind has its own tests; simulate its result.
        wpath = self.runtime / "external-waits/release-readback.json"
        wait = json.loads(wpath.read_text()); wait["coordinatorSessionId"] = "coord2"
        self.put(wpath, wait)
        _, pub = publish2()
        self.assertEqual(pub["record"]["declared"]["productIncrement"]["stage"], "complete")
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "verified")

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

    def test_unobserved_blocked_publish_fails_closed_and_stored_snapshot_remains_stale(self):
        self.base_project(); self.lease(state="handoff-ready")
        cp, _ = self.publish({"objective": "x", "phase": "blocked", "currentFocus": "prose blocker",
                              "nextActions": []}, ok=False)
        self.assertIn("blocked phase requires", cp.stderr)
        # A legacy stored prose-blocked snapshot still classifies stale, never healthy.
        self.put(self.runtime / "coordinator-status" / "demo.json",
                 {"schemaVersion": 1, "project": "demo", "coordinatorSessionId": "coord1",
                  "generation": 2, "revision": 1, "publishedAt": self.now, "updatedAt": self.now,
                  "declared": {"objective": "x", "phase": "blocked",
                               "currentFocus": "prose blocker", "nextActions": []}})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "stale")
        self.assertIn("no-observed-activity", show["issues"])

    def test_idle_ready_work_behind_a_gate_is_a_contradiction(self):
        # An owner gate holds its own scope only. A whole increment parked behind
        # one gate while a dependency-ready story has no lane is the observed
        # "nobody is working" failure and must not read as healthy.
        self.base_project()
        self.cli(GATE, "create", "--project", "demo", "--gate", "physical-check",
                 "--question", "Owner must verify the device?", "--choices", "DONE,HOLD",
                 "--owner-only-category", "human-product-judgment-action", "--scope", "work-unit")
        payload = self.increment_payload()
        payload.update({"phase": "blocked", "gateRefs": ["physical-check"], "nextActions": [],
                        "childRefs": []})
        payload["productIncrement"]["stories"] = [
            {"id": "blocked-by-gate", "title": "Physical acceptance", "state": "blocked",
             "dependsOn": [], "riskContribution": "medium"},
            {"id": "independent", "title": "Independent normalization", "state": "ready",
             "dependsOn": [], "riskContribution": "low"}]
        self.publish(payload)
        # No lease, wait or commitment observed → the ready story is unassigned.
        self.put(self.runtime / "worker-leases" / "worker1.json",
                 {"schemaVersion": 1, "sessionId": "worker1", "parentSessionId": "coord1",
                  "role": "worker", "workUnit": "wu-1", "attempt": "1", "state": "handoff-ready"})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "contradictory")
        self.assertIn("idle-ready-work:independent", show["issues"])
        # A live lane clears it: the project is demonstrably working the ready lane.
        self.lease(state="running")
        _, working = self.cli(STATUS, "show", "--project", "demo")
        self.assertNotIn("idle-ready-work:independent", working["issues"])

    def commitment(self, cid, binding, state, ref=None, generation=2):
        row = {"schemaVersion": 1, "project": "demo", "commitmentId": cid, "generation": generation,
               "bindingKind": binding, "ref": ref, "state": state,
               "coordinatorSessionId": "coord1", "subject": "s"}
        self.put(self.runtime / "coordinator-commitments" / "demo" / f"{cid}.json", row)

    def idle_ready_payload(self):
        payload = self.increment_payload()
        payload.update({"phase": "blocked", "gateRefs": ["physical-check"], "nextActions": [],
                        "childRefs": []})
        payload["productIncrement"]["stories"] = [
            {"id": "blocked-by-gate", "title": "Physical acceptance", "state": "blocked",
             "dependsOn": [], "riskContribution": "medium"},
            {"id": "independent", "title": "Independent normalization", "state": "ready",
             "dependsOn": [], "riskContribution": "low"}]
        return payload

    def test_promise_commitments_do_not_mask_idle_ready_work(self):
        # A scheduled-review or owner-gate commitment is a promise to look later,
        # not execution: it must not hide an unassigned ready story (observed live
        # on two projects).
        self.base_project()
        self.cli(GATE, "create", "--project", "demo", "--gate", "physical-check",
                 "--question", "Owner must verify?", "--choices", "DONE,HOLD",
                 "--owner-only-category", "human-product-judgment-action", "--scope", "work-unit")
        self.publish(self.idle_ready_payload())
        self.put(self.runtime / "worker-leases" / "worker1.json",
                 {"schemaVersion": 1, "sessionId": "worker1", "parentSessionId": "coord1",
                  "role": "worker", "workUnit": "wu-1", "attempt": "1", "state": "handoff-ready"})
        for binding, ref in (("scheduled-review", None), ("owner-gate", "physical-check")):
            with self.subTest(binding=binding):
                self.commitment("promise", binding, "observing", ref=ref)
                _, show = self.cli(STATUS, "show", "--project", "demo")
                self.assertIn("idle-ready-work:independent", show["issues"])
        # A real work observer clears it.
        self.commitment("promise", "worker-lease", "observing", ref="worker1")
        _, working = self.cli(STATUS, "show", "--project", "demo")
        self.assertNotIn("idle-ready-work:independent", working["issues"])

    def test_repeated_timed_out_self_reviews_are_flagged_as_churn(self):
        # Re-scheduling a self-review while nothing executes is the
        # bookkeeping-instead-of-delivery signature.
        self.base_project()
        self.cli(GATE, "create", "--project", "demo", "--gate", "physical-check",
                 "--question", "Owner must verify?", "--choices", "DONE,HOLD",
                 "--owner-only-category", "human-product-judgment-action", "--scope", "work-unit")
        self.publish(self.idle_ready_payload())
        self.put(self.runtime / "worker-leases" / "worker1.json",
                 {"schemaVersion": 1, "sessionId": "worker1", "parentSessionId": "coord1",
                  "role": "worker", "workUnit": "wu-1", "attempt": "1", "state": "handoff-ready"})
        self.commitment("review-r2", "scheduled-review", "resolved-timeout")
        _, one = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in one["issues"] if i.startswith("scheduled-review-churn")])
        self.commitment("review-r3", "scheduled-review", "resolved-timeout")
        _, two = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("scheduled-review-churn:2", two["issues"])
        # An executing lane means the project is delivering, not just reviewing.
        self.lease(state="running")
        _, working = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in working["issues"] if i.startswith("scheduled-review-churn")])

    def github_sync(self, stage="building", issue="razumv/demo#42", **over):
        row = {"issue": issue, "commentRef": f"https://github.com/{issue.replace('#', '/issues/')}#issuecomment-1",
               "projectField": "Status=In progress", "syncedStage": stage, "syncedAt": self.now - 1000}
        row.update(over)
        return row

    def test_material_stage_without_github_sync_is_a_contradiction(self):
        # GitHub is the task source of truth: a stage visible in Craft but absent
        # from the issue/Project board is an unreported stage.
        self.base_project()
        self.publish(self.increment_payload())            # stage=building, no githubSync
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("github-sync-missing:building", show["issues"])
        # A sync naming an older stage is stale, not proof.
        payload = self.increment_payload()
        payload["githubSync"] = self.github_sync(stage="discovery")
        self.publish(payload)
        _, stale = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("github-sync-stale:discovery!=building", stale["issues"])
        # A sync for the current stage clears it and survives the round trip.
        payload["githubSync"] = self.github_sync(stage="building")
        _, ok = self.publish(payload)
        self.assertEqual(ok["record"]["declared"]["githubSync"]["issue"], "razumv/demo#42")
        _, clean = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in clean["issues"] if i.startswith("github-sync")])

    def test_discovery_stage_needs_no_github_sync(self):
        self.base_project()
        payload = self.increment_payload()
        payload["productIncrement"]["stage"] = "discovery"
        self.publish(payload)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in show["issues"] if i.startswith("github-sync")])

    def test_github_sync_shape_fails_closed(self):
        self.base_project()
        for bad, needle in (
            ({"issue": "not-an-issue", "commentRef": "x", "syncedStage": "building", "syncedAt": self.now},
             "owner/repo#123"),
            (self.github_sync(syncedStage="mysterious"), "syncedStage"),
            (self.github_sync(syncedAt=self.now + 3_600_000), "syncedAt"),
            ({"issue": "razumv/demo#42", "commentRef": "authorization: Bearer sk-1",
              "syncedStage": "building", "syncedAt": self.now}, "credential"),
            ("not-an-object", "must be an object"),
        ):
            payload = self.increment_payload()
            payload["githubSync"] = bad
            cp, _ = self.publish(payload, ok=False)
            self.assertIn(needle, cp.stderr)

    def test_accepted_story_must_name_observed_acceptance_evidence(self):
        # v3.4.21: `accepted` builds the owner's counter and Done column, and was
        # the only claim with no evidence requirement — fifteen accepted stories
        # across six live projects were bound to nothing.
        self.base_project()
        payload = self.increment_payload()
        payload["githubSync"] = self.github_sync()
        payload["productIncrement"]["stories"] = [
            {"id": "shipped", "title": "Shipped work", "state": "accepted",
             "dependsOn": [], "riskContribution": "low"}]
        self.publish(payload)
        _, bare = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("story-accepted-without-evidence:shipped", bare["issues"])
        # A named ref nobody observed reads as proof, so it is its own contradiction.
        payload["productIncrement"]["stories"][0]["acceptanceRef"] = "invented-evidence"
        self.publish(payload)
        _, invented = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("story-acceptance-ref-not-observed:shipped", invented["issues"])
        # A completion certificate for the story's work unit is admissible proof.
        cert_dir = self.runtime / "completion-certificates" / "demo"
        cert_dir.mkdir(parents=True, exist_ok=True)
        (cert_dir / "SHIPPED-WORK-abc123.json").write_text(json.dumps(
            {"project": "demo", "workUnit": "shipped-work", "auditVerdict": "PASS"}))
        payload["productIncrement"]["stories"][0]["acceptanceRef"] = "SHIPPED-WORK-abc123"
        self.publish(payload)
        _, certified = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in certified["issues"] if i.startswith("story-accept")])

    def test_merge_claims_are_verified_against_the_clone_not_the_declaration(self):
        # The protocol cannot read GitHub, but it can read the clone: a declared
        # merge commit either is an ancestor of the tracked default branch or is not.
        repo = self.runtime.parent / "repo"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
        (repo / "f").write_text("one")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "one"], check=True, env=env)
        merged = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        # origin/main is what delivery means; a local branch is not delivery.
        subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", merged], check=True)
        (repo / "f").write_text("two")
        subprocess.run(["git", "-C", str(repo), "commit", "-qam", "two"], check=True, env=env)
        unmerged = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()

        self.base_project()
        payload = self.increment_payload()
        payload["githubSync"] = self.github_sync()
        payload["delivery"] = {"repoPath": str(repo), "targetBranch": "main"}
        payload["productIncrement"]["stories"] = [
            {"id": "delivered", "title": "Delivered", "state": "accepted", "dependsOn": [],
             "riskContribution": "low", "acceptanceRef": "cert-1", "mergeSha": merged}]
        cert_dir = self.runtime / "completion-certificates" / "demo"
        cert_dir.mkdir(parents=True, exist_ok=True)
        (cert_dir / "cert-1.json").write_text(json.dumps({"project": "demo", "workUnit": "delivered"}))
        self.publish(payload)
        _, ok = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in ok["issues"] if i.startswith("merge-claim")])
        # A commit that exists but never landed is an unverified claim, not delivery.
        payload["productIncrement"]["stories"][0]["mergeSha"] = unmerged
        self.publish(payload)
        _, bad = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn(f"merge-claim-unverified:delivered@{unmerged[:12]}", bad["issues"])
        # An unreadable repository fails closed instead of passing silently.
        payload["delivery"]["repoPath"] = str(self.runtime / "no-such-repo")
        self.publish(payload)
        _, missing = self.cli(STATUS, "show", "--project", "demo")
        self.assertTrue([i for i in missing["issues"] if i.startswith("delivery-repo-unreadable")])

    def test_accepted_story_at_deploying_stage_needs_a_merge_commit(self):
        self.base_project()
        payload = self.increment_payload()
        payload["githubSync"] = self.github_sync()
        payload["githubSync"]["syncedStage"] = "deploying"
        payload["productIncrement"]["stage"] = "deploying"
        payload["productIncrement"]["stories"] = [
            {"id": "shipped", "title": "Shipped", "state": "accepted", "dependsOn": [],
             "riskContribution": "low", "acceptanceRef": "cert-2"}]
        cert_dir = self.runtime / "completion-certificates" / "demo"
        cert_dir.mkdir(parents=True, exist_ok=True)
        (cert_dir / "cert-2.json").write_text(json.dumps({"project": "demo", "workUnit": "shipped"}))
        payload["delivery"] = {"repoPath": str(self.runtime.parent), "targetBranch": "main"}
        self.publish(payload)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        # No .git means the repository claim fails closed first; the delivery gap is
        # reported once a real clone is named.
        self.assertTrue([i for i in show["issues"] if i.startswith("delivery-repo-unreadable")])

    def test_own_pull_requests_must_be_finished_not_parked(self):
        # Observed live: a green, conflict-free PR sat unmerged for three days while
        # the project published `deploying`.
        self.base_project()
        payload = self.increment_payload()
        payload["githubSync"] = self.github_sync()
        payload["delivery"] = {"repoPath": str(self.runtime.parent),
                               "openPullRequests": [
                                   {"ref": "razumv/demo#7", "state": "green-clean",
                                    "checkedAt": self.now - 4 * 3600 * 1000}]}
        self.publish(payload)
        _, parked = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("pull-request-unfinished:razumv/demo#7:green-clean", parked["issues"])
        # A freshly checked PR is work in flight, not neglect.
        payload["delivery"]["openPullRequests"][0]["checkedAt"] = self.now - 60_000
        self.publish(payload)
        _, fresh = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in fresh["issues"] if i.startswith("pull-request")])
        # Review-required is somebody else's turn, but the check must stay fresh.
        payload["delivery"]["openPullRequests"][0].update(
            {"state": "review-required", "checkedAt": self.now - 8 * 3600 * 1000})
        self.publish(payload)
        _, stale = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("pull-request-check-stale:razumv/demo#7", stale["issues"])

    def test_one_self_granted_extension_escalates_the_second_returns_to_owner(self):
        # A proven deterministic cause with a single-scope fix does not need the
        # owner's judgment; a second attempt at the same story does.
        self.base_project()
        payload = self.increment_payload()
        payload["githubSync"] = self.github_sync()
        payload["nextActions"] = []
        payload["childRefs"] = []
        payload["productIncrement"]["stories"] = [
            {"id": "migration", "title": "Bounded migration", "state": "failed",
             "dependsOn": [], "riskContribution": "medium"}]
        payload["correctionBudgetExtensions"] = [
            {"storyId": "migration", "rootCauseRef": "audit-event-1f49986",
             "correctionScope": "alembic/versions/0010_wallet.py", "grantedAt": self.now-1000}]
        self.publish(payload)
        _, granted = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in granted["issues"]
                          if i.startswith("exhausted-correction-without-escalation")])
        payload["correctionBudgetExtensions"].append(
            {"storyId": "migration", "rootCauseRef": "audit-event-2ab77f0",
             "correctionScope": "alembic/versions/0010_wallet.py", "grantedAt": self.now-500})
        self.publish(payload)
        _, reused = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("correction-budget-extension-reused:migration", reused["issues"])

    def test_complete_increment_without_next_or_gate_is_silent_idle(self):
        # Observed live: a project closed its increment, published `complete`, and
        # simply stopped — indistinguishable from health on the board.
        self.base_project()
        self.publish(self.completion_payload())
        # A freshly published completion is a finished increment, not idleness.
        _, fresh = self.cli(STATUS, "show", "--project", "demo")
        self.assertNotIn("complete-without-next-increment", fresh["issues"])
        # Left standing with no plan and no gate, it is a project that stopped.
        self.env = {**self.env, "CRAFT_STATUS_COMPLETE_IDLE_SECONDS": "0"}
        _, idle = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("complete-without-next-increment", idle["issues"])
        # Asking the owner what to take next is the sanctioned way to pause.
        self.cli(GATE, "create", "--project", "demo", "--gate", "next-increment-selection",
                 "--question", "Which increment should the project take next?",
                 "--choices", "BILLING,MOBILE", "--owner-only-category",
                 "human-product-judgment-action", "--scope", "project")
        _, asked = self.cli(STATUS, "show", "--project", "demo")
        self.assertNotIn("complete-without-next-increment", asked["issues"])

    def test_dead_lane_this_generation_dispatched_must_be_replaced(self):
        # A lane the coordinator itself dispatched and that died is neglect, not
        # housekeeping debt: holding it silently is how a project looks busy.
        self.base_project()
        payload = self.increment_payload()
        payload["githubSync"] = self.github_sync()
        self.publish(payload)
        reg = json.loads((self.runtime / "coordinators" / "demo.json").read_text())
        self.put(self.runtime / "worker-leases" / "worker1.json",
                 {"schemaVersion": 1, "sessionId": "worker1", "parentSessionId": "coord1",
                  "role": "worker", "workUnit": "wu-1", "attempt": "1", "state": "stalled",
                  "createdAt": int(reg.get("claimedAt") or 0) + 1000})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("dead-lane-unreplaced:wu-1", show["issues"])
        # A lane inherited from an earlier generation is housekeeping debt, not neglect.
        self.put(self.runtime / "worker-leases" / "worker1.json",
                 {"schemaVersion": 1, "sessionId": "worker1", "parentSessionId": "coord1",
                  "role": "worker", "workUnit": "wu-1", "attempt": "1", "state": "stalled",
                  "createdAt": int(reg.get("claimedAt") or 0) - 60_000})
        _, inherited = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in inherited["issues"] if i.startswith("dead-lane")])

    def test_failed_story_without_plan_gate_or_lane_is_unescalated(self):
        # The correction budget is bounded; a failed story with no plan, no lane and
        # no owner gate is a dead end the owner never hears about.
        self.base_project()
        payload = self.increment_payload()
        payload["githubSync"] = self.github_sync()
        payload["nextActions"] = []
        payload["childRefs"] = []
        payload["productIncrement"]["stories"] = [
            {"id": "correction", "title": "Correction attempt", "state": "failed",
             "dependsOn": [], "riskContribution": "medium"},
            {"id": "kept", "title": "Kept evidence", "state": "accepted",
             "dependsOn": [], "riskContribution": "low"}]
        self.publish(payload)
        self.put(self.runtime / "worker-leases" / "worker1.json",
                 {"schemaVersion": 1, "sessionId": "worker1", "parentSessionId": "coord1",
                  "role": "worker", "workUnit": "wu-1", "attempt": "1", "state": "handoff-ready"})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertIn("exhausted-correction-without-escalation:correction", show["issues"])
        # An owner gate is the sanctioned escalation and clears it.
        self.cli(GATE, "create", "--project", "demo", "--gate", "correction-exhausted",
                 "--question", "Authorise an exceptional third attempt?", "--choices", "AUTHORIZE,HOLD",
                 "--owner-only-category", "human-product-judgment-action", "--scope", "work-unit")
        _, escalated = self.cli(STATUS, "show", "--project", "demo")
        self.assertFalse([i for i in escalated["issues"]
                          if i.startswith("exhausted-correction-without-escalation")])

    def test_blocked_publish_with_open_gate_reference_is_allowed(self):
        self.base_project()
        self.cli(GATE, "create", "--project", "demo", "--gate", "ship-decision",
                 "--question", "Ship the paid tier to production now?", "--choices", "SHIP,WAIT",
                 "--owner-only-category", "human-product-judgment-action",
                 "--scope", "work-unit", "--work-unit", "wu-1")
        _, pub = self.publish({"objective": "x", "phase": "blocked", "currentFocus": "owner decision",
                               "gateRefs": ["ship-decision"], "nextActions": []})
        self.assertEqual(pub["revision"], 1)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "blocked")

    def test_hold_publish_requires_open_explicit_hold_gate(self):
        self.base_project()
        cp, _ = self.publish({"objective": "x", "phase": "hold", "nextActions": []}, ok=False)
        self.assertIn("self-hold", cp.stderr)
        self.cli(GATE, "hold", "--project", "demo", "--reason", "direct owner hold")
        _, pub = self.publish({"objective": "x", "phase": "hold", "nextActions": []})
        self.assertEqual(pub["revision"], 1)

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
        self.base_project()
        self.submit("candidate", "candidate ready")
        self.lease(state="handoff-ready")
        payload = self.executing_payload(); payload["childRefs"] = ["worker1"]
        self.publish(payload)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "executing")

    def test_candidate_only_cannot_verify_completion(self):
        self.base_project()
        _, submitted = self.submit("candidate", "candidate ready")
        self.lease(state="handoff-ready")
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
        self.lease("worker2", work_unit="wu-2")
        self.submit("candidate", "later unrelated candidate", sender="worker2", work_unit="wu-2")
        self.lease("worker2", work_unit="wu-2", state="handoff-ready")
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

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

    def lease(self, sid="worker1", parent="coord1", work_unit="wu-1", attempt="1", state="running"):
        self.manifest(sid, role="worker", labels=[f"parent-session::{parent}",
                      f"work-unit::{work_unit}", f"attempt::{attempt}"])
        self.put(self.runtime / "worker-leases" / f"{sid}.json",
                 {"schemaVersion": 1, "sessionId": sid, "parentSessionId": parent,
                  "role": "worker", "workUnit": work_unit, "attempt": attempt, "state": state})

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

    def test_submit_rejects_credentials_in_evidence(self):
        self.base_project()
        cp, _ = self.submit("progress", evidence=["authorization: Bearer sk-1"], ok=False)
        self.assertIn("credential", cp.stderr)

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
        # ack with terminal evidence consumes
        _, acked = self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--token", token,
                            "--terminal-evidence", "pushed abc123", "--apply")
        self.assertEqual(len(acked["acked"]), 1)
        _, report = self.cli(INBOX, "report", "--project", "demo")
        self.assertEqual(report["projects"][0]["summary"]["total"], 0)

    def test_duplicate_ack_is_idempotent(self):
        self.base_project()
        self.submit("terminal-handoff", "candidate")
        _, claim = self.cli(INBOX, "claim", "--project", "demo", "--session", "coord1",
                            "--generation", "2", "--apply")
        token = claim["token"]
        self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1", "--generation", "2",
                 "--token", token, "--terminal-evidence", "x", "--apply")
        _, again = self.cli(INBOX, "ack", "--project", "demo", "--session", "coord1", "--generation", "2",
                            "--token", token, "--terminal-evidence", "x", "--apply")
        self.assertTrue(again["idempotent"])

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
                "childRefs": ["worker1"],
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
        cp, _ = self.publish({"objective": "x", "phase": "waiting", "nextActions": []}, ok=False)
        self.assertIn("commitment", cp.stderr)
        # register a commitment then publishing waiting succeeds
        self.cli(COMMIT, "register", "--project", "demo", "--session", "coord1", "--generation", "2",
                 "--commitment-id", "c1", "--subject", "await", "--binding-kind", "worker-lease",
                 "--ref", "worker1", "--deadline-seconds", "3600", "--success-action", "merge",
                 "--failure-action", "rework", "--apply")
        _, pub = self.publish({"objective": "x", "phase": "waiting", "commitmentRefs": ["c1"],
                               "childRefs": ["worker1"]})
        self.assertEqual(pub["revision"], 1)
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "waiting-observed")

    def test_contradiction_complete_with_active_workers(self):
        self.base_project()
        self.publish({"objective": "x", "phase": "complete", "nextActions": []})
        _, show = self.cli(STATUS, "show", "--project", "demo")
        self.assertEqual(show["classification"], "contradictory")

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

    def test_overdue_emits_incident_reason(self):
        self.base_project()
        self.register(binding="scheduled-review", ref=None, deadline=60)
        far = self.now + 10_000_000
        _, rec = self.cli(COMMIT, "reconcile", "--apply", now=far)
        reasons = [a["reason"] for a in rec["actions"]]
        self.assertIn("deadline-overdue", reasons)

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

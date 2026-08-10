# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import json, os, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts"))
NOW = 1_786_370_000_000


class ExternalWaitV321Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"; self.sessions = self.root / "sessions"
        self.env = {**os.environ, "CRAFT_RUNTIME": str(self.runtime), "CRAFT_SESSIONS": str(self.sessions),
                    "CRAFT_WORKSPACE": str(self.root), "CRAFT_TEST_NOW_MS": str(NOW)}
        self.manifest("coord", "coordinator")
        self.manifest("watch", "worker")
        self.put(self.runtime / "coordinators/alpha.json", {
            "schemaVersion": 1, "project": "alpha", "coordinatorSessionId": "coord",
            "state": "authoritative", "leaseExpiresAt": NOW + 3_600_000})
        self.put(self.runtime / "worker-leases/watch.json", {
            "schemaVersion": 1, "sessionId": "watch", "parentSessionId": "coord", "role": "worker",
            "state": "running", "workUnit": "325", "lastHeartbeatAt": NOW})
        self.put(self.runtime / "worker-jobs/watch.json", {
            "schemaVersion": 1, "sessionId": "watch", "jobId": "run-610", "supervisorPid": os.getpid(),
            "childPid": None, "command": ["gh", "run", "watch", "610", "--exit-status"],
            "exitCode": None, "finishedAt": None, "reportedAt": None})

    def tearDown(self): self.tmp.cleanup()

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value) + "\n")

    def manifest(self, sid, role):
        self.put(self.sessions / sid / "session.jsonl", {
            "id": sid, "labels": [f"agent-role::{role}"], "isArchived": False,
            "sessionStatus": "todo", "workingDirectory": str(self.root)})

    def cli(self, script, *args, ok=True, env=None):
        cp = subprocess.run([str(SCRIPTS / script), *args], env=env or self.env,
                            text=True, capture_output=True, timeout=20)
        if ok and cp.returncode:
            self.fail(cp.stdout + cp.stderr)
        payload = json.loads(cp.stdout) if cp.returncode == 0 and cp.stdout else None
        return cp, payload

    def register(self, **overrides):
        values = {"wait_id": "pr-326-ci", "project": "alpha", "coordinator": "coord", "work_unit": "325",
                  "kind": "github-actions", "subject": "run:610@sha:abc", "watcher_session": "watch", "timeout": "600"}
        values.update(overrides)
        args = []
        for key, value in values.items(): args += ["--" + key.replace("_", "-"), str(value)]
        return self.cli("external-wait.py", "register", *args, "--apply")

    def test_register_requires_exact_active_observer_receipt(self):
        _, row = self.register()
        self.assertTrue(row["applied"]); self.assertEqual(row["wait"]["state"], "observing")
        self.assertEqual(row["wait"]["jobId"], "run-610")
        self.assertEqual(len(row["wait"]["observerCommandSha256"]), 64)
        (self.runtime / "worker-jobs/watch.json").unlink()
        cp = subprocess.run([str(SCRIPTS / "external-wait.py"), "register", "--wait-id", "missing",
                             "--project", "alpha", "--coordinator", "coord", "--work-unit", "325",
                             "--kind", "github-actions", "--subject", "run:611", "--watcher-session", "watch",
                             "--timeout", "600", "--apply"], env=self.env, text=True, capture_output=True)
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse((self.runtime / "external-waits/missing.json").exists())

    def test_terminal_receipt_reconciles_and_clear_acknowledges_job(self):
        self.register()
        premature = subprocess.run([str(SCRIPTS / "external-wait.py"), "clear", "--wait-id", "pr-326-ci",
                                    "--coordinator", "coord", "--evidence", "not terminal", "--apply"],
                                   env=self.env, text=True, capture_output=True)
        self.assertNotEqual(premature.returncode, 0)
        self.assertEqual(json.loads((self.runtime / "external-waits/pr-326-ci.json").read_text())["state"], "observing")
        job_path = self.runtime / "worker-jobs/watch.json"; job = json.loads(job_path.read_text())
        job.update(exitCode=0, finishedAt=NOW + 1000); self.put(job_path, job)
        _, row = self.cli("external-wait.py", "reconcile", "--apply")
        self.assertEqual(row["waits"][0]["state"], "terminal")
        _, cleared = self.cli("external-wait.py", "clear", "--wait-id", "pr-326-ci", "--coordinator", "coord",
                              "--evidence", "exact run 610 success at sha abc consumed", "--apply")
        self.assertEqual(cleared["wait"]["state"], "cleared")
        self.assertEqual(json.loads(job_path.read_text())["reportedAt"], NOW)

    def test_clear_invalid_terminal_receipt_persists_unobserved(self):
        self.register()
        wait_path = self.runtime / "external-waits/pr-326-ci.json"
        wait = json.loads(wait_path.read_text()); wait.update(state="terminal", terminalExitCode=0)
        self.put(wait_path, wait)
        job_path = self.runtime / "worker-jobs/watch.json"; job = json.loads(job_path.read_text())
        job.update(exitCode=0, finishedAt=NOW + 1000, command=["changed", "command"])
        self.put(job_path, job)
        cp = subprocess.run([str(SCRIPTS / "external-wait.py"), "clear", "--wait-id", "pr-326-ci",
                             "--coordinator", "coord", "--evidence", "attempt consume", "--apply"],
                            env=self.env, text=True, capture_output=True)
        self.assertEqual(cp.returncode, 2)
        durable = json.loads(wait_path.read_text())
        self.assertEqual(durable["state"], "unobserved")
        self.assertTrue(durable["clearTransactionPending"])
        self.assertEqual(durable["reason"], "clear-transaction-terminal-receipt-missing")

    def test_clear_crash_journal_reconciles_job_and_wait(self):
        self.register()
        job_path = self.runtime / "worker-jobs/watch.json"; job = json.loads(job_path.read_text())
        job.update(exitCode=0, finishedAt=NOW + 1000); self.put(job_path, job)
        self.cli("external-wait.py", "reconcile", "--apply")
        crashing = {**self.env, "CRAFT_TEST_CRASH_AFTER_CLEAR_JOURNAL": "1"}
        cp = subprocess.run([str(SCRIPTS / "external-wait.py"), "clear", "--wait-id", "pr-326-ci",
                             "--coordinator", "coord", "--evidence", "run 610 consumed", "--apply"],
                            env=crashing, text=True, capture_output=True)
        self.assertEqual(cp.returncode, 75)
        self.assertEqual(json.loads((self.runtime / "external-waits/pr-326-ci.json").read_text())["state"], "clearing")
        self.assertIsNone(json.loads(job_path.read_text())["reportedAt"])
        _, report = self.cli("external-wait.py", "reconcile", "--apply")
        self.assertEqual(report["waits"][0]["state"], "cleared")
        self.assertEqual(json.loads(job_path.read_text())["reportedAt"], NOW)

    def test_missing_process_and_deadline_fail_closed(self):
        self.register()
        job_path = self.runtime / "worker-jobs/watch.json"; job = json.loads(job_path.read_text())
        job["supervisorPid"] = 99999999; self.put(job_path, job)
        _, row = self.cli("external-wait.py", "reconcile", "--apply")
        self.assertEqual(row["waits"][0]["state"], "unobserved")
        # Re-register a distinct wait with a live observer, then cross its deadline.
        self.put(job_path, {**job, "supervisorPid": os.getpid(), "exitCode": None, "reportedAt": None})
        self.register(wait_id="deadline", timeout="60")
        later = {**self.env, "CRAFT_TEST_NOW_MS": str(NOW + 61_000)}
        _, row = self.cli("external-wait.py", "reconcile", "--apply", env=later)
        states = {w["waitId"]: w["state"] for w in row["waits"]}
        self.assertEqual(states["deadline"], "deadline")

    def test_concurrent_register_has_one_winner(self):
        command = [str(SCRIPTS / "external-wait.py"), "register", "--wait-id", "race",
                   "--project", "alpha", "--coordinator", "coord", "--work-unit", "325",
                   "--kind", "github-actions", "--subject", "run:610@sha:abc",
                   "--watcher-session", "watch", "--timeout", "600", "--apply"]
        procs = [subprocess.Popen(command, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(2)]
        results = [proc.communicate(timeout=20) + (proc.returncode,) for proc in procs]
        self.assertEqual(sorted(result[2] for result in results), [0, 1])
        self.assertEqual(json.loads((self.runtime / "external-waits/race.json").read_text())["state"], "observing")

    def test_changed_observer_command_and_secret_subject_fail_closed(self):
        self.register()
        job_path = self.runtime / "worker-jobs/watch.json"; job = json.loads(job_path.read_text())
        job["command"] = ["sleep", "999"]
        self.put(job_path, job)
        _, row = self.cli("external-wait.py", "reconcile", "--apply")
        self.assertEqual(row["waits"][0]["state"], "unobserved")
        cp = subprocess.run([str(SCRIPTS / "external-wait.py"), "register", "--wait-id", "secret",
                             "--project", "alpha", "--coordinator", "coord", "--work-unit", "325",
                             "--kind", "github-actions", "--subject", "token=do-not-store",
                             "--watcher-session", "watch", "--timeout", "600", "--apply"],
                            env=self.env, text=True, capture_output=True)
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse((self.runtime / "external-waits/secret.json").exists())

    def test_recovery_incident_uses_external_wait_semantics_not_generic_job_exit(self):
        self.register()
        wait_path = self.runtime / "external-waits/pr-326-ci.json"; wait = json.loads(wait_path.read_text())
        wait.update(state="terminal", terminalExitCode=0, terminalAt=NOW + 1000, reason="watcher-job-terminal")
        self.put(wait_path, wait)
        job = json.loads((self.runtime / "worker-jobs/watch.json").read_text())
        job.update(exitCode=0, finishedAt=NOW + 1000); self.put(self.runtime / "worker-jobs/watch.json", job)
        _, report = self.cli("recovery-incident.py", "detect")
        kinds = [row["kind"] for row in report["observations"]]
        self.assertIn("external-wait-terminal", kinds)
        self.assertNotIn("job-exit-unreported", kinds)
        incident = next(row for row in report["observations"] if row["kind"] == "external-wait-terminal")
        self.assertEqual(incident["coordinatorSessionId"], "coord")
        self.assertIn("wake-coordinator", incident["allowedActions"])


if __name__ == "__main__":
    unittest.main()

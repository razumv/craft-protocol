# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class Correction6V3435(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.runtime = self.root / "runtime"
        self.env = {**os.environ, "CRAFT_WORKSPACE": str(self.root),
                    "CRAFT_SESSIONS": str(self.sessions), "CRAFT_RUNTIME": str(self.runtime)}
        self.manifest("coord", "coordinator", ["coordinators", "project::p", "protocol-version::3.4.35"],
                      name="[p] Coordinator v3.4.35", project_id="native")
        (self.runtime / "coordinators").mkdir(parents=True)
        self.put(self.runtime / "coordinators/p.json", {"project": "p", "projectId": "native",
                 "state": "authoritative", "coordinatorSessionId": "coord", "generation": 1})

    def tearDown(self):
        self.temp.cleanup()

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def manifest(self, sid, role, labels=(), *, name=None, project_id="native", worktree=None):
        wt = Path(worktree or self.root / f"wt-{sid}")
        wt.mkdir(parents=True, exist_ok=True)
        self.put(self.sessions / sid / "session.jsonl", {
            "id": sid, "isArchived": False, "sessionStatus": "todo", "workspaceRootPath": str(self.root),
            "workingDirectory": str(wt), "projectId": project_id, "name": name or sid,
            "llmConnection": "chatgpt-plus", "model": "pi/gpt-5.6-sol" if role == "coordinator" else "pi/gpt-5.6-terra",
            "permissionMode": "allow-all", "labels": [f"agent-role::{role}", *labels],
        })

    def tool(self, name, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / name), *args], env=self.env,
                                text=True, capture_output=True, timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def admit_worker(self, sid="worker"):
        self.manifest(sid, "worker", ["parent-session::coord", "project::p", "work-unit::u", "attempt::1", "protocol-version::3.4.35"])
        path = self.sessions / sid / "session.jsonl"
        saved = path.read_text(); path.unlink()
        self.tool("lane-admission.py", "reserve", "--token", sid, "--parent", "coord", "--role", "worker",
                  "--work-unit", "u", "--attempt", "1", "--worktree", str(self.root / f"wt-{sid}"))
        path.write_text(saved)
        self.tool("lane-admission.py", "confirm", "--token", sid, "--session", sid)
        self.tool("worker-lease.py", "create", "--session", sid, "--admission-token", sid)

    def drift_worker_admission(self, sid="worker"):
        path = self.sessions / sid / "session.jsonl"
        row = json.loads(path.read_text())
        row["labels"] = ["attempt::2" if x == "attempt::1" else x for x in row["labels"]]
        self.put(path, row)

    def assert_quarantined(self, sid="worker"):
        row = json.loads((self.runtime / f"worker-leases/{sid}.json").read_text())
        self.assertEqual((row["state"], row["phase"]), ("error", "admission-fail-closed"))
        self.assertTrue(row["lastError"])

    def test_live_admission_failures_quarantine_heartbeat_finish_and_observable_job(self):
        for command in (("worker-lease.py", "heartbeat", "--session", "worker"),
                        ("worker-lease.py", "finish", "--session", "worker"),
                        ("observable-job.py", "start", "--session", "worker", "--cwd", str(self.root / "wt-worker"),
                         "--log", str(self.root / "job.log"), "--", "/bin/echo", "no")):
            with self.subTest(command=command[0:2]):
                self.admit_worker(); self.drift_worker_admission()
                result = self.tool(*command, ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assert_quarantined()
                (self.runtime / "worker-leases/worker.json").unlink()

    def test_admission_valid_and_legacy_lease_paths_remain_usable(self):
        self.admit_worker()
        self.tool("worker-lease.py", "heartbeat", "--session", "worker", "--phase", "working")
        self.assertEqual(json.loads((self.runtime / "worker-leases/worker.json").read_text())["state"], "running")
        self.manifest("legacy", "worker", ["parent-session::coord", "project::p"])
        self.tool("worker-lease.py", "heartbeat", "--session", "legacy", "--phase", "working")
        self.assertEqual(json.loads((self.runtime / "worker-leases/legacy.json").read_text())["state"], "running")

    def test_lane_collision_canonicalizes_stored_admission_and_legacy_lease_paths(self):
        real = Path(tempfile.mkdtemp(dir="/var/tmp")).resolve()
        self.addCleanup(lambda: real.rmdir())
        if not str(real).startswith("/private/var/"):
            self.skipTest("macOS /var alias unavailable")
        alias = "/var" + str(real)[len("/private/var"):]
        identity = {"token": "prior", "worktree": alias}
        self.put(self.runtime / "lane-admissions/prior.json", {"token": "prior", "state": "reserved", "identity": identity})
        result = self.tool("lane-admission.py", "reserve", "--token", "new", "--parent", "coord", "--role", "worker",
                           "--work-unit", "u", "--attempt", "1", "--worktree", str(real), ok=False)
        self.assertIn("live admission collision", result.stderr)
        (self.runtime / "lane-admissions/prior.json").unlink()
        self.manifest("legacy-manifest", "worker", ["parent-session::coord"], worktree=alias)
        result = self.tool("lane-admission.py", "reserve", "--token", "new", "--parent", "coord", "--role", "worker",
                           "--work-unit", "u", "--attempt", "1", "--worktree", str(real), ok=False)
        self.assertIn("live manifest worktree collision", result.stderr)
        path = self.sessions / "legacy-manifest/session.jsonl"; row = json.loads(path.read_text()); row["isArchived"] = True; self.put(path, row)
        self.put(self.runtime / "worker-leases/legacy.json", {"sessionId": "legacy", "state": "running", "worktree": alias})
        result = self.tool("lane-admission.py", "reserve", "--token", "new", "--parent", "coord", "--role", "worker",
                           "--work-unit", "u", "--attempt", "1", "--worktree", str(real), ok=False)
        self.assertIn("live lease worktree collision", result.stderr)

    def test_reporting_policy_rejects_malformed_durable_state_and_empty_identifiers(self):
        self.tool("reporting-policy.py", "configure", "--owner-facing-session", "owner")
        self.tool("reporting-policy.py", "query")
        for bad in ("", "   "):
            self.assertNotEqual(self.tool("reporting-policy.py", "configure", "--owner-facing-session", bad, ok=False).returncode, 0)
        self.assertNotEqual(self.tool("reporting-policy.py", "check", "--session", "", ok=False).returncode, 0)
        valid = json.loads((self.runtime / "reporting-policy.json").read_text())
        malformed = [
            {**valid, "schemaVersion": 2}, {**valid, "mode": "push"}, {**valid, "ownerFacingSessionId": " "},
            {**valid, "configuredAt": 0}, {**valid, "configuredAt": "1"},
            {**valid, "configuredAt": int(time.time() * 1000) + 60_000},
            {**valid, "interception": "available"}, {**valid, "detection": "unknown"},
        ]
        for policy in malformed:
            self.put(self.runtime / "reporting-policy.json", policy)
            self.assertNotEqual(self.tool("reporting-policy.py", "query", ok=False).returncode, 0)
            self.assertNotEqual(self.tool("reporting-policy.py", "check", "--session", "coord", ok=False).returncode, 0)

    def test_current_coordinator_requires_raw_label_cardinality_and_legacy_inspect_works(self):
        self.tool("reporting-policy.py", "configure", "--owner-facing-session", "owner")
        self.tool("coordinator-registry.py", "claim", "--project", "p", "--session", "coord", "--project-id", "native")
        path = self.sessions / "coord/session.jsonl"; current = json.loads(path.read_text())
        current["labels"].append("agent-role::coordinator"); self.put(path, current)
        inspect = self.tool("coordinator-registry.py", "inspect", "--project", "p", ok=False)
        self.assertIn("canonical-coordinator-identity-mismatch", inspect.stdout)
        current["labels"].pop(); self.put(path, current)
        cases = [
            ["coordinators", "agent-role::coordinator", "agent-role::coordinator", "project::p", "protocol-version::3.4.35"],
            ["coordinators", "agent-role::coordinator", "agent-role::worker", "project::p", "protocol-version::3.4.35"],
            ["coordinators", "agent-role::coordinator", "project::p", "project::p", "protocol-version::3.4.35"],
            ["coordinators", "agent-role::coordinator", "project::p", "project::other", "protocol-version::3.4.35"],
            ["coordinators", "agent-role::coordinator", "project::p", "protocol-version::3.4.35", "protocol-version::3.4.35"],
            ["coordinators", "agent-role::coordinator", "project::p", "protocol-version::3.4.35", "protocol-version::3.4.34"],
        ]
        for number, labels in enumerate(cases):
            sid = f"bad-{number}"
            self.manifest(sid, "coordinator", name="[p] Coordinator v3.4.35")
            path = self.sessions / sid / "session.jsonl"; row = json.loads(path.read_text())
            row["labels"] = labels; self.put(path, row)
            self.assertIn("canonical coordinator identity mismatch", self.tool(
                "coordinator-registry.py", "claim", "--project", "p", "--session", sid, "--project-id", "native", ok=False).stderr)
        path = self.sessions / "coord/session.jsonl"; legacy = json.loads(path.read_text())
        legacy["labels"] = ["coordinators", "agent-role::coordinator", "project::p", "protocol-version::3.4.34"]
        self.put(path, legacy)
        self.tool("coordinator-registry.py", "inspect", "--project", "p")


if __name__ == "__main__":
    unittest.main()

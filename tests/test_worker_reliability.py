import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts"))


class ReliabilityToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.runtime = self.root / "runtime"
        self.pids = self.root / "pids"
        self.sessions.mkdir()
        self.env = os.environ.copy()
        self.env.update({
            "CRAFT_WORKSPACE": str(self.root),
            "CRAFT_SESSIONS": str(self.sessions),
            "CRAFT_RUNTIME": str(self.runtime),
            "CRAFT_PID_DIR": str(self.pids),
            "CRAFT_LEASE_HEALTHY_SECONDS": "1",
            "CRAFT_LEASE_STALLED_SECONDS": "2",
        })

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self, sid="s1", archived=False, status=None, worktree=None, event=None):
        folder = self.sessions / sid
        folder.mkdir(exist_ok=True)
        wt = worktree or (self.root / f"wt-{sid}")
        Path(wt).mkdir(exist_ok=True)
        value = {
            "id": sid,
            "createdAt": int(time.time() * 1000),
            "lastMessageAt": int(time.time() * 1000),
            "isArchived": archived,
            "sessionStatus": status,
            "workingDirectory": str(wt),
            "labels": ["agent-role::worker", "parent-session::parent", "work-unit::unit"],
            "name": sid,
        }
        lines = [json.dumps(value)]
        if event:
            lines.append(json.dumps(event))
        (folder / "session.jsonl").write_text("\n".join(lines) + "\n")
        return folder / "session.jsonl"

    def exec_tool(self, script, *args, check=True):
        return subprocess.run([str(SCRIPTS / script), *args], env=self.env,
                              capture_output=True, text=True, check=check, timeout=20)

    def test_reconcile_creates_and_archive_removes_all_runtime(self):
        path = self.manifest()
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        lease = self.runtime / "worker-leases/s1.json"
        job = self.runtime / "worker-jobs/s1.json"
        pid = self.pids / "s1.pid"
        self.assertTrue(lease.exists())
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_text("{}")
        self.pids.mkdir(parents=True, exist_ok=True)
        pid.write_text("999999")
        manifest = json.loads(path.read_text().splitlines()[0])
        manifest["isArchived"] = True
        path.write_text(json.dumps(manifest) + "\n")
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        self.assertFalse(lease.exists())
        self.assertFalse(job.exists())
        self.assertFalse(pid.exists())

    def test_coordinator_heartbeat_does_not_create_worker_lease(self):
        path = self.manifest("coord")
        value = json.loads(path.read_text().splitlines()[0])
        value["labels"] = ["agent-role::coordinator"]
        path.write_text(json.dumps(value) + "\n")
        job = self.runtime / "worker-jobs/coord.json"
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_text(json.dumps({"sessionId": "coord", "exitCode": None}))
        self.exec_tool("worker-lease.py", "heartbeat", "--session", "coord", "--phase", "build")
        self.assertFalse((self.runtime / "worker-leases/coord.json").exists())
        self.assertTrue(job.exists())

    def test_terminal_and_error_classification(self):
        self.manifest("terminal", status="needs-review")
        self.manifest("errored", event={"timestamp": int(time.time() * 1000),
                                         "type": "error", "content": "Connection Error"})
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        terminal = json.loads((self.runtime / "worker-leases/terminal.json").read_text())
        errored = json.loads((self.runtime / "worker-leases/errored.json").read_text())
        self.assertEqual(terminal["state"], "handoff-ready")
        self.assertEqual(errored["state"], "error")

    def test_missing_session_heartbeat_self_removes(self):
        lease = self.runtime / "worker-leases/missing.json"
        lease.parent.mkdir(parents=True)
        lease.write_text(json.dumps({"sessionId": "missing", "state": "running"}))
        self.exec_tool("worker-lease.py", "heartbeat", "--session", "missing")
        self.assertFalse(lease.exists())

    def test_create_refuses_role_drift_parents(self):
        # Self-parented lane is refused.
        self.manifest("selfy")
        proc = self.exec_tool("worker-lease.py", "create", "--session", "selfy",
                              "--parent", "selfy", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("self-parented", proc.stderr)
        # A live worker parenting a sub-lane is refused; only coordinators own lanes.
        self.manifest("rogue-parent")
        self.manifest("child")
        proc = self.exec_tool("worker-lease.py", "create", "--session", "child",
                              "--parent", "rogue-parent", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a live coordinator", proc.stderr)
        # A live coordinator parent is accepted.
        coord = self.manifest("coord-parent")
        value = json.loads(coord.read_text().splitlines()[0])
        value["labels"] = ["agent-role::coordinator"]
        coord.write_text(json.dumps(value) + "\n")
        self.exec_tool("worker-lease.py", "create", "--session", "child",
                       "--parent", "coord-parent")
        # An absent parent manifest stays permitted for watchdog backfill.
        self.manifest("orphan")
        self.exec_tool("worker-lease.py", "create", "--session", "orphan",
                       "--parent", "missing-coordinator")

    def test_create_refuses_worktree_collision(self):
        shared = self.root / "shared-wt"
        self.manifest("lane1", worktree=shared)
        self.exec_tool("worker-lease.py", "create", "--session", "lane1")
        # Idempotent re-create for the same session stays allowed.
        self.exec_tool("worker-lease.py", "create", "--session", "lane1")
        self.manifest("lane2", worktree=shared)
        proc = self.exec_tool("worker-lease.py", "create", "--session", "lane2", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("worktree already", proc.stderr)

    def test_observable_job_writes_exit_receipt(self):
        self.manifest("job")
        self.exec_tool("worker-lease.py", "create", "--session", "job")
        log = self.root / "job.log"
        self.exec_tool("observable-job.py", "start", "--session", "job", "--cwd", str(self.root),
                 "--log", str(log), "--", "/bin/sh", "-c", "echo observable-ok")
        receipt = self.runtime / "worker-jobs/job.json"
        deadline = time.time() + 5
        value = None
        while time.time() < deadline:
            if receipt.exists():
                value = json.loads(receipt.read_text())
                if value.get("exitCode") is not None:
                    break
            time.sleep(0.1)
        self.assertIsNotNone(value)
        self.assertEqual(value.get("exitCode"), 0)
        self.assertIn("observable-ok", log.read_text())

    def test_heavy_jobs_are_serialized(self):
        self.manifest("heavy1")
        self.manifest("heavy2")
        self.exec_tool("worker-lease.py", "create", "--session", "heavy1")
        self.exec_tool("worker-lease.py", "create", "--session", "heavy2")
        self.exec_tool("observable-job.py", "start", "--session", "heavy1", "--cwd", str(self.root),
                       "--log", str(self.root / "heavy1.log"), "--heavy", "--",
                       "/bin/sh", "-c", "sleep 1")
        deadline = time.time() + 3
        while time.time() < deadline and not (self.runtime / "heavy-job-owner.json").exists():
            time.sleep(0.05)
        self.assertTrue((self.runtime / "heavy-job-owner.json").exists())
        self.exec_tool("observable-job.py", "start", "--session", "heavy2", "--cwd", str(self.root),
                       "--log", str(self.root / "heavy2.log"), "--heavy", "--", "/bin/echo", "no")
        receipt2 = self.runtime / "worker-jobs/heavy2.json"
        value2 = None
        deadline = time.time() + 3
        while time.time() < deadline:
            if receipt2.exists():
                value2 = json.loads(receipt2.read_text())
                if value2.get("exitCode") is not None:
                    break
            time.sleep(0.05)
        self.assertEqual(value2.get("exitCode"), 75)
        receipt1 = self.runtime / "worker-jobs/heavy1.json"
        deadline = time.time() + 3
        while time.time() < deadline:
            value1 = json.loads(receipt1.read_text()) if receipt1.exists() else {}
            if value1.get("exitCode") is not None:
                break
            time.sleep(0.05)
        self.assertEqual(value1.get("exitCode"), 0)

    def test_live_coordinator_blocks_reap_of_archived_worker_sharing_cwd(self):
        code = (
            "import importlib.util,tempfile,pathlib;"
            f"p='{SCRIPTS / 'post-archive-reaper.py'}';"
            "s=importlib.util.spec_from_file_location('r',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "cwd=str(pathlib.Path(tempfile.mkdtemp()).resolve());"
            "rows={'coord':{'id':'coord','workingDirectory':cwd,'isArchived':False,'labels':['agent-role::coordinator']},"
            "'worker':{'id':'worker','workingDirectory':cwd,'isArchived':True,'labels':['agent-role::worker']}};"
            "active,archived=m.classify_session_cwds(rows);"
            "assert active=={cwd:['coord']},active;assert archived=={cwd:['worker']},archived"
        )
        subprocess.run(["python3", "-c", code], env=self.env, check=True, timeout=20)

    def test_archived_absent_and_clean_auditor_lanes_are_reap_safe(self):
        code = (
            "import importlib.util,tempfile,pathlib,subprocess;"
            f"p='{SCRIPTS / 'post-archive-reaper.py'}';"
            "s=importlib.util.spec_from_file_location('r',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "t=pathlib.Path(tempfile.mkdtemp());missing=t/'missing';"
            "assert m.work_preserved(str(missing))[0];"
            "repo=t/'repo';repo.mkdir();subprocess.run(['git','init','-q'],cwd=repo,check=True);"
            "subprocess.run(['git','config','user.email','test@example.com'],cwd=repo,check=True);"
            "subprocess.run(['git','config','user.name','Test'],cwd=repo,check=True);"
            "(repo/'x').write_text('x');subprocess.run(['git','add','x'],cwd=repo,check=True);"
            "subprocess.run(['git','commit','-qm','x'],cwd=repo,check=True);"
            "assert m.work_preserved(str(repo),readonly_auditors=True)[0]"
        )
        subprocess.run(["python3", "-c", code], env=self.env, check=True, timeout=20)

    def test_non_harness_process_is_never_accepted(self):
        code = (
            "import importlib.util,os;"
            f"p='{SCRIPTS / 'post-archive-reaper.py'}';"
            "s=importlib.util.spec_from_file_location('r',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "raise SystemExit(0 if not m.harness_ok(str(os.getpid())) else 1)"
        )
        subprocess.run(["python3", "-c", code], env=self.env, check=True, timeout=20)


if __name__ == "__main__":
    unittest.main()

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
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

    def test_report_exposes_archivable_backlog(self):
        self.manifest("done1", status="needs-review")
        self.manifest("done2", status="needs-review")
        self.manifest("dirty", status="needs-review")
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        for sid, preservation in (("done1", "pushed"), ("done2", "merged")):
            lease_path = self.runtime / f"worker-leases/{sid}.json"
            lease = json.loads(lease_path.read_text())
            lease["preservationState"] = preservation
            lease_path.write_text(json.dumps(lease))
        report = json.loads(self.exec_tool("worker-lease.py", "report").stdout)
        self.assertEqual(report["archivableBacklog"], 2)  # dirty stays unknown → excluded

    def test_reconcile_rebinds_adopted_children_to_registry_successor(self):
        # Children keep creation-time parent-session labels naming the archived
        # predecessor; the successor registry's activeChildren is machine truth.
        self.manifest("child")  # label parent-session::parent
        registry = self.runtime / "coordinators/demo.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "project": "demo", "state": "authoritative",
            "coordinatorSessionId": "successor", "generation": 2,
            "activeChildren": ["child"]}))
        self.manifest("stray")  # not adopted by any registry
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        child = json.loads((self.runtime / "worker-leases/child.json").read_text())
        self.assertEqual(child["parentSessionId"], "successor")
        stray = json.loads((self.runtime / "worker-leases/stray.json").read_text())
        self.assertEqual(stray["parentSessionId"], "parent")
        # Rebind is stable across repeated reconciles.
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        child = json.loads((self.runtime / "worker-leases/child.json").read_text())
        self.assertEqual(child["parentSessionId"], "successor")

    def test_create_refuses_role_drift_parents(self):
        # Self-parented lane is refused.
        self.manifest("selfy")
        proc = self.exec_tool("worker-lease.py", "create", "--session", "selfy",
                              "--parent", "selfy", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("admission token", proc.stderr)
        # A live worker parenting a sub-lane is refused; only coordinators own lanes.
        self.manifest("rogue-parent")
        self.manifest("child")
        proc = self.exec_tool("worker-lease.py", "create", "--session", "child",
                              "--parent", "rogue-parent", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("admission token", proc.stderr)
        # A live coordinator parent is accepted.
        coord = self.manifest("coord-parent")
        value = json.loads(coord.read_text().splitlines()[0])
        value["labels"] = ["agent-role::coordinator"]
        coord.write_text(json.dumps(value) + "\n")
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        # An absent parent manifest stays permitted for watchdog backfill.
        self.manifest("orphan")
        self.exec_tool("worker-lease.py", "reconcile", "--apply")

    def test_create_refuses_worktree_collision(self):
        shared = self.root / "shared-wt"
        self.manifest("lane1", worktree=shared)
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        # Legacy sessions are backfilled by reconcile; explicit create needs admission.
        self.manifest("lane2", worktree=shared)
        proc = self.exec_tool("worker-lease.py", "create", "--session", "lane2", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("worktree already", proc.stderr)

    def test_fixed_clock_classification_has_inclusive_cutoffs_without_one_second_race(self):
        # Healthy includes exactly HEALTHY_SECONDS; suspect includes exactly
        # STALLED_SECONDS; stalled begins one millisecond after that boundary.
        self.manifest("boundary")
        self.env["CRAFT_TEST_NOW_MS"] = "100000"
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        path = self.runtime / "worker-leases/boundary.json"
        lease = json.loads(path.read_text())
        lease["lastHeartbeatAt"] = lease["lastEvidenceAt"] = 99000
        path.write_text(json.dumps(lease))
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        self.assertEqual(json.loads(path.read_text())["state"], "running")
        self.env["CRAFT_TEST_NOW_MS"] = "100001"
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        self.assertEqual(json.loads(path.read_text())["state"], "suspect")
        self.env["CRAFT_TEST_NOW_MS"] = "101000"
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        self.assertEqual(json.loads(path.read_text())["state"], "suspect")
        self.env["CRAFT_TEST_NOW_MS"] = "101001"
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        self.assertEqual(json.loads(path.read_text())["state"], "stalled")

    def test_observable_job_writes_exit_receipt(self):
        self.manifest("job")
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
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

    def test_terminal_receipt_waits_for_all_finalization(self):
        # The terminal receipt is the handoff/reap linearization point. Exercise
        # the supervisor directly so this proves the ordering without timing:
        # the prior implementation published exitCode before the exit marker,
        # log close, final heartbeat, owner cleanup, and heavy-lock release.
        spec = importlib.util.spec_from_file_location(
            f"observable_job_{time.time_ns()}", SCRIPTS / "observable-job.py")
        observable = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(observable)
        observable.RUNTIME = self.runtime
        observable.JOBS = self.runtime / "worker-jobs"
        observable.HEAVY_LOCK = self.runtime / "heavy-job.lock"
        observable.HEAVY_OWNER = self.runtime / "heavy-job-owner.json"
        log_path = self.root / "terminal-order.log"
        events = []
        original_atomic = observable.atomic_json

        def heartbeat(session, phase, evidence, pid, log):
            events.append(f"heartbeat:{phase}")

        def terminal_after_finalization(path, value):
            if path == observable.path_for("terminal-order") and value.get("exitCode") is not None:
                self.assertEqual(events[-1:], ["heartbeat:job-finished"])
                self.assertFalse(observable.HEAVY_OWNER.exists())
                with observable.HEAVY_LOCK.open("a+") as probe:
                    fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
                self.assertIn("observable job exit 0", log_path.read_text())
            original_atomic(path, value)

        observable.heartbeat = heartbeat
        observable.atomic_json = terminal_after_finalization
        args = argparse.Namespace(
            session="terminal-order", cwd=str(self.root), log=str(log_path), heavy=True,
            command=[sys.executable, "-c", "pass"])
        self.assertEqual(observable.supervise(args), 0)
        receipt = json.loads((self.runtime / "worker-jobs/terminal-order.json").read_text())
        self.assertEqual(receipt["exitCode"], 0)

    def test_status_keeps_a_live_supervisor_visible_after_child_exit(self):
        # A completed child must not hide its supervisor while it finalizes.
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=5)
        jobs = self.runtime / "worker-jobs"
        jobs.mkdir(parents=True)
        (jobs / "supervisor.json").write_text(json.dumps({
            "sessionId": "supervisor", "childPid": child.pid,
            "supervisorPid": os.getpid(), "exitCode": None,
            "logPath": str(self.root / "supervisor.log")}))
        status = json.loads(self.exec_tool("observable-job.py", "status", "--session", "supervisor").stdout)
        self.assertTrue(status["alive"])

    def test_heavy_jobs_are_serialized(self):
        self.manifest("heavy1")
        self.manifest("heavy2")
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
        self.exec_tool("worker-lease.py", "reconcile", "--apply")
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
        # A terminal receipt linearizes owned writes, but the short-lived Python
        # supervisor may still be exiting. Wait for both supervisors before the
        # TemporaryDirectory teardown so process-exit timing cannot race cleanup.
        for value in (value1, value2):
            pid = int(value.get("supervisorPid") or 0)
            deadline = time.time() + 3
            alive = bool(pid)
            while alive and time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    alive = False
                    break
                time.sleep(0.02)
            self.assertFalse(alive, f"observable supervisor {pid} outlived terminal receipt")

    def test_descendant_process_tree_cpu_counts_as_progress(self):
        # A4 regression: supervisor alive, direct driver nearly idle, a descendant
        # (Blender) burning CPU. Tree CPU must count as progress evidence; a tree
        # with no CPU growth must still classify stalled.
        proc = subprocess.Popen(["/bin/sleep", "300"])
        try:
            self.manifest("gta")
            self.exec_tool("worker-lease.py", "reconcile", "--apply")
            jobs = self.runtime / "worker-jobs"
            jobs.mkdir(parents=True, exist_ok=True)
            (jobs / "gta.json").write_text(json.dumps(
                {"sessionId": "gta", "childPid": proc.pid, "exitCode": None}))
            psfile = self.root / "ps.txt"
            psfile.write_text(f"{proc.pid} 1 0:00.05\n99999 {proc.pid} 8:20.00\n")
            env = dict(self.env)
            env["CRAFT_TEST_PS_FILE"] = str(psfile)
            run = lambda: subprocess.run([str(SCRIPTS / "worker-lease.py"), "reconcile", "--apply"],
                                         env=env, check=True, capture_output=True, timeout=20)
            lease_path = self.runtime / "worker-leases/gta.json"
            run()
            lease = json.loads(lease_path.read_text())
            self.assertGreater(lease["childCpuSeconds"], 500)  # descendant CPU visible
            # Stale evidence + flat tree CPU → stalled, exactly as before.
            lease["lastHeartbeatAt"] = lease["lastEvidenceAt"] = 0
            lease_path.write_text(json.dumps(lease))
            run()
            self.assertEqual(json.loads(lease_path.read_text())["state"], "stalled")
            # Descendant CPU growth alone revives the lane to running.
            psfile.write_text(f"{proc.pid} 1 0:00.05\n99999 {proc.pid} 9:00.00\n")
            run()
            lease = json.loads(lease_path.read_text())
            self.assertEqual(lease["state"], "running")
            self.assertGreater(lease["childCpuSeconds"], 530)
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_a_dead_lane_is_reapable_even_though_its_status_never_turned_terminal(self):
        # v3.4.34: a lane that dies never reaches needs-review — it keeps whatever
        # status the board last set — so the reaper skipped it as `status=todo`
        # forever. Measured live: 23 dead lanes aged 70-110 hours, 14 with clean
        # worktrees that were always safe to reap.
        import subprocess as sp
        # A finished lane whose work is committed and pushed: nothing is at risk.
        bare = self.root / "origin.git"
        sp.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        wt = self.root / "wt-dead"
        self.manifest("dead", status="todo", worktree=wt)
        env_git = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
        sp.run(["git", "init", "-q", "-b", "main", str(wt)], check=True)
        (wt / "f").write_text("done")
        sp.run(["git", "-C", str(wt), "add", "-A"], check=True)
        sp.run(["git", "-C", str(wt), "commit", "-qm", "done"], check=True, env=env_git)
        sp.run(["git", "-C", str(wt), "remote", "add", "origin", str(bare)], check=True)
        sp.run(["git", "-C", str(wt), "push", "-q", "-u", "origin", "main"], check=True)
        (self.runtime / "worker-leases").mkdir(parents=True, exist_ok=True)
        (self.runtime / "worker-leases" / "dead.json").write_text(json.dumps(
            {"schemaVersion": 1, "sessionId": "dead", "parentSessionId": "parent",
             "role": "worker", "workUnit": "unit", "state": "stalled"}))
        env = {**self.env, "CRAFT_WORKER_LEASES": str(self.runtime / "worker-leases"),
               "CRAFT_REAP_IDLE_MINUTES": "0", "CRAFT_WORKSPACE": str(self.root)}
        out = sp.run([sys.executable, str(SCRIPTS / "scan-reapable-workers.py")],
                     env=env, capture_output=True, text=True, timeout=60)
        report = json.loads(out.stdout)
        ids = [x["id"] for x in report["reapable"]]
        self.assertIn("dead", ids)
        self.assertEqual([x for x in report["reapable"] if x["id"] == "dead"][0]["laneState"], "stalled")
        # A live lane at the same status stays out of reach.
        (self.runtime / "worker-leases" / "dead.json").write_text(json.dumps(
            {"schemaVersion": 1, "sessionId": "dead", "parentSessionId": "parent",
             "role": "worker", "workUnit": "unit", "state": "running"}))
        out = sp.run([sys.executable, str(SCRIPTS / "scan-reapable-workers.py")],
                     env=env, capture_output=True, text=True, timeout=60)
        self.assertNotIn("dead", [x["id"] for x in json.loads(out.stdout)["reapable"]])

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

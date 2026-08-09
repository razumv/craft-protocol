# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import json, os, subprocess, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts"))
TOOL = SCRIPTS / "recovery-incident.py"

class SelfHealingV311Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.home = Path(self.tmp.name)
        self.runtime = self.home / "runtime"; self.sessions = self.home / "sessions"
        self.env = {**os.environ, "CRAFT_RUNTIME": str(self.runtime), "CRAFT_SESSIONS": str(self.sessions),
                    "CRAFT_WORKSPACE": str(self.home / "workspace"), "CRAFT_RECOVERY_MAX_ATTEMPTS": "2",
                    "CRAFT_COORDINATOR_RECOVERY_MAX_ATTEMPTS": "3"}
        self.now = int(time.time()*1000)
    def tearDown(self): self.tmp.cleanup()
    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value)+"\n")
    def manifest(self, sid, *, status="in_progress", archived=False, labels=None, cwd=None, error=None):
        m={"id":sid,"sessionStatus":status,"isArchived":archived,"labels":labels or []}
        if cwd: m["workingDirectory"]=cwd
        if error: m["lastError"]=error
        self.put(self.sessions/sid/"session.jsonl",m)
    def registry(self, project="alpha", sid="coord", **extra):
        row={"schemaVersion":1,"project":project,"coordinatorSessionId":sid,"state":"active","generation":1,
             "leaseExpiresAt":self.now+60000,"lastHeartbeatAt":self.now, **extra}
        self.put(self.runtime/"coordinators"/f"{project}.json",row); return row
    def lease(self, sid="worker", **extra):
        row={"schemaVersion":1,"sessionId":sid,"parentSessionId":"coord","role":"worker","state":"running",
             "phase":"implementation","lastHeartbeatAt":self.now,"preservationState":"unknown","worktree":f"/tmp/{sid}", **extra}
        self.put(self.runtime/"worker-leases"/f"{sid}.json",row); return row
    def cli(self,*args,ok=True):
        cp=subprocess.run([str(TOOL),*args],env=self.env,text=True,capture_output=True)
        if ok and cp.returncode: self.fail(cp.stderr or cp.stdout)
        return cp, json.loads(cp.stdout) if cp.returncode==0 and cp.stdout else None
    def base(self): self.manifest("coord",cwd="/tmp/project"); self.registry()

    def test_stale_coordinator_is_idempotent(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1000)
        _,a=self.cli("detect","--apply"); _,b=self.cli("detect","--apply")
        self.assertEqual(a["observed"],1); self.assertEqual(b["observed"],1)
        _,rows=self.cli("list","--state","open"); self.assertEqual(rows["count"],1)
        self.assertEqual(rows["incidents"][0]["kind"],"coordinator-lease-stale")
    def test_unresolved_repeated_pi_sigterm(self):
        self.manifest("coord"); self.registry(lastHeartbeatAt=self.now-5000)
        with (self.sessions/"coord"/"session.jsonl").open("a") as f:
            f.write(json.dumps({"type":"error","timestamp":self.now-4000,"content":"Pi subprocess exited unexpectedly (signal SIGTERM)"})+"\n")
            f.write(json.dumps({"type":"error","timestamp":self.now-3000,"content":"Pi subprocess exited unexpectedly (signal SIGTERM)"})+"\n")
        _,d=self.cli("detect"); row=[x for x in d["observations"] if x["kind"]=="coordinator-pi-sigterm"][0]
        self.assertEqual(row["evidence"]["countSinceHeartbeat"],2)
        self.assertNotIn("content",row["evidence"]); self.assertIn("bridge-rotation-on-attempt-3",row["allowedActions"])
        self.registry(lastHeartbeatAt=self.now+1000)
        _,d=self.cli("detect"); self.assertNotIn("coordinator-pi-sigterm",[x["kind"] for x in d["observations"]])
    def test_hold_suppresses_stale_and_not_live(self):
        self.registry(state="hold",leaseExpiresAt=self.now-1)
        _,d=self.cli("detect"); self.assertEqual(d["observations"],[])
    def test_archived_coordinator_is_critical(self):
        self.manifest("coord",archived=True); self.registry()
        _,d=self.cli("detect"); self.assertEqual(d["observations"][0]["kind"],"coordinator-not-live")
    def test_terminal_unknown_emits_two_refusals(self):
        self.base(); self.manifest("worker",cwd="/tmp/worker"); self.lease(state="handoff-ready")
        _,d=self.cli("detect"); self.assertEqual({x["kind"] for x in d["observations"]},{"terminal-handoff-unconsumed","preservation-unknown"})
    def test_terminal_auditor_omits_push_requirement(self):
        self.base(); self.manifest("worker"); self.lease(state="handoff-ready", role="auditor")
        _,d=self.cli("detect"); self.assertEqual([x["kind"] for x in d["observations"]],["terminal-handoff-unconsumed"])
    def test_terminal_pushed_omits_unknown(self):
        self.base(); self.manifest("worker"); self.lease(state="handoff-ready",preservationState="pushed")
        _,d=self.cli("detect"); self.assertEqual([x["kind"] for x in d["observations"]],["terminal-handoff-unconsumed"])
    def test_exit_75_is_contention(self):
        self.base(); self.manifest("worker"); self.lease()
        self.put(self.runtime/"worker-jobs"/"worker.json",{"sessionId":"worker","jobId":"j","exitCode":75,"endedAt":self.now})
        _,d=self.cli("detect"); row=[x for x in d["observations"] if x["kind"]=="heavy-lock-wait"][0]
        self.assertIn("queue-after-lock",row["allowedActions"])
    def test_unreported_exit_zero(self):
        self.base(); self.manifest("worker"); self.lease()
        self.put(self.runtime/"worker-jobs"/"worker.json",{"sessionId":"worker","jobId":"j","exitCode":0,"endedAt":self.now})
        _,d=self.cli("detect"); row=[x for x in d["observations"] if x["kind"]=="job-exit-unreported"][0]
        self.assertEqual(row["evidence"]["jobId"],"j"); self.assertEqual(row["evidence"]["endedAt"],self.now)
    def test_gate_is_report_only(self):
        self.put(self.runtime/"owner-gates"/"alpha"/"gate.json",{"gateId":"g","project":"alpha","state":"open","action":"deploy"})
        _,d=self.cli("detect"); row=d["observations"][0]
        self.assertEqual(row["kind"],"owner-gate-blocked"); self.assertEqual(row["allowedActions"],["report-only"])
    def test_cleared_gate_report_resolves(self):
        gate=self.runtime/"owner-gates"/"alpha"/"gate.json"
        self.put(gate,{"gateId":"g","project":"alpha","state":"open","action":"deploy"})
        self.cli("detect","--apply"); gate.unlink(); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","resolved"); self.assertEqual(rows["count"],1)
    def test_parent_project_overrides_conflicting_child_label(self):
        self.manifest("coord"); self.registry("alpha")
        self.manifest("worker",labels=["project::beta"]); self.lease(state="stalled")
        _,d=self.cli("detect"); rows=d["observations"]
        stalled=[x for x in rows if x["kind"]=="worker-stalled"][0]
        conflict=[x for x in rows if x["kind"]=="project-mapping-conflict"][0]
        self.assertEqual(stalled["project"],"alpha"); self.assertEqual(conflict["project"],"alpha")
        self.assertIn("hard-refusal",conflict["allowedActions"])
    def test_claim_cas(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("claim","--incident",iid,"--controller","c1")
        for controller in ("c1","c2"):
            cp,_=self.cli("claim","--incident",iid,"--controller",controller,ok=False); self.assertNotEqual(cp.returncode,0)
    def test_expired_claim_cannot_mutate_or_revive(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("claim","--incident",iid,"--controller","c1","--ttl","0")
        for command in (("heartbeat","--ttl","900"),("resolve","--evidence-kind","test","--evidence","no"),("defer","--reason","no"),("escalate","--reason","no"),("claim","--ttl","900")):
            cp,_=self.cli(command[0],"--incident",iid,"--controller","c1",*command[1:],ok=False)
            self.assertNotEqual(cp.returncode,0)
        self.cli("detect","--apply")
        _,row=self.cli("claim","--incident",iid,"--controller","c2")
        self.assertEqual(row["state"],"claimed")
    def test_kill_switch_blocks_incident_heartbeat_and_mutation(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("claim","--incident",iid,"--controller","c1")
        self.runtime.mkdir(parents=True,exist_ok=True); (self.runtime/"self-healing.disabled").touch()
        for command in (("heartbeat","--ttl","900"),("defer","--reason","no")):
            cp,_=self.cli(command[0],"--incident",iid,"--controller","c1",*command[1:],ok=False)
            self.assertNotEqual(cp.returncode,0)
    def test_cooldown_and_mutation_owner_guard(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("claim","--incident",iid,"--controller","c0")
        cp,_=self.cli("defer","--incident",iid,"--controller","wrong","--reason","wait",ok=False); self.assertNotEqual(cp.returncode,0)
        self.cli("defer","--incident",iid,"--controller","c0","--reason","wait","--cooldown","3600")
        cp,_=self.cli("claim","--incident",iid,"--controller","c1",ok=False); self.assertNotEqual(cp.returncode,0)
    def test_coordinator_budget_allows_two_wakes_and_rotation_then_escalates(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        for controller,stage in (("c1","wake-1"),("c2","wake-2"),("c3","rotation")):
            _,row=self.cli("claim","--incident",iid,"--controller",controller)
            self.assertEqual(row["state"],"claimed"); self.assertEqual(row["claimStage"],stage)
            if stage.startswith("wake"):
                self.assertNotIn("bridge-rotation-on-attempt-3",row["claimAllowedActions"])
            else:
                self.assertNotIn("wake-coordinator",row["claimAllowedActions"])
                self.assertIn("bridge-rotation-on-attempt-3",row["claimAllowedActions"])
            self.cli("defer","--incident",iid,"--controller",controller,"--reason","retry","--cooldown","0")
            self.cli("detect","--apply")
        _,row=self.cli("claim","--incident",iid,"--controller","c4")
        self.assertEqual(row["state"],"escalated"); self.assertEqual(row["recoveryAttempts"],3)
    def test_condition_cleared_resolves(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        self.registry(leaseExpiresAt=self.now+60000); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","resolved"); self.assertEqual(rows["count"],1)
    def test_controller_lock_and_kill_switch(self):
        _,first=self.cli("controller-claim","--session","c1")
        _,renewed=self.cli("controller-heartbeat","--session","c1")
        self.assertGreaterEqual(renewed["lastHeartbeatAt"],first["lastHeartbeatAt"])
        cp,_=self.cli("controller-claim","--session","c2",ok=False); self.assertNotEqual(cp.returncode,0)
        self.runtime.mkdir(parents=True,exist_ok=True); (self.runtime/"self-healing.disabled").touch()
        cp,_=self.cli("controller-heartbeat","--session","c1",ok=False); self.assertNotEqual(cp.returncode,0)
        # Release is the sole fail-safe mutation allowed under the kill switch.
        self.cli("controller-release","--session","c1")
        cp,_=self.cli("controller-claim","--session","c2",ok=False); self.assertNotEqual(cp.returncode,0)
    def test_expired_controller_cannot_revive_but_can_release(self):
        self.cli("controller-claim","--session","c1","--ttl","0")
        for command in ("controller-heartbeat","controller-claim"):
            cp,_=self.cli(command,"--session","c1",ok=False); self.assertNotEqual(cp.returncode,0)
        self.cli("controller-release","--session","c1")
        _,row=self.cli("controller-claim","--session","c1")
        self.assertEqual(row["sessionId"],"c1")
    def test_cwd_collision_is_critical(self):
        self.base(); self.manifest("worker"); self.lease(cwdCollision={"with":"w2"})
        _,d=self.cli("detect"); row=[x for x in d["observations"] if x["kind"]=="cwd-collision"][0]
        self.assertEqual(row["severity"],"critical"); self.assertIn("hard-refusal",row["allowedActions"])

if __name__ == "__main__": unittest.main()

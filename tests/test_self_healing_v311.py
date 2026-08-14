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
                    "CRAFT_COORDINATOR_RECOVERY_MAX_ATTEMPTS": "3", "CRAFT_RECOVERY_CLEAR_CONFIRM_SECONDS": "0"}
        self.now = int(time.time()*1000)
    def tearDown(self): self.tmp.cleanup()
    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value)+"\n")
    def manifest(self, sid, *, status="in_progress", archived=False, labels=None, cwd=None, error=None, **extra):
        m={"id":sid,"sessionStatus":status,"isArchived":archived,"labels":labels or [], **extra}
        if cwd: m["workingDirectory"]=cwd
        if error: m["lastError"]=error
        self.put(self.sessions/sid/"session.jsonl",m)
    def registry(self, project="alpha", sid="coord", **extra):
        row={"schemaVersion":1,"project":project,"coordinatorSessionId":sid,"state":"authoritative","generation":1,
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

    def test_transport_loss_is_a_named_condition(self):
        # v3.4.26: a lost channel is indistinguishable from lazy agents from the
        # outside. Live 2026-08-14: Tailscale logged out at ~19:02, the server's
        # listening address vanished with the interface, and for an hour the only
        # visible symptom was a fleet that appeared to have stopped caring.
        self.base()
        transport = self.runtime / "self-healing" / "transport.json"
        _, quiet = self.cli("drain")
        self.assertFalse(quiet["transport"]["lost"])
        # Failures with no recent success are a lost channel.
        self.put(transport, {"schemaVersion": 1, "consecutiveFailures": 3,
                             "lastSuccessAt": self.now - 3_600_000,
                             "lastFailureReason": "connection timeout"})
        _, lost = self.cli("drain")
        self.assertTrue(lost["transport"]["lost"])
        self.assertEqual(lost["transport"]["consecutiveFailures"], 3)
        self.assertIn("timeout", lost["transport"]["lastFailureReason"])
        # One failure after a fresh success is a hiccup, not a loss.
        self.put(transport, {"schemaVersion": 1, "consecutiveFailures": 1,
                             "lastSuccessAt": self.now - 60_000})
        _, hiccup = self.cli("drain")
        self.assertFalse(hiccup["transport"]["lost"])
        # A restored channel clears the condition outright.
        self.put(transport, {"schemaVersion": 1, "consecutiveFailures": 0,
                             "lastSuccessAt": self.now})
        _, restored = self.cli("drain")
        self.assertFalse(restored["transport"]["lost"])

    def test_controller_silence_is_reported_when_work_is_waiting(self):
        # v3.4.25: a self-healing lane that stopped is worse than one that never
        # existed. Live, the wake lane was hard-blocked by one failed probe, the
        # controller went 56 minutes without a turn, and the ledger grew to 74 open
        # conditions while every project looked merely busy.
        self.base()
        self.lease("worker1", state="handoff-ready")
        self.cli("detect", "--apply")
        harness = self.runtime / "controller-harnesses" / "ctrl.json"
        self.put(harness, {"schemaVersion": 1, "sessionId": "ctrl",
                           "sessionRole": "recovery-controller",
                           "registeredAt": self.now - 7_200_000})
        _, silent = self.cli("drain")
        self.assertTrue(silent["controller"]["silent"])
        self.assertGreater(silent["controller"]["deliveryBlockingCount"], 0)
        # A controller that just took a turn is alive, not silent.
        self.put(harness, {"schemaVersion": 1, "sessionId": "ctrl",
                           "sessionRole": "recovery-controller", "registeredAt": self.now})
        _, alive = self.cli("drain")
        self.assertFalse(alive["controller"]["silent"])
        # A deliberate kill switch is rest: silence is only alarming when the lane
        # is supposed to be running.
        self.put(harness, {"schemaVersion": 1, "sessionId": "ctrl",
                           "sessionRole": "recovery-controller",
                           "registeredAt": self.now - 7_200_000})
        (self.runtime / "self-healing.disabled").write_text("")
        _, paused = self.cli("drain")
        self.assertFalse(paused["controller"]["silent"])

    def test_an_idle_executor_outranks_a_bookkeeping_mismatch(self):
        # v3.4.27: with a three-action turn budget, status contradictions and overdue
        # commitments consumed every turn while two finished workers waited 25 and 30
        # minutes to be collected. An executor idle right now goes first.
        self.base()
        self.lease("worker1", state="handoff-ready")
        self.put(self.runtime / "coordinator-status" / "alpha.json",
                 {"schemaVersion": 1, "project": "alpha", "coordinatorSessionId": "coord",
                  "generation": 1, "revision": 1, "publishedAt": 1, "updatedAt": 1,
                  "declared": {"objective": "x", "phase": "executing", "nextActions": []}})
        self.cli("detect", "--apply")
        _, drained = self.cli("drain", "--limit", "2")
        kinds = [w["kind"] for w in drained["work"]]
        self.assertEqual(kinds[0], "terminal-handoff-unconsumed")
        self.assertEqual(drained["work"][0]["rank"], 1)
        bookkeeping = [w for w in drained["work"] if w["kind"].startswith("coordinator-status")]
        self.assertTrue(all(w["rank"] >= 2 for w in bookkeeping))

    def test_a_stranded_kill_switch_is_not_an_owner_pause(self):
        # v3.4.28: an install arms the switch before mutating the payload and removes
        # it after its tests pass. On 2026-08-14 an interrupted install left the file
        # behind: the recovery lane stayed dead for 75 minutes while every check
        # reported a deliberate pause, because rest and outage looked identical.
        self.base()
        self.lease("worker1", state="handoff-ready")
        self.cli("detect", "--apply")
        switch = self.runtime / "self-healing.disabled"
        switch.parent.mkdir(parents=True, exist_ok=True)
        harness = self.runtime / "controller-harnesses" / "ctrl.json"
        self.put(harness, {"schemaVersion": 1, "sessionId": "ctrl",
                           "sessionRole": "recovery-controller",
                           "registeredAt": self.now - 7_200_000})

        # An owner's own pause: no marker, so silence is rest and stays unreported.
        switch.write_text("")
        os.utime(switch, (time.time() - 7200, time.time() - 7200))
        _, paused = self.cli("drain")
        self.assertEqual(paused["killSwitch"]["armedBy"], "owner")
        self.assertFalse(paused["killSwitch"]["stranded"])
        self.assertFalse(paused["controller"]["silent"])

        # The installer's own marker turns the same file into a stranded install.
        switch.write_text("armed-by=install.sh armed-at=2026-08-14T21:11:45Z rearm-expected=1")
        os.utime(switch, (time.time() - 7200, time.time() - 7200))
        _, stranded = self.cli("drain")
        self.assertEqual(stranded["killSwitch"]["armedBy"], "install.sh")
        self.assertTrue(stranded["killSwitch"]["stranded"])
        self.assertTrue(stranded["controller"]["silent"])

        # A marker minutes old is an install still running, not a stranded one.
        switch.write_text("armed-by=install.sh armed-at=2026-08-14T21:11:45Z rearm-expected=1")
        os.utime(switch, None)
        _, running = self.cli("drain")
        self.assertFalse(running["killSwitch"]["stranded"])
        self.assertFalse(running["controller"]["silent"])

    def test_host_saturation_is_not_reported_as_a_lost_channel(self):
        # v3.4.27: eight parallel builds from unrelated work drove the load to 59 on
        # an 8-core host and every RPC timed out — while the channel was fine.
        self.base()
        transport = self.runtime / "self-healing" / "transport.json"
        self.put(transport, {"schemaVersion": 1, "consecutiveFailures": 4,
                            "lastSuccessAt": self.now - 3_600_000,
                            "lastFailureReason": "connection timeout"})
        env = {**self.env, "CRAFT_HOST_SATURATION_RATIO": "0.0001"}
        cp = subprocess.run([str(TOOL), "drain"], env=env, text=True, capture_output=True)
        saturated = json.loads(cp.stdout)["transport"]
        self.assertTrue(saturated["host"]["saturated"])
        self.assertFalse(saturated["lost"])
        self.assertTrue(saturated["hostStarved"])
        # On an idle host the same failures do mean the channel is gone.
        env = {**self.env, "CRAFT_HOST_SATURATION_RATIO": "10000"}
        cp = subprocess.run([str(TOOL), "drain"], env=env, text=True, capture_output=True)
        calm = json.loads(cp.stdout)["transport"]
        self.assertFalse(calm["host"]["saturated"])
        self.assertTrue(calm["lost"])
        self.assertFalse(calm["hostStarved"])

    def test_drain_puts_pipeline_blockers_before_housekeeping(self):
        # v3.4.24: the ledger is worked in the order that unblocks delivery. Live,
        # 23 cwd-collision and 10 orphaned-lane records shared one queue with four
        # idle finished workers and a lease stale for 67 minutes, and the noise won.
        self.base()
        self.lease("worker1", state="handoff-ready")
        self.registry(leaseExpiresAt=self.now-60_000)
        for n in range(6):
            self.manifest(f"dup{n}", cwd="/tmp/shared")
            self.lease(f"dup{n}", state="stalled", worktree="/tmp/shared",
                       lastHeartbeatAt=self.now-7_200_000)
        self.cli("detect", "--apply")
        _, drained = self.cli("drain", "--limit", "3")
        self.assertGreater(drained["openCount"], 3)
        self.assertGreater(drained["housekeepingCount"], 0)
        kinds = [w["kind"] for w in drained["work"]]
        self.assertIn("coordinator-lease-stale", kinds)
        self.assertIn("terminal-handoff-unconsumed", kinds)
        # Housekeeping never takes more than its quota of a turn.
        self.assertLessEqual(len([w for w in drained["work"] if w["rank"] == 3]), 1)
        # An unfinished backlog demands another turn now, not after the next window.
        self.assertTrue(drained["requestImmediateCycle"])

    def test_drain_is_empty_and_calm_when_nothing_is_open(self):
        self.base()
        self.cli("detect", "--apply")
        _, drained = self.cli("drain")
        self.assertEqual(drained["work"], [])
        self.assertFalse(drained["requestImmediateCycle"])

    def test_child_of_a_live_coordinator_without_a_lease_is_detected(self):
        # v3.4.21: an executor with no lease is invisible to every machine check at
        # once. Observed live: six such children across three projects, one running
        # the owner-authorized correction attempt.
        self.base()
        self.manifest("ghost", cwd="/tmp/ghost", parentSessionId="coord",
                      createdAt=self.now-1_800_000, name="Revision ID Correction")
        _, d = self.cli("detect")
        rows = [x for x in d["observations"] if x["kind"] == "unregistered-child-lane"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "alpha")
        self.assertEqual(rows[0]["evidence"]["parentSessionId"], "coord")
        # Registering the lease is exactly what closes it.
        self.lease("ghost", parentSessionId="coord")
        _, d = self.cli("detect")
        self.assertNotIn("unregistered-child-lane", [x["kind"] for x in d["observations"]])

    def test_a_just_spawned_child_is_within_its_registration_window(self):
        self.base()
        self.manifest("fresh", cwd="/tmp/fresh", parentSessionId="coord", createdAt=self.now-30_000)
        _, d = self.cli("detect")
        self.assertNotIn("unregistered-child-lane", [x["kind"] for x in d["observations"]])
        # An archived child owes nothing: it is finished, not unobserved.
        self.manifest("gone", cwd="/tmp/gone", parentSessionId="coord",
                      createdAt=self.now-1_800_000, archived=True)
        _, d = self.cli("detect")
        self.assertNotIn("unregistered-child-lane", [x["kind"] for x in d["observations"]])

    def test_forgotten_kill_switch_stops_being_silent(self):
        # Observed live: two upgrades left self-healing disabled for three hours
        # while eleven conditions accumulated, and the fleet looked healthy.
        self.base()
        self.lease("worker", state="stalled")
        _, armed = self.cli("detect")
        self.assertFalse(armed["killSwitch"]["present"])
        switch = self.runtime / "self-healing.disabled"
        switch.parent.mkdir(parents=True, exist_ok=True)
        switch.write_text("")
        os.utime(switch, (time.time() - 7200, time.time() - 7200))
        _, disabled = self.cli("detect")
        self.assertTrue(disabled["killSwitch"]["present"])
        self.assertTrue(disabled["killSwitch"]["staleWithOpenConditions"])
        self.assertGreater(disabled["killSwitch"]["observedConditions"], 0)
        # A switch someone just set is a deliberate pause, not a forgotten one.
        os.utime(switch, None)
        _, fresh = self.cli("detect")
        self.assertFalse(fresh["killSwitch"]["staleWithOpenConditions"])

    def test_orphaned_dead_lane_gets_a_disposition_path(self):
        # v3.4.20: a dead lane whose dispatching coordinator is gone can never be
        # preservation-proven, so it sat outside archivableBacklog forever while
        # holding a worktree — 23 of them accumulated, the oldest 91 hours old.
        self.base()
        self.manifest("orphan", cwd="/tmp/orphan")
        self.lease("orphan", parentSessionId="dead-coord", state="stalled",
                   preservationState="unknown", createdAt=self.now-200_000_000)
        _, d = self.cli("detect")
        rows = [x for x in d["observations"] if x["kind"] == "orphaned-dead-lane"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evidence"]["parentSessionId"], "dead-coord")
        # A lane whose parent still owns a project is ordinary worker recovery.
        self.lease("orphan", parentSessionId="coord", state="stalled",
                   preservationState="unknown", createdAt=self.now-200_000_000)
        _, d = self.cli("detect")
        self.assertNotIn("orphaned-dead-lane", [x["kind"] for x in d["observations"]])
        # A recently dispatched orphan is still within its coordinator's reach.
        self.lease("orphan", parentSessionId="dead-coord", state="stalled",
                   preservationState="unknown", createdAt=self.now-60_000)
        _, d = self.cli("detect")
        self.assertNotIn("orphaned-dead-lane", [x["kind"] for x in d["observations"]])

    def test_live_predecessor_after_settled_handoff_emits_incident(self):
        # v3.4.19: registry validate has flagged predecessor-not-archived since
        # v3.4.8, but nothing woke anyone — the owner kept seeing two coordinators.
        self.manifest("coord", cwd="/tmp/project"); self.manifest("old", cwd="/tmp/project")
        self.registry(predecessorSessionId="old", transferAcceptedAt=self.now-3_600_000)
        _, d = self.cli("detect")
        rows = [x for x in d["observations"] if x["kind"] == "predecessor-unarchived"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evidence"]["predecessorSessionId"], "old")
        # A freshly accepted handoff is still within the archiving grace window.
        self.registry(predecessorSessionId="old", transferAcceptedAt=self.now-60_000)
        _, d = self.cli("detect")
        self.assertNotIn("predecessor-unarchived", [x["kind"] for x in d["observations"]])
        # An archived predecessor is the completed duty.
        self.manifest("old", archived=True, cwd="/tmp/project")
        self.registry(predecessorSessionId="old", transferAcceptedAt=self.now-3_600_000)
        _, d = self.cli("detect")
        self.assertNotIn("predecessor-unarchived", [x["kind"] for x in d["observations"]])

    def test_worker_terminal_status_coordinator_emits_incident(self):
        # An authoritative coordinator parked in a worker-terminal session status is
        # deaf to queued wakes; detection must surface it deterministically.
        self.manifest("coord", status="needs-review", cwd="/tmp/project"); self.registry()
        _, d = self.cli("detect")
        rows = [x for x in d["observations"] if x["kind"] == "coordinator-worker-terminal-status"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evidence"]["sessionStatus"], "needs-review")
        self.assertEqual(rows[0]["project"], "alpha")
        # A HOLD-parked project is intentional rest, never flagged.
        self.registry(state="hold")
        _, d = self.cli("detect")
        self.assertNotIn("coordinator-worker-terminal-status", [x["kind"] for x in d["observations"]])
        # An active coordinator is clean.
        self.manifest("coord", status="in_progress", cwd="/tmp/project"); self.registry()
        _, d = self.cli("detect")
        self.assertNotIn("coordinator-worker-terminal-status", [x["kind"] for x in d["observations"]])

    def test_stale_coordinator_is_idempotent(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1000)
        _,a=self.cli("detect","--apply"); _,b=self.cli("detect","--apply")
        self.assertEqual(a["observed"],1); self.assertEqual(b["observed"],1)
        _,rows=self.cli("list","--state","open"); self.assertEqual(rows["count"],1)
        self.assertEqual(rows["incidents"][0]["kind"],"coordinator-lease-stale")

    def test_stale_age_growth_does_not_change_evidence_fingerprint(self):
        expiry=self.now-1000
        self.manifest("coord"); self.registry(leaseExpiresAt=expiry,lastHeartbeatAt=self.now-5000,generation=7)
        self.env["CRAFT_TEST_NOW_MS"]=str(self.now)
        _,first=self.cli("detect"); stale1=first["observations"][0]
        self.env["CRAFT_TEST_NOW_MS"]=str(self.now+300_000)
        _,second=self.cli("detect"); stale2=second["observations"][0]
        self.assertNotEqual(stale1["evidence"]["agePastExpiryMs"],stale2["evidence"]["agePastExpiryMs"])
        self.assertEqual(stale1["evidenceFingerprint"],stale2["evidenceFingerprint"])
        self.assertEqual(stale1["incidentId"],stale2["incidentId"])
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
    def test_terminal_completion_error_wakes_until_later_success(self):
        error_at=self.now-2000
        self.manifest("coord", lastCompletedErrorMessageAt=error_at,
                      lastCompletedErrorMessageId="err-1", lastCompletedMessageId="err-1",
                      lastCompletedAt=error_at, lastMessageRole="error")
        self.registry()
        _,d=self.cli("detect"); row=[x for x in d["observations"] if x["kind"]=="coordinator-session-error"][0]
        self.assertEqual(row["evidence"]["errorAt"],error_at)
        self.assertEqual(row["evidence"]["generation"],1)
        success_at=self.now-1000
        self.manifest("coord", lastCompletedErrorMessageAt=error_at,
                      lastCompletedErrorMessageId="err-1", lastCompletedMessageId="ok-1",
                      lastCompletedAt=success_at, lastCompletedFinalMessageAt=success_at,
                      lastCompletedFinalMessageId="ok-1", lastMessageRole="assistant")
        _,d=self.cli("detect"); self.assertNotIn("coordinator-session-error",[x["kind"] for x in d["observations"]])
    def test_tool_error_inside_successful_turn_does_not_wake_loop(self):
        error_at=self.now-2000; success_at=self.now-1000
        self.manifest("coord", lastCompletedErrorMessageAt=error_at,
                      lastCompletedErrorMessageId="tool-error", lastCompletedMessageId="ok-1",
                      lastCompletedAt=success_at, lastCompletedFinalMessageAt=success_at,
                      lastCompletedFinalMessageId="ok-1", lastMessageRole="assistant")
        self.registry()
        _,d=self.cli("detect"); self.assertNotIn("coordinator-session-error",[x["kind"] for x in d["observations"]])
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
        self.assertFalse(d["observations"][0]["evidence"]["activeChild"])

    def test_current_active_child_handoff_is_high_severity_wake(self):
        self.manifest("coord",cwd="/tmp/project"); self.registry(activeChildren=["worker"])
        self.manifest("worker"); self.lease(state="handoff-ready",preservationState="pushed")
        _,d=self.cli("detect"); row=d["observations"][0]
        self.assertEqual(row["kind"],"terminal-handoff-unconsumed")
        self.assertEqual(row["severity"],"high")
        self.assertTrue(row["evidence"]["activeChild"])
        self.assertIn("wake-coordinator",row["allowedActions"])
    def test_exit_75_is_contention(self):
        self.base(); self.manifest("worker"); self.lease()
        self.put(self.runtime/"worker-jobs"/"worker.json",{"sessionId":"worker","jobId":"j","exitCode":75,"endedAt":self.now})
        _,d=self.cli("detect"); row=[x for x in d["observations"] if x["kind"]=="heavy-lock-wait"][0]
        self.assertIn("queue-after-lock",row["allowedActions"])
    def test_acknowledged_exit_75_is_not_replayed(self):
        self.base(); self.manifest("worker"); self.lease()
        self.put(self.runtime/"worker-jobs"/"worker.json",{"sessionId":"worker","jobId":"j","exitCode":75,"endedAt":self.now,"reportedAt":self.now+1})
        _,d=self.cli("detect")
        self.assertFalse([x for x in d["observations"] if x["kind"]=="heavy-lock-wait"])
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
        self.cli("controller-claim","--session","c1")
        self.cli("claim","--incident",iid,"--controller","c1")
        for controller in ("c1","c2"):
            cp,_=self.cli("claim","--incident",iid,"--controller",controller,ok=False); self.assertNotEqual(cp.returncode,0)
    def test_expired_claim_cannot_mutate_or_revive(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("controller-claim","--session","c1")
        self.cli("claim","--incident",iid,"--controller","c1","--ttl","0")
        for command in (("heartbeat","--ttl","900"),("resolve","--evidence-kind","test","--evidence","no"),("defer","--reason","no"),("escalate","--reason","no"),("claim","--ttl","900")):
            cp,_=self.cli(command[0],"--incident",iid,"--controller","c1",*command[1:],ok=False)
            self.assertNotEqual(cp.returncode,0)
        self.cli("detect","--apply")
        self.cli("controller-release","--session","c1"); self.cli("controller-claim","--session","c2")
        _,row=self.cli("claim","--incident",iid,"--controller","c2")
        self.assertEqual(row["state"],"claimed")
    def test_kill_switch_blocks_incident_heartbeat_and_mutation(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("controller-claim","--session","c1"); self.cli("claim","--incident",iid,"--controller","c1")
        self.runtime.mkdir(parents=True,exist_ok=True); (self.runtime/"self-healing.disabled").touch()
        for command in (("heartbeat","--ttl","900"),("defer","--reason","no")):
            cp,_=self.cli(command[0],"--incident",iid,"--controller","c1",*command[1:],ok=False)
            self.assertNotEqual(cp.returncode,0)
    def test_cooldown_and_mutation_owner_guard(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("controller-claim","--session","c0"); self.cli("claim","--incident",iid,"--controller","c0")
        cp,_=self.cli("defer","--incident",iid,"--controller","wrong","--reason","wait",ok=False); self.assertNotEqual(cp.returncode,0)
        self.cli("defer","--incident",iid,"--controller","c0","--reason","wait","--cooldown","3600")
        cp,_=self.cli("claim","--incident",iid,"--controller","c1",ok=False); self.assertNotEqual(cp.returncode,0)
    def test_coordinator_budget_allows_two_wakes_and_rotation_then_escalates(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        for controller,stage in (("c1","wake-1"),("c2","wake-2"),("c3","rotation")):
            self.cli("controller-claim","--session",controller)
            _,row=self.cli("claim","--incident",iid,"--controller",controller)
            self.assertEqual(row["state"],"claimed"); self.assertEqual(row["claimStage"],stage)
            if stage.startswith("wake"):
                self.assertNotIn("bridge-rotation-on-attempt-3",row["claimAllowedActions"])
            else:
                self.assertNotIn("wake-coordinator",row["claimAllowedActions"])
                self.assertIn("bridge-rotation-on-attempt-3",row["claimAllowedActions"])
            self.cli("defer","--incident",iid,"--controller",controller,"--reason","retry","--cooldown","0")
            self.cli("controller-release","--session",controller); self.cli("detect","--apply")
        self.cli("controller-claim","--session","c4")
        _,row=self.cli("claim","--incident",iid,"--controller","c4")
        self.assertEqual(row["state"],"escalated"); self.assertEqual(row["recoveryAttempts"],3)
    def test_condition_cleared_resolves(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        self.registry(leaseExpiresAt=self.now+60000); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","resolved"); self.assertEqual(rows["count"],1)

    def test_transient_observation_gap_cannot_reset_attempt_budget(self):
        base=self.now+10_000
        self.env["CRAFT_RECOVERY_CLEAR_CONFIRM_SECONDS"]="300"
        self.env["CRAFT_TEST_NOW_MS"]=str(base)
        self.manifest("coord"); self.registry(leaseExpiresAt=base-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("controller-claim","--session","c1")
        _,first=self.cli("claim","--incident",iid,"--controller","c1")
        self.assertEqual(first["recoveryAttempts"],1)
        self.cli("defer","--incident",iid,"--controller","c1","--reason","wait","--cooldown","0")
        self.cli("controller-release","--session","c1")
        self.registry(leaseExpiresAt=base+60_000); self.cli("detect","--apply")
        pending=json.loads((self.runtime/"recovery-incidents"/f"{iid}.json").read_text())
        self.assertEqual(pending["recoveryAttempts"],1)
        self.assertEqual(pending["clearCandidateAt"],base)
        self.env["CRAFT_TEST_NOW_MS"]=str(base+1_000)
        self.registry(leaseExpiresAt=base); self.cli("detect","--apply")
        reopened=json.loads((self.runtime/"recovery-incidents"/f"{iid}.json").read_text())
        self.assertNotIn("clearCandidateAt",reopened)
        self.assertEqual(reopened["recoveryAttempts"],1)
        self.cli("controller-claim","--session","c2")
        _,second=self.cli("claim","--incident",iid,"--controller","c2")
        self.assertEqual(second["recoveryAttempts"],2)
        self.assertEqual(second["claimStage"],"wake-2")

    def test_cleared_then_reopened_condition_gets_fresh_wake_budget(self):
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("controller-claim","--session","c1")
        _,first=self.cli("claim","--incident",iid,"--controller","c1")
        self.assertEqual(first["claimStage"],"wake-1")
        self.cli("defer","--incident",iid,"--controller","c1","--reason","wait","--cooldown","0")
        self.cli("controller-release","--session","c1")
        self.registry(leaseExpiresAt=self.now+60000); self.cli("detect","--apply")
        _,resolved=self.cli("list","--state","resolved")
        self.assertEqual(resolved["incidents"][0]["recoveryAttempts"],0)
        self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        reopened_row=json.loads((self.runtime/"recovery-incidents"/f"{iid}.json").read_text())
        self.assertEqual(reopened_row["conditionRevision"],2)
        self.cli("controller-claim","--session","c2")
        _,reopened=self.cli("claim","--incident",iid,"--controller","c2")
        self.assertEqual(reopened["recoveryAttempts"],1)
        self.assertEqual(reopened["claimStage"],"wake-1")
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
    def test_controller_max_runtime_blocks_incident_actions(self):
        self.env["CRAFT_RECOVERY_CONTROLLER_MAX_RUNTIME_SECONDS"]="0"
        self.manifest("coord"); self.registry(leaseExpiresAt=self.now-1); self.cli("detect","--apply")
        _,rows=self.cli("list","--state","open"); iid=rows["incidents"][0]["incidentId"]
        self.cli("controller-claim","--session","c1")
        for command,args in (("controller-heartbeat",("--session","c1")),("claim",("--incident",iid,"--controller","c1"))):
            cp,_=self.cli(command,*args,ok=False); self.assertNotEqual(cp.returncode,0)
        self.cli("controller-release","--session","c1")
    def test_legacy_controller_future_lease_does_not_block_after_max_runtime(self):
        self.put(self.runtime/"self-healing/controller.json",{"schemaVersion":1,"sessionId":"old",
            "claimedAt":self.now-1000000,"lastHeartbeatAt":self.now-1000,"leaseExpiresAt":self.now+60000})
        _,row=self.cli("controller-claim","--session","replacement")
        self.assertEqual(row["sessionId"],"replacement"); self.assertIn("maxRuntimeExpiresAt",row)
    def test_expired_controller_cannot_revive_but_can_release(self):
        self.cli("controller-claim","--session","c1","--ttl","0")
        for command in ("controller-heartbeat","controller-claim"):
            cp,_=self.cli(command,"--session","c1",ok=False); self.assertNotEqual(cp.returncode,0)
        self.cli("controller-release","--session","c1")
        _,row=self.cli("controller-claim","--session","c1")
        self.assertEqual(row["sessionId"],"c1")
    def test_cwd_collision_is_critical(self):
        self.base(); self.manifest("worker"); self.lease(cwdCollisionSessions=["worker","w2"])
        _,d=self.cli("detect"); row=[x for x in d["observations"] if x["kind"]=="cwd-collision"][0]
        self.assertEqual(row["severity"],"critical"); self.assertIn("hard-refusal",row["allowedActions"])
    def test_reconcile_collision_emits_incidents(self):
        self.base(); shared="/tmp/shared-cwd"
        for sid in ("w1","w2"):
            self.manifest(sid,cwd=shared,labels=["agent-role::worker","parent-session::coord"])
            self.lease(sid,state="running",worktree=shared)
        cp=subprocess.run([str(SCRIPTS/"worker-lease.py"),"reconcile","--apply"],env=self.env,text=True,capture_output=True)
        self.assertEqual(cp.returncode,0,cp.stderr)
        self.assertEqual(set((json.loads((self.runtime/"worker-leases/w1.json").read_text()))["cwdCollisionSessions"]),{"w1","w2"})
        _,d=self.cli("detect"); rows=[x for x in d["observations"] if x["kind"]=="cwd-collision"]
        self.assertEqual({x["sessionId"] for x in rows},{"w1","w2"})

if __name__ == "__main__": unittest.main()

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import json, os, subprocess, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TOOL=Path(os.environ.get("CRAFT_TEST_SCRIPTS",ROOT/"scripts"))/"recovery-admission.py"
NOW=1786298100000

class RecoveryAdmissionV321Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.workspace=self.root/"workspace"; self.runtime=self.root/"runtime"; self.sessions=self.workspace/"sessions"
        self.config=self.workspace/"automations.json"; self.history=self.workspace/"automations-history.jsonl"
        self.harness=self.root/"controller-harness.py"
        self.harness.write_text('#!/bin/sh\necho \'{"healthy":true,"rows":[{"sessionId":"controller","sessionRole":"recovery-controller","state":"active"}]}\'\n')
        self.harness.chmod(0o755)
        self.env={**os.environ,"CRAFT_WORKSPACE":str(self.workspace),"CRAFT_RUNTIME":str(self.runtime),
          "CRAFT_SESSIONS":str(self.sessions),"CRAFT_TEST_NOW_MS":str(NOW),"CRAFT_RECOVERY_ARM_TTL_SECONDS":"60",
          "CRAFT_CONTROLLER_HARNESS":str(self.harness),"CRAFT_TEST_MODE":"1","CRAFT_SCHEDULER_PREFIRE_CLAIM_SUPPORTED":"1"}
        self.put(self.config,{"version":2,"automations":{"SchedulerTick":[{"id":"a321-notifier","enabled":False,"cron":"0 0 1 1 *","timezone":"UTC","labels":["agent-role::recovery-notifier"],"actions":[{"type":"prompt","prompt":"disabled"}]}]}})
        self.manifest("controller")
    def tearDown(self): self.tmp.cleanup()
    def put(self,path,value): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value)+"\n")
    def manifest(self,sid,role="recovery-controller",mode="persistent",archived=False,status="todo"):
        labels=[f"agent-role::{role}"]
        if mode: labels.append(f"controller-mode::{mode}")
        self.put(self.sessions/sid/"session.jsonl",{"id":sid,"labels":labels,"isArchived":archived,"sessionStatus":status})
    def incident(self,iid="i1",kind="coordinator-lease-stale",state="open",session="coord"):
        self.put(self.runtime/"recovery-incidents"/f"{iid}.json",{"incidentId":iid,"kind":kind,"state":state,"sessionId":session,"severity":"high","firstSeenAt":1,"evidenceFingerprint":"ef"+iid})
    def cli(self,*args,ok=True,env=None):
        cp=subprocess.run([str(TOOL),*args],env=env or self.env,text=True,capture_output=True)
        if ok and cp.returncode:self.fail(cp.stdout+cp.stderr)
        return cp,json.loads(cp.stdout)
    def matcher(self): return json.loads(self.config.read_text())["automations"]["SchedulerTick"][0]

    def test_report_only_never_arms(self):
        self.incident(); _,row=self.cli("report")
        self.assertEqual(row["actionableCount"],1); self.assertFalse(self.matcher()["enabled"])
        self.assertFalse((self.runtime/"self-healing/admission.json").exists())
    def test_kill_switch_blocks_before_config_mutation(self):
        self.incident(); (self.runtime/"self-healing.disabled").parent.mkdir(parents=True,exist_ok=True); (self.runtime/"self-healing.disabled").touch()
        cp,row=self.cli("tick","--controller-session","controller","--apply",ok=False)
        self.assertNotEqual(cp.returncode,0); self.assertEqual(row["reason"],"kill-switch-active"); self.assertFalse(self.matcher()["enabled"])
    def test_unsupported_scheduler_prefire_claim_blocks_apply(self):
        self.incident(); env={k:v for k,v in self.env.items() if k not in {"CRAFT_TEST_MODE","CRAFT_SCHEDULER_PREFIRE_CLAIM_SUPPORTED"}}
        cp,row=self.cli("tick","--controller-session","controller","--apply",ok=False,env=env)
        self.assertEqual(row["reason"],"scheduler-prefire-claim-unsupported"); self.assertFalse(self.matcher()["enabled"])
    def test_no_incident_creates_no_session_trigger(self):
        _,row=self.cli("tick","--controller-session","controller","--apply")
        self.assertEqual(row["reason"],"no-actionable-incidents"); self.assertFalse(self.matcher()["enabled"])
    def test_arm_exact_minute_and_receipt(self):
        self.incident(); _,row=self.cli("tick","--controller-session","controller","--apply")
        self.assertEqual(row["state"]["phase"],"armed"); self.assertTrue(self.matcher()["enabled"])
        self.assertEqual(self.matcher()["cron"],"56 17 9 8 *")
        self.assertIn("i1",self.matcher()["actions"][0]["prompt"])
        self.assertEqual(json.loads((self.runtime/"self-healing/admission.json").read_text())["fingerprint"],row["state"]["fingerprint"])
    def test_owner_gate_and_preservation_unknown_are_not_actionable(self):
        self.incident("g","owner-gate-blocked"); self.incident("p","preservation-unknown")
        _,row=self.cli("report"); self.assertEqual(row["actionableCount"],0)
    def test_requires_exact_persistent_controller(self):
        self.incident(); self.manifest("controller",mode=None)
        cp,row=self.cli("tick","--controller-session","controller","--apply",ok=False)
        self.assertIn("not marked persistent",row["error"]); self.assertFalse(self.matcher()["enabled"])
    def test_execution_history_disables_matcher(self):
        self.incident(); _,armed=self.cli("tick","--controller-session","controller","--apply")
        self.history.write_text(json.dumps({"id":"a321-notifier","ts":NOW+60000,"ok":True,"sessionId":"notifier"})+"\n")
        env={**self.env,"CRAFT_TEST_NOW_MS":str(NOW+70000)}
        _,row=self.cli("tick","--controller-session","controller","--apply",env=env)
        self.assertEqual(row["state"]["phase"],"notified"); self.assertFalse(self.matcher()["enabled"]); self.assertEqual(row["state"]["notifierSessionId"],"notifier")
    def test_duplicate_execution_fails_closed(self):
        self.incident(); self.cli("tick","--controller-session","controller","--apply")
        self.history.write_text("\n".join(json.dumps({"id":"a321-notifier","ts":NOW+60000+i,"ok":True,"sessionId":f"n{i}"}) for i in (1,2))+"\n")
        env={**self.env,"CRAFT_TEST_NOW_MS":str(NOW+70000)}
        cp,row=self.cli("tick","--controller-session","controller","--apply",ok=False,env=env)
        self.assertEqual(row["state"]["phase"],"blocked"); self.assertFalse(self.matcher()["enabled"])
    def test_missed_window_fails_closed(self):
        self.incident(); self.cli("tick","--controller-session","controller","--apply")
        env={**self.env,"CRAFT_TEST_NOW_MS":str(NOW+121000)}
        cp,row=self.cli("tick","--controller-session","controller","--apply",ok=False,env=env)
        self.assertEqual(row["state"]["reason"],"armed-window-expired-without-execution"); self.assertFalse(self.matcher()["enabled"])
    def test_kill_switch_disarms_armed_matcher(self):
        self.incident(); self.cli("tick","--controller-session","controller","--apply")
        (self.runtime/"self-healing.disabled").touch()
        _,row=self.cli("disarm","--apply"); self.assertEqual(row["state"]["reason"],"kill-switch-disarm"); self.assertFalse(self.matcher()["enabled"])
    def test_prepared_transaction_is_disabled_and_blocked(self):
        self.incident(); self.put(self.runtime/"self-healing/admission.json",{"phase":"prepared","armedAt":NOW})
        config=json.loads(self.config.read_text()); config["automations"]["SchedulerTick"][0]["enabled"]=True; self.put(self.config,config)
        cp,row=self.cli("tick","--controller-session","controller","--apply",ok=False)
        self.assertEqual(row["state"]["reason"],"incomplete-arm-transaction"); self.assertFalse(self.matcher()["enabled"])
    def test_install_guard_upserts_notifier_and_disables_legacy(self):
        self.put(self.config,{"version":2,"automations":{"SchedulerTick":[{"id":"a31101","enabled":True,"actions":[{"type":"prompt","prompt":"legacy"}]}]}})
        template=self.root/"template.json"; self.put(template,{"version":2,"automations":{"SchedulerTick":[{"id":"a321-notifier","enabled":False,"actions":[{"type":"prompt","prompt":"disabled"}]}]}})
        self.cli("install-guard","--template",str(template),"--apply")
        rows=json.loads(self.config.read_text())["automations"]["SchedulerTick"]
        self.assertEqual(sum(r.get("id")=="a321-notifier" for r in rows),1); self.assertTrue(all(not r.get("enabled",True) for r in rows))
    def test_duplicate_automation_id_refused(self):
        config=json.loads(self.config.read_text()); config["automations"]["SchedulerTick"].append(dict(config["automations"]["SchedulerTick"][0])); self.put(self.config,config); self.incident()
        cp,row=self.cli("tick","--controller-session","controller","--apply",ok=False)
        self.assertIn("duplicate",row["error"])
    def test_health_classifies_child_progress_and_stall(self):
        self.manifest("coord-a",role="coordinator",mode=None); self.manifest("coord-b",role="coordinator",mode=None)
        self.put(self.runtime/"coordinators/a.json",{"coordinatorSessionId":"coord-a","state":"authoritative","lastHeartbeatAt":NOW-7200000,"leaseExpiresAt":NOW-3600000,"activeChildren":["worker"]})
        self.put(self.runtime/"coordinators/b.json",{"coordinatorSessionId":"coord-b","state":"authoritative","lastHeartbeatAt":NOW-7200000,"leaseExpiresAt":NOW-3600000,"activeChildren":[]})
        self.put(self.runtime/"worker-leases/worker.json",{"sessionId":"worker","state":"active","lastHeartbeatAt":NOW-60000})
        _,row=self.cli("report"); health={r["project"]:r["health"] for r in row["coordinatorHealth"]}
        self.assertEqual(health,{"a":"child-active","b":"stalled"}); self.assertEqual(row["healthSummary"]["child-active"],1)

if __name__=="__main__": unittest.main()

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts")) / "recovery-admission.py"
NOW = 1786429000000
TOKEN = "never-print-this-test-token"
RUNTIME_VERSION = "0.11.4-admission.db51340"
RUNTIME_COMMIT = "db51340bfd4595178316f048b17c6cca552b2ad5"
WIRE_FIXTURE = json.loads((ROOT / "tests/fixtures/admission-v2-wire.json").read_text())

FAKE_CLI = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
from pathlib import Path
state_path = Path(sys.argv[1]); raw = sys.argv[2:]
state = json.loads(state_path.read_text())
state["allJson"] = state.get("allJson", True) and raw[:1] == ["--json"]
args = raw[1:] if raw[:1] == ["--json"] else raw
state.setdefault("records", []).append(args)
state["tokenMatched"] = state.get("tokenMatched", True) and os.environ.get("CRAFT_SERVER_TOKEN") == state["expectedToken"]
def save(): state_path.write_text(json.dumps(state))
def output(v): save(); print(json.dumps(v)); raise SystemExit(0)
def fields(start=2):
    out={}; i=start
    while i < len(args)-1:
        if args[i].startswith("--") and i+1 < len(args): out[args[i]]=args[i+1]; i+=2
        else: i+=1
    return out
def require_flags(command,f):
    missing=[flag for flag in state["wire"]["requiredFlags"][command] if flag not in f]
    if missing: output({"status":"blocked","reason":"missing flags: "+",".join(missing)})
def revision(message): return hashlib.sha256(message.encode("utf-8")).hexdigest()
def consume(receipt):
    generation=int(state["session"]["processingGeneration"])
    completed_at=int(state["now"])
    receipt.update({"deliveryState":"consumed","completedContentRevision":receipt["contentRevision"],
                    "completedProcessingGeneration":generation,"completedMessageId":"assistant-final-a",
                    "completedMessageAt":completed_at,"consumedAt":completed_at+1})
def receipt_for(f,message,create=True):
    scope="|".join(f[x] for x in ("--workspace","--session","--matcher","--action","--occurrence","--key"))
    receipt=state.setdefault("receipts",{}).get(scope)
    if receipt is None and create:
        receipt={"workspaceId":f["--workspace"],"sessionId":f["--session"],"targetKind":f["--target-kind"],"targetId":f["--target-id"],
                 "targetGeneration":f["--target-generation"],"matcherId":f["--matcher"],"actionId":f["--action"],
                 "occurrenceId":f["--occurrence"],"idempotencyKey":f["--key"],"messageId":"msg-"+str(len(state["receipts"])+1),
                 "deliveredAt":int(state["now"]),"deliveryState":"pending-consumption",
                 "acceptedProcessingGeneration":int(state["session"]["processingGeneration"]),
                 "contentRevision":revision(message),"message":message}
        state["receipts"][scope]=receipt
    return scope,receipt
if args == ["automation","capabilities"]:
    if state.get("rejectCapabilitiesOnce") and not state.get("capabilitiesRejected"):
        state["capabilitiesRejected"]=True; save(); raise SystemExit(9)
    output(state["wire"]["capabilities"])
if args == ["workspaces"]: output(state["workspaces"])
if args[:2] == ["automation","deliver"]:
    f=fields(); require_flags("deliver",f); message=args[-1]; scope,receipt=receipt_for(f,message)
    state.setdefault("deliveryScopes",[]).append(scope)
    if state.get("busyOnce") and not state.get("busySeen"):
        state["busySeen"]=True; output({"status":"busy","reason":"busy"})
    if state.get("blockedDeliver"): output({"status":"blocked","reason":"blocked"})
    duplicate=receipt["message"] != message or state["deliveryScopes"].count(scope)>1
    receipt.update(message=message,contentRevision=revision(message))
    if state.get("nullAcceptedGeneration"): receipt["acceptedProcessingGeneration"]=None
    for key in ("completedContentRevision","completedProcessingGeneration","completedMessageId","completedMessageAt","consumedAt"):
        receipt.pop(key,None)
    if state.get("nullOptionalCompletion"): receipt["completedMessageId"]=None
    if state.get("consume"): consume(receipt)
    elif duplicate:
        receipt["deliveryState"]="pending-consumption"
        if state.get("startProcessingOnDuplicate"):
            generation=int(state["session"]["processingGeneration"])+1
            state["session"].update(isProcessing=True,processingGeneration=generation,
                                    processingStartedAt=int(state["now"]),processingAgeMs=0,queueDepth=0)
            receipt["acceptedProcessingGeneration"]=generation
    status="consumed" if receipt["deliveryState"]=="consumed" else "pending-consumption"
    result={"status":status,"messageId":receipt["messageId"],"receipt":receipt}
    if state.get("crashAfterReceipt") and not state.get("crashed"):
        state["crashed"]=True; save(); raise SystemExit(9)
    output(result)
if args[:2] == ["automation","inspect"]:
    if state.get("rejectInspectOnce") and not state.get("inspectRejected"):
        state["inspectRejected"]=True; save(); raise SystemExit(9)
    f=fields(); require_flags("inspect",f)
    scope="|".join(f[x] for x in ("--workspace","--session","--matcher","--action","--occurrence","--key"))
    receipt=state.setdefault("receipts",{}).get(scope)
    if receipt and state.get("consume"): consume(receipt)
    if not receipt: output({"status":"missing","receipt":None,"session":state["session"]})
    output({"status":receipt["deliveryState"],"receipt":receipt,"session":state["session"]})
if args[:2] == ["automation","recover"]:
    f=fields(); require_flags("recover",f); state["recoverCalls"]=state.get("recoverCalls",0)+1
    scope="|".join(f[x] for x in ("--workspace","--session","--matcher","--action","--occurrence","--key"))
    receipt=state["receipts"][scope]; requested=int(f["--processing-generation"])
    if state.get("busyRecover"): output({"status":"busy","messageId":receipt["messageId"],"reason":"Recovery CAS is already held"})
    if state.get("blockedRecover"): output({"status":"blocked","messageId":receipt["messageId"],"reason":"blocked"})
    if state.get("consumeOnRecover"):
        consume(receipt)
        if state.get("badConsumedProof"): receipt["completedContentRevision"]="0"*64
        if state.get("missingConsumedFinalId"): receipt.pop("completedMessageId",None)
        if state.get("sameConsumedFinalId"): receipt["completedMessageId"]=receipt["messageId"]
        result={"status":"consumed","messageId":receipt["messageId"],
                "processingGeneration":int(state["session"]["processingGeneration"]),"receipt":receipt}
        if state.get("badConsumedPrevious"): result["previousProcessingGeneration"]=requested
        output(result)
    advanced=requested if state.get("badRecoverTransition") else requested+1
    state["session"].update(isProcessing=True,processingGeneration=advanced,processingStartedAt=int(state["now"]),processingAgeMs=0)
    output({"status":"recovered","messageId":receipt["messageId"],
            "previousProcessingGeneration":requested,"processingGeneration":advanced})
save(); print(json.dumps({"status":"error"})); raise SystemExit(2)
'''


class RecoveryAdmissionV322Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.workspace=self.root/"workspace"; self.runtime=self.root/"runtime"; self.sessions=self.workspace/"sessions"
        self.harness=self.root/"controller-harness.py"
        self.harness.write_text('#!/bin/sh\necho \'{"healthy":true,"rows":[{"sessionId":"controller","sessionRole":"recovery-controller","state":"active"}]}\'\n'); self.harness.chmod(0o755)
        self.fake_cli=self.root/"fake-cli.py"; self.fake_cli.write_text(FAKE_CLI); self.fake_cli.chmod(0o755)
        self.fake_state=self.root/"fake.json"
        wire=json.loads(json.dumps(WIRE_FIXTURE))
        idle=wire["idleSession"]
        self.put(self.fake_state,{"expectedToken":TOKEN,"now":NOW,"wire":wire,
            "workspaces":[{"id":"workspace-7","rootPath":str(self.workspace)}],"session":idle})
        self.env={**os.environ,"CRAFT_WORKSPACE":str(self.workspace),"CRAFT_RUNTIME":str(self.runtime),"CRAFT_SESSIONS":str(self.sessions),
            "CRAFT_TEST_NOW_MS":str(NOW),"CRAFT_CONTROLLER_HARNESS":str(self.harness),
            "CRAFT_RPC_CLI":f"{sys.executable} {self.fake_cli} {self.fake_state}","CRAFT_SERVER_TOKEN":TOKEN,
            "CRAFT_WORKSPACE_ID":"workspace-7","CRAFT_EXPECTED_RUNTIME_VERSION":RUNTIME_VERSION,"CRAFT_EXPECTED_RUNTIME_COMMIT":RUNTIME_COMMIT,
            "CRAFT_ADMISSION_RECOVERY_MIN_AGE_SECONDS":"60"}
        self.manifest("controller","recovery-controller",["controller-mode::persistent"])
        self.manifest("coord","coordinator")

    def tearDown(self): self.tmp.cleanup()
    def put(self,path,value): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value)+"\n")
    def fake(self): return json.loads(self.fake_state.read_text())
    def mutate_fake(self,**changes): row=self.fake(); row.update(changes); self.put(self.fake_state,row)
    def manifest(self,sid,role,extra=None,status="todo",archived=False,workspace_root=None):
        self.put(self.sessions/sid/"session.jsonl",{"id":sid,"labels":[f"agent-role::{role}",*(extra or [])],"sessionStatus":status,
            "isArchived":archived,"workspaceRootPath":str(workspace_root or self.workspace)})
    def registry(self,**changes):
        row={"project":"alpha","state":"authoritative","coordinatorSessionId":"coord","generation":7,"activeChildren":[]}
        row.update(changes); self.put(self.runtime/"coordinators/alpha.json",row)
    def incident(self,iid="i1",kind="coordinator-session-error",session="coord",project="alpha",evidence=None,coordinator="coord",work_unit=None):
        row={"incidentId":iid,"kind":kind,"state":"open","sessionId":session,"project":project,"severity":"high","firstSeenAt":1,
             "evidence":evidence or {},"evidenceFingerprint":"ef-"+iid,"conditionRevision":1,"coordinatorSessionId":coordinator}
        if work_unit: row["workUnit"]=work_unit
        self.put(self.runtime/f"recovery-incidents/{iid}.json",row)
    def cli(self,*args,ok=True,env=None):
        cp=subprocess.run([sys.executable,str(TOOL),*args],env=env or self.env,text=True,capture_output=True)
        if ok and cp.returncode: self.fail(cp.stdout+cp.stderr)
        return cp,json.loads(cp.stdout)
    def apply(self,ok=True,env=None): return self.cli("tick","--controller-session","controller","--apply",ok=ok,env=env)
    def direct_state(self):
        digest=hashlib.sha256(b"alpha").hexdigest()[:20]
        return json.loads((self.runtime/f"self-healing/coordinator-ticks/{digest}.json").read_text())
    def controller_state(self): return json.loads((self.runtime/"self-healing/admission.json").read_text())
    def records(self,command): return [r for r in self.fake().get("records",[]) if r[:2]==["automation",command]]

    def test_corrected_wire_fixture_and_cli_round_trip_matrix(self):
        wire=WIRE_FIXTURE
        self.assertIsInstance(wire["idleSession"]["processingGeneration"],int)
        self.assertIsNone(wire["idleSession"]["processingStartedAt"]); self.assertIsNone(wire["idleSession"]["processingAgeMs"])
        self.assertEqual(wire["runtimeCorrectionCommit"],RUNTIME_COMMIT)
        self.assertIs(wire["capabilities"]["available"],True)
        self.assertEqual(wire["capabilities"]["runtimeVersion"],RUNTIME_VERSION)
        self.assertEqual(wire["capabilities"]["runtimeCommit"],RUNTIME_COMMIT)
        self.assertEqual(wire["capabilitiesRequestParams"],[])
        for request in (wire["deliverRequestParams"],wire["inspectRequestParams"],wire["recoverRequestParams"]):
            self.assertEqual(len(request),2); self.assertIsInstance(request[0],str); self.assertIsInstance(request[1],dict)
        delivered=wire["deliveredResponse"]
        self.assertEqual(delivered["status"],"pending-consumption")
        self.assertIn("acceptedProcessingGeneration",delivered["receipt"])
        self.assertEqual(delivered["receipt"]["contentRevision"],hashlib.sha256(wire["exampleMessage"].encode()).hexdigest())
        self.assertEqual(wire["inspectIdleResponse"]["session"],wire["idleSession"])
        self.assertEqual(wire["inspectProcessingResponse"]["session"],wire["processingSession"])
        recovered=wire["recoveredResponse"]
        self.assertGreater(recovered["processingGeneration"],recovered["previousProcessingGeneration"])
        consumed=wire["consumedRaceResponse"]
        self.assertNotIn("previousProcessingGeneration",consumed)
        self.assertEqual(consumed["receipt"]["contentRevision"],hashlib.sha256(wire["exampleMessage"].encode()).hexdigest())
        self.assertEqual(consumed["receipt"]["contentRevision"],consumed["receipt"]["completedContentRevision"])
        self.assertNotEqual(consumed["receipt"]["messageId"],consumed["receipt"]["completedMessageId"])
        self.assertNotIn("processingGeneration",wire["busyResponse"])
        _,verified=self.cli("verify-runtime")
        self.assertTrue(verified["verified"]); self.assertEqual(verified["capabilityVersion"],2)
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        self.put(self.fake_state,fake); self.apply()
        for command in ("deliver","inspect","recover"):
            record=self.records(command)[-1]
            for flag in wire["requiredFlags"][command]: self.assertIn(flag,record)
        state=self.controller_state()
        self.assertEqual(state["recovery"]["previousProcessingGeneration"],41)
        self.assertEqual(state["recovery"]["processingGeneration"],42)

    def test_installer_restores_kill_switch_before_payload_and_requires_verification(self):
        text=(ROOT/"install.sh").read_text()
        sentinel=': > "$RUNTIME/self-healing.disabled"'
        self.assertEqual(text.count(sentinel),1)
        self.assertLess(text.index(sentinel),text.index("for name in $files; do"))
        self.assertLess(text.index("verify-runtime"),text.index("Optional launchd activation"))
        self.assertIn(RUNTIME_COMMIT,text)

    def test_installer_first_copy_observes_restored_kill_switch(self):
        home=self.root/"installer-home"; wrappers=self.root/"wrappers"; wrappers.mkdir()
        marker=self.root/"first-copy-observed"
        wrapper=wrappers/"cp"
        wrapper.write_text("#!/bin/sh\n"
            "test -f \"$HOME/.craft-agent/runtime/self-healing.disabled\" || exit 97\n"
            f"echo yes > {marker}\n"
            "exit 99\n")
        wrapper.chmod(0o755)
        env={**os.environ,"HOME":str(home),"PATH":f"{wrappers}:{os.environ.get('PATH','/usr/bin:/bin')}","CRAFT_PYTHON":sys.executable}
        cp=subprocess.run(["/bin/zsh",str(ROOT/"install.sh"),"--apply"],env=env,text=True,capture_output=True)
        self.assertEqual(cp.returncode,99,cp.stdout+cp.stderr)
        self.assertTrue(marker.exists())
        self.assertTrue((home/".craft-agent/runtime/self-healing.disabled").exists())
        self.assertFalse((home/".craft-agent/scripts/orchestration-common.py").exists())

    def test_verify_runtime_fails_closed_on_identity_mismatch_without_state(self):
        fake=self.fake(); fake["wire"]["capabilities"]["runtimeCommit"]="wrong"; self.put(self.fake_state,fake)
        cp,row=self.cli("verify-runtime",ok=False)
        self.assertEqual(cp.returncode,2); self.assertIn("runtime identity",row["error"])
        self.assertFalse((self.runtime/"self-healing/admission.json").exists())
        self.assertEqual(self.records("deliver"),[])

    def test_report_only_never_calls_runtime_or_creates_state(self):
        self.incident(); _,row=self.cli("report","--controller-session","controller")
        self.assertEqual(row["actionableCount"],1); self.assertEqual(self.fake().get("records"),None)
        self.assertFalse((self.runtime/"self-healing/admission.json").exists())

    def test_half_ttl_schedule_delivers_exact_generation_tick_before_expiry(self):
        self.registry(lastHeartbeatAt=NOW-1_900_000,leaseExpiresAt=NOW+1_700_000)
        self.apply(); state=self.direct_state()
        self.assertEqual(state["targetGeneration"],"7"); self.assertTrue(state["incidentIds"][0].startswith("tick-"))
        self.assertIn("COORDINATOR TICK v3.2.2",state["message"]); self.assertEqual(len(self.records("deliver")),1)

    def test_ambiguous_cross_project_owner_gets_no_scheduled_direct_tick(self):
        self.registry(lastHeartbeatAt=NOW-1_900_000,leaseExpiresAt=NOW+1_700_000)
        self.put(self.runtime/"coordinators/beta.json",{"project":"beta","state":"authoritative","coordinatorSessionId":"coord","generation":3,
            "activeChildren":[],"lastHeartbeatAt":NOW-1_900_000,"leaseExpiresAt":NOW+1_700_000})
        _,row=self.apply(); self.assertEqual(row["reason"],"no-actionable-or-outstanding-admissions")
        self.assertEqual(self.records("deliver"),[])

    def test_exact_generation_stale_goes_direct_to_coordinator(self):
        self.registry(); self.incident(kind="coordinator-lease-stale",evidence={"generation":7,"agePastExpiryMs":999999})
        _,row=self.apply(); state=self.direct_state()
        self.assertEqual(state["targetKind"],"coordinator"); self.assertEqual(state["targetGeneration"],"7")
        self.assertEqual(state["phase"],"pending-consumption"); self.assertEqual(len(self.records("deliver")),1)
        call=self.records("deliver")[0]
        self.assertEqual(call[call.index("--target-id")+1],"coord")
        self.assertEqual(call[call.index("--target-generation")+1],"7")
        self.assertFalse((self.runtime/"self-healing/admission.json").exists())

    def test_current_handoff_and_terminal_wait_share_one_direct_envelope(self):
        self.registry(activeChildren=["worker"]); self.manifest("worker","worker")
        self.incident("handoff","terminal-handoff-unconsumed","worker",evidence={"activeChild":True})
        self.incident("wait","external-wait-terminal","watcher",evidence={"waitId":"ci","terminalExitCode":0})
        self.manifest("watcher","worker")
        self.apply(); state=self.direct_state()
        self.assertEqual(state["incidentIds"],["handoff","wait"]); self.assertEqual(len(self.records("deliver")),1)

    def test_complex_recovery_remains_controller_bound(self):
        self.incident(kind="coordinator-session-error")
        self.apply(); state=self.controller_state()
        self.assertEqual(state["targetKind"],"controller"); self.assertEqual(state["targetSessionId"],"controller")
        self.assertIn("RECOVERY ADMISSION",state["message"])

    def test_misbound_live_controller_does_not_block_valid_direct_tick(self):
        self.registry(lastHeartbeatAt=NOW-1_900_000,leaseExpiresAt=NOW+1_700_000)
        self.incident(kind="coordinator-session-error")
        self.manifest("controller","recovery-controller",["controller-mode::persistent"],workspace_root=self.root/"other-workspace")
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertEqual(self.direct_state()["phase"],"pending-consumption")
        self.assertEqual(self.controller_state()["phase"],"blocked")
        self.assertEqual(len(self.records("deliver")),1)
        self.assertEqual(self.records("deliver")[0][self.records("deliver")[0].index("--target-kind")+1],"coordinator")

    def test_generation_change_blocks_old_outstanding_tick(self):
        self.registry(); self.incident(kind="coordinator-lease-stale",evidence={"generation":7})
        self.apply(); self.registry(generation=8)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertEqual(self.direct_state()["phase"],"blocked")
        self.assertIn("no longer authoritative",self.direct_state()["reason"])

    def test_hold_suppresses_scheduled_and_incident_direct_ticks(self):
        self.registry(state="hold",lastHeartbeatAt=NOW-2_000_000,leaseExpiresAt=NOW+1_000_000)
        self.incident(kind="coordinator-lease-stale",evidence={"generation":7})
        _,row=self.apply(); self.assertEqual(row["reason"],"no-actionable-or-outstanding-admissions")
        self.assertFalse((self.runtime/"self-healing/coordinator-ticks").exists())
        self.assertFalse((self.runtime/"self-healing/admission.json").exists()); self.assertEqual(self.records("deliver"),[])

    def test_preservation_ambiguity_routes_current_handoff_to_controller(self):
        self.registry(activeChildren=["worker"]); self.manifest("worker","worker")
        self.incident("handoff","terminal-handoff-unconsumed","worker",evidence={"activeChild":True})
        self.incident("preserve","preservation-unknown","worker")
        self.apply(); self.assertFalse((self.runtime/"self-healing/coordinator-ticks").exists())
        self.assertEqual(self.controller_state()["incidentIds"],["handoff"])

    def test_preservation_ambiguity_routes_external_wait_to_controller(self):
        self.registry(); self.manifest("watcher","worker")
        self.incident("wait","external-wait-terminal","watcher",evidence={"waitId":"ci","terminalExitCode":0})
        self.incident("preserve","preservation-unknown","watcher")
        self.apply(); self.assertFalse((self.runtime/"self-healing/coordinator-ticks").exists())
        self.assertEqual(self.controller_state()["incidentIds"],["wait"])

    def test_exact_owner_gate_blocks_direct_handoff(self):
        self.registry(activeChildren=["worker"]); self.manifest("worker","worker")
        self.incident("handoff","terminal-handoff-unconsumed","worker",evidence={"activeChild":True},work_unit="325")
        self.incident("gate","owner-gate-blocked","gate",evidence={"workUnit":"325"},work_unit="325")
        _,row=self.apply(); self.assertEqual(row["reason"],"no-actionable-or-outstanding-admissions")
        self.assertEqual(self.records("deliver"),[])

    def test_scope_excludes_wall_clock_and_volatile_evidence_age(self):
        self.incident(evidence={"agePastExpiryMs":1})
        _,first=self.cli("tick","--controller-session","controller")
        incident=json.loads((self.runtime/"recovery-incidents/i1.json").read_text()); incident["evidence"]["agePastExpiryMs"]=999_999
        self.put(self.runtime/"recovery-incidents/i1.json",incident)
        later=dict(self.env); later["CRAFT_TEST_NOW_MS"]=str(NOW+900_001)
        _,second=self.cli("tick","--controller-session","controller",env=later)
        self.assertEqual(first["results"][0]["scope"],second["results"][0]["scope"])
        self.assertEqual(first["results"][0]["fingerprint"],second["results"][0]["fingerprint"])

    def test_pending_never_cooldown_rearms(self):
        self.incident(); self.apply(); first=self.controller_state(); scope=first["scope"]
        later=dict(self.env); later["CRAFT_TEST_NOW_MS"]=str(NOW+900_001); later["CRAFT_ADMISSION_RECOVERY_MIN_AGE_SECONDS"]="3600"
        self.apply(env=later); second=self.controller_state()
        self.assertEqual(second["phase"],"pending-consumption"); self.assertEqual(second["scope"],scope)
        self.assertEqual(len(self.records("deliver")),1); self.assertEqual(len(self.records("inspect")),1)

    def test_repeated_pending_ticks_keep_one_outstanding_envelope(self):
        self.incident(); self.apply(); first=self.controller_state()
        for offset in range(1,6):
            later=dict(self.env); later["CRAFT_TEST_NOW_MS"]=str(NOW+offset*10_000)
            self.apply(env=later)
        final=self.controller_state()
        self.assertEqual(final["messageId"],first["messageId"]); self.assertEqual(final["scope"],first["scope"])
        self.assertEqual(len(self.records("deliver")),1); self.assertEqual(len(self.fake()["receipts"]),1)
        self.assertEqual(len(self.records("inspect")),5)

    def test_changed_meaningful_evidence_coalesces_same_message_and_scope(self):
        self.incident(); self.apply(); first=self.controller_state()
        self.incident("i2",kind="job-exit-unreported",session="worker")
        self.apply(); second=self.controller_state()
        self.assertEqual(second["messageId"],first["messageId"]); self.assertEqual(second["scope"],first["scope"])
        self.assertEqual(second["incidentIds"],["i1","i2"]); self.assertEqual(len(self.fake()["receipts"]),1)
        self.assertEqual(len(self.records("deliver")),2)

    def test_coalesced_redelivery_reinspects_started_revision_before_idle_deadline(self):
        self.incident(); self.apply(); first=self.controller_state()
        self.incident("i2",kind="job-exit-unreported",session="worker")
        fake=self.fake(); fake.update(startProcessingOnDuplicate=True,now=NOW+61_000); self.put(self.fake_state,fake)
        later=dict(self.env); later["CRAFT_TEST_NOW_MS"]=str(NOW+61_000)
        cp,_=self.apply(env=later)
        state=self.controller_state()
        self.assertEqual(cp.returncode,0)
        self.assertEqual(state["messageId"],first["messageId"])
        self.assertEqual(state["phase"],"pending-consumption")
        self.assertNotIn("reason",state)
        self.assertTrue(state["lastInspection"]["isProcessing"])
        self.assertEqual(state["lastInspection"]["processingAgeMs"],0)
        self.assertEqual(len(self.records("deliver")),2)
        self.assertEqual(len(self.records("inspect")),2)

    def test_consumed_receipt_ends_cycle_without_redelivery(self):
        self.incident(); self.apply(); self.mutate_fake(consume=True)
        self.apply(); consumed=self.controller_state(); self.assertEqual(consumed["phase"],"consumed")
        self.apply(); self.assertEqual(len(self.records("deliver")),1)

    def test_recovered_response_proves_generation_transition_then_second_stall_blocks(self):
        self.incident(); self.apply()
        fake=self.fake(); fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        self.put(self.fake_state,fake); self.apply(); recovering=self.controller_state()
        self.assertEqual(recovering["phase"],"recovering"); self.assertEqual(self.fake()["recoverCalls"],1)
        self.assertEqual(recovering["recovery"]["previousProcessingGeneration"],41)
        self.assertEqual(recovering["recovery"]["processingGeneration"],42)
        fake=self.fake(); fake["session"].update(isProcessing=True,processingGeneration=42,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        self.put(self.fake_state,fake); cp,_=self.apply(ok=False)
        self.assertEqual(cp.returncode,2); self.assertEqual(self.controller_state()["reason"],"bounded-admission-recovery-exhausted")
        self.assertEqual(self.fake()["recoverCalls"],1)

    def test_second_stuck_generation_blocks_after_recovery_became_idle(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        self.put(self.fake_state,fake); self.apply()
        fake=self.fake(); fake["session"].update(isProcessing=False,processingGeneration=42,processingStartedAt=None,processingAgeMs=None)
        self.put(self.fake_state,fake); self.apply(); self.assertEqual(self.controller_state()["phase"],"pending-consumption")
        fake=self.fake(); fake["session"].update(isProcessing=True,processingGeneration=42,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        self.put(self.fake_state,fake); cp,_=self.apply(ok=False)
        self.assertEqual(cp.returncode,2); self.assertEqual(self.controller_state()["reason"],"bounded-admission-recovery-exhausted")
        self.assertEqual(self.fake()["recoverCalls"],1)

    def test_idle_pending_envelope_blocks_at_deadline(self):
        self.incident(); self.apply(); later=dict(self.env); later["CRAFT_TEST_NOW_MS"]=str(NOW+61_000)
        cp,_=self.apply(ok=False,env=later); self.assertEqual(cp.returncode,2)
        self.assertEqual(self.controller_state()["reason"],"pending-admission-not-processing-at-deadline")

    def test_recover_response_cas_mismatch_hard_blocks(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=9,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        fake["blockedRecover"]=True; self.put(self.fake_state,fake)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2); self.assertEqual(self.controller_state()["phase"],"blocked")

    def test_recover_rejects_non_advanced_generation(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=9,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        fake["badRecoverTransition"]=True; self.put(self.fake_state,fake)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertIn("generation transition mismatch",self.controller_state()["reason"])

    def test_consumed_recovery_race_uses_revision_proof_without_previous_generation(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        fake["consumeOnRecover"]=True; self.put(self.fake_state,fake)
        self.apply(); state=self.controller_state(); self.assertEqual(state["phase"],"consumed")
        self.assertNotIn("previousProcessingGeneration",state["recovery"])
        self.assertEqual(state["recovery"]["processingGeneration"],41)
        self.assertEqual(state["receipt"]["contentRevision"],state["receipt"]["completedContentRevision"])
        self.assertNotEqual(state["receipt"]["completedMessageId"],state["messageId"])
        self.assertEqual(state["receipt"]["completedMessageId"],"assistant-final-a")

    def test_consumed_recovery_race_rejects_spurious_previous_generation(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        fake.update(consumeOnRecover=True,badConsumedPrevious=True); self.put(self.fake_state,fake)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertIn("must not claim a recovery transition",self.controller_state()["reason"])

    def test_consumed_recovery_race_rejects_missing_final_assistant_id(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        fake.update(consumeOnRecover=True,missingConsumedFinalId=True); self.put(self.fake_state,fake)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertIn("consumed receipt proof invalid",self.controller_state()["reason"])

    def test_consumed_recovery_race_rejects_envelope_as_final_id(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        fake.update(consumeOnRecover=True,sameConsumedFinalId=True); self.put(self.fake_state,fake)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertIn("consumed receipt proof invalid",self.controller_state()["reason"])

    def test_consumed_recovery_race_rejects_bad_revision_proof(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        fake.update(consumeOnRecover=True,badConsumedProof=True); self.put(self.fake_state,fake)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertIn("consumed receipt proof invalid",self.controller_state()["reason"])

    def test_busy_recovery_cas_is_retryable_without_spending_attempt(self):
        self.incident(); self.apply(); fake=self.fake()
        fake["session"].update(isProcessing=True,processingGeneration=41,processingStartedAt=NOW-61_000,processingAgeMs=61_000)
        fake["busyRecover"]=True; self.put(self.fake_state,fake)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,75)
        self.assertEqual(self.controller_state()["recoveryAttempts"],0)
        fake=self.fake(); fake["busyRecover"]=False; self.put(self.fake_state,fake)
        self.apply(); self.assertEqual(self.controller_state()["phase"],"recovering")
        self.assertEqual(self.controller_state()["recovery"]["processingGeneration"],42)

    def test_idle_inspect_retains_numeric_durable_generation(self):
        self.incident(); self.apply(); self.apply(); state=self.controller_state()
        self.assertEqual(state["phase"],"pending-consumption")
        self.assertFalse(state["lastInspection"]["isProcessing"])
        self.assertEqual(state["lastInspection"]["processingGeneration"],WIRE_FIXTURE["idleSession"]["processingGeneration"])
        self.assertIsNone(state["lastInspection"]["processingStartedAt"])
        self.assertIsNone(state["lastInspection"]["processingAgeMs"])

    def test_crash_retry_keeps_scope_and_receipt(self):
        self.incident(); self.mutate_fake(crashAfterReceipt=True)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,75); prepared=self.controller_state(); self.assertEqual(prepared["phase"],"prepared")
        self.mutate_fake(crashAfterReceipt=False); self.apply(); final=self.controller_state()
        self.assertEqual(final["scope"],prepared["scope"]); self.assertEqual(final["messageId"],"msg-1")
        self.assertEqual(len(self.fake()["receipts"]),1)

    def test_unknown_delivery_retry_uses_original_receipt_deadline(self):
        self.incident(); self.mutate_fake(crashAfterReceipt=True)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,75)
        later=dict(self.env); later["CRAFT_TEST_NOW_MS"]=str(NOW+61_000)
        self.mutate_fake(crashAfterReceipt=False)
        cp,_=self.apply(ok=False,env=later); self.assertEqual(cp.returncode,2)
        self.assertEqual(self.controller_state()["deliveredAt"],NOW)
        self.assertEqual(self.controller_state()["reason"],"pending-admission-not-processing-at-deadline")
        self.assertEqual(len(self.fake()["receipts"]),1)

    def test_busy_retry_keeps_prepared_scope(self):
        self.incident(); self.mutate_fake(busyOnce=True)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,75); scope=self.controller_state()["scope"]
        self.apply(); self.assertEqual(self.controller_state()["scope"],scope); self.assertEqual(len(self.fake()["receipts"]),1)

    def test_unconsumed_receipt_omits_optional_completion_fields(self):
        self.incident(); self.mutate_fake(nullOptionalCompletion=True)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertIn("optional completion fields must be omitted",self.controller_state()["reason"])

    def test_receipt_requires_numeric_accepted_processing_generation(self):
        self.incident(); self.mutate_fake(nullAcceptedGeneration=True)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertIn("receipt lifecycle invalid",self.controller_state()["reason"])

    def test_capability_v1_invalid_availability_or_extra_state_fails_closed(self):
        for mutation in (lambda cap: cap.update(version=1),lambda cap: cap["deliveryStates"].append("queued"),
                         lambda cap: cap.update(available=False),lambda cap: cap.pop("available")):
            with self.subTest(mutation=mutation):
                if (self.runtime/"self-healing/admission.json").exists(): (self.runtime/"self-healing/admission.json").unlink()
                self.incident(); fake=self.fake(); cap=fake["wire"]["capabilities"]; cap["version"]=2; cap["deliveryStates"]=["delivered","pending-consumption","consumed","duplicate","busy","blocked"]
                mutation(cap); self.put(self.fake_state,fake)
                cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2); self.assertEqual(self.controller_state()["phase"],"blocked")

    def test_workspace_root_mismatch_fails_closed(self):
        self.incident(); self.mutate_fake(workspaces=[{"id":"workspace-7","rootPath":str(self.root/"other")}])
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2); self.assertEqual(self.records("deliver"),[])

    def test_transient_discovery_retries_prepared_scope(self):
        self.incident(); self.mutate_fake(rejectCapabilitiesOnce=True)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,75); scope=self.controller_state()["scope"]
        self.apply(); self.assertEqual(self.controller_state()["scope"],scope)

    def test_transient_inspection_retries_without_blocking_or_redelivery(self):
        self.incident(); self.apply(); self.mutate_fake(rejectInspectOnce=True)
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,75); self.assertEqual(self.controller_state()["phase"],"pending-consumption")
        self.apply(); self.assertEqual(self.controller_state()["phase"],"pending-consumption")
        self.assertEqual(len(self.records("deliver")),1)

    def test_kill_switch_prevents_all_runtime_calls(self):
        self.incident(); (self.runtime/"self-healing.disabled").parent.mkdir(parents=True,exist_ok=True); (self.runtime/"self-healing.disabled").touch()
        cp,row=self.apply(ok=False); self.assertEqual(cp.returncode,2); self.assertEqual(row["reason"],"kill-switch-active")
        self.assertEqual(self.fake().get("records"),None)

    def test_legacy_state_requires_owner_reset(self):
        self.incident(); self.put(self.runtime/"self-healing/admission.json",{"schemaVersion":2,"phase":"notified"})
        cp,_=self.apply(ok=False); self.assertEqual(cp.returncode,2)
        self.assertEqual(self.controller_state()["reason"],"legacy-admission-state-requires-owner-reset")

    def test_unreadable_state_is_preserved_and_never_redelivered(self):
        self.incident(); path=self.runtime/"self-healing/admission.json"; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("{broken")
        cp,row=self.apply(ok=False); self.assertEqual(cp.returncode,2); self.assertEqual(path.read_text(),"{broken")
        self.assertEqual(self.records("deliver"),[]); self.assertTrue(row["results"][0]["statePreserved"])

    def test_unreadable_owner_gate_blocks_direct_selection(self):
        self.registry(lastHeartbeatAt=NOW-1_900_000,leaseExpiresAt=NOW+1_700_000)
        path=self.runtime/"owner-gates/alpha/broken.json"; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("{broken")
        cp,row=self.apply(ok=False); self.assertEqual(cp.returncode,2); self.assertIn("unreadable owner gate",row["error"])
        self.assertEqual(self.records("deliver"),[])

    def test_unreadable_incident_blocks_direct_selection(self):
        self.registry(lastHeartbeatAt=NOW-1_900_000,leaseExpiresAt=NOW+1_700_000)
        path=self.runtime/"recovery-incidents/broken.json"; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("{broken")
        cp,row=self.apply(ok=False); self.assertEqual(cp.returncode,2); self.assertIn("unreadable recovery incident",row["error"])
        self.assertEqual(self.records("deliver"),[])

    def test_unreadable_registry_blocks_global_owner_proof(self):
        self.registry(lastHeartbeatAt=NOW-1_900_000,leaseExpiresAt=NOW+1_700_000)
        path=self.runtime/"coordinators/beta.json"; path.write_text("{broken")
        cp,row=self.apply(ok=False); self.assertEqual(cp.returncode,2); self.assertIn("unreadable coordinator registry",row["error"])
        self.assertEqual(self.records("deliver"),[])

    def test_token_never_appears_in_output_or_receipt(self):
        self.incident(); cp,row=self.apply(); material=cp.stdout+json.dumps(row)+self.controller_state().__repr__()
        self.assertNotIn(TOKEN,material); self.assertTrue(self.fake()["tokenMatched"]); self.assertTrue(self.fake()["allJson"])


class RecoveryAdmissionCronV322Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.craft=self.root/".craft-agent"
        scripts=self.craft/"scripts"; scripts.mkdir(parents=True); (self.craft/"runtime/self-healing").mkdir(parents=True)
        self.capture=self.root/"capture.json"; self.rpc=self.root/"craft-cli"; self.rpc.write_text("#!/bin/sh\nexit 0\n"); self.rpc.chmod(0o700)
        (scripts/"recovery-admission.py").write_text("import json,os,sys\nopen(os.environ['CRAFT_TEST_CAPTURE'],'w').write(json.dumps({'rpc':os.environ.get('CRAFT_RPC_CLI'),'args':sys.argv[1:]}))\n")
        self.config=self.craft/"runtime/self-healing/persistent-controller.json"
        self.config.write_text(json.dumps({"sessionId":"controller","workspaceId":"workspace-7","expectedRuntimeVersion":RUNTIME_VERSION,
            "expectedRuntimeCommit":RUNTIME_COMMIT,"serverUrl":"wss://craft.example.test:9100","rpcCli":str(self.rpc)}))
        self.env={**os.environ,"HOME":str(self.root),"CRAFT_HOME":str(self.craft),"CRAFT_PYTHON":sys.executable,"CRAFT_TEST_CAPTURE":str(self.capture)}
    def tearDown(self): self.tmp.cleanup()
    def test_launcher_pins_runtime_identity_and_cli(self):
        cp=subprocess.run(["/bin/zsh",str(ROOT/"scripts/recovery-admission-cron.sh")],env=self.env,text=True,capture_output=True)
        self.assertEqual(cp.returncode,0,cp.stdout+cp.stderr); row=json.loads(self.capture.read_text())
        self.assertEqual(row["rpc"],str(self.rpc)); self.assertIn("--apply",row["args"]); self.assertIn("--expected-runtime-commit",row["args"])


if __name__ == "__main__": unittest.main()

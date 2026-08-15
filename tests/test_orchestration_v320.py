import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts"))

class OrchestrationV320Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"; self.runtime = self.root / "runtime"; self.sessions.mkdir()
        self.env = os.environ.copy(); self.env.update({"CRAFT_WORKSPACE": str(self.root), "CRAFT_SESSIONS": str(self.sessions),
            "CRAFT_RUNTIME": str(self.runtime), "CRAFT_COORDINATOR_TTL_SECONDS": "1", "CRAFT_FALLBACK_TTL_SECONDS": "1"})
        self.runtime.mkdir(exist_ok=True); (self.runtime / "reporting-policy.json").write_text(json.dumps({"mode":"pull-only","ownerFacingSessionId":"owner","configuredAt":1}))
    def tearDown(self): self.temp.cleanup()
    def manifest(self, sid, project="demo", model="pi/gpt-5.6-sol", connection="chatgpt-plus", archived=False,
                 labels=None, messages=1, tokens=1, name=None):
        d = self.sessions / sid; d.mkdir(exist_ok=True)
        value = {"id": sid, "name": name or f"[{project}] Coordinator v3.4.35", "createdAt": int(time.time()*1000), "isArchived": archived,
            "sessionStatus": "todo", "workingDirectory": str(self.root/f"wt-{sid}"), "projectId": f"pid-{project}", "permissionMode": "allow-all",
            "model": model, "llmConnection": connection, "messageCount": messages, "tokenUsage": {"totalTokens": tokens},
            "labels": labels or ["coordinators", "agent-role::coordinator", f"project::{project}", "protocol-version::3.4.35"]}
        (d/"session.jsonl").write_text(json.dumps(value)+"\n"); return value
    def exec_tool(self, script, *args, ok=True):
        p = subprocess.run([str(SCRIPTS/script), *args], env=self.env, text=True, capture_output=True, timeout=20)
        if ok and p.returncode: self.fail(f"{script} failed {p.returncode}: {p.stdout}\n{p.stderr}")
        return p
    def claim(self, sid="c1", project="demo"):
        return self.exec_tool("coordinator-registry.py", "claim", "--project", project, "--session", sid, "--project-id", f"pid-{project}")
    def cert(self, **changes):
        value = {"project":"demo","workUnit":"1","candidateSha":"a"*40,"auditedSha":"a"*40,
            "auditorSessionId":"audit","auditVerdict":"PASS","requiredCiRunIds":["run-1","run-2"],
            "requiredCiAllSuccess":True,"mergeSha":"b"*40,"headUnchanged":True,
            "mergedMainRunIds":["run-3"],"mergedMainAllSuccess":True,"unresolvedGates":[]}
        value.update(changes); p=self.root/"cert.json"; p.write_text(json.dumps(value)); return p

    def test_00_version_markers_match_changelog(self):
        # Patch releases must not leave stale version markers that make live
        # coordinators report an owner-visible protocol discrepancy.
        import re
        changelog = (ROOT / "CHANGELOG.md").read_text()
        released = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M).group(1)
        version = f"v{released}"
        self.assertIn(f"# Coordinator Lifecycle Protocol {version}",
                      (ROOT / "skills/coordinator-lifecycle-protocol/SKILL.md").read_text())
        self.assertIn(f"# Worker Completion Protocol {version}",
                      (ROOT / "skills/worker-completion-protocol/SKILL.md").read_text())
        controller = (ROOT / "skills/self-healing-controller/SKILL.md").read_text()
        self.assertIn(f"# Self-Healing Controller — Protocol {version}", controller)
        self.assertIn(f"Craft Protocol {version} incidents", controller)
        self.assertIn(f"(canonical {version})", (ROOT / "scripts/coordinator-kickoff.md").read_text())
        self.assertIn(f"protocol v{released}.", (ROOT / "install.sh").read_text())
        self.assertIn(f'PROTOCOL_VERSION = "{version}"',
                      (ROOT / "scripts/recovery-admission.py").read_text())

    def test_01_claim_and_renew(self):
        self.manifest("c1"); self.claim(); self.exec_tool("coordinator-registry.py","renew","--project","demo","--session","c1")
    def test_01b_completed_assistant_activity_renews_exact_owner(self):
        self.env["CRAFT_COORDINATOR_TTL_SECONDS"] = "60"
        self.manifest("c1"); self.claim()
        path = self.runtime / "coordinators/demo.json"; row = json.loads(path.read_text())
        activity = int(time.time() * 1000)
        row["lastHeartbeatAt"] = activity - 120_000; row["leaseExpiresAt"] = activity - 60_000
        path.write_text(json.dumps(row) + "\n")
        with (self.sessions / "c1/session.jsonl").open("a") as handle:
            handle.write(json.dumps({"type": "assistant", "timestamp": activity - 1, "isIntermediate": True, "content": "working"}) + "\n")
            handle.write(json.dumps({"type": "assistant", "timestamp": activity, "content": "completed turn"}) + "\n")
        report = json.loads(self.exec_tool("coordinator-registry.py", "reconcile-activity", "--apply").stdout)
        self.assertEqual(report["changed"], ["demo"])
        renewed = json.loads(path.read_text())
        self.assertEqual(renewed["lastHeartbeatAt"], activity)
        self.assertEqual(renewed["leaseExpiresAt"], activity + 60_000)
        self.assertEqual(renewed["activityEvidenceAt"], activity)

    def test_01c_intermediate_or_old_activity_does_not_renew(self):
        self.env["CRAFT_COORDINATOR_TTL_SECONDS"] = "60"
        self.manifest("c1"); self.claim()
        path = self.runtime / "coordinators/demo.json"; before = json.loads(path.read_text())
        with (self.sessions / "c1/session.jsonl").open("a") as handle:
            handle.write(json.dumps({"type": "assistant", "timestamp": int(time.time() * 1000), "isIntermediate": True}) + "\n")
        report = json.loads(self.exec_tool("coordinator-registry.py", "reconcile-activity", "--apply").stdout)
        self.assertEqual(report["changed"], [])
        self.assertEqual(json.loads(path.read_text())["lastHeartbeatAt"], before["lastHeartbeatAt"])

    def test_02_split_brain_refused(self):
        self.manifest("c1"); self.manifest("c2"); self.claim(); p=self.exec_tool("coordinator-registry.py","claim","--project","demo","--session","c2",ok=False); self.assertEqual(p.returncode,3)
    def test_03_two_phase_transfer(self):
        self.manifest("c1"); self.manifest("c2"); self.claim(); self.exec_tool("coordinator-registry.py","begin-transfer","--project","demo","--session","c1","--successor","c2","--reason","rotation"); self.exec_tool("coordinator-registry.py","accept-transfer","--project","demo","--session","c2","--expected-generation","1"); r=json.loads((self.runtime/"coordinators/demo.json").read_text()); self.assertEqual(r["coordinatorSessionId"],"c2")
    def test_04_interrupted_transfer_blocks_second(self):
        self.manifest("c1"); self.manifest("c2"); self.manifest("c3"); self.claim(); self.exec_tool("coordinator-registry.py","begin-transfer","--project","demo","--session","c1","--successor","c2","--reason","x"); p=self.exec_tool("coordinator-registry.py","begin-transfer","--project","demo","--session","c1","--successor","c3","--reason","y",ok=False); self.assertNotEqual(p.returncode,0)
    def test_05_fallback_ttl_detected(self):
        self.manifest("c1",model="claude",connection="claude"); self.claim(); r=json.loads((self.runtime/"coordinators/demo.json").read_text())
        self.assertEqual(r["fallbackExpiresAt"]-r["fallbackSince"],1000)
        r["fallbackExpiresAt"]=0; (self.runtime/"coordinators/demo.json").write_text(json.dumps(r)); p=self.exec_tool("coordinator-registry.py","inspect","--project","demo",ok=False); self.assertIn("fallback-ttl-expired",p.stdout)
    def test_06_archived_owner_detected(self):
        m=self.manifest("c1"); self.claim(); m["isArchived"]=True; (self.sessions/"c1/session.jsonl").write_text(json.dumps(m)+"\n"); p=self.exec_tool("coordinator-registry.py","inspect","--project","demo",ok=False); self.assertIn("owner-not-live",p.stdout)
    def test_06b_coordinator_in_worker_terminal_status_is_flagged(self):
        # A coordinator parked in needs-review is deaf to queued admission wakes;
        # validate must surface it as an issue instead of reporting healthy.
        m=self.manifest("c1"); self.claim()
        m["sessionStatus"]="needs-review"; (self.sessions/"c1/session.jsonl").write_text(json.dumps(m)+"\n")
        p=self.exec_tool("coordinator-registry.py","inspect","--project","demo",ok=False)
        self.assertIn("coordinator-worker-terminal-status:needs-review",p.stdout)
        v=self.exec_tool("coordinator-registry.py","validate",ok=False)
        self.assertEqual(v.returncode,2); self.assertIn("coordinator-worker-terminal-status",v.stdout)
        # An intentionally parked HOLD project is not flagged for the same status.
        row=json.loads((self.runtime/"coordinators/demo.json").read_text()); row["state"]="hold"
        (self.runtime/"coordinators/demo.json").write_text(json.dumps(row))
        p=self.exec_tool("coordinator-registry.py","inspect","--project","demo")
        self.assertNotIn("coordinator-worker-terminal-status",p.stdout)

    def test_06d_unarchived_predecessor_is_flagged(self):
        self.manifest("c1"); self.manifest("c2"); self.claim()
        self.exec_tool("coordinator-registry.py","begin-transfer","--project","demo","--session","c1","--successor","c2","--reason","rotation")
        self.exec_tool("coordinator-registry.py","accept-transfer","--project","demo","--session","c2","--expected-generation","1")
        p=self.exec_tool("coordinator-registry.py","inspect","--project","demo",ok=False)
        self.assertIn("predecessor-not-archived:c1",p.stdout)
        # Archiving the predecessor clears the debt.
        m=self.manifest("c1"); m["isArchived"]=True
        (self.sessions/"c1/session.jsonl").write_text(json.dumps(m)+"\n")
        p=self.exec_tool("coordinator-registry.py","inspect","--project","demo")
        self.assertNotIn("predecessor-not-archived",p.stdout)

    def test_06c_complexity_threshold_is_flagged(self):
        # Rotation thresholds are machine-flagged before context deaths, not after.
        m=self.manifest("c1", messages=501, tokens=250_000); self.claim()
        p=self.exec_tool("coordinator-registry.py","inspect","--project","demo",ok=False)
        self.assertIn("coordinator-complexity-threshold:messages=501",p.stdout)
        self.assertIn("coordinator-complexity-threshold:tokens=250000",p.stdout)
        # Below thresholds no flag is raised.
        m["messageCount"]=10; m["tokenUsage"]={"totalTokens":1000}
        (self.sessions/"c1/session.jsonl").write_text(json.dumps(m)+"\n")
        p=self.exec_tool("coordinator-registry.py","inspect","--project","demo")
        self.assertNotIn("coordinator-complexity-threshold",p.stdout)

    def test_07_hold_blocks_spawn(self):
        self.exec_tool("owner-gate.py","hold","--project","gve","--reason","owner hold"); p=self.exec_tool("owner-gate.py","check","--project","gve","--action","spawn",ok=False); self.assertEqual(p.returncode,4)
    def test_08_hold_requires_exact_resume(self):
        self.exec_tool("owner-gate.py","hold","--project","gve","--reason","hold"); p=self.exec_tool("owner-gate.py","resolve","--project","gve","--gate","project-hold","--choice","GO","--authority","direct-owner","--evidence","msg",ok=False); self.assertNotEqual(p.returncode,0)
    def test_08b_rehold_after_resume_creates_fresh_open_gate(self):
        self.exec_tool("owner-gate.py","hold","--project","gve","--reason","first hold")
        # An open hold is idempotent and never duplicated.
        again=json.loads(self.exec_tool("owner-gate.py","hold","--project","gve","--reason","dup").stdout)
        self.assertTrue(again.get("idempotent")); self.assertEqual(again["gate"]["gateId"],"project-hold")
        self.exec_tool("owner-gate.py","resolve","--project","gve","--gate","project-hold","--choice","RESUME","--authority","direct-owner","--evidence","resume")
        self.exec_tool("owner-gate.py","check","--project","gve","--action","spawn")
        # A repeated HOLD after RESUME mints a fresh open gate instead of
        # idempotently returning the resolved one and silently not holding.
        rehold=json.loads(self.exec_tool("owner-gate.py","hold","--project","gve","--reason","second hold").stdout)
        self.assertFalse(rehold.get("idempotent",False))
        gate_id=rehold["gate"]["gateId"]
        self.assertNotEqual(gate_id,"project-hold"); self.assertTrue(gate_id.startswith("project-hold"))
        self.assertEqual(rehold["gate"]["state"],"open")
        p=self.exec_tool("owner-gate.py","check","--project","gve","--action","spawn",ok=False); self.assertEqual(p.returncode,4)
        # The generated hold gate keeps exact-RESUME semantics.
        p=self.exec_tool("owner-gate.py","resolve","--project","gve","--gate",gate_id,"--choice","GO","--authority","direct-owner","--evidence","msg",ok=False)
        self.assertNotEqual(p.returncode,0)
        self.exec_tool("owner-gate.py","resolve","--project","gve","--gate",gate_id,"--choice","RESUME","--authority","direct-owner","--evidence","resume again")
        self.exec_tool("owner-gate.py","check","--project","gve","--action","spawn")
    def test_08b_coordinator_name_must_say_project_and_protocol(self):
        # v3.4.32: successors are spawned by their predecessor, which named them
        # whatever it liked — "l2 client", "Coordinator Handoff", "Coordinator
        # Lifecycle Protocol" — so the owner's coordinator list stopped saying which
        # project or protocol version a row belonged to, while superseded sessions
        # piled up beside the live one.
        self.manifest("coord", name="Coordinator Handoff")
        self.exec_tool("coordinator-registry.py", "claim", "--project", "demo",
                       "--session", "coord", "--ttl", "3600")
        p = self.exec_tool("coordinator-registry.py", "inspect", "--project", "demo", ok=False)
        self.assertIn("coordinator-name-nonconforming", p.stdout)
        self.manifest("coord", name="[demo] Coordinator v3.4.32")
        p = self.exec_tool("coordinator-registry.py", "inspect", "--project", "demo")
        self.assertNotIn("coordinator-name-nonconforming", p.stdout)
        # A conforming name that names someone else's project is its own defect.
        self.manifest("coord", name="[other] Coordinator v3.4.32")
        p = self.exec_tool("coordinator-registry.py", "inspect", "--project", "demo", ok=False)
        self.assertIn("coordinator-name-project-mismatch", p.stdout)

    def test_09_gate_direct_owner_required(self):
        self.exec_tool("owner-gate.py","create","--project","demo","--gate","g1","--question","Q?","--choices","A,B","--owner-only-category","human-product-judgment-action","--external-effect","product-direction-decision","--scope","merge"); p=self.exec_tool("owner-gate.py","resolve","--project","demo","--gate","g1","--choice","A","--authority","coordinator","--evidence","relay",ok=False); self.assertNotEqual(p.returncode,0)
    def test_09b_technical_transition_cannot_create_owner_gate(self):
        p=self.exec_tool("owner-gate.py","create","--project","demo","--gate","technical","--question","Continue after CI?","--choices","CONTINUE,HOLD","--scope","implement",ok=False)
        self.assertNotEqual(p.returncode,0); self.assertIn("--owner-only-category",p.stderr); self.assertIn("required",p.stderr)
    def test_10_resolved_gate_allows_action(self):
        self.exec_tool("owner-gate.py","create","--project","demo","--gate","g1","--question","Q?","--choices","A,B","--owner-only-category","irreversible-destructive","--external-effect","irreversible-data-change","--scope","merge"); self.exec_tool("owner-gate.py","resolve","--project","demo","--gate","g1","--choice","A","--authority","direct-owner","--evidence","msg"); self.exec_tool("owner-gate.py","check","--project","demo","--action","merge")
    def test_11_valid_completion_certificate(self): self.exec_tool("completion-certificate.py","validate","--file",str(self.cert()))
    def test_12_changed_head_rejected(self):
        p=self.exec_tool("completion-certificate.py","validate","--file",str(self.cert(auditedSha="c"*40)),ok=False); self.assertIn("audited-head-mismatch",p.stdout)
    def test_13_reused_ci_rejected(self):
        p=self.exec_tool("completion-certificate.py","validate","--file",str(self.cert(mergedMainRunIds=["run-2"])),ok=False); self.assertIn("reused-ci-as-readback",p.stdout)
    def test_14_unresolved_gate_rejected(self):
        p=self.exec_tool("completion-certificate.py","validate","--file",str(self.cert(unresolvedGates=["g1"])),ok=False); self.assertIn("unresolved-gates",p.stdout)
    def test_15_recovery_ledger_adopts_live_lane(self):
        self.manifest("c1"); self.claim(); self.manifest("w1", labels=["agent-role::worker","parent-session::c1","work-unit::u1","attempt::1"])
        d=self.runtime/"worker-leases"; d.mkdir(parents=True); (d/"w1.json").write_text(json.dumps({"sessionId":"w1","parentSessionId":"c1","role":"worker","workUnit":"u1","attempt":"1","state":"running","preservationState":"unknown"}))
        p=self.exec_tool("recovery-ledger.py","reconstruct","--project","demo"); self.assertIn('"safeToLaunchNewLane": false',p.stdout)
    def test_16_metadata_and_complexity_drift(self):
        self.manifest("c1",messages=600,tokens=210000); self.claim(); p=self.exec_tool("coordinator-reconcile.py",ok=False); self.assertIn("rotation-recommended",p.stdout); self.assertIn("canonical-name-drift",p.stdout)
    def test_17_unscoped_gate_does_not_block_unrelated_unit(self):
        self.exec_tool("owner-gate.py","create","--project","demo","--gate","policy","--question","Q?","--choices","A,B","--owner-only-category","conflicting-direct-owner-priorities","--external-effect","product-direction-decision","--scope","implement")
        self.exec_tool("owner-gate.py","check","--project","demo","--work-unit","unrelated","--action","implement")
    def test_18_shared_native_project_does_not_cross_scope(self):
        self.manifest("c1",project="client"); self.claim("c1","client")
        self.manifest("server-worker", project="shared", labels=["agent-role::worker","project::server","parent-session::other","work-unit::u"])
        d=self.runtime/"worker-leases"; d.mkdir(parents=True,exist_ok=True)
        (d/"server-worker.json").write_text(json.dumps({"sessionId":"server-worker","parentSessionId":"other","role":"worker","workUnit":"u","state":"running","worktree":str(self.root/'wt-server-worker')}))
        p=self.exec_tool("recovery-ledger.py","reconstruct","--project","client"); self.assertNotIn('"sessionId": "server-worker"',p.stdout)
    def test_19_authoritative_parent_mapping_is_exclusive(self):
        self.manifest("alpha-coord",project="alpha"); self.claim("alpha-coord","alpha")
        self.manifest("beta-coord",project="beta"); self.claim("beta-coord","beta")
        self.manifest("worker",labels=["agent-role::worker","project::beta","parent-session::alpha-coord","work-unit::u"])
        d=self.runtime/"worker-leases"; d.mkdir(parents=True,exist_ok=True)
        (d/"worker.json").write_text(json.dumps({"sessionId":"worker","parentSessionId":"alpha-coord","role":"worker","workUnit":"u","state":"running","preservationState":"pushed"}))
        alpha=json.loads(self.exec_tool("recovery-ledger.py","reconstruct","--project","alpha").stdout)["observed"]
        beta=json.loads(self.exec_tool("recovery-ledger.py","reconstruct","--project","beta").stdout)["observed"]
        self.assertEqual([c["sessionId"] for c in alpha["activeChildren"]],["worker"])
        self.assertEqual(beta["activeChildren"],[])
        self.assertEqual(alpha["projectMappingConflicts"][0]["childLabelProject"],"beta")
        self.assertIn("project-mapping:worker",alpha["unknowns"])
    def test_20_duplicate_coordinator_session_is_global_hard_refusal(self):
        self.manifest("shared",project="alpha"); self.claim("shared","alpha")
        rejected=self.exec_tool("coordinator-registry.py","claim","--project","beta","--session","shared","--project-id","pid-beta",ok=False)
        self.assertEqual(rejected.returncode,3); self.assertIn("cross-project-owner-refused",rejected.stdout)
        alpha=json.loads((self.runtime/"coordinators/alpha.json").read_text())
        beta={**alpha,"project":"beta","projectId":"pid-beta"}
        (self.runtime/"coordinators/beta.json").write_text(json.dumps(beta))
        self.manifest("worker",labels=["agent-role::worker","project::beta","parent-session::shared","work-unit::u"])
        d=self.runtime/"worker-leases"; d.mkdir(parents=True,exist_ok=True)
        (d/"worker.json").write_text(json.dumps({"sessionId":"worker","parentSessionId":"shared","role":"worker","workUnit":"u","state":"stalled","preservationState":"pushed"}))
        for project in ("alpha","beta"):
            ledger=json.loads(self.exec_tool("recovery-ledger.py","reconstruct","--project",project).stdout)["observed"]
            self.assertEqual(ledger["activeChildren"],[])
            self.assertIn("ambiguous-parent:shared",ledger["unknowns"])
        validation=self.exec_tool("coordinator-registry.py","validate",ok=False)
        self.assertEqual(validation.returncode,2); self.assertIn("cross-project-owner",validation.stdout)
        incidents=json.loads(self.exec_tool("recovery-incident.py","detect").stdout)["observations"]
        self.assertIn("ambiguous-coordinator-owner",[r["kind"] for r in incidents])
        conflict=[r for r in incidents if r["kind"]=="project-mapping-conflict"][0]
        self.assertIsNone(conflict["project"]); self.assertNotIn("worker-stalled",[r["kind"] for r in incidents])
    def test_21_v311_label_is_compatible_with_v320_name(self):
        self.env["CRAFT_COORDINATOR_TTL_SECONDS"]="60"  # compatibility assertion must not race a 1s expiry
        self.manifest("c1",labels=["coordinators","agent-role::coordinator","project::demo","protocol-version::3.1.1"])
        self.claim("c1","demo")
        self.exec_tool("coordinator-registry.py","inspect","--project","demo")
        report=json.loads(self.exec_tool("coordinator-reconcile.py").stdout)
        self.assertTrue(report["healthy"]); self.assertEqual(report["coordinators"][0]["issues"],[])
        self.assertTrue(report["coordinators"][0]["desiredName"].endswith("v3.3.0"))

if __name__ == "__main__": unittest.main()

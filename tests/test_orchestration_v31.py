import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", Path.home() / ".craft-agent/scripts"))

class OrchestrationV31Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"; self.runtime = self.root / "runtime"; self.sessions.mkdir()
        self.env = os.environ.copy(); self.env.update({"CRAFT_WORKSPACE": str(self.root), "CRAFT_SESSIONS": str(self.sessions),
            "CRAFT_RUNTIME": str(self.runtime), "CRAFT_COORDINATOR_TTL_SECONDS": "1", "CRAFT_FALLBACK_TTL_SECONDS": "1"})
    def tearDown(self): self.temp.cleanup()
    def manifest(self, sid, project="demo", model="pi/gpt-5.6-sol", connection="chatgpt-plus", archived=False,
                 labels=None, messages=1, tokens=1):
        d = self.sessions / sid; d.mkdir(exist_ok=True)
        value = {"id": sid, "name": sid, "createdAt": int(time.time()*1000), "isArchived": archived,
            "sessionStatus": "todo", "workingDirectory": str(self.root/f"wt-{sid}"), "projectId": f"pid-{project}",
            "model": model, "llmConnection": connection, "messageCount": messages, "tokenUsage": {"totalTokens": tokens},
            "labels": labels or ["coordinators", "agent-role::coordinator", f"project::{project}", "protocol-version::3"]}
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

    def test_01_claim_and_renew(self):
        self.manifest("c1"); self.claim(); self.exec_tool("coordinator-registry.py","renew","--project","demo","--session","c1")
    def test_02_split_brain_refused(self):
        self.manifest("c1"); self.manifest("c2"); self.claim(); p=self.exec_tool("coordinator-registry.py","claim","--project","demo","--session","c2",ok=False); self.assertEqual(p.returncode,3)
    def test_03_two_phase_transfer(self):
        self.manifest("c1"); self.manifest("c2"); self.claim(); self.exec_tool("coordinator-registry.py","begin-transfer","--project","demo","--session","c1","--successor","c2","--reason","rotation"); self.exec_tool("coordinator-registry.py","accept-transfer","--project","demo","--session","c2","--expected-generation","1"); r=json.loads((self.runtime/"coordinators/demo.json").read_text()); self.assertEqual(r["coordinatorSessionId"],"c2")
    def test_04_interrupted_transfer_blocks_second(self):
        self.manifest("c1"); self.manifest("c2"); self.manifest("c3"); self.claim(); self.exec_tool("coordinator-registry.py","begin-transfer","--project","demo","--session","c1","--successor","c2","--reason","x"); p=self.exec_tool("coordinator-registry.py","begin-transfer","--project","demo","--session","c1","--successor","c3","--reason","y",ok=False); self.assertNotEqual(p.returncode,0)
    def test_05_fallback_ttl_detected(self):
        self.manifest("c1",model="claude",connection="claude"); self.claim(); r=json.loads((self.runtime/"coordinators/demo.json").read_text()); r["fallbackExpiresAt"]=0; (self.runtime/"coordinators/demo.json").write_text(json.dumps(r)); p=self.exec_tool("coordinator-registry.py","inspect","--project","demo",ok=False); self.assertIn("fallback-ttl-expired",p.stdout)
    def test_06_archived_owner_detected(self):
        m=self.manifest("c1"); self.claim(); m["isArchived"]=True; (self.sessions/"c1/session.jsonl").write_text(json.dumps(m)+"\n"); p=self.exec_tool("coordinator-registry.py","inspect","--project","demo",ok=False); self.assertIn("owner-not-live",p.stdout)
    def test_07_hold_blocks_spawn(self):
        self.exec_tool("owner-gate.py","hold","--project","gve","--reason","owner hold"); p=self.exec_tool("owner-gate.py","check","--project","gve","--action","spawn",ok=False); self.assertEqual(p.returncode,4)
    def test_08_hold_requires_exact_resume(self):
        self.exec_tool("owner-gate.py","hold","--project","gve","--reason","hold"); p=self.exec_tool("owner-gate.py","resolve","--project","gve","--gate","project-hold","--choice","GO","--authority","direct-owner","--evidence","msg",ok=False); self.assertNotEqual(p.returncode,0)
    def test_09_gate_direct_owner_required(self):
        self.exec_tool("owner-gate.py","create","--project","demo","--gate","g1","--question","Q?","--choices","A,B","--scope","merge"); p=self.exec_tool("owner-gate.py","resolve","--project","demo","--gate","g1","--choice","A","--authority","coordinator","--evidence","relay",ok=False); self.assertNotEqual(p.returncode,0)
    def test_10_resolved_gate_allows_action(self):
        self.exec_tool("owner-gate.py","create","--project","demo","--gate","g1","--question","Q?","--choices","A,B","--scope","merge"); self.exec_tool("owner-gate.py","resolve","--project","demo","--gate","g1","--choice","A","--authority","direct-owner","--evidence","msg"); self.exec_tool("owner-gate.py","check","--project","demo","--action","merge")
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
        self.exec_tool("owner-gate.py","create","--project","demo","--gate","policy","--question","Q?","--choices","A,B","--scope","implement")
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

if __name__ == "__main__": unittest.main()

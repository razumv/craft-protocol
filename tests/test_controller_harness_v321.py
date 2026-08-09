# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import json, os, subprocess, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=Path(os.environ.get("CRAFT_TEST_SCRIPTS",ROOT/"scripts"))
TOOL=SCRIPTS/"controller-harness.py"

class ControllerHarnessV321Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.sessions=self.root/"sessions"; self.runtime=self.root/"runtime"; self.table=self.root/"processes.json"
        self.env={**os.environ,"CRAFT_SESSIONS":str(self.sessions),"CRAFT_RUNTIME":str(self.runtime),
                  "CRAFT_WORKSPACE":str(self.root/"workspace"),"CRAFT_PROCESS_TABLE":str(self.table),
                  "CRAFT_TEST_MODE":"1","CRAFT_TEST_NO_SIGNAL":"1"}
        self.put(self.table,{})
    def tearDown(self): self.tmp.cleanup()
    def put(self,path,value): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value)+"\n")
    def manifest(self,sid,*,role="recovery-controller",archived=False,status="todo"):
        self.put(self.sessions/sid/"session.jsonl",{"id":sid,"labels":[f"agent-role::{role}"],"isArchived":archived,"sessionStatus":status,"createdAt":1})
    def processes(self,rows): self.put(self.table,{str(k):v for k,v in rows.items()})
    def cli(self,*args,ok=True):
        cp=subprocess.run([str(TOOL),*args],env=self.env,text=True,capture_output=True)
        if ok and cp.returncode:self.fail(cp.stdout+cp.stderr)
        return cp,json.loads(cp.stdout) if cp.stdout else None
    def register(self,sid="c1",pid=100): return self.cli("register","--session",sid,"--pid",str(pid))
    def controller_pair(self):
        self.manifest("c1"); self.manifest("c2"); self.env["CRAFT_TEST_CALLER_PID"]="200"
        self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"S1"},
                        200:{"command":"bun pi-agent-server/index.js","startToken":"CURRENT"}})
        self.register("c1",100); self.register("c2",200)

    def test_register_exact_harness(self):
        self.manifest("c1"); self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"S1","ppid":9}})
        _,row=self.register(); self.assertEqual(row["harnessPid"],100); self.assertEqual(row["state"],"registered")
    def test_register_refuses_non_controller_and_app(self):
        self.manifest("w",role="worker"); self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"S1"}})
        cp,_=self.cli("register","--session","w","--pid","100",ok=False); self.assertNotEqual(cp.returncode,0)
        self.manifest("c1"); self.processes({101:{"command":"/Applications/Craft Agents.app/Contents/MacOS/Craft Agents","startToken":"S2"}})
        cp,_=self.cli("register","--session","c1","--pid","101",ok=False); self.assertNotEqual(cp.returncode,0)
    def test_reap_requires_archived_terminal_and_refuses_self(self):
        self.controller_pair()
        cp,_=self.cli("reap","--session","c1","--current-session","c2","--apply",ok=False); self.assertNotEqual(cp.returncode,0)
        self.manifest("c1",archived=True,status="needs-review")
        cp,_=self.cli("reap","--session","c1","--current-session","c1","--apply",ok=False); self.assertNotEqual(cp.returncode,0)
    def test_archive_first_reap_succeeds(self):
        self.controller_pair()
        self.manifest("c1",archived=True,status="needs-review")
        _,row=self.cli("reap","--session","c1","--current-session","c2","--apply"); self.assertEqual(row["state"],"reaped")
        self.assertFalse((self.runtime/"controller-harnesses/c1.json").exists())
    def test_pid_reuse_fails_closed(self):
        self.controller_pair(); self.manifest("c1",archived=True,status="needs-review")
        self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"S2"},
                        200:{"command":"bun pi-agent-server/index.js","startToken":"CURRENT"}})
        cp,row=self.cli("reap","--session","c1","--current-session","c2","--apply",ok=False)
        self.assertNotEqual(cp.returncode,0); self.assertIn("PID identity changed",row["error"])
    def test_sigterm_without_exit_keeps_receipt(self):
        self.controller_pair(); self.manifest("c1",archived=True,status="needs-review"); self.env["CRAFT_TEST_SIGNAL_STICKS"]="1"
        self.env["CRAFT_CONTROLLER_REAP_WAIT_SECONDS"]="0.01"
        cp,row=self.cli("reap","--session","c1","--current-session","c2","--apply",ok=False)
        self.assertNotEqual(cp.returncode,0); self.assertEqual(row["state"],"still-running-after-sigterm")
        self.assertTrue((self.runtime/"controller-harnesses/c1.json").exists())
    def test_unknown_process_lookup_keeps_receipt(self):
        self.controller_pair(); self.manifest("c1",archived=True,status="needs-review")
        self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"S1","lookupError":True},
                        200:{"command":"bun pi-agent-server/index.js","startToken":"CURRENT"}})
        cp,row=self.cli("reap","--session","c1","--current-session","c2","--apply",ok=False)
        self.assertNotEqual(cp.returncode,0); self.assertIn("lookup unknown",row["error"])
        self.assertTrue((self.runtime/"controller-harnesses/c1.json").exists())
        cp,row=self.cli("report",ok=False); self.assertEqual(row["counts"]["lookupUnknown"],1)
    def test_current_session_argument_is_mandatory(self):
        self.manifest("c1"); self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"S1"}}); self.register()
        self.manifest("c1",archived=True,status="needs-review")
        cp,_=self.cli("reap","--session","c1","--apply",ok=False)
        self.assertNotEqual(cp.returncode,0); self.assertTrue((self.runtime/"controller-harnesses/c1.json").exists())
    def test_other_live_controller_receipt_cannot_authorize_reap(self):
        self.controller_pair(); self.manifest("c3")
        self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"S1"},
                        200:{"command":"bun pi-agent-server/index.js","startToken":"CURRENT"},
                        300:{"command":"bun pi-agent-server/index.js","startToken":"OTHER"}})
        self.register("c3",300); self.manifest("c1",archived=True,status="needs-review")
        cp,row=self.cli("reap","--session","c1","--current-session","c3","--apply",ok=False)
        self.assertNotEqual(cp.returncode,0); self.assertIn("does not belong to calling harness",row["error"])
        self.assertTrue((self.runtime/"controller-harnesses/c1.json").exists())
    def test_report_already_exited_receipt_is_unhealthy(self):
        self.manifest("c1"); self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"S1"}}); self.register()
        self.processes({100:{"alive":False}})
        cp,row=self.cli("report",ok=False); self.assertNotEqual(cp.returncode,0)
        self.assertEqual(row["counts"]["alreadyExited"],1); self.assertFalse(row["healthy"])
    def test_report_allows_one_active_one_terminal(self):
        self.manifest("active"); self.manifest("old");
        self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"A"},101:{"command":"bun pi-agent-server/index.js","startToken":"B"}})
        self.register("active",100); self.register("old",101); self.manifest("old",archived=True,status="needs-review")
        cp,row=self.cli("report"); self.assertEqual(cp.returncode,0); self.assertTrue(row["healthy"])
        self.assertEqual(row["counts"]["active"],1); self.assertEqual(row["counts"]["terminalAwaitingReap"],1)
    def test_report_detects_growth(self):
        self.processes({100:{"command":"bun pi-agent-server/index.js","startToken":"A"},101:{"command":"bun pi-agent-server/index.js","startToken":"B"}})
        for sid,pid in (("a",100),("b",101)): self.manifest(sid); self.register(sid,pid)
        cp,row=self.cli("report",ok=False); self.assertNotEqual(cp.returncode,0); self.assertFalse(row["healthy"])
        self.assertIn("more than one registered active",row["violations"][0])

if __name__=="__main__": unittest.main()

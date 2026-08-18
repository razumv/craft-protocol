# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; S=Path(os.environ.get('CRAFT_TEST_SCRIPTS',ROOT/'scripts'))
class AdmissionV3435(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.r=Path(self.t.name);self.sessions=self.r/'sessions';self.runtime=self.r/'runtime'
  self.env={**os.environ,'CRAFT_WORKSPACE':str(self.r),'CRAFT_SESSIONS':str(self.sessions),'CRAFT_RUNTIME':str(self.runtime)}
  self.manifest('coord','coordinator',['project::p','coordinators','protocol-version::3.4.41'],name='[p] Coordinator v3.4.41',project_id='native')
  (self.runtime/'coordinators').mkdir(parents=True,exist_ok=True);(self.runtime/'coordinators/p.json').write_text(json.dumps({'project':'p','projectId':'native','state':'authoritative','coordinatorSessionId':'coord','generation':1}))
  self.manifest('worker','worker',['parent-session::coord','project::p','work-unit::u','attempt::1','protocol-version::3.4.41'],project_id='native')
 def tearDown(self):self.t.cleanup()
 def manifest(self,sid,role,labels=(),name=None,project_id=None):
  wt=self.r/f'wt-{sid}';wt.mkdir(parents=True,exist_ok=True);p=self.sessions/sid;p.mkdir(parents=True,exist_ok=True)
  (p/'session.jsonl').write_text(json.dumps({'id':sid,'isArchived':False,'sessionStatus':'todo','workspaceRootPath':str(self.r),'workingDirectory':str(wt),'projectId':project_id,'name':name or sid,'llmConnection':'chatgpt-plus','model':'pi/gpt-5.6-terra' if role!='coordinator' else 'pi/gpt-5.6-sol','permissionMode':'allow-all','labels':[f'agent-role::{role}',*labels]})+'\n')
 def tool(self,name,*args,ok=True,cwd=None):
  p=subprocess.run([sys.executable,str(S/name),*args],env=self.env,text=True,capture_output=True,cwd=cwd)
  if ok:self.assertEqual(p.returncode,0,p.stderr)
  return p
 def reserve_without_worker(self,token='t',worktree=None,cwd=None):
  worker_manifest=self.sessions/'worker'/'session.jsonl';saved=worker_manifest.read_text();worker_manifest.unlink()
  result=self.tool('lane-admission.py','reserve','--token',token,'--parent','coord','--role','worker','--work-unit','u','--attempt','1','--worktree',str(worktree or self.r/'wt-worker'),cwd=cwd)
  worker_manifest.write_text(saved);return result
 def test_confirm_from_exact_child_worktree_succeeds(self):
  self.reserve_without_worker(cwd=self.r)
  self.tool('lane-admission.py','confirm','--token','t','--session','worker',cwd=self.r/'wt-worker')
 def test_reserve_still_rejects_parent_and_repository_root(self):
  for token,worktree in (('parent',self.r/'wt-coord'),('root',self.r)):
   result=self.tool('lane-admission.py','reserve','--token',token,'--parent','coord','--role','worker','--work-unit','u','--attempt','1','--worktree',str(worktree),ok=False,cwd=self.r)
   self.assertNotEqual(result.returncode,0);self.assertIn('lane worktree cannot be parent or repository root',result.stderr)
 def test_confirm_still_rejects_reserved_identity_mismatch(self):
  self.reserve_without_worker(cwd=self.r)
  worker_manifest=self.sessions/'worker'/'session.jsonl';row=json.loads(worker_manifest.read_text());row['projectId']='other';worker_manifest.write_text(json.dumps(row)+'\n')
  result=self.tool('lane-admission.py','confirm','--token','t','--session','worker',ok=False,cwd=self.r/'wt-worker')
  self.assertNotEqual(result.returncode,0);self.assertIn('admission manifest/registry identity mismatch',result.stderr)
 def test_confirm_still_rejects_live_worktree_collision(self):
  self.reserve_without_worker(cwd=self.r)
  self.manifest('other','worker',['parent-session::coord','project::p','work-unit::other','attempt::1','protocol-version::3.4.41'],project_id='native')
  other=self.sessions/'other'/'session.jsonl';row=json.loads(other.read_text());row['workingDirectory']=str(self.r/'wt-worker');other.write_text(json.dumps(row)+'\n')
  result=self.tool('lane-admission.py','confirm','--token','t','--session','worker',ok=False,cwd=self.r/'wt-worker')
  self.assertNotEqual(result.returncode,0);self.assertIn('live manifest worktree collision',result.stderr)
 def test_reservation_must_match_before_lease(self):
  token='t';wt=str(self.r/'wt-worker')
  worker_manifest=self.sessions/'worker'/'session.jsonl'; saved=worker_manifest.read_text(); worker_manifest.unlink()
  self.tool('lane-admission.py','reserve','--token',token,'--parent','coord','--role','worker','--work-unit','u','--attempt','1','--worktree',wt)
  worker_manifest.write_text(saved)
  self.tool('worker-lease.py','create','--session','worker','--admission-token',token,ok=False)
  self.tool('lane-admission.py','confirm','--token',token,'--session','worker')
  self.tool('worker-lease.py','create','--session','worker','--admission-token',token)
 def test_owner_gate_reuses_exact_resolved_decision(self):
  tail=('--project','p','--work-unit','u','--question','Deploy?','--choices','YES,NO','--owner-only-category','human-product-judgment-action','--external-effect','product-direction-decision','--decision-key','deploy-preference')
  self.tool('owner-gate.py','create',*tail[:2],'--gate','g1',*tail[2:]);self.tool('owner-gate.py','resolve','--project','p','--gate','g1','--choice','YES','--authority','direct-owner','--evidence','owner')
  out=self.tool('owner-gate.py','create',*tail[:2],'--gate','g2',*tail[2:]).stdout;self.assertIn('reusedDecision',out)
if __name__=='__main__':unittest.main()

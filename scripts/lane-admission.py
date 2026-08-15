#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Strict two-phase admission for v3.4.35 worker/auditor lanes; legacy records pass unchanged."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re
from pathlib import Path
import os
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('common',HERE/'orchestration-common.py');common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common) # type: ignore
ROOT=common.RUNTIME/'lane-admissions'; LOCK=common.RUNTIME/'lane-admissions.lock'; ROLES={'worker','auditor'}; VERSION='3.4.35'
def clean(x):
 x=re.sub(r'[^A-Za-z0-9._-]+','-',x.strip()).strip('-')
 if not x: raise SystemExit('invalid identifier')
 return x
def path(t): return ROOT/f'{clean(t)}.json'
def lab(m,p): return common.label_value(m,p)
def live(s):
 m=common.read_manifest(s)
 if not m or m.get('id')!=s or not common.session_live(m): raise SystemExit('live session manifest required')
 return m
def role(m): return common.role_of(m)
def canon(raw): return common.canonical_path(raw)
def wd(m): return canon(m.get('workingDirectory') or m.get('sdkCwd') or '')
def fp(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def strict(m): return f'protocol-version::{VERSION}' in set(m.get('labels') or [])
def parent_truth(parent, project):
 m=live(parent)
 if role(m)!='coordinator' or lab(m,'project::')!=project: raise SystemExit('parent coordinator project label mismatch')
 r=common.read_json(common.RUNTIME/'coordinators'/f'{project}.json')
 if not r or r.get('state')!='authoritative' or r.get('coordinatorSessionId')!=parent: raise SystemExit('parent is not authoritative coordinator')
 if m.get('projectId')!=r.get('projectId'): raise SystemExit('parent projectId mismatch')
 return r,m
def collisions(identity,session=None):
 worktree=canon(identity['worktree'])
 for p in ROOT.glob('*.json'):
  x=common.read_json(p) or {}; i=x.get('identity') or {}
  if x.get('state') in {'reserved','admitted'} and (canon(i.get('worktree'))==worktree or (session and x.get('sessionId')==session)) and x.get('token')!=identity.get('token'): raise SystemExit('live admission collision')
 for sid,m in common.all_manifests().items():
  if sid!=session and common.session_live(m) and role(m) in ROLES and wd(m)==worktree: raise SystemExit('live manifest worktree collision')
 for p in (common.RUNTIME/'worker-leases').glob('*.json'):
  x=common.read_json(p) or {}
  if p.stem!=session and x.get('state')!='handoff-ready' and canon(x.get('worktree'))==worktree: raise SystemExit('live lease worktree collision')
def cmd_reserve(a):
 parent=live(a.parent); project=lab(parent,'project::')
 if not project: raise SystemExit('parent project label required')
 r,_=parent_truth(a.parent,project); worktree=canon(a.worktree)
 if worktree in {wd(parent),str(Path.cwd().resolve())}: raise SystemExit('lane worktree cannot be parent or repository root')
 i={'parentSessionId':a.parent,'role':a.role,'project':project,'projectId':r.get('projectId'),'generation':r.get('generation'),'workUnit':a.work_unit,'attempt':str(a.attempt),'worktree':worktree}
 t=clean(a.token or fp(i)[:24]);i['token']=t
 with common.file_lock(LOCK):
  old=common.read_json(path(t))
  if old:
   if old.get('identity')!=i: raise SystemExit('admission token identity mismatch')
   print(json.dumps({'ok':True,'idempotent':True,'admission':old},indent=2));return 0
  collisions(i);v={'schemaVersion':1,'token':t,'state':'reserved','identity':i,'identityDigest':fp(i),'reservedAt':common.now_ms(),'sessionId':None};common.atomic_json(path(t),v)
 print(json.dumps({'ok':True,'admission':v},indent=2));return 0
def cmd_confirm(a):
 with common.file_lock(LOCK):
  v=common.read_json(path(a.token));
  if not v: raise SystemExit('admission reservation not found')
  i=v.get('identity') or {}; m=live(a.session); r,_=parent_truth(i.get('parentSessionId',''),i.get('project',''))
  actual={'parentSessionId':lab(m,'parent-session::') or m.get('parentSessionId'),'role':role(m),'project':lab(m,'project::'),'projectId':m.get('projectId'),'generation':r.get('generation'),'workUnit':lab(m,'work-unit::'),'attempt':lab(m,'attempt::'),'worktree':wd(m),'token':i.get('token')}
  required={f'agent-role::{i.get("role")}',f'parent-session::{i.get("parentSessionId")}',f'project::{i.get("project")}',f'work-unit::{i.get("workUnit")}',f'attempt::{i.get("attempt")}',f'protocol-version::{VERSION}'}
  if not strict(m) or not required.issubset(set(m.get('labels') or [])): raise SystemExit('v3.4.35 canonical lane labels required')
  if m.get('llmConnection')!='chatgpt-plus' or m.get('model')!='pi/gpt-5.6-terra' or m.get('permissionMode') not in {'allow-all','execute'}: raise SystemExit('v3.4.35 worker/auditor runtime identity mismatch')
  if actual!=i: raise SystemExit('admission manifest/registry identity mismatch')
  if actual['worktree'] in {wd(live(i['parentSessionId'])),str(Path.cwd().resolve())}: raise SystemExit('lane worktree cannot be parent or repository root')
  collisions(i,a.session)
  if v.get('state')=='admitted' and v.get('sessionId')!=a.session: raise SystemExit('admission already bound to another session')
  if v.get('state') not in {'reserved','admitted'}: raise SystemExit('admission is not confirmable')
  v.update(state='admitted',sessionId=a.session,admittedAt=common.now_ms(),manifestIdentityDigest=fp(actual));common.atomic_json(path(a.token),v)
 print(json.dumps({'ok':True,'admission':v},indent=2));return 0
def cmd_check(a):
 v=common.read_json(path(a.token));ok=bool(v and v.get('state')=='admitted' and v.get('sessionId')==a.session)
 print(json.dumps({'allowed':ok,'admission':v},indent=2));return 0 if ok else 4
def parser():
 p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True);r=s.add_parser('reserve');r.add_argument('--token');r.add_argument('--parent',required=True);r.add_argument('--role',required=True,choices=sorted(ROLES));r.add_argument('--work-unit',required=True);r.add_argument('--attempt',required=True);r.add_argument('--worktree',required=True);r.set_defaults(func=cmd_reserve);c=s.add_parser('confirm');c.add_argument('--token',required=True);c.add_argument('--session',required=True);c.set_defaults(func=cmd_confirm);k=s.add_parser('check');k.add_argument('--token',required=True);k.add_argument('--session',required=True);k.set_defaults(func=cmd_check);return p
if __name__=='__main__': a=parser().parse_args();raise SystemExit(a.func(a))

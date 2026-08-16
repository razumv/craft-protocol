#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Authenticated, byte-bound direct-owner plan receipts; never external authority."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, re
from pathlib import Path
from typing import Any
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("common",HERE/"orchestration-common.py");common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common) # type: ignore
ROOT=common.RUNTIME/"owner-plan-receipts";LOCK=common.RUNTIME/"owner-plan-receipts.lock";SCHEMA=2
ID=re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$");CRED=("authorization:","bearer ","token=","api_key=","apikey=","secret=","password=","-----begin")
SAFE={"documentation","investigation","local-repair","observation","test-only"};DANGEROUS={"deploy","irreversible-data-change","merge-protected-branch","physical-or-remote-access","publish-release","spend-money-or-entitlement","use-credential"}
def fail(x:str)->None:print(json.dumps({"ok":False,"error":x}));raise SystemExit(2)
def project(x:str)->str:
 x=re.sub(r"[^a-z0-9._-]+","-",x.strip().lower()).strip("-")
 if not x:fail("invalid project slug")
 return x
def scope(x:str)->str:
 if not isinstance(x,str) or not re.fullmatch(r"(?:increment|work-unit):[a-z0-9][a-z0-9._-]{0,95}",x):fail("scope must be exact increment:<id> or work-unit:<id>")
 return x
def path(p:str,r:str)->Path:return ROOT/p/f"{r}.json"
def load(p:str,r:str)->dict[str,Any]|None:
 x=common.read_json(path(p,r));return x if isinstance(x,dict) else None
def plan_bytes(raw:str)->tuple[str,int]:
 p=Path(raw).expanduser().resolve()
 try:b=p.read_bytes()
 except Exception:fail("plan file unreadable")
 if not b or len(b)>200_000 or any(marker.encode() in b.lower() for marker in CRED):fail("plan bytes are empty, oversized, or credential-like")
 return hashlib.sha256(b).hexdigest(),len(b)
def effects(values:list[str])->list[str]:
 x=sorted(set(values))
 if not x:fail("explicit safe effect is required")
 if set(x)&DANGEROUS:fail("dangerous plan effects are never authorized")
 if set(x)-SAFE:fail("unknown plan effect")
 return x
def excludes(values:list[str])->list[str]:
 x=sorted(set(values))
 if x!=sorted(DANGEROUS):fail("every dangerous effect must be explicitly excluded")
 return x
def owner(session:str)->dict[str,Any]:
 expected=os.environ.get("CRAFT_OWNER_SESSION_ID","")
 m=common.read_manifest(session)
 if not expected or session!=expected or not common.session_live(m) or "agent-role::owner" not in set((m or {}).get("labels") or []):fail("authenticated direct-owner session identity required")
 return m or {}
def active(x:dict[str,Any],now:int)->str|None:
 if x.get("state")!="approved":return "receipt-revoked" if x.get("state")=="revoked" else "receipt-state-invalid"
 return "receipt-expired" if not isinstance(x.get("expiresAt"),int) or x["expiresAt"]<=now else None
def reasons(p:str,r:str,sc:str,plan_file:str,effect_values:list[str])->list[str]:
 x=load(p,r);now=common.now_ms();out=[]
 digest,size=plan_bytes(plan_file);requested=effects(effect_values)
 if not x:return ["receipt-missing"]
 for condition,name in ((active(x,now),None),(x.get("project")!=p,"receipt-project-mismatch"),(x.get("scope")!=sc,"receipt-scope-mismatch"),(x.get("planSha256")!=digest or x.get("planBytes")!=size,"receipt-plan-bytes-mismatch"),(not set(requested).issubset(set(x.get("effects") or [])),"receipt-effect-mismatch"),(set(x.get("exclusions") or [])!=DANGEROUS,"receipt-exclusions-invalid")):
  if condition:out.append(condition if name is None else name)
 return [str(v) for v in out if v]
def cmd_approve(a:argparse.Namespace)->int:
 p=project(a.project);r=a.receipt_id
 if not ID.fullmatch(r):fail("invalid receipt id")
 owner(a.owner_session);sc=scope(a.scope);digest,size=plan_bytes(a.plan_file);fx=effects(a.effect);ex=excludes(a.exclude);ttl=int(a.ttl_seconds)
 if ttl<60 or ttl>604800:fail("ttl outside 60..604800")
 now=common.now_ms();x={"schemaVersion":SCHEMA,"receiptId":r,"project":p,"scope":sc,"planSha256":digest,"planBytes":size,"effects":fx,"exclusions":ex,"ownerSessionId":a.owner_session,"approvedAt":now,"expiresAt":now+ttl*1000,"state":"approved","revokedAt":None,"revokeReason":None}
 with common.file_lock(LOCK):
  old=load(p,r)
  if old and old!=x:fail("receipt id immutable")
  if not old:common.atomic_json(path(p,r),x)
 print(json.dumps({"ok":True,"idempotent":bool(old),"receipt":old or x},indent=2));return 0
def cmd_revoke(a:argparse.Namespace)->int:
 p=project(a.project);owner(a.owner_session);x=load(p,a.receipt_id)
 if not x:fail("receipt missing")
 if any(v in a.reason.lower() for v in CRED):fail("credential-like revoke reason")
 x.update(state="revoked",revokedAt=common.now_ms(),revokeReason=a.reason);common.atomic_json(path(p,a.receipt_id),x);print(json.dumps({"ok":True,"receipt":x},indent=2));return 0
def cmd_check(a:argparse.Namespace)->int:
 p=project(a.project);sc=scope(a.scope);out=reasons(p,a.receipt_id,sc,a.plan_file,a.effect);print(json.dumps({"ok":not out,"authorized":not out,"refusals":out},indent=2));return 0 if not out else 4
def parser()->argparse.ArgumentParser:
 p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest="command",required=True)
 for n,f in (("approve",cmd_approve),("revoke",cmd_revoke),("check",cmd_check)):
  q=s.add_parser(n);q.add_argument("--project",required=True);q.add_argument("--receipt-id",required=True);q.add_argument("--owner-session",required=n!="check")
  if n!="revoke":q.add_argument("--scope",required=True);q.add_argument("--plan-file",required=True);q.add_argument("--effect",action="append",default=[])
  if n=="approve":q.add_argument("--exclude",action="append",default=[]);q.add_argument("--ttl-seconds",default="86400")
  if n=="revoke":q.add_argument("--reason",required=True)
  q.set_defaults(func=f)
 return p
if __name__=="__main__":a=parser().parse_args();raise SystemExit(a.func(a))

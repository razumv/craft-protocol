#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Two-phase, fail-closed admission for disposable worker and auditor lanes.

Reserve immutable lane identity before spawning; confirm only the returned live
session whose manifest proves the exact reservation.  The resulting record is
additive to (and does not replace) legacy worker-lease records.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, re
from pathlib import Path
from typing import Any
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("common", HERE/"orchestration-common.py")
common=importlib.util.module_from_spec(spec); spec.loader.exec_module(common) # type: ignore
ROOT=common.RUNTIME/"lane-admissions"; LOCK=common.RUNTIME/"lane-admissions.lock"; SCHEMA=1
ROLES={"worker","auditor"}
def clean(s:str)->str:
    v=re.sub(r"[^A-Za-z0-9._-]+","-",s.strip()).strip("-")
    if not v: raise SystemExit("invalid identifier")
    return v
def path(token:str)->Path:return ROOT/f"{clean(token)}.json"
def label(row:dict[str,Any],prefix:str)->str|None:
    return next((x.split("::",1)[1] for x in row.get("labels") or [] if isinstance(x,str) and x.startswith(prefix)),None)
def manifest(sid:str)->dict[str,Any]:
    row=common.read_manifest(sid)
    if not row or row.get("id")!=sid or not common.session_live(row): raise SystemExit("live session manifest required")
    return row
def digest(row:dict[str,Any])->str:
    return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(",",":" )).encode()).hexdigest()
def cmd_reserve(a:argparse.Namespace)->int:
    if a.role not in ROLES: raise SystemExit("worker or auditor role required")
    wt=str(Path(a.worktree).expanduser().resolve())
    body={"parentSessionId":a.parent,"role":a.role,"workUnit":a.work_unit,"attempt":str(a.attempt),"worktree":wt}
    token=clean(a.token or hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()[:24])
    with common.file_lock(LOCK):
        old=common.read_json(path(token))
        if old:
            if old.get("identity")!=body: raise SystemExit("admission token identity mismatch")
            print(json.dumps({"ok":True,"idempotent":True,"admission":old},indent=2)); return 0
        parent=manifest(a.parent)
        if common.role_of(parent)!="coordinator": raise SystemExit("reservation parent is not a live coordinator")
        for p in ROOT.glob("*.json"):
            other=common.read_json(p) or {}
            if other.get("state") in {"reserved","admitted"} and other.get("identity",{}).get("worktree")==wt:
                raise SystemExit("worktree already has a live admission")
        value={"schemaVersion":SCHEMA,"token":token,"state":"reserved","identity":body,"identityDigest":digest(body),"reservedAt":common.now_ms(),"sessionId":None,"admittedAt":None}
        common.atomic_json(path(token),value)
    print(json.dumps({"ok":True,"admission":value},indent=2));return 0
def cmd_confirm(a:argparse.Namespace)->int:
    with common.file_lock(LOCK):
        value=common.read_json(path(a.token))
        if not value: raise SystemExit("admission reservation not found")
        row=manifest(a.session); identity=value.get("identity") or {}
        actual={"parentSessionId":label(row,"parent-session::") or row.get("parentSessionId"),"role":common.role_of(row),"workUnit":label(row,"work-unit::"),"attempt":label(row,"attempt::"),"worktree":str(Path(str(row.get("workingDirectory") or row.get("sdkCwd") or "")).expanduser().resolve())}
        if actual!=identity: raise SystemExit("admission manifest identity mismatch")
        if value.get("state")=="admitted":
            if value.get("sessionId")!=a.session: raise SystemExit("admission already bound to another session")
            print(json.dumps({"ok":True,"idempotent":True,"admission":value},indent=2));return 0
        if value.get("state")!="reserved": raise SystemExit("admission is not reservable")
        value.update({"state":"admitted","sessionId":a.session,"admittedAt":common.now_ms(),"manifestIdentityDigest":digest(actual)})
        common.atomic_json(path(a.token),value)
    print(json.dumps({"ok":True,"admission":value},indent=2));return 0
def cmd_check(a:argparse.Namespace)->int:
    value=common.read_json(path(a.token))
    ok=bool(value and value.get("state")=="admitted" and (not a.session or value.get("sessionId")==a.session))
    print(json.dumps({"allowed":ok,"admission":value},indent=2));return 0 if ok else 4
def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest="command",required=True)
    r=s.add_parser("reserve");r.add_argument("--token");r.add_argument("--parent",required=True);r.add_argument("--role",required=True,choices=sorted(ROLES));r.add_argument("--work-unit",required=True);r.add_argument("--attempt",required=True);r.add_argument("--worktree",required=True);r.set_defaults(func=cmd_reserve)
    c=s.add_parser("confirm");c.add_argument("--token",required=True);c.add_argument("--session",required=True);c.set_defaults(func=cmd_confirm)
    k=s.add_parser("check");k.add_argument("--token",required=True);k.add_argument("--session");k.set_defaults(func=cmd_check)
    return p
if __name__=="__main__":raise SystemExit(parser().parse_args().func(parser().parse_args()))

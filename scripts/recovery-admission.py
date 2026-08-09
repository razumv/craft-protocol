#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Deterministic pre-session admission for bounded recovery notifications.

The supervisor reads recovery incidents and atomically arms one exact-minute
SchedulerTick matcher. No LLM session exists before this admission succeeds.
It never edits session manifests/databases, sends messages, or kills processes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

HOME = Path.home()
WORKSPACE = Path(os.environ.get("CRAFT_WORKSPACE", HOME / ".craft-agent/workspaces/general")).expanduser()
RUNTIME = Path(os.environ.get("CRAFT_RUNTIME", HOME / ".craft-agent/runtime")).expanduser()
SESSIONS = Path(os.environ.get("CRAFT_SESSIONS", WORKSPACE / "sessions")).expanduser()
INCIDENTS = Path(os.environ.get("CRAFT_RECOVERY_INCIDENTS", RUNTIME / "recovery-incidents")).expanduser()
COORDINATORS = Path(os.environ.get("CRAFT_COORDINATORS", RUNTIME / "coordinators")).expanduser()
WORKER_LEASES = Path(os.environ.get("CRAFT_WORKER_LEASES", RUNTIME / "worker-leases")).expanduser()
CONFIG = Path(os.environ.get("CRAFT_AUTOMATIONS_CONFIG", WORKSPACE / "automations.json")).expanduser()
HISTORY = Path(os.environ.get("CRAFT_AUTOMATIONS_HISTORY", WORKSPACE / "automations-history.jsonl")).expanduser()
STATE = Path(os.environ.get("CRAFT_ADMISSION_STATE", RUNTIME / "self-healing/admission.json")).expanduser()
LOCK = Path(os.environ.get("CRAFT_ADMISSION_LOCK", RUNTIME / "self-healing/admission.lock")).expanduser()
DISABLED = Path(os.environ.get("CRAFT_SELF_HEALING_DISABLED", RUNTIME / "self-healing.disabled")).expanduser()
AUTOMATION_ID = os.environ.get("CRAFT_RECOVERY_NOTIFIER_AUTOMATION_ID", "a321-notifier")
CONTROLLER_HARNESS = Path(os.environ.get("CRAFT_CONTROLLER_HARNESS", Path(__file__).with_name("controller-harness.py"))).expanduser()
MAX_INCIDENTS = int(os.environ.get("CRAFT_RECOVERY_ADMISSION_MAX_INCIDENTS", "3"))
ARM_TTL_SECONDS = int(os.environ.get("CRAFT_RECOVERY_ARM_TTL_SECONDS", "180"))
COOLDOWN_SECONDS = int(os.environ.get("CRAFT_RECOVERY_ADMISSION_COOLDOWN_SECONDS", "900"))
# No supported Craft pre-fire claim exists in the current public interface.
# This production module has no environment or CLI override. Unit tests inject
# synthetic support in-process without exposing an installed-runtime bypass.
PREFIRE_CLAIM_SUPPORTED = False
NOW_MS = lambda: int(os.environ.get("CRAFT_TEST_NOW_MS", "0")) or int(time.time() * 1000)
BLOCKED_KINDS = {"owner-gate-blocked", "cwd-collision", "project-mapping-conflict", "ambiguous-coordinator-owner", "preservation-unknown"}
WAKE_KINDS = {"coordinator-lease-stale", "coordinator-session-error", "coordinator-pi-sigterm", "job-exit-unreported", "heavy-lock-wait"}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n"); fh.flush(); os.fsync(fh.fileno())
        os.replace(raw, path)
    finally:
        try: os.unlink(raw)
        except FileNotFoundError: pass


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def manifest(session_id: str) -> dict[str, Any] | None:
    path = SESSIONS / session_id / "session.jsonl"
    try: return json.loads(path.open(encoding="utf-8", errors="ignore").readline())
    except Exception: return None


def label_value(row: dict[str, Any], prefix: str) -> str | None:
    for label in row.get("labels") or []:
        if isinstance(label, str) and label.startswith(prefix): return label.split("::", 1)[1]
    return None


def require_persistent_controller(session_id: str) -> dict[str, Any]:
    row = manifest(session_id)
    if not row: raise ValueError("persistent controller manifest missing")
    if row.get("isArchived"): raise ValueError("persistent controller is archived")
    if row.get("sessionStatus") in {"done", "cancelled", "error"}: raise ValueError("persistent controller is terminal")
    if label_value(row, "agent-role::") != "recovery-controller": raise ValueError("session is not recovery-controller")
    if label_value(row, "controller-mode::") != "persistent": raise ValueError("controller is not marked persistent")
    try:
        cp=subprocess.run([str(CONTROLLER_HARNESS),"report"],text=True,capture_output=True,timeout=10)
        report=json.loads(cp.stdout); matches=[r for r in report.get("rows",[]) if r.get("sessionId")==session_id]
    except Exception as exc: raise ValueError(f"controller harness proof unavailable: {exc}")
    if cp.returncode or not report.get("healthy") or len(matches)!=1 or matches[0].get("state")!="active" or matches[0].get("sessionRole")!="recovery-controller":
        raise ValueError("persistent controller harness is not uniquely live/proven")
    return row


def live_scope_blocked(row: dict[str, Any], all_rows: list[dict[str, Any]]) -> bool:
    project=row.get("project"); session=row.get("sessionId"); work_unit=row.get("workUnit")
    registry=read_json(COORDINATORS/f"{project}.json") if project else None
    if registry and registry.get("state") in {"hold","needs-owner"}: return True
    for blocker in all_rows:
        if blocker.get("state") not in {"open","claimed","deferred"} or blocker.get("kind") not in BLOCKED_KINDS: continue
        same_scope=(session and blocker.get("sessionId")==session) or (project and blocker.get("project")==project)
        if same_scope: return True
    if project and work_unit:
        for path in (RUNTIME/"owner-gates"/str(project)).glob("*.json"):
            gate=read_json(path) or {}
            if gate.get("state")=="open" and str(gate.get("workUnit") or "")==str(work_unit): return True
    return False


def incidents() -> list[dict[str, Any]]:
    all_rows=[r for p in sorted(INCIDENTS.glob("*.json")) if (r:=read_json(p))]
    rows=[]
    for row in all_rows:
        if row.get("state") != "open": continue
        if row.get("kind") in BLOCKED_KINDS: continue
        if row.get("kind") not in WAKE_KINDS: continue
        if not row.get("sessionId") or live_scope_blocked(row,all_rows): continue
        rows.append(row)
    order={"critical":0,"high":1,"medium":2,"low":3,"info":4}
    rows.sort(key=lambda r:(order.get(str(r.get("severity")),9),int(r.get("firstSeenAt") or 0),str(r.get("incidentId"))))
    return rows[:MAX_INCIDENTS]


def fingerprint(rows: list[dict[str, Any]]) -> str:
    value=[{"incidentId":r.get("incidentId"),"evidenceFingerprint":r.get("evidenceFingerprint"),"state":r.get("state")} for r in rows]
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def coordinator_health(now: int) -> list[dict[str, Any]]:
    """Classify execution health without treating silence alone as completion."""
    out=[]
    for path in sorted(COORDINATORS.glob("*.json")):
        row=read_json(path) or {}; sid=str(row.get("coordinatorSessionId") or ""); man=manifest(sid) if sid else None
        heartbeat=int(row.get("lastHeartbeatAt") or 0); expiry=int(row.get("leaseExpiresAt") or 0)
        age=max(0,now-heartbeat) if heartbeat else None; children=[]
        for child in row.get("activeChildren") or []:
            lease=read_json(WORKER_LEASES/f"{child}.json") or {}
            child_hb=int(lease.get("lastHeartbeatAt") or 0)
            children.append({"sessionId":child,"state":lease.get("state"),"heartbeatAgeMs":max(0,now-child_hb) if child_hb else None})
        live_children=[c for c in children if c["state"] in {"active","starting","suspect"} and c["heartbeatAgeMs"] is not None and c["heartbeatAgeMs"] <= 900000]
        if not man or man.get("isArchived") or man.get("sessionStatus") in {"done","cancelled","error"}: health="failed"
        elif row.get("state")=="hold": health="idle-healthy"
        elif live_children: health="child-active"
        elif expiry and now <= expiry: health="active" if children else "idle-healthy"
        elif expiry and now-expiry <= 900000: health="suspect"
        else: health="stalled"
        out.append({"project":path.stem,"sessionId":sid,"health":health,"heartbeatAgeMs":age,
                    "leaseExpiredByMs":max(0,now-expiry) if expiry else None,"activeChildren":len(live_children),"registeredChildren":len(children)})
    return out


def history_rows() -> list[dict[str, Any]]:
    out=[]
    try:
        for line in HISTORY.read_text(encoding="utf-8",errors="ignore").splitlines():
            try:
                row=json.loads(line)
                if row.get("id") == AUTOMATION_ID: out.append(row)
            except Exception: pass
    except FileNotFoundError: pass
    return out


def load_config() -> dict[str, Any]:
    row=read_json(CONFIG)
    if not row or row.get("version") != 2 or not isinstance(row.get("automations"),dict):
        raise ValueError("automations.json missing or invalid")
    return row


def install_guard(args: argparse.Namespace) -> int:
    template=read_json(Path(args.template).expanduser())
    if not template: raise ValueError("automation template missing or invalid")
    candidates=[r for r in template.get("automations",{}).get("SchedulerTick",[]) if r.get("id")==AUTOMATION_ID]
    if len(candidates)!=1: raise ValueError("template must contain exactly one notifier")
    config=read_json(CONFIG) or {"version":2,"automations":{}}
    if config.get("version")!=2 or not isinstance(config.get("automations"),dict): raise ValueError("existing automations config invalid")
    sched=config["automations"].setdefault("SchedulerTick",[]); matches=[r for r in sched if r.get("id")==AUTOMATION_ID]
    if len(matches)>1: raise ValueError("duplicate recovery notifier automation id")
    if not matches: sched.insert(0,json.loads(json.dumps(candidates[0])))
    for rows in config["automations"].values():
        if not isinstance(rows,list): continue
        for row in rows:
            if isinstance(row,dict) and row.get("id") in {AUTOMATION_ID,"a31101","a31102"}: row["enabled"]=False
    if args.apply: atomic_json(CONFIG,config)
    print(json.dumps({"schemaVersion":1,"applied":args.apply,"notifierCount":1,"legacyDisabled":True},indent=2)); return 0


def set_matcher(*,enabled: bool, cron: str | None=None, prompt: str | None=None) -> None:
    config=load_config(); sched=config["automations"].setdefault("SchedulerTick",[])
    found=None
    for row in sched:
        if isinstance(row,dict) and row.get("id") == AUTOMATION_ID:
            if found is not None: raise ValueError("duplicate recovery notifier automation id")
            found=row
    if found is None: raise ValueError("recovery notifier automation missing")
    found["enabled"]=enabled
    if cron is not None: found["cron"]=cron
    if prompt is not None:
        actions=found.get("actions") or []
        if len(actions)!=1 or actions[0].get("type")!="prompt": raise ValueError("notifier must have exactly one prompt action")
        actions[0]["prompt"]=prompt
    atomic_json(CONFIG,config)


def next_minute(now_ms: int) -> dt.datetime:
    now=dt.datetime.fromtimestamp(now_ms/1000,dt.timezone.utc)
    return (now+dt.timedelta(minutes=1)).replace(second=0,microsecond=0)


def reconcile_state(state: dict[str, Any] | None, now: int, apply: bool) -> tuple[dict[str, Any] | None,list[str]]:
    events=[]
    if state and state.get("phase") == "prepared":
        if apply: set_matcher(enabled=False)
        state.update(phase="blocked",blockedAt=now,reason="incomplete-arm-transaction")
        return state,["incomplete-arm-disabled"]
    if not state or state.get("phase") not in {"armed","notified"}: return state,events
    runs=[r for r in history_rows() if int(r.get("ts") or 0) >= int(state.get("armedAt") or 0)]
    if len(runs)>1:
        if apply: set_matcher(enabled=False)
        state.update(phase="blocked",blockedAt=now,reason="duplicate-notifier-execution",executions=runs)
        events.append("duplicate-execution-blocked")
    elif len(runs)==1 and state.get("phase")=="armed":
        if apply: set_matcher(enabled=False)
        state.update(phase="notified",notifiedAt=int(runs[0].get("ts") or now),notifierSessionId=runs[0].get("sessionId"),execution=runs[0])
        events.append("execution-observed-disabled")
    elif now > int(state.get("armExpiresAt") or 0):
        if apply: set_matcher(enabled=False)
        state.update(phase="blocked",blockedAt=now,reason="armed-window-expired-without-execution")
        events.append("missed-execution-blocked")
    return state,events


def report(args: argparse.Namespace) -> int:
    now=NOW_MS(); state=read_json(STATE); state,events=reconcile_state(state,now,False); rows=incidents()
    health=coordinator_health(now)
    out={"schemaVersion":1,"mode":"report-only","disabled":DISABLED.exists(),"state":state or {"phase":"idle"},
         "actionableCount":len(rows),"actionableIncidentIds":[r.get("incidentId") for r in rows],"events":events,
         "coordinatorHealth":health,"healthSummary":{name:sum(1 for r in health if r["health"]==name) for name in ("active","child-active","idle-healthy","suspect","stalled","failed")}}
    print(json.dumps(out,indent=2)); return 0


def tick(args: argparse.Namespace) -> int:
    now=NOW_MS(); LOCK.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=read_json(STATE); state,events=reconcile_state(state,now,args.apply)
        if state and events and args.apply: atomic_json(STATE,state)
        if state and state.get("phase") in {"armed","notified","blocked"}:
            print(json.dumps({"schemaVersion":1,"applied":args.apply,"state":state,"events":events},indent=2)); return 0 if state.get("phase")!="blocked" else 2
        rows=incidents()
        if not rows:
            out={"schemaVersion":1,"applied":args.apply,"state":{"phase":"idle"},"events":events,"reason":"no-actionable-incidents"}
            print(json.dumps(out,indent=2)); return 0
        if DISABLED.exists() and not args.ignore_kill_switch:
            out={"schemaVersion":1,"applied":False,"state":{"phase":"idle"},"reason":"kill-switch-active","actionableCount":len(rows)}
            print(json.dumps(out,indent=2)); return 2
        if args.apply and not PREFIRE_CLAIM_SUPPORTED:
            out={"schemaVersion":1,"applied":False,"state":{"phase":"blocked"},"reason":"scheduler-prefire-claim-unsupported","actionableCount":len(rows)}
            print(json.dumps(out,indent=2)); return 2
        require_persistent_controller(args.controller_session)
        fp=fingerprint(rows)
        old=state or {}
        if old.get("lastFingerprint")==fp and now < int(old.get("cooldownUntil") or 0):
            print(json.dumps({"schemaVersion":1,"applied":False,"state":old,"reason":"fingerprint-cooldown"},indent=2)); return 0
        when=next_minute(now); cron=f"{when.minute} {when.hour} {when.day} {when.month} *"
        ids=[str(r["incidentId"]) for r in rows]
        prompt=("RECOVERY NOTIFIER v3.2.1. This is not a project controller. Get your exact session ID and FIRST run "
                "~/.craft-agent/scripts/controller-harness.py register --session <self>. If registration fails, stop without messaging. "
                f"Then send exactly one message to persistent recovery controller {args.controller_session} containing your session ID, admission fingerprint {fp}, and incident IDs {','.join(ids)}. "
                "Do not contact project coordinators, inspect projects, claim incidents, spawn sessions, or mutate project files. After delivery, set needs-review and stop. "
                "The persistent controller must archive your terminal session before exact guarded reap.")
        new={"schemaVersion":1,"phase":"armed","controllerSessionId":args.controller_session,"fingerprint":fp,"incidentIds":ids,
             "armedAt":now,"scheduledFor":int(when.timestamp()*1000),"armExpiresAt":int(when.timestamp()*1000)+ARM_TTL_SECONDS*1000,
             "historyBaseline":len(history_rows()),"lastFingerprint":fp,"cooldownUntil":now+COOLDOWN_SECONDS*1000}
        if args.apply:
            prepared={**new,"phase":"prepared","preparedAt":now}
            atomic_json(STATE,prepared)
            try:
                set_matcher(enabled=True,cron=cron,prompt=prompt)
                atomic_json(STATE,new)
            except Exception:
                try:set_matcher(enabled=False)
                finally:raise
        print(json.dumps({"schemaVersion":1,"applied":args.apply,"state":new,"events":events},indent=2)); return 0


def disarm(args: argparse.Namespace) -> int:
    if not DISABLED.exists() and not args.force: raise ValueError("kill switch is not active; refusing unforced disarm")
    now=NOW_MS(); LOCK.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=read_json(STATE) or {"schemaVersion":1,"phase":"idle"}
        if args.apply:
            set_matcher(enabled=False)
            if state.get("phase") in {"armed","prepared"}:
                state.update(phase="blocked",blockedAt=now,reason="kill-switch-disarm")
            atomic_json(STATE,state)
    print(json.dumps({"schemaVersion":1,"applied":args.apply,"state":state,"disabled":True},indent=2)); return 0


def reset(args: argparse.Namespace) -> int:
    state=read_json(STATE) or {"phase":"idle"}
    if state.get("phase") not in {"blocked","notified"} and not args.force: raise ValueError("reset allowed only from blocked/notified unless --force")
    if args.apply:
        set_matcher(enabled=False)
        state={"schemaVersion":1,"phase":"idle","resetAt":NOW_MS(),"previousPhase":state.get("phase"),
               "lastFingerprint":state.get("fingerprint") or state.get("lastFingerprint"),"cooldownUntil":state.get("cooldownUntil")}
        atomic_json(STATE,state)
    print(json.dumps({"schemaVersion":1,"applied":args.apply,"state":state},indent=2)); return 0


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest="command",required=True)
    q=sub.add_parser("report"); q.set_defaults(func=report)
    q=sub.add_parser("install-guard"); q.add_argument("--template",required=True); q.add_argument("--apply",action="store_true"); q.set_defaults(func=install_guard)
    q=sub.add_parser("disarm"); q.add_argument("--apply",action="store_true"); q.add_argument("--force",action="store_true"); q.set_defaults(func=disarm)
    q=sub.add_parser("tick"); q.add_argument("--controller-session",required=True); q.add_argument("--apply",action="store_true"); q.add_argument("--ignore-kill-switch",action="store_true",help=argparse.SUPPRESS); q.set_defaults(func=tick)
    q=sub.add_parser("reset"); q.add_argument("--apply",action="store_true"); q.add_argument("--force",action="store_true"); q.set_defaults(func=reset)
    args=p.parse_args()
    try:return args.func(args)
    except BlockingIOError:
        print(json.dumps({"error":"admission supervisor already running"},indent=2)); return 75
    except Exception as exc:
        print(json.dumps({"error":str(exc),"command":args.command},indent=2)); return 2

if __name__=="__main__": raise SystemExit(main())

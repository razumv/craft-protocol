#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Deterministic v3.1.1 recovery incident registry.

Observes manifests/runtime and writes atomic incidents. It never calls an LLM,
messages/spawns sessions, decides gates, archives sessions, or kills processes.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore
RUNTIME = common.RUNTIME
INCIDENTS = RUNTIME / "recovery-incidents"
SELF_HEALING = RUNTIME / "self-healing"
LOCK = RUNTIME / "recovery-incidents.lock"
CONTROLLER = SELF_HEALING / "controller.json"
DISABLED = RUNTIME / "self-healing.disabled"
SCHEMA = 1
TRANSFER_STUCK_SECONDS = int(os.environ.get("CRAFT_TRANSFER_STUCK_SECONDS", "1800"))
CLAIM_TTL_SECONDS = int(os.environ.get("CRAFT_RECOVERY_CLAIM_TTL_SECONDS", "900"))
MAX_ATTEMPTS = int(os.environ.get("CRAFT_RECOVERY_MAX_ATTEMPTS", "2"))
COORDINATOR_MAX_ATTEMPTS = int(os.environ.get("CRAFT_COORDINATOR_RECOVERY_MAX_ATTEMPTS", "3"))
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
ACTION_MATRIX = {
    "coordinator-not-live": ["owner-escalation"],
    "coordinator-lease-stale": ["wake-coordinator", "renew-request", "bridge-rotation-after-two-failures"],
    "coordinator-session-error": ["wake-coordinator", "preserve-snapshot", "bridge-rotation-on-attempt-3"],
    "coordinator-pi-sigterm": ["wake-coordinator", "renew-request", "preserve-snapshot", "bridge-rotation-on-attempt-3"],
    "fallback-ttl-expired": ["codex-repatriation"],
    "transfer-stuck": ["inspect-transfer", "wake-coordinator", "owner-escalation"],
    "worker-suspect": ["inspect-progress", "wake-coordinator"],
    "worker-stalled": ["inspect-progress", "preserve", "archive-reap-if-proven", "request-replacement"],
    "worker-error": ["inspect-error", "preserve", "archive-reap-if-proven", "request-replacement"],
    "terminal-handoff-unconsumed": ["verify-preservation", "archive-reap-if-proven", "release-slot"],
    "job-exit-unreported": ["inspect-receipt", "wake-coordinator"],
    "heavy-lock-wait": ["ack-receipt", "queue-after-lock", "wake-coordinator"],
    "cwd-collision": ["hard-refusal", "owner-escalation"],
    "project-mapping-conflict": ["hard-refusal", "owner-escalation"],
    "preservation-unknown": ["verify-preservation", "hard-refusal-until-proven"],
    "owner-gate-blocked": ["report-only"],
}

def now_ms(): return common.now_ms()
def fail(message): raise SystemExit(message)
def session_error(manifest):
    return bool(manifest and (manifest.get("sessionStatus") == "error" or manifest.get("lastError")))
def unresolved_pi_sigterms(session_id, after_ms):
    path = common.manifest_path(session_id)
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2); size = fh.tell(); fh.seek(max(0, size-524288))
            lines = fh.read().decode("utf-8", "ignore").splitlines()
    except Exception:
        return []
    timestamps = []
    for line in lines:
        if "Pi subprocess exited unexpectedly (signal SIGTERM)" not in line: continue
        try: row = json.loads(line)
        except Exception: continue
        ts = int(row.get("timestamp") or row.get("createdAt") or 0)
        if row.get("type") == "error" and ts > int(after_ms or 0): timestamps.append(ts)
    return sorted(set(timestamps))
def incident_id(key): return hashlib.sha256(key.encode()).hexdigest()[:20]
def incident_path(iid): return INCIDENTS / f"{iid}.json"
def read_incidents():
    INCIDENTS.mkdir(parents=True, exist_ok=True)
    return {p.stem: v for p in INCIDENTS.glob("*.json") if (v := common.read_json(p))}
def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
def path_within(child, parent):
    if not child or not parent: return False
    try:
        Path(child).expanduser().resolve().relative_to(Path(parent).expanduser().resolve()); return True
    except Exception: return False

def project_context():
    records = {p.stem: v for p in (RUNTIME / "coordinators").glob("*.json") if (v := common.read_json(p))}
    owners = {str(v.get("coordinatorSessionId")): p for p, v in records.items()}
    roots = {}
    for project, record in records.items():
        manifest = common.read_manifest(str(record.get("coordinatorSessionId") or "")) or {}
        cwd = common.expand_path(manifest.get("workingDirectory") or manifest.get("sdkCwd"))
        if cwd: roots[cwd] = project
    return records, owners, roots

def project_for_child(lease, manifest, owners, roots):
    parent = str(lease.get("parentSessionId") or "")
    if parent in owners: return owners[parent]
    explicit = common.label_value(manifest, "project::")
    if explicit: return explicit
    cwd = lease.get("worktree") or manifest.get("workingDirectory") or manifest.get("sdkCwd")
    for root, project in roots.items():
        if path_within(cwd, root): return project
    return None

def observation(kind, severity, project, session_id, evidence, **extra):
    key = ":".join((project or "global", kind, session_id or str(extra.get("gateId") or "none")))
    return {"stableKey": key, "incidentId": incident_id(key), "kind": kind,
            "severity": severity, "project": project, "sessionId": session_id,
            "evidence": evidence, "evidenceFingerprint": fingerprint(evidence),
            "allowedActions": ACTION_MATRIX.get(kind, ["report-only"]), **extra}

def collect_observations():
    now = now_ms(); records, owners, roots = project_context(); manifests = common.all_manifests(); out = []
    for project, record in records.items():
        sid = str(record.get("coordinatorSessionId") or ""); manifest = manifests.get(sid)
        if record.get("state") != "hold" and not common.session_live(manifest):
            out.append(observation("coordinator-not-live", "critical", project, sid,
                {"registryState": record.get("state"), "manifestPresent": bool(manifest), "archived": (manifest or {}).get("isArchived")}))
        expiry = record.get("leaseExpiresAt")
        if record.get("state") != "hold" and expiry is not None and now > int(expiry):
            out.append(observation("coordinator-lease-stale", "high", project, sid,
                {"leaseExpiresAt": int(expiry), "lastHeartbeatAt": record.get("lastHeartbeatAt"),
                 "agePastExpiryMs": now-int(expiry), "generation": record.get("generation")}))
        fallback = record.get("fallbackExpiresAt")
        if fallback is not None and now > int(fallback):
            out.append(observation("fallback-ttl-expired", "high", project, sid,
                {"connection": record.get("connection"), "model": record.get("model"), "fallbackExpiresAt": fallback}))
        if record.get("state") == "rotating":
            started = int(record.get("transferStartedAt") or 0)
            if started and now-started > TRANSFER_STUCK_SECONDS*1000:
                out.append(observation("transfer-stuck", "critical", project, sid,
                    {"transferStartedAt": started, "pendingSessionId": record.get("pendingSessionId"), "generation": record.get("generation")}))
        if manifest and session_error(manifest):
            out.append(observation("coordinator-session-error", "high", project, sid,
                {"updatedAt": manifest.get("updatedAt"), "errorClass": "manifest-terminal-error"}))
        sigterms = unresolved_pi_sigterms(sid, record.get("lastHeartbeatAt") or record.get("claimedAt") or 0)
        if sigterms:
            out.append(observation("coordinator-pi-sigterm", "high", project, sid,
                {"errorClass": "pi-sigterm", "countSinceHeartbeat": len(sigterms),
                 "firstAt": sigterms[0], "lastAt": sigterms[-1], "generation": record.get("generation")}))

    for p in (RUNTIME / "worker-leases").glob("*.json"):
        lease = common.read_json(p) or {}; sid = str(lease.get("sessionId") or p.stem)
        manifest = manifests.get(sid, {}); project = project_for_child(lease, manifest, owners, roots)
        state = str(lease.get("state") or ""); role = lease.get("role") or common.label_value(manifest, "agent-role::")
        explicit_project = common.label_value(manifest, "project::")
        parent_project = owners.get(str(lease.get("parentSessionId") or ""))
        evidence = {"state": state, "phase": lease.get("phase"), "lastHeartbeatAt": lease.get("lastHeartbeatAt"),
                    "preservationState": lease.get("preservationState"), "worktree": lease.get("worktree"), "role": role}
        kind = {"suspect": "worker-suspect", "stalled": "worker-stalled", "error": "worker-error",
                "handoff-ready": "terminal-handoff-unconsumed"}.get(state)
        if kind:
            out.append(observation(kind, "high" if state in {"stalled", "error"} else "medium", project, sid, evidence,
                                   coordinatorSessionId=lease.get("parentSessionId"), workUnit=lease.get("workUnit")))
        if explicit_project and parent_project and explicit_project != parent_project:
            out.append(observation("project-mapping-conflict", "critical", parent_project, sid,
                {"authoritativeParentProject": parent_project, "childLabelProject": explicit_project,
                 "parentSessionId": lease.get("parentSessionId"), "worktree": lease.get("worktree")}))
        if lease.get("cwdCollision"):
            out.append(observation("cwd-collision", "critical", project, sid, {**evidence, "cwdCollision": lease.get("cwdCollision")}))
        if state == "handoff-ready" and role != "auditor" and lease.get("preservationState") not in {"pushed", "merged"}:
            out.append(observation("preservation-unknown", "high", project, sid, evidence))

    for p in (RUNTIME / "worker-jobs").glob("*.json"):
        job = common.read_json(p) or {}; sid = str(job.get("sessionId") or p.stem)
        lease = common.read_json(RUNTIME / "worker-leases" / f"{sid}.json") or {}; manifest = manifests.get(sid, {})
        project = project_for_child(lease, manifest, owners, roots); code = job.get("exitCode")
        ev = {"exitCode": code, "jobId": job.get("jobId") or sid,
              "endedAt": job.get("finishedAt") or job.get("endedAt"),
              "reportedAt": job.get("reportedAt"), "logPath": job.get("logPath"), "heavy": job.get("heavy")}
        if code == 75:
            out.append(observation("heavy-lock-wait", "medium", project, sid, ev, coordinatorSessionId=lease.get("parentSessionId")))
        elif code is not None and not job.get("reportedAt"):
            out.append(observation("job-exit-unreported", "medium", project, sid, ev, coordinatorSessionId=lease.get("parentSessionId")))

    for p in (RUNTIME / "owner-gates").glob("*/*.json"):
        gate = common.read_json(p) or {}
        if gate.get("state") == "open":
            out.append(observation("owner-gate-blocked", "info", gate.get("project"), None,
                {"state": gate.get("state"), "action": gate.get("action"), "workUnit": gate.get("workUnit")}, gateId=gate.get("gateId") or p.stem))
    return out

def detect(apply=False):
    observed = collect_observations(); existing = read_incidents(); now = now_ms(); seen = set(); changed = []
    if not apply:
        return {"schemaVersion": SCHEMA, "apply": False, "disabled": DISABLED.exists(), "observations": observed}
    with common.file_lock(LOCK):
        existing = read_incidents()
        for obs in observed:
            iid = obs["incidentId"]; seen.add(iid); old = existing.get(iid); fp = obs["evidenceFingerprint"]
            if not old:
                initial_state = "suppressed" if obs.get("kind") == "owner-gate-blocked" else "open"
                row = {"schemaVersion": SCHEMA, **obs, "state": initial_state, "firstSeenAt": now, "lastSeenAt": now,
                       "claimOwner": None, "claimExpiresAt": None, "recoveryAttempts": 0,
                       "lastActionAt": None, "cooldownUntil": None, "resolutionEvidence": None, "history": []}
            else:
                row = {**old, **obs, "lastSeenAt": now}
                if row.get("state") == "claimed" and int(row.get("claimExpiresAt") or 0) <= now:
                    row.update(state="open", claimOwner=None, claimExpiresAt=None)
                    row.setdefault("history", []).append({"at": now, "action": "claim-expired"})
                if row.get("state") == "deferred" and int(row.get("cooldownUntil") or 0) <= now:
                    row.update(state="open", cooldownUntil=None)
                    row.setdefault("history", []).append({"at": now, "action": "cooldown-expired"})
                if row.get("state") == "resolved":
                    row.update(state="open", resolutionEvidence=None)
                    row.setdefault("history", []).append({"at": now, "action": "condition-reopened", "fingerprint": fp})
            common.atomic_json(incident_path(iid), row); changed.append(iid)
        for iid, row in existing.items():
            if iid in seen or row.get("state") == "resolved": continue
            row.update(state="resolved", lastSeenAt=now, claimOwner=None, claimExpiresAt=None,
                       resolutionEvidence={"kind": "condition-cleared", "at": now})
            row.setdefault("history", []).append({"at": now, "action": "condition-cleared"})
            common.atomic_json(incident_path(iid), row); changed.append(iid)
    rows = read_incidents()
    return {"schemaVersion": SCHEMA, "apply": True, "disabled": DISABLED.exists(),
            "observed": len(observed), "changed": sorted(set(changed)), "summary": summarize(rows.values())}

def summarize(rows):
    summary = {"total": 0, "open": 0, "claimed": 0, "deferred": 0, "resolved": 0, "escalated": 0, "suppressed": 0,
               "byKind": {}, "bySeverity": {}}
    for row in rows:
        summary["total"] += 1; state = row.get("state", "open"); summary[state] = summary.get(state, 0)+1
        summary["byKind"][row.get("kind")] = summary["byKind"].get(row.get("kind"), 0)+1
        summary["bySeverity"][row.get("severity")] = summary["bySeverity"].get(row.get("severity"), 0)+1
    return summary

def mutate(iid, fn):
    with common.file_lock(LOCK):
        row = common.read_json(incident_path(iid))
        if not row: fail(f"incident not found: {iid}")
        fn(row); common.atomic_json(incident_path(iid), row); return row

def claim_limit(row):
    return COORDINATOR_MAX_ATTEMPTS if row.get("kind") in {"coordinator-lease-stale", "coordinator-session-error", "coordinator-pi-sigterm"} else MAX_ATTEMPTS

def coordinator_incident(row):
    return row.get("kind") in {"coordinator-lease-stale", "coordinator-session-error", "coordinator-pi-sigterm"}

def claim_stage(row, attempt_number):
    if not coordinator_incident(row): return "recover"
    return {1: "wake-1", 2: "wake-2", 3: "rotation"}.get(attempt_number, "exhausted")

def claim_actions(row, stage):
    if stage in {"wake-1", "wake-2"}: return ["wake-coordinator", "renew-request", "preserve-snapshot"]
    if stage == "rotation": return ["preserve-snapshot", "bridge-rotation-on-attempt-3", "owner-escalation"]
    return row.get("allowedActions") or ["report-only"]

def require_claim(row, controller):
    if DISABLED.exists(): fail("self-healing kill switch is active")
    if row.get("state") != "claimed" or row.get("claimOwner") != controller:
        fail("incident claim owner/state mismatch")
    if int(row.get("claimExpiresAt") or 0) <= now_ms(): fail("incident claim expired")

def claim(args):
    now = now_ms()
    def action(row):
        if DISABLED.exists(): fail("self-healing kill switch is active")
        if row.get("state") == "claimed" and int(row.get("claimExpiresAt") or 0) > now and row.get("claimOwner") != args.controller:
            fail(f"incident already claimed by {row.get('claimOwner')}")
        if row.get("state") not in {"open", "deferred", "claimed"}: fail(f"incident is not claimable: {row.get('state')}")
        if int(row.get("cooldownUntil") or 0) > now: fail("incident cooldown is active")
        if int(row.get("recoveryAttempts") or 0) >= claim_limit(row):
            row.update(state="escalated", claimOwner=None, claimExpiresAt=None,
                       resolutionEvidence={"kind": "retry-budget-exhausted", "at": now})
            row.setdefault("history", []).append({"at": now, "action": "retry-budget-exhausted"})
            return
        attempt_number = int(row.get("recoveryAttempts") or 0)+1
        stage = claim_stage(row, attempt_number)
        row.update(state="claimed", claimOwner=args.controller, claimExpiresAt=now+args.ttl*1000,
                   recoveryAttempts=attempt_number, lastActionAt=now, claimStage=stage,
                   claimAllowedActions=claim_actions(row, stage))
        row.setdefault("history", []).append({"at": now, "action": "claimed", "controller": args.controller,
                                              "attempt": attempt_number, "stage": stage})
    return mutate(args.incident, action)

def heartbeat(args):
    now = now_ms()
    def action(row):
        require_claim(row, args.controller)
        row["claimExpiresAt"] = now+args.ttl*1000
    return mutate(args.incident, action)

def resolve(args):
    now = now_ms()
    def action(row):
        require_claim(row, args.controller)
        row.update(state="resolved", claimOwner=None, claimExpiresAt=None, cooldownUntil=None,
                   resolutionEvidence={"kind": args.evidence_kind, "detail": args.evidence, "at": now})
        row.setdefault("history", []).append({"at": now, "action": "resolved", "evidence": args.evidence})
    return mutate(args.incident, action)

def defer(args):
    now = now_ms()
    def action(row):
        require_claim(row, args.controller)
        row.update(state="deferred", claimOwner=None, claimExpiresAt=None, cooldownUntil=now+args.cooldown*1000,
                   resolutionEvidence={"kind": "deferred", "detail": args.reason, "at": now})
        row.setdefault("history", []).append({"at": now, "action": "deferred", "reason": args.reason})
    return mutate(args.incident, action)

def escalate(args):
    now = now_ms()
    def action(row):
        require_claim(row, args.controller)
        row.update(state="escalated", claimOwner=None, claimExpiresAt=None,
                   resolutionEvidence={"kind": "owner-escalation", "detail": args.reason, "at": now})
        row.setdefault("history", []).append({"at": now, "action": "escalated", "reason": args.reason})
    return mutate(args.incident, action)

def controller_claim(args):
    now = now_ms(); SELF_HEALING.mkdir(parents=True, exist_ok=True)
    with common.file_lock(LOCK):
        if DISABLED.exists(): fail("self-healing kill switch is active")
        row = common.read_json(CONTROLLER) or {}
        if row.get("sessionId") != args.session and int(row.get("leaseExpiresAt") or 0) > now:
            fail(f"controller already active: {row.get('sessionId')}")
        row = {"schemaVersion": SCHEMA, "sessionId": args.session, "claimedAt": now,
               "lastHeartbeatAt": now, "leaseExpiresAt": now+args.ttl*1000}
        common.atomic_json(CONTROLLER, row); return row

def controller_heartbeat(args):
    now = now_ms()
    with common.file_lock(LOCK):
        if DISABLED.exists(): fail("self-healing kill switch is active")
        row = common.read_json(CONTROLLER) or {}
        if row.get("sessionId") != args.session: fail("controller owner mismatch")
        if int(row.get("leaseExpiresAt") or 0) <= now: fail("controller lease expired")
        row.update(lastHeartbeatAt=now, leaseExpiresAt=now+args.ttl*1000)
        common.atomic_json(CONTROLLER, row)
    return row

def controller_release(args):
    with common.file_lock(LOCK):
        row = common.read_json(CONTROLLER) or {}
        if row.get("sessionId") != args.session: fail("controller owner mismatch")
        CONTROLLER.unlink(missing_ok=True)
    return {"released": True, "sessionId": args.session}

def list_rows(args):
    rows = list(read_incidents().values())
    if args.state: rows = [r for r in rows if r.get("state") == args.state]
    if args.project: rows = [r for r in rows if r.get("project") == args.project]
    if args.kind: rows = [r for r in rows if r.get("kind") == args.kind]
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r.get("severity"), 9), r.get("firstSeenAt", 0)))
    return {"count": len(rows), "incidents": rows}

def parser():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd", required=True)
    d=sub.add_parser("detect"); d.add_argument("--apply", action="store_true")
    l=sub.add_parser("list"); l.add_argument("--state"); l.add_argument("--project"); l.add_argument("--kind")
    sub.add_parser("report")
    c=sub.add_parser("claim"); c.add_argument("--incident", required=True); c.add_argument("--controller", required=True); c.add_argument("--ttl", type=int, default=CLAIM_TTL_SECONDS)
    h=sub.add_parser("heartbeat"); h.add_argument("--incident", required=True); h.add_argument("--controller", required=True); h.add_argument("--ttl", type=int, default=CLAIM_TTL_SECONDS)
    r=sub.add_parser("resolve"); r.add_argument("--incident", required=True); r.add_argument("--controller", required=True); r.add_argument("--evidence-kind", required=True); r.add_argument("--evidence", required=True)
    f=sub.add_parser("defer"); f.add_argument("--incident", required=True); f.add_argument("--controller", required=True); f.add_argument("--reason", required=True); f.add_argument("--cooldown", type=int, default=900)
    e=sub.add_parser("escalate"); e.add_argument("--incident", required=True); e.add_argument("--controller", required=True); e.add_argument("--reason", required=True)
    cc=sub.add_parser("controller-claim"); cc.add_argument("--session", required=True); cc.add_argument("--ttl", type=int, default=CLAIM_TTL_SECONDS)
    ch=sub.add_parser("controller-heartbeat"); ch.add_argument("--session", required=True); ch.add_argument("--ttl", type=int, default=CLAIM_TTL_SECONDS)
    cr=sub.add_parser("controller-release"); cr.add_argument("--session", required=True)
    return p

def main():
    args=parser().parse_args()
    if args.cmd == "detect": result=detect(args.apply)
    elif args.cmd == "list": result=list_rows(args)
    elif args.cmd == "report": result={"disabled": DISABLED.exists(), "controller": common.read_json(CONTROLLER), "summary": summarize(read_incidents().values())}
    elif args.cmd == "claim": result=claim(args)
    elif args.cmd == "heartbeat": result=heartbeat(args)
    elif args.cmd == "resolve": result=resolve(args)
    elif args.cmd == "defer": result=defer(args)
    elif args.cmd == "escalate": result=escalate(args)
    elif args.cmd == "controller-claim": result=controller_claim(args)
    elif args.cmd == "controller-heartbeat": result=controller_heartbeat(args)
    elif args.cmd == "controller-release": result=controller_release(args)
    print(json.dumps(result, indent=2, sort_keys=True))
if __name__ == "__main__": main()

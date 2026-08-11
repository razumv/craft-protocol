#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Observer-bound coordinator commitments (Protocol v3.3.0).

A coordinator may not end a turn with a prose-only future promise ("I will check
CI later"). Every future-tense wait must bind to a durable observer:

- an exact worker/auditor lease;
- an existing external-wait observer;
- an owner gate;
- a bounded scheduled review time.

Each commitment records its project, exact coordinator generation, subject, deadline
or next-check time, success action, timeout/failure action, and evidence revision.
Overdue, unobserved, missing-reference, and terminal commitments emit deterministic
incidents that wake the exact coordinator generation. Resolution requires durable
evidence, not prose. This tool never mutates session JSONL, leases, waits, or gates.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore

RUNTIME = common.RUNTIME
COMMITMENTS = RUNTIME / "coordinator-commitments"
LOCK = RUNTIME / "coordinator-commitments.lock"
COORDINATORS = RUNTIME / "coordinators"
LEASES = RUNTIME / "worker-leases"
WAITS = RUNTIME / "external-waits"
GATES = RUNTIME / "owner-gates"
SCHEMA = 1

BINDINGS = {"worker-lease", "external-wait", "owner-gate", "scheduled-review"}
ACTIVE_LEASE_STATES = {"starting", "running", "suspect", "active"}
OBSERVED_WAIT_STATES = {"observing", "terminal"}
ACTIVE_STATES = {"registered", "observing", "overdue", "ready"}
RESOLVED_STATES = {"resolved-success", "resolved-timeout", "resolved-failed", "cancelled"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
CRED_MARKERS = ("authorization:", "bearer ", "token=", "api_key=", "apikey=", "secret=", "password=")
TEXT_LIMIT = 500


def fail(message: str) -> None:
    raise SystemExit(message)


def clean_project(raw: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-")
    if not value:
        fail("invalid project slug")
    return value


def valid_id(value: str, label: str) -> str:
    if not value or not SAFE_ID.fullmatch(value):
        fail(f"invalid {label}")
    return value


def valid_text(value: str, label: str, limit: int = TEXT_LIMIT) -> str:
    if value is None or not value or len(value) > limit or any(ord(ch) < 32 and ch not in "\t" for ch in value):
        fail(f"invalid {label}")
    if any(marker in value.lower() for marker in CRED_MARKERS):
        fail(f"{label} may not contain credentials")
    return value


def commitment_path(project: str, commitment_id: str) -> Path:
    return COMMITMENTS / project / f"{valid_id(commitment_id, 'commitment id')}.json"


def project_commitments(project: str) -> list[dict[str, Any]]:
    return [c for path in sorted((COMMITMENTS / project).glob("*.json")) if (c := common.read_json(path))]


def authoritative_coordinator(project: str, session: str, generation: int) -> dict[str, Any]:
    row = common.read_json(COORDINATORS / f"{project}.json")
    if not row or row.get("state") != "authoritative":
        fail("no authoritative coordinator for project")
    if row.get("coordinatorSessionId") != session:
        fail("coordinator session mismatch")
    if int(row.get("generation") or -1) != generation:
        fail("stale coordinator generation may not register commitments")
    manifest = common.read_manifest(session)
    if not common.session_live(manifest) or common.role_of(manifest) != "coordinator":
        fail("coordinator session is not live")
    return row


def binding_state(project: str, coordinator: str, kind: str, ref: str | None, now: int) -> dict[str, Any]:
    """Return {observed: bool, terminal: bool, present: bool, detail: str} for a binding."""
    if kind == "worker-lease":
        lease = common.read_json(LEASES / f"{ref}.json") if ref else None
        if not lease or lease.get("parentSessionId") != coordinator:
            return {"present": False, "observed": False, "terminal": False, "detail": "lease-missing-or-unbound"}
        if lease.get("state") == "handoff-ready":
            return {"present": True, "observed": True, "terminal": True, "detail": "lease-terminal-handoff"}
        observed = lease.get("state") in ACTIVE_LEASE_STATES
        return {"present": True, "observed": observed, "terminal": False,
                "detail": f"lease-{lease.get('state')}"}
    if kind == "external-wait":
        wait = common.read_json(WAITS / f"{ref}.json") if ref else None
        if not wait or wait.get("coordinatorSessionId") != coordinator or wait.get("project") != project:
            return {"present": False, "observed": False, "terminal": False, "detail": "wait-missing-or-unbound"}
        state = wait.get("state")
        return {"present": True, "observed": state in OBSERVED_WAIT_STATES,
                "terminal": state in {"terminal", "deadline", "cleared"}, "detail": f"wait-{state}"}
    if kind == "owner-gate":
        gate = common.read_json(GATES / project / f"{ref}.json") if ref else None
        if not gate:
            return {"present": False, "observed": False, "terminal": False, "detail": "gate-missing"}
        resolved = gate.get("state") == "resolved"
        return {"present": True, "observed": gate.get("state") == "open", "terminal": resolved,
                "detail": f"gate-{gate.get('state')}"}
    # scheduled-review: the observer is the clock itself.
    return {"present": True, "observed": True, "terminal": False, "detail": "scheduled-review"}


def cmd_register(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    commitment_id = valid_id(args.commitment_id, "commitment id")
    subject = valid_text(args.subject, "subject")
    success_action = valid_text(args.success_action, "success action")
    failure_action = valid_text(args.failure_action, "failure action")
    if args.binding_kind not in BINDINGS:
        fail("unsupported binding kind")
    now = common.now_ms()
    if args.deadline_seconds is None:
        fail("--deadline-seconds is required")
    if args.deadline_seconds < 60 or args.deadline_seconds > 604800:
        fail("deadline must be between 60 and 604800 seconds")
    deadline_at = now + args.deadline_seconds * 1000
    ref = valid_id(args.ref, "binding ref") if args.ref else None
    if args.binding_kind != "scheduled-review" and not ref:
        fail(f"{args.binding_kind} binding requires --ref")

    with common.file_lock(LOCK):
        authoritative_coordinator(project, args.session, args.generation)
        binding = binding_state(project, args.session, args.binding_kind, ref, now)
        if not binding["present"]:
            fail(f"binding reference is not observable: {binding['detail']}")
        if args.binding_kind != "scheduled-review" and not binding["observed"] and not binding["terminal"]:
            fail(f"binding observer is not active: {binding['detail']}")
        path = commitment_path(project, commitment_id)
        existing = common.read_json(path)
        if existing and existing.get("state") not in RESOLVED_STATES:
            if (existing.get("bindingKind") == args.binding_kind and existing.get("ref") == ref
                    and existing.get("generation") == args.generation):
                print(json.dumps({"applied": args.apply, "idempotent": True, "commitment": existing},
                                 ensure_ascii=False, indent=2))
                return 0
            fail("active commitment id already exists with a different binding")
        record = {
            "schemaVersion": SCHEMA, "project": project, "commitmentId": commitment_id,
            "coordinatorSessionId": args.session, "generation": args.generation,
            "subject": subject, "bindingKind": args.binding_kind, "ref": ref,
            "deadlineAt": deadline_at, "successAction": success_action, "failureAction": failure_action,
            "state": "observing" if args.binding_kind != "scheduled-review" else "registered",
            "evidenceRevision": 1, "registeredAt": now, "updatedAt": now,
            "lastObservedDetail": binding["detail"], "resolvedAt": None, "resolutionEvidence": None,
        }
        if args.apply:
            common.atomic_json(path, record)
    print(json.dumps({"applied": args.apply, "commitment": record}, ensure_ascii=False, indent=2))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    evidence = valid_text(args.evidence, "resolution evidence")
    if args.resolution not in {"success", "timeout", "failed", "cancelled"}:
        fail("unsupported resolution")
    now = common.now_ms()
    with common.file_lock(LOCK):
        authoritative_coordinator(project, args.session, args.generation)
        path = commitment_path(project, args.commitment_id)
        record = common.read_json(path)
        if not record:
            fail("commitment not found")
        target_state = f"resolved-{args.resolution}" if args.resolution != "cancelled" else "cancelled"
        if record.get("state") in RESOLVED_STATES:
            if record.get("state") == target_state:
                print(json.dumps({"applied": args.apply, "idempotent": True, "commitment": record},
                                 ensure_ascii=False, indent=2))
                return 0
            fail("commitment already resolved differently")
        if int(record.get("generation") or -1) != args.generation:
            fail("commitment generation mismatch")
        binding = binding_state(project, args.session, record.get("bindingKind"), record.get("ref"), now)
        # Durable evidence, not prose: success/failure must be backed by an observer
        # terminal (or, for a scheduled review, the review time must have arrived).
        if args.resolution in {"success", "failed"}:
            if record.get("bindingKind") == "scheduled-review":
                if now < int(record.get("deadlineAt") or 0):
                    fail("scheduled review cannot be resolved before its next-check time")
            elif not binding["terminal"]:
                fail(f"resolution requires a terminal observer receipt, not prose: {binding['detail']}")
        record.update({"state": target_state, "resolvedAt": now, "updatedAt": now,
                       "resolutionEvidence": evidence, "lastObservedDetail": binding["detail"],
                       "evidenceRevision": int(record.get("evidenceRevision") or 1) + 1})
        if args.apply:
            common.atomic_json(path, record)
    print(json.dumps({"applied": args.apply, "commitment": record}, ensure_ascii=False, indent=2))
    return 0


def reconcile_one(record: dict[str, Any], now: int) -> tuple[dict[str, Any], str | None]:
    """Return (updated_record, incident_reason_or_None)."""
    if record.get("state") in RESOLVED_STATES:
        return record, None
    project = str(record.get("project") or "")
    coordinator = str(record.get("coordinatorSessionId") or "")
    reg = common.read_json(COORDINATORS / f"{project}.json")
    reg_generation = int(reg.get("generation") or -1) if reg else None
    updated = dict(record)
    binding = binding_state(project, coordinator, record.get("bindingKind"), record.get("ref"), now)
    reason: str | None = None
    if reg is None or reg.get("state") != "authoritative" or reg_generation != int(record.get("generation") or -1):
        # Superseded/absent generation: leave the record but do not raise an incident
        # against a non-authoritative target.
        updated["state"] = "orphaned"
        updated["lastObservedDetail"] = "coordinator-generation-superseded"
    elif not binding["present"]:
        updated["state"] = "missing-reference"; updated["lastObservedDetail"] = binding["detail"]
        reason = "missing-reference"
    elif binding["terminal"]:
        updated["state"] = "ready"; updated["lastObservedDetail"] = binding["detail"]
        reason = "observer-terminal"
    elif now >= int(record.get("deadlineAt") or 0):
        updated["state"] = "overdue"; updated["lastObservedDetail"] = binding["detail"]
        reason = "deadline-overdue"
    elif record.get("bindingKind") != "scheduled-review" and not binding["observed"]:
        updated["state"] = "unobserved"; updated["lastObservedDetail"] = binding["detail"]
        reason = "unobserved"
    else:
        updated["state"] = "registered" if record.get("bindingKind") == "scheduled-review" else "observing"
        updated["lastObservedDetail"] = binding["detail"]
    updated["updatedAt"] = now
    return updated, reason


def cmd_reconcile(args: argparse.Namespace) -> int:
    now = common.now_ms()
    actions: list[dict[str, Any]] = []
    with common.file_lock(LOCK):
        for project_dir in sorted(COMMITMENTS.glob("*")):
            if not project_dir.is_dir():
                continue
            for path in sorted(project_dir.glob("*.json")):
                record = common.read_json(path)
                if not record:
                    continue
                updated, reason = reconcile_one(record, now)
                if updated != record:
                    actions.append({"project": record.get("project"), "commitmentId": record.get("commitmentId"),
                                    "from": record.get("state"), "to": updated.get("state"), "reason": reason})
                    if args.apply:
                        common.atomic_json(path, updated)
    print(json.dumps({"applied": args.apply, "actions": actions}, ensure_ascii=False, indent=2))
    return 0


def overdue_observations(now: int) -> list[dict[str, Any]]:
    """Deterministic incident rows for recovery-incident consumption. Only commitments
    bound to a current authoritative generation are reported so wakes stay fenced."""
    out: list[dict[str, Any]] = []
    COMMITMENTS.mkdir(parents=True, exist_ok=True)
    for project_dir in sorted(COMMITMENTS.glob("*")):
        if not project_dir.is_dir():
            continue
        project = project_dir.name
        reg = common.read_json(COORDINATORS / f"{project}.json")
        if not reg or reg.get("state") != "authoritative":
            continue
        coordinator = str(reg.get("coordinatorSessionId") or "")
        reg_generation = int(reg.get("generation") or -1)
        manifest = common.read_manifest(coordinator)
        if not common.session_live(manifest) or common.role_of(manifest or {}) != "coordinator":
            continue
        for path in sorted(project_dir.glob("*.json")):
            record = common.read_json(path)
            if not record or int(record.get("generation") or -1) != reg_generation:
                continue
            updated, reason = reconcile_one(record, now)
            if reason:
                out.append({"project": project, "sessionId": coordinator, "generation": reg_generation,
                            "commitmentId": record.get("commitmentId"), "reason": reason,
                            "evidence": {"generation": reg_generation, "commitmentId": record.get("commitmentId"),
                                         "bindingKind": record.get("bindingKind"), "reason": reason}})
    return out


def cmd_list(args: argparse.Namespace) -> int:
    projects = [clean_project(args.project)] if args.project else sorted({p.name for p in COMMITMENTS.glob("*") if p.is_dir()})
    rows: list[dict[str, Any]] = []
    for project in projects:
        for record in project_commitments(project):
            if args.state and record.get("state") != args.state:
                continue
            rows.append(record)
    rows.sort(key=lambda r: (str(r.get("project")), str(r.get("commitmentId"))))
    print(json.dumps({"count": len(rows), "commitments": rows}, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    rg = sub.add_parser("register")
    rg.add_argument("--project", required=True); rg.add_argument("--session", required=True)
    rg.add_argument("--generation", type=int, required=True); rg.add_argument("--commitment-id", required=True)
    rg.add_argument("--subject", required=True); rg.add_argument("--binding-kind", required=True)
    rg.add_argument("--ref"); rg.add_argument("--deadline-seconds", type=int, required=True)
    rg.add_argument("--success-action", required=True); rg.add_argument("--failure-action", required=True)
    rg.add_argument("--apply", action="store_true"); rg.set_defaults(func=cmd_register)

    rs = sub.add_parser("resolve")
    rs.add_argument("--project", required=True); rs.add_argument("--session", required=True)
    rs.add_argument("--generation", type=int, required=True); rs.add_argument("--commitment-id", required=True)
    rs.add_argument("--resolution", required=True); rs.add_argument("--evidence", required=True)
    rs.add_argument("--apply", action="store_true"); rs.set_defaults(func=cmd_resolve)

    rc = sub.add_parser("reconcile"); rc.add_argument("--apply", action="store_true"); rc.set_defaults(func=cmd_reconcile)
    ls = sub.add_parser("list"); ls.add_argument("--project"); ls.add_argument("--state"); ls.set_defaults(func=cmd_list)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))

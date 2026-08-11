#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Durable product-status snapshot for autonomous coordinators (Protocol v3.3.0).

A coordinator publishes a declarative product-status snapshot: objective, current
phase, completed outcomes, current focus, up to three ordered next actions (each
with a trigger, required evidence, and success/failure branch), and its next review
time. Everything else in a report — coordinator lease health, active/terminal worker
leases, external waits, owner gates, inbox pressure, latest immutable candidate/audit
evidence, freshness, and contradictions — is synthesized independently from runtime
state and cannot be caller-invented.

Publishing fails closed on a stale coordinator generation, invented child/wait/gate
references, malformed next actions, secret-like content, unbounded fields, or a
`waiting` phase without a durable observable commitment. Stale status never renews
authority. This tool never mutates session JSONL, leases, the registry, or gates.
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
STATUS = RUNTIME / "coordinator-status"
LOCK = RUNTIME / "coordinator-status.lock"
COORDINATORS = RUNTIME / "coordinators"
LEASES = RUNTIME / "worker-leases"
WAITS = RUNTIME / "external-waits"
GATES = RUNTIME / "owner-gates"
INBOX = RUNTIME / "coordinator-inbox"
COMMITMENTS = RUNTIME / "coordinator-commitments"
SCHEMA = 1

PHASES = {"initializing", "executing", "waiting", "blocked", "review", "complete", "hold"}
TERMINAL_PHASES = {"complete", "hold", "blocked"}
ACTIVE_LEASE_STATES = {"starting", "running", "suspect", "active"}
ACTIVE_COMMITMENT_STATES = {"registered", "observing", "overdue", "ready"}
CLASSIFICATIONS = {"verified", "executing", "waiting-observed", "blocked", "stale", "contradictory"}
CRED_MARKERS = ("authorization:", "bearer ", "token=", "api_key=", "apikey=", "secret=", "password=", "-----begin")
MAX_ACTIONS = 3
TEXT_LIMIT = 800
LIST_LIMIT = 32
STALE_REVIEW_GRACE_SECONDS = int(os.environ.get("CRAFT_STATUS_REVIEW_GRACE_SECONDS", "900"))


def fail(message: str) -> None:
    raise SystemExit(message)


def clean_project(raw: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-")
    if not value:
        fail("invalid project slug")
    return value


def scan_secret(value: Any, label: str = "field") -> None:
    if isinstance(value, str):
        if len(value) > TEXT_LIMIT:
            fail(f"{label} exceeds {TEXT_LIMIT} characters")
        lowered = value.lower()
        if any(marker in lowered for marker in CRED_MARKERS):
            fail(f"{label} may not contain credentials")
    elif isinstance(value, list):
        if len(value) > LIST_LIMIT:
            fail(f"{label} list is unbounded")
        for item in value:
            scan_secret(item, label)
    elif isinstance(value, dict):
        for key, item in value.items():
            scan_secret(item, f"{label}.{key}")


def req_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 and c not in "\t\n" for c in value):
        fail(f"missing or invalid {key}")
    return value


# --------------------------------------------------------------- runtime readers

def registry(project: str) -> dict[str, Any] | None:
    return common.read_json(COORDINATORS / f"{project}.json")


def authoritative(project: str) -> dict[str, Any] | None:
    row = registry(project)
    if not row or row.get("state") not in {"authoritative", "hold"}:
        return None
    return row


def project_leases(coordinator: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(LEASES.glob("*.json")):
        lease = common.read_json(path)
        if lease and lease.get("parentSessionId") == coordinator:
            rows.append(lease)
    return rows


def project_waits(project: str, coordinator: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(WAITS.glob("*.json")):
        wait = common.read_json(path)
        if wait and wait.get("project") == project and wait.get("coordinatorSessionId") == coordinator:
            rows.append(wait)
    return rows


def project_gates(project: str) -> list[dict[str, Any]]:
    return [g for path in sorted((GATES / project).glob("*.json")) if (g := common.read_json(path))]


def project_commitments(project: str) -> list[dict[str, Any]]:
    return [c for path in sorted((COMMITMENTS / project).glob("*.json")) if (c := common.read_json(path))]


def inbox_pressure(project: str, generation: int | None, now: int) -> dict[str, Any]:
    pending = claimed = waking = 0
    for path in sorted((INBOX / project).glob("*.json")):
        item = common.read_json(path)
        if not item:
            continue
        if generation is not None and int(item.get("coordinatorGeneration") or -1) != generation:
            continue
        available = item.get("state") == "pending" or (
            item.get("state") == "claimed" and int(item.get("claimExpiresAt") or 0) <= now)
        if available:
            pending += 1
            if item.get("waking"):
                waking += 1
        elif item.get("state") == "claimed":
            claimed += 1
    return {"pending": pending, "claimed": claimed, "wakingPending": waking}


def latest_evidence(coordinator: str, project: str, generation: int | None) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for path in sorted((INBOX / project).glob("*.json")):
        item = common.read_json(path)
        if not item or item.get("kind") not in {"candidate", "audit-verdict", "terminal-handoff"}:
            continue
        if generation is not None and int(item.get("coordinatorGeneration") or -1) != generation:
            continue
        ts = int(item.get("updatedAt") or 0)
        if best is None or ts > int(best.get("at") or 0):
            best = {"kind": item.get("kind"), "workUnit": item.get("workUnit"),
                    "subject": item.get("subject"), "evidence": item.get("evidence"), "at": ts}
    return best


# ------------------------------------------------------------------- synthesis

def synthesize(project: str, now: int) -> dict[str, Any]:
    reg = registry(project)
    coordinator = str(reg.get("coordinatorSessionId") or "") if reg else ""
    generation = int(reg.get("generation") or -1) if reg else None
    manifest = common.read_manifest(coordinator) if coordinator else None
    lease_expiry = int(reg.get("leaseExpiresAt") or 0) if reg else 0
    leases = project_leases(coordinator) if coordinator else []
    waits = project_waits(project, coordinator) if coordinator else []
    gates = project_gates(project)
    commitments = project_commitments(project)
    active_workers = [l for l in leases if l.get("state") in ACTIVE_LEASE_STATES]
    terminal_workers = [l for l in leases if l.get("state") == "handoff-ready"]
    observed_waits = [w for w in waits if w.get("state") in {"observing", "terminal"}]
    open_gates = [g for g in gates if g.get("state") == "open"]
    active_commitments = [c for c in commitments if c.get("state") in ACTIVE_COMMITMENT_STATES]
    return {
        "coordinatorSessionId": coordinator or None,
        "generation": generation,
        "registryState": reg.get("state") if reg else "missing",
        "coordinatorLive": bool(common.session_live(manifest) and common.role_of(manifest or {}) == "coordinator"),
        "leaseExpiresAt": lease_expiry or None,
        "leaseStale": bool(reg and reg.get("state") != "hold" and lease_expiry and now > lease_expiry),
        "activeWorkers": [{"sessionId": l.get("sessionId"), "workUnit": l.get("workUnit"),
                           "attempt": l.get("attempt"), "state": l.get("state")} for l in active_workers],
        "terminalWorkers": [{"sessionId": l.get("sessionId"), "workUnit": l.get("workUnit"),
                             "state": l.get("state")} for l in terminal_workers],
        "externalWaits": [{"waitId": w.get("waitId"), "kind": w.get("kind"), "state": w.get("state"),
                           "deadlineAt": w.get("deadlineAt")} for w in waits],
        "observedWaitCount": len(observed_waits),
        "ownerGates": [{"gateId": g.get("gateId"), "state": g.get("state"),
                        "blockingScope": g.get("blockingScope"), "workUnit": g.get("workUnit")} for g in gates],
        "openGateCount": len(open_gates),
        "hold": bool(reg and reg.get("state") == "hold"),
        "commitments": [{"commitmentId": c.get("commitmentId"), "state": c.get("state"),
                         "bindingKind": c.get("bindingKind"), "deadlineAt": c.get("deadlineAt")} for c in commitments],
        "activeCommitmentCount": len(active_commitments),
        "inbox": inbox_pressure(project, generation, now),
        "latestEvidence": latest_evidence(coordinator, project, generation) if coordinator else None,
    }


def contradictions(declared: dict[str, Any], synth: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    phase = declared.get("phase")
    if phase == "waiting" and synth["observedWaitCount"] == 0 and synth["activeCommitmentCount"] == 0:
        issues.append("declared-waiting-without-observed-wait-or-commitment")
    if phase == "complete" and synth["activeWorkers"]:
        issues.append("declared-complete-with-active-workers")
    live_children = {w["sessionId"] for w in synth["activeWorkers"]} | {w["sessionId"] for w in synth["terminalWorkers"]}
    for ref in declared.get("childRefs") or []:
        if ref not in live_children:
            issues.append(f"child-ref-not-observed:{ref}")
    observed_wait_ids = {w["waitId"] for w in synth["externalWaits"] if w.get("state") in {"observing", "terminal"}}
    for ref in declared.get("waitRefs") or []:
        if ref not in observed_wait_ids:
            issues.append(f"wait-ref-not-observed:{ref}")
    return issues


def executable_actions(declared: dict[str, Any], synth: dict[str, Any]) -> bool:
    """A next action is executable if it exists and, when it waits on an observer,
    that observer is currently observed. Evidence-aware: an observed long wait stays
    trustworthy until its deadline."""
    actions = declared.get("nextActions") or []
    observed = bool(synth["activeWorkers"] or synth["observedWaitCount"] or synth["activeCommitmentCount"])
    if not actions:
        # No declared action is only executable if a live observer will drive the plan
        # (e.g. a waiting phase backed by an active commitment or worker). An idle
        # coordinator with neither a plan nor an observer is unexecutable.
        return observed
    if observed:
        return True
    # No live observers: the plan is only executable if an action is triggerable now
    # (its trigger is not a wait on something that no longer exists).
    for action in actions:
        trigger = str(action.get("trigger") or "").lower()
        if not any(word in trigger for word in ("wait", "await", "observe", "deadline", "commitment")):
            return True
    return False


def classify(declared: dict[str, Any] | None, synth: dict[str, Any], now: int, *,
             status_generation: int | None) -> dict[str, Any]:
    reg_generation = synth.get("generation")
    contra = contradictions(declared, synth) if declared else []
    issues: list[str] = []
    missing = declared is None
    generation_mismatch = (not missing and status_generation is not None
                           and reg_generation is not None and status_generation != reg_generation)
    review_at = declared.get("nextReviewAt") if declared else None
    observed = bool(synth["activeWorkers"] or synth["observedWaitCount"] or synth["activeCommitmentCount"])
    review_overdue = bool(review_at and now > int(review_at) + STALE_REVIEW_GRACE_SECONDS * 1000 and not observed)
    unexecutable = (not missing and not generation_mismatch
                    and declared.get("phase") not in TERMINAL_PHASES
                    and not executable_actions(declared, synth))

    if synth.get("hold"):
        classification = "blocked"
        issues.append("owner-hold")
    elif missing:
        classification = "stale"; issues.append("status-missing")
    elif generation_mismatch:
        classification = "stale"; issues.append("status-generation-mismatch")
    elif contra:
        classification = "contradictory"; issues.extend(contra)
    elif synth.get("leaseStale") or review_overdue:
        classification = "stale"
        issues.append("coordinator-lease-stale" if synth.get("leaseStale") else "review-overdue")
    elif unexecutable:
        classification = "contradictory"; issues.append("plan-unexecutable")
    elif synth["openGateCount"]:
        classification = "blocked"; issues.append("owner-gate-open")
    elif declared.get("phase") == "waiting" and (synth["observedWaitCount"] or synth["activeCommitmentCount"]):
        classification = "waiting-observed"
    elif synth["activeWorkers"]:
        classification = "executing"
    elif declared.get("phase") == "complete" or synth["latestEvidence"]:
        classification = "verified"
    else:
        classification = "stale"; issues.append("no-observed-activity")
    return {"classification": classification, "issues": issues, "contradictions": contra,
            "statusMissing": missing, "statusStale": missing or generation_mismatch or review_overdue or bool(synth.get("leaseStale")),
            "planUnexecutable": unexecutable}


# ---------------------------------------------------------- importable health API

def health_observations(now: int) -> list[dict[str, Any]]:
    """Deterministic per-project health rows for recovery-incident consumption.
    Only authoritative, live, non-HOLD coordinators are assessed."""
    out: list[dict[str, Any]] = []
    STATUS.mkdir(parents=True, exist_ok=True)
    projects = sorted({p.stem for p in COORDINATORS.glob("*.json")})
    for project in projects:
        reg = registry(project)
        if not reg or reg.get("state") != "authoritative":
            continue
        coordinator = str(reg.get("coordinatorSessionId") or "")
        generation = int(reg.get("generation") or -1)
        manifest = common.read_manifest(coordinator)
        if not common.session_live(manifest) or common.role_of(manifest or {}) != "coordinator":
            continue
        status = common.read_json(STATUS / f"{project}.json")
        declared = status.get("declared") if status else None
        synth = synthesize(project, now)
        verdict = classify(declared, synth, now, status_generation=(status.get("generation") if status else None))
        base = {"project": project, "sessionId": coordinator, "generation": generation}
        if verdict["statusMissing"]:
            out.append({**base, "kind": "coordinator-status-missing", "evidence": {"generation": generation}})
        else:
            if status and status.get("generation") != generation:
                out.append({**base, "kind": "coordinator-status-stale",
                            "evidence": {"generation": generation, "statusGeneration": status.get("generation"),
                                         "reason": "generation-mismatch"}})
            elif verdict["statusStale"]:
                out.append({**base, "kind": "coordinator-status-stale",
                            "evidence": {"generation": generation, "reason": "review-overdue-or-lease-stale"}})
            if verdict["contradictions"]:
                out.append({**base, "kind": "coordinator-status-contradiction",
                            "evidence": {"generation": generation, "contradictions": sorted(verdict["contradictions"])}})
            if verdict["planUnexecutable"]:
                out.append({**base, "kind": "coordinator-plan-unexecutable",
                            "evidence": {"generation": generation, "phase": declared.get("phase") if declared else None}})
    return out


# ------------------------------------------------------------------- validation

def validate_refs(project: str, coordinator: str, declared: dict[str, Any]) -> None:
    leases = {l.get("sessionId"): l for l in project_leases(coordinator)}
    for ref in declared.get("childRefs") or []:
        if ref not in leases:
            fail(f"child reference is not a live lease bound to this coordinator: {ref}")
    wait_ids = {w.get("waitId") for w in project_waits(project, coordinator)}
    for ref in declared.get("waitRefs") or []:
        if ref not in wait_ids:
            fail(f"wait reference does not exist for this coordinator: {ref}")
    gate_ids = {g.get("gateId") for g in project_gates(project)}
    for ref in (declared.get("gateRefs") or []) + (declared.get("blockerRefs") or []):
        if ref not in gate_ids:
            fail(f"gate reference does not exist for this project: {ref}")
    commitments = {c.get("commitmentId"): c for c in project_commitments(project)}
    for ref in declared.get("commitmentRefs") or []:
        if ref not in commitments:
            fail(f"commitment reference does not exist for this project: {ref}")


def normalize_declared(payload: dict[str, Any], now: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        fail("declared status payload must be a JSON object")
    scan_secret(payload, "status")
    objective = req_text(payload, "objective")
    phase = payload.get("phase")
    if phase not in PHASES:
        fail(f"phase must be one of {sorted(PHASES)}")
    actions = payload.get("nextActions") or []
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        fail(f"nextActions must be a list of at most {MAX_ACTIONS}")
    norm_actions = []
    for action in actions:
        if not isinstance(action, dict):
            fail("each next action must be an object")
        for key in ("description", "trigger", "requiredEvidence", "successBranch", "failureBranch"):
            req_text(action, key)
        norm_actions.append({k: action[k] for k in
                             ("description", "trigger", "requiredEvidence", "successBranch", "failureBranch")})
    completed = payload.get("completedOutcomes") or []
    if not isinstance(completed, list):
        fail("completedOutcomes must be a list")
    for outcome in completed:
        if not isinstance(outcome, dict) or not isinstance(outcome.get("summary"), str):
            fail("each completed outcome needs a summary")
    review_at = payload.get("nextReviewAt")
    if payload.get("nextReviewInSeconds") is not None:
        seconds = int(payload["nextReviewInSeconds"])
        if seconds < 60 or seconds > 604800:
            fail("nextReviewInSeconds must be between 60 and 604800")
        review_at = now + seconds * 1000
    for key in ("childRefs", "waitRefs", "gateRefs", "blockerRefs", "commitmentRefs"):
        refs = payload.get(key) or []
        if not isinstance(refs, list) or len(refs) > LIST_LIMIT:
            fail(f"{key} must be a bounded list")
    return {
        "objective": objective, "phase": phase, "currentFocus": payload.get("currentFocus"),
        "completedOutcomes": completed, "nextActions": norm_actions,
        "childRefs": payload.get("childRefs") or [], "waitRefs": payload.get("waitRefs") or [],
        "gateRefs": payload.get("gateRefs") or [], "blockerRefs": payload.get("blockerRefs") or [],
        "commitmentRefs": payload.get("commitmentRefs") or [], "nextReviewAt": review_at,
    }


# ---------------------------------------------------------------------- commands

def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.json:
        return json.loads(args.json)
    if args.input:
        return json.loads(Path(args.input).expanduser().read_text(encoding="utf-8"))
    fail("publish requires --json or --input")


def cmd_publish(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    now = common.now_ms()
    payload = load_payload(args)
    with common.file_lock(LOCK):
        reg = registry(project)
        if not reg or reg.get("state") != "authoritative":
            fail("no authoritative coordinator for project")
        if reg.get("coordinatorSessionId") != args.session:
            fail("coordinator session mismatch")
        if int(reg.get("generation") or -1) != args.generation:
            fail("stale coordinator generation may not publish status")
        manifest = common.read_manifest(args.session)
        if not common.session_live(manifest) or common.role_of(manifest) != "coordinator":
            fail("coordinator session is not live")
        declared = normalize_declared(payload, now)
        validate_refs(project, args.session, declared)
        if declared["phase"] == "waiting":
            commitments = {c.get("commitmentId"): c for c in project_commitments(project)}
            active = [r for r in declared["commitmentRefs"]
                      if commitments.get(r, {}).get("state") in ACTIVE_COMMITMENT_STATES]
            if not active:
                fail("a waiting phase requires at least one active observable commitment reference")
        prior = common.read_json(STATUS / f"{project}.json")
        revision = int(prior.get("revision") or 0) + 1 if prior else 1
        record = {"schemaVersion": SCHEMA, "project": project, "coordinatorSessionId": args.session,
                  "generation": args.generation, "revision": revision, "declared": declared,
                  "publishedAt": now, "updatedAt": now}
        if args.apply:
            common.atomic_json(STATUS / f"{project}.json", record)
    print(json.dumps({"applied": args.apply, "revision": revision, "record": record}, ensure_ascii=False, indent=2))
    return 0


def build_report(project: str, now: int) -> dict[str, Any]:
    status = common.read_json(STATUS / f"{project}.json")
    declared = status.get("declared") if status else None
    synth = synthesize(project, now)
    verdict = classify(declared, synth, now, status_generation=(status.get("generation") if status else None))
    return {"project": project, "declared": declared,
            "revision": status.get("revision") if status else None,
            "publishedAt": status.get("publishedAt") if status else None,
            "synthesized": synth, "classification": verdict["classification"],
            "issues": verdict["issues"]}


def cmd_show(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    report = build_report(project, common.now_ms())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    report = build_report(project, common.now_ms())
    healthy = report["classification"] not in {"stale", "contradictory"}
    print(json.dumps({"project": project, "healthy": healthy,
                      "classification": report["classification"], "issues": report["issues"]},
                     ensure_ascii=False, indent=2))
    return 0 if healthy else 2


def cmd_reconcile(args: argparse.Namespace) -> int:
    now = common.now_ms()
    rows = health_observations(now)
    print(json.dumps({"applied": args.apply, "observations": rows}, ensure_ascii=False, indent=2))
    return 0


def markdown_report(reports: list[dict[str, Any]], now: int) -> str:
    lines = ["# Product status — all projects", ""]
    for report in reports:
        declared = report["declared"] or {}
        synth = report["synthesized"]
        lines.append(f"## {report['project']} — `{report['classification']}`")
        lines.append("")
        lines.append(f"- **Product objective:** {declared.get('objective') or '_not published_'}")
        lines.append(f"- **Current phase / outcome:** {declared.get('phase') or 'unknown'} — {declared.get('currentFocus') or 'n/a'}")
        active = ", ".join(f"{w['workUnit'] or w['sessionId']} ({w['state']})" for w in synth["activeWorkers"]) or "none"
        lines.append(f"- **Executing now:** {active}")
        terminal = ", ".join(f"{w['workUnit'] or w['sessionId']}" for w in synth["terminalWorkers"]) or "none"
        lines.append(f"- **Worker/auditor progress:** {len(synth['activeWorkers'])} active, {len(synth['terminalWorkers'])} terminal ({terminal})")
        awaited = ", ".join(f"{w['waitId']}:{w['state']}" for w in synth["externalWaits"]) or "nothing"
        lines.append(f"- **Awaited (observed):** {awaited}; {synth['activeCommitmentCount']} active commitment(s)")
        gates = ", ".join(f"{g['gateId']}" for g in synth["ownerGates"] if g["state"] == "open") or "none"
        lines.append(f"- **Blockers / owner gates:** {'HOLD' if synth['hold'] else gates}")
        actions = declared.get("nextActions") or []
        if actions:
            lines.append("- **Next actions:**")
            for i, action in enumerate(actions, 1):
                lines.append(f"  {i}. {action['description']} — _on_ {action['trigger']} → {action['successBranch']} / {action['failureBranch']}")
        else:
            lines.append("- **Next actions:** _none published_")
        review = declared.get("nextReviewAt")
        lines.append(f"- **Next automatic check:** {review if review else 'unscheduled'}")
        lines.append(f"- **Evidence timestamp:** {report.get('publishedAt') or 'never'}; freshness `{report['classification']}`")
        if report["issues"]:
            lines.append(f"- **Notes:** {', '.join(report['issues'])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def cmd_report(args: argparse.Namespace) -> int:
    now = common.now_ms()
    projects = sorted({p.stem for p in COORDINATORS.glob("*.json")}) if args.all else (
        [clean_project(args.project)] if args.project else [])
    if not projects:
        fail("report requires --all or --project")
    reports = [build_report(project, now) for project in projects]
    if args.format == "markdown":
        print(markdown_report(reports, now), end="")
    else:
        print(json.dumps({"projects": reports}, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    pub = sub.add_parser("publish")
    pub.add_argument("--project", required=True); pub.add_argument("--session", required=True)
    pub.add_argument("--generation", type=int, required=True)
    pub.add_argument("--json"); pub.add_argument("--input"); pub.add_argument("--apply", action="store_true")
    pub.set_defaults(func=cmd_publish)

    sh = sub.add_parser("show"); sh.add_argument("--project", required=True); sh.set_defaults(func=cmd_show)
    va = sub.add_parser("validate"); va.add_argument("--project", required=True); va.set_defaults(func=cmd_validate)
    rc = sub.add_parser("reconcile"); rc.add_argument("--apply", action="store_true"); rc.set_defaults(func=cmd_reconcile)

    rp = sub.add_parser("report")
    rp.add_argument("--all", action="store_true"); rp.add_argument("--project")
    rp.add_argument("--format", choices=["json", "markdown"], default="markdown"); rp.set_defaults(func=cmd_report)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))

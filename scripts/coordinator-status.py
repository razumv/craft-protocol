#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Durable product-increment status for autonomous coordinators (Protocol v3.4.0).

A coordinator publishes a declarative product-status snapshot: customer-visible
objective, what is demonstrable now, remaining outcome, ETA/confidence, one real
blocker, a bounded dependency-valid Product Increment, current phase, completed
outcomes, current focus, up to three ordered next actions, and its next review time.
Everything else in a report — coordinator lease health, active/terminal worker
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
MAX_INCREMENT_STORIES = 8
TEXT_LIMIT = 800
LIST_LIMIT = 32
CONFIDENCE_LEVELS = {"low", "medium", "high"}
INCREMENT_STAGES = {"discovery", "building", "integrating", "accepting", "deploying", "demonstrating", "complete", "blocked"}
RISK_TIERS = {"low", "medium", "high"}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
STORY_STATES = {"planned", "ready", "executing", "integrated", "accepted", "blocked", "failed", "deferred"}
STALE_REVIEW_GRACE_SECONDS = int(os.environ.get("CRAFT_STATUS_REVIEW_GRACE_SECONDS", "900"))


def fail(message: str) -> None:
    raise SystemExit(message)


def clean_project(raw: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-")
    if not value:
        fail("invalid project slug")
    return value


def scan_secret(value: Any, label: str = "field", depth: int = 0) -> None:
    if depth > 8:
        fail(f"{label} nesting is unbounded")
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
            scan_secret(item, label, depth + 1)
    elif isinstance(value, dict):
        if len(value) > LIST_LIMIT:
            fail(f"{label} object is unbounded")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                fail(f"{label} has an invalid key")
            scan_secret(item, f"{label}.{key}", depth + 1)


def req_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 and c not in "\t\n" for c in value):
        fail(f"missing or invalid {key}")
    return value


def object_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def exact_int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def exact_generation(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


# --------------------------------------------------------------- runtime readers

def registry(project: str) -> dict[str, Any] | None:
    return object_or_none(common.read_json(COORDINATORS / f"{project}.json"))


def authoritative(project: str) -> dict[str, Any] | None:
    row = registry(project)
    if not row or row.get("state") not in {"authoritative", "hold"}:
        return None
    return row


def project_leases(coordinator: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(LEASES.glob("*.json")):
        lease = object_or_none(common.read_json(path))
        if lease and lease.get("parentSessionId") == coordinator:
            rows.append(lease)
    return rows


def project_waits(project: str, coordinator: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(WAITS.glob("*.json")):
        wait = object_or_none(common.read_json(path))
        if wait and wait.get("project") == project and wait.get("coordinatorSessionId") == coordinator:
            rows.append(wait)
    return rows


def project_gates(project: str) -> list[dict[str, Any]]:
    return [g for path in sorted((GATES / project).glob("*.json"))
            if (g := object_or_none(common.read_json(path)))]


def project_commitments(project: str) -> list[dict[str, Any]]:
    return [c for path in sorted((COMMITMENTS / project).glob("*.json"))
            if (c := object_or_none(common.read_json(path)))]


def inbox_pressure(project: str, generation: int | None, now: int) -> dict[str, Any]:
    pending = claimed = waking = 0
    for path in sorted((INBOX / project).glob("*.json")):
        item = object_or_none(common.read_json(path))
        if not item:
            continue
        if generation is not None and exact_generation(item.get("coordinatorGeneration")) != generation:
            continue
        available = item.get("state") == "pending" or (
            item.get("state") == "claimed" and exact_int(item.get("claimExpiresAt"), 0) <= now)
        if available:
            pending += 1
            if item.get("waking"):
                waking += 1
        elif item.get("state") == "claimed":
            claimed += 1
    return {"pending": pending, "claimed": claimed, "wakingPending": waking}


def verification_evidence_keys(project: str, generation: int | None) -> list[str]:
    keys: list[str] = []
    for path in sorted((INBOX / project).glob("*.json")):
        item = object_or_none(common.read_json(path))
        if not item or (generation is not None
                        and exact_generation(item.get("coordinatorGeneration")) != generation):
            continue
        verification_grade = (item.get("kind") in {"audit-verdict", "observer-terminal"}
                              or (item.get("kind") == "terminal-handoff" and bool(item.get("evidence"))))
        if verification_grade and item.get("eventKey"):
            keys.append(str(item["eventKey"]))
    return keys


def latest_evidence(coordinator: str, project: str, generation: int | None) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for path in sorted((INBOX / project).glob("*.json")):
        item = object_or_none(common.read_json(path))
        if not item or item.get("kind") not in {"candidate", "audit-verdict", "terminal-handoff", "observer-terminal"}:
            continue
        if generation is not None and exact_generation(item.get("coordinatorGeneration")) != generation:
            continue
        ts = exact_int(item.get("updatedAt"), 0)
        if best is None or ts > exact_int(best.get("at"), 0):
            best = {"eventKey": item.get("eventKey"), "kind": item.get("kind"),
                    "workUnit": item.get("workUnit"), "subject": item.get("subject"),
                    "evidence": item.get("evidence"), "at": ts}
    return best


# ------------------------------------------------------------------- synthesis

def synthesize(project: str, now: int) -> dict[str, Any]:
    reg = registry(project)
    coordinator = str(reg.get("coordinatorSessionId") or "") if reg else ""
    generation = exact_generation(reg.get("generation")) if reg else None
    manifest = object_or_none(common.read_manifest(coordinator)) if coordinator else None
    lease_expiry = exact_int(reg.get("leaseExpiresAt"), 0) if reg else 0
    leases = project_leases(coordinator) if coordinator else []
    waits = project_waits(project, coordinator) if coordinator else []
    gates = project_gates(project)
    commitments = [c for c in project_commitments(project)
                   if generation is not None and exact_generation(c.get("generation")) == generation]
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
                           "attempt": l.get("attempt"), "state": l.get("state")} for l in active_workers[:LIST_LIMIT]],
        "activeWorkerCount": len(active_workers), "activeWorkersTruncated": len(active_workers) > LIST_LIMIT,
        "terminalWorkers": [{"sessionId": l.get("sessionId"), "workUnit": l.get("workUnit"),
                             "state": l.get("state")} for l in terminal_workers[:LIST_LIMIT]],
        "terminalWorkerCount": len(terminal_workers), "terminalWorkersTruncated": len(terminal_workers) > LIST_LIMIT,
        "externalWaits": [{"waitId": w.get("waitId"), "kind": w.get("kind"), "state": w.get("state"),
                           "deadlineAt": w.get("deadlineAt")} for w in waits[:LIST_LIMIT]],
        "externalWaitCount": len(waits), "externalWaitsTruncated": len(waits) > LIST_LIMIT,
        "observedWaitCount": len(observed_waits),
        "ownerGates": [{"gateId": g.get("gateId"), "state": g.get("state"),
                        "blockingScope": g.get("blockingScope"), "workUnit": g.get("workUnit")} for g in gates[:LIST_LIMIT]],
        "ownerGateCount": len(gates), "ownerGatesTruncated": len(gates) > LIST_LIMIT,
        "openGateCount": len(open_gates),
        "hold": bool(reg and reg.get("state") == "hold"),
        "commitments": [{"commitmentId": c.get("commitmentId"), "state": c.get("state"),
                         "bindingKind": c.get("bindingKind"), "deadlineAt": c.get("deadlineAt")} for c in commitments[:LIST_LIMIT]],
        "commitmentCount": len(commitments), "commitmentsTruncated": len(commitments) > LIST_LIMIT,
        "activeCommitmentCount": len(active_commitments),
        "inbox": inbox_pressure(project, generation, now),
        "latestEvidence": latest_evidence(coordinator, project, generation) if coordinator else None,
        "_verificationEvidenceKeys": verification_evidence_keys(project, generation),
        "_activeWorkerIds": [str(l.get("sessionId")) for l in active_workers],
        "_terminalWorkerIds": [str(l.get("sessionId")) for l in terminal_workers],
        "_observedWaitIds": [str(w.get("waitId")) for w in observed_waits],
    }


def contradictions(declared: dict[str, Any], synth: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    phase = declared.get("phase")
    if phase == "waiting" and synth["observedWaitCount"] == 0 and synth["activeCommitmentCount"] == 0:
        issues.append("declared-waiting-without-observed-wait-or-commitment")
    if phase == "complete" and synth["activeWorkers"]:
        issues.append("declared-complete-with-active-workers")
    outcome_refs = {str(o.get("evidenceRef") or "") for o in declared.get("completedOutcomes") or []
                    if isinstance(o, dict)}
    verification_keys = set(synth.get("_verificationEvidenceKeys") or [])
    if phase == "complete" and (not outcome_refs or not outcome_refs.issubset(verification_keys)):
        issues.append("declared-complete-without-observed-verification-evidence")
    live_children = set(synth.get("_activeWorkerIds") or []) | set(synth.get("_terminalWorkerIds") or [])
    for ref in declared.get("childRefs") or []:
        if ref not in live_children:
            issues.append(f"child-ref-not-observed:{ref}")
    observed_wait_ids = set(synth.get("_observedWaitIds") or [])
    for ref in declared.get("waitRefs") or []:
        if ref not in observed_wait_ids:
            issues.append(f"wait-ref-not-observed:{ref}")
    # An owner gate holds its own scope only. A declared dependency-ready or
    # executing story with no lane, no wait and no commitment means the project
    # stopped work it is allowed to do — the observed failure mode where a whole
    # increment halts behind one gate while `ready` stories sit unassigned.
    increment = declared.get("productIncrement") or {}
    idle_ready = sorted({str(story.get("id")) for story in increment.get("stories") or []
                         if isinstance(story, dict) and story.get("state") in {"ready", "executing"}})
    if (idle_ready and not synth["activeWorkers"] and not synth["observedWaitCount"]
            and not synth["activeCommitmentCount"]):
        issues.append("idle-ready-work:" + ",".join(idle_ready[:8]))
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
    generation_mismatch = (not missing and (status_generation is None
                           or reg_generation is None or status_generation != reg_generation))
    review_at = declared.get("nextReviewAt") if declared else None
    review_value = exact_int(review_at, -1) if review_at is not None else None
    if review_at is not None and review_value == -1:
        contra.append("next-review-malformed")
    observed = bool(synth["activeWorkers"] or synth["observedWaitCount"] or synth["activeCommitmentCount"])
    review_overdue = bool(review_value is not None and review_value >= 0
                          and now > review_value + STALE_REVIEW_GRACE_SECONDS * 1000 and not observed)
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
    elif declared.get("phase") == "blocked" and synth["activeCommitmentCount"]:
        # A truthful blocked plan can remain healthy when a bounded observer is
        # responsible for its next review. Absence of both an open gate and an
        # active commitment still falls through to stale/no-observed-activity.
        classification = "blocked"
    elif declared.get("phase") == "waiting" and (synth["observedWaitCount"] or synth["activeCommitmentCount"]):
        classification = "waiting-observed"
    elif synth["activeWorkers"]:
        classification = "executing"
    elif declared.get("phase") == "complete":
        classification = "verified"
    elif declared.get("phase") in {"initializing", "executing", "review"}:
        classification = "executing"
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
        generation = exact_generation(reg.get("generation"))
        if generation is None:
            continue
        manifest = object_or_none(common.read_manifest(coordinator))
        if not common.session_live(manifest) or common.role_of(manifest or {}) != "coordinator":
            continue
        status_path = STATUS / f"{project}.json"
        raw_status = common.read_json(status_path)
        status = object_or_none(raw_status)
        declared, stored_malformed = canonical_stored_declared(
            status, project, coordinator, generation)
        synth = synthesize(project, now)
        status_generation = exact_generation(status.get("generation")) if status else None
        verdict = classify(declared, synth, now, status_generation=status_generation)
        base = {"project": project, "sessionId": coordinator, "generation": generation}
        if ((status_path.exists() and status is None) or stored_malformed):
            out.append({**base, "kind": "coordinator-status-contradiction",
                        "evidence": {"generation": generation, "reason": "stored-status-malformed"}})
            continue
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
    reg = registry(project) or {}
    generation = exact_generation(reg.get("generation"))
    if generation is None:
        fail("coordinator registry generation is malformed")
    commitments = {c.get("commitmentId"): c for c in project_commitments(project)
                   if exact_generation(c.get("generation")) == generation}
    for ref in declared.get("commitmentRefs") or []:
        if ref not in commitments:
            fail(f"commitment reference does not exist in this coordinator generation: {ref}")
    inbox_items = {str(i.get("eventKey")): i for i in
                   (object_or_none(common.read_json(p)) for p in sorted((INBOX / project).glob("*.json")))
                   if i and exact_generation(i.get("coordinatorGeneration")) == generation}
    for outcome in declared.get("completedOutcomes") or []:
        ref = str(outcome.get("evidenceRef") or "")
        item = inbox_items.get(ref)
        if not item:
            fail(f"completed outcome evidence reference is not observed in this generation: {ref}")
        verification_grade = (item.get("kind") in {"audit-verdict", "observer-terminal"}
                              or (item.get("kind") == "terminal-handoff" and bool(item.get("evidence"))))
        if declared.get("phase") == "complete" and not verification_grade:
            fail(f"completed outcome evidence is not verification-grade: {ref}")
    increment = declared.get("productIncrement") or {}
    completion = increment.get("completionEvidence") or {}
    if increment.get("stage") == "complete":
        bound: dict[str, dict[str, Any]] = {}
        for key, binding in completion.items():
            event_key = str(binding.get("eventKey") or "")
            item = inbox_items.get(event_key)
            if not item:
                fail(f"Product Increment completion evidence is not observed in this generation: {key}={event_key}")
            if (exact_int(item.get("revision"), 0) != binding.get("revision")
                    or item.get("fingerprint") != binding.get("fingerprint")):
                fail(f"Product Increment completion evidence immutable binding mismatch: {key}={event_key}")
            if not item.get("evidence"):
                fail(f"Product Increment completion evidence has no evidence payload: {key}={event_key}")
            bound[key] = item
        if bound["integratedCandidateRef"].get("kind") != "terminal-handoff":
            fail("integratedCandidateRef must reference a terminal-handoff")
        acceptance_kind = bound["acceptanceRef"].get("kind")
        if increment.get("riskTier") in {"medium", "high"} and acceptance_kind != "audit-verdict":
            fail("Medium/High Product Increment acceptanceRef must reference an audit-verdict")
        if acceptance_kind == "audit-verdict" and bound["acceptanceRef"].get("senderRole") != "auditor":
            fail("audit-verdict acceptanceRef must be authored by an auditor")
        if acceptance_kind not in {"audit-verdict", "terminal-handoff"}:
            fail("acceptanceRef must reference a verification-grade acceptance report")
        release = bound["releaseReadbackRef"]
        if release.get("kind") != "observer-terminal":
            fail("releaseReadbackRef must reference an observer-terminal receipt")
        release_waits = [wait for wait in project_waits(project, coordinator)
                         if wait.get("watcherSessionId") == release.get("sender")
                         and wait.get("workUnit") == release.get("workUnit")
                         and wait.get("state") in {"terminal", "deadline", "cleared"}]
        if len(release_waits) != 1:
            fail("releaseReadbackRef must retain one exact terminal external-wait provenance binding")
        demonstration = bound["demonstrationRef"]
        if demonstration.get("kind") not in {"terminal-handoff", "observer-terminal"}:
            fail("demonstrationRef must reference a terminal real-workflow report")
        criterion = str(increment.get("demonstrationCriterion") or "").strip()
        evidence_text = "\n".join([str(demonstration.get("subject") or ""),
                                   *[str(item) for item in demonstration.get("evidence") or []]])
        if criterion not in evidence_text:
            fail("demonstrationRef evidence must include the exact demonstrationCriterion")


def optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > TEXT_LIMIT
            or any(ord(c) < 32 and c not in "\t\n" for c in value)):
        fail(f"{key} must be bounded non-empty text or null")
    return value


def normalize_increment(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        fail("productIncrement must be an object or null")
    increment_id = req_text(value, "id")
    stage = value.get("stage")
    if stage not in INCREMENT_STAGES:
        fail(f"productIncrement.stage must be one of {sorted(INCREMENT_STAGES)}")
    risk_tier = value.get("riskTier")
    if risk_tier not in RISK_TIERS:
        fail(f"productIncrement.riskTier must be one of {sorted(RISK_TIERS)}")
    demo = req_text(value, "demonstrationCriterion")
    raw_non_goals = value.get("nonGoals")
    non_goals = [] if raw_non_goals is None else raw_non_goals
    if not isinstance(non_goals, list) or len(non_goals) > MAX_INCREMENT_STORIES:
        fail(f"productIncrement.nonGoals must be a list of at most {MAX_INCREMENT_STORIES}")
    if any(not isinstance(item, str) or not item.strip() or len(item) > TEXT_LIMIT
           or any(ord(ch) < 32 and ch not in "\t\n" for ch in item) for item in non_goals):
        fail("productIncrement.nonGoals entries must be bounded non-empty text")
    stories = value.get("stories") or []
    if not isinstance(stories, list) or not stories or len(stories) > MAX_INCREMENT_STORIES:
        fail(f"productIncrement.stories must contain 1..{MAX_INCREMENT_STORIES} stories")
    normalized: list[dict[str, Any]] = []
    story_ids: set[str] = set()
    for story in stories:
        if not isinstance(story, dict):
            fail("each productIncrement story must be an object")
        story_id = req_text(story, "id")
        if len(story_id) > 128 or story_id in story_ids:
            fail(f"duplicate or invalid productIncrement story id: {story_id}")
        story_ids.add(story_id)
        title = req_text(story, "title")
        state = story.get("state")
        if state not in STORY_STATES:
            fail(f"productIncrement story state must be one of {sorted(STORY_STATES)}")
        risk = story.get("riskContribution", "low")
        if risk not in RISK_TIERS:
            fail(f"productIncrement story riskContribution must be one of {sorted(RISK_TIERS)}")
        raw_dependencies = story.get("dependsOn")
        dependencies = [] if raw_dependencies is None else raw_dependencies
        if (not isinstance(dependencies, list) or len(dependencies) > MAX_INCREMENT_STORIES
                or any(not isinstance(dep, str) or not dep for dep in dependencies)):
            fail("productIncrement story dependsOn must be a bounded text list")
        if len(set(dependencies)) != len(dependencies):
            fail(f"duplicate dependency in productIncrement story: {story_id}")
        normalized.append({"id": story_id, "title": title, "state": state,
                           "dependsOn": dependencies, "riskContribution": risk})
    graph = {story["id"]: story["dependsOn"] for story in normalized}
    for story_id, dependencies in graph.items():
        for dep in dependencies:
            if dep == story_id:
                fail(f"productIncrement story may not depend on itself: {story_id}")
            if dep not in story_ids:
                fail(f"unknown productIncrement dependency: {story_id} -> {dep}")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(story_id: str) -> None:
        if story_id in visiting:
            fail(f"productIncrement dependency cycle detected at: {story_id}")
        if story_id in visited:
            return
        visiting.add(story_id)
        for dep in graph[story_id]:
            visit(dep)
        visiting.remove(story_id)
        visited.add(story_id)
    for story_id in sorted(graph):
        visit(story_id)
    max_story_risk = max((RISK_ORDER[story["riskContribution"]] for story in normalized), default=0)
    if RISK_ORDER[risk_tier] < max_story_risk:
        fail("productIncrement.riskTier may not understate story riskContribution")
    raw_completion = value.get("completionEvidence")
    completion: dict[str, dict[str, Any]] | None = None
    if raw_completion is not None:
        if not isinstance(raw_completion, dict) or set(raw_completion) != {
                "integratedCandidateRef", "acceptanceRef", "releaseReadbackRef", "demonstrationRef"}:
            fail("productIncrement.completionEvidence must contain exactly four evidence bindings")
        completion = {}
        for key in ("integratedCandidateRef", "acceptanceRef", "releaseReadbackRef", "demonstrationRef"):
            binding = raw_completion.get(key)
            if not isinstance(binding, dict) or set(binding) != {"eventKey", "revision", "fingerprint"}:
                fail(f"productIncrement.completionEvidence.{key} must bind eventKey, revision, and fingerprint")
            event_key = binding.get("eventKey")
            fingerprint = binding.get("fingerprint")
            revision = binding.get("revision")
            if (not isinstance(event_key, str) or not event_key or len(event_key) > 128
                    or any(ord(ch) < 32 for ch in event_key)):
                fail(f"productIncrement.completionEvidence.{key}.eventKey must be bounded text")
            if (not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
                fail(f"productIncrement.completionEvidence.{key}.fingerprint must be SHA-256 hex")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                fail(f"productIncrement.completionEvidence.{key}.revision must be a positive integer")
            completion[key] = {"eventKey": event_key, "revision": revision, "fingerprint": fingerprint}
        if len({binding["eventKey"] for binding in completion.values()}) != 4:
            fail("productIncrement.completionEvidence event keys must be distinct")
    if stage == "complete":
        if any(story["state"] != "accepted" for story in normalized):
            fail("complete Product Increment requires every story to be accepted")
        if completion is None:
            fail("complete Product Increment requires completionEvidence")
    elif completion is not None:
        fail("productIncrement.completionEvidence is allowed only at stage complete")
    return {"id": increment_id, "stage": stage, "riskTier": risk_tier,
            "demonstrationCriterion": demo, "nonGoals": non_goals, "stories": normalized,
            "completionEvidence": completion}


def normalize_declared(payload: dict[str, Any], now: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        fail("declared status payload must be a JSON object")
    scan_secret(payload, "status")
    objective = req_text(payload, "objective")
    demonstrable_now = optional_text(payload, "demonstrableNow")
    remaining_outcome = optional_text(payload, "remainingOutcome")
    eta_range = optional_text(payload, "etaRange")
    real_blocker = optional_text(payload, "realBlocker")
    confidence = payload.get("confidence")
    if confidence is not None and confidence not in CONFIDENCE_LEVELS:
        fail(f"confidence must be one of {sorted(CONFIDENCE_LEVELS)} or null")
    product_increment = normalize_increment(payload.get("productIncrement"))
    current_focus = payload.get("currentFocus")
    if current_focus is not None and not isinstance(current_focus, str):
        fail("currentFocus must be text or null")
    phase = payload.get("phase")
    if phase not in PHASES:
        fail(f"phase must be one of {sorted(PHASES)}")
    if product_increment is not None:
        increment_complete = product_increment["stage"] == "complete"
        if (phase == "complete") != increment_complete:
            fail("Product Increment stage complete and coordinator phase complete must match")
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
    if not isinstance(completed, list) or len(completed) > LIST_LIMIT:
        fail("completedOutcomes must be a bounded list")
    normalized_completed = []
    for outcome in completed:
        if not isinstance(outcome, dict):
            fail("each completed outcome must be an object")
        summary = req_text(outcome, "summary")
        evidence_ref = req_text(outcome, "evidenceRef")
        normalized_completed.append({"summary": summary, "evidenceRef": evidence_ref})
    if payload.get("nextReviewAt") is not None and payload.get("nextReviewInSeconds") is not None:
        fail("provide either nextReviewAt or nextReviewInSeconds, not both")
    review_at = payload.get("nextReviewAt")
    if payload.get("nextReviewInSeconds") is not None:
        seconds = payload["nextReviewInSeconds"]
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            fail("nextReviewInSeconds must be an integer")
        if seconds < 60 or seconds > 604800:
            fail("nextReviewInSeconds must be between 60 and 604800")
        review_at = now + seconds * 1000
    elif review_at is not None:
        if not isinstance(review_at, int) or isinstance(review_at, bool):
            fail("nextReviewAt must be an integer timestamp")
        if review_at < now + 60_000 or review_at > now + 604_800_000:
            fail("nextReviewAt must be between 60 seconds and 7 days from publish")
    if phase not in TERMINAL_PHASES and review_at is None:
        fail("non-terminal status requires nextReviewAt or nextReviewInSeconds")
    normalized_refs: dict[str, list[str]] = {}
    for key in ("childRefs", "waitRefs", "gateRefs", "blockerRefs", "commitmentRefs"):
        refs = payload.get(key) or []
        if not isinstance(refs, list) or len(refs) > LIST_LIMIT:
            fail(f"{key} must be a bounded list")
        if any(not isinstance(ref, str) or not ref or len(ref) > 128
               or any(ord(ch) < 32 for ch in ref) for ref in refs):
            fail(f"{key} entries must be bounded non-empty text")
        normalized_refs[key] = refs
    return {
        "objective": objective, "demonstrableNow": demonstrable_now,
        "remainingOutcome": remaining_outcome, "etaRange": eta_range,
        "confidence": confidence, "realBlocker": real_blocker,
        "productIncrement": product_increment,
        "phase": phase, "currentFocus": current_focus,
        "completedOutcomes": normalized_completed, "nextActions": norm_actions,
        "childRefs": normalized_refs["childRefs"], "waitRefs": normalized_refs["waitRefs"],
        "gateRefs": normalized_refs["gateRefs"], "blockerRefs": normalized_refs["blockerRefs"],
        "commitmentRefs": normalized_refs["commitmentRefs"], "nextReviewAt": review_at,
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
        if args.generation < 1 or exact_generation(reg.get("generation")) != args.generation:
            fail("stale coordinator generation may not publish status")
        manifest = object_or_none(common.read_manifest(args.session))
        if not common.session_live(manifest) or common.role_of(manifest) != "coordinator":
            fail("coordinator session is not live")
        declared = normalize_declared(payload, now)
        validate_refs(project, args.session, declared)
        if declared["phase"] == "waiting":
            commitments = {c.get("commitmentId"): c for c in project_commitments(project)
                           if exact_generation(c.get("generation")) == args.generation}
            active = [r for r in declared["commitmentRefs"]
                      if commitments.get(r, {}).get("state") in ACTIVE_COMMITMENT_STATES]
            if not active:
                fail("a waiting phase requires at least one active observable commitment reference")
        if declared["phase"] == "blocked":
            open_gate_ids = {g.get("gateId") for g in project_gates(project) if g.get("state") == "open"}
            open_gate_refs = [r for r in declared["gateRefs"] + declared["blockerRefs"]
                              if r in open_gate_ids]
            commitments = {c.get("commitmentId"): c for c in project_commitments(project)
                           if exact_generation(c.get("generation")) == args.generation}
            active = [r for r in declared["commitmentRefs"]
                      if commitments.get(r, {}).get("state") in ACTIVE_COMMITMENT_STATES]
            if not open_gate_refs and not active:
                fail("a blocked phase requires an open owner-gate reference or an active observable "
                     "commitment; reversible technical work must continue autonomously instead of "
                     "publishing a prose blocker")
        if declared["phase"] == "hold":
            holds = [g for g in project_gates(project) if g.get("state") == "open"
                     and (g.get("gateId") == "project-hold"
                          or g.get("ownerOnlyCategory") == "explicit-hold")]
            if not holds:
                fail("a hold phase requires an open explicit-hold owner gate; a coordinator may not self-hold")
        prior_path = STATUS / f"{project}.json"
        prior_raw = common.read_json(prior_path)
        prior = object_or_none(prior_raw)
        if prior_path.exists() and prior is None:
            fail("stored coordinator status is malformed")
        prior_revision = exact_int(prior.get("revision"), 0) if prior else 0
        if prior and prior_revision <= 0:
            fail("stored coordinator status revision is malformed")
        revision = prior_revision + 1
        record = {"schemaVersion": SCHEMA, "project": project, "coordinatorSessionId": args.session,
                  "generation": args.generation, "revision": revision, "declared": declared,
                  "publishedAt": now, "updatedAt": now}
        if args.apply:
            common.atomic_json(STATUS / f"{project}.json", record)
    print(json.dumps({"applied": args.apply, "revision": revision, "record": record}, ensure_ascii=False, indent=2))
    return 0


def canonical_stored_declared(status: dict[str, Any] | None, project: str,
                              coordinator: str | None, generation: int | None) -> tuple[dict[str, Any] | None, bool]:
    if not status:
        return None, False
    raw_declared = status.get("declared")
    published_at = exact_int(status.get("publishedAt"), 0)
    updated_at = exact_int(status.get("updatedAt"), 0)
    top_level_valid = (
        status.get("schemaVersion") == SCHEMA and status.get("project") == project
        and isinstance(status.get("coordinatorSessionId"), str)
        and status.get("coordinatorSessionId") == coordinator
        and exact_generation(status.get("generation")) == generation
        and exact_int(status.get("revision"), 0) >= 1
        and published_at > 0 and updated_at >= published_at
    )
    if not top_level_valid or not isinstance(raw_declared, dict):
        return None, True
    try:
        return normalize_declared(raw_declared, published_at), False
    except SystemExit:
        return None, True


def build_report(project: str, now: int) -> dict[str, Any]:
    status_path = STATUS / f"{project}.json"
    raw_status = common.read_json(status_path)
    status = object_or_none(raw_status)
    malformed = status_path.exists() and status is None
    synth = synthesize(project, now)
    declared, stored_malformed = canonical_stored_declared(
        status, project, synth.get("coordinatorSessionId"), synth.get("generation"))
    malformed = malformed or stored_malformed
    status_generation = exact_generation(status.get("generation")) if status else None
    verdict = classify(declared, synth, now, status_generation=status_generation)
    issues = list(verdict["issues"])
    if malformed:
        issues.append("stored-status-malformed")
    public_synth = {key: value for key, value in synth.items() if not key.startswith("_")}
    return {"project": project, "declared": declared,
            "revision": status.get("revision") if status else None,
            "publishedAt": status.get("publishedAt") if status else None,
            "synthesized": public_synth, "classification": ("contradictory" if malformed else verdict["classification"]),
            "issues": issues}


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
        lines.append(f"- **What the customer will see:** {declared.get('objective') or '_not published_'}")
        lines.append(f"- **Demonstrable now:** {declared.get('demonstrableNow') or '_not published_'}")
        lines.append(f"- **What remains:** {declared.get('remainingOutcome') or '_not published_'}")
        lines.append(f"- **ETA / confidence:** {declared.get('etaRange') or '_not published_'} / {declared.get('confidence') or '_not published_'}")
        lines.append(f"- **One real blocker:** {declared.get('realBlocker') or 'none'}")
        increment = declared.get("productIncrement") or {}
        if increment:
            stories = increment.get("stories") or []
            accepted = sum(1 for story in stories if story.get("state") in {"accepted", "integrated"})
            lines.append(f"- **Product Increment:** {increment.get('id')} — {increment.get('stage')} — {accepted}/{len(stories)} integrated or accepted — risk `{increment.get('riskTier')}`")
            lines.append(f"- **Real-workflow demonstration:** {increment.get('demonstrationCriterion')}")
        else:
            lines.append("- **Product Increment:** _not published (legacy v3.3 snapshot)_")
        lines.append(f"- **Current phase / technical focus:** {declared.get('phase') or 'unknown'} — {declared.get('currentFocus') or 'n/a'}")
        active = ", ".join(f"{w['workUnit'] or w['sessionId']} ({w['state']})" for w in synth["activeWorkers"]) or "none"
        lines.append(f"- **Executing now:** {active}")
        terminal = ", ".join(f"{w['workUnit'] or w['sessionId']}" for w in synth["terminalWorkers"]) or "none"
        lines.append(f"- **Worker/auditor progress:** {synth['activeWorkerCount']} active, {synth['terminalWorkerCount']} terminal ({terminal})")
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

#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Atomic authoritative-coordinator ownership registry.

The registry is append-independent runtime truth. It never mutates session JSONL.
Transfers are two-phase and generation-checked to prevent split brain.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore

RUNTIME = common.RUNTIME
REGISTRY = RUNTIME / "coordinators"
LOCK = RUNTIME / "coordinators.lock"
SCHEMA = 1
PREFERRED_CONNECTION = os.environ.get("CRAFT_COORDINATOR_CONNECTION", "chatgpt-plus")
PREFERRED_MODEL = os.environ.get("CRAFT_COORDINATOR_MODEL", "pi/gpt-5.6-sol")
DEFAULT_TTL = int(os.environ.get("CRAFT_COORDINATOR_TTL_SECONDS", "3600"))
FALLBACK_TTL = int(os.environ.get("CRAFT_FALLBACK_TTL_SECONDS", "3600"))
VALID_STATES = {"authoritative", "rotating", "hold", "superseded", "needs-owner"}
REPORTING_POLICY = RUNTIME / "reporting-policy.json"
REPORTING_POLICY_TOOL = HERE / "reporting-policy.py"
CURRENT_VERSION = "3.4.41"
# Retain already-admitted v3.4.35–v3.4.40 coordinators as readable/adoptable
# runtime records; new claims and successors use CURRENT_VERSION.
COMPATIBLE_COORDINATOR_VERSIONS = {"3.4.35", "3.4.36", "3.4.37", "3.4.38", "3.4.39", "3.4.40", CURRENT_VERSION}
ROTATION_GRACE_MS = int(os.environ.get("CRAFT_ROTATION_GRACE_SECONDS", "900")) * 1000
ROTATION_MESSAGE_HYSTERESIS = int(os.environ.get("CRAFT_ROTATION_MESSAGE_HYSTERESIS", "450"))
ROTATION_TOKEN_HYSTERESIS = int(os.environ.get("CRAFT_ROTATION_TOKEN_HYSTERESIS", "180000"))


# [<project>] Coordinator v<major>.<minor>.<patch> — nothing else, so the list the
# owner scans says project and protocol version on every row.
COORDINATOR_NAME = re.compile(r"^\[(?P<project>[a-z0-9][a-z0-9._-]{0,63})\] Coordinator v(?P<version>\d+\.\d+\.\d+)$")


def clean_project(raw: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-")
    if not value:
        raise SystemExit("invalid project slug")
    return value


def path_for(project: str) -> Path:
    return REGISTRY / f"{clean_project(project)}.json"


def load(project: str) -> dict[str, Any] | None:
    return common.read_json(path_for(project))


def active_records() -> list[dict[str, Any]]:
    return [value for path in sorted(REGISTRY.glob("*.json"))
            if (value := common.read_json(path)) and value.get("state") in {"authoritative", "rotating", "hold", "needs-owner"}]


def session_projects(session_id: str, exclude_project: str | None = None, include_pending: bool = False) -> list[str]:
    projects = []
    for row in active_records():
        project = str(row.get("project") or "")
        if exclude_project and project == exclude_project: continue
        owns = row.get("coordinatorSessionId") == session_id
        pending = include_pending and row.get("state") == "rotating" and row.get("successorSessionId") == session_id
        if owns or pending: projects.append(project)
    return sorted(set(projects))


def refuse_cross_project(session_id: str, project: str, include_pending: bool = False) -> None:
    conflicts = session_projects(session_id, exclude_project=project, include_pending=include_pending)
    if conflicts:
        print(json.dumps({"ok": False, "error": "cross-project-owner-refused", "sessionId": session_id,
                          "requestedProject": project, "conflictingProjects": conflicts}, indent=2))
        raise SystemExit(3)


def save(value: dict[str, Any]) -> None:
    value["schemaVersion"] = SCHEMA
    value["updatedAt"] = common.now_ms()
    common.atomic_json(path_for(str(value["project"])), value)


def manifest_or_die(sid: str) -> dict[str, Any]:
    manifest = common.read_manifest(sid)
    if not manifest:
        raise SystemExit(f"session manifest not found: {sid}")
    if not common.session_live(manifest):
        raise SystemExit(f"session is not live: {sid}")
    if common.role_of(manifest) != "coordinator":
        raise SystemExit(f"session is not a coordinator: {sid}")
    return manifest


def reporting_policy(required: bool = False) -> dict[str, Any]:
    row = common.read_json(REPORTING_POLICY)
    if not common.valid_reporting_policy(row):
        if required: raise SystemExit("v3.4.41 requires valid configured pull-only reporting policy")
        return {}
    fingerprint = __import__("hashlib").sha256(json.dumps({"mode": row.get("mode"), "ownerFacingSessionId": row.get("ownerFacingSessionId"), "configuredAt": row.get("configuredAt")}, sort_keys=True).encode()).hexdigest()
    return {"reportingMode": "pull-only", "reportingPolicyRevision": row.get("configuredAt"), "reportingPolicyFingerprint": fingerprint}


def reporting_audit(session: str) -> dict[str, Any]:
    """Run the explicit transcript detector; it is not outbound interception."""
    spec = importlib.util.spec_from_file_location("reporting_policy_admission", REPORTING_POLICY_TOOL)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)  # type: ignore
    with common.file_lock(module.LOCK):
        return module.audit_session(session)


def reporting_refusal(session: str) -> dict[str, Any] | None:
    result = reporting_audit(session)
    return result if not result.get("compliant") else None


def mark_reporting_violation(value: dict[str, Any], result: dict[str, Any]) -> None:
    """Remove healthy authority without destroying lanes or work evidence."""
    value["reportingViolation"] = {"detectedAt": common.now_ms(), "sessionId": result.get("sessionId"),
                                   "admissionBlocker": result.get("admissionBlocker"),
                                   "violations": result.get("violations", [])}
    value["state"] = "needs-owner"


def provider_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    connection = manifest.get("llmConnection")
    model = manifest.get("model")
    preferred = connection == PREFERRED_CONNECTION and model == PREFERRED_MODEL
    fallback_since = None if preferred else common.now_ms()
    return {
        "connection": connection, "model": model, "preferredProvider": preferred,
        "fallbackSince": fallback_since,
        "fallbackExpiresAt": None if fallback_since is None else fallback_since + FALLBACK_TTL * 1000,
    }


def cmd_claim(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    manifest = manifest_or_die(args.session)
    # Claim creates/replaces authority: it is always a current v3.4.41 admission,
    # never a loophole for malformed legacy successors.
    validate_successor_manifest(manifest, project, {"projectId": args.project_id or manifest.get("projectId")})
    reporting_policy(True)
    if reporting_refusal(args.session):
        raise SystemExit("unresolved-owner-reporting-violation")
    with common.file_lock(LOCK):
        refuse_cross_project(args.session, project, include_pending=True)
        current = load(project)
        if current and current.get("state") in {"authoritative", "rotating", "hold", "needs-owner"}:
            owner = str(current.get("coordinatorSessionId") or "")
            if owner != args.session and common.session_live(common.read_manifest(owner)):
                print(json.dumps({"ok": False, "error": "split-brain-refused", "current": current}, indent=2))
                return 3
            generation = int(current.get("generation") or 0) + (0 if owner == args.session else 1)
        else:
            generation = int((current or {}).get("generation") or 0) + 1
        now = common.now_ms()
        value = {
            "schemaVersion": SCHEMA, "project": project,
            "projectId": args.project_id or manifest.get("projectId"),
            "coordinatorCwd": common.coordinator_cwd(manifest.get("workingDirectory") or manifest.get("sdkCwd")),
            "coordinatorSessionId": args.session, "generation": generation,
            "state": "authoritative", "predecessorSessionId": args.predecessor,
            "successorSessionId": None, "claimedAt": now, "lastHeartbeatAt": now,
            "leaseExpiresAt": now + int(args.ttl) * 1000,
            "transferStartedAt": None, "fallbackReason": args.fallback_reason,
            "unresolvedGates": [], "activeChildren": [],
            "rotationMetric": {"sessionId": args.session, "startedAt": now, "pressureActive": False},
            **provider_fields(manifest),
            **reporting_policy(any(f"protocol-version::{version}" in set(manifest.get("labels") or [])
                                for version in COMPATIBLE_COORDINATOR_VERSIONS)),
        }
        save(value)
    print(json.dumps({"ok": True, "record": value}, ensure_ascii=False, indent=2))
    return 0


def require_owner(project: str, session: str) -> dict[str, Any]:
    value = load(project)
    if not value or value.get("coordinatorSessionId") != session:
        raise SystemExit(f"not authoritative owner for {project}: {session}")
    return value


def cmd_renew(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    with common.file_lock(LOCK):
        value = require_owner(project, args.session)
        violation = reporting_refusal(args.session)
        if violation:
            mark_reporting_violation(value, violation); save(value)
            raise SystemExit("unresolved-owner-reporting-violation; authority marked needs-owner for preservation-safe rotation")
        if value.get("state") == "needs-owner" and isinstance(value.get("reportingViolation"), dict):
            # Reporting quarantine is recoverable once a newer policy epoch makes
            # the same current coordinator compliant. Preserve all children,
            # generation, gates, and transfer state; only restore its authority.
            value["state"] = "authoritative"
            value.pop("reportingViolation", None)
        if value.get("state") not in {"authoritative", "hold"}:
            raise SystemExit(f"cannot renew state={value.get('state')}")
        # Renewal is the successful current-policy admission boundary. Refresh
        # only its reporting epoch tuple; recovery above intentionally preserves
        # state, generation, children, gates, and all transfer evidence.
        value.update(reporting_policy(True))
        now = common.now_ms(); value["lastHeartbeatAt"] = now; value["leaseExpiresAt"] = now + int(args.ttl) * 1000
        save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def latest_completed_assistant_at(session_id: str) -> int:
    path = common.SESSIONS / session_id / "session.jsonl"
    latest = 0
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            next(handle, None)  # header is metadata, not completed-turn evidence
            for line in handle:
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get("type") != "assistant" or event.get("isIntermediate"):
                    continue
                timestamp = int(event.get("timestamp") or 0)
                latest = max(latest, timestamp)
    except Exception:
        return 0
    return latest


def cmd_reconcile_rotation(args: argparse.Namespace) -> int:
    """Persist current-session pressure state so hysteresis survives watchdog ticks."""
    now = common.now_ms(); rows: list[dict[str, Any]] = []; changed: list[str] = []
    with common.file_lock(LOCK):
        for path in sorted(REGISTRY.glob("*.json")):
            value = common.read_json(path)
            if not value or value.get("state") not in {"authoritative", "rotating"}:
                continue
            session_id = str(value.get("coordinatorSessionId") or "")
            manifest = common.read_manifest(session_id)
            if not common.session_live(manifest) or common.role_of(manifest or {}) != "coordinator":
                continue
            violation = reporting_refusal(session_id)
            if violation:
                row = {"project": value.get("project"), "sessionId": session_id,
                       "admissionBlocker": violation.get("admissionBlocker")}
                rows.append(row)
                if args.apply:
                    mark_reporting_violation(value, violation); save(value)
                changed.append(str(value.get("project")))
                continue
            metric = rotation_metric(value, manifest or {}, now)
            compact = {"sessionId": metric["sessionId"], "startedAt": metric["startedAt"],
                       "pressureActive": metric["pressureActive"], "lastMeasuredAt": now,
                       "lastReasons": metric["reasons"]}
            rows.append({"project": value.get("project"), "sessionId": session_id, "metric": metric})
            if value.get("rotationMetric") != compact:
                value["rotationMetric"] = compact
                if args.apply:
                    save(value)
                changed.append(str(value.get("project")))
    print(json.dumps({"applied": args.apply, "changed": changed, "rows": rows}, ensure_ascii=False, indent=2)); return 0


def cmd_reconcile_activity(args: argparse.Namespace) -> int:
    now = common.now_ms(); rows = []; changed = []
    with common.file_lock(LOCK):
        for path in sorted(REGISTRY.glob("*.json")):
            value = common.read_json(path)
            if not value or value.get("state") != "authoritative":
                continue
            session_id = str(value.get("coordinatorSessionId") or "")
            # activeChildren is authoritative while a lane exists. Reconciliation
            # may prune only archived/absent IDs (plus malformed/duplicate entries);
            # it must retain every non-archived lane, including needs-review/done.
            raw_children = value.get("activeChildren") if isinstance(value.get("activeChildren"), list) else []
            original_children = [sid for sid in raw_children if isinstance(sid, str) and sid]
            retained_children: list[str] = []
            pruned_children: list[dict[str, str]] = [
                {"sessionId": str(sid), "reason": "invalid"} for sid in raw_children
                if not isinstance(sid, str) or not sid]
            seen: set[str] = set()
            for child_id in sorted(original_children):
                if child_id in seen:
                    pruned_children.append({"sessionId": child_id, "reason": "duplicate"})
                    continue
                seen.add(child_id)
                child = common.read_manifest(child_id)
                if child is None:
                    pruned_children.append({"sessionId": child_id, "reason": "absent"})
                elif child.get("isArchived"):
                    pruned_children.append({"sessionId": child_id, "reason": "archived"})
                else:
                    retained_children.append(child_id)
            children_changed = retained_children != raw_children
            if children_changed:
                value["activeChildren"] = retained_children
                value["activeChildrenReconciledAt"] = now
            manifest = common.read_manifest(session_id)
            if not common.session_live(manifest) or common.role_of(manifest) != "coordinator":
                row = {"project": value.get("project"), "sessionId": session_id,
                       "eligible": False, "admissionBlocker": "owner-not-live",
                       "retainedActiveChildren": retained_children, "prunedActiveChildren": pruned_children}
                if children_changed:
                    if args.apply:
                        save(value)
                    changed.append(str(value.get("project")))
                rows.append(row)
                continue
            violation = reporting_refusal(session_id)
            if violation:
                row = {"project": value.get("project"), "sessionId": session_id,
                       "eligible": False, "admissionBlocker": violation.get("admissionBlocker"),
                       "retainedActiveChildren": retained_children, "prunedActiveChildren": pruned_children}
                if args.apply:
                    mark_reporting_violation(value, violation); save(value); changed.append(str(value.get("project")))
                rows.append(row)
                continue
            activity_at = latest_completed_assistant_at(session_id)
            previous = int(value.get("lastHeartbeatAt") or 0)
            stored_ttl = int(value.get("leaseExpiresAt") or 0) - previous
            ttl_ms = stored_ttl if 60_000 <= stored_ttl <= 604_800_000 else int(args.ttl) * 1000
            eligible = activity_at > previous and now - activity_at <= ttl_ms
            row = {"project": value.get("project"), "sessionId": session_id,
                   "previousHeartbeatAt": previous, "activityAt": activity_at, "eligible": eligible,
                   "retainedActiveChildren": retained_children, "prunedActiveChildren": pruned_children}
            if eligible:
                value["lastHeartbeatAt"] = activity_at
                value["leaseExpiresAt"] = activity_at + ttl_ms
                value["activityRenewedAt"] = now
                value["activityEvidenceAt"] = activity_at
                row["leaseExpiresAt"] = value["leaseExpiresAt"]
            if eligible or children_changed:
                if args.apply:
                    save(value)
                changed.append(str(value.get("project")))
            rows.append(row)
    changed = sorted(set(changed))
    print(json.dumps({"applied": args.apply, "changed": changed, "rows": rows}, ensure_ascii=False, indent=2))
    return 0


def validate_successor_manifest(manifest: dict[str, Any], project: str, record: dict[str, Any]) -> None:
    raw_labels = manifest.get("labels")
    labels = raw_labels if isinstance(raw_labels, list) and all(isinstance(x, str) for x in raw_labels) else []
    # Existing v3.4.35–v3.4.40 coordinators remain admissible during rollout. New v3.4.41
    # coordinators use the current name/label pair; mixed or duplicated identities
    # are never accepted.
    roles = [x for x in labels if x.startswith("agent-role::")]
    projects = [x for x in labels if x.startswith("project::")]
    protocols = [x for x in labels if x.startswith("protocol-version::")]
    version = protocols[0].split("::", 1)[1] if len(protocols) == 1 else None
    expected_name = f"[{project}] Coordinator v{version}" if version in COMPATIBLE_COORDINATOR_VERSIONS else None
    if (manifest.get("name") != expected_name or "coordinators" not in labels
            or roles != ["agent-role::coordinator"] or projects != [f"project::{project}"]
            or version not in COMPATIBLE_COORDINATOR_VERSIONS):
        raise SystemExit("successor canonical coordinator identity mismatch")
    if (manifest.get("projectId") != record.get("projectId")
            or manifest.get("llmConnection") != PREFERRED_CONNECTION
            or manifest.get("model") != PREFERRED_MODEL
            or manifest.get("permissionMode") not in {"allow-all", "execute"}):
        raise SystemExit("successor project/provider identity mismatch")
    cwd = manifest.get("workingDirectory") or manifest.get("sdkCwd")
    canonical_cwd = common.coordinator_cwd(cwd)
    if canonical_cwd is None or not os.path.isdir(canonical_cwd):
        raise SystemExit("successor working directory is invalid")
    expected_cwd = record.get("coordinatorCwd")
    if expected_cwd and canonical_cwd != expected_cwd:
        raise SystemExit("successor working directory differs from authoritative repository")


def transfer_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    """Immutable fields that prove the exact successor selected at transfer start."""
    return {"id": manifest.get("id"), "workspaceRootPath": manifest.get("workspaceRootPath"),
            "projectId": manifest.get("projectId"), "role": common.role_of(manifest),
            "connection": manifest.get("llmConnection"), "model": manifest.get("model")}


def rotation_metric(record: dict[str, Any], manifest: dict[str, Any], now: int) -> dict[str, Any]:
    """Measure only the current coordinator session; grace never masks context errors.

    Counters in a successor's manifest are its own counters.  The durable metric is
    rebound at claim/accept, starts with a bounded grace interval, and keeps an
    already-raised signal until it falls below lower clear thresholds.  A recorded
    request-buffer/context error bypasses both grace and hysteresis.
    """
    session = str(record.get("coordinatorSessionId") or "")
    metric = record.get("rotationMetric") if isinstance(record.get("rotationMetric"), dict) else {}
    started = int(metric.get("startedAt") or record.get("transferAcceptedAt") or record.get("claimedAt") or now)
    same_session = metric.get("sessionId") == session
    messages = manifest.get("messageCount")
    tokens = (manifest.get("tokenUsage") or {}).get("totalTokens") if isinstance(manifest.get("tokenUsage"), dict) else None
    max_messages = int(os.environ.get("CRAFT_COORDINATOR_MAX_MESSAGES", "500"))
    max_tokens = int(os.environ.get("CRAFT_COORDINATOR_MAX_TOKENS", "200000"))
    error_text = " ".join(str(manifest.get(k) or "") for k in ("error", "lastError", "sessionError", "contextError")).lower()
    context_error = any(marker in error_text for marker in ("context", "request buffer", "token limit", "maximum tokens", "provider", "connection", "identity mismatch", "sigterm"))
    prior_active = bool(metric.get("pressureActive")) and same_session
    message_pressure = isinstance(messages, int) and messages >= (ROTATION_MESSAGE_HYSTERESIS if prior_active else max_messages)
    token_pressure = isinstance(tokens, int) and tokens >= (ROTATION_TOKEN_HYSTERESIS if prior_active else max_tokens)
    in_grace = same_session and now < started + ROTATION_GRACE_MS
    reasons: list[str] = []
    if context_error:
        reasons.append("context-error")
    if not in_grace or context_error:
        if message_pressure: reasons.append(f"messages={messages}")
        if token_pressure: reasons.append(f"tokens={tokens}")
    return {"sessionId": session, "startedAt": started, "sameCurrentSession": same_session,
            "graceUntil": started + ROTATION_GRACE_MS, "inGrace": in_grace,
            "messages": messages if isinstance(messages, int) else None,
            "tokens": tokens if isinstance(tokens, int) else None,
            "pressureActive": bool(reasons), "reasons": reasons,
            "contextError": context_error,
            "messageClearThreshold": ROTATION_MESSAGE_HYSTERESIS,
            "tokenClearThreshold": ROTATION_TOKEN_HYSTERESIS}


def transfer_child_cwd(manifest: dict[str, Any]) -> str:
    """Return an unambiguous, canonical, existing child worktree or refuse.

    Child manifests may use either historical CWD field, but an asserted field is
    never allowed to be silently ignored.  A transfer is an authority boundary:
    resolving a relative path against this process, accepting a dangling path, or
    choosing between disagreeing fields could attach a live lane to the wrong
    repository.
    """
    values = [manifest.get(field) for field in ("workingDirectory", "sdkCwd") if field in manifest]
    if not values:
        raise SystemExit("transfer child working directory missing")
    canonical: set[str] = set()
    for raw in values:
        cwd = common.coordinator_cwd(raw)
        # ``coordinator_cwd`` accepts only absolute or protocol-trusted ``~/``
        # syntax and returns its real canonical identity.  Compare identities,
        # not spelling: a worker manifest may legitimately preserve ``~/work``.
        if cwd is None or not os.path.isdir(cwd):
            raise SystemExit("transfer child working directory is not canonical and existing")
        canonical.add(cwd)
    if len(canonical) != 1:
        raise SystemExit("transfer child working directory fields disagree")
    return canonical.pop()


def discover_transfer_children(predecessor: str, project: str, existing: list[Any]) -> list[str]:
    """Adopt only exact live predecessor lanes; registry IDs are never trusted.

    ``activeChildren`` is persisted coordinator state, not an authority to revive
    an archived, foreign, terminal, colliding, or duplicate lane.  Every retained
    ID is re-derived from its manifest and must pass the same immutable binding as
    a newly discovered child.  A stale/missing lease is deliberately irrelevant.
    """
    manifests = common.all_manifests()
    requested = {str(value) for value in existing if isinstance(value, str) and value}
    candidates = {sid for sid, manifest in manifests.items()
                  if (common.label_value(manifest, "parent-session::") or manifest.get("parentSessionId")) == predecessor}
    # A persisted ID not rediscovered as a precisely predecessor-bound manifest is
    # corruption, not something a successor may silently adopt or discard.
    stale = requested - candidates
    if stale:
        raise SystemExit("transfer activeChildren contain missing-or-foreign lane")
    seen_attempts: set[tuple[str, str]] = set()
    seen_cwds: dict[str, str] = {}
    predecessor_manifest = common.read_manifest(predecessor) or {}
    predecessor_cwd = common.coordinator_cwd(predecessor_manifest.get("workingDirectory") or predecessor_manifest.get("sdkCwd"))
    if predecessor_cwd is None or not os.path.isdir(predecessor_cwd):
        raise SystemExit("transfer predecessor working directory is invalid")
    adopted: list[str] = []
    for session_id in sorted(candidates):
        manifest = manifests[session_id]
        if not common.session_live(manifest) or common.role_of(manifest) not in {"worker", "auditor"}:
            if session_id in requested:
                raise SystemExit("transfer activeChildren contain inactive-or-archived lane")
            continue
        if common.label_value(manifest, "project::") != project:
            raise SystemExit("transfer child project binding mismatch")
        unit = common.label_value(manifest, "work-unit::")
        attempt = common.label_value(manifest, "attempt::")
        if not unit or not attempt:
            raise SystemExit("transfer child immutable work-unit/attempt binding missing")
        identity = (unit, attempt)
        if identity in seen_attempts:
            raise SystemExit("duplicate live transfer child work-unit/attempt")
        seen_attempts.add(identity)
        cwd = transfer_child_cwd(manifest)
        if cwd == predecessor_cwd or cwd in seen_cwds:
            raise SystemExit("transfer child shared worktree refusal")
        seen_cwds[cwd] = session_id
        adopted.append(session_id)
    return adopted


def cmd_begin_transfer(args: argparse.Namespace) -> int:
    project = clean_project(args.project); successor = manifest_or_die(args.successor)
    identity = transfer_identity(successor)
    validate_successor_manifest(successor, project, load(project) or {})
    reporting_policy(True)
    if reporting_refusal(args.successor):
        raise SystemExit("unresolved-owner-reporting-violation")
    # A coordinator is project-bound.  Do not defer this proof until after the
    # predecessor has entered rotating state.
    if successor.get("projectId") != (load(project) or {}).get("projectId"):
        raise SystemExit("successor native project binding mismatch")
    with common.file_lock(LOCK):
        refuse_cross_project(args.successor, project, include_pending=True)
        value = require_owner(project, args.session)
        source_violation = reporting_refusal(args.session)
        if source_violation:
            # The reporting violator may only use this path to hand work over to a
            # clean successor; it may not renew or present healthy authority.
            mark_reporting_violation(value, source_violation)
        if value.get("state") == "rotating":
            if value.get("successorSessionId") == args.successor:
                print(json.dumps({"ok": True, "record": value, "idempotent": True}, indent=2)); return 0
            raise SystemExit("transfer already open")
        if value.get("state") == "hold":
            raise SystemExit("project HOLD blocks transfer")
        value["state"] = "rotating"; value["successorSessionId"] = args.successor
        value["transferStartedAt"] = common.now_ms(); value["transferReason"] = args.reason
        value["successorIdentity"] = identity
        save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def cmd_accept_transfer(args: argparse.Namespace) -> int:
    project = clean_project(args.project); manifest = manifest_or_die(args.session)
    with common.file_lock(LOCK):
        refuse_cross_project(args.session, project, include_pending=True)
        value = load(project)
        if not value or value.get("state") != "rotating" or value.get("successorSessionId") != args.session:
            raise SystemExit("no matching open transfer")
        if args.expected_generation is not None and int(value.get("generation") or 0) != args.expected_generation:
            raise SystemExit("generation mismatch")
        validate_successor_manifest(manifest, project, value)
        policy = reporting_policy(True)
        if reporting_refusal(args.session):
            raise SystemExit("unresolved-owner-reporting-violation")
        expected_identity = value.get("successorIdentity")
        if not isinstance(expected_identity, dict):
            raise SystemExit("transfer identity admission missing; begin a new transfer")
        if transfer_identity(manifest) != expected_identity:
            raise SystemExit("successor transfer identity mismatch")
        predecessor = value.get("coordinatorSessionId"); now = common.now_ms()
        adopted_children = discover_transfer_children(str(predecessor or ""), project, list(value.get("activeChildren") or []))
        value.update({"coordinatorSessionId": args.session, "coordinatorCwd": common.coordinator_cwd(manifest.get("workingDirectory") or manifest.get("sdkCwd")), "predecessorSessionId": predecessor,
                      "successorSessionId": None, "generation": int(value.get("generation") or 0) + 1,
                      "activeChildren": adopted_children, "transferDiscovery": {"predecessorSessionId": predecessor,
                      "adoptedChildren": adopted_children, "discoveredAt": now},
                      "rotationMetric": {"sessionId": args.session, "startedAt": now, "pressureActive": False},
                      "state": "authoritative", "claimedAt": now, "lastHeartbeatAt": now,
                      "leaseExpiresAt": now + int(args.ttl) * 1000, "transferAcceptedAt": now,
                      "transferStartedAt": None, "transferIdentityAcceptedAt": now,
                      "rotationAuthority": None, "rotationReason": None, "rotationRequestedAt": None,
                      "priorFallbackReason": value.get("fallbackReason"),
                      "fallbackReason": None, **provider_fields(manifest), **policy})
        save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def cmd_request_rotation(args: argparse.Namespace) -> int:
    """Record a direct-owner rotation request for complex recovery to consume."""
    if args.authority != "direct-owner":
        raise SystemExit("direct-owner authority required")
    project = clean_project(args.project)
    with common.file_lock(LOCK):
        value = load(project)
        if not value or value.get("state") not in {"authoritative", "rotating"}:
            raise SystemExit("project has no coordinator eligible for rotation")
        value.update({"rotationAuthority": "direct-owner", "rotationReason": args.reason,
                      "rotationRequestedAt": common.now_ms()})
        save(value)
    print(json.dumps({"ok": True, "record": value}, ensure_ascii=False, indent=2)); return 0


def cmd_hold(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    with common.file_lock(LOCK):
        value = require_owner(project, args.session)
        value["state"] = "hold"; value["holdReason"] = args.reason; value["holdAt"] = common.now_ms(); save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def cmd_resume(args: argparse.Namespace) -> int:
    if args.authorization != "RESUME":
        raise SystemExit("exact authorization RESUME required")
    project = clean_project(args.project)
    with common.file_lock(LOCK):
        value = load(project)
        if not value or value.get("state") != "hold": raise SystemExit("project is not HOLD")
        value["state"] = "authoritative"; value["resumedAt"] = common.now_ms(); value["resumeAuthority"] = "direct-owner"
        save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def inspect_one(project: str) -> dict[str, Any]:
    project = clean_project(project)
    value = load(project) or {"project": project, "state": "missing"}
    manifest = common.read_manifest(str(value.get("coordinatorSessionId") or ""))
    now = common.now_ms(); issues: list[str] = []
    if value.get("state") != "missing" and not common.session_live(manifest): issues.append("owner-not-live")
    duplicates = session_projects(str(value.get("coordinatorSessionId") or ""), exclude_project=project)
    if duplicates: issues.append("cross-project-owner:" + ",".join(duplicates))
    if value.get("state") != "hold" and value.get("leaseExpiresAt") is not None and now > int(value["leaseExpiresAt"]): issues.append("coordinator-lease-stale")
    if value.get("fallbackExpiresAt") is not None and now > int(value["fallbackExpiresAt"]): issues.append("fallback-ttl-expired")
    predecessor = str(value.get("predecessorSessionId") or "")
    if predecessor and value.get("state") in {"authoritative", "rotating"}:
        pred_manifest = common.read_manifest(predecessor)
        # A completed handoff ends with the successor archiving the predecessor;
        # a lingering live predecessor is untracked housekeeping debt.
        if pred_manifest and not pred_manifest.get("isArchived"):
            issues.append(f"predecessor-not-archived:{predecessor}")
    if manifest:
        try:
            violation = reporting_refusal(str(value.get("coordinatorSessionId") or ""))
        except SystemExit:
            violation = {"admissionBlocker": "owner-reporting-transcript-unavailable"}
        if violation:
            issues.append(str(violation.get("admissionBlocker") or "unresolved-owner-reporting-violation"))
        raw_labels = manifest.get("labels")
        if isinstance(raw_labels, list):
            roles = [x for x in raw_labels if isinstance(x, str) and x.startswith("agent-role::")]
            projects = [x for x in raw_labels if isinstance(x, str) and x.startswith("project::")]
            protocols = [x for x in raw_labels if isinstance(x, str) and x.startswith("protocol-version::")]
            version = protocols[0].split("::", 1)[1] if len(protocols) == 1 else None
            if version in COMPATIBLE_COORDINATOR_VERSIONS and (roles != ["agent-role::coordinator"]
                    or projects != [f"project::{project}"] or len(protocols) != 1):
                issues.append("canonical-coordinator-identity-mismatch")
            policy = reporting_policy(False)
            if not policy or any(value.get(k) != v for k, v in policy.items()): issues.append("owner-reporting-policy-drift")
        # A coordinator parked in a worker-terminal session status is deaf to queued
        # admission wakes until a direct owner message; that is role drift, not rest.
        if (value.get("state") in {"authoritative", "rotating"}
                and manifest.get("sessionStatus") in {"needs-review", "done"}):
            issues.append(f"coordinator-worker-terminal-status:{manifest.get('sessionStatus')}")
        # Rotation pressure is measured against this session only. A grace window
        # prevents a fresh successor inheriting a stale warning, while a real
        # context/request-buffer error bypasses grace and hysteresis.
        if value.get("state") in {"authoritative", "rotating"}:
            pressure = rotation_metric(value, manifest, now)
            for reason in pressure["reasons"]:
                issues.append(f"coordinator-complexity-threshold:{reason}")
        if manifest.get("projectId") != value.get("projectId"): issues.append("native-project-binding-drift")
        # A coordinator's session name is how the owner finds it among hundreds.
        # Successors are spawned by their predecessor, which named them whatever it
        # liked — "l2 client", "Coordinator Handoff", "Coordinator Lifecycle
        # Protocol" — so the owner's coordinator list stopped saying which project
        # or protocol anything belonged to. The format is fixed and checkable.
        name = str(manifest.get("name") or "")
        if not COORDINATOR_NAME.fullmatch(name):
            issues.append("coordinator-name-nonconforming")
        elif COORDINATOR_NAME.fullmatch(name).group("project") != project:
            issues.append("coordinator-name-project-mismatch")
        if manifest.get("llmConnection") != value.get("connection") or manifest.get("model") != value.get("model"): issues.append("provider-record-drift")
        labels = set(manifest.get("labels") or [])
        for required in ("coordinators", "agent-role::coordinator"):
            if required not in labels: issues.append(f"missing-label:{required}")
        if not any(label == "protocol-version::3" or label.startswith("protocol-version::3.") for label in labels):
            issues.append("missing-label:protocol-version::3.x")
    pressure = rotation_metric(value, manifest, now) if manifest and value.get("state") in {"authoritative", "rotating"} else None
    maintenance_debt = [issue for issue in issues if issue.startswith("predecessor-not-archived:")]
    return {"record": value, "rotationMetric": pressure, "issues": issues,
            "maintenanceDebt": maintenance_debt, "healthy": not issues}


def cmd_inspect(args: argparse.Namespace) -> int:
    result = inspect_one(args.project); print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["healthy"] else 2


def cmd_validate(_: argparse.Namespace) -> int:
    REGISTRY.mkdir(parents=True, exist_ok=True)
    rows = [inspect_one(path.stem) for path in sorted(REGISTRY.glob("*.json"))]
    print(json.dumps({"healthy": all(r["healthy"] for r in rows), "projects": rows}, ensure_ascii=False, indent=2))
    return 0 if all(r["healthy"] for r in rows) else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("claim"); c.add_argument("--project", required=True); c.add_argument("--session", required=True); c.add_argument("--project-id"); c.add_argument("--predecessor"); c.add_argument("--fallback-reason"); c.add_argument("--ttl", type=int, default=DEFAULT_TTL); c.set_defaults(func=cmd_claim)
    r = sub.add_parser("renew"); r.add_argument("--project", required=True); r.add_argument("--session", required=True); r.add_argument("--ttl", type=int, default=DEFAULT_TTL); r.set_defaults(func=cmd_renew)
    rr_metric = sub.add_parser("reconcile-rotation"); rr_metric.add_argument("--apply", action="store_true"); rr_metric.set_defaults(func=cmd_reconcile_rotation)
    ra = sub.add_parser("reconcile-activity"); ra.add_argument("--ttl", type=int, default=DEFAULT_TTL); ra.add_argument("--apply", action="store_true"); ra.set_defaults(func=cmd_reconcile_activity)
    b = sub.add_parser("begin-transfer"); b.add_argument("--project", required=True); b.add_argument("--session", required=True); b.add_argument("--successor", required=True); b.add_argument("--reason", required=True); b.set_defaults(func=cmd_begin_transfer)
    a = sub.add_parser("accept-transfer"); a.add_argument("--project", required=True); a.add_argument("--session", required=True); a.add_argument("--expected-generation", type=int); a.add_argument("--ttl", type=int, default=DEFAULT_TTL); a.set_defaults(func=cmd_accept_transfer)
    rr = sub.add_parser("request-rotation"); rr.add_argument("--project", required=True); rr.add_argument("--authority", required=True); rr.add_argument("--reason", required=True); rr.set_defaults(func=cmd_request_rotation)
    h = sub.add_parser("hold"); h.add_argument("--project", required=True); h.add_argument("--session", required=True); h.add_argument("--reason", required=True); h.set_defaults(func=cmd_hold)
    u = sub.add_parser("resume"); u.add_argument("--project", required=True); u.add_argument("--authorization", required=True); u.set_defaults(func=cmd_resume)
    i = sub.add_parser("inspect"); i.add_argument("--project", required=True); i.set_defaults(func=cmd_inspect)
    v = sub.add_parser("validate"); v.set_defaults(func=cmd_validate)
    return p

if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(args.func(args))

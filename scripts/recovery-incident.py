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
EXTERNAL_WAITS = RUNTIME / "external-waits"
SELF_HEALING = RUNTIME / "self-healing"
LOCK = RUNTIME / "recovery-incidents.lock"
CONTROLLER = SELF_HEALING / "controller.json"
DISABLED = RUNTIME / "self-healing.disabled"
SCHEMA = 1
TRANSFER_STUCK_SECONDS = int(os.environ.get("CRAFT_TRANSFER_STUCK_SECONDS", "1800"))
PREDECESSOR_ARCHIVE_GRACE_SECONDS = int(os.environ.get("CRAFT_PREDECESSOR_ARCHIVE_GRACE_SECONDS", "900"))
ORPHANED_LANE_SECONDS = int(os.environ.get("CRAFT_ORPHANED_LANE_SECONDS", "86400"))
KILL_SWITCH_STALE_SECONDS = int(os.environ.get("CRAFT_KILL_SWITCH_STALE_SECONDS", "1800"))
UNREGISTERED_CHILD_SECONDS = int(os.environ.get("CRAFT_UNREGISTERED_CHILD_SECONDS", "600"))
# Draining the ledger is ordered by what stops product work, not by arrival.
# Observed live: 23 cwd-collision and 10 orphaned-lane records sat in one queue
# with four idle finished workers and a coordinator lease stale for 67 minutes,
# and the housekeeping noise crowded out everything that mattered.
SAFETY_KINDS = {"ambiguous-coordinator-owner", "project-mapping-conflict"}
# Conditions that stop the pipeline right now: a finished worker nobody collects,
# a coordinator that cannot own or publish, a wait nobody observes. Recovering an
# old stalled lane matters too, but it does not unblock a queue the way these do.
# An executor idle right now outranks a bookkeeping mismatch. Measured live: with a
# three-action turn budget, status contradictions and overdue commitments consumed
# every turn while two finished workers waited 25 and 30 minutes to be collected.
IDLE_EXECUTOR_KINDS = {"terminal-handoff-unconsumed", "coordinator-not-live", "coordinator-lease-stale",
                       "coordinator-session-error", "coordinator-pi-sigterm", "transfer-stuck",
                       "coordinator-worker-terminal-status", "unregistered-child-lane",
                       "heavy-lock-wait", "external-wait-terminal", "external-wait-unobserved"}
BOOKKEEPING_KINDS = {"coordinator-status-missing", "coordinator-status-stale",
                     "coordinator-status-contradiction", "coordinator-plan-unexecutable",
                     "coordinator-inbox-ready", "coordinator-commitment-overdue",
                     "external-wait-deadline"}
LANE_RECOVERY_KINDS = {"worker-suspect", "worker-stalled", "worker-error"}
HOUSEKEEPING_KINDS = {"cwd-collision", "orphaned-dead-lane", "preservation-unknown",
                      "owner-gate-blocked", "job-exit-unreported", "predecessor-unarchived"}
HOUSEKEEPING_QUOTA = int(os.environ.get("CRAFT_DRAIN_HOUSEKEEPING_QUOTA", "1"))
DRAIN_LIMIT = int(os.environ.get("CRAFT_DRAIN_LIMIT", "3"))
CONTROLLER_SILENT_SECONDS = int(os.environ.get("CRAFT_CONTROLLER_SILENT_SECONDS", "1800"))
PERSISTENT_CONTROLLER = SELF_HEALING / "persistent-controller.json"
TRANSPORT = SELF_HEALING / "transport.json"
TRANSPORT_LOST_SECONDS = int(os.environ.get("CRAFT_TRANSPORT_LOST_SECONDS", "900"))
HOST_SATURATION_RATIO = float(os.environ.get("CRAFT_HOST_SATURATION_RATIO", "2.5"))
HARNESSES = RUNTIME / "controller-harnesses"
CLAIM_TTL_SECONDS = int(os.environ.get("CRAFT_RECOVERY_CLAIM_TTL_SECONDS", "900"))
MAX_ATTEMPTS = int(os.environ.get("CRAFT_RECOVERY_MAX_ATTEMPTS", "2"))
COORDINATOR_MAX_ATTEMPTS = int(os.environ.get("CRAFT_COORDINATOR_RECOVERY_MAX_ATTEMPTS", "3"))
MAX_CONTROLLER_RUNTIME_SECONDS = int(os.environ.get("CRAFT_RECOVERY_CONTROLLER_MAX_RUNTIME_SECONDS", "900"))
CLEAR_CONFIRM_SECONDS = int(os.environ.get("CRAFT_RECOVERY_CLEAR_CONFIRM_SECONDS", "300"))
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
ACTION_MATRIX = {
    "coordinator-not-live": ["verify-session-absent", "respawn-from-handoff-snapshot", "owner-escalation"],
    "coordinator-lease-stale": ["wake-coordinator", "renew-request", "bridge-rotation-after-two-failures"],
    "coordinator-session-error": ["wake-coordinator", "preserve-snapshot", "bridge-rotation-on-attempt-3"],
    "coordinator-pi-sigterm": ["wake-coordinator", "renew-request", "preserve-snapshot", "bridge-rotation-on-attempt-3"],
    "coordinator-worker-terminal-status": ["wake-coordinator", "preserve-snapshot", "bridge-rotation-on-attempt-3"],
    "predecessor-unarchived": ["wake-coordinator", "verify-preservation", "archive-reap-if-proven"],
    "orphaned-dead-lane": ["verify-worktree-clean", "archive-reap-if-clean", "owner-escalation-if-dirty"],
    "unregistered-child-lane": ["wake-coordinator", "require-lease-registration", "release-slot-if-abandoned"],
    "controller-silent": ["inspect-admission-lane", "clear-deferred-probe", "owner-escalation"],
    "fallback-ttl-expired": ["codex-repatriation"],
    "transfer-stuck": ["inspect-transfer", "wake-coordinator", "owner-escalation"],
    "worker-suspect": ["inspect-progress", "wake-coordinator"],
    "worker-stalled": ["inspect-progress", "preserve", "archive-reap-if-proven", "request-replacement"],
    "worker-error": ["inspect-error", "preserve", "archive-reap-if-proven", "request-replacement"],
    "terminal-handoff-unconsumed": ["wake-coordinator", "verify-preservation", "archive-reap-if-proven", "release-slot"],
    "job-exit-unreported": ["inspect-receipt", "wake-coordinator"],
    "heavy-lock-wait": ["ack-receipt", "queue-after-lock", "wake-coordinator"],
    "external-wait-terminal": ["inspect-receipt", "wake-coordinator", "continue-after-proved-success"],
    "external-wait-unobserved": ["wake-coordinator", "attach-observer", "fail-closed"],
    "external-wait-deadline": ["wake-coordinator", "inspect-external-state", "bounded-correction"],
    "cwd-collision": ["hard-refusal", "owner-escalation"],
    "project-mapping-conflict": ["hard-refusal", "owner-escalation"],
    "ambiguous-coordinator-owner": ["hard-refusal", "owner-escalation"],
    "preservation-unknown": ["verify-preservation", "hard-refusal-until-proven"],
    "owner-gate-blocked": ["report-only"],
    # Protocol v3.3.0 coordinator inbox / product status / commitment incidents.
    "coordinator-inbox-ready": ["wake-coordinator", "consume-digest"],
    "coordinator-status-missing": ["wake-coordinator", "publish-status"],
    "coordinator-status-stale": ["wake-coordinator", "publish-status"],
    "coordinator-plan-unexecutable": ["wake-coordinator", "publish-executable-plan"],
    "coordinator-commitment-overdue": ["wake-coordinator", "resolve-commitment"],
    "coordinator-status-contradiction": ["wake-coordinator", "reconcile-status"],
}

# Protocol v3.3.0 siblings expose deterministic, importable observation feeds.
def _load_sibling(name):
    spec_s = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec_s); spec_s.loader.exec_module(module)  # type: ignore
    return module

inbox_mod = _load_sibling("coordinator-inbox")
status_mod = _load_sibling("coordinator-status")
commitment_mod = _load_sibling("coordinator-commitment")

def now_ms(): return common.now_ms()
def fail(message): raise SystemExit(message)
def terminal_session_error(manifest):
    """Return an unresolved terminal coordinator error, never an in-turn tool error.

    Current Craft manifests retain the last failed completion even after a later
    successful turn.  A coordinator needs a wake only when that failed completion
    is newer than every successful final response and is still the latest completed
    message.  Older manifests may expose only sessionStatus/lastError.
    """
    if not manifest:
        return None
    error_at = int(manifest.get("lastCompletedErrorMessageAt") or 0)
    final_at = int(manifest.get("lastCompletedFinalMessageAt") or 0)
    error_id = manifest.get("lastCompletedErrorMessageId")
    completed_id = manifest.get("lastCompletedMessageId")
    completed_at = int(manifest.get("lastCompletedAt") or 0)
    if error_at and error_at > final_at and (
        (error_id and completed_id == error_id) or
        (manifest.get("lastMessageRole") == "error" and error_at >= completed_at)
    ):
        return {"errorAt": error_at, "errorMessageId": error_id,
                "errorClass": "terminal-completion-error"}
    if manifest.get("sessionStatus") == "error" or manifest.get("lastError"):
        return {"errorAt": int(manifest.get("updatedAt") or error_at or 0),
                "errorMessageId": error_id, "errorClass": "manifest-terminal-error"}
    return None
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
    owner_projects = {}
    for project, row in records.items():
        if row.get("state") not in {"authoritative", "rotating", "hold", "needs-owner"}: continue
        sid = str(row.get("coordinatorSessionId") or "")
        if sid: owner_projects.setdefault(sid, set()).add(project)
    owners = {sid: next(iter(projects)) for sid, projects in owner_projects.items() if len(projects) == 1}
    ambiguous = {sid: sorted(projects) for sid, projects in owner_projects.items() if len(projects) > 1}
    roots = {}
    for sid, project in owners.items():
        manifest = common.read_manifest(sid) or {}
        cwd = common.expand_path(manifest.get("workingDirectory") or manifest.get("sdkCwd"))
        if cwd: roots[cwd] = project
    return records, owners, roots, ambiguous

def project_for_child(lease, manifest, owners, roots, ambiguous):
    parent = str(lease.get("parentSessionId") or "")
    if parent in ambiguous: return None
    if parent in owners: return owners[parent]
    explicit = common.label_value(manifest, "project::")
    if explicit: return explicit
    cwd = lease.get("worktree") or manifest.get("workingDirectory") or manifest.get("sdkCwd")
    for root, project in roots.items():
        if path_within(cwd, root): return project
    return None

def observation(kind, severity, project, session_id, evidence, *, fingerprint_evidence=None, **extra):
    """Build an observation with a stable, meaningful condition revision.

    Human-facing evidence may include volatile diagnostic counters/ages. They
    must never enter ``evidenceFingerprint`` because admission idempotency is
    keyed by this value. Callers with volatile evidence provide the immutable
    condition identity explicitly through ``fingerprint_evidence``.
    """
    slot = session_id or str(extra.get("gateId") or "none")
    discriminator = extra.pop("discriminator", None)
    parts = (project or "global", kind, slot) + ((str(discriminator),) if discriminator else ())
    key = ":".join(parts)
    stable_evidence = evidence if fingerprint_evidence is None else fingerprint_evidence
    return {"stableKey": key, "incidentId": incident_id(key), "kind": kind,
            "severity": severity, "project": project, "sessionId": session_id,
            "evidence": evidence, "evidenceFingerprint": fingerprint(stable_evidence),
            "allowedActions": ACTION_MATRIX.get(kind, ["report-only"]), **extra}

def collect_observations():
    now = now_ms(); records, owners, roots, ambiguous = project_context(); manifests = common.all_manifests(); out = []
    leased = {p.stem for p in (RUNTIME / "worker-leases").glob("*.json")}
    for sid, row in sorted(manifests.items()):
        parent = str(row.get("parentSessionId") or "")
        project = owners.get(parent)
        if not project or sid in leased or row.get("isArchived") or sid in records:
            continue
        created = row.get("createdAt") if isinstance(row.get("createdAt"), int) else None
        if created is None or now - created <= UNREGISTERED_CHILD_SECONDS * 1000:
            continue
        # An executor with no lease is invisible to every machine check at once:
        # idle-ready detection, dead-lane detection, watchdog liveness, worktree
        # uniqueness, preservation proof and archivable backlog all miss it.
        # Observed live: six such children across three projects, one of them
        # running the owner-authorized correction attempt.
        stable = {"parentSessionId": parent, "sessionName": row.get("name")}
        out.append(observation("unregistered-child-lane", "high", project, sid,
            {**stable, "sessionStatus": row.get("sessionStatus"), "ageMs": now - created},
            fingerprint_evidence=stable, coordinatorSessionId=parent))
    for sid, projects in sorted(ambiguous.items()):
        out.append(observation("ambiguous-coordinator-owner", "critical", None, sid,
            {"projects": projects, "errorClass": "cross-project-owner"}))
    for project, record in records.items():
        sid = str(record.get("coordinatorSessionId") or ""); manifest = manifests.get(sid)
        if sid in ambiguous: continue
        if record.get("state") != "hold" and not common.session_live(manifest):
            out.append(observation("coordinator-not-live", "critical", project, sid,
                {"registryState": record.get("state"), "manifestPresent": bool(manifest), "archived": (manifest or {}).get("isArchived")}))
        expiry = record.get("leaseExpiresAt")
        if record.get("state") != "hold" and expiry is not None and now > int(expiry):
            stable_lease = {"leaseExpiresAt": int(expiry), "lastHeartbeatAt": record.get("lastHeartbeatAt"),
                            "generation": record.get("generation")}
            out.append(observation("coordinator-lease-stale", "high", project, sid,
                {**stable_lease, "agePastExpiryMs": now-int(expiry)}, fingerprint_evidence=stable_lease))
        fallback = record.get("fallbackExpiresAt")
        if fallback is not None and now > int(fallback):
            out.append(observation("fallback-ttl-expired", "high", project, sid,
                {"connection": record.get("connection"), "model": record.get("model"), "fallbackExpiresAt": fallback}))
        if record.get("state") == "rotating":
            started = int(record.get("transferStartedAt") or 0)
            if started and now-started > TRANSFER_STUCK_SECONDS*1000:
                out.append(observation("transfer-stuck", "critical", project, sid,
                    {"transferStartedAt": started, "pendingSessionId": record.get("pendingSessionId"), "generation": record.get("generation")}))
        session_status = (manifest or {}).get("sessionStatus")
        if record.get("state") in {"authoritative", "rotating"} and session_status in {"needs-review", "done"}:
            # A coordinator parked in a worker-terminal session status is deaf to
            # queued admission wakes (role drift observed live on 2026-08-13); a
            # direct wake is futile, so the controller owns the recovery stages.
            out.append(observation("coordinator-worker-terminal-status", "high", project, sid,
                {"sessionStatus": session_status, "generation": record.get("generation")}))
        predecessor = str(record.get("predecessorSessionId") or "")
        accepted = int(record.get("transferAcceptedAt") or record.get("claimedAt") or 0)
        if (predecessor and record.get("state") in {"authoritative", "rotating"}
                and accepted and now-accepted > PREDECESSOR_ARCHIVE_GRACE_SECONDS*1000):
            # The successor owes the predecessor an archive once the handoff settles.
            # Registry validate has flagged this since v3.4.8, but nothing woke anyone:
            # observed live three times as two coordinators on the same project.
            pred_manifest = common.read_manifest(predecessor)
            if pred_manifest and not pred_manifest.get("isArchived"):
                out.append(observation("predecessor-unarchived", "medium", project, sid,
                    {"predecessorSessionId": predecessor, "transferAcceptedAt": accepted,
                     "generation": record.get("generation")}))
        terminal_error = terminal_session_error(manifest)
        if terminal_error:
            out.append(observation("coordinator-session-error", "high", project, sid,
                {**terminal_error, "generation": record.get("generation")}))
        sigterms = unresolved_pi_sigterms(sid, record.get("lastHeartbeatAt") or record.get("claimedAt") or 0)
        if sigterms:
            out.append(observation("coordinator-pi-sigterm", "high", project, sid,
                {"errorClass": "pi-sigterm", "countSinceHeartbeat": len(sigterms),
                 "firstAt": sigterms[0], "lastAt": sigterms[-1], "generation": record.get("generation")}))

    for p in (RUNTIME / "worker-leases").glob("*.json"):
        lease = common.read_json(p) or {}; sid = str(lease.get("sessionId") or p.stem)
        manifest = manifests.get(sid, {}); project = project_for_child(lease, manifest, owners, roots, ambiguous)
        state = str(lease.get("state") or ""); role = lease.get("role") or common.label_value(manifest, "agent-role::")
        explicit_project = common.label_value(manifest, "project::")
        parent_session = str(lease.get("parentSessionId") or "")
        parent_project = owners.get(parent_session)
        ambiguous_parent = ambiguous.get(parent_session)
        active_child = bool(project and sid in (records.get(project, {}).get("activeChildren") or []))
        evidence = {"state": state, "phase": lease.get("phase"), "lastHeartbeatAt": lease.get("lastHeartbeatAt"),
                    "preservationState": lease.get("preservationState"), "worktree": lease.get("worktree"), "role": role,
                    "activeChild": active_child}
        kind = {"suspect": "worker-suspect", "stalled": "worker-stalled", "error": "worker-error",
                "handoff-ready": "terminal-handoff-unconsumed"}.get(state)
        if kind and not ambiguous_parent:
            severity = "high" if state in {"stalled", "error"} or (state == "handoff-ready" and active_child) else "medium"
            out.append(observation(kind, severity, project, sid, evidence,
                                   coordinatorSessionId=lease.get("parentSessionId"), workUnit=lease.get("workUnit")))
        # A dead lane whose dispatching generation is gone can never become
        # preservation-proven: no one is left to prove it. Without a disposition
        # path these accumulate forever, each holding a worktree — observed live
        # as 23 lanes, the oldest 91 hours old, with archivableBacklog at zero.
        raw_created = lease.get("createdAt") if isinstance(lease.get("createdAt"), int) else lease.get("lastHeartbeatAt")
        created = raw_created if isinstance(raw_created, int) and not isinstance(raw_created, bool) else 0
        if (state in {"stalled", "error"} and parent_session and parent_session not in owners
                and not ambiguous_parent and created and now-created > ORPHANED_LANE_SECONDS*1000):
            stable = {"parentSessionId": parent_session, "workUnit": lease.get("workUnit"),
                      "worktree": lease.get("worktree")}
            out.append(observation("orphaned-dead-lane", "medium", project, sid,
                {**stable, "state": state, "preservationState": lease.get("preservationState"),
                 "ageMs": now-created}, fingerprint_evidence=stable,
                coordinatorSessionId=parent_session, workUnit=lease.get("workUnit")))
        if ambiguous_parent:
            out.append(observation("project-mapping-conflict", "critical", None, sid,
                {"ambiguousParentProjects": ambiguous_parent, "childLabelProject": explicit_project,
                 "parentSessionId": parent_session, "worktree": lease.get("worktree")}))
        elif explicit_project and parent_project and explicit_project != parent_project:
            out.append(observation("project-mapping-conflict", "critical", parent_project, sid,
                {"authoritativeParentProject": parent_project, "childLabelProject": explicit_project,
                 "parentSessionId": lease.get("parentSessionId"), "worktree": lease.get("worktree")}))
        collision_sessions = lease.get("cwdCollisionSessions") or lease.get("cwdCollision")
        if collision_sessions:
            out.append(observation("cwd-collision", "critical", project, sid,
                {**evidence, "cwdCollisionSessions": collision_sessions}))
        if not ambiguous_parent and state == "handoff-ready" and role != "auditor" and lease.get("preservationState") not in {"pushed", "merged"}:
            out.append(observation("preservation-unknown", "high", project, sid, evidence))

    external_watcher_ids = set()
    for p in EXTERNAL_WAITS.glob("*.json"):
        wait = common.read_json(p) or {}
        state = str(wait.get("state") or "")
        watcher = str(wait.get("watcherSessionId") or "")
        if watcher and state != "cleared":
            external_watcher_ids.add(watcher)
        kind = {"terminal": "external-wait-terminal", "unobserved": "external-wait-unobserved",
                "deadline": "external-wait-deadline"}.get(state)
        if not kind:
            continue
        evidence = {"waitId": wait.get("waitId") or p.stem, "waitKind": wait.get("kind"),
                    "subject": wait.get("subject"), "state": state, "reason": wait.get("reason"),
                    "deadlineAt": wait.get("deadlineAt"), "terminalExitCode": wait.get("terminalExitCode"),
                    "jobId": wait.get("jobId")}
        severity = "high" if state in {"unobserved", "deadline"} or wait.get("terminalExitCode") not in {0, None} else "medium"
        out.append(observation(kind, severity, wait.get("project"), watcher, evidence,
                               coordinatorSessionId=wait.get("coordinatorSessionId"), workUnit=wait.get("workUnit")))

    for p in (RUNTIME / "worker-jobs").glob("*.json"):
        job = common.read_json(p) or {}; sid = str(job.get("sessionId") or p.stem)
        if sid in external_watcher_ids:
            continue
        lease = common.read_json(RUNTIME / "worker-leases" / f"{sid}.json") or {}; manifest = manifests.get(sid, {})
        parent_session = str(lease.get("parentSessionId") or "")
        project = project_for_child(lease, manifest, owners, roots, ambiguous); code = job.get("exitCode")
        if parent_session in ambiguous: continue
        ev = {"exitCode": code, "jobId": job.get("jobId") or sid,
              "endedAt": job.get("finishedAt") or job.get("endedAt"),
              "reportedAt": job.get("reportedAt"), "logPath": job.get("logPath"), "heavy": job.get("heavy")}
        if code == 75 and not job.get("reportedAt"):
            out.append(observation("heavy-lock-wait", "medium", project, sid, ev, coordinatorSessionId=lease.get("parentSessionId")))
        elif code is not None and not job.get("reportedAt"):
            out.append(observation("job-exit-unreported", "medium", project, sid, ev, coordinatorSessionId=lease.get("parentSessionId")))

    for p in (RUNTIME / "owner-gates").glob("*/*.json"):
        gate = common.read_json(p) or {}
        if gate.get("state") == "open":
            out.append(observation("owner-gate-blocked", "info", gate.get("project"), None,
                {"state": gate.get("state"), "action": gate.get("action"), "workUnit": gate.get("workUnit")}, gateId=gate.get("gateId") or p.stem))

    # Protocol v3.3.0: coordinator inbox digests, product-status trust, and commitments.
    # Each feed embeds the exact coordinator generation so admission fences the wake, and
    # provides a stable fingerprint so a continuously-observed condition coalesces to one wake.
    for row in inbox_mod.wake_observations(now):
        out.append(observation("coordinator-inbox-ready", "high", row["project"], row["sessionId"],
            {"generation": row["generation"], "wakingCount": row["count"],
             "itemIds": row["itemIds"], "kinds": row["kinds"]},
            fingerprint_evidence={"generation": row["generation"], "itemIds": row["itemIds"]},
            coordinatorGeneration=row["generation"]))
    for row in status_mod.health_observations(now):
        out.append(observation(row["kind"], "high", row["project"], row["sessionId"], row["evidence"],
            fingerprint_evidence=row["evidence"], coordinatorGeneration=row["generation"]))
    for row in commitment_mod.overdue_observations(now):
        out.append(observation("coordinator-commitment-overdue", "high", row["project"], row["sessionId"],
            {**row["evidence"], "commitmentId": row["commitmentId"]},
            fingerprint_evidence=row["evidence"], discriminator=row["commitmentId"],
            coordinatorGeneration=row["generation"]))
    return out

def controller_last_turn_at() -> int:
    """When the recovery controller last started a turn.

    Harness registration happens at the start of every controller turn, so the
    newest registration is the freshest proof that the lane is alive."""
    newest = 0
    for path in HARNESSES.glob("*.json"):
        row = common.read_json(path)
        if not isinstance(row, dict) or row.get("sessionRole") != "recovery-controller":
            continue
        for key in ("registeredAt", "lastSeenAt"):
            value = row.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                newest = max(newest, value)
    return newest


def host_load() -> dict[str, Any]:
    """Host saturation looks exactly like a lost channel: everything times out.

    Measured live: eight parallel build processes from unrelated work drove the
    1-minute load to 59 on an 8-core host, RPC timed out at 10 s, and the channel
    itself was perfectly fine. Naming saturation keeps the transport verdict honest."""
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        return {"load1": None, "cores": None, "saturated": False}
    cores = os.cpu_count() or 1
    return {"load1": round(load1, 2), "cores": cores,
            "saturated": bool(load1 / cores > HOST_SATURATION_RATIO)}


def transport_state() -> dict[str, Any]:
    """Whether the fleet's channel is answering, as a named condition.

    A lost transport is indistinguishable from lazy agents from the outside: the
    ledger grows, coordinators fall silent, results go uncollected. Naming it is
    the whole fix — nothing here can restore a channel the host no longer has."""
    row = common.read_json(TRANSPORT) or {}
    last_ok = row.get("lastSuccessAt")
    failures = int(row.get("consecutiveFailures") or 0)
    age = now_ms() - int(last_ok) if isinstance(last_ok, int) else None
    lost = bool(failures and (age is None or age > TRANSPORT_LOST_SECONDS * 1000))
    host = host_load()
    return {"lastSuccessAt": last_ok, "ageMs": age, "consecutiveFailures": failures,
            "lastFailureReason": row.get("lastFailureReason"),
            # Starved is not lost: with the host saturated, timeouts say nothing
            # about the channel, and calling it lost would send recovery the wrong way.
            "lost": bool(lost and not host["saturated"]),
            "hostStarved": bool(failures and host["saturated"]), "host": host}


def controller_silence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A self-healing lane that has stopped is worse than one that never existed.

    Nothing here can restart the controller — that is the admission lane's job —
    but silence must stop being invisible. Observed live: the wake lane was hard
    blocked by a single failed probe, the controller went 56 minutes without a
    turn, and the ledger grew to 74 open conditions while every project looked
    merely busy. A deliberate kill switch is rest, not silence."""
    blocking = [r for r in rows if drain_rank(r) <= 3]
    last = controller_last_turn_at()
    age = now_ms() - last if last else None
    silent = bool(blocking and not DISABLED.exists()
                  and (age is None or age > CONTROLLER_SILENT_SECONDS * 1000))
    return {"lastTurnAt": last or None, "ageMs": age,
            "deliveryBlockingCount": len(blocking), "silent": silent}


def drain_rank(row: dict[str, Any]) -> int:
    kind = str(row.get("kind") or "")
    if kind in SAFETY_KINDS:
        return 0
    if kind in IDLE_EXECUTOR_KINDS:
        return 1
    if kind in BOOKKEEPING_KINDS:
        return 2
    if kind in LANE_RECOVERY_KINDS:
        return 3
    return 4


def drain_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The backlog in the order a controller must work it.

    Safety first, then everything that stops delivery, then housekeeping under a
    quota so it can never starve delivery. Within a rank: severity, then age, so
    the oldest genuine blocker goes before a fresher one."""
    now = now_ms()
    def key(row):
        return (drain_rank(row),
                SEVERITY_ORDER.get(str(row.get("severity")), 9),
                int(row.get("firstSeenAt") or row.get("createdAt") or now))
    ordered = sorted(rows, key=key)
    out: list[dict[str, Any]] = []
    housekeeping = 0
    for row in ordered:
        if drain_rank(row) == 4:
            if housekeeping >= HOUSEKEEPING_QUOTA:
                continue
            housekeeping += 1
        out.append(row)
    return out


def drain(args: argparse.Namespace) -> dict[str, Any]:
    """What this controller turn must work on, whether or not a wake envelope exists.

    An admission envelope says *why* the controller woke; it never limited what the
    ledger needs. Treating it as the work list left 73 open conditions with none
    claimed, turn after turn reporting that nothing was delivered."""
    rows = [r for r in read_incidents().values() if r.get("state") == "open"]
    ordered = drain_order(rows)
    silence = controller_silence(rows)
    blocking = [r for r in rows if drain_rank(r) <= 3]
    limit = max(1, int(getattr(args, "limit", DRAIN_LIMIT) or DRAIN_LIMIT))
    selected = ordered[:limit]
    return {"schemaVersion": SCHEMA, "disabled": DISABLED.exists(),
            "killSwitch": kill_switch_state(rows), "controller": silence,
            "transport": transport_state(),
            "openCount": len(rows), "deliveryBlockingCount": len(blocking),
            "housekeepingCount": len(rows) - len(blocking),
            # A turn that ends with delivery still blocked must be followed by
            # another turn now, not after the next coalesce window.
            "requestImmediateCycle": len(blocking) > len(selected),
            "work": [{"incidentId": r.get("incidentId"), "kind": r.get("kind"),
                      "project": r.get("project"), "sessionId": r.get("sessionId"),
                      "severity": r.get("severity"), "rank": drain_rank(r),
                      "firstSeenAt": r.get("firstSeenAt"),
                      "completedRecoveryAttempts": r.get("completedRecoveryAttempts")}
                     for r in selected]}


def kill_switch_state(observed: list[dict[str, Any]]) -> dict[str, Any]:
    """A forgotten kill switch looks exactly like a healthy fleet.

    While it is present nothing may be claimed or acted on, so the disabled state
    cannot become an incident that heals itself. What it can do is stop being
    silent: every detect/report carries the switch's age and whether conditions
    are piling up behind it. Observed live on 2026-08-14 — two upgrades left
    self-healing off for three hours while eleven conditions accumulated."""
    if not DISABLED.exists():
        return {"present": False, "ageMs": None, "staleWithOpenConditions": False}
    try:
        age = max(0, now_ms() - int(DISABLED.stat().st_mtime * 1000))
    except OSError:
        age = None
    stale = bool(age is not None and age > KILL_SWITCH_STALE_SECONDS * 1000 and observed)
    return {"present": True, "ageMs": age, "observedConditions": len(observed),
            "staleWithOpenConditions": stale}


def detect(apply=False):
    observed = collect_observations(); existing = read_incidents(); now = now_ms(); seen = set(); changed = []
    if not apply:
        return {"schemaVersion": SCHEMA, "apply": False, "disabled": DISABLED.exists(),
                "killSwitch": kill_switch_state(observed), "observations": observed}
    with common.file_lock(LOCK):
        existing = read_incidents()
        for obs in observed:
            iid = obs["incidentId"]; seen.add(iid); old = existing.get(iid); fp = obs["evidenceFingerprint"]
            if not old:
                initial_state = "suppressed" if obs.get("kind") == "owner-gate-blocked" else "open"
                row = {"schemaVersion": SCHEMA, **obs, "state": initial_state, "firstSeenAt": now, "lastSeenAt": now,
                       "conditionRevision": 1, "claimOwner": None, "claimExpiresAt": None, "recoveryAttempts": 0,
                       "lastActionAt": None, "cooldownUntil": None, "resolutionEvidence": None, "history": []}
            else:
                row = {**old, **obs, "lastSeenAt": now}
                row.setdefault("conditionRevision", 1)
                if old.get("clearCandidateAt") is not None:
                    row.pop("clearCandidateAt", None)
                    row.setdefault("history", []).append({"at": now, "action": "condition-clear-cancelled",
                                                          "candidateAt": old.get("clearCandidateAt")})
                if row.get("state") == "claimed" and int(row.get("claimExpiresAt") or 0) <= now:
                    row.update(state="open", claimOwner=None, claimExpiresAt=None)
                    row.setdefault("history", []).append({"at": now, "action": "claim-expired"})
                if row.get("state") == "deferred" and int(row.get("cooldownUntil") or 0) <= now:
                    row.update(state="open", cooldownUntil=None)
                    row.setdefault("history", []).append({"at": now, "action": "cooldown-expired"})
                if row.get("state") == "resolved":
                    # A condition that objectively cleared ended the prior
                    # bounded recovery cycle. A later recurrence starts with a
                    # fresh wake-1 budget rather than inheriting rotation/exhaustion.
                    revision = int(old.get("conditionRevision") or 1) + 1
                    row.update(state="open", resolutionEvidence=None, recoveryAttempts=0,
                               conditionRevision=revision, claimOwner=None, claimExpiresAt=None, claimStage=None,
                               claimAllowedActions=None, lastActionAt=None, cooldownUntil=None)
                    row.setdefault("history", []).append({"at": now, "action": "condition-reopened",
                                                          "fingerprint": fp, "conditionRevision": revision,
                                                          "freshRecoveryCycle": True})
            common.atomic_json(incident_path(iid), row); changed.append(iid)
        for iid, row in existing.items():
            if iid in seen or row.get("state") == "resolved": continue
            candidate_at = int(row.get("clearCandidateAt") or 0)
            if not candidate_at:
                candidate_at = now
                row["clearCandidateAt"] = candidate_at
                row.setdefault("history", []).append({"at": now, "action": "condition-clear-candidate"})
            if now - candidate_at < CLEAR_CONFIRM_SECONDS * 1000:
                common.atomic_json(incident_path(iid), row); changed.append(iid)
                continue
            previous_attempts = int(row.get("recoveryAttempts") or 0)
            row.pop("clearCandidateAt", None)
            row.update(state="resolved", lastSeenAt=now, claimOwner=None, claimExpiresAt=None,
                       recoveryAttempts=0, claimStage=None, claimAllowedActions=None,
                       lastActionAt=None, cooldownUntil=None,
                       resolutionEvidence={"kind": "condition-cleared", "at": now})
            row.setdefault("history", []).append({"at": now, "action": "condition-cleared",
                                                  "clearCandidateAt": candidate_at,
                                                  "completedRecoveryAttempts": previous_attempts})
            common.atomic_json(incident_path(iid), row); changed.append(iid)
    rows = read_incidents()
    return {"schemaVersion": SCHEMA, "apply": True, "disabled": DISABLED.exists(),
            "killSwitch": kill_switch_state(observed),
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

COORDINATOR_RECOVERY_KINDS = {"coordinator-lease-stale", "coordinator-session-error",
                              "coordinator-pi-sigterm", "coordinator-worker-terminal-status"}

def claim_limit(row):
    return COORDINATOR_MAX_ATTEMPTS if row.get("kind") in COORDINATOR_RECOVERY_KINDS else MAX_ATTEMPTS

def coordinator_incident(row):
    return row.get("kind") in COORDINATOR_RECOVERY_KINDS

def claim_stage(row, attempt_number):
    if not coordinator_incident(row): return "recover"
    return {1: "wake-1", 2: "wake-2", 3: "rotation"}.get(attempt_number, "exhausted")

def claim_actions(row, stage):
    if stage in {"wake-1", "wake-2"}: return ["wake-coordinator", "renew-request", "preserve-snapshot"]
    if stage == "rotation": return ["preserve-snapshot", "bridge-rotation-on-attempt-3", "owner-escalation"]
    return row.get("allowedActions") or ["report-only"]

def require_active_controller(controller):
    now = now_ms()
    active = common.read_json(CONTROLLER) or {}
    if active.get("sessionId") != controller: fail("active controller owner mismatch")
    if int(active.get("leaseExpiresAt") or 0) <= now: fail("controller lease expired")
    deadline = int(active.get("maxRuntimeExpiresAt") or (int(active.get("claimedAt") or 0) + MAX_CONTROLLER_RUNTIME_SECONDS*1000))
    if deadline <= now: fail("controller maximum runtime exceeded")
    return active

def require_claim(row, controller):
    if DISABLED.exists(): fail("self-healing kill switch is active")
    require_active_controller(controller)
    if row.get("state") != "claimed" or row.get("claimOwner") != controller:
        fail("incident claim owner/state mismatch")
    if int(row.get("claimExpiresAt") or 0) <= now_ms(): fail("incident claim expired")

def claim(args):
    now = now_ms()
    def action(row):
        if DISABLED.exists(): fail("self-healing kill switch is active")
        require_active_controller(args.controller)
        if row.get("clearCandidateAt") is not None:
            fail("incident is awaiting clear confirmation")
        if row.get("state") == "claimed":
            if int(row.get("claimExpiresAt") or 0) <= now:
                fail("incident claim expired; run deterministic detect --apply before reclaim")
            fail(f"incident already claimed by {row.get('claimOwner')}; use heartbeat, not reclaim")
        if row.get("state") not in {"open", "deferred"}: fail(f"incident is not claimable: {row.get('state')}")
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
        row.pop("clearCandidateAt", None)
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
        lease_expiry = int(row.get("leaseExpiresAt") or 0)
        deadline = int(row.get("maxRuntimeExpiresAt") or (int(row.get("claimedAt") or 0) + MAX_CONTROLLER_RUNTIME_SECONDS*1000))
        effective_expiry = min(lease_expiry, deadline) if lease_expiry and deadline else max(lease_expiry, deadline)
        if row.get("sessionId") == args.session:
            if effective_expiry <= now: fail("expired controller cannot reclaim; release first")
            fail("controller already active; use controller-heartbeat, not reclaim")
        if row.get("sessionId") and effective_expiry > now:
            fail(f"controller already active: {row.get('sessionId')}")
        deadline = now + MAX_CONTROLLER_RUNTIME_SECONDS*1000
        row = {"schemaVersion": SCHEMA, "sessionId": args.session, "claimedAt": now,
               "lastHeartbeatAt": now, "maxRuntimeExpiresAt": deadline,
               "leaseExpiresAt": min(now+args.ttl*1000, deadline)}
        common.atomic_json(CONTROLLER, row); return row

def controller_heartbeat(args):
    now = now_ms()
    with common.file_lock(LOCK):
        if DISABLED.exists(): fail("self-healing kill switch is active")
        row = common.read_json(CONTROLLER) or {}
        if row.get("sessionId") != args.session: fail("controller owner mismatch")
        if int(row.get("leaseExpiresAt") or 0) <= now: fail("controller lease expired")
        deadline = int(row.get("maxRuntimeExpiresAt") or (int(row.get("claimedAt") or 0) + MAX_CONTROLLER_RUNTIME_SECONDS*1000))
        if deadline <= now: fail("controller maximum runtime exceeded")
        row.update(lastHeartbeatAt=now, maxRuntimeExpiresAt=deadline,
                   leaseExpiresAt=min(now+args.ttl*1000, deadline))
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
    dr=sub.add_parser("drain"); dr.add_argument("--limit", type=int, default=DRAIN_LIMIT)
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
    elif args.cmd == "drain": result=drain(args)
    elif args.cmd == "list": result=list_rows(args)
    elif args.cmd == "report":
        rows = read_incidents()
        open_rows = [r for r in rows.values() if r.get("state") == "open"]
        result={"disabled": DISABLED.exists(), "killSwitch": kill_switch_state(open_rows),
                "controller": common.read_json(CONTROLLER), "summary": summarize(rows.values())}
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

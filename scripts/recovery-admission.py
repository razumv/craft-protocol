#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Protocol v3.2.2 authenticated, consumption-aware admission supervisor.

The supervisor never creates sessions or mutates Craft session state. It uses
only the capability-v2 admission adapter documented in
``docs/SELF-HEALING-v3.2.2.md`` and fails closed on every contract ambiguity.
Routine, exact-generation coordinator ticks and complex controller recovery use
independent durable target cycles, so a stuck recovery controller cannot block
ordinary handoff/external-wait reconciliation.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
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
STATE = Path(os.environ.get("CRAFT_ADMISSION_STATE", RUNTIME / "self-healing/admission.json")).expanduser()
TICK_STATES = Path(os.environ.get("CRAFT_COORDINATOR_TICK_STATES", RUNTIME / "self-healing/coordinator-ticks")).expanduser()
LOCK = Path(os.environ.get("CRAFT_ADMISSION_LOCK", RUNTIME / "self-healing/admission.lock")).expanduser()
DISABLED = Path(os.environ.get("CRAFT_SELF_HEALING_DISABLED", RUNTIME / "self-healing.disabled")).expanduser()
PROTOCOL_VERSION = "v3.4.34"
AUTOMATION_ID = os.environ.get("CRAFT_RECOVERY_NOTIFIER_AUTOMATION_ID", "a322-admission")
LEGACY_AUTOMATION_IDS = {"a321-notifier", "a31101", "a31102"}
CONTROLLER_ACTION_ID = "a322-controller-recovery"
COORDINATOR_ACTION_ID = "a322-coordinator-tick"
CONTROLLER_HARNESS = Path(os.environ.get("CRAFT_CONTROLLER_HARNESS", Path(__file__).with_name("controller-harness.py"))).expanduser()
TOKEN_FILE = Path(os.environ.get("CRAFT_SERVER_TOKEN_FILE", HOME / ".config/craft-agent-headless/server-token")).expanduser()
MAX_INCIDENTS = int(os.environ.get("CRAFT_RECOVERY_ADMISSION_MAX_INCIDENTS", "3"))
RPC_TIMEOUT_SECONDS = int(os.environ.get("CRAFT_ADMISSION_RPC_TIMEOUT_SECONDS", "60"))
if RPC_TIMEOUT_SECONDS < 20 or RPC_TIMEOUT_SECONDS > 120:
    raise ValueError("CRAFT_ADMISSION_RPC_TIMEOUT_SECONDS must be between 20 and 120")
RECOVERY_MIN_AGE_SECONDS = int(os.environ.get("CRAFT_ADMISSION_RECOVERY_MIN_AGE_SECONDS", "1800"))
RECOVERY_MIN_AGE_MS = RECOVERY_MIN_AGE_SECONDS * 1000
# A consumed wake whose condition persists is re-issued a bounded number of times
# after a quiet period; then the incidents fall through to the controller lane.
MAX_REWAKES = int(os.environ.get("CRAFT_ADMISSION_MAX_REWAKES", "2"))
MAX_PROBE_FAILURES = int(os.environ.get("CRAFT_ADMISSION_MAX_PROBE_FAILURES", "3"))
TRANSPORT = Path(os.environ.get("CRAFT_ADMISSION_TRANSPORT", RUNTIME / "self-healing/transport.json")).expanduser()
TRANSPORT_LOST_SECONDS = int(os.environ.get("CRAFT_TRANSPORT_LOST_SECONDS", "900"))
REWAKE_QUIET_MS = int(os.environ.get("CRAFT_ADMISSION_REWAKE_QUIET_SECONDS", "1800")) * 1000
NOW_MS = lambda: int(os.environ.get("CRAFT_TEST_NOW_MS", "0")) or int(time.time() * 1000)

CAPABILITY_VERSION = 2
CLAIM_CHANNEL = "automations:admissionClaim"
DELIVER_CHANNEL = "automations:admissionDeliver"
INSPECT_CHANNEL = "automations:admissionInspect"
RECOVER_CHANNEL = "automations:admissionRecover"
PENDING_PHASES = {"delivered", "pending-consumption", "recovering"}
DELIVERY_STATUSES = {"delivered", "pending-consumption", "consumed", "duplicate"}
INSPECT_STATUSES = {"delivered", "pending-consumption", "consumed"}
RECOVER_STATUSES = {"recovered", "consumed", "busy"}
BLOCKED_KINDS = {"owner-gate-blocked", "cwd-collision", "project-mapping-conflict", "ambiguous-coordinator-owner", "preservation-unknown"}
# Protocol v3.3.0 coordinator inbox/status/commitment wakes ride the existing v3.2.2
# admission lane. They are generation-fenced and never grant merge/rotation authority.
COORDINATOR_V33_WAKE_KINDS = {"coordinator-inbox-ready", "coordinator-status-missing", "coordinator-status-stale", "coordinator-plan-unexecutable", "coordinator-commitment-overdue", "coordinator-status-contradiction"}
WAKE_KINDS = {"coordinator-lease-stale", "coordinator-session-error", "coordinator-pi-sigterm", "coordinator-worker-terminal-status", "predecessor-unarchived", "job-exit-unreported", "heavy-lock-wait", "terminal-handoff-unconsumed", "external-wait-terminal", "external-wait-unobserved", "external-wait-deadline"} | COORDINATOR_V33_WAKE_KINDS
ROUTINE_KINDS = {"coordinator-tick-due", "coordinator-lease-stale", "terminal-handoff-unconsumed", "external-wait-terminal"} | COORDINATOR_V33_WAKE_KINDS


class AdmissionError(ValueError):
    """A deterministic fail-closed admission rejection."""


class CapabilityError(AdmissionError):
    """The running Craft server is not the explicitly supported API."""


class TransientRpcError(AdmissionError):
    """Capability/workspace discovery failed before target mutation."""


class DeliveryUnknown(AdmissionError):
    """A target mutation may have succeeded and must be retried idempotently."""


class ProbeUnavailable(AdmissionError):
    """A safety fact could not be *observed* — not a proven-unsafe condition.

    Being unable to look is not evidence of danger. A durable block belongs to
    conditions proven unsafe (ambiguous controller identity, runtime mismatch,
    foreign workspace); a probe that timed out or returned garbage must be retried
    and only becomes durable after it fails repeatedly. Observed live 2026-08-14:
    one failed controller-harness probe blocked the wake lane permanently, the
    controller went 56 minutes without a turn, and the incident ledger grew to 74
    open conditions while every project looked merely busy."""


class StateError(AdmissionError):
    """Durable local target state is unreadable and must remain untouched."""


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(raw, path)
    finally:
        try:
            os.unlink(raw)
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def required_json(path: Path, label: str) -> dict[str, Any]:
    value = read_json(path)
    if value is None:
        raise StateError(f"unreadable {label}: {path}")
    return value


def validate_fencing_inputs() -> None:
    """No direct target is selected while any durable safety fence is unreadable."""
    for path in sorted(COORDINATORS.glob("*.json")):
        required_json(path, "coordinator registry")
    for path in sorted(INCIDENTS.glob("*.json")):
        required_json(path, "recovery incident")
    for path in sorted((RUNTIME / "owner-gates").glob("*/*.json")):
        required_json(path, "owner gate")


def manifest(session_id: str) -> dict[str, Any] | None:
    try:
        return json.loads((SESSIONS / session_id / "session.jsonl").open(encoding="utf-8", errors="ignore").readline())
    except Exception:
        return None


def label_value(row: dict[str, Any], prefix: str) -> str | None:
    for label in row.get("labels") or []:
        if isinstance(label, str) and label.startswith(prefix):
            return label.split("::", 1)[1]
    return None


def require_live_manifest(session_id: str, role: str) -> dict[str, Any]:
    row = manifest(session_id)
    if not row:
        raise AdmissionError(f"{role} manifest missing")
    if (row.get("id") or row.get("sessionId")) != session_id:
        raise AdmissionError(f"{role} manifest identity mismatch")
    if row.get("isArchived") or row.get("sessionStatus") in {"done", "cancelled", "error"}:
        raise AdmissionError(f"{role} is not live")
    if label_value(row, "agent-role::") != role:
        raise AdmissionError(f"session is not {role}")
    return row


def require_persistent_controller(session_id: str) -> dict[str, Any]:
    row = require_live_manifest(session_id, "recovery-controller")
    if label_value(row, "controller-mode::") != "persistent":
        raise AdmissionError("controller is not marked persistent")
    try:
        cp = subprocess.run([str(CONTROLLER_HARNESS), "report"], text=True, capture_output=True, timeout=10)
        report = json.loads(cp.stdout)
        rows = report.get("rows", [])
        matches = [item for item in rows if item.get("sessionId") == session_id]
    except Exception as exc:
        raise ProbeUnavailable("controller harness proof unavailable") from exc
    # The singleton invariant is "no other live controller", not "this controller
    # already registered". A runtime restart kills every harness, so demanding a
    # proven-active receipt before delivery self-deadlocks the controller lane:
    # registration happens inside the turn that only a delivery can start. An
    # absent registration and a receipt whose PID is objectively gone both prove
    # there is no competing controller, so delivery is safe; the controller
    # re-registers at startup and the deterministic controller lease still fences
    # concurrent turns. Ambiguous identity remains a hard refusal.
    others_active = [item for item in rows if item.get("sessionId") != session_id
                     and item.get("state") == "active"
                     and item.get("sessionRole") == "recovery-controller"]
    state = matches[0].get("state") if matches else "unregistered"
    if (len(matches) > 1 or others_active
            or (matches and matches[0].get("sessionRole") != "recovery-controller")
            or state not in {"active", "alreadyExited", "unregistered"}):
        raise AdmissionError("persistent controller harness is not uniquely live/proven")
    return row


def require_manifest_workspace(row: dict[str, Any]) -> dict[str, Any]:
    try:
        manifest_root = Path(str(row["workspaceRootPath"])).expanduser().resolve()
    except Exception as exc:
        raise AdmissionError("target manifest workspace binding is invalid") from exc
    if manifest_root != WORKSPACE.resolve():
        raise AdmissionError("target manifest is not bound to the configured workspace")
    return row


def require_exact_coordinator_target(batch: dict[str, Any]) -> dict[str, Any]:
    project = str(batch.get("project") or "")
    registry = read_json(COORDINATORS / f"{project}.json") if project else None
    if (not registry or registry.get("state") != "authoritative" or
            registry.get("coordinatorSessionId") != batch.get("targetSessionId") or
            scalar_generation(registry.get("generation")) != str(batch.get("targetGeneration"))):
        raise AdmissionError("coordinator target generation is no longer authoritative")
    return require_live_manifest(str(batch["targetSessionId"]), "coordinator")


def live_scope_blocked(row: dict[str, Any], all_rows: list[dict[str, Any]]) -> bool:
    project, session, work_unit = row.get("project"), row.get("sessionId"), row.get("workUnit")
    registry = read_json(COORDINATORS / f"{project}.json") if project else None
    if registry and registry.get("state") in {"hold", "needs-owner"}:
        return True
    for blocker in all_rows:
        if blocker.get("state") not in {"open", "claimed", "deferred"} or blocker.get("kind") not in BLOCKED_KINDS:
            continue
        kind = blocker.get("kind")
        blocker_session = blocker.get("sessionId")
        blocker_work_unit = blocker.get("workUnit") or (blocker.get("evidence") or {}).get("workUnit")
        if kind == "owner-gate-blocked":
            if project and blocker.get("project") == project and work_unit and str(blocker_work_unit or "") == str(work_unit):
                return True
            continue
        if kind == "preservation-unknown":
            # Preservation ambiguity remains actionable for the complex
            # controller, but direct_target() must reject the routine lane.
            continue
        if session and blocker_session == session:
            return True
    if project and work_unit:
        for path in (RUNTIME / "owner-gates" / str(project)).glob("*.json"):
            gate = required_json(path, "owner gate")
            if gate.get("state") == "open" and str(gate.get("workUnit") or "") == str(work_unit):
                return True
    return False


def all_incidents() -> list[dict[str, Any]]:
    return [required_json(path, "recovery incident") for path in sorted(INCIDENTS.glob("*.json"))]


def actionable_incidents() -> list[dict[str, Any]]:
    all_rows = all_incidents()
    rows = []
    for row in all_rows:
        if (row.get("state") != "open" or row.get("clearCandidateAt") is not None or
                row.get("kind") in BLOCKED_KINDS or row.get("kind") not in WAKE_KINDS):
            continue
        if row.get("kind") == "terminal-handoff-unconsumed" and (row.get("evidence") or {}).get("activeChild") is not True:
            continue
        if not row.get("sessionId") or live_scope_blocked(row, all_rows):
            continue
        rows.append(row)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    rows.sort(key=lambda row: (order.get(str(row.get("severity")), 9), int(row.get("firstSeenAt") or 0), str(row.get("incidentId"))))
    return rows


def incident_fingerprint(rows: list[dict[str, Any]]) -> str:
    value = [{"incidentId": row.get("incidentId"), "evidenceFingerprint": row.get("evidenceFingerprint"),
              "conditionRevision": int(row.get("conditionRevision") or 1)} for row in rows]
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def scalar_generation(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int)) and str(value):
        return str(value)
    return None


def authoritative_owner_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in COORDINATORS.glob("*.json"):
        row = required_json(path, "coordinator registry")
        session_id = str(row.get("coordinatorSessionId") or "")
        if row.get("state") in {"authoritative", "rotating", "hold", "needs-owner"} and session_id:
            counts[session_id] = counts.get(session_id, 0) + 1
    return counts


def direct_target(row: dict[str, Any]) -> dict[str, str] | None:
    if row.get("kind") not in ROUTINE_KINDS or not row.get("project"):
        return None
    project = str(row["project"])
    if project in {".", ".."} or Path(project).name != project:
        return None
    registry_path = COORDINATORS / f"{project}.json"
    registry = required_json(registry_path, "coordinator registry") if registry_path.exists() else {}
    if registry.get("state") != "authoritative":
        return None
    session_id = str(registry.get("coordinatorSessionId") or "")
    generation = scalar_generation(registry.get("generation"))
    if not session_id or generation is None or authoritative_owner_counts().get(session_id) != 1:
        return None
    kind = row.get("kind")
    evidence = row.get("evidence") or {}
    for blocker in all_incidents():
        if (blocker.get("state") in {"open", "claimed", "deferred"} and
                blocker.get("kind") == "preservation-unknown" and
                blocker.get("sessionId") == row.get("sessionId")):
            return None
    if kind in {"coordinator-tick-due", "coordinator-lease-stale"} | COORDINATOR_V33_WAKE_KINDS:
        if row.get("sessionId") != session_id or scalar_generation(evidence.get("generation")) != generation:
            return None
    elif kind == "terminal-handoff-unconsumed":
        if row.get("coordinatorSessionId") != session_id or row.get("sessionId") not in (registry.get("activeChildren") or []):
            return None
    elif kind == "external-wait-terminal" and row.get("coordinatorSessionId") != session_id:
        return None
    try:
        require_live_manifest(session_id, "coordinator")
    except AdmissionError:
        return None
    return {"targetType": "coordinator", "targetKind": "coordinator", "project": project,
            "targetSessionId": session_id, "targetGeneration": generation,
            "coordinatorGeneration": generation}


def scheduled_tick_rows(now: int) -> list[dict[str, Any]]:
    """Emit stable half-TTL coordinator tick candidates without an incident.

    A completed coordinator turn advances lastHeartbeatAt/leaseExpiresAt through
    deterministic activity reconciliation, which creates the next immutable
    tick identity. Until then, consumption of this exact identity prevents
    repeated delivery even though every five-minute scan remains due.
    """
    rows = []
    owner_counts = authoritative_owner_counts()
    for path in sorted(COORDINATORS.glob("*.json")):
        registry = required_json(path, "coordinator registry")
        if registry.get("state") != "authoritative":
            continue
        session_id = str(registry.get("coordinatorSessionId") or "")
        generation = scalar_generation(registry.get("generation"))
        heartbeat = registry.get("lastHeartbeatAt")
        expiry = registry.get("leaseExpiresAt")
        if (not session_id or generation is None or owner_counts.get(session_id) != 1 or
                not isinstance(heartbeat, int) or not isinstance(expiry, int) or
                expiry <= heartbeat or now < heartbeat + (expiry-heartbeat)//2):
            continue
        try:
            require_live_manifest(session_id, "coordinator")
        except AdmissionError:
            continue
        stable = {"generation": registry.get("generation"), "lastHeartbeatAt": heartbeat, "leaseExpiresAt": expiry}
        identity = f"{path.stem}:coordinator-tick-due:{session_id}:{generation}:{heartbeat}:{expiry}"
        rows.append({"incidentId": "tick-"+hashlib.sha256(identity.encode()).hexdigest()[:20],
                     "kind": "coordinator-tick-due", "state": "open", "sessionId": session_id,
                     "project": path.stem, "severity": "medium", "firstSeenAt": heartbeat+(expiry-heartbeat)//2,
                     "evidence": stable,
                     "evidenceFingerprint": hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                     "conditionRevision": 1})
    return rows


def direct_lane_exhausted(project: str, target: dict[str, Any] | None = None) -> bool:
    """True when this project's direct tick lane cannot reach *this* coordinator:
    the durable state for the same target identity is blocked, or its bounded
    re-wakes are spent. A state belonging to a superseded generation is not
    exhaustion — that case is superseded with a fresh cycle instead."""
    digest = hashlib.sha256(str(project).encode()).hexdigest()[:20]
    state = read_json(TICK_STATES / f"{digest}.json")
    if not state:
        return False
    if target and (state.get("targetSessionId") != target.get("targetSessionId")
                   or str(state.get("targetGeneration")) != str(target.get("targetGeneration"))):
        return False
    if state.get("phase") == "blocked":
        return True
    return (state.get("phase") == "consumed"
            and int(state.get("rewakeCount") or 0) >= MAX_REWAKES)


def admission_batches(controller_session: str) -> list[dict[str, Any]]:
    validate_fencing_inputs()
    direct: dict[tuple[str, str, str], dict[str, Any]] = {}
    complex_rows: list[dict[str, Any]] = []
    # Concrete incidents consume the bounded batch first; a due heartbeat tick
    # joins the same envelope only when capacity remains.
    for row in [*actionable_incidents(), *scheduled_tick_rows(NOW_MS())]:
        target = direct_target(row)
        if target and direct_lane_exhausted(target["project"], target):
            # The direct lane for this project is durably blocked or has spent its
            # bounded re-wakes: the coordinator is provably unreachable by queue
            # delivery. Routine kinds normally never reach the controller, so a
            # dead coordinator had no escalation path at all and stayed dead. Hand
            # these incidents to the controller lane, which owns wake/rotation.
            target = None
        if target:
            key = (target["project"], target["targetSessionId"], target["coordinatorGeneration"])
            direct.setdefault(key, {**target, "rows": []})["rows"].append(row)
        else:
            complex_rows.append(row)
    batches = []
    for key in sorted(direct):
        batch = direct[key]
        batch["rows"] = batch["rows"][:MAX_INCIDENTS]
        batches.append(batch)
    if complex_rows:
        batches.append({"targetType": "recovery-controller", "targetKind": "controller",
                        "targetSessionId": controller_session, "targetGeneration": f"session:{controller_session}",
                        "rows": complex_rows[:MAX_INCIDENTS]})
    return batches


def state_path(batch: dict[str, Any]) -> Path:
    if batch["targetType"] == "recovery-controller":
        return STATE
    digest = hashlib.sha256(str(batch["project"]).encode()).hexdigest()[:20]
    return TICK_STATES / f"{digest}.json"


def all_target_states() -> list[dict[str, Any]]:
    rows = []
    if state := read_json(STATE):
        rows.append(state)
    rows.extend(row for path in sorted(TICK_STATES.glob("*.json")) if (row := read_json(path)))
    return rows


def coordinator_health(now: int) -> list[dict[str, Any]]:
    out = []
    for path in sorted(COORDINATORS.glob("*.json")):
        row = read_json(path) or {}
        sid = str(row.get("coordinatorSessionId") or "")
        man = manifest(sid) if sid else None
        heartbeat, expiry = int(row.get("lastHeartbeatAt") or 0), int(row.get("leaseExpiresAt") or 0)
        children = []
        for child in row.get("activeChildren") or []:
            lease = read_json(WORKER_LEASES / f"{child}.json") or {}
            child_hb = int(lease.get("lastHeartbeatAt") or 0)
            children.append({"sessionId": child, "state": lease.get("state"), "heartbeatAgeMs": max(0, now-child_hb) if child_hb else None})
        live_children = [child for child in children if child["state"] in {"active", "starting", "suspect"} and child["heartbeatAgeMs"] is not None and child["heartbeatAgeMs"] <= 900000]
        if not man or man.get("isArchived") or man.get("sessionStatus") in {"done", "cancelled", "error"}:
            health = "failed"
        elif row.get("state") == "hold":
            health = "idle-healthy"
        elif live_children:
            health = "child-active"
        elif expiry and now <= expiry:
            health = "active" if children else "idle-healthy"
        elif expiry and now-expiry <= 900000:
            health = "suspect"
        else:
            health = "stalled"
        out.append({"project": path.stem, "sessionId": sid, "generation": row.get("generation"), "health": health,
                    "heartbeatAgeMs": max(0, now-heartbeat) if heartbeat else None,
                    "leaseExpiredByMs": max(0, now-expiry) if expiry else None,
                    "activeChildren": len(live_children), "registeredChildren": len(children)})
    return out


def load_config() -> dict[str, Any]:
    row = read_json(CONFIG)
    if not row or row.get("version") != 2 or not isinstance(row.get("automations"), dict):
        raise AdmissionError("automations.json missing or invalid")
    return row


def install_guard(args: argparse.Namespace) -> int:
    template = read_json(Path(args.template).expanduser())
    if not template:
        raise AdmissionError("automation template missing or invalid")
    candidates = [row for row in template.get("automations", {}).get("SchedulerTick", []) if row.get("id") == AUTOMATION_ID]
    if len(candidates) != 1:
        raise AdmissionError("template must contain exactly one disabled v3.2.2 legacy guard")
    config = read_json(CONFIG) or {"version": 2, "automations": {}}
    if config.get("version") != 2 or not isinstance(config.get("automations"), dict):
        raise AdmissionError("existing automations config invalid")
    sched = config["automations"].setdefault("SchedulerTick", [])
    matches = [row for row in sched if row.get("id") == AUTOMATION_ID]
    if len(matches) > 1:
        raise AdmissionError("duplicate recovery admission guard id")
    if not matches:
        sched.insert(0, json.loads(json.dumps(candidates[0])))
    if args.apply:
        for rows in config["automations"].values():
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("id") in LEGACY_AUTOMATION_IDS | {AUTOMATION_ID}:
                        row["enabled"] = False
        atomic_json(CONFIG, config)
    print(json.dumps({"schemaVersion": 3, "applied": args.apply, "guardCount": 1, "legacyDisabled": True}, indent=2))
    return 0


def valid_config_value(value: str | None, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ch in value for ch in "\r\n\x00"):
        raise AdmissionError(f"explicit {label} is required")
    return value.strip()


def expected_runtime_version(args: argparse.Namespace) -> str:
    return valid_config_value(args.expected_runtime_version or os.environ.get("CRAFT_EXPECTED_RUNTIME_VERSION"), "expected runtime version")


def expected_runtime_commit(args: argparse.Namespace) -> str:
    return valid_config_value(args.expected_runtime_commit or os.environ.get("CRAFT_EXPECTED_RUNTIME_COMMIT"), "expected runtime commit")


def workspace_id(args: argparse.Namespace) -> str:
    return valid_config_value(args.workspace_id or os.environ.get("CRAFT_WORKSPACE_ID"), "workspace ID")


def server_token() -> str:
    token = os.environ.get("CRAFT_SERVER_TOKEN")
    if token:
        return token
    try:
        info = TOKEN_FILE.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise AdmissionError("server token file must be owner-only")
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise AdmissionError("server token unavailable") from exc
    if not token:
        raise AdmissionError("server token unavailable")
    return token


def rpc_command() -> list[str]:
    try:
        command = shlex.split(os.environ.get("CRAFT_RPC_CLI", "craft-cli"), posix=True)
    except ValueError as exc:
        raise AdmissionError("CRAFT_RPC_CLI is invalid") from exc
    if not command:
        raise AdmissionError("CRAFT_RPC_CLI is invalid")
    return command + ["--json"]


def rpc_env(token: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(HOME), "CRAFT_SERVER_TOKEN": token}
    if url := os.environ.get("CRAFT_SERVER_URL"):
        env["CRAFT_SERVER_URL"] = url
    return env


def rpc_json(args: list[str], token: str, *, mutation: bool = False, expected_type: type = dict) -> Any:
    try:
        cp = subprocess.run(rpc_command()+args, text=True, capture_output=True,
                            timeout=RPC_TIMEOUT_SECONDS, env=rpc_env(token))
    except (OSError, subprocess.TimeoutExpired) as exc:
        if mutation:
            raise DeliveryUnknown("admission mutation outcome unavailable") from exc
        raise TransientRpcError("Craft CLI unavailable") from exc
    if cp.returncode and not mutation:
        raise TransientRpcError("Craft CLI rejected discovery/inspection query")
    try:
        value = json.loads(cp.stdout)
    except Exception as exc:
        if mutation:
            raise DeliveryUnknown("admission mutation outcome unavailable") from exc
        raise TransientRpcError("Craft CLI response invalid") from exc
    if not isinstance(value, expected_type):
        if mutation:
            raise DeliveryUnknown("admission mutation outcome unavailable")
        raise TransientRpcError("Craft CLI response type invalid")
    return value


def verify_capabilities(args: argparse.Namespace, token: str) -> dict[str, Any]:
    capabilities = rpc_json(["automation", "capabilities"], token)
    expected = {
        "available": True,
        "version": CAPABILITY_VERSION,
        "runtimeVersion": expected_runtime_version(args),
        "runtimeCommit": expected_runtime_commit(args),
        "actions": ["session-message"],
        "states": ["prepared", "delivering", "committed", "completed", "blocked"],
        "deliveryStates": ["delivered", "pending-consumption", "consumed", "duplicate", "busy", "blocked"],
        "targetKinds": ["controller", "coordinator"],
        "minimumRecoveryAgeMs": 60000,
        "claimChannel": CLAIM_CHANNEL,
        "deliverChannel": DELIVER_CHANNEL,
        "inspectChannel": INSPECT_CHANNEL,
        "recoverChannel": RECOVER_CHANNEL,
    }
    if capabilities != expected or RECOVERY_MIN_AGE_MS < expected["minimumRecoveryAgeMs"]:
        raise CapabilityError("Craft admission capability-v2/runtime identity unavailable or mismatched")
    return capabilities


def verify_workspace_binding(configured_workspace_id: str, token: str) -> dict[str, Any]:
    rows = rpc_json(["workspaces"], token, expected_type=list)
    matches = [row for row in rows if isinstance(row, dict) and row.get("id") == configured_workspace_id]
    if len(matches) != 1:
        raise CapabilityError("configured Craft workspace ID is unavailable or ambiguous")
    try:
        rpc_root = Path(str(matches[0]["rootPath"])).expanduser().resolve()
        configured_root = WORKSPACE.resolve()
    except Exception as exc:
        raise CapabilityError("target workspace binding is invalid") from exc
    if rpc_root != configured_root:
        raise CapabilityError("Craft workspace ID is not bound to the configured workspace")
    return matches[0]


def verify_runtime(args: argparse.Namespace) -> int:
    token = server_token()
    capabilities = verify_capabilities(args, token)
    workspace = verify_workspace_binding(workspace_id(args), token)
    print(json.dumps({"schemaVersion": 3, "verified": True, "capabilityVersion": capabilities["version"],
                      "runtimeVersion": capabilities["runtimeVersion"], "runtimeCommit": capabilities["runtimeCommit"],
                      "workspaceId": workspace["id"], "workspaceRootPath": workspace["rootPath"]}, indent=2))
    return 0


def scope_for(batch: dict[str, Any], initial_fingerprint: str) -> dict[str, str]:
    # Scope identity contains no wall clock. A continuously observed condition
    # therefore replays/coalesces into one runtime envelope; confirmed
    # recurrence or a new heartbeat lease changes conditionRevision/evidence.
    identity = (f"{batch['targetType']}:{batch.get('project','global')}:"
                f"{batch['targetSessionId']}:{batch['targetGeneration']}:{initial_fingerprint}")
    digest = hashlib.sha256(identity.encode()).hexdigest()
    action = COORDINATOR_ACTION_ID if batch["targetType"] == "coordinator" else CONTROLLER_ACTION_ID
    occurrence = f"protocol-v322-{digest}"
    return {"matcherId": AUTOMATION_ID, "actionId": action, "occurrenceId": occurrence, "key": occurrence}


def cycle_message(batch: dict[str, Any], fp: str, ids: list[str]) -> str:
    # PROTOCOL_VERSION is the installed protocol; the admission lane wire format
    # stays v3.2.2 — its identifier is baked into occurrence/idempotency keys.
    if batch["targetType"] == "coordinator":
        return (f"COORDINATOR TICK {PROTOCOL_VERSION} (admission lane v3.2.2)\n"
                f"project: {batch['project']}\ncoordinatorGeneration: {batch['coordinatorGeneration']}\n"
                f"fingerprint: {fp}\nincidentIds: {','.join(ids)}\n"
                "Reconcile the exact registry generation, active children, and external waits; continue executable lanes. "
                "Renew authority only through a completed coordinator turn. Do not rotate, reap, bypass gates, or send routine owner-facing reports. "
                "Re-read the installed coordinator-lifecycle-protocol skill if any of its rules is not immediately recalled; the installed protocol version is authoritative over your spawn-time copy.\n")
    return (f"RECOVERY ADMISSION {PROTOCOL_VERSION} (admission lane v3.2.2)\n"
            f"fingerprint: {fp}\nincidentIds: {','.join(ids)}\n"
            "Acquire the bounded controller lease and apply only ledger-authorized complex recovery.\n")


def prepared_cycle(now: int, workspace: str, batch: dict[str, Any]) -> dict[str, Any]:
    rows = batch["rows"]
    fp = incident_fingerprint(rows)
    ids = [str(row["incidentId"]) for row in rows]
    value = {"schemaVersion": 3, "phase": "prepared", "mode": "capability-v2", "preparedAt": now,
             "workspaceId": workspace, "targetType": batch["targetType"], "targetKind": batch["targetKind"],
             "targetSessionId": batch["targetSessionId"], "targetGeneration": batch["targetGeneration"],
             "fingerprint": fp, "incidentIds": ids, "scope": scope_for(batch, fp), "recoveryAttempts": 0}
    if batch["targetType"] == "coordinator":
        value.update(project=batch["project"], coordinatorGeneration=batch["coordinatorGeneration"])
    value["message"] = cycle_message(batch, fp, ids)
    return value


def validate_scope(state: dict[str, Any]) -> dict[str, str]:
    scope = state.get("scope")
    if not isinstance(scope, dict) or any(not isinstance(scope.get(key), str) or not scope[key]
                                          for key in ("matcherId", "actionId", "occurrenceId", "key")):
        raise AdmissionError("admission scope invalid")
    return scope  # type: ignore[return-value]


def scope_args(state: dict[str, Any]) -> list[str]:
    scope = validate_scope(state)
    return ["--workspace", str(state["workspaceId"]), "--session", str(state["targetSessionId"]),
            "--matcher", scope["matcherId"], "--action", scope["actionId"],
            "--occurrence", scope["occurrenceId"], "--key", scope["key"]]


def target_identity_args(state: dict[str, Any]) -> list[str]:
    return ["--target-kind", str(state["targetKind"]), "--target-id", str(state["targetSessionId"]),
            "--target-generation", str(state["targetGeneration"])]


def content_revision(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def validate_receipt(state: dict[str, Any], receipt: Any, *, message_id: str | None = None) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise AdmissionError("capability-v2 admission receipt missing")
    expected = {"workspaceId": state["workspaceId"], "sessionId": state["targetSessionId"],
                "targetKind": state["targetKind"], "targetId": state["targetSessionId"],
                "targetGeneration": state["targetGeneration"],
                "matcherId": state["scope"]["matcherId"], "actionId": state["scope"]["actionId"],
                "occurrenceId": state["scope"]["occurrenceId"], "idempotencyKey": state["scope"]["key"]}
    if any(str(receipt.get(key)) != str(value) for key, value in expected.items()):
        raise AdmissionError("capability-v2 admission receipt identity mismatch")
    if message_id is not None and receipt.get("messageId") != message_id:
        raise AdmissionError("capability-v2 admission receipt message mismatch")
    revision = receipt.get("contentRevision")
    if (not isinstance(revision, str) or len(revision) != 64 or
            any(ch not in "0123456789abcdef" for ch in revision) or
            revision != content_revision(str(state["message"]))):
        raise AdmissionError("capability-v2 admission receipt content revision mismatch")
    completion_keys = {"completedContentRevision", "completedProcessingGeneration", "completedMessageId",
                       "completedMessageAt", "consumedAt"}
    if (receipt.get("deliveryState") != "consumed" and any(key in receipt for key in completion_keys)):
        raise AdmissionError("capability-v2 admission receipt optional completion fields must be omitted")
    if (receipt.get("deliveryState") not in INSPECT_STATUSES or
            not isinstance(receipt.get("deliveredAt"), int) or isinstance(receipt.get("deliveredAt"), bool) or receipt["deliveredAt"] <= 0 or
            not isinstance(receipt.get("acceptedProcessingGeneration"), int) or
            isinstance(receipt.get("acceptedProcessingGeneration"), bool) or receipt["acceptedProcessingGeneration"] < 0):
        raise AdmissionError("capability-v2 admission receipt lifecycle invalid")
    return receipt


def validate_consumed_receipt(state: dict[str, Any], receipt: Any, *, message_id: str | None = None) -> dict[str, Any]:
    expected_message_id = message_id if message_id is not None else str(state["messageId"])
    value = validate_receipt(state, receipt, message_id=expected_message_id)
    completed_generation = value.get("completedProcessingGeneration")
    completed_at, consumed_at = value.get("completedMessageAt"), value.get("consumedAt")
    if (value.get("deliveryState") != "consumed" or
            value.get("completedContentRevision") != value.get("contentRevision") or
            not isinstance(completed_generation, int) or isinstance(completed_generation, bool) or completed_generation < 0 or
            not isinstance(value.get("completedMessageId"), str) or not value["completedMessageId"].strip() or
            value.get("completedMessageId") == value.get("messageId") or
            not isinstance(completed_at, int) or isinstance(completed_at, bool) or completed_at < value["deliveredAt"] or
            not isinstance(consumed_at, int) or isinstance(consumed_at, bool) or consumed_at < completed_at):
        raise AdmissionError("capability-v2 consumed receipt proof invalid")
    return value


def deliver(state: dict[str, Any], token: str) -> dict[str, Any]:
    response = rpc_json(["automation", "deliver", *scope_args(state), *target_identity_args(state), str(state["message"])], token, mutation=True)
    status = response.get("status")
    if status == "busy":
        raise DeliveryUnknown("capability-v2 delivery busy")
    if status == "blocked" or status not in DELIVERY_STATUSES:
        raise AdmissionError("capability-v2 delivery response blocked or invalid")
    receipt = validate_receipt(state, response.get("receipt"))
    message_id = response.get("messageId") or receipt.get("messageId")
    if not isinstance(message_id, str) or not message_id or receipt.get("messageId") != message_id:
        raise AdmissionError("capability-v2 delivery message receipt invalid")
    if status != "duplicate" and receipt.get("deliveryState") != status:
        raise AdmissionError("capability-v2 delivery receipt state mismatch")
    if receipt.get("deliveryState") == "consumed":
        receipt = validate_consumed_receipt(state, receipt, message_id=message_id)
    return {"status": status, "messageId": message_id, "receipt": receipt}


def inspect(state: dict[str, Any], token: str) -> dict[str, Any]:
    response = rpc_json(["automation", "inspect", *scope_args(state)], token)
    status = response.get("status")
    if status == "missing" or status == "blocked" or status not in INSPECT_STATUSES:
        raise AdmissionError("admission inspection outstanding state missing or blocked")
    receipt = validate_receipt(state, response.get("receipt"), message_id=str(state["messageId"]))
    if receipt.get("deliveryState") != status:
        raise AdmissionError("admission inspection receipt state mismatch")
    if status == "consumed":
        receipt = validate_consumed_receipt(state, receipt)
    session = response.get("session")
    if not isinstance(session, dict) or not isinstance(session.get("isProcessing"), bool):
        raise AdmissionError("admission inspection processing state invalid")
    generation = session.get("processingGeneration")
    started = session.get("processingStartedAt")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise AdmissionError("admission inspection durable processing generation invalid")
    if session["isProcessing"] and (not isinstance(started, int) or started <= 0):
        raise AdmissionError("admission inspection processing generation invalid")
    if not session["isProcessing"] and started is not None:
        raise AdmissionError("admission inspection idle timing invalid")
    if not isinstance(session.get("queueDepth"), int) or isinstance(session.get("queueDepth"), bool) or session["queueDepth"] < 0:
        raise AdmissionError("admission inspection queue depth invalid")
    age = session.get("processingAgeMs")
    if (session["isProcessing"] and (not isinstance(age, int) or isinstance(age, bool) or age < 0)) or (
            not session["isProcessing"] and age is not None):
        raise AdmissionError("admission inspection processing age invalid")
    for id_key, at_key in (("lastFinalMessageId", "lastFinalMessageAt"),
                           ("lastErrorMessageId", "lastErrorMessageAt")):
        if id_key not in session or at_key not in session:
            raise AdmissionError(f"admission inspection missing session.{id_key}/{at_key}")
        message_id, message_at = session[id_key], session[at_key]
        if ((message_id is None) != (message_at is None) or
                (message_id is not None and (not isinstance(message_id, str) or not message_id)) or
                (message_at is not None and (not isinstance(message_at, int) or isinstance(message_at, bool) or message_at <= 0))):
            raise AdmissionError(f"admission inspection invalid session.{id_key}/{at_key}")
    return {"status": status, "receipt": receipt, "session": session}


def recover(state: dict[str, Any], inspection: dict[str, Any], token: str, args: argparse.Namespace) -> dict[str, Any]:
    session = inspection["session"]
    generation = session.get("processingGeneration")
    if not session.get("isProcessing") or not isinstance(generation, int) or isinstance(generation, bool):
        raise AdmissionError("recovery requires an exact active processing generation")
    response = rpc_json(["automation", "recover", *scope_args(state), *target_identity_args(state),
                         "--message-id", str(state["messageId"]), "--runtime-version", expected_runtime_version(args),
                         "--runtime-commit", expected_runtime_commit(args), "--processing-generation", str(generation),
                         "--minimum-processing-age-ms", str(RECOVERY_MIN_AGE_MS)], token, mutation=True)
    status = response.get("status")
    if status == "busy":
        if response.get("messageId") != state.get("messageId") or not isinstance(response.get("reason"), str) or not response["reason"]:
            raise AdmissionError("capability-v2 recovery busy response invalid")
        raise TransientRpcError("capability-v2 recovery CAS busy")
    if status == "blocked" or status not in RECOVER_STATUSES:
        raise AdmissionError("capability-v2 recovery response blocked or invalid")
    if response.get("messageId") != state.get("messageId"):
        raise AdmissionError("capability-v2 recovery message mismatch")
    if status == "consumed":
        if "previousProcessingGeneration" in response:
            raise AdmissionError("capability-v2 consumed race must not claim a recovery transition")
        current_generation = response.get("processingGeneration")
        if not isinstance(current_generation, int) or isinstance(current_generation, bool) or current_generation < 0:
            raise AdmissionError("capability-v2 consumed race durable generation invalid")
        receipt = validate_consumed_receipt(state, response.get("receipt"))
        return {"status": "consumed", "processingGeneration": current_generation, "receipt": receipt}
    previous = response.get("previousProcessingGeneration")
    advanced = response.get("processingGeneration")
    if (previous != generation or not isinstance(advanced, int) or isinstance(advanced, bool) or advanced <= generation):
        raise AdmissionError("capability-v2 recovery generation transition mismatch")
    return {"status": "recovered", "previousProcessingGeneration": previous,
            "processingGeneration": advanced}


def probe_deferral(state: dict[str, Any], now: int, reason: str) -> tuple[dict[str, Any], bool]:
    """Record an unobservable safety fact and say whether the budget is spent.

    Each consecutive failure to observe increments the counter; a successful cycle
    clears it. Only a repeatedly unavailable probe becomes durable, and then with a
    reason that names the real problem instead of implying proven danger."""
    deferred = dict(state)
    count = int(deferred.get("probeFailureCount") or 0) + 1
    deferred.update(phase="probe-deferred", probeFailureCount=count,
                    probeFailureReason=reason, probeDeferredAt=now)
    return deferred, count >= MAX_PROBE_FAILURES


def clear_probe_failures(state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("probeFailureCount"):
        return state
    cleared = dict(state)
    for key in ("probeFailureCount", "probeFailureReason", "probeDeferredAt"):
        cleared.pop(key, None)
    return cleared


def record_transport(ok: bool, now: int, reason: str | None = None) -> dict[str, Any]:
    """Remember whether the fleet's transport answered this tick.

    Losing the transport looks exactly like lazy agents: coordinators go quiet,
    the ledger grows, finished workers sit uncollected, and nothing says the
    channel is gone. Observed live 2026-08-14: Tailscale logged out at ~19:02, the
    server's listening address vanished with the interface, and for an hour the
    only visible symptom was a fleet that appeared to have stopped caring."""
    row = read_json(TRANSPORT) or {}
    if ok:
        row.update(lastSuccessAt=now, consecutiveFailures=0, lastFailureReason=None)
    else:
        row.update(lastFailureAt=now, lastFailureReason=reason,
                   consecutiveFailures=int(row.get("consecutiveFailures") or 0) + 1)
        row.setdefault("lastSuccessAt", None)
    row["schemaVersion"] = 1
    try:
        atomic_json(TRANSPORT, row)
    except OSError:
        pass
    return row


def hard_block(state: dict[str, Any], now: int, reason: str) -> dict[str, Any]:
    blocked = dict(state)
    blocked.update(phase="blocked", blockedAt=now, reason=reason)
    return blocked


def batch_matches_state(batch: dict[str, Any], state: dict[str, Any]) -> bool:
    if (state.get("targetType") != batch.get("targetType") or state.get("targetKind") != batch.get("targetKind") or
            state.get("targetSessionId") != batch.get("targetSessionId") or
            str(state.get("targetGeneration")) != str(batch.get("targetGeneration"))):
        return False
    return batch.get("targetType") != "coordinator" or state.get("project") == batch.get("project")


def coalesce_cycle(state: dict[str, Any], batch: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if batch.get("retainStateFingerprint"):
        return state, False
    fp = incident_fingerprint(batch["rows"])
    if fp == state.get("fingerprint"):
        return state, False
    updated = dict(state)
    ids = [str(row["incidentId"]) for row in batch["rows"]]
    updated.update(fingerprint=fp, incidentIds=ids, message=cycle_message(batch, fp, ids), coalescedAt=NOW_MS())
    return updated, True


def process_cycle(path: Path, batch: dict[str, Any], workspace: str, token: str, apply: bool,
                  args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    now = NOW_MS()
    state = read_json(path)
    if path.exists() and state is None:
        raise StateError(f"admission state unreadable: {path}")
    if state and state.get("schemaVersion") != 3:
        blocked = hard_block(state, now, "legacy-admission-state-requires-owner-reset")
        if apply:
            atomic_json(path, blocked)
        return 2, blocked
    if (state and state.get("phase") == "blocked"
            and not batch_matches_state(batch, state)):
        # A durable block binds to its exact target identity/generation. Once the
        # registry rotates to a new authoritative target, the dead generation's
        # block must not wall off the successor's wake lane; supersede it with a
        # fresh cycle for the current target. Same-identity blocks keep the full
        # acknowledge/stable-degraded semantics below.
        state = None
    if state and state.get("phase") == "blocked":
        # A hard block is durable and never auto-cleared or redelivered. The
        # first unchanged observation records an acknowledgement and remains a
        # hard supervisor failure. Later observations of the exact same
        # condition are reported as stable degraded state without permanently
        # poisoning the global launchd exit code. Any meaningful incident
        # fingerprint change reopens exit 2 and requires a fresh acknowledgement.
        incoming_fp = (state.get("fingerprint") if batch.get("retainStateFingerprint")
                       else incident_fingerprint(batch["rows"]))
        observed_fp = state.get("blockedConditionFingerprint") or state.get("fingerprint")
        updated = dict(state)
        if incoming_fp != observed_fp:
            updated.update(blockedConditionFingerprint=incoming_fp,
                           blockedConditionAcknowledgedAt=None,
                           blockedConditionChangedAt=now,
                           blockedConditionIncidentIds=[str(row["incidentId"]) for row in batch["rows"]])
            if apply:
                atomic_json(path, updated)
            return 2, updated
        if not isinstance(state.get("blockedConditionAcknowledgedAt"), int):
            updated.update(blockedConditionFingerprint=incoming_fp,
                           blockedConditionAcknowledgedAt=now,
                           blockedConditionIncidentIds=[str(row["incidentId"]) for row in batch["rows"]])
            if apply:
                atomic_json(path, updated)
            return 2, updated
        return 0, {**state, "stableBlocked": True, "degraded": True}
    if state and not batch_matches_state(batch, state):
        if state.get("phase") in PENDING_PHASES | {"prepared"}:
            blocked = hard_block(state, now, "outstanding-admission-target-generation-changed")
            if apply:
                atomic_json(path, blocked)
            return 2, blocked
        state = None
    if state and state.get("phase") == "consumed" and state.get("fingerprint") == incident_fingerprint(batch["rows"]):
        # A consumed wake used to close an unchanged condition forever. A target
        # that consumed its wake and then died (or simply failed to fix anything)
        # was never woken again, because the incident set never changes while the
        # condition persists — two coordinators sat dead for four hours this way.
        # Re-wake the same condition a bounded number of times, and only after a
        # quiet period with no completed turn since consumption.
        rewakes = int(state.get("rewakeCount") or 0)
        consumed_at = int(state.get("consumedAt") or state.get("deliveredAt") or now)
        quiet = now - consumed_at >= REWAKE_QUIET_MS
        if rewakes >= MAX_REWAKES or not quiet:
            return 0, state
        state = {**prepared_cycle(now, workspace, batch), "rewakeCount": rewakes + 1,
                 "rewakeOf": state.get("messageId"), "rewakeReason": "condition-unresolved-after-consumed-wake"}
        if apply:
            atomic_json(path, state)
    elif state is None or state.get("phase") in {"consumed", "probe-deferred"}:
        # A deferred probe is a retry, not a verdict: start the cycle afresh and
        # carry the failure count so a permanently unavailable probe still ends
        # in a durable block instead of retrying forever.
        carried = int((state or {}).get("probeFailureCount") or 0)
        state = prepared_cycle(now, workspace, batch)
        if carried:
            state["probeFailureCount"] = carried
    elif state.get("phase") == "prepared":
        # No message ID exists yet, so a crash-replayed prepare may safely fold
        # in newer incidents before the first/duplicate-safe delivery.
        state, _ = coalesce_cycle(state, batch)
    if not apply:
        return 0, state
    if state["phase"] == "prepared":
        atomic_json(path, state)
        receipt = deliver(state, token)
        state.update(messageId=receipt["messageId"], deliveredAt=receipt["receipt"]["deliveredAt"], receipt=receipt["receipt"])
        receipt_state = receipt["receipt"]["deliveryState"]
        state["phase"] = "consumed" if receipt_state == "consumed" else (
            "pending-consumption" if receipt["status"] in {"pending-consumption", "duplicate"} else "delivered")
        state["deliveryStatus"] = receipt["status"]
        state = clear_probe_failures(state)
        if state["phase"] == "consumed":
            state["consumedAt"] = receipt["receipt"]["consumedAt"]
        atomic_json(path, state)
        # A duplicate proves an earlier attempt may already have been pending
        # for a long time. Inspect immediately against the original receipt
        # timestamp instead of postponing its deadline to this retry.
        if receipt["status"] != "duplicate" and now <= int(state["deliveredAt"]):
            return 0, state

    inspection = inspect(state, token)
    outstanding_status = inspection["status"]
    session_inspection = inspection["session"]
    state["lastInspectedAt"] = now
    state["receipt"] = inspection["receipt"]
    state["lastInspection"] = {key: session_inspection.get(key) for key in
                               ("isProcessing", "processingGeneration", "processingStartedAt", "processingAgeMs",
                                "queueDepth", "lastFinalMessageId", "lastFinalMessageAt",
                                "lastErrorMessageId", "lastErrorMessageAt")}
    if outstanding_status == "consumed":
        state.update(phase="consumed", consumedAt=inspection["receipt"]["consumedAt"])
        atomic_json(path, state)
        return 0, state

    state, changed = coalesce_cycle(state, batch)
    if changed:
        receipt = deliver(state, token)
        if receipt["messageId"] != state.get("messageId") or receipt["receipt"]["deliveryState"] == "consumed":
            if receipt["messageId"] != state.get("messageId"):
                raise AdmissionError("coalesced admission changed outstanding message ID")
            state.update(phase="consumed", consumedAt=receipt["receipt"]["consumedAt"],
                         deliveryStatus="consumed", receipt=receipt["receipt"])
            atomic_json(path, state)
            return 0, state
        state.update(phase="pending-consumption", deliveryStatus=receipt["status"], receipt=receipt["receipt"],
                     deliveredAt=receipt["receipt"]["deliveredAt"])

        # Delivery can synchronously queue/start the newer coalesced revision.
        # The inspection above describes the old revision and must not be used
        # to declare the refreshed envelope idle at its historical deadline.
        # Re-inspect the exact receipt after delivery before any deadline or
        # recovery decision.
        inspection = inspect(state, token)
        outstanding_status = inspection["status"]
        session_inspection = inspection["session"]
        state["lastInspectedAt"] = NOW_MS()
        state["receipt"] = inspection["receipt"]
        state["lastInspection"] = {key: session_inspection.get(key) for key in
                                   ("isProcessing", "processingGeneration", "processingStartedAt", "processingAgeMs",
                                    "queueDepth", "lastFinalMessageId", "lastFinalMessageAt",
                                    "lastErrorMessageId", "lastErrorMessageAt")}
        if outstanding_status == "consumed":
            state.update(phase="consumed", consumedAt=inspection["receipt"]["consumedAt"])
            atomic_json(path, state)
            return 0, state

    started = session_inspection.get("processingStartedAt")
    stuck = session_inspection.get("isProcessing") and isinstance(started, int) and now-started >= RECOVERY_MIN_AGE_MS
    delivered_at = int(state.get("deliveredAt") or state.get("preparedAt") or now)
    last_final_at = session_inspection.get("lastFinalMessageAt")
    completed_turn_after_delivery = (isinstance(last_final_at, int) and not isinstance(last_final_at, bool)
                                     and last_final_at > delivered_at)
    idle_expired = (not session_inspection.get("isProcessing") and now-delivered_at >= RECOVERY_MIN_AGE_MS)
    if idle_expired and completed_turn_after_delivery:
        # The runtime never attributed consumption, yet the target completed at
        # least one full turn after this delivery: ordered message processing means
        # the injected wake reached the session. Busy-session attribution gaps and
        # stale duplicate receipts from a recurring incident fingerprint both land
        # here; hard-blocking would sever the wake lane of a demonstrably live
        # target, so record deterministic liveness-proven consumption instead.
        state.update(phase="consumed", consumedAt=last_final_at,
                     consumedVia="completed-turn-liveness")
        atomic_json(path, state)
        return 0, state
    if idle_expired:
        state = hard_block(state, now, "pending-admission-not-processing-at-deadline")
        atomic_json(path, state)
        return 2, state
    if stuck and int(state.get("recoveryAttempts") or 0) == 0:
        recovery = recover(state, inspection, token, args)
        if recovery["status"] == "consumed":
            proof = recovery["receipt"]
            state.update(phase="consumed", consumedAt=proof["consumedAt"], receipt=proof,
                         recoveryAttempts=1, recovery=recovery)
            atomic_json(path, state)
            return 0, state
        state.update(phase="recovering", recoveryAttempts=1, recoveryStartedAt=now, recovery=recovery)
    elif stuck and int(state.get("recoveryAttempts") or 0) >= 1:
        state = hard_block(state, now, "bounded-admission-recovery-exhausted")
        atomic_json(path, state)
        return 2, state
    else:
        state["phase"] = "pending-consumption"
    atomic_json(path, state)
    return 0, state


def report(args: argparse.Namespace) -> int:
    now = NOW_MS()
    batches = admission_batches(getattr(args, "controller_session", None) or "<configured-controller>")
    health = coordinator_health(now)
    out = {"schemaVersion": 3, "mode": "report-only", "disabled": DISABLED.exists(),
           "states": all_target_states(), "actionableCount": sum(len(batch["rows"]) for batch in batches),
           "batches": [{key: batch.get(key) for key in ("targetType", "project", "targetSessionId", "coordinatorGeneration")} |
                       {"incidentIds": [row.get("incidentId") for row in batch["rows"]]} for batch in batches],
           "coordinatorHealth": health,
           "healthSummary": {name: sum(1 for row in health if row["health"] == name)
                             for name in ("active", "child-active", "idle-healthy", "suspect", "stalled", "failed")}}
    print(json.dumps(out, indent=2))
    return 0


def tick(args: argparse.Namespace) -> int:
    now = NOW_MS()
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if DISABLED.exists():
            print(json.dumps({"schemaVersion": 3, "applied": False, "reason": "kill-switch-active", "states": all_target_states()}, indent=2))
            return 2
        workspace = workspace_id(args)
        batches = admission_batches(args.controller_session)
        # Pending targets must still be inspected even when their triggering
        # incidents have cleared; consumption is a server receipt, never local inference.
        by_path = {state_path(batch): batch for batch in batches}
        for path in [STATE, *sorted(TICK_STATES.glob("*.json"))]:
            state = read_json(path)
            if not state or state.get("phase") not in PENDING_PHASES | {"prepared"} or path in by_path:
                continue
            synthetic = {"targetType": state.get("targetType"), "targetKind": state.get("targetKind"),
                         "targetSessionId": state.get("targetSessionId"), "targetGeneration": state.get("targetGeneration"),
                         "project": state.get("project"), "coordinatorGeneration": state.get("coordinatorGeneration"),
                         "retainStateFingerprint": True,
                         "rows": [{"incidentId": iid, "evidenceFingerprint": "retained", "conditionRevision": 1}
                                  for iid in state.get("incidentIds") or []]}
            by_path[path] = synthetic
        if not by_path:
            print(json.dumps({"schemaVersion": 3, "applied": args.apply, "reason": "no-actionable-or-outstanding-admissions", "states": all_target_states()}, indent=2))
            return 0

        pre_results = []
        invalid_paths = []
        for path, batch in by_path.items():
            if path.exists() and read_json(path) is None:
                pre_results.append({"schemaVersion": 3, "phase": "blocked",
                                    "reason": f"admission state unreadable: {path}",
                                    "statePreserved": True, "path": str(path)})
                invalid_paths.append(path)
                continue
            try:
                target_manifest = (require_persistent_controller(batch["targetSessionId"])
                                   if batch["targetType"] == "recovery-controller"
                                   else require_exact_coordinator_target(batch))
                require_manifest_workspace(target_manifest)
            except ProbeUnavailable as exc:
                current = read_json(path) or prepared_cycle(now, workspace, batch)
                deferred, exhausted = probe_deferral(current, now, str(exc))
                if exhausted:
                    deferred = hard_block(deferred, now,
                                          f"probe-unavailable-repeatedly: {exc}")
                if args.apply:
                    atomic_json(path, deferred)
                pre_results.append(deferred)
                invalid_paths.append(path)
            except AdmissionError as exc:
                current = read_json(path) or prepared_cycle(now, workspace, batch)
                blocked = hard_block(current, now, str(exc))
                if args.apply:
                    atomic_json(path, blocked)
                pre_results.append(blocked)
                invalid_paths.append(path)
        for path in invalid_paths:
            by_path.pop(path, None)
        if not by_path:
            print(json.dumps({"schemaVersion": 3, "applied": args.apply, "results": pre_results}, indent=2))
            return 2 if pre_results else 0
        if not args.apply:
            results = [*pre_results, *[process_cycle(path, batch, workspace, "", False, args)[1]
                                      for path, batch in by_path.items()]]
            print(json.dumps({"schemaVersion": 3, "applied": False, "results": results}, indent=2))
            return 2 if pre_results else 0

        token = server_token()
        try:
            verify_capabilities(args, token)
            verify_workspace_binding(workspace, token)
        except TransientRpcError as exc:
            results = []
            for path, batch in by_path.items():
                state = read_json(path) or prepared_cycle(now, workspace, batch)
                atomic_json(path, state)
                results.append(state)
            record_transport(False, now, f"discovery-retry: {exc}")
            print(json.dumps({"schemaVersion": 3, "applied": True, "reason": "discovery-retry", "detail": str(exc), "results": results}, indent=2))
            return 75
        except (AdmissionError, CapabilityError) as exc:
            results = []
            for path, batch in by_path.items():
                blocked = hard_block(read_json(path) or prepared_cycle(now, workspace, batch), now, str(exc))
                atomic_json(path, blocked)
                results.append(blocked)
            print(json.dumps({"schemaVersion": 3, "applied": True, "results": results}, indent=2))
            return 2

        results, exit_code = list(pre_results), (2 if pre_results else 0)
        for path, batch in by_path.items():
            if DISABLED.exists():
                blocked = hard_block(read_json(path) or prepared_cycle(now, workspace, batch), NOW_MS(), "kill-switch-active-before-target-mutation")
                atomic_json(path, blocked)
                results.append(blocked)
                exit_code = 2
                continue
            try:
                code, result = process_cycle(path, batch, workspace, token, True, args)
            except StateError as exc:
                result = {"schemaVersion": 3, "phase": "blocked", "reason": str(exc),
                          "statePreserved": True, "path": str(path)}
                code = 2
            except (DeliveryUnknown, TransientRpcError) as exc:
                record_transport(False, now, str(exc))
                result = read_json(path) or prepared_cycle(now, workspace, batch)
                atomic_json(path, result)
                result = {**result, "retryReason": str(exc)}
                code = 75
            except AdmissionError as exc:
                result = hard_block(read_json(path) or prepared_cycle(now, workspace, batch), NOW_MS(), str(exc))
                atomic_json(path, result)
                code = 2
            results.append(result)
            exit_code = max(exit_code, code)
        if exit_code != 75:
            # The channel answered this tick: whatever else went wrong is local.
            record_transport(True, NOW_MS())
        print(json.dumps({"schemaVersion": 3, "applied": True, "results": results}, indent=2))
        return exit_code


def disarm(args: argparse.Namespace) -> int:
    if not DISABLED.exists() and not args.force:
        raise AdmissionError("kill switch is not active; refusing unforced disarm")
    states = all_target_states()
    print(json.dumps({"schemaVersion": 3, "applied": args.apply, "states": states, "disabled": True}, indent=2))
    return 0


def reset(args: argparse.Namespace) -> int:
    paths = [STATE, *sorted(TICK_STATES.glob("*.json"))]
    if getattr(args, "project", None):
        # A scoped reset touches only the named project's tick state; an unrelated
        # in-flight delivery elsewhere no longer forces the operator to wait.
        wanted = args.project.strip().lower()
        scoped = []
        for path in paths:
            state = read_json(path)
            if state and str(state.get("project") or "").lower() == wanted:
                scoped.append(path)
        if not scoped:
            raise AdmissionError(f"no admission state found for project: {wanted}")
        paths = scoped
    changed = []
    for path in paths:
        state = read_json(path)
        if path.exists() and state is None:
            if not args.force:
                raise AdmissionError("unreadable admission state requires --force")
            if args.apply:
                path.unlink(missing_ok=True)
            changed.append(str(path))
            continue
        if not state:
            continue
        if state.get("phase") not in {"blocked", "consumed"} and not args.force:
            raise AdmissionError("reset allowed only from blocked/consumed unless --force")
        if args.apply:
            path.unlink(missing_ok=True)
        changed.append(str(path))
    print(json.dumps({"schemaVersion": 3, "applied": args.apply, "reset": changed}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("report")
    cmd.add_argument("--controller-session")
    cmd.set_defaults(func=report)
    cmd = sub.add_parser("install-guard")
    cmd.add_argument("--template", required=True)
    cmd.add_argument("--apply", action="store_true")
    cmd.set_defaults(func=install_guard)
    cmd = sub.add_parser("disarm")
    cmd.add_argument("--apply", action="store_true")
    cmd.add_argument("--force", action="store_true")
    cmd.set_defaults(func=disarm)
    cmd = sub.add_parser("verify-runtime")
    cmd.add_argument("--workspace-id")
    cmd.add_argument("--expected-runtime-version")
    cmd.add_argument("--expected-runtime-commit")
    cmd.set_defaults(func=verify_runtime)
    cmd = sub.add_parser("tick")
    cmd.add_argument("--controller-session", required=True)
    cmd.add_argument("--workspace-id")
    cmd.add_argument("--expected-runtime-version")
    cmd.add_argument("--expected-runtime-commit")
    cmd.add_argument("--apply", action="store_true")
    cmd.set_defaults(func=tick)
    cmd = sub.add_parser("reset")
    cmd.add_argument("--apply", action="store_true")
    cmd.add_argument("--force", action="store_true")
    cmd.add_argument("--project")
    cmd.set_defaults(func=reset)
    args = parser.parse_args()
    try:
        return args.func(args)
    except BlockingIOError:
        print(json.dumps({"error": "admission supervisor already running"}, indent=2))
        return 75
    except Exception as exc:
        print(json.dumps({"error": str(exc), "command": args.command}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

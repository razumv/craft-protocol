#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Deterministic direct admission delivery to one persistent recovery controller.

This supervisor selects permitted recovery incidents but never creates sessions or
writes Craft session state.  A supported Craft server receives one idempotent,
authenticated direct delivery only after the controller manifest and harness are
proved.  Scheduler prompt matchers remain a disabled legacy installation guard.
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
LOCK = Path(os.environ.get("CRAFT_ADMISSION_LOCK", RUNTIME / "self-healing/admission.lock")).expanduser()
DISABLED = Path(os.environ.get("CRAFT_SELF_HEALING_DISABLED", RUNTIME / "self-healing.disabled")).expanduser()
AUTOMATION_ID = os.environ.get("CRAFT_RECOVERY_NOTIFIER_AUTOMATION_ID", "a321-notifier")
DIRECT_ACTION_ID = "a321-direct-delivery"
CONTROLLER_HARNESS = Path(os.environ.get("CRAFT_CONTROLLER_HARNESS", Path(__file__).with_name("controller-harness.py"))).expanduser()
TOKEN_FILE = Path(os.environ.get("CRAFT_SERVER_TOKEN_FILE", HOME / ".config/craft-agent-headless/server-token")).expanduser()
MAX_INCIDENTS = int(os.environ.get("CRAFT_RECOVERY_ADMISSION_MAX_INCIDENTS", "3"))
COOLDOWN_SECONDS = int(os.environ.get("CRAFT_RECOVERY_ADMISSION_COOLDOWN_SECONDS", "900"))
NOW_MS = lambda: int(os.environ.get("CRAFT_TEST_NOW_MS", "0")) or int(time.time() * 1000)
BLOCKED_KINDS = {"owner-gate-blocked", "cwd-collision", "project-mapping-conflict", "ambiguous-coordinator-owner", "preservation-unknown"}
WAKE_KINDS = {"coordinator-lease-stale", "coordinator-session-error", "coordinator-pi-sigterm", "job-exit-unreported", "heavy-lock-wait"}
SUCCESS_STATUSES = {"delivered", "queued", "duplicate"}


class AdmissionError(ValueError):
    """A deterministic fail-closed admission rejection."""


class CapabilityError(AdmissionError):
    """The running Craft server is not the explicitly supported API."""


class DeliveryUnknown(AdmissionError):
    """The delivery process outcome is unknown and must be retried idempotently."""


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


def manifest(session_id: str) -> dict[str, Any] | None:
    path = SESSIONS / session_id / "session.jsonl"
    try:
        return json.loads(path.open(encoding="utf-8", errors="ignore").readline())
    except Exception:
        return None


def label_value(row: dict[str, Any], prefix: str) -> str | None:
    for label in row.get("labels") or []:
        if isinstance(label, str) and label.startswith(prefix):
            return label.split("::", 1)[1]
    return None


def require_persistent_controller(session_id: str) -> dict[str, Any]:
    row = manifest(session_id)
    if not row:
        raise AdmissionError("persistent controller manifest missing")
    if (row.get("id") or row.get("sessionId")) != session_id:
        raise AdmissionError("persistent controller manifest identity mismatch")
    if row.get("isArchived"):
        raise AdmissionError("persistent controller is archived")
    if row.get("sessionStatus") in {"done", "cancelled", "error"}:
        raise AdmissionError("persistent controller is terminal")
    if label_value(row, "agent-role::") != "recovery-controller":
        raise AdmissionError("session is not recovery-controller")
    if label_value(row, "controller-mode::") != "persistent":
        raise AdmissionError("controller is not marked persistent")
    try:
        cp = subprocess.run([str(CONTROLLER_HARNESS), "report"], text=True, capture_output=True, timeout=10)
        report = json.loads(cp.stdout)
        matches = [item for item in report.get("rows", []) if item.get("sessionId") == session_id]
    except Exception as exc:
        raise AdmissionError("controller harness proof unavailable") from exc
    if (cp.returncode or not report.get("healthy") or len(matches) != 1 or
            matches[0].get("state") != "active" or matches[0].get("sessionRole") != "recovery-controller"):
        raise AdmissionError("persistent controller harness is not uniquely live/proven")
    return row


def live_scope_blocked(row: dict[str, Any], all_rows: list[dict[str, Any]]) -> bool:
    project, session, work_unit = row.get("project"), row.get("sessionId"), row.get("workUnit")
    registry = read_json(COORDINATORS / f"{project}.json") if project else None
    if registry and registry.get("state") in {"hold", "needs-owner"}:
        return True
    for blocker in all_rows:
        if blocker.get("state") not in {"open", "claimed", "deferred"} or blocker.get("kind") not in BLOCKED_KINDS:
            continue
        same_scope = (session and blocker.get("sessionId") == session) or (project and blocker.get("project") == project)
        if same_scope:
            return True
    if project and work_unit:
        for path in (RUNTIME / "owner-gates" / str(project)).glob("*.json"):
            gate = read_json(path) or {}
            if gate.get("state") == "open" and str(gate.get("workUnit") or "") == str(work_unit):
                return True
    return False


def incidents() -> list[dict[str, Any]]:
    all_rows = [row for path in sorted(INCIDENTS.glob("*.json")) if (row := read_json(path))]
    rows = []
    for row in all_rows:
        if row.get("state") != "open" or row.get("kind") in BLOCKED_KINDS or row.get("kind") not in WAKE_KINDS:
            continue
        if not row.get("sessionId") or live_scope_blocked(row, all_rows):
            continue
        rows.append(row)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    rows.sort(key=lambda row: (order.get(str(row.get("severity")), 9), int(row.get("firstSeenAt") or 0), str(row.get("incidentId"))))
    return rows[:MAX_INCIDENTS]


def fingerprint(rows: list[dict[str, Any]]) -> str:
    value = [{"incidentId": row.get("incidentId"), "evidenceFingerprint": row.get("evidenceFingerprint"), "state": row.get("state")} for row in rows]
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def coordinator_health(now: int) -> list[dict[str, Any]]:
    out = []
    for path in sorted(COORDINATORS.glob("*.json")):
        row = read_json(path) or {}
        sid = str(row.get("coordinatorSessionId") or "")
        man = manifest(sid) if sid else None
        heartbeat, expiry = int(row.get("lastHeartbeatAt") or 0), int(row.get("leaseExpiresAt") or 0)
        age = max(0, now - heartbeat) if heartbeat else None
        children = []
        for child in row.get("activeChildren") or []:
            lease = read_json(WORKER_LEASES / f"{child}.json") or {}
            child_hb = int(lease.get("lastHeartbeatAt") or 0)
            children.append({"sessionId": child, "state": lease.get("state"), "heartbeatAgeMs": max(0, now - child_hb) if child_hb else None})
        live_children = [child for child in children if child["state"] in {"active", "starting", "suspect"} and child["heartbeatAgeMs"] is not None and child["heartbeatAgeMs"] <= 900000]
        if not man or man.get("isArchived") or man.get("sessionStatus") in {"done", "cancelled", "error"}:
            health = "failed"
        elif row.get("state") == "hold":
            health = "idle-healthy"
        elif live_children:
            health = "child-active"
        elif expiry and now <= expiry:
            health = "active" if children else "idle-healthy"
        elif expiry and now - expiry <= 900000:
            health = "suspect"
        else:
            health = "stalled"
        out.append({"project": path.stem, "sessionId": sid, "health": health, "heartbeatAgeMs": age,
                    "leaseExpiredByMs": max(0, now - expiry) if expiry else None, "activeChildren": len(live_children), "registeredChildren": len(children)})
    return out


def load_config() -> dict[str, Any]:
    row = read_json(CONFIG)
    if not row or row.get("version") != 2 or not isinstance(row.get("automations"), dict):
        raise AdmissionError("automations.json missing or invalid")
    return row


def disable_legacy_matchers() -> None:
    """Installation/recovery guard only; direct delivery never calls this path."""
    config = load_config()
    for rows in config["automations"].values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("id") in {AUTOMATION_ID, "a31101", "a31102"}:
                row["enabled"] = False
    atomic_json(CONFIG, config)


def install_guard(args: argparse.Namespace) -> int:
    template = read_json(Path(args.template).expanduser())
    if not template:
        raise AdmissionError("automation template missing or invalid")
    candidates = [row for row in template.get("automations", {}).get("SchedulerTick", []) if row.get("id") == AUTOMATION_ID]
    if len(candidates) != 1:
        raise AdmissionError("template must contain exactly one disabled legacy notifier")
    config = read_json(CONFIG) or {"version": 2, "automations": {}}
    if config.get("version") != 2 or not isinstance(config.get("automations"), dict):
        raise AdmissionError("existing automations config invalid")
    sched = config["automations"].setdefault("SchedulerTick", [])
    matches = [row for row in sched if row.get("id") == AUTOMATION_ID]
    if len(matches) > 1:
        raise AdmissionError("duplicate recovery notifier automation id")
    if not matches:
        sched.insert(0, json.loads(json.dumps(candidates[0])))
    if args.apply:
        # Installation is the only normal configuration mutation; the direct path never arms it.
        for rows in config["automations"].values():
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("id") in {AUTOMATION_ID, "a31101", "a31102"}:
                        row["enabled"] = False
        atomic_json(CONFIG, config)
    print(json.dumps({"schemaVersion": 2, "applied": args.apply, "notifierCount": 1, "legacyDisabled": True}, indent=2))
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
    # All adapter responses are machine contracts. The standalone CLI defaults
    # list-shaped results (notably `workspaces`) to a human table unless the
    # global JSON flag precedes the command.
    return command + ["--json"]


def rpc_env(token: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(HOME), "CRAFT_SERVER_TOKEN": token}
    if url := os.environ.get("CRAFT_SERVER_URL"):
        env["CRAFT_SERVER_URL"] = url
    return env


def rpc_json(args: list[str], token: str, *, delivery: bool = False, expected_type: type = dict) -> Any:
    try:
        cp = subprocess.run(rpc_command() + args, text=True, capture_output=True, timeout=20, env=rpc_env(token))
    except (OSError, subprocess.TimeoutExpired) as exc:
        if delivery:
            raise DeliveryUnknown("delivery outcome unavailable") from exc
        raise CapabilityError("Craft CLI unavailable") from exc
    if cp.returncode and not delivery:
        raise CapabilityError("Craft CLI rejected capability query")
    try:
        value = json.loads(cp.stdout)
    except Exception as exc:
        if delivery:
            raise DeliveryUnknown("delivery outcome unavailable") from exc
        raise CapabilityError("Craft CLI capability response invalid") from exc
    if not isinstance(value, expected_type):
        if delivery:
            raise DeliveryUnknown("delivery outcome unavailable")
        raise CapabilityError("Craft CLI response type invalid")
    return value


def verify_capabilities(args: argparse.Namespace, token: str) -> None:
    """Accept only the identity and channel advertised by admissionCapabilities.

    `system:versions` is intentionally not queried: it identifies desktop
    components, not the configured Craft runtime serving this RPC.
    """
    expected_version = expected_runtime_version(args)
    expected_commit = expected_runtime_commit(args)
    capabilities = rpc_json(["automation", "capabilities"], token)
    if (capabilities.get("available") is not True or capabilities.get("version") != 1 or
            capabilities.get("deliverChannel") != "automations:admissionDeliver" or
            capabilities.get("runtimeVersion") != expected_version or
            capabilities.get("runtimeCommit") != expected_commit):
        raise CapabilityError("Craft direct admission capability/runtime identity unavailable or mismatched")


def verify_workspace_binding(configured_workspace_id: str, token: str, controller_manifest: dict[str, Any]) -> None:
    rows = rpc_json(["workspaces"], token, expected_type=list)
    matches = [row for row in rows if isinstance(row, dict) and row.get("id") == configured_workspace_id]
    if len(matches) != 1:
        raise CapabilityError("configured Craft workspace ID is unavailable or ambiguous")
    try:
        rpc_root = Path(str(matches[0]["rootPath"])).expanduser().resolve()
        manifest_root = Path(str(controller_manifest["workspaceRootPath"])).expanduser().resolve()
        configured_root = WORKSPACE.resolve()
    except Exception as exc:
        raise CapabilityError("controller workspace binding is invalid") from exc
    if rpc_root != configured_root or manifest_root != configured_root:
        raise CapabilityError("controller session and Craft workspace ID are not bound to the configured workspace")


def direct_scope(fp: str, prepared_at: int) -> dict[str, str]:
    # A prepared cycle is stable across crash retries, while a later cooldown
    # cycle for the same unresolved fingerprint must produce a fresh wake.
    scope = f"recovery-admission-{fp}-{prepared_at}"
    return {"matcherId": AUTOMATION_ID, "actionId": DIRECT_ACTION_ID, "occurrenceId": scope, "key": scope}


def direct_message(fp: str, ids: list[str]) -> str:
    return "RECOVERY ADMISSION v3.2.1 direct delivery\n" + f"fingerprint: {fp}\nincidentIds: {','.join(ids)}\n"


def prepared_state(now: int, controller: str, workspace: str, fp: str, ids: list[str]) -> dict[str, Any]:
    return {"schemaVersion": 2, "phase": "prepared", "mode": "direct-delivery", "preparedAt": now,
            "controllerSessionId": controller, "workspaceId": workspace, "fingerprint": fp, "incidentIds": ids,
            "scope": direct_scope(fp, now), "message": direct_message(fp, ids), "lastFingerprint": fp,
            "cooldownUntil": now + COOLDOWN_SECONDS * 1000}


def hard_block(state: dict[str, Any], now: int, reason: str) -> dict[str, Any]:
    blocked = dict(state)
    blocked.update(phase="blocked", blockedAt=now, reason=reason)
    return blocked


def deliver(state: dict[str, Any], token: str) -> dict[str, Any]:
    scope = state.get("scope") or {}
    if not isinstance(scope, dict) or any(not isinstance(scope.get(key), str) or not scope[key] for key in ("matcherId", "actionId", "occurrenceId", "key")):
        raise AdmissionError("prepared direct delivery scope invalid")
    response = rpc_json(["automation", "deliver", "--workspace", str(state["workspaceId"]), "--session", str(state["controllerSessionId"]),
                         "--matcher", scope["matcherId"], "--action", scope["actionId"], "--occurrence", scope["occurrenceId"],
                         "--key", scope["key"], str(state["message"])], token, delivery=True)
    status, message_id = response.get("status"), response.get("messageId")
    if status == "busy":
        raise DeliveryUnknown("direct delivery busy")
    if status not in SUCCESS_STATUSES or not isinstance(message_id, str) or not message_id:
        raise AdmissionError("direct delivery blocked or invalid")
    return {"status": status, "messageId": message_id}


def report(args: argparse.Namespace) -> int:
    now = NOW_MS()
    state = read_json(STATE)
    rows = incidents()
    health = coordinator_health(now)
    out = {"schemaVersion": 2, "mode": "report-only", "disabled": DISABLED.exists(), "state": state or {"phase": "idle"},
           "actionableCount": len(rows), "actionableIncidentIds": [row.get("incidentId") for row in rows],
           "coordinatorHealth": health,
           "healthSummary": {name: sum(1 for row in health if row["health"] == name) for name in ("active", "child-active", "idle-healthy", "suspect", "stalled", "failed")}}
    print(json.dumps(out, indent=2))
    return 0


def tick(args: argparse.Namespace) -> int:
    now = NOW_MS()
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read_json(STATE)
        if state and state.get("phase") == "armed":
            blocked = hard_block(state, now, "legacy-scheduler-state-unsupported")
            if args.apply:
                atomic_json(STATE, blocked)
            print(json.dumps({"schemaVersion": 2, "applied": args.apply, "state": blocked}, indent=2))
            return 2
        if state and state.get("phase") == "blocked":
            print(json.dumps({"schemaVersion": 2, "applied": args.apply, "state": state}, indent=2))
            return 2
        if state and state.get("phase") == "notified":
            current_rows = incidents()
            current_fp = fingerprint(current_rows) if current_rows else None
            same_fingerprint_cooling = (current_fp == state.get("lastFingerprint") and
                                        now < int(state.get("cooldownUntil") or 0))
            if same_fingerprint_cooling:
                print(json.dumps({"schemaVersion": 2, "applied": False, "state": state,
                                  "reason": "fingerprint-cooldown"}, indent=2))
                return 0
            state = {"schemaVersion": 2, "phase": "idle", "rearmedAt": now,
                     "previousPhase": "notified", "lastFingerprint": state.get("lastFingerprint"),
                     "cooldownUntil": state.get("cooldownUntil")}
            if args.apply:
                atomic_json(STATE, state)
        if DISABLED.exists():
            print(json.dumps({"schemaVersion": 2, "applied": False, "state": state or {"phase": "idle"}, "reason": "kill-switch-active"}, indent=2))
            return 2
        if state and state.get("phase") == "prepared":
            configured_workspace = workspace_id(args)
            if (state.get("mode") != "direct-delivery" or state.get("controllerSessionId") != args.controller_session or
                    state.get("workspaceId") != configured_workspace):
                blocked = hard_block(state, now, "prepared-direct-delivery-state-invalid")
                if args.apply:
                    atomic_json(STATE, blocked)
                print(json.dumps({"schemaVersion": 2, "applied": args.apply, "state": blocked}, indent=2))
                return 2
            prepared = state
        else:
            rows = incidents()
            if not rows:
                print(json.dumps({"schemaVersion": 2, "applied": args.apply, "state": {"phase": "idle"}, "reason": "no-actionable-incidents"}, indent=2))
                return 0
            fp = fingerprint(rows)
            if state and state.get("lastFingerprint") == fp and now < int(state.get("cooldownUntil") or 0):
                print(json.dumps({"schemaVersion": 2, "applied": False, "state": state, "reason": "fingerprint-cooldown"}, indent=2))
                return 0
            prepared = prepared_state(now, args.controller_session, workspace_id(args), fp, [str(row["incidentId"]) for row in rows])
        controller_manifest = require_persistent_controller(args.controller_session)
        if not args.apply:
            print(json.dumps({"schemaVersion": 2, "applied": False, "state": prepared}, indent=2))
            return 0
        try:
            token = server_token()
            verify_capabilities(args, token)
            verify_workspace_binding(workspace_id(args), token, controller_manifest)
        except (AdmissionError, CapabilityError) as exc:
            blocked = hard_block(prepared, now, str(exc))
            atomic_json(STATE, blocked)
            print(json.dumps({"schemaVersion": 2, "applied": True, "state": blocked}, indent=2))
            return 2
        if state is None or state.get("phase") != "prepared":
            atomic_json(STATE, prepared)
        # Linearization check immediately before the external delivery call. A
        # kill switch created during capability/workspace discovery wins and
        # leaves a durable blocked receipt without invoking admissionDeliver.
        if DISABLED.exists():
            blocked = hard_block(prepared, NOW_MS(), "kill-switch-active-before-delivery")
            atomic_json(STATE, blocked)
            print(json.dumps({"schemaVersion": 2, "applied": True, "state": blocked}, indent=2))
            return 2
        try:
            receipt = deliver(prepared, token)
        except DeliveryUnknown:
            # The server may have received the request.  Keep the exact durable scope for a duplicate-safe retry.
            atomic_json(STATE, prepared)
            print(json.dumps({"schemaVersion": 2, "applied": True, "state": prepared, "reason": "direct-delivery-retry"}, indent=2))
            return 75
        except AdmissionError as exc:
            blocked = hard_block(prepared, now, str(exc))
            atomic_json(STATE, blocked)
            print(json.dumps({"schemaVersion": 2, "applied": True, "state": blocked}, indent=2))
            return 2
        notified = dict(prepared)
        notified.update(phase="notified", notifiedAt=now, notifierSessionId=None,
                        directDelivery={"workspaceId": prepared["workspaceId"], "controllerSessionId": prepared["controllerSessionId"],
                                        **prepared["scope"], **receipt})
        atomic_json(STATE, notified)
        print(json.dumps({"schemaVersion": 2, "applied": True, "state": notified}, indent=2))
        return 0


def disarm(args: argparse.Namespace) -> int:
    if not DISABLED.exists() and not args.force:
        raise AdmissionError("kill switch is not active; refusing unforced disarm")
    now = NOW_MS()
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read_json(STATE) or {"schemaVersion": 2, "phase": "idle"}
        if args.apply:
            if state.get("phase") == "prepared":
                state = hard_block(state, now, "kill-switch-disarm")
            atomic_json(STATE, state)
    print(json.dumps({"schemaVersion": 2, "applied": args.apply, "state": state, "disabled": True}, indent=2))
    return 0


def reset(args: argparse.Namespace) -> int:
    state = read_json(STATE) or {"phase": "idle"}
    if state.get("phase") not in {"blocked", "notified"} and not args.force:
        raise AdmissionError("reset allowed only from blocked/notified unless --force")
    if args.apply:
        state = {"schemaVersion": 2, "phase": "idle", "resetAt": NOW_MS(), "previousPhase": state.get("phase"),
                 "lastFingerprint": state.get("fingerprint") or state.get("lastFingerprint"), "cooldownUntil": state.get("cooldownUntil")}
        atomic_json(STATE, state)
    print(json.dumps({"schemaVersion": 2, "applied": args.apply, "state": state}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("report")
    cmd.set_defaults(func=report)
    cmd = sub.add_parser("install-guard")
    cmd.add_argument("--template", required=True)
    cmd.add_argument("--apply", action="store_true")
    cmd.set_defaults(func=install_guard)
    cmd = sub.add_parser("disarm")
    cmd.add_argument("--apply", action="store_true")
    cmd.add_argument("--force", action="store_true")
    cmd.set_defaults(func=disarm)
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

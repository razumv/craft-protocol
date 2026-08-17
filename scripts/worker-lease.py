#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Deterministic worker/auditor lease registry and stall classifier.

No LLM calls and no session mutation. Active leases are disposable runtime state.
Archived or absent sessions lose their lease, PID fallback, and job receipt during
`reconcile --apply`. All mutations are atomic and guarded by one file lock.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import glob
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

HOME = Path.home()
WORKSPACE = Path(os.environ.get("CRAFT_WORKSPACE", HOME / ".craft-agent/workspaces/general")).expanduser()
SESSIONS = Path(os.environ.get("CRAFT_SESSIONS", WORKSPACE / "sessions")).expanduser()
RUNTIME = Path(os.environ.get("CRAFT_RUNTIME", HOME / ".craft-agent/runtime")).expanduser()
LEASES = RUNTIME / "worker-leases"
JOBS = RUNTIME / "worker-jobs"
COORDINATORS = RUNTIME / "coordinators"
PID_DIR = Path(os.environ.get("CRAFT_PID_DIR", HOME / ".craft-agent/pids")).expanduser()
LOCK = RUNTIME / "worker-leases.lock"
HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
COMMON = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(COMMON)  # type: ignore
SCHEMA = 1
HEALTHY_SECONDS = int(os.environ.get("CRAFT_LEASE_HEALTHY_SECONDS", "900"))
STALLED_SECONDS = int(os.environ.get("CRAFT_LEASE_STALLED_SECONDS", "1800"))
ORPHANED_LANE_SECONDS = int(os.environ.get("CRAFT_ORPHANED_LANE_SECONDS", "86400"))
TERMINAL_STATES = {"handoff-ready"}
TERMINAL_SESSION_STATUSES = {"needs-review", "done"}
ROLES = {"worker", "auditor"}


def now_ms() -> int:
    """Use the shared injectable clock; tests never depend on a wall-clock second."""
    return COMMON.now_ms()


def ensure_dirs() -> None:
    for path in (RUNTIME, LEASES, JOBS, PID_DIR):
        path.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def locked():
    ensure_dirs()
    with LOCK.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def manifest_path(session_id: str) -> Path:
    return SESSIONS / session_id / "session.jsonl"


def read_manifest(session_id: str) -> dict[str, Any] | None:
    path = manifest_path(session_id)
    try:
        return json.loads(path.open(encoding="utf-8", errors="ignore").readline())
    except Exception:
        return None


def all_manifests() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in glob.glob(str(SESSIONS / "*" / "session.jsonl")):
        try:
            with open(raw, encoding="utf-8", errors="ignore") as fh:
                value = json.loads(fh.readline())
            if value.get("id"):
                result[str(value["id"])] = value
        except Exception:
            continue
    return result


def label_value(manifest: dict[str, Any], prefix: str) -> str | None:
    for label in manifest.get("labels") or []:
        if isinstance(label, str) and label.startswith(prefix):
            return label.split("::", 1)[1]
    return None


def role_of(manifest: dict[str, Any]) -> str:
    return label_value(manifest, "agent-role::") or "unknown"


def expand(path: str | None) -> str | None:
    # One canonical path representation for manifests, CLI values, leases and
    # collision scans: resolve symlinks (/var -> /private/var on macOS) first.
    return COMMON.canonical_path(path)


def last_event(session_id: str) -> tuple[int, str | None, str | None]:
    path = manifest_path(session_id)
    latest = (0, None, None)
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            next(fh, None)
            for line in fh:
                try:
                    value = json.loads(line)
                except Exception:
                    continue
                ts = int(value.get("timestamp") or 0)
                if ts >= latest[0]:
                    content = str(value.get("content") or value.get("error") or "")[:500]
                    latest = (ts, value.get("type"), content)
    except Exception:
        pass
    return latest


def pid_alive(raw: Any) -> bool:
    try:
        os.kill(int(raw), 0)
        return True
    except Exception:
        return False


def log_mtime_ms(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        return int(Path(raw).expanduser().stat().st_mtime * 1000)
    except Exception:
        return 0


def parse_ps_time(raw: str) -> float:
    try:
        result = raw.strip()
        if not result:
            return 0.0
        days = 0
        if "-" in result:
            day, result = result.split("-", 1)
            days = int(day)
        parts = result.split(":")
        seconds = float(parts[-1])
        minutes = int(parts[-2]) if len(parts) >= 2 else 0
        hours = int(parts[-3]) if len(parts) >= 3 else 0
        return days * 86400 + hours * 3600 + minutes * 60 + seconds
    except Exception:
        return 0.0


def process_cpu_seconds(raw: Any) -> float:
    try:
        return parse_ps_time(os.popen(f"ps -o time= -p {int(raw)}").read())
    except Exception:
        return 0.0


def process_snapshot() -> list[tuple[int, int, float]]:
    override = os.environ.get("CRAFT_TEST_PS_FILE")
    try:
        if override:
            text = Path(override).expanduser().read_text(encoding="utf-8", errors="ignore")
        else:
            text = os.popen("ps -axo pid=,ppid=,time=").read()
    except Exception:
        return []
    rows: list[tuple[int, int, float]] = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parse_ps_time(parts[2])))
        except ValueError:
            continue
    return rows


def process_tree_cpu_seconds(raw: Any) -> float:
    """Aggregate CPU across the observable job's whole descendant tree.

    A supervisor whose nearly-idle driver delegates the heavy work to a
    descendant (e.g. python -> Blender) is objectively progressing; measuring
    only the direct child demoted such live lanes to suspect/stalled."""
    try:
        root = int(raw)
    except Exception:
        return 0.0
    rows = process_snapshot()
    if not rows:
        return process_cpu_seconds(root)
    children: dict[int, list[int]] = {}
    cpu: dict[int, float] = {}
    for pid, ppid, seconds in rows:
        cpu[pid] = seconds
        children.setdefault(ppid, []).append(pid)
    if root not in cpu:
        return process_cpu_seconds(root)
    total = 0.0
    stack = [root]
    visited: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in visited:
            continue
        visited.add(pid)
        total += cpu.get(pid, 0.0)
        stack.extend(children.get(pid, []))
    return total


def registry_adopted_parent(session_id: str) -> str | None:
    """The coordinator registry is machine truth for post-rotation ownership: a
    two-phase transfer lists adopted children on the successor while creation-time
    `parent-session::` labels keep naming the archived predecessor forever. Without
    this rebind, adopted children cannot submit inbox reports and are invisible to
    the successor's status synthesis."""
    try:
        paths = sorted(COORDINATORS.glob("*.json"))
    except OSError:
        return None
    for path in paths:
        row = read_json(path)
        if not row or row.get("state") not in {"authoritative", "rotating", "hold"}:
            continue
        if session_id in (row.get("activeChildren") or []):
            owner = str(row.get("coordinatorSessionId") or "")
            if owner and owner != session_id:
                return owner
    return None


def lease_path(session_id: str) -> Path:
    return LEASES / f"{session_id}.json"


def job_path(session_id: str) -> Path:
    return JOBS / f"{session_id}.json"


def lease_from_manifest(manifest: dict[str, Any], state: str = "starting") -> dict[str, Any]:
    sid = str(manifest["id"])
    event_at, event_type, event_content = last_event(sid)
    status = manifest.get("sessionStatus")
    if status in ("needs-review", "done"):
        state = "handoff-ready"
    elif event_type == "error":
        state = "error"
    created = int(manifest.get("createdAt") or now_ms())
    return {
        "schemaVersion": SCHEMA,
        "sessionId": sid,
        "parentSessionId": (registry_adopted_parent(sid) or label_value(manifest, "parent-session::")
                            or manifest.get("parentSessionId")),
        "role": role_of(manifest),
        "workUnit": label_value(manifest, "work-unit::"),
        "attempt": label_value(manifest, "attempt::"),
        "worktree": expand(manifest.get("workingDirectory") or manifest.get("sdkCwd")),
        "state": state,
        "phase": "spawned" if state == "starting" else None,
        "createdAt": created,
        "updatedAt": now_ms(),
        "lastHeartbeatAt": created,
        "lastEvidenceAt": event_at or created,
        "lastSessionEventAt": event_at,
        "lastSessionEventType": event_type,
        "lastError": event_content if event_type == "error" else None,
        "childJobPid": None,
        "childCpuSeconds": 0.0,
        "logPath": None,
        "logMtime": 0,
        "preservationState": "unknown",
    }


def save_lease(value: dict[str, Any]) -> None:
    value["schemaVersion"] = SCHEMA
    value["updatedAt"] = now_ms()
    atomic_json(lease_path(str(value["sessionId"])), value)


def quarantine_admission_failure(session_id: str, reason: str,
                                  manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist one fail-closed lease state while the worker-lease lock is held."""
    current = manifest or read_manifest(session_id)
    value = read_json(lease_path(session_id))
    if value is None:
        value = lease_from_manifest(current) if current else {"sessionId": session_id}
    value.update({"sessionId": session_id, "state": "error",
                  "phase": "admission-fail-closed", "lastError": reason})
    save_lease(value)
    return value


def remove_runtime(session_id: str) -> list[str]:
    removed: list[str] = []
    for path in (lease_path(session_id), job_path(session_id), PID_DIR / f"{session_id}.pid"):
        try:
            path.unlink()
            removed.append(str(path))
        except FileNotFoundError:
            pass
    return removed


def classify(lease: dict[str, Any], manifest: dict[str, Any], now: int) -> str:
    status = manifest.get("sessionStatus")
    if status in ("needs-review", "done") or lease.get("state") in TERMINAL_STATES:
        return "handoff-ready"
    event_at, event_type, event_content = last_event(str(manifest["id"]))
    lease["lastSessionEventAt"] = event_at
    lease["lastSessionEventType"] = event_type
    if event_type == "error":
        lease["lastError"] = event_content
        return "error"
    job = read_json(job_path(str(manifest["id"]))) or {}
    child_pid = job.get("childPid") or job.get("supervisorPid") or lease.get("childJobPid")
    log_path = job.get("logPath") or lease.get("logPath")
    old_log_time = int(lease.get("logMtime") or 0)
    old_cpu = float(lease.get("childCpuSeconds") or 0.0)
    log_time = log_mtime_ms(log_path)
    cpu_time = process_tree_cpu_seconds(child_pid) if child_pid and pid_alive(child_pid) else 0.0
    lease["childJobPid"] = child_pid
    lease["childCpuSeconds"] = cpu_time
    lease["logPath"] = log_path
    lease["logMtime"] = log_time
    if child_pid and pid_alive(child_pid):
        progressed = log_time > old_log_time or cpu_time > old_cpu + 0.01
        if progressed:
            lease["lastEvidenceAt"] = now
            lease["lastError"] = None
            return "running"
        evidence = max(int(lease.get("lastHeartbeatAt") or 0),
                       int(lease.get("lastEvidenceAt") or 0), int(event_at or 0), int(log_time or 0))
        age = max(0, now - evidence) / 1000
        if age <= HEALTHY_SECONDS:
            return "running"
        if age <= STALLED_SECONDS:
            return "suspect"
        lease["lastError"] = "observable child alive but CPU/log evidence did not advance"
        return "stalled"
    if job.get("exitCode") is not None and not job.get("reportedAt"):
        lease["lastError"] = f"observable job exited {job.get('exitCode')} without terminal handoff"
        return "error" if int(job.get("exitCode")) != 0 else "suspect"
    evidence = max(
        int(lease.get("lastHeartbeatAt") or 0),
        int(lease.get("lastEvidenceAt") or 0),
        int(event_at or 0),
        int(log_time or 0),
    )
    age = max(0, now - evidence) / 1000
    if age <= HEALTHY_SECONDS:
        return "running"
    if age <= STALLED_SECONDS:
        return "suspect"
    return "stalled"


def refuse_role_drift_create(session_id: str, value: dict[str, Any]) -> None:
    """Role fidelity: a child lane is owned by exactly one live coordinator and one
    unique worktree. A worker/auditor parenting its own sub-lanes, or two live lanes
    sharing a cwd, is refused at creation instead of detected later."""
    parent = value.get("parentSessionId")
    if parent == session_id:
        raise SystemExit(f"refusing self-parented lease: {session_id}")
    if parent:
        parent_manifest = read_manifest(str(parent))
        # A missing parent manifest stays permitted: the deterministic watchdog
        # backfills leases after a coordinator crash and must not fail closed.
        if parent_manifest and (parent_manifest.get("isArchived")
                                or role_of(parent_manifest) != "coordinator"):
            raise SystemExit(
                f"refusing lease: parent {parent} is not a live coordinator "
                f"(role={role_of(parent_manifest)})")
    worktree = value.get("worktree")
    if not worktree:
        return
    for sid, other in all_manifests().items():
        if sid == session_id or other.get("isArchived") or role_of(other) not in ROLES:
            continue
        if expand(other.get("workingDirectory") or other.get("sdkCwd")) == worktree:
            raise SystemExit(f"refusing lease: worktree already owned by live session {sid}: {worktree}")
    for path in LEASES.glob("*.json"):
        if path.stem == session_id:
            continue
        other_lease = read_json(path)
        if (other_lease and other_lease.get("worktree") == worktree
                and other_lease.get("state") not in TERMINAL_STATES):
            other_manifest = read_manifest(path.stem)
            if other_manifest and not other_manifest.get("isArchived"):
                raise SystemExit(f"refusing lease: worktree already leased by live session {path.stem}: {worktree}")


def validate_existing_admission(session_id: str) -> None:
    manifest = read_manifest(session_id) or {}
    if not ({"protocol-version::3.4.35", "protocol-version::3.4.36", "protocol-version::3.4.37", "protocol-version::3.4.38", "protocol-version::3.4.39", "protocol-version::3.4.40"} & set(manifest.get("labels") or [])): return
    matches = [read_json(p) for p in (RUNTIME / "lane-admissions").glob("*.json")]
    matches = [x for x in matches if x and x.get("state") == "admitted" and x.get("sessionId") == session_id]
    if len(matches) != 1: raise SystemExit("v3.4.35–v3.4.40 lane has no unique admitted record")
    spec = importlib.util.spec_from_file_location("lane_admission", Path(__file__).with_name("lane-admission.py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)  # type: ignore
    module.cmd_confirm(argparse.Namespace(token=matches[0]["token"], session=session_id))


def require_admission(token: str | None, session_id: str, args: argparse.Namespace) -> None:
    if not token:
        raise SystemExit("explicit lease create requires confirmed admission token")
    # Reuse the exact admission validator at every mutable boundary rather than
    # trusting a confirmation that may predate manifest/registry drift.
    spec = importlib.util.spec_from_file_location("lane_admission", Path(__file__).with_name("lane-admission.py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)  # type: ignore
    module.cmd_confirm(argparse.Namespace(token=token, session=session_id))
    row = read_json(RUNTIME / "lane-admissions" / f"{token}.json") or {}
    identity = row.get("identity") or {}
    for key, arg in (("parentSessionId", "parent"), ("workUnit", "work_unit"), ("attempt", "attempt"), ("worktree", "worktree")):
        supplied = getattr(args, arg, None)
        if supplied is not None:
            actual = expand(supplied) if key == "worktree" else str(supplied)
            if actual != str(identity.get(key) or ""):
                raise SystemExit(f"lease override differs from immutable admission {key}")
    if args.state != "starting":
        raise SystemExit("lease create state must be starting; use heartbeat after admission")


def cmd_create(args: argparse.Namespace) -> int:
    with locked():
        manifest = read_manifest(args.session)
        if not manifest:
            raise SystemExit(f"session manifest not found: {args.session}")
        if manifest.get("isArchived"):
            raise SystemExit(f"refusing lease for archived session: {args.session}")
        if role_of(manifest) not in ROLES:
            raise SystemExit(f"refusing lease for role={role_of(manifest)}")
        value = read_json(lease_path(args.session)) or lease_from_manifest(manifest)
        # Structural role/cwd refusals remain more specific than token refusal.
        refuse_role_drift_create(args.session, value)
        require_admission(args.admission_token, args.session, args)
        for key, arg in (
            ("parentSessionId", "parent"), ("workUnit", "work_unit"),
            ("attempt", "attempt"), ("phase", "phase"), ("worktree", "worktree"),
        ):
            raw = getattr(args, arg, None)
            if raw is not None:
                value[key] = expand(raw) if key == "worktree" else raw
        value["state"] = args.state
        value["lastHeartbeatAt"] = now_ms()
        save_lease(value)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    with locked():
        manifest = read_manifest(args.session)
        if not manifest or manifest.get("isArchived"):
            removed = remove_runtime(args.session)
            print(json.dumps({"sessionId": args.session, "state": "removed", "removed": removed}, indent=2))
            return 0
        if role_of(manifest) not in ROLES:
            removed = []
            try:
                lease_path(args.session).unlink()
                removed.append(str(lease_path(args.session)))
            except FileNotFoundError:
                pass
            print(json.dumps({"sessionId": args.session, "state": "ignored",
                              "reason": f"role={role_of(manifest)}", "removed": removed}, indent=2))
            return 0
        try:
            validate_existing_admission(args.session)
        except SystemExit as exc:
            reason = str(exc)
            quarantine_admission_failure(args.session, reason, manifest)
            print(json.dumps({"sessionId": args.session, "state": "admission-refused", "reason": reason}, indent=2)); return 4
        value = read_json(lease_path(args.session)) or lease_from_manifest(manifest)
        value["state"] = args.state or "running"
        if value["state"] in ("starting", "running", "suspect"):
            value["lastError"] = None
        if args.phase is not None:
            value["phase"] = args.phase
        if args.evidence is not None:
            value["evidence"] = args.evidence
            value["lastEvidenceAt"] = now_ms()
        if args.child_pid is not None:
            value["childJobPid"] = int(args.child_pid)
        if args.log is not None:
            value["logPath"] = expand(args.log)
            value["logMtime"] = log_mtime_ms(value["logPath"])
        value["lastHeartbeatAt"] = now_ms()
        save_lease(value)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    with locked():
        manifest = read_manifest(args.session)
        if not manifest or manifest.get("isArchived"):
            removed = remove_runtime(args.session)
            print(json.dumps({"sessionId": args.session, "state": "removed", "removed": removed}, indent=2))
            return 0
        if role_of(manifest) not in ROLES:
            with contextlib.suppress(FileNotFoundError):
                lease_path(args.session).unlink()
            print(json.dumps({"sessionId": args.session, "state": "ignored",
                              "reason": f"role={role_of(manifest)}"}, indent=2))
            return 0
        try:
            validate_existing_admission(args.session)
        except SystemExit as exc:
            reason = str(exc)
            quarantine_admission_failure(args.session, reason, manifest)
            print(json.dumps({"sessionId": args.session, "state": "admission-refused", "reason": reason}, indent=2)); return 4
        value = read_json(lease_path(args.session)) or lease_from_manifest(manifest)
        value["state"] = "handoff-ready"
        value["phase"] = "terminal-handoff"
        value["preservationState"] = args.preservation
        value["lastHeartbeatAt"] = now_ms()
        value["lastEvidenceAt"] = now_ms()
        save_lease(value)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    with locked():
        removed = remove_runtime(args.session)
        print(json.dumps({"sessionId": args.session, "removed": removed}, ensure_ascii=False, indent=2))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    with locked():
        # Classify an entire locked reconciliation against one instant.  Sampling
        # once prevents lanes either side of a one-second wall-clock rollover from
        # receiving contradictory healthy/suspect/stalled results.
        now = now_ms()
        manifests = all_manifests()
        active_by_cwd: dict[str, list[str]] = {}
        for candidate_id, candidate in manifests.items():
            if candidate.get("isArchived") or role_of(candidate) not in ROLES:
                continue
            candidate_cwd = expand(candidate.get("workingDirectory") or candidate.get("sdkCwd"))
            if candidate_cwd:
                active_by_cwd.setdefault(candidate_cwd, []).append(candidate_id)
        actions: list[dict[str, Any]] = []
        leases = {p.stem: read_json(p) for p in LEASES.glob("*.json")}
        for sid, lease in leases.items():
            manifest = manifests.get(sid)
            if lease is None:
                actions.append({"action": "remove-invalid", "sessionId": sid})
                if args.apply:
                    remove_runtime(sid)
                continue
            if not manifest or manifest.get("isArchived"):
                reason = "absent" if not manifest else "archived"
                actions.append({"action": "remove", "sessionId": sid, "reason": reason})
                if args.apply:
                    remove_runtime(sid)
                continue
            if role_of(manifest) not in ROLES:
                actions.append({"action": "remove-lease", "sessionId": sid, "reason": "role-changed"})
                if args.apply:
                    with contextlib.suppress(FileNotFoundError):
                        lease_path(sid).unlink()
                continue
            old = lease.get("state")
            try:
                validate_existing_admission(sid)
            except SystemExit as exc:
                reason = str(exc)
                actions.append({"action": "admission-refused", "sessionId": sid, "reason": reason})
                if args.apply: quarantine_admission_failure(sid, reason, manifest)
                continue
            new = classify(lease, manifest, now)
            lease["state"] = new
            lease["parentSessionId"] = (registry_adopted_parent(sid) or label_value(manifest, "parent-session::")
                                        or manifest.get("parentSessionId"))
            lease["workUnit"] = label_value(manifest, "work-unit::")
            lease["attempt"] = label_value(manifest, "attempt::")
            lease["worktree"] = expand(manifest.get("workingDirectory") or manifest.get("sdkCwd"))
            sharing = active_by_cwd.get(lease["worktree"], []) if lease.get("worktree") else []
            lease["cwdCollisionSessions"] = sharing if len(sharing) > 1 else []
            if new != old:
                actions.append({"action": "classify", "sessionId": sid, "from": old, "to": new})
            if args.apply:
                save_lease(lease)
        for sid, manifest in manifests.items():
            if manifest.get("isArchived") or role_of(manifest) not in ROLES or sid in leases:
                continue
            try:
                validate_existing_admission(sid)
            except SystemExit as exc:
                reason = str(exc)
                actions.append({"action": "admission-refused", "sessionId": sid, "reason": reason})
                if args.apply: quarantine_admission_failure(sid, reason, manifest)
                continue
            value = lease_from_manifest(manifest)
            value["state"] = classify(value, manifest, now)
            sharing = active_by_cwd.get(value.get("worktree"), []) if value.get("worktree") else []
            value["cwdCollisionSessions"] = sharing if len(sharing) > 1 else []
            actions.append({"action": "create", "sessionId": sid, "state": value["state"], "reason": "missing-live-lease"})
            if args.apply:
                save_lease(value)
        summary: dict[str, int] = {}
        if args.apply:
            for path in LEASES.glob("*.json"):
                lease = read_json(path) or {}
                state = str(lease.get("state") or "invalid")
                summary[state] = summary.get(state, 0) + 1
        collision_groups = {cwd: ids for cwd, ids in active_by_cwd.items() if len(ids) > 1}
        print(json.dumps({"applied": bool(args.apply), "actions": actions, "summary": summary,
                          "cwdCollisions": collision_groups}, ensure_ascii=False, indent=2))
    return 0


def lifecycle_debt(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify all known lifecycle debt without treating one zero as cleanliness."""
    now = now_ms()
    manifests = all_manifests()
    records = [value for path in sorted(COORDINATORS.glob("*.json"))
               if (value := read_json(path)) and value.get("state") in
               {"authoritative", "rotating", "hold", "needs-owner"}]
    current_coordinators = {str(value.get("coordinatorSessionId") or "") for value in records}
    current_coordinators.update(str(value.get("successorSessionId") or "") for value in records
                                if value.get("state") == "rotating")
    classes: dict[str, list[dict[str, Any]]] = {
        "preservation-proven-archivable": [],
        "terminal-unknown": [],
        "orphaned-dead": [],
        "stale-coordinator": [],
        "archived-active-child": [],
        "stale-clean-worktree": [],
    }
    leases_by_id = {str(value.get("sessionId") or ""): value for value in rows}
    for value in rows:
        sid = str(value.get("sessionId") or "")
        manifest = manifests.get(sid) or {}
        if manifest.get("isArchived"):
            continue
        state = str(value.get("state") or "")
        preservation = value.get("preservationState")
        evidence = {"sessionId": sid, "state": state, "preservationState": preservation,
                    "worktree": value.get("worktree"), "parentSessionId": value.get("parentSessionId")}
        if state == "handoff-ready" and preservation in {"pushed", "merged"}:
            classes["preservation-proven-archivable"].append(evidence)
        elif state == "handoff-ready" or manifest.get("sessionStatus") in TERMINAL_SESSION_STATUSES:
            classes["terminal-unknown"].append(evidence)
        created = value.get("createdAt") if isinstance(value.get("createdAt"), int) else value.get("lastHeartbeatAt")
        parent = str(value.get("parentSessionId") or "")
        if (state in {"stalled", "error"} and parent and parent not in current_coordinators
                and isinstance(created, int) and not isinstance(created, bool)
                and created > 0 and now - created > ORPHANED_LANE_SECONDS * 1000):
            classes["orphaned-dead"].append({**evidence, "ageMs": now - created})
    # A terminal manifest with a missing lease is still unknown debt, not proof of
    # a clean fleet. Reconcile can rebuild its lease, but report must not false-zero.
    terminal_ids = {row["sessionId"] for row in classes["terminal-unknown"]}
    terminal_ids.update(row["sessionId"] for row in classes["preservation-proven-archivable"])
    for sid, manifest in sorted(manifests.items()):
        if (sid not in terminal_ids and sid not in leases_by_id and not manifest.get("isArchived")
                and role_of(manifest) in ROLES and manifest.get("sessionStatus") in TERMINAL_SESSION_STATUSES):
            classes["terminal-unknown"].append({"sessionId": sid, "state": "missing-lease",
                                                "preservationState": "unknown",
                                                "worktree": expand(manifest.get("workingDirectory") or manifest.get("sdkCwd"))})
        if (not manifest.get("isArchived") and role_of(manifest) == "coordinator"
                and sid not in current_coordinators):
            classes["stale-coordinator"].append({"sessionId": sid,
                                                 "sessionStatus": manifest.get("sessionStatus"),
                                                 "worktree": expand(manifest.get("workingDirectory") or manifest.get("sdkCwd"))})
    for record in records:
        for sid in sorted({str(value) for value in (record.get("activeChildren") or []) if value}):
            manifest = manifests.get(sid)
            if not manifest or manifest.get("isArchived"):
                classes["archived-active-child"].append({"sessionId": sid,
                    "project": record.get("project"), "manifestState": "absent" if not manifest else "archived",
                    "worktree": expand((manifest or {}).get("workingDirectory") or (manifest or {}).get("sdkCwd"))})
    gc_report = read_json(Path(os.environ.get("CRAFT_WORKTREE_GC_REPORT", RUNTIME / "worktree-gc/latest.json")).expanduser())
    gc_observed = bool(gc_report and gc_report.get("schemaVersion") == 1
                       and gc_report.get("protocolVersion") == "3.4.40"
                       and gc_report.get("mode") in {"dry-run", "apply"}
                       and not gc_report.get("applyRefusal")
                       and gc_report.get("observationComplete"))
    if gc_report:
        for value in gc_report.get("worktrees") or []:
            if (isinstance(value, dict) and value.get("classification") == "stale-clean-worktree"
                    and value.get("state") in {"candidate", "deferred"}):
                classes["stale-clean-worktree"].append({key: value.get(key) for key in
                    ("repository", "worktree", "head", "branch", "ownership", "ageMs", "state")})
    for values in classes.values():
        values.sort(key=lambda value: (str(value.get("project") or ""),
                                       str(value.get("sessionId") or value.get("worktree") or "")))
    counts = {name: len(values) for name, values in classes.items()}
    total = sum(counts.values())
    unknown = [] if gc_observed else ["stale-clean-worktree:not-observed"]
    return {"schemaVersion": 1, "clean": total == 0 and gc_observed,
            "observationComplete": gc_observed, "unknown": unknown,
            "total": total, "summary": counts, "classes": classes,
            "worktreeGcReport": str(Path(os.environ.get("CRAFT_WORKTREE_GC_REPORT",
                                                         RUNTIME / "worktree-gc/latest.json")).expanduser())}


def cmd_report(_: argparse.Namespace) -> int:
    ensure_dirs()
    rows = []
    summary: dict[str, int] = {}
    for path in sorted(LEASES.glob("*.json")):
        value = read_json(path) or {"sessionId": path.stem, "state": "invalid"}
        state = str(value.get("state") or "invalid")
        summary[state] = summary.get(state, 0) + 1
        rows.append(value)
    collisions: dict[str, list[str]] = {}
    for value in rows:
        sharing = value.get("cwdCollisionSessions") or []
        if sharing and value.get("worktree"):
            collisions[str(value["worktree"])] = sharing
    debt = lifecycle_debt(rows)
    # Compatibility: archivableBacklog remains the preservation-proven class,
    # but callers must use lifecycleDebt.clean for an exhaustive cleanliness claim.
    archivable = debt["summary"]["preservation-proven-archivable"]
    print(json.dumps({"summary": summary, "archivableBacklog": archivable,
                      "lifecycleClean": debt["clean"], "lifecycleDebt": debt,
                      "cwdCollisions": collisions, "leases": rows}, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create")
    c.add_argument("--session", required=True)
    c.add_argument("--parent")
    c.add_argument("--work-unit")
    c.add_argument("--attempt")
    c.add_argument("--worktree")
    c.add_argument("--phase", default="spawned")
    c.add_argument("--state", default="starting", choices=["starting", "running"])
    c.add_argument("--admission-token", help="confirmed v3.4.35–v3.4.40 lane-admission token")
    c.set_defaults(func=cmd_create)
    h = sub.add_parser("heartbeat")
    h.add_argument("--session", required=True)
    h.add_argument("--state", choices=["starting", "running", "suspect", "error"])
    h.add_argument("--phase")
    h.add_argument("--evidence")
    h.add_argument("--child-pid", type=int)
    h.add_argument("--log")
    h.set_defaults(func=cmd_heartbeat)
    f = sub.add_parser("finish")
    f.add_argument("--session", required=True)
    f.add_argument("--preservation", default="pushed", choices=["pushed", "merged"])
    f.set_defaults(func=cmd_finish)
    r = sub.add_parser("remove")
    r.add_argument("--session", required=True)
    r.set_defaults(func=cmd_remove)
    q = sub.add_parser("reconcile")
    q.add_argument("--apply", action="store_true")
    q.set_defaults(func=cmd_reconcile)
    z = sub.add_parser("report")
    z.set_defaults(func=cmd_report)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))

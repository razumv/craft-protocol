#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Register and reconcile observable external waits for autonomous coordinators.

A coordinator may not end a turn with a prose-only "waiting for CI/deploy" state.
The wait must bind to an active child lease and durable observable-job receipt.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any

HOME = Path.home()
RUNTIME = Path(os.environ.get("CRAFT_RUNTIME", HOME / ".craft-agent/runtime")).expanduser()
SESSIONS = Path(os.environ.get("CRAFT_SESSIONS", HOME / ".craft-agent/workspaces/general/sessions")).expanduser()
WAITS = RUNTIME / "external-waits"
LOCK = RUNTIME / "external-waits.lock"
COORDINATORS = RUNTIME / "coordinators"
LEASES = RUNTIME / "worker-leases"
JOBS = RUNTIME / "worker-jobs"
NOW_MS = lambda: int(os.environ.get("CRAFT_TEST_NOW_MS", "0")) or int(time.time() * 1000)
KINDS = {"github-actions", "auto-merge", "deployment", "external-check"}
ACTIVE_LEASE_STATES = {"active", "running", "starting", "suspect"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def fail(message: str) -> None:
    raise SystemExit(message)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n"); fh.flush(); os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


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


def valid_id(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value):
        fail(f"invalid {label}")
    return value


def valid_text(value: str, label: str, limit: int = 500) -> str:
    if not value or len(value) > limit or any(ord(ch) < 32 for ch in value):
        fail(f"invalid {label}")
    lowered = value.lower()
    if any(marker in lowered for marker in ("authorization:", "bearer ", "token=", "api_key=", "apikey=")):
        fail(f"{label} may not contain credentials")
    return value


def command_hash(job: dict[str, Any]) -> str:
    command = job.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        fail("watcher job command is missing or invalid")
    return hashlib.sha256(json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def command_hash_or_none(job: dict[str, Any]) -> str | None:
    try:
        return command_hash(job)
    except SystemExit:
        return None


def wait_path(wait_id: str) -> Path:
    return WAITS / f"{valid_id(wait_id, 'wait id')}.json"


def coordinator_record(project: str, coordinator: str) -> dict[str, Any]:
    row = read_json(COORDINATORS / f"{valid_id(project, 'project')}.json")
    if not row or row.get("coordinatorSessionId") != coordinator:
        fail("coordinator registry identity mismatch")
    if row.get("state") != "authoritative":
        fail("coordinator is not authoritative")
    man = manifest(coordinator)
    if not man or man.get("isArchived") or man.get("sessionStatus") in {"done", "cancelled", "error"}:
        fail("coordinator session is not live")
    return row


def observer_proof(watcher: str, coordinator: str) -> tuple[dict[str, Any], dict[str, Any]]:
    lease = read_json(LEASES / f"{watcher}.json")
    job = read_json(JOBS / f"{watcher}.json")
    man = manifest(watcher)
    if not man or man.get("isArchived"):
        fail("watcher session is not live")
    if label_value(man, "agent-role::") not in {"worker", "auditor"}:
        fail("watcher must have worker/auditor role")
    if not lease or lease.get("sessionId") != watcher or lease.get("parentSessionId") != coordinator:
        fail("watcher lease is missing or parent mismatch")
    if lease.get("state") not in ACTIVE_LEASE_STATES:
        fail("watcher lease is not active")
    if not job or job.get("sessionId") != watcher or job.get("exitCode") is not None or job.get("reportedAt") is not None:
        fail("watcher observable job is not active")
    command_hash(job)
    if not process_identity(job):
        fail("watcher observable process identity is unavailable")
    return lease, job


def pid_alive(value: Any) -> bool:
    try:
        os.kill(int(value), 0)
        return True
    except Exception:
        return False


def process_identity(job: dict[str, Any]) -> dict[str, Any] | None:
    # The supervisor writes the terminal receipt before it exits, making it the
    # stable identity for an observing job. Fall back to child only for legacy receipts.
    pid = job.get("supervisorPid") if pid_alive(job.get("supervisorPid")) else job.get("childPid")
    if not pid_alive(pid):
        return None
    try:
        cp = subprocess.run(["ps", "-p", str(pid), "-o", "ppid=,lstart=,command="],
                            capture_output=True, text=True, timeout=5)
        parts = cp.stdout.strip().split(None, 6)
        if cp.returncode or len(parts) < 7:
            return None
        return {"pid": int(pid), "ppid": int(parts[0]), "startToken": " ".join(parts[1:6]),
                "processCommandSha256": hashlib.sha256(parts[6].encode()).hexdigest()}
    except Exception:
        return None


@contextlib.contextmanager
def exclusive_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cmd_register(args: argparse.Namespace) -> int:
    now = NOW_MS()
    wait_id = valid_id(args.wait_id, "wait id")
    coordinator = valid_id(args.coordinator, "coordinator")
    watcher = valid_id(args.watcher_session, "watcher session")
    project = valid_id(args.project, "project")
    work_unit = valid_id(args.work_unit, "work unit")
    if args.kind not in KINDS:
        fail("unsupported wait kind")
    subject = valid_text(args.subject, "subject")
    if args.timeout < 60 or args.timeout > 604800:
        fail("timeout must be between 60 and 604800 seconds")
    path = wait_path(wait_id)
    with exclusive_lock():
        existing = read_json(path)
        if existing and existing.get("state") != "cleared":
            fail("active wait id already exists")
        coordinator_record(project, coordinator)
        _, job = observer_proof(watcher, coordinator)
        identity = process_identity(job)
        if not identity:
            fail("watcher observable process identity is unavailable")
        row = {
            "schemaVersion": 1, "waitId": wait_id, "project": project,
            "coordinatorSessionId": coordinator, "workUnit": work_unit,
            "kind": args.kind, "subject": subject, "watcherSessionId": watcher,
            "jobId": job.get("jobId") or watcher, "observerCommandSha256": command_hash(job),
            "observerProcessIdentity": identity, "state": "observing",
            "registeredAt": now, "deadlineAt": now + args.timeout * 1000,
            "lastCheckedAt": now,
        }
        if args.apply:
            atomic_json(path, row)
    print(json.dumps({"applied": args.apply, "wait": row}, ensure_ascii=False, indent=2))
    return 0


def reconcile_row(row: dict[str, Any], now: int) -> dict[str, Any]:
    if row.get("state") in {"cleared", "clearing"}:
        return row
    watcher = str(row.get("watcherSessionId") or "")
    coordinator = str(row.get("coordinatorSessionId") or "")
    lease = read_json(LEASES / f"{watcher}.json")
    job = read_json(JOBS / f"{watcher}.json")
    out = dict(row); out["lastCheckedAt"] = now
    if not lease or lease.get("parentSessionId") != coordinator:
        out.update(state="unobserved", reason="watcher-lease-missing-or-mismatched", detectedAt=now)
    elif not job or job.get("sessionId") != watcher:
        out.update(state="unobserved", reason="watcher-job-receipt-missing", detectedAt=now)
    elif command_hash_or_none(job) != row.get("observerCommandSha256"):
        out.update(state="unobserved", reason="watcher-command-binding-changed", detectedAt=now)
    elif job.get("exitCode") is not None:
        out.update(state="terminal", terminalAt=job.get("finishedAt") or now,
                   terminalExitCode=job.get("exitCode"), reason="watcher-job-terminal")
    elif now >= int(row.get("deadlineAt") or 0):
        out.update(state="deadline", detectedAt=now, reason="external-wait-deadline")
    elif process_identity(job) != row.get("observerProcessIdentity"):
        out.update(state="unobserved", detectedAt=now, reason="watcher-process-identity-changed")
    else:
        out.update(state="observing", reason=None)
    return out


def finish_clear(path: Path, row: dict[str, Any], now: int, apply: bool) -> dict[str, Any]:
    job_path = JOBS / f"{row.get('watcherSessionId')}.json"
    job = read_json(job_path)
    if (not job or job.get("sessionId") != row.get("watcherSessionId") or
            job.get("exitCode") is None or command_hash_or_none(job) != row.get("observerCommandSha256")):
        return {**row, "state": "unobserved", "detectedAt": now,
                "reason": "clear-transaction-terminal-receipt-missing", "clearTransactionPending": True}
    job.update(reportedAt=job.get("reportedAt") or now, updatedAt=now)
    cleared = {**row, "state": "cleared", "clearedAt": now, "lastCheckedAt": now,
               "clearTransactionPending": False}
    if apply:
        atomic_json(job_path, job)
        atomic_json(path, cleared)
    return cleared


def cmd_reconcile(args: argparse.Namespace) -> int:
    now = NOW_MS(); rows = []; changed = []
    with exclusive_lock():
        for path in sorted(WAITS.glob("*.json")):
            row = read_json(path)
            if not row:
                continue
            updated = finish_clear(path, row, now, args.apply) if row.get("state") == "clearing" else reconcile_row(row, now)
            if updated != row:
                changed.append(str(updated.get("waitId") or path.stem))
                if args.apply:
                    atomic_json(path, updated)
            rows.append(updated)
    print(json.dumps({"applied": args.apply, "changed": changed, "waits": rows,
                      "summary": {state: sum(r.get("state") == state for r in rows)
                                  for state in ("observing", "terminal", "unobserved", "deadline", "cleared")}},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    path = wait_path(args.wait_id)
    coordinator = valid_id(args.coordinator, "coordinator")
    evidence = valid_text(args.evidence, "evidence")
    now = NOW_MS()
    with exclusive_lock():
        row = read_json(path)
        if not row:
            fail("wait not found")
        if row.get("coordinatorSessionId") != coordinator:
            fail("wait coordinator mismatch")
        coordinator_record(str(row.get("project") or ""), coordinator)
        if row.get("state") != "terminal":
            fail("wait may be cleared only after a terminal observer receipt")
        job_path = JOBS / f"{row.get('watcherSessionId')}.json"
        job = read_json(job_path)
        if not job or job.get("exitCode") is None:
            fail("terminal observer receipt is missing")
        clearing = {**row, "state": "clearing", "clearingAt": now, "clearEvidence": evidence,
                    "clearTransactionPending": True, "lastCheckedAt": now}
        if args.apply:
            # Journal intent first. A crash after this point is completed by
            # reconcile under the same lock on the next watchdog cycle.
            atomic_json(path, clearing)
            if os.environ.get("CRAFT_TEST_CRASH_AFTER_CLEAR_JOURNAL") == "1":
                raise SystemExit(75)
        row = finish_clear(path, clearing, now, args.apply)
        if args.apply and row.get("state") != "cleared":
            atomic_json(path, row)
    print(json.dumps({"applied": args.apply, "wait": row}, ensure_ascii=False, indent=2))
    return 0 if row.get("state") == "cleared" else 2


def cmd_list(_args: argparse.Namespace) -> int:
    with exclusive_lock():
        rows = [row for path in sorted(WAITS.glob("*.json")) if (row := read_json(path))]
    print(json.dumps({"count": len(rows), "waits": rows}, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="action", required=True)
    r = sub.add_parser("register")
    for name in ("wait-id", "project", "coordinator", "work-unit", "kind", "subject", "watcher-session"):
        r.add_argument(f"--{name}", required=True)
    r.add_argument("--timeout", type=int, default=7200); r.add_argument("--apply", action="store_true"); r.set_defaults(func=cmd_register)
    q = sub.add_parser("reconcile"); q.add_argument("--apply", action="store_true"); q.set_defaults(func=cmd_reconcile)
    c = sub.add_parser("clear"); c.add_argument("--wait-id", required=True); c.add_argument("--coordinator", required=True); c.add_argument("--evidence", required=True); c.add_argument("--apply", action="store_true"); c.set_defaults(func=cmd_clear)
    l = sub.add_parser("list"); l.set_defaults(func=cmd_list)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))

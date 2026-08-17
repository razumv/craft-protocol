#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Launch and observe long-running worker commands through durable job receipts."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

HOME = Path.home()
RUNTIME = Path(os.environ.get("CRAFT_RUNTIME", HOME / ".craft-agent/runtime")).expanduser()
JOBS = RUNTIME / "worker-jobs"
HEAVY_LOCK = RUNTIME / "heavy-job.lock"
HEAVY_OWNER = RUNTIME / "heavy-job-owner.json"
_spec = importlib.util.spec_from_file_location("orch_common", Path(__file__).with_name("orchestration-common.py"))
COMMON = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(COMMON)  # type: ignore
LEASE_TOOL = Path(__file__).with_name("worker-lease.py")
SESSIONS = Path(os.environ.get("CRAFT_SESSIONS", Path(os.environ.get("CRAFT_WORKSPACE", HOME / ".craft-agent/workspaces/general")) / "sessions")).expanduser()


def quarantine_admission_failure(session_id: str, reason: str) -> None:
    """Use the lease registry's shared atomic admission-failure quarantine."""
    spec = importlib.util.spec_from_file_location("worker_lease", LEASE_TOOL)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)  # type: ignore
    with module.locked():
        module.quarantine_admission_failure(session_id, reason)


def require_admitted_v3435(session_id: str, cwd: str) -> None:
    try:
        manifest = json.loads((SESSIONS / session_id / "session.jsonl").open(encoding="utf-8").readline())
    except Exception:
        raise SystemExit("session manifest required for observable job")
    if not ({"protocol-version::3.4.35", "protocol-version::3.4.36", "protocol-version::3.4.37", "protocol-version::3.4.38", "protocol-version::3.4.39"} & set(manifest.get("labels") or [])):
        return  # Existing v3.4.34 observable jobs remain readable/runnable.
    matches = []
    for record_path in (RUNTIME / "lane-admissions").glob("*.json"):
        record = read_json_file(record_path) or {}
        if record.get("state") == "admitted" and record.get("sessionId") == session_id:
            matches.append(record)
    if len(matches) != 1 or (matches[0].get("identity") or {}).get("worktree") != COMMON.canonical_path(cwd):
        raise SystemExit("observable job requires matching admitted lane")
    identity = matches[0].get("identity") or {}
    project = str(identity.get("project") or "")
    registry = read_json_file(RUNTIME / "coordinators" / f"{project}.json") if project else None
    if not registry or registry.get("state") != "authoritative" or registry.get("coordinatorSessionId") != identity.get("parentSessionId"):
        raise SystemExit("project HOLD or non-authoritative coordinator blocks new implementation jobs")
    # Re-run shared confirmation so post-confirm manifest/registry drift refuses.
    spec = importlib.util.spec_from_file_location("lane_admission", Path(__file__).with_name("lane-admission.py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)  # type: ignore
    module.cmd_confirm(argparse.Namespace(token=matches[0]["token"], session=session_id))


def now_ms() -> int:
    return int(time.time() * 1000)


def path_for(session_id: str) -> Path:
    return JOBS / f"{session_id}.json"


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


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def read_job(session_id: str) -> dict[str, Any] | None:
    return read_json_file(path_for(session_id))


def heartbeat(session_id: str, phase: str, evidence: str, pid: int | None, log: str) -> None:
    command = [str(LEASE_TOOL), "heartbeat", "--session", session_id, "--phase", phase,
               "--evidence", evidence, "--log", log]
    if pid is not None:
        command += ["--child-pid", str(pid)]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def supervise(args: argparse.Namespace) -> int:
    log_path = Path(args.log).expanduser().resolve()
    cwd = Path(args.cwd).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    heavy_handle = None
    if args.heavy:
        heavy_handle = HEAVY_LOCK.open("a+")
        try:
            fcntl.flock(heavy_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # This supervisor owns no heavyweight lock after a failed nonblocking
            # acquisition. Close its handle before making the terminal result
            # observable, just as successful finalization closes all owned state
            # before publishing its receipt.
            heavy_handle.close()
            atomic_json(path_for(args.session), {
                "schemaVersion": 1, "sessionId": args.session, "supervisorPid": os.getpid(),
                "childPid": None, "cwd": str(cwd), "logPath": str(log_path),
                "command": args.command, "startedAt": now_ms(), "updatedAt": now_ms(),
                "exitCode": 75, "finishedAt": now_ms(), "reportedAt": None,
                "launchError": "global heavyweight lane busy", "heavy": True,
            })
            return 75
        atomic_json(HEAVY_OWNER, {"sessionId": args.session, "supervisorPid": os.getpid(),
                                  "cwd": str(cwd), "logPath": str(log_path), "startedAt": now_ms()})
    receipt = {
        "schemaVersion": 1,
        "sessionId": args.session,
        "supervisorPid": os.getpid(),
        "childPid": None,
        "cwd": str(cwd),
        "logPath": str(log_path),
        "command": args.command,
        "startedAt": now_ms(),
        "updatedAt": now_ms(),
        "exitCode": None,
        "finishedAt": None,
        "reportedAt": None,
        "heavy": bool(args.heavy),
    }
    atomic_json(path_for(args.session), receipt)
    # A terminal receipt is the linearization point for consumers that may reap a
    # worktree or start another heavy job. Finish every owned operation first:
    # close the log, record the final heartbeat, and release/remove heavyweight
    # ownership. If finalization crashes, the nonterminal receipt and dead PID
    # deliberately remain recoverable as a failed observable job.
    try:
        with log_path.open("ab", buffering=0) as log:
            log.write((f"\n===== observable job start {time.strftime('%F %T')} =====\n").encode())
            try:
                child = subprocess.Popen(args.command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
                receipt["childPid"] = child.pid
                receipt["updatedAt"] = now_ms()
                atomic_json(path_for(args.session), receipt)
                heartbeat(args.session, "long-job", "observable child started", child.pid, str(log_path))
                rc = child.wait()
            except Exception as exc:
                log.write((f"observable job launch failure: {exc}\n").encode())
                rc = 127
                receipt["launchError"] = str(exc)
            log.write((f"===== observable job exit {rc} {time.strftime('%F %T')} =====\n").encode())
        heartbeat(args.session, "job-finished", f"observable job exited {rc}", None, str(log_path))
    finally:
        if heavy_handle is not None:
            owner = read_json_file(HEAVY_OWNER) or {}
            if owner.get("sessionId") == args.session:
                with contextlib.suppress(FileNotFoundError):
                    HEAVY_OWNER.unlink()
            fcntl.flock(heavy_handle.fileno(), fcntl.LOCK_UN)
            heavy_handle.close()
    receipt["exitCode"] = rc
    receipt["finishedAt"] = now_ms()
    receipt["updatedAt"] = now_ms()
    atomic_json(path_for(args.session), receipt)
    return rc


def cmd_start(args: argparse.Namespace) -> int:
    try:
        require_admitted_v3435(args.session, args.cwd)
    except SystemExit as exc:
        quarantine_admission_failure(args.session, str(exc))
        raise
    if not args.command:
        raise SystemExit("command required after --")
    if read_job(args.session) and not args.replace:
        existing = read_job(args.session) or {}
        # The child can exit before its supervisor completes its final owned
        # writes. Check both identities so a dead child never masks that live
        # supervisor and allows a duplicate launch into its cleanup window.
        for pid in (existing.get("childPid"), existing.get("supervisorPid")):
            if not pid:
                continue
            try:
                os.kill(int(pid), 0)
                raise SystemExit(f"live receipt already exists for {args.session}: pid {pid}")
            except ProcessLookupError:
                pass
            except PermissionError:
                raise SystemExit(f"cannot verify existing pid {pid}")
    supervisor_command = [sys.executable, str(Path(__file__).resolve()), "_supervise",
                          "--session", args.session, "--cwd", args.cwd, "--log", args.log]
    if args.heavy:
        supervisor_command.append("--heavy")
    supervisor_command += ["--", *args.command]
    proc = subprocess.Popen(supervisor_command, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    print(json.dumps({"sessionId": args.session, "supervisorPid": proc.pid,
                      "receipt": str(path_for(args.session)), "log": str(Path(args.log).expanduser())},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    value = read_job(args.session)
    if not value:
        print(json.dumps({"sessionId": args.session, "state": "absent"}, indent=2))
        return 1
    # The supervisor remains live during finalization after its child exits.
    # Expose either live identity rather than letting a dead child mask it.
    alive = False
    for pid in (value.get("childPid"), value.get("supervisorPid")):
        if not pid:
            continue
        try:
            os.kill(int(pid), 0)
            alive = True
            break
        except Exception:
            pass
    value["alive"] = alive
    try:
        value["logMtime"] = int(Path(value["logPath"]).stat().st_mtime * 1000)
        value["logSize"] = Path(value["logPath"]).stat().st_size
    except Exception:
        value["logMtime"] = 0
        value["logSize"] = 0
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    value = read_job(args.session)
    if not value:
        return 0
    value["reportedAt"] = now_ms()
    value["updatedAt"] = now_ms()
    atomic_json(path_for(args.session), value)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    s = sub.add_parser("start")
    s.add_argument("--session", required=True)
    s.add_argument("--cwd", required=True)
    s.add_argument("--log", required=True)
    s.add_argument("--replace", action="store_true")
    s.add_argument("--heavy", action="store_true", help="acquire the global heavyweight job lane")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_start)
    i = sub.add_parser("status")
    i.add_argument("--session", required=True)
    i.set_defaults(func=cmd_status)
    a = sub.add_parser("ack")
    a.add_argument("--session", required=True)
    a.set_defaults(func=cmd_ack)
    r = sub.add_parser("_supervise")
    r.add_argument("--session", required=True)
    r.add_argument("--cwd", required=True)
    r.add_argument("--log", required=True)
    r.add_argument("--heavy", action="store_true")
    r.add_argument("command", nargs=argparse.REMAINDER)
    r.set_defaults(func=supervise)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    if args.action in ("start", "_supervise") and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    raise SystemExit(args.func(args))

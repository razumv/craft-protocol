#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Register and safely reap short-lived recovery-controller harness processes.

A controller registers its exact harness PID/start identity at startup. A later
controller may reap it only after the owning session is archived and terminal.
The Craft app process, PID reuse, unknown commands, live sessions, and ambiguous
identity all fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Any

HOME = Path.home()
WORKSPACE = Path(os.environ.get("CRAFT_WORKSPACE", HOME / ".craft-agent/workspaces/general")).expanduser()
SESSIONS = Path(os.environ.get("CRAFT_SESSIONS", WORKSPACE / "sessions")).expanduser()
RUNTIME = Path(os.environ.get("CRAFT_RUNTIME", HOME / ".craft-agent/runtime")).expanduser()
RECEIPTS = Path(os.environ.get("CRAFT_CONTROLLER_HARNESSES", RUNTIME / "controller-harnesses")).expanduser()
PROCESS_TABLE = os.environ.get("CRAFT_PROCESS_TABLE")
NOW_MS = lambda: int(time.time() * 1000)


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.write("\n"); fh.flush(); os.fsync(fh.fileno())
        os.replace(raw, path)
    finally:
        try: os.unlink(raw)
        except FileNotFoundError: pass


def manifest(session_id: str) -> dict[str, Any] | None:
    path = SESSIONS / session_id / "session.jsonl"
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            return json.loads(fh.readline())
    except Exception:
        return None


def label_value(row: dict[str, Any], prefix: str) -> str | None:
    for label in row.get("labels") or []:
        if isinstance(label, str) and label.startswith(prefix):
            return label.split("::", 1)[1]
    return None


def process_table() -> dict[str, Any] | None:
    if not PROCESS_TABLE: return None
    try: return json.loads(Path(PROCESS_TABLE).read_text(encoding="utf-8"))
    except Exception: return {}


def process_probe(pid: int) -> tuple[str, dict[str, Any] | None]:
    """Return (alive|absent|unknown, identity). Only absent proves exit."""
    table = process_table()
    if table is not None:
        if os.environ.get("CRAFT_TEST_MODE") != "1": return "unknown", None
        row = table.get(str(pid))
        if not row: return "unknown", None
        if row.get("alive") is False: return "absent", None
        if row.get("lookupError"): return "unknown", None
        command = str(row.get("command", ""))
        return "alive", {"pid": pid, "ppid": int(row.get("ppid", 1)), "startToken": str(row.get("startToken", "")),
                         "command": command, "commandSha256": hashlib.sha256(command.encode()).hexdigest()}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "absent", None
    except (PermissionError, OSError):
        return "unknown", None
    try:
        cp = subprocess.run(["ps", "-p", str(pid), "-o", "ppid=,lstart=,command="], text=True,
                            capture_output=True, timeout=5)
    except Exception:
        return "unknown", None
    line = cp.stdout.strip()
    if cp.returncode or not line: return "unknown", None
    parts = line.split(None, 6)
    if len(parts) < 7: return "unknown", None
    try: ppid = int(parts[0])
    except ValueError: return "unknown", None
    command = parts[6]
    return "alive", {"pid": pid, "ppid": ppid, "startToken": " ".join(parts[1:6]),
                     "command": command, "commandSha256": hashlib.sha256(command.encode()).hexdigest()}


def identity(pid: int) -> dict[str, Any] | None:
    state, row = process_probe(pid)
    return row if state == "alive" else None


def is_harness(command: str) -> bool:
    if "/Contents/MacOS/Craft Agents" in command: return False
    return "pi-agent-server" in command or "claude-agent-sdk-binary/claude" in command


def nearest_harness(start_pid: int) -> dict[str, Any] | None:
    seen: set[int] = set(); pid = start_pid
    for _ in range(24):
        if pid <= 1 or pid in seen: break
        seen.add(pid); row = identity(pid)
        if not row: break
        if "/Contents/MacOS/Craft Agents" in row["command"]: break
        if is_harness(row["command"]): return row
        pid = int(row["ppid"])
    return None


def require_role(session_id: str, roles: set[str], archived: bool | None = None) -> tuple[dict[str, Any], str]:
    row = manifest(session_id)
    if not row: raise ValueError("session manifest missing")
    role = str(label_value(row, "agent-role::") or "")
    if role not in roles: raise ValueError(f"session role {role or 'missing'} is not permitted")
    if archived is not None and bool(row.get("isArchived")) != archived:
        raise ValueError("session archive state does not satisfy guard")
    return row, role


def require_controller(session_id: str, archived: bool | None = None) -> dict[str, Any]:
    return require_role(session_id, {"recovery-controller"}, archived)[0]


def register(args: argparse.Namespace) -> int:
    row, role = require_role(args.session, {"recovery-controller", "recovery-notifier"}, archived=False)
    proc = identity(args.pid) if args.pid else nearest_harness(os.getpid())
    if not proc or not is_harness(proc["command"]): raise ValueError("exact session harness not found; refusing registration")
    receipt = {"schemaVersion": 1, "sessionId": args.session, "sessionRole": role, "harnessPid": proc["pid"],
               "harnessStartToken": proc["startToken"], "harnessCommandSha256": proc["commandSha256"],
               "registeredAt": NOW_MS(), "sessionCreatedAt": row.get("createdAt"), "state": "registered"}
    atomic_write(RECEIPTS / f"{args.session}.json", receipt)
    print(json.dumps(receipt, indent=2)); return 0


def read_receipt(session_id: str) -> dict[str, Any]:
    path = RECEIPTS / f"{session_id}.json"
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: raise ValueError("controller harness receipt missing or invalid")


def verify_current_controller(session_id: str) -> None:
    require_controller(session_id, archived=False)
    receipt = read_receipt(session_id); state, proc = process_probe(int(receipt["harnessPid"]))
    if state != "alive" or not proc or not is_harness(proc["command"]):
        raise ValueError("current controller harness identity is not live/proven")
    if proc["startToken"] != receipt.get("harnessStartToken") or proc["commandSha256"] != receipt.get("harnessCommandSha256"):
        raise ValueError("current controller harness receipt identity mismatch")
    caller_pid = os.getpid()
    if os.environ.get("CRAFT_TEST_MODE") == "1" and os.environ.get("CRAFT_TEST_CALLER_PID"):
        caller_pid = int(os.environ["CRAFT_TEST_CALLER_PID"])
    caller = nearest_harness(caller_pid)
    if not caller:
        raise ValueError("calling process has no proven harness ancestor")
    if (caller["pid"] != int(receipt["harnessPid"]) or
        caller["startToken"] != receipt.get("harnessStartToken") or
        caller["commandSha256"] != receipt.get("harnessCommandSha256")):
        raise ValueError("current-session receipt does not belong to calling harness")


def reap(args: argparse.Namespace) -> int:
    verify_current_controller(args.current_session)
    if args.session == args.current_session: raise ValueError("controller cannot reap itself")
    row, role = require_role(args.session, {"recovery-controller", "recovery-notifier"}, archived=True)
    if row.get("sessionStatus") not in ("needs-review", "done", "cancelled"):
        raise ValueError("archived recovery session is not terminal")
    receipt = read_receipt(args.session); pid = int(receipt["harnessPid"]); probe_state, proc = process_probe(pid)
    result: dict[str, Any] = {"sessionId": args.session, "sessionRole": role, "pid": pid, "applied": bool(args.apply)}
    if probe_state == "unknown": raise ValueError("process identity lookup unknown; refusing receipt deletion")
    if probe_state == "absent":
        result["state"] = "already-exited"
        if args.apply: (RECEIPTS / f"{args.session}.json").unlink(missing_ok=True)
        print(json.dumps(result, indent=2)); return 0
    assert proc is not None
    if not is_harness(proc["command"]): raise ValueError("non-harness/app guard refusal")
    if proc["startToken"] != receipt.get("harnessStartToken") or proc["commandSha256"] != receipt.get("harnessCommandSha256"):
        raise ValueError("PID identity changed; possible PID reuse")
    result["state"] = "reapable"
    if args.apply:
        test_no_signal = os.environ.get("CRAFT_TEST_NO_SIGNAL") == "1"
        if not test_no_signal:
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + float(os.environ.get("CRAFT_CONTROLLER_REAP_WAIT_SECONDS", "5"))
        after = proc
        while time.monotonic() < deadline:
            if test_no_signal and os.environ.get("CRAFT_TEST_SIGNAL_STICKS") != "1":
                after = None
            else:
                after_state, after = process_probe(pid)
                if after_state == "unknown":
                    result["state"] = "identity-unknown-after-signal"
                    print(json.dumps(result, indent=2)); return 2
            if after is None: break
            if after["startToken"] != receipt.get("harnessStartToken") or after["commandSha256"] != receipt.get("harnessCommandSha256"):
                result["state"] = "identity-changed-after-signal"
                print(json.dumps(result, indent=2)); return 2
            time.sleep(0.1)
        if after is not None:
            result["state"] = "still-running-after-sigterm"
            print(json.dumps(result, indent=2)); return 2
        result["state"] = "reaped"
        (RECEIPTS / f"{args.session}.json").unlink(missing_ok=True)
    print(json.dumps(result, indent=2)); return 0


def report(_: argparse.Namespace) -> int:
    rows=[]; counts={"active":0,"activeControllers":0,"activeNotifiers":0,"terminalAwaitingReap":0,"alreadyExited":0,"lookupUnknown":0,"identityMismatch":0,"unknown":0}
    for path in sorted(RECEIPTS.glob("*.json")):
        try:
            rec=json.loads(path.read_text()); sid=str(rec["sessionId"]); man=manifest(sid)
            role=str(rec.get("sessionRole") or (label_value(man or {}, "agent-role::") if man else "") or "unknown")
            probe_state, proc=process_probe(int(rec["harnessPid"]))
            if not man: state="unknown"
            elif probe_state == "unknown": state="lookupUnknown"
            elif probe_state == "absent": state="alreadyExited"
            elif proc and (proc["startToken"] != rec.get("harnessStartToken") or proc["commandSha256"] != rec.get("harnessCommandSha256")): state="identityMismatch"
            elif man.get("isArchived") and man.get("sessionStatus") in ("needs-review","done","cancelled"): state="terminalAwaitingReap"
            elif not man.get("isArchived"): state="active"
            else: state="unknown"
            counts[state]+=1
            if state == "active" and role == "recovery-controller": counts["activeControllers"]+=1
            if state == "active" and role == "recovery-notifier": counts["activeNotifiers"]+=1
            rows.append({"sessionId":sid,"sessionRole":role,"pid":rec.get("harnessPid"),"state":state})
        except Exception:
            counts["unknown"]+=1; rows.append({"sessionId":path.stem,"state":"unknown"})
    violations=[]
    if counts["activeControllers"] > 1: violations.append("more than one registered active controller harness")
    if counts["activeNotifiers"] > 1: violations.append("more than one registered active notifier harness")
    if counts["terminalAwaitingReap"] > 1: violations.append("more than one terminal recovery harness awaits reap")
    if counts["alreadyExited"]: violations.append("stale exited controller harness receipt requires cleanup")
    if counts["lookupUnknown"]: violations.append("controller harness process lookup is unknown")
    if counts["identityMismatch"]: violations.append("controller harness PID identity mismatch")
    if counts["unknown"]: violations.append("unknown controller harness ownership")
    out={"schemaVersion":1,"counts":counts,"rows":rows,"violations":violations,"healthy":not violations}
    print(json.dumps(out,indent=2)); return 0 if not violations else 2


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("register"); p.add_argument("--session",required=True); p.add_argument("--pid",type=int); p.set_defaults(func=register)
    p=sub.add_parser("reap"); p.add_argument("--session",required=True); p.add_argument("--current-session",required=True); p.add_argument("--apply",action="store_true"); p.set_defaults(func=reap)
    p=sub.add_parser("report"); p.set_defaults(func=report)
    args=parser.parse_args()
    try: return args.func(args)
    except Exception as exc:
        print(json.dumps({"error":str(exc),"command":args.command},indent=2)); return 2


if __name__ == "__main__": raise SystemExit(main())

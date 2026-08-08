#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Reap harnesses left behind by archived worker/auditor sessions.

Safety gates: archived manifest, role worker/auditor, no live session sharing cwd,
clean worktree, HEAD preserved on an origin ref, and bun/claude harness guard.
Shared cwd groups are drained only when every owning session is archived.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

HOME = Path.home()
WORKSPACE = Path(os.environ.get("CRAFT_WORKSPACE", HOME / ".craft-agent/workspaces/general")).expanduser()
SESSIONS = Path(os.environ.get("CRAFT_SESSIONS", WORKSPACE / "sessions")).expanduser()
RUNTIME = Path(os.environ.get("CRAFT_RUNTIME", HOME / ".craft-agent/runtime")).expanduser()
PID_DIR = Path(os.environ.get("CRAFT_PID_DIR", HOME / ".craft-agent/pids")).expanduser()
LEASES = RUNTIME / "worker-leases"
JOBS = RUNTIME / "worker-jobs"
ROLES = {"worker", "auditor"}


def run(args: list[str], cwd: str | None = None, timeout: int = 20) -> tuple[int, str, str]:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def manifests() -> dict[str, dict[str, Any]]:
    result = {}
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


def worktree_of(manifest: dict[str, Any]) -> str | None:
    raw = manifest.get("workingDirectory") or manifest.get("sdkCwd")
    return str(Path(raw).expanduser().resolve()) if raw else None


def cwd_pids() -> dict[str, list[str]]:
    rc, out, _ = run(["lsof", "-a", "-d", "cwd"], timeout=30)
    result: dict[str, list[str]] = {}
    if rc != 0:
        return result
    for line in out.splitlines()[1:]:
        parts = line.split(None, 8)
        if len(parts) < 9 or parts[0] not in ("bun", "claude"):
            continue
        result.setdefault(parts[8], []).append(parts[1])
    return result


def harness_ok(pid: str) -> bool:
    _, command, _ = run(["ps", "-o", "command=", "-p", str(pid)])
    if not command:
        return False
    if "MacOS/Craft Agents" in command and "Helper" not in command:
        return False
    return "pi-agent-server" in command or "claude-agent-sdk-binary/claude" in command


def default_branch(repo: str) -> str:
    rc, out, _ = run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo)
    if rc == 0 and out:
        return out.rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        rc, _, _ = run(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{candidate}"], cwd=repo)
        if rc == 0:
            return candidate
    return "main"


def work_preserved(worktree: str, readonly_auditors: bool = False) -> tuple[bool, str]:
    if not os.path.isdir(worktree):
        return True, "worktree already absent after archive; no filesystem work remains to lose"
    rc, repo, _ = run(["git", "rev-parse", "--show-toplevel"], cwd=worktree)
    if rc != 0:
        return False, "not a git worktree"
    rc, dirty, _ = run(["git", "status", "--porcelain"], cwd=worktree)
    if rc != 0 or dirty.strip():
        return False, "uncommitted changes"
    if readonly_auditors:
        return True, "clean archived auditor lane; no code changes to preserve"
    _, head, _ = run(["git", "rev-parse", "HEAD"], cwd=worktree)
    _, branch, _ = run(["git", "branch", "--show-current"], cwd=worktree)
    candidates = []
    if branch:
        candidates.append(f"origin/{branch}")
    candidates.append(f"origin/{default_branch(repo)}")
    for ref in candidates:
        rc, _, _ = run(["git", "merge-base", "--is-ancestor", head, ref], cwd=worktree)
        if rc == 0:
            return True, f"HEAD preserved on {ref}"
    return False, "HEAD not found on origin branch/default"


def remove_runtime(session_id: str) -> None:
    for path in (LEASES / f"{session_id}.json", JOBS / f"{session_id}.json", PID_DIR / f"{session_id}.pid"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--session")
    parser.add_argument("--max-groups", type=int, default=0,
                        help="maximum reapable cwd groups to terminate in one run (0 = unlimited)")
    args = parser.parse_args()
    if args.apply and not (args.all or args.session):
        parser.error("--apply requires --all or --session")

    all_manifests = manifests()
    active_by_cwd: dict[str, list[str]] = {}
    archived_by_cwd: dict[str, list[str]] = {}
    for sid, manifest in all_manifests.items():
        if role_of(manifest) not in ROLES:
            continue
        cwd = worktree_of(manifest)
        if not cwd:
            continue
        target = archived_by_cwd if manifest.get("isArchived") else active_by_cwd
        target.setdefault(cwd, []).append(sid)

    pid_map = cwd_pids()
    rows = []
    applied_groups = 0
    for cwd, session_ids in sorted(archived_by_cwd.items()):
        if args.session and args.session not in session_ids:
            continue
        pids = pid_map.get(cwd, [])
        if not pids:
            continue
        row: dict[str, Any] = {"worktree": cwd, "archivedSessions": session_ids, "pids": pids}
        if active_by_cwd.get(cwd):
            row.update(state="blocked", reason="live sessions share cwd", liveSessions=active_by_cwd[cwd])
            rows.append(row)
            continue
        readonly_auditors = all(role_of(all_manifests[sid]) == "auditor" for sid in session_ids)
        ok, detail = work_preserved(cwd, readonly_auditors=readonly_auditors)
        row["preservation"] = detail
        if not ok:
            row.update(state="blocked", reason="work not proved preserved")
            rows.append(row)
            continue
        unsafe = [pid for pid in pids if not harness_ok(pid)]
        if unsafe:
            row.update(state="blocked", reason="non-harness/app guard", unsafePids=unsafe)
            rows.append(row)
            continue
        row["state"] = "reapable"
        if args.apply and args.max_groups > 0 and applied_groups >= args.max_groups:
            row.update(state="deferred", reason="batch limit")
            rows.append(row)
            continue
        if args.apply:
            killed, failed = [], []
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    killed.append(pid)
                except ProcessLookupError:
                    killed.append(pid)
                except Exception as exc:
                    failed.append({"pid": pid, "error": str(exc)})
            time.sleep(0.2)
            row["killed"] = killed
            row["failed"] = failed
            row["state"] = "reaped" if not failed else "partial"
            if not failed:
                applied_groups += 1
                for sid in session_ids:
                    remove_runtime(sid)
        rows.append(row)
    print(json.dumps({"applied": bool(args.apply), "groups": rows,
                      "summary": {"groups": len(rows),
                                  "reapable": sum(r.get("state") == "reapable" for r in rows),
                                  "reaped": sum(r.get("state") == "reaped" for r in rows),
                                  "blocked": sum(r.get("state") == "blocked" for r in rows),
                                  "deferred": sum(r.get("state") == "deferred" for r in rows)}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

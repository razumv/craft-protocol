#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""
Find worker sessions safe to terminate; optionally kill their leaked processes.
PROVIDER-INDEPENDENT and needs NO agent cooperation: a worker's OS harness is
identified by matching the harness process's CWD to the worker's workingDirectory.
Because every worker lives in a UNIQUE <repo>/.worktrees/<branch> (the worktree
standard), this cwd match is unambiguous — works for both Claude (claude-agent-sdk)
and Codex/pi (bun) harnesses. Fallbacks: PID file (register-agent-pid.sh), then the
legacy claude-agent-sdk --resume grep.

Reapable ONLY if: role in {worker,auditor} & not archived & status in {needs-review,done}
& idle >= IDLE_MIN & work preserved (clean worktree AND every local commit on origin).
A cwd with multiple harnesses is a hard collision: no PID is selected or killed.
Default: JSON report. --reap: kill the resolved PID (guarded: never the app), then
remove its PID file. Archiving the session is the AGENT's job (no external CLI).
"""
import json, os, glob, re, subprocess, sys, time
WS = os.path.expanduser("~/.craft-agent/workspaces/general")   # <WORKSPACE>
SESS = os.path.join(WS, "sessions")
PID_DIR = os.path.expanduser("~/.craft-agent/pids")
IDLE_MIN = 10
REAP = "--reap" in sys.argv
ALL = "--all" in sys.argv        # system backstop only (launchd): reap across all projects
PARENT = None                    # coordinator scope: only workers whose parent-session == PARENT
for _i, _a in enumerate(sys.argv):
    if _a.startswith("--parent="): PARENT = _a.split("=", 1)[1]
    elif _a == "--parent" and _i + 1 < len(sys.argv): PARENT = sys.argv[_i + 1]
UU = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
now = time.time() * 1000

def run(a, cwd=None, t=20):
    try:
        r = subprocess.run(a, cwd=cwd, capture_output=True, text=True, timeout=t)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def alive(pid):
    try: os.kill(int(pid), 0); return True
    except Exception: return False

def harness_ok(pid):
    """SAFETY: only ever kill a real session harness — never the Craft app or unrelated procs."""
    _, cmd, _ = run(["ps", "-o", "command=", "-p", str(pid)])
    if not cmd: return False
    if "MacOS/Craft Agents" in cmd and "Helper" not in cmd:  # the app itself
        return False
    return ("pi-agent-server" in cmd) or ("claude-agent-sdk-binary/claude" in cmd)

def cwd_pid_map():
    """PRIMARY: map harness cwd -> ALL PIDs; never hide shared-cwd collisions."""
    _, out, _ = run(["lsof", "-a", "-d", "cwd"], t=30)
    m = {}
    for ln in out.splitlines()[1:]:
        parts = ln.split(None, 8)
        if len(parts) >= 9 and parts[0] in ("bun", "claude"):
            m.setdefault(parts[8], []).append(parts[1])
    return m

def pidfile_pid(sid):
    p = os.path.join(PID_DIR, f"{sid}.pid")
    if not os.path.isfile(p): return None
    try: pid = open(p).read().strip()
    except: return None
    return pid if pid and alive(pid) else None

def rm_pidfile(sid):
    try: os.remove(os.path.join(PID_DIR, f"{sid}.pid"))
    except Exception: pass

def manifest(d):
    f = os.path.join(d, "session.jsonl")
    if not os.path.isfile(f): return None
    try: return json.loads(open(f, encoding="utf-8", errors="ignore").readline())
    except: return None

def label_value(m, prefix):
    for l in (m.get("labels") or []):
        if isinstance(l, str) and l.startswith(prefix): return l.split("::", 1)[1]
    return None

def role_of(m):
    return label_value(m, "agent-role::") or "?"

def parent_of(m):
    return label_value(m, "parent-session::") or m.get("parentSessionId")

def build_uuid_map():
    u2s = {}
    for d in glob.glob(SESS + "/*"):
        sid = os.path.basename(d); f = os.path.join(d, "session.jsonl")
        if os.path.isfile(f):
            for mt in re.finditer(r'"sdkSessionId":"(' + UU + ')"', open(f, encoding="utf-8", errors="ignore").read()):
                u2s[mt.group(1)] = sid
        ta = os.path.join(d, "meta", "claude-turn-anchors.json")
        if os.path.isfile(ta):
            for mt in re.finditer(r'"sdkSessionId":"(' + UU + ')"', open(ta).read()):
                u2s[mt.group(1)] = sid
    return u2s

def legacy_claude_pids(u2s):
    _, out, _ = run(["ps", "-axww", "-o", "pid=,command="], t=15)
    s2 = {}
    for line in out.splitlines():
        if "claude-agent-sdk-binary/claude" not in line: continue
        pid = line.split()[0]
        r = re.search(r'--resume=(' + UU + ')', line)
        if r and r.group(1) in u2s: s2[u2s[r.group(1)]] = pid
    return s2

def default_branch(repo):
    _, out, _ = run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo)
    if out: return out.rsplit("/", 1)[-1]
    for c in ("main", "master"):
        rc, _, _ = run(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{c}"], cwd=repo)
        if rc == 0: return c
    return "main"

def work_preserved(worktree):
    wt = os.path.expanduser(worktree or "")
    if not wt or not os.path.isdir(wt): return False, f"no worktree: {worktree}"
    rc, top, _ = run(["git", "rev-parse", "--show-toplevel"], cwd=wt)
    if rc != 0: return False, "not a git repo"
    rc, dirty, _ = run(["git", "status", "--porcelain"], cwd=wt)
    if dirty.strip(): return False, "uncommitted changes"
    _, branch, _ = run(["git", "branch", "--show-current"], cwd=wt)
    _, head, _ = run(["git", "rev-parse", "HEAD"], cwd=wt)
    base = default_branch(top)
    run(["git", "fetch", "--quiet", "origin"], cwd=top, t=40)
    if branch:
        rc, _, _ = run(["git", "merge-base", "--is-ancestor", head, f"origin/{branch}"], cwd=top)
        if rc == 0: return True, f"pushed to origin/{branch}"
    rc, _, _ = run(["git", "merge-base", "--is-ancestor", head, f"origin/{base}"], cwd=top)
    if rc == 0: return True, f"merged into origin/{base}"
    return False, "unpushed commits"

def resolve_pid(sid, wd, cwdmap, legacy):
    """cwd (primary, unique per worktree) -> pidfile -> legacy claude-resume."""
    wd = os.path.realpath(os.path.expanduser(wd or "")) if wd else ""
    pids = cwdmap.get(wd) or []
    if len(pids) == 1: return pids[0], "cwd"
    if len(pids) > 1: return None, f"cwd-collision:{len(pids)}"
    p = pidfile_pid(sid)
    if p: return p, "pidfile"
    if legacy.get(sid): return legacy[sid], "claude-sdk"
    return None, "none"

def main():
    cwdmap = cwd_pid_map()
    legacy = legacy_claude_pids(build_uuid_map())
    reapable, skipped = [], []
    for d in sorted(glob.glob(SESS + "/*")):
        m = manifest(d)
        if not m: continue
        sid = m.get("id")
        if role_of(m) not in ("worker", "auditor") or m.get("isArchived"): continue
        if PARENT and parent_of(m) != PARENT: continue
        status = m.get("sessionStatus"); name = (m.get("name") or "")[:50]
        wd = m.get("workingDirectory") or m.get("sdkCwd")
        if status not in ("needs-review", "done"):
            skipped.append({"id": sid, "reason": f"status={status}", "name": name}); continue
        idle = (now - (m.get("lastMessageAt") or m.get("lastUsedAt") or 0)) / 60000
        if idle < IDLE_MIN:
            skipped.append({"id": sid, "reason": f"active ({idle:.0f}m)", "name": name}); continue
        ok, detail = work_preserved(wd)
        if not ok:
            skipped.append({"id": sid, "reason": f"not preserved: {detail}", "name": name}); continue
        pid, src = resolve_pid(sid, wd, cwdmap, legacy)
        item = {"id": sid, "pid": pid, "pid_source": src, "status": status,
                "role": role_of(m), "idle_min": round(idle), "worktree": wd,
                "handoff": detail, "name": name}
        if src.startswith("cwd-collision:"):
            item["blocked"] = "shared cwd; use a fresh unique worktree and drain only after the lane is quiescent"
        reapable.append(item)
    report = {"reapable": reapable, "skipped": skipped,
              "summary": f"{len(reapable)} reapable, {len(skipped)} skipped"}
    if REAP and not ALL and not PARENT:
        print(json.dumps({"error": "refusing global --reap without a scope. Coordinators MUST use --parent <your-coordinator-session-id> (your own workers only). Only the system launchd backstop uses --all."}, ensure_ascii=False, indent=2))
        return
    if REAP:
        for w in reapable:
            ok, _ = work_preserved(w["worktree"])
            if not ok: w["kill"] = "ABORTED: no longer preserved"; continue
            if not w["pid"]:
                w["kill"] = "no PID resolved (worktree not matched, no pidfile) — archive only"; continue
            if not harness_ok(w["pid"]):
                w["kill"] = f"REFUSED: PID {w['pid']} is not a session harness (safety guard)"; continue
            rc, _, err = run(["kill", str(w["pid"])])
            if rc == 0: w["kill"] = f"killed {w['pid']} ({w['pid_source']})"; rm_pidfile(w["id"])
            else: w["kill"] = f"fail: {err}"
        report["note"] = "archive_session(id) must be done by the agent (no external CLI)"
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()

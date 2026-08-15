#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Standing owner authority for a repeating external effect, with fail-closed checks.

The owner-gate exists because some effects are the owner's alone. But one class of
gate repeats identically forever: *merge this accepted candidate into the protected
branch*. The decision is always the same shape — independent acceptance passed, the
required CI was green, the certificate validates, no gate blocks that work unit —
so asking again each time spends the owner's attention on a question they already
answered. Observed live on 2026-08-14: a project reached exactly this gate on its
third authorized attempt with every condition already proven.

A standing authority is the owner saying once: *while these conditions hold, do it.*
It is deliberately narrow.

- It is granted per project and per exact branch, by direct owner authority, with a
  reason and an expiry. It never covers a branch or project it does not name.
- It caps risk: a work unit above `maxRiskTier` still needs its own gate.
- Every condition is machine-checked here, from runtime truth and the local clone —
  never from a coordinator's claim. Missing evidence refuses; it never assumes.
- `use` writes a durable receipt before the merge, so an authorized merge is
  auditable afterwards and an unauthorized one is detectable (`unauthorized-merge`).
- A project HOLD, or any open gate scoped to the work unit, overrides it. The owner
  pausing a project must not be silently outranked by an earlier grant.
- The owner may revoke at any time; revocation is immediate and durable.

The authority covers merging into a protected branch. It never covers publishing a
release, deploying, spending money or entitlements, using credentials, or any
irreversible data change — those stay owner-only regardless of any grant.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore
cert_spec = importlib.util.spec_from_file_location("cert_tool", HERE / "completion-certificate.py")
cert_tool = importlib.util.module_from_spec(cert_spec); cert_spec.loader.exec_module(cert_tool)  # type: ignore

RUNTIME = common.RUNTIME
ROOT = RUNTIME / "standing-authorities"
RECEIPTS = RUNTIME / "standing-merges"
GATES = RUNTIME / "owner-gates"
COORDINATORS = RUNTIME / "coordinators"
LOCK = RUNTIME / "standing-authorities.lock"
SCHEMA = 1
KINDS = {"protected-merge"}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_TTL_SECONDS = int(os.environ.get("CRAFT_STANDING_AUTHORITY_MAX_TTL_SECONDS", "2592000"))
GIT_TIMEOUT_SECONDS = int(os.environ.get("CRAFT_STANDING_GIT_TIMEOUT_SECONDS", "20"))


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), flush=True)
    raise SystemExit(2)


def clean_project(raw: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-")
    if not value:
        fail("invalid project slug")
    return value


def path_for(project: str, kind: str) -> Path:
    return ROOT / project / f"{kind}.json"


def load(project: str, kind: str) -> dict[str, Any] | None:
    value = common.read_json(path_for(project, kind))
    return value if isinstance(value, dict) else None


def git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return proc.returncode, proc.stdout.strip()


def open_gates(project: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((GATES / project).glob("*.json")):
        row = common.read_json(path)
        if isinstance(row, dict) and row.get("state") == "open":
            rows.append(row)
    return rows


def blocking_gates(project: str, work_unit: str | None) -> list[str]:
    """A project HOLD blocks everything; a work-unit gate blocks its own work unit.

    An earlier standing grant must never outrank the owner pausing the project now.
    """
    blockers = []
    for gate in open_gates(project):
        gate_id = str(gate.get("gateId") or "")
        # The field is `blockingScope` on disk; `scope` is the CLI's name for it.
        scope = gate.get("blockingScope") or gate.get("scope")
        target = gate.get("workUnit")
        if scope == "project" or gate_id.startswith("project-hold"):
            blockers.append(gate_id)
        elif scope in {"work-unit", "merge"} and target and target == work_unit:
            blockers.append(gate_id)
    return sorted(set(blockers))


def refusals(authority: dict[str, Any] | None, certificate: dict[str, Any] | None,
             *, project: str, work_unit: str, branch: str, repo: Path | None,
             risk_tier: str, now: int) -> list[str]:
    """Every reason this merge may not proceed on standing authority alone."""
    out: list[str] = []
    if not authority:
        return ["no-standing-authority"]
    if authority.get("state") != "granted":
        out.append(f"authority-{authority.get('state') or 'unknown'}")
    expires = authority.get("expiresAt")
    if not isinstance(expires, int) or expires <= now:
        out.append("authority-expired")
    if authority.get("project") != project:
        out.append("authority-project-mismatch")
    if branch not in (authority.get("branches") or []):
        out.append(f"branch-not-authorized:{branch}")
    ceiling = str(authority.get("maxRiskTier") or "medium")
    if RISK_ORDER.get(risk_tier, 3) > RISK_ORDER.get(ceiling, 1):
        out.append(f"risk-above-ceiling:{risk_tier}>{ceiling}")
    if certificate is None:
        out.append("certificate-unreadable")
    else:
        if certificate.get("project") != project:
            out.append("certificate-project-mismatch")
        if str(certificate.get("workUnit") or "") != work_unit:
            out.append("certificate-work-unit-mismatch")
        # Authorization is a pre-merge judgement: an independent PASS on this exact
        # candidate, green required CI, a head proven unchanged, no unresolved gates.
        # The merged-branch readback belongs to the completion certificate written
        # after the merge — demanding it here made authorization impossible on any
        # branch that takes real merge commits, and turned it into a post-hoc stamp
        # wherever squash merges hid the cycle.
        for error in cert_tool.pre_merge_errors(certificate):
            out.append(f"certificate:{error}")
    registry = common.read_json(COORDINATORS / f"{project}.json")
    if registry and registry.get("state") == "hold":
        out.append("project-hold")
    gates = blocking_gates(project, work_unit)
    if gates:
        out.append("gate-blocks:" + ",".join(gates[:4]))
    if repo is None:
        out.append("repo-unreadable")
    else:
        if not (repo / ".git").exists():
            out.append("repo-unreadable")
        elif certificate is not None:
            candidate = str(certificate.get("candidateSha") or "")
            if not SHA.fullmatch(candidate):
                out.append("candidate-sha-not-exact")
            elif git(repo, "cat-file", "-e", candidate + "^{commit}")[0] != 0:
                out.append("candidate-absent-from-clone")
            elif git(repo, "rev-parse", "--verify", f"origin/{branch}")[0] != 0:
                out.append(f"branch-absent-from-clone:origin/{branch}")
            elif git(repo, "merge-base", "--is-ancestor", candidate, f"origin/{branch}")[0] == 0:
                # Already delivered: re-merging is not what the owner authorized.
                out.append("candidate-already-in-branch")
    return sorted(set(out))


def cmd_grant(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    if args.kind not in KINDS:
        fail(f"kind must be one of {sorted(KINDS)}")
    branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    if not branches or any(not BRANCH.fullmatch(b) for b in branches):
        fail("--branches must be a comma-separated list of exact branch names")
    if args.max_risk_tier not in RISK_ORDER:
        fail(f"--max-risk-tier must be one of {sorted(RISK_ORDER)}")
    ttl = int(args.ttl_seconds)
    if ttl < 60 or ttl > MAX_TTL_SECONDS:
        fail(f"--ttl-seconds must be between 60 and {MAX_TTL_SECONDS}")
    if args.authority != "direct-owner":
        fail("only direct-owner authority may grant a standing authority")
    now = common.now_ms()
    value = {"schemaVersion": SCHEMA, "project": project, "kind": args.kind,
             "state": "granted", "branches": branches, "maxRiskTier": args.max_risk_tier,
             "authority": args.authority, "reason": args.reason,
             "grantedAt": now, "expiresAt": now + ttl * 1000,
             "revokedAt": None, "revokeReason": None}
    with common.file_lock(LOCK):
        common.atomic_json(path_for(project, args.kind), value)
    print(json.dumps({"ok": True, "authority": value}, ensure_ascii=False, indent=2))
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    with common.file_lock(LOCK):
        value = load(project, args.kind)
        if not value:
            fail("no standing authority to revoke")
        value.update({"state": "revoked", "revokedAt": common.now_ms(),
                      "revokeReason": args.reason})
        common.atomic_json(path_for(project, args.kind), value)
    print(json.dumps({"ok": True, "authority": value}, ensure_ascii=False, indent=2))
    return 0


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    project = clean_project(args.project)
    certificate = common.read_json(Path(args.certificate).expanduser())
    certificate = certificate if isinstance(certificate, dict) else None
    repo_raw = common.expand_path(args.repo) if args.repo else None
    repo = Path(repo_raw) if repo_raw else None
    now = common.now_ms()
    authority = load(project, "protected-merge")
    reasons = refusals(authority, certificate, project=project, work_unit=args.work_unit,
                       branch=args.branch, repo=repo, risk_tier=args.risk_tier, now=now)
    return {"project": project, "workUnit": args.work_unit, "branch": args.branch,
            "riskTier": args.risk_tier, "authorized": not reasons, "refusals": reasons,
            "authority": authority, "checkedAt": now}


def cmd_check(args: argparse.Namespace) -> int:
    result = evaluate(args)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0 if result["authorized"] else 4


def cmd_use(args: argparse.Namespace) -> int:
    """Record the receipt *before* the merge, so the trail cannot be written after."""
    result = evaluate(args)
    if not result["authorized"]:
        print(json.dumps({"ok": False, **result}, ensure_ascii=False, indent=2))
        return 4
    certificate = common.read_json(Path(args.certificate).expanduser()) or {}
    receipt = {"schemaVersion": SCHEMA, "project": result["project"],
               "workUnit": args.work_unit, "branch": args.branch,
               "candidateSha": certificate.get("candidateSha"),
               "certificatePath": str(Path(args.certificate).expanduser()),
               "riskTier": args.risk_tier, "coordinatorSessionId": args.session,
               # The merge is authorized, the proof that it landed cleanly is not yet
               # written: the completion certificate still owes merged-branch readback.
               "readbackOwed": True,
               "authorityGrantedAt": (result.get("authority") or {}).get("grantedAt"),
               "usedAt": result["checkedAt"]}
    candidate = str(certificate.get("candidateSha") or "")
    path = RECEIPTS / result["project"] / f"{args.work_unit}-{candidate[:12]}.json"
    with common.file_lock(LOCK):
        if path.exists():
            fail("this candidate already has a standing-merge receipt")
        common.atomic_json(path, receipt)
    print(json.dumps({"ok": True, "authorized": True, "receipt": receipt,
                      "receiptPath": str(path)}, ensure_ascii=False, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = []
    pattern = f"{clean_project(args.project)}/*.json" if args.project else "*/*.json"
    for path in sorted(ROOT.glob(pattern)):
        row = common.read_json(path)
        if isinstance(row, dict):
            rows.append(row)
    receipts = []
    receipt_pattern = f"{clean_project(args.project)}/*.json" if args.project else "*/*.json"
    for path in sorted(RECEIPTS.glob(receipt_pattern)):
        row = common.read_json(path)
        if isinstance(row, dict):
            receipts.append(row)
    print(json.dumps({"ok": True, "authorities": rows, "receipts": receipts},
                     ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("grant")
    g.add_argument("--project", required=True)
    g.add_argument("--kind", default="protected-merge")
    g.add_argument("--branches", required=True)
    g.add_argument("--max-risk-tier", default="medium")
    g.add_argument("--ttl-seconds", default="604800")
    g.add_argument("--authority", required=True)
    g.add_argument("--reason", required=True)
    g.set_defaults(func=cmd_grant)
    r = sub.add_parser("revoke")
    r.add_argument("--project", required=True)
    r.add_argument("--kind", default="protected-merge")
    r.add_argument("--reason", required=True)
    r.set_defaults(func=cmd_revoke)
    for name, func in (("check", cmd_check), ("use", cmd_use)):
        c = sub.add_parser(name)
        c.add_argument("--project", required=True)
        c.add_argument("--work-unit", required=True)
        c.add_argument("--branch", required=True)
        c.add_argument("--certificate", required=True)
        c.add_argument("--repo", required=True)
        c.add_argument("--risk-tier", default="medium")
        c.add_argument("--session", default=None)
        c.set_defaults(func=func)
    l = sub.add_parser("list")
    l.add_argument("--project")
    l.set_defaults(func=cmd_list)
    return p


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

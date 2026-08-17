#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed garbage collection for registered Craft worktrees.

Dry-run is the default. Apply mode is bounded and revalidates every candidate.
This tool only invokes ``git worktree remove``; it never deletes branches, refs,
commits, session manifests, or session history.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from typing import Any

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(common)  # type: ignore

RUNTIME = common.RUNTIME
REGISTRY = RUNTIME / "coordinators"
REPORT_DIR = RUNTIME / "worktree-gc"
LOCK = RUNTIME / "worktree-gc.lock"
ACTIVE_REGISTRY_STATES = {"authoritative", "rotating", "hold", "needs-owner"}
DEFAULT_MAX_REMOVE = int(os.environ.get("CRAFT_WORKTREE_GC_MAX_REMOVE", "3"))
DEFAULT_UNOWNED_MIN_AGE_HOURS = float(os.environ.get("CRAFT_WORKTREE_GC_UNOWNED_MIN_AGE_HOURS", "24"))
DRY_RUN_MAX_AGE_SECONDS = int(os.environ.get("CRAFT_WORKTREE_GC_DRY_RUN_MAX_AGE_SECONDS", "3600"))


def run(args: list[str], cwd: str | None = None, timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def canonical(raw: object) -> str | None:
    return common.canonical_path(raw) if isinstance(raw, (str, Path)) else None


def manifest_cwds(manifest: dict[str, Any]) -> list[str]:
    """Protect every asserted canonical CWD, even when legacy fields disagree."""
    return sorted({value for key in ("workingDirectory", "sdkCwd")
                   if (value := canonical(manifest.get(key)))})


def registry_records() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(REGISTRY.glob("*.json")):
        value = common.read_json(path)
        if value and value.get("state") in ACTIVE_REGISTRY_STATES:
            rows.append(value)
    return rows


def cwd_holders() -> tuple[bool, dict[str, list[str]], str | None]:
    """Return every observable process CWD, or fail closed if observation fails.

    Tests and controlled runtimes may provide an exact JSON object through
    CRAFT_GC_CWD_HOLDERS_JSON. Production uses lsof's machine-readable fields.
    """
    injected = os.environ.get("CRAFT_GC_CWD_HOLDERS_JSON")
    if injected is not None:
        try:
            value = json.loads(injected)
            if not isinstance(value, dict):
                raise ValueError("expected object")
            return True, {path: sorted({str(pid) for pid in pids})
                          for raw, pids in value.items()
                          if (path := canonical(raw)) and isinstance(pids, list)}, None
        except Exception as exc:
            return False, {}, f"invalid CRAFT_GC_CWD_HOLDERS_JSON: {exc}"
    rc, out, err = run(["lsof", "-a", "-d", "cwd", "-Fn"], timeout=45)
    if rc != 0:
        return False, {}, err or "lsof cwd observation failed"
    result: dict[str, list[str]] = {}
    pid: str | None = None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("n") and pid and (path := canonical(line[1:])):
            result.setdefault(path, []).append(pid)
    return True, {path: sorted(set(pids)) for path, pids in result.items()}, None


def parse_worktrees(repo: str) -> tuple[list[dict[str, Any]], str | None]:
    rc, out, err = run(["git", "-C", repo, "worktree", "list", "--porcelain"])
    if rc != 0:
        return [], err or "git worktree list failed"
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*out.splitlines(), ""]:
        if not line:
            if current.get("path"):
                rows.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree": current["path"] = canonical(value)
        elif key == "HEAD": current["head"] = value
        elif key == "branch": current["branch"] = value
        elif key in {"detached", "bare", "locked", "prunable"}: current[key] = value or True
    if rows:
        rows[0]["rootCheckout"] = True
    return rows, None


def repo_identity(cwd: str) -> tuple[str | None, str | None]:
    rc, top, _ = run(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
    if rc != 0 or not top:
        return None, None
    rc, common_dir, _ = run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if rc != 0 or not common_dir:
        return None, None
    return canonical(top), canonical(common_dir)


def discover_repositories(explicit: list[str], manifests: dict[str, dict[str, Any]], records: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    seeds = [*explicit]
    seeds.extend(cwd for manifest in manifests.values() for cwd in manifest_cwds(manifest))
    seeds.extend(str(row.get("coordinatorCwd")) for row in records if row.get("coordinatorCwd"))
    by_common: dict[str, str] = {}
    errors: list[str] = []
    for raw in sorted(set(seeds)):
        cwd = canonical(raw)
        if not cwd or not os.path.isdir(cwd):
            if raw in explicit:
                errors.append(f"repository path unavailable: {raw}")
            continue
        top, common_dir = repo_identity(cwd)
        if top and common_dir:
            existing = by_common.get(common_dir)
            if existing is None or len(top) < len(existing):
                by_common[common_dir] = top
        elif raw in explicit:
            errors.append(f"not a git worktree: {raw}")
    return sorted(by_common.values()), errors


def worktree_age_ms(path: str, now: int) -> int:
    mtimes: list[float] = []
    with contextlib.suppress(OSError):
        mtimes.append(Path(path).stat().st_mtime)
    rc, git_dir, _ = run(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-dir"])
    if rc == 0 and git_dir:
        admin = Path(git_dir)
        for child in (admin, admin / "HEAD", admin / "gitdir", admin / "index"):
            with contextlib.suppress(OSError):
                mtimes.append(child.stat().st_mtime)
    latest = max(mtimes, default=now / 1000)
    return max(0, now - int(latest * 1000))


def detached_references(path: str, head: str) -> list[str]:
    rc, out, _ = run(["git", "-C", path, "for-each-ref", "--format=%(refname)",
                      "--contains", head, "refs/heads", "refs/remotes", "refs/tags"])
    return sorted(line for line in out.splitlines() if line) if rc == 0 else []


def protection_snapshot() -> dict[str, Any]:
    manifests = common.all_manifests()
    records = registry_records()
    owners: dict[str, list[str]] = {}
    active: dict[str, list[str]] = {}
    archived: dict[str, list[str]] = {}
    for sid, manifest in sorted(manifests.items()):
        for cwd in manifest_cwds(manifest):
            owners.setdefault(cwd, []).append(sid)
            (archived if manifest.get("isArchived") else active).setdefault(cwd, []).append(sid)
    active_child_ids = sorted({str(sid) for row in records for sid in (row.get("activeChildren") or [])
                               if isinstance(sid, str) and sid})
    active_child_paths: dict[str, list[str]] = {}
    for sid in active_child_ids:
        manifest = manifests.get(sid)
        for cwd in manifest_cwds(manifest or {}):
            active_child_paths.setdefault(cwd, []).append(sid)
    holders_complete, holders, holders_error = cwd_holders()
    return {"manifests": manifests, "records": records, "owners": owners, "active": active,
            "archived": archived, "activeChildIds": active_child_ids,
            "activeChildPaths": active_child_paths, "holdersComplete": holders_complete,
            "holders": holders, "holdersError": holders_error}


def values_within(mapping: dict[str, list[str]], worktree: str) -> list[str]:
    """Collect identities whose CWD is the worktree or any directory below it."""
    values: set[str] = set()
    for cwd, identities in mapping.items():
        try:
            if os.path.commonpath([cwd, worktree]) == worktree:
                values.update(identities)
        except ValueError:
            continue
    return sorted(values)


def classify(row: dict[str, Any], root: str, snapshot: dict[str, Any], now: int,
             unowned_min_age_ms: int) -> dict[str, Any]:
    path = str(row.get("path") or "")
    owners = values_within(snapshot["owners"], path)
    active_owners = values_within(snapshot["active"], path)
    archived_owners = values_within(snapshot["archived"], path)
    active_children = values_within(snapshot["activeChildPaths"], path)
    holder_pids = values_within(snapshot["holders"], path)
    result = {"repository": root, "worktree": path, "head": row.get("head"),
              "branch": row.get("branch"), "detached": bool(row.get("detached")),
              "owners": owners, "activeOwners": active_owners,
              "archivedOwners": archived_owners, "activeChildren": active_children,
              "cwdHolderPids": holder_pids}
    reasons: list[str] = []
    if row.get("rootCheckout") or path == root:
        reasons.append("root-checkout")
    managed_root = canonical(Path(root) / ".worktrees")
    try:
        under_managed = bool(managed_root and os.path.commonpath([path, managed_root]) == managed_root and path != managed_root)
    except ValueError:
        under_managed = False
    if not under_managed:
        reasons.append("outside-managed-worktree-root")
    if row.get("locked"):
        reasons.append("git-worktree-locked")
    if active_owners:
        reasons.append("non-archived-session-cwd")
    if active_children:
        reasons.append("authoritative-active-child")
    if len(owners) > 1:
        reasons.append("shared-session-cwd")
    if not snapshot["holdersComplete"]:
        reasons.append("cwd-holder-scan-unavailable")
    elif holder_pids:
        reasons.append("cwd-holder")
    if not os.path.isdir(path):
        reasons.append("worktree-path-absent")
    else:
        rc, dirty, err = run(["git", "-C", path, "status", "--porcelain", "--untracked-files=all"])
        if rc != 0:
            reasons.append("git-status-unavailable")
            result["gitError"] = err
        elif dirty:
            reasons.append("dirty-worktree")
        if row.get("detached"):
            refs = detached_references(path, str(row.get("head") or ""))
            result["preservingRefs"] = refs
            if not refs:
                reasons.append("unreachable-detached-head")
        elif row.get("branch"):
            rc, branch_head, _ = run(["git", "-C", path, "rev-parse", str(row["branch"])])
            if rc != 0 or branch_head != row.get("head"):
                reasons.append("attached-branch-head-mismatch")
        else:
            reasons.append("head-preservation-unproved")
    age_ms = worktree_age_ms(path, now)
    result["ageMs"] = age_ms
    ownership = "archived" if archived_owners else "unowned"
    result["ownership"] = ownership
    if ownership == "unowned" and age_ms < unowned_min_age_ms:
        reasons.append("unowned-worktree-too-recent")
    result["reasons"] = sorted(set(reasons))
    if reasons:
        result["state"] = "protected"
    else:
        result["state"] = "candidate"
        result["classification"] = "stale-clean-worktree"
        result["preservation"] = "attached branch retained" if row.get("branch") else "detached HEAD reachable from retained ref"
    return result


def build_report(explicit_repos: list[str], unowned_min_age_ms: int) -> dict[str, Any]:
    now = common.now_ms()
    snapshot = protection_snapshot()
    repos, discovery_errors = discover_repositories(explicit_repos, snapshot["manifests"], snapshot["records"])
    rows: list[dict[str, Any]] = []
    errors = list(discovery_errors)
    for root in repos:
        worktrees, error = parse_worktrees(root)
        if error:
            errors.append(f"{root}: {error}")
            continue
        rows.extend(classify(row, root, snapshot, now, unowned_min_age_ms) for row in worktrees)
    return {"schemaVersion": 1, "protocolVersion": "3.4.39", "generatedAt": now,
            "mode": "dry-run", "applied": False, "observationComplete": not errors and snapshot["holdersComplete"],
            "observationErrors": [*errors, *([snapshot["holdersError"]] if snapshot["holdersError"] else [])],
            "repositories": repos, "worktrees": rows}


def finalize(report: dict[str, Any]) -> None:
    rows = report["worktrees"]
    report["summary"] = {
        "repositories": len(report["repositories"]), "worktrees": len(rows),
        "staleCleanWorktrees": sum(row.get("classification") == "stale-clean-worktree"
                                   and row.get("state") in {"candidate", "deferred"} for row in rows),
        "removed": sum(row.get("state") == "removed" for row in rows),
        "protected": sum(row.get("state") == "protected" for row in rows),
        "deferred": sum(row.get("state") == "deferred" for row in rows),
    }


def write_report(report: dict[str, Any], output: Path) -> None:
    common.atomic_json(output, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="remove eligible worktrees after fresh safety validation")
    parser.add_argument("--max-remove", type=int, default=DEFAULT_MAX_REMOVE,
                        help=f"maximum removals in apply mode (default {DEFAULT_MAX_REMOVE})")
    parser.add_argument("--unowned-min-age-hours", type=float, default=DEFAULT_UNOWNED_MIN_AGE_HOURS,
                        help=f"minimum age for unowned worktrees (default {DEFAULT_UNOWNED_MIN_AGE_HOURS:g})")
    parser.add_argument("--repo", action="append", default=[], help="additional repository/worktree seed (repeatable)")
    parser.add_argument("--output", default=str(REPORT_DIR / "latest.json"), help="durable JSON report path")
    args = parser.parse_args()
    if args.max_remove <= 0:
        parser.error("--max-remove must be positive")
    if args.unowned_min_age_hours < 0:
        parser.error("--unowned-min-age-hours must be non-negative")
    output = Path(args.output).expanduser()
    removed = 0
    exit_code = 0
    with common.file_lock(LOCK):
        prior = common.read_json(output) if args.apply else None
        report = build_report(args.repo, int(args.unowned_min_age_hours * 3_600_000))
        report["mode"] = "apply" if args.apply else "dry-run"
        report["applied"] = bool(args.apply)
        report["maxRemove"] = args.max_remove
        report["unownedMinAgeHours"] = args.unowned_min_age_hours
        reviewed: set[tuple[str, str, str]] = set()
        if args.apply:
            prior_age = common.now_ms() - int((prior or {}).get("generatedAt") or 0)
            prior_ok = bool(prior and prior.get("schemaVersion") == 1
                            and prior.get("protocolVersion") == "3.4.39"
                            and prior.get("mode") == "dry-run"
                            and prior.get("observationComplete")
                            and prior.get("unownedMinAgeHours") == args.unowned_min_age_hours
                            and 0 <= prior_age <= DRY_RUN_MAX_AGE_SECONDS * 1000)
            if prior_ok:
                reviewed = {(str(row.get("repository") or ""), str(row.get("worktree") or ""),
                             str(row.get("head") or "")) for row in (prior.get("worktrees") or [])
                            if isinstance(row, dict) and row.get("state") == "candidate"
                            and row.get("classification") == "stale-clean-worktree"}
            else:
                report["mode"] = "apply-refused"
                report["applied"] = False
                report["applyRefusal"] = "fresh-complete-dry-run-report-required"
                exit_code = 2
            for row in report["worktrees"]:
                if row.get("state") != "candidate":
                    continue
                fingerprint = (str(row.get("repository") or ""), str(row.get("worktree") or ""),
                               str(row.get("head") or ""))
                if not prior_ok or fingerprint not in reviewed:
                    row["state"] = "protected"; row["revalidationReasons"] = ["not-reviewed-in-prior-dry-run"]
                    continue
                if removed >= args.max_remove:
                    row["state"] = "deferred"; row["reason"] = "bounded-apply-limit"
                    continue
                # Reload all session, registry and process-CWD protections, then
                # reload Git's registered row. This closes the scan/apply gap.
                fresh_snapshot = protection_snapshot()
                fresh_rows, error = parse_worktrees(str(row["repository"]))
                fresh_raw = next((item for item in fresh_rows if item.get("path") == row["worktree"]), None)
                if error or not fresh_raw:
                    row["state"] = "protected"; row["revalidationReasons"] = ["registration-changed"]
                    continue
                fresh = classify(fresh_raw, str(row["repository"]), fresh_snapshot,
                                 common.now_ms(), int(args.unowned_min_age_hours * 3_600_000))
                if fresh.get("state") != "candidate":
                    row["state"] = "protected"; row["revalidationReasons"] = fresh.get("reasons", [])
                    continue
                rc, _, err = run(["git", "-C", str(row["repository"]), "worktree", "remove", str(row["worktree"])], timeout=60)
                if rc == 0:
                    row["state"] = "removed"; row["removedAt"] = common.now_ms(); removed += 1
                else:
                    row["state"] = "protected"; row["revalidationReasons"] = ["git-worktree-remove-refused"]
                    row["gitError"] = err
        finalize(report)
        write_report(report, output)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

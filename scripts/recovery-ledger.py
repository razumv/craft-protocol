#!/opt/homebrew/bin/python3
"""Synthesize an external cold-takeover ledger from manifests, leases, jobs and gates."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore
ROOT = common.RUNTIME / "recovery-ledger"; LOCK = common.RUNTIME / "recovery-ledger.lock"


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [v for p in sorted(path.glob("*.json")) if (v := common.read_json(p))]


def path_within(raw_child: str | None, raw_parent: str | None) -> bool:
    if not raw_child or not raw_parent:
        return False
    try:
        Path(raw_child).expanduser().resolve().relative_to(Path(raw_parent).expanduser().resolve())
        return True
    except Exception:
        return False


def synthesize(project: str) -> dict[str, Any]:
    coordinator = common.read_json(common.RUNTIME / "coordinators" / f"{project}.json") or {"project": project, "state": "missing"}
    owner = coordinator.get("coordinatorSessionId")
    manifests = common.all_manifests(); owner_manifest = manifests.get(str(owner)) or {}
    owner_cwd = owner_manifest.get("workingDirectory") or owner_manifest.get("sdkCwd")
    leases = load_rows(common.RUNTIME / "worker-leases")
    jobs = {p.stem: v for p in (common.RUNTIME / "worker-jobs").glob("*.json") if (v := common.read_json(p))}
    children: list[dict[str, Any]] = []
    for lease in leases:
        sid = str(lease.get("sessionId") or ""); manifest = manifests.get(sid)
        if not manifest or manifest.get("isArchived"): continue
        explicit_project = common.label_value(manifest, "project::")
        child_cwd = lease.get("worktree") or manifest.get("workingDirectory") or manifest.get("sdkCwd")
        belongs = (lease.get("parentSessionId") == owner or explicit_project == project or
                   (explicit_project is None and path_within(child_cwd, owner_cwd)))
        # Native projectId alone is never sufficient: Lineage client/server share it.
        if not belongs: continue
        children.append({"sessionId": sid, "role": lease.get("role"), "workUnit": lease.get("workUnit"),
            "attempt": lease.get("attempt"), "worktree": lease.get("worktree"), "state": lease.get("state"),
            "phase": lease.get("phase"), "lastEvidenceAt": lease.get("lastEvidenceAt"),
            "preservationState": lease.get("preservationState", "unknown"), "job": jobs.get(sid),
            "manifestStatus": manifest.get("sessionStatus"), "parentSessionId": lease.get("parentSessionId")})
    gates = [v for p in (common.RUNTIME / "owner-gates" / project).glob("*.json") if (v := common.read_json(p))]
    certs = [str(p) for p in sorted((common.RUNTIME / "completion-certificates" / project).glob("*.json"))]
    return {"schemaVersion": 1, "project": project, "generatedAt": common.now_ms(), "coordinator": coordinator,
            "activeChildren": children, "openGates": [g for g in gates if g.get("state") == "open"],
            "resolvedGates": [g for g in gates if g.get("state") == "resolved"],
            "completionCertificates": certs,
            "unknowns": (["authoritative-coordinator"] if coordinator.get("state") == "missing" else []) +
                        [f"preservation:{c['sessionId']}" for c in children if c.get("preservationState") == "unknown"]}


def cmd_snapshot(args: argparse.Namespace) -> int:
    value = synthesize(args.project)
    with common.file_lock(LOCK):
        common.atomic_json(ROOT / f"{args.project}.json", value)
    # Keep compact ownership metadata in sync without changing owner/generation.
    registry_path = common.RUNTIME / "coordinators" / f"{args.project}.json"
    with common.file_lock(common.RUNTIME / "coordinators.lock"):
        record = common.read_json(registry_path)
        if record:
            record["activeChildren"] = [c.get("sessionId") for c in value["activeChildren"]]
            record["unresolvedGates"] = [g.get("gateId") for g in value["openGates"]]
            record["lastRecoverySnapshotAt"] = value["generatedAt"]
            common.atomic_json(registry_path, record)
    print(json.dumps(value, ensure_ascii=False, indent=2)); return 0


def cmd_reconstruct(args: argparse.Namespace) -> int:
    observed = synthesize(args.project); saved = common.read_json(ROOT / f"{args.project}.json")
    print(json.dumps({"project": args.project, "saved": saved, "observed": observed,
        "safeToLaunchNewLane": not any(c.get("state") in {"starting", "running", "suspect"} for c in observed["activeChildren"]),
        "rule": "adopt matching live work-unit attempt; never infer completion from absence"}, ensure_ascii=False, indent=2)); return 0


def cmd_diff(args: argparse.Namespace) -> int:
    saved = common.read_json(ROOT / f"{args.project}.json") or {}; current = synthesize(args.project)
    old = {c.get("sessionId"): c for c in saved.get("activeChildren") or []}; new = {c.get("sessionId"): c for c in current.get("activeChildren") or []}
    result = {"addedChildren": sorted(set(new)-set(old)), "missingChildren": sorted(set(old)-set(new)),
              "changedChildren": sorted(k for k in set(old)&set(new) if old[k] != new[k]),
              "coordinatorChanged": saved.get("coordinator") != current.get("coordinator")}
    print(json.dumps(result, indent=2)); return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    for name, func in (("snapshot", cmd_snapshot), ("reconstruct", cmd_reconstruct), ("diff", cmd_diff)):
        q = sub.add_parser(name); q.add_argument("--project", required=True); q.set_defaults(func=func)
    return p

if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(args.func(args))

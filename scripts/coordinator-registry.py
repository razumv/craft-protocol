#!/opt/homebrew/bin/python3
"""Atomic authoritative-coordinator ownership registry.

The registry is append-independent runtime truth. It never mutates session JSONL.
Transfers are two-phase and generation-checked to prevent split brain.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore

RUNTIME = common.RUNTIME
REGISTRY = RUNTIME / "coordinators"
LOCK = RUNTIME / "coordinators.lock"
SCHEMA = 1
PREFERRED_CONNECTION = os.environ.get("CRAFT_COORDINATOR_CONNECTION", "chatgpt-plus")
PREFERRED_MODEL = os.environ.get("CRAFT_COORDINATOR_MODEL", "pi/gpt-5.6-sol")
DEFAULT_TTL = int(os.environ.get("CRAFT_COORDINATOR_TTL_SECONDS", "3600"))
FALLBACK_TTL = int(os.environ.get("CRAFT_FALLBACK_TTL_SECONDS", "3600"))
VALID_STATES = {"authoritative", "rotating", "hold", "superseded", "needs-owner"}


def clean_project(raw: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-")
    if not value:
        raise SystemExit("invalid project slug")
    return value


def path_for(project: str) -> Path:
    return REGISTRY / f"{clean_project(project)}.json"


def load(project: str) -> dict[str, Any] | None:
    return common.read_json(path_for(project))


def save(value: dict[str, Any]) -> None:
    value["schemaVersion"] = SCHEMA
    value["updatedAt"] = common.now_ms()
    common.atomic_json(path_for(str(value["project"])), value)


def manifest_or_die(sid: str) -> dict[str, Any]:
    manifest = common.read_manifest(sid)
    if not manifest:
        raise SystemExit(f"session manifest not found: {sid}")
    if not common.session_live(manifest):
        raise SystemExit(f"session is not live: {sid}")
    if common.role_of(manifest) != "coordinator":
        raise SystemExit(f"session is not a coordinator: {sid}")
    return manifest


def provider_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    connection = manifest.get("llmConnection")
    model = manifest.get("model")
    preferred = connection == PREFERRED_CONNECTION and model == PREFERRED_MODEL
    return {
        "connection": connection, "model": model, "preferredProvider": preferred,
        "fallbackSince": None if preferred else common.now_ms(),
        "fallbackExpiresAt": None if preferred else common.now_ms() + FALLBACK_TTL * 1000,
    }


def cmd_claim(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    manifest = manifest_or_die(args.session)
    with common.file_lock(LOCK):
        current = load(project)
        if current and current.get("state") in {"authoritative", "rotating", "hold", "needs-owner"}:
            owner = str(current.get("coordinatorSessionId") or "")
            if owner != args.session and common.session_live(common.read_manifest(owner)):
                print(json.dumps({"ok": False, "error": "split-brain-refused", "current": current}, indent=2))
                return 3
            generation = int(current.get("generation") or 0) + (0 if owner == args.session else 1)
        else:
            generation = int((current or {}).get("generation") or 0) + 1
        now = common.now_ms()
        value = {
            "schemaVersion": SCHEMA, "project": project,
            "projectId": args.project_id or manifest.get("projectId"),
            "coordinatorSessionId": args.session, "generation": generation,
            "state": "authoritative", "predecessorSessionId": args.predecessor,
            "successorSessionId": None, "claimedAt": now, "lastHeartbeatAt": now,
            "leaseExpiresAt": now + int(args.ttl) * 1000,
            "transferStartedAt": None, "fallbackReason": args.fallback_reason,
            "unresolvedGates": [], "activeChildren": [], **provider_fields(manifest),
        }
        save(value)
    print(json.dumps({"ok": True, "record": value}, ensure_ascii=False, indent=2))
    return 0


def require_owner(project: str, session: str) -> dict[str, Any]:
    value = load(project)
    if not value or value.get("coordinatorSessionId") != session:
        raise SystemExit(f"not authoritative owner for {project}: {session}")
    return value


def cmd_renew(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    with common.file_lock(LOCK):
        value = require_owner(project, args.session)
        if value.get("state") not in {"authoritative", "hold", "needs-owner"}:
            raise SystemExit(f"cannot renew state={value.get('state')}")
        now = common.now_ms(); value["lastHeartbeatAt"] = now; value["leaseExpiresAt"] = now + int(args.ttl) * 1000
        save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def cmd_begin_transfer(args: argparse.Namespace) -> int:
    project = clean_project(args.project); manifest_or_die(args.successor)
    with common.file_lock(LOCK):
        value = require_owner(project, args.session)
        if value.get("state") == "rotating":
            if value.get("successorSessionId") == args.successor:
                print(json.dumps({"ok": True, "record": value, "idempotent": True}, indent=2)); return 0
            raise SystemExit("transfer already open")
        if value.get("state") == "hold":
            raise SystemExit("project HOLD blocks transfer")
        value["state"] = "rotating"; value["successorSessionId"] = args.successor
        value["transferStartedAt"] = common.now_ms(); value["transferReason"] = args.reason
        save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def cmd_accept_transfer(args: argparse.Namespace) -> int:
    project = clean_project(args.project); manifest = manifest_or_die(args.session)
    with common.file_lock(LOCK):
        value = load(project)
        if not value or value.get("state") != "rotating" or value.get("successorSessionId") != args.session:
            raise SystemExit("no matching open transfer")
        if args.expected_generation is not None and int(value.get("generation") or 0) != args.expected_generation:
            raise SystemExit("generation mismatch")
        predecessor = value.get("coordinatorSessionId"); now = common.now_ms()
        value.update({"coordinatorSessionId": args.session, "predecessorSessionId": predecessor,
                      "successorSessionId": None, "generation": int(value.get("generation") or 0) + 1,
                      "state": "authoritative", "claimedAt": now, "lastHeartbeatAt": now,
                      "leaseExpiresAt": now + int(args.ttl) * 1000, "transferAcceptedAt": now,
                      "transferStartedAt": None, "priorFallbackReason": value.get("fallbackReason"),
                      "fallbackReason": None, **provider_fields(manifest)})
        save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def cmd_hold(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    with common.file_lock(LOCK):
        value = require_owner(project, args.session)
        value["state"] = "hold"; value["holdReason"] = args.reason; value["holdAt"] = common.now_ms(); save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def cmd_resume(args: argparse.Namespace) -> int:
    if args.authorization != "RESUME":
        raise SystemExit("exact authorization RESUME required")
    project = clean_project(args.project)
    with common.file_lock(LOCK):
        value = load(project)
        if not value or value.get("state") != "hold": raise SystemExit("project is not HOLD")
        value["state"] = "authoritative"; value["resumedAt"] = common.now_ms(); value["resumeAuthority"] = "direct-owner"
        save(value)
    print(json.dumps({"ok": True, "record": value}, indent=2)); return 0


def inspect_one(project: str) -> dict[str, Any]:
    value = load(project) or {"project": clean_project(project), "state": "missing"}
    manifest = common.read_manifest(str(value.get("coordinatorSessionId") or ""))
    now = common.now_ms(); issues: list[str] = []
    if value.get("state") != "missing" and not common.session_live(manifest): issues.append("owner-not-live")
    if value.get("state") != "hold" and value.get("leaseExpiresAt") is not None and now > int(value["leaseExpiresAt"]): issues.append("coordinator-lease-stale")
    if value.get("fallbackExpiresAt") is not None and now > int(value["fallbackExpiresAt"]): issues.append("fallback-ttl-expired")
    if manifest:
        if manifest.get("projectId") != value.get("projectId"): issues.append("native-project-binding-drift")
        if manifest.get("llmConnection") != value.get("connection") or manifest.get("model") != value.get("model"): issues.append("provider-record-drift")
        labels = set(manifest.get("labels") or [])
        for required in ("coordinators", "agent-role::coordinator"):
            if required not in labels: issues.append(f"missing-label:{required}")
        if "protocol-version::3" not in labels: issues.append("missing-label:protocol-version::3")
    return {"record": value, "issues": issues, "healthy": not issues}


def cmd_inspect(args: argparse.Namespace) -> int:
    result = inspect_one(args.project); print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["healthy"] else 2


def cmd_validate(_: argparse.Namespace) -> int:
    REGISTRY.mkdir(parents=True, exist_ok=True)
    rows = [inspect_one(path.stem) for path in sorted(REGISTRY.glob("*.json"))]
    print(json.dumps({"healthy": all(r["healthy"] for r in rows), "projects": rows}, ensure_ascii=False, indent=2))
    return 0 if all(r["healthy"] for r in rows) else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("claim"); c.add_argument("--project", required=True); c.add_argument("--session", required=True); c.add_argument("--project-id"); c.add_argument("--predecessor"); c.add_argument("--fallback-reason"); c.add_argument("--ttl", type=int, default=DEFAULT_TTL); c.set_defaults(func=cmd_claim)
    r = sub.add_parser("renew"); r.add_argument("--project", required=True); r.add_argument("--session", required=True); r.add_argument("--ttl", type=int, default=DEFAULT_TTL); r.set_defaults(func=cmd_renew)
    b = sub.add_parser("begin-transfer"); b.add_argument("--project", required=True); b.add_argument("--session", required=True); b.add_argument("--successor", required=True); b.add_argument("--reason", required=True); b.set_defaults(func=cmd_begin_transfer)
    a = sub.add_parser("accept-transfer"); a.add_argument("--project", required=True); a.add_argument("--session", required=True); a.add_argument("--expected-generation", type=int); a.add_argument("--ttl", type=int, default=DEFAULT_TTL); a.set_defaults(func=cmd_accept_transfer)
    h = sub.add_parser("hold"); h.add_argument("--project", required=True); h.add_argument("--session", required=True); h.add_argument("--reason", required=True); h.set_defaults(func=cmd_hold)
    u = sub.add_parser("resume"); u.add_argument("--project", required=True); u.add_argument("--authorization", required=True); u.set_defaults(func=cmd_resume)
    i = sub.add_parser("inspect"); i.add_argument("--project", required=True); i.set_defaults(func=cmd_inspect)
    v = sub.add_parser("validate"); v.set_defaults(func=cmd_validate)
    return p

if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(args.func(args))

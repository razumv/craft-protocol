#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Durable, narrow direct-owner receipts for an already-reviewed implementation plan.

A plan receipt proves only that the owner approved one exact plan digest for one
bounded increment/work unit and listed reversible effects.  It is deliberately not
a standing authority: it cannot imply credentials, spending, deployment, protected
merges, release publication, remote access, or irreversible data changes.  Those
effects continue to require their own exact owner gates/authorities.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore

ROOT = common.RUNTIME / "owner-plan-receipts"
LOCK = common.RUNTIME / "owner-plan-receipts.lock"
SCHEMA = 1
ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# These effects do not perform an external/irreversible action.  A plan receipt
# never substitutes for an owner gate, including product release decisions.
SAFE_EFFECTS = {"documentation", "investigation", "local-repair", "observation", "test-only"}
PROHIBITED_EFFECTS = {"deploy", "irreversible-data-change", "merge-protected-branch",
                      "physical-or-remote-access", "publish-release", "spend-money-or-entitlement",
                      "use-credential"}
CREDENTIAL_MARKERS = ("authorization:", "bearer ", "token=", "api_key=", "apikey=", "secret=", "password=", "-----begin")


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    raise SystemExit(2)


def clean_project(raw: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-")
    if not value:
        fail("invalid project slug")
    return value


def bounded(value: str, name: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        fail(f"{name} must be a bounded identifier")
    return value


def receipt_path(project: str, receipt_id: str) -> Path:
    return ROOT / project / f"{receipt_id}.json"


def load(project: str, receipt_id: str) -> dict[str, Any] | None:
    value = common.read_json(receipt_path(project, receipt_id))
    return value if isinstance(value, dict) else None


def validate_scope(scope: str) -> str:
    """Only exact increment or work-unit scopes are admissible; project-wide is not."""
    if not isinstance(scope, str) or ":" not in scope:
        fail("scope must be increment:<id> or work-unit:<id>")
    kind, value = scope.split(":", 1)
    if kind not in {"increment", "work-unit"} or not ID.fullmatch(value):
        fail("scope must be increment:<id> or work-unit:<id>")
    return f"{kind}:{value}"


def normalize_effects(values: list[str]) -> list[str]:
    effects = sorted(set(values))
    if not effects:
        fail("at least one explicit safe --effect is required")
    forbidden = sorted(set(effects) & PROHIBITED_EFFECTS)
    if forbidden:
        fail("plan receipts never authorize " + ",".join(forbidden))
    unknown = sorted(set(effects) - SAFE_EFFECTS)
    if unknown:
        fail("effect is not a reversible plan effect: " + ",".join(unknown))
    return effects


def active(receipt: dict[str, Any], now: int) -> tuple[bool, str | None]:
    if receipt.get("state") == "revoked":
        return False, "receipt-revoked"
    if receipt.get("state") != "approved":
        return False, "receipt-state-invalid"
    expiry = receipt.get("expiresAt")
    if not isinstance(expiry, int) or expiry <= now:
        return False, "receipt-expired"
    return True, None


def cmd_approve(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    receipt_id = bounded(args.receipt_id, "receipt id")
    scope = validate_scope(args.scope)
    plan = args.plan_sha256.lower()
    if not SHA256.fullmatch(plan):
        fail("plan sha256 must be lowercase SHA-256 hex")
    if args.authority != "direct-owner":
        fail("only direct-owner authority may approve a plan receipt")
    effects = normalize_effects(args.effect)
    ttl = int(args.ttl_seconds)
    if ttl < 60 or ttl > int(os.environ.get("CRAFT_PLAN_RECEIPT_MAX_TTL_SECONDS", "604800")):
        fail("ttl-seconds is outside the 60..604800 bounded approval window")
    now = common.now_ms()
    value = {"schemaVersion": SCHEMA, "receiptId": receipt_id, "project": project,
             "scope": scope, "planSha256": plan, "effects": effects,
             "authority": "direct-owner", "state": "approved", "approvedAt": now,
             "expiresAt": now + ttl * 1000, "revokedAt": None, "revokeReason": None}
    with common.file_lock(LOCK):
        prior = load(project, receipt_id)
        if prior and prior != value:
            fail("receipt id is immutable; use a new id for a changed plan/scope/effect")
        if prior:
            print(json.dumps({"ok": True, "idempotent": True, "receipt": prior}, ensure_ascii=False, indent=2)); return 0
        common.atomic_json(receipt_path(project, receipt_id), value)
    print(json.dumps({"ok": True, "receipt": value}, ensure_ascii=False, indent=2)); return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    project = clean_project(args.project); receipt_id = bounded(args.receipt_id, "receipt id")
    if args.authority != "direct-owner":
        fail("only direct-owner authority may revoke a plan receipt")
    if not args.reason.strip() or len(args.reason) > 800:
        fail("revoke reason must be bounded non-empty text")
    if any(marker in args.reason.lower() for marker in CREDENTIAL_MARKERS):
        fail("revoke reason may not contain credentials")
    with common.file_lock(LOCK):
        value = load(project, receipt_id)
        if not value:
            fail("plan receipt not found")
        if value.get("state") == "revoked":
            print(json.dumps({"ok": True, "idempotent": True, "receipt": value}, ensure_ascii=False, indent=2)); return 0
        value.update({"state": "revoked", "revokedAt": common.now_ms(), "revokeReason": args.reason})
        common.atomic_json(receipt_path(project, receipt_id), value)
    print(json.dumps({"ok": True, "receipt": value}, ensure_ascii=False, indent=2)); return 0


def cmd_check(args: argparse.Namespace) -> int:
    project = clean_project(args.project); receipt_id = bounded(args.receipt_id, "receipt id")
    scope = validate_scope(args.scope); plan = args.plan_sha256.lower()
    if not SHA256.fullmatch(plan):
        fail("plan sha256 must be lowercase SHA-256 hex")
    requested = normalize_effects(args.effect)
    receipt = load(project, receipt_id); now = common.now_ms(); reasons: list[str] = []
    if not receipt:
        reasons.append("receipt-missing")
    else:
        ok, reason = active(receipt, now)
        if not ok and reason: reasons.append(reason)
        if receipt.get("project") != project: reasons.append("receipt-project-mismatch")
        if receipt.get("scope") != scope: reasons.append("receipt-scope-mismatch")
        if receipt.get("planSha256") != plan: reasons.append("receipt-plan-digest-mismatch")
        if not set(requested).issubset(set(receipt.get("effects") or [])):
            reasons.append("receipt-effect-mismatch")
    result = {"ok": not reasons, "authorized": not reasons, "project": project, "receiptId": receipt_id,
              "scope": scope, "planSha256": plan, "effects": requested, "checkedAt": now,
              "refusals": sorted(set(reasons)), "receipt": receipt}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if not reasons else 4


def cmd_list(args: argparse.Namespace) -> int:
    project = clean_project(args.project) if args.project else None
    paths = sorted((ROOT / project).glob("*.json")) if project else sorted(ROOT.glob("*/*.json"))
    rows = [row for path in paths if isinstance((row := common.read_json(path)), dict)]
    now = common.now_ms()
    print(json.dumps({"ok": True, "receipts": rows,
                      "activeCount": sum(active(row, now)[0] for row in rows)}, ensure_ascii=False, indent=2)); return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("approve"); a.add_argument("--project", required=True); a.add_argument("--receipt-id", required=True)
    a.add_argument("--scope", required=True); a.add_argument("--plan-sha256", required=True); a.add_argument("--effect", action="append", default=[])
    a.add_argument("--ttl-seconds", default="86400"); a.add_argument("--authority", required=True); a.set_defaults(func=cmd_approve)
    r = sub.add_parser("revoke"); r.add_argument("--project", required=True); r.add_argument("--receipt-id", required=True)
    r.add_argument("--authority", required=True); r.add_argument("--reason", required=True); r.set_defaults(func=cmd_revoke)
    c = sub.add_parser("check"); c.add_argument("--project", required=True); c.add_argument("--receipt-id", required=True)
    c.add_argument("--scope", required=True); c.add_argument("--plan-sha256", required=True); c.add_argument("--effect", action="append", default=[]); c.set_defaults(func=cmd_check)
    l = sub.add_parser("list"); l.add_argument("--project"); l.set_defaults(func=cmd_list)
    return p


if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(args.func(args))

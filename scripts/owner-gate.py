#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Structured owner decisions and machine-enforced project/work-unit gates."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import re
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore
ROOT = common.RUNTIME / "owner-gates"; LOCK = common.RUNTIME / "owner-gates.lock"; SCHEMA = 1
OWNER_ONLY_CATEGORIES = (
    "explicit-hold",
    "human-product-judgment-action",
    "irreversible-destructive",
    "secrets-credentials",
    "money-entitlements",
    "legal-privacy-security-exception",
    "high-blast-radius-public-release",
    "conflicting-direct-owner-priorities",
)


def clean(raw: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw.strip()).strip("-")
    if not value: raise SystemExit("invalid identifier")
    return value


def gate_path(project: str, gate_id: str) -> Path:
    return ROOT / clean(project).lower() / f"{clean(gate_id)}.json"


def all_gates(project: str | None = None) -> list[dict[str, Any]]:
    paths = (ROOT / clean(project).lower()).glob("*.json") if project else ROOT.glob("*/*.json")
    return [v for p in sorted(paths) if (v := common.read_json(p))]


def cmd_create(args: argparse.Namespace) -> int:
    choices = [x.strip() for x in args.choices.split(",") if x.strip()]
    if not choices: raise SystemExit("at least one choice required")
    category = getattr(args, "owner_only_category", None)
    if category not in OWNER_ONLY_CATEGORIES:
        raise SystemExit("owner-only category required; reversible technical transitions and bounded corrections must continue autonomously")
    path = gate_path(args.project, args.gate)
    with common.file_lock(LOCK):
        existing = common.read_json(path)
        if existing:
            print(json.dumps({"ok": True, "idempotent": True, "gate": existing}, indent=2)); return 0
        value = {"schemaVersion": SCHEMA, "project": clean(args.project).lower(), "gateId": clean(args.gate),
                 "workUnit": args.work_unit, "question": args.question, "choices": choices,
                 "ownerOnlyCategory": category, "blockingScope": args.scope, "safeDefault": args.safe_default,
                 "state": "open", "createdAt": common.now_ms(), "resolvedAt": None,
                 "choice": None, "authority": None, "evidence": args.evidence}
        common.atomic_json(path, value)
    print(json.dumps({"ok": True, "gate": value}, ensure_ascii=False, indent=2)); return 0


def cmd_hold(args: argparse.Namespace) -> int:
    # An open project-wide hold is idempotent. A resolved RESUME is immutable
    # history, so a repeated HOLD after resume must mint a fresh gate id instead
    # of idempotently returning the resolved gate and silently not holding.
    open_hold = next((g for g in all_gates(args.project)
                      if g.get("state") == "open" and g.get("blockingScope") == "project"
                      and str(g.get("gateId") or "").startswith("project-hold")), None)
    if open_hold:
        print(json.dumps({"ok": True, "idempotent": True, "gate": open_hold}, ensure_ascii=False, indent=2))
        return 0
    gate_id = "project-hold"
    existing = common.read_json(gate_path(args.project, gate_id))
    if existing and existing.get("state") == "resolved":
        gate_id = f"project-hold-{common.now_ms()}"
    ns = argparse.Namespace(project=args.project, gate=gate_id, work_unit=None,
        question=f"Project HOLD: {args.reason}", choices="RESUME", scope="project",
        safe_default="HOLD", evidence=args.evidence, owner_only_category="explicit-hold")
    return cmd_create(ns)


def cmd_resolve(args: argparse.Namespace) -> int:
    path = gate_path(args.project, args.gate)
    with common.file_lock(LOCK):
        value = common.read_json(path)
        if not value: raise SystemExit("gate not found")
        if value.get("state") == "resolved":
            if value.get("choice") == args.choice:
                print(json.dumps({"ok": True, "idempotent": True, "gate": value}, indent=2)); return 0
            raise SystemExit("gate already resolved differently")
        if args.authority != "direct-owner": raise SystemExit("direct-owner authority required")
        if args.choice not in (value.get("choices") or []): raise SystemExit("choice is not allowed")
        if str(value.get("gateId") or "").startswith("project-hold") and args.choice != "RESUME": raise SystemExit("exact RESUME required")
        value.update({"state": "resolved", "choice": args.choice, "authority": args.authority,
                      "authorityEvidence": args.evidence, "resolvedAt": common.now_ms()})
        common.atomic_json(path, value)
    print(json.dumps({"ok": True, "gate": value}, ensure_ascii=False, indent=2)); return 0


def blockers(project: str, work_unit: str | None, action: str) -> list[dict[str, Any]]:
    blocked = []
    for gate in all_gates(project):
        if gate.get("state") != "open": continue
        scope = gate.get("blockingScope")
        if gate.get("gateId") == "project-hold" or scope == "project": blocked.append(gate); continue
        target = gate.get("workUnit")
        # An unscoped decision blocks an unscoped operation, not every unrelated
        # work unit in the project. Project-wide blocking must use scope=project.
        target_matches = (target == work_unit) if target is not None else (work_unit is None)
        if scope in {action, "work-unit"} and target_matches: blocked.append(gate)
    return blocked


def cmd_check(args: argparse.Namespace) -> int:
    rows = blockers(args.project, args.work_unit, args.action)
    result = {"allowed": not rows, "project": args.project, "workUnit": args.work_unit, "action": args.action, "blockers": rows}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if not rows else 4


def cmd_list(args: argparse.Namespace) -> int:
    rows = all_gates(args.project); print(json.dumps({"gates": rows}, ensure_ascii=False, indent=2)); return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    rows = [g for g in all_gates(args.project) if g.get("state") == "open"]
    compact = [{"project": g.get("project"), "gateId": g.get("gateId"), "workUnit": g.get("workUnit"),
                "question": g.get("question"), "choices": g.get("choices"),
                "ownerOnlyCategory": g.get("ownerOnlyCategory"),
                "safeDefault": g.get("safeDefault"), "blockingScope": g.get("blockingScope")} for g in rows]
    print(json.dumps({"openCount": len(compact), "decisions": compact}, ensure_ascii=False, indent=2)); return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create"); c.add_argument("--project", required=True); c.add_argument("--gate", required=True); c.add_argument("--work-unit"); c.add_argument("--question", required=True); c.add_argument("--choices", required=True); c.add_argument("--owner-only-category", required=True, choices=OWNER_ONLY_CATEGORIES); c.add_argument("--scope", default="work-unit", choices=["project", "work-unit", "spawn", "implement", "merge", "close"]); c.add_argument("--safe-default", default="BLOCK"); c.add_argument("--evidence"); c.set_defaults(func=cmd_create)
    h = sub.add_parser("hold"); h.add_argument("--project", required=True); h.add_argument("--reason", required=True); h.add_argument("--evidence"); h.set_defaults(func=cmd_hold)
    r = sub.add_parser("resolve"); r.add_argument("--project", required=True); r.add_argument("--gate", required=True); r.add_argument("--choice", required=True); r.add_argument("--authority", required=True); r.add_argument("--evidence", required=True); r.set_defaults(func=cmd_resolve)
    k = sub.add_parser("check"); k.add_argument("--project", required=True); k.add_argument("--work-unit"); k.add_argument("--action", required=True, choices=["spawn", "implement", "merge", "close"]); k.set_defaults(func=cmd_check)
    l = sub.add_parser("list"); l.add_argument("--project"); l.set_defaults(func=cmd_list)
    i = sub.add_parser("inbox"); i.add_argument("--project"); i.set_defaults(func=cmd_inbox)
    return p

if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(args.func(args))

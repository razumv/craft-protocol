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
# A category names the *domain*; this names the concrete thing the owner alone may
# cause. Coordinators kept escalating by domain — "money" for writing a test in a
# wallet project, "product judgment" for removing an extended attribute from their
# own scratch file — and the protocol only ever checked that some category was
# named. An effect is far harder to claim falsely than a domain, and the owner can
# see at a glance what they are actually being asked to permit.
EXTERNAL_EFFECTS = (
    "publish-release",
    "merge-protected-branch",
    "deploy",
    "spend-money-or-entitlement",
    "use-credential",
    "irreversible-data-change",
    "physical-or-remote-access",
    "legal-or-rights-decision",
    "product-direction-decision",
)
# Named to be refused: whatever a coordinator may do on its own authority.
SELF_AUTHORIZED_EFFECTS = ("none", "local-repair", "test-only", "observation",
                           "investigation", "documentation")


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
    # Coordinators write choice lists both ways. A pipe-joined list used to become a
    # single unselectable choice, so the owner could not answer their own gate at
    # all — observed twice on 2026-08-14, each time forcing the decision to be
    # recorded as free text instead of a choice. A gate exists to be answered, so
    # both separators are accepted and normalized here.
    choices, seen = [], set()
    for raw in re.split(r"[,|]", args.choices):
        choice = raw.strip()
        if not choice or choice in seen:
            continue
        seen.add(choice)
        choices.append(choice)
    if not choices: raise SystemExit("at least one choice required")
    category = getattr(args, "owner_only_category", None)
    if category not in OWNER_ONLY_CATEGORIES:
        raise SystemExit("owner-only category required; reversible technical transitions and bounded corrections must continue autonomously")
    effect = getattr(args, "external_effect", None)
    if effect in SELF_AUTHORIZED_EFFECTS:
        raise SystemExit(
            f"'{effect}' is not an external effect: do it on your own authority. "
            "A gate spends the owner's attention on something only they may cause")
    if effect not in EXTERNAL_EFFECTS:
        raise SystemExit(
            f"--external-effect must name exactly what the owner alone may cause: {list(EXTERNAL_EFFECTS)}. "
            "If none of these fits, you do not need a gate — proceed autonomously "
            "and keep the work reversible")
    project = clean(args.project).lower()
    # A direct owner answer is durable policy for this exact decision, not a
    # one-shot chat fact.  The optional key lets a coordinator name a stable
    # preference; otherwise every decision-bearing field is canonicalized.
    decision_key = getattr(args, "decision_key", None)
    identity = {"project": project, "workUnit": args.work_unit, "question": args.question.strip(),
                "choices": choices, "externalEffect": effect, "ownerOnlyCategory": category,
                "blockingScope": args.scope, "safeDefault": args.safe_default,
                "decisionKey": decision_key.strip() if isinstance(decision_key, str) and decision_key.strip() else None}
    fingerprint = __import__("hashlib").sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    path = gate_path(args.project, args.gate)
    with common.file_lock(LOCK):
        existing = common.read_json(path)
        if existing:
            print(json.dumps({"ok": True, "idempotent": True, "gate": existing}, indent=2)); return 0
        for prior in all_gates(project):
            if prior.get("decisionFingerprint") == fingerprint:
                print(json.dumps({"ok": True, "idempotent": True, "reusedDecision": True,
                                  "gate": prior}, ensure_ascii=False, indent=2)); return 0
        value = {"schemaVersion": SCHEMA, "project": project, "gateId": clean(args.gate),
                 "workUnit": args.work_unit, "question": args.question, "choices": choices,
                 "decisionKey": identity["decisionKey"], "decisionFingerprint": fingerprint,
                 "externalEffect": effect,
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
        # A resolved HOLD is history, never reusable preference policy.
        decision_key=f"hold:{common.now_ms()}", safe_default="HOLD", evidence=args.evidence, owner_only_category="explicit-hold",
        # A hold is the owner directing the project; the effect is their decision itself.
        external_effect="product-direction-decision")
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
    receipt_refusal: list[str] = []
    # When a coordinator presents a plan receipt at an execution boundary, verify
    # its durable byte/scope/effect binding here rather than trusting prompt text.
    if args.plan_receipt or args.plan_file or args.plan_scope or args.plan_effect:
        if not (args.plan_receipt and args.plan_file and args.plan_scope and args.plan_effect):
            receipt_refusal = ["plan-receipt-binding-incomplete"]
        else:
            spec = importlib.util.spec_from_file_location("owner_plan_receipt", HERE / "owner-plan-receipt.py")
            tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)  # type: ignore
            try:
                receipt_refusal = tool.reasons(clean(args.project), args.plan_receipt, args.plan_scope,
                                               args.plan_file, args.plan_effect)
            except SystemExit:
                receipt_refusal = ["plan-receipt-invalid"]
    result = {"allowed": not rows and not receipt_refusal, "project": args.project, "workUnit": args.work_unit,
              "action": args.action, "blockers": rows, "planReceiptRefusals": receipt_refusal}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["allowed"] else 4


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
    c = sub.add_parser("create"); c.add_argument("--project", required=True); c.add_argument("--gate", required=True); c.add_argument("--work-unit"); c.add_argument("--question", required=True); c.add_argument("--choices", required=True); c.add_argument("--owner-only-category", required=True, choices=OWNER_ONLY_CATEGORIES); c.add_argument("--external-effect", required=True); c.add_argument("--scope", default="work-unit", choices=["project", "work-unit", "spawn", "implement", "merge", "close"]); c.add_argument("--safe-default", default="BLOCK"); c.add_argument("--evidence"); c.add_argument("--decision-key", help="stable exact owner preference identity"); c.set_defaults(func=cmd_create)
    h = sub.add_parser("hold"); h.add_argument("--project", required=True); h.add_argument("--reason", required=True); h.add_argument("--evidence"); h.set_defaults(func=cmd_hold)
    r = sub.add_parser("resolve"); r.add_argument("--project", required=True); r.add_argument("--gate", required=True); r.add_argument("--choice", required=True); r.add_argument("--authority", required=True); r.add_argument("--evidence", required=True); r.set_defaults(func=cmd_resolve)
    k = sub.add_parser("check"); k.add_argument("--project", required=True); k.add_argument("--work-unit"); k.add_argument("--action", required=True, choices=["spawn", "implement", "merge", "close"]); k.add_argument("--plan-receipt"); k.add_argument("--plan-file"); k.add_argument("--plan-scope"); k.add_argument("--plan-effect", action="append", default=[]); k.set_defaults(func=cmd_check)
    l = sub.add_parser("list"); l.add_argument("--project"); l.set_defaults(func=cmd_list)
    i = sub.add_parser("inbox"); i.add_argument("--project"); i.set_defaults(func=cmd_inbox)
    return p

if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(args.func(args))

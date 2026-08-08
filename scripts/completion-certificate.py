#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Create and validate immutable evidence certificates for simple merge/closure."""
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
ROOT = common.RUNTIME / "completion-certificates"; SCHEMA = 1
SHA = re.compile(r"^[0-9a-f]{7,64}$")


def validate(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = ["project", "workUnit", "candidateSha", "auditedSha", "auditorSessionId", "auditVerdict", "requiredCiRunIds", "mergeSha", "mergedMainRunIds", "unresolvedGates"]
    for key in required:
        if key not in value: errors.append(f"missing:{key}")
    candidate = str(value.get("candidateSha") or ""); audited = str(value.get("auditedSha") or "")
    if not SHA.match(candidate): errors.append("invalid:candidateSha")
    if not SHA.match(audited): errors.append("invalid:auditedSha")
    if candidate and audited and candidate != audited: errors.append("audited-head-mismatch")
    if value.get("auditVerdict") != "PASS": errors.append("audit-not-pass")
    ci = [str(x) for x in value.get("requiredCiRunIds") or []]
    post = [str(x) for x in value.get("mergedMainRunIds") or []]
    if not ci: errors.append("missing:requiredCiRunIds")
    if not post: errors.append("missing:mergedMainRunIds")
    if len(ci) != len(set(ci)): errors.append("duplicate:requiredCiRunIds")
    if len(post) != len(set(post)): errors.append("duplicate:mergedMainRunIds")
    if set(ci) & set(post): errors.append("reused-ci-as-readback")
    if any(not x or x.lower() in {"success", "passed", "green"} for x in ci + post): errors.append("non-immutable-run-id")
    if value.get("requiredCiAllSuccess") is not True: errors.append("required-ci-not-green")
    if value.get("mergedMainAllSuccess") is not True: errors.append("merged-main-not-green")
    if value.get("unresolvedGates") not in ([], None): errors.append("unresolved-gates")
    if value.get("headUnchanged") is not True: errors.append("head-not-proven-unchanged")
    if value.get("closureEvidenceRequired") and not value.get("closureEvidence"): errors.append("missing:closureEvidence")
    return sorted(set(errors))


def cmd_validate(args: argparse.Namespace) -> int:
    value = common.read_json(Path(args.file).expanduser())
    if not value: print(json.dumps({"valid": False, "errors": ["invalid-json"]}, indent=2)); return 2
    errors = validate(value); print(json.dumps({"valid": not errors, "errors": errors, "certificate": value}, ensure_ascii=False, indent=2)); return 0 if not errors else 2


def cmd_scan(_: argparse.Namespace) -> int:
    rows = []
    for path in sorted(ROOT.glob("*/*.json")):
        value = common.read_json(path)
        errors = validate(value) if value else ["invalid-json"]
        rows.append({"path": str(path), "valid": not errors, "errors": errors})
    result = {"healthy": all(r["valid"] for r in rows), "count": len(rows), "certificates": rows}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["healthy"] else 2


def cmd_create(args: argparse.Namespace) -> int:
    value = {"schemaVersion": SCHEMA, "project": args.project, "workUnit": args.work_unit,
        "attempt": args.attempt, "candidateSha": args.candidate_sha, "auditedSha": args.audited_sha,
        "auditorSessionId": args.auditor, "auditVerdict": args.verdict,
        "requiredCiRunIds": args.ci_run, "requiredCiAllSuccess": args.ci_success,
        "mergeSha": args.merge_sha, "headUnchanged": args.head_unchanged,
        "mergedMainRunIds": args.readback_run, "mergedMainAllSuccess": args.readback_success,
        "unresolvedGates": args.unresolved_gate, "standingAuthority": args.standing_authority,
        "closureEvidenceRequired": args.require_closure_evidence, "closureEvidence": args.closure_evidence,
        "createdAt": common.now_ms()}
    errors = validate(value)
    if errors: print(json.dumps({"valid": False, "errors": errors, "certificate": value}, indent=2)); return 2
    path = ROOT / args.project / f"{args.work_unit}-{args.candidate_sha[:12]}.json"; common.atomic_json(path, value)
    print(json.dumps({"valid": True, "path": str(path), "certificate": value}, ensure_ascii=False, indent=2)); return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate"); v.add_argument("--file", required=True); v.set_defaults(func=cmd_validate)
    s = sub.add_parser("scan"); s.set_defaults(func=cmd_scan)
    c = sub.add_parser("create"); c.add_argument("--project", required=True); c.add_argument("--work-unit", required=True); c.add_argument("--attempt"); c.add_argument("--candidate-sha", required=True); c.add_argument("--audited-sha", required=True); c.add_argument("--auditor", required=True); c.add_argument("--verdict", default="PASS"); c.add_argument("--ci-run", action="append", default=[]); c.add_argument("--ci-success", action="store_true"); c.add_argument("--merge-sha", required=True); c.add_argument("--head-unchanged", action="store_true"); c.add_argument("--readback-run", action="append", default=[]); c.add_argument("--readback-success", action="store_true"); c.add_argument("--unresolved-gate", action="append", default=[]); c.add_argument("--standing-authority"); c.add_argument("--require-closure-evidence", action="store_true"); c.add_argument("--closure-evidence"); c.set_defaults(func=cmd_create)
    return p

if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(args.func(args))

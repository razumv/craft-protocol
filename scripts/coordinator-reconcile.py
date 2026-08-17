#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Coordinator identity drift report and authenticated canonical rename helper.

``report`` is read-only. ``apply`` never edits a session JSONL file: it sends the
supported authenticated session-rename request through an explicitly configured
Craft RPC CLI.  This keeps the runtime as the authority for session identity and
makes the action auditable by its server receipt.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore

CURRENT_VERSION = "3.4.40"
PROJECT_NAMES = {"magicmarkets": "Magicmarkets", "twenty": "Twenty", "lineage-client": "Lineage Client",
                 "lineage-server": "Lineage Server", "magnetring": "Magnetring", "gta-kiev": "GTA-Kiev", "gve": "GVE"}


def desired_name(project: str) -> str:
    return f"[{project}] Coordinator v{CURRENT_VERSION}"


def report() -> dict[str, Any]:
    rows = []
    for path in sorted((common.RUNTIME / "coordinators").glob("*.json")):
        record = common.read_json(path) or {}; project = str(record.get("project") or path.stem)
        sid = str(record.get("coordinatorSessionId") or ""); manifest = common.read_manifest(sid); issues = []; warnings = []
        desired = desired_name(project)
        if not manifest:
            issues.append("manifest-missing")
        else:
            if manifest.get("name") != desired:
                warnings.append("canonical-name-drift")
            labels = set(manifest.get("labels") or [])
            required = {"coordinators", "agent-role::coordinator", f"project::{project}"}
            issues += [f"missing-label:{label}" for label in sorted(required-labels)]
            if not any(label == "protocol-version::3" or label.startswith("protocol-version::3.") for label in labels):
                issues.append("missing-label:protocol-version::3.x")
            if manifest.get("projectId") != record.get("projectId"):
                issues.append("native-project-binding-drift")
            if record.get("state") == "authoritative" and (manifest.get("llmConnection"), manifest.get("model")) != ("chatgpt-plus", "pi/gpt-5.6-sol"):
                issues.append("provider-policy-drift")
            token = manifest.get("tokenUsage") or {}; messages = int(manifest.get("messageCount") or 0); total = int(token.get("totalTokens") or 0)
            active_children = len(record.get("activeChildren") or []); open_gates = len(record.get("unresolvedGates") or [])
            reasons = []
            if messages >= 500: reasons.append("message-threshold")
            if total >= 200000: reasons.append("token-threshold")
            if active_children >= 3: reasons.append("lane-threshold")
            if open_gates >= 8: reasons.append("gate-threshold")
            if reasons and record.get("state") == "authoritative":
                warnings.append("rotation-recommended:" + ",".join(reasons))
        rows.append({"project": project, "sessionId": sid, "desiredName": desired,
                     "currentName": manifest.get("name") if manifest else None, "issues": issues,
                     "warnings": warnings,
                     "applyRequired": bool(manifest and manifest.get("name") != desired)})
    return {"healthy": all(not row["issues"] for row in rows), "coordinators": rows,
            "note": "apply uses an authenticated runtime rename; session JSONL is never rewritten"}


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip() or any(ch in value for ch in "\r\n\x00"):
        raise SystemExit(f"{name} is required for authenticated identity apply")
    return value.strip()


def cmd_report(_: argparse.Namespace) -> int:
    value = report()
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["healthy"] else 2


def cmd_apply(args: argparse.Namespace) -> int:
    project = args.project.strip().lower()
    row = common.read_json(common.RUNTIME / "coordinators" / f"{project}.json") or {}
    session = str(row.get("coordinatorSessionId") or "")
    if not session or (args.session and args.session != session):
        raise SystemExit("authoritative coordinator session mismatch")
    manifest = common.read_manifest(session)
    if not common.session_live(manifest) or common.role_of(manifest or {}) != "coordinator":
        raise SystemExit("coordinator session is not live")
    name = desired_name(project)
    if manifest.get("name") == name:
        print(json.dumps({"ok": True, "idempotent": True, "sessionId": session, "name": name}, indent=2)); return 0
    rpc = shlex.split(required_env("CRAFT_RPC_CLI"))
    workspace_id = required_env("CRAFT_WORKSPACE_ID")
    token = required_env("CRAFT_SERVER_TOKEN")
    if not rpc or not Path(rpc[0]).is_absolute():
        raise SystemExit("CRAFT_RPC_CLI must be an absolute authenticated Craft RPC CLI")
    command = [*rpc, "--json", "session", "rename", "--workspace", workspace_id,
               "--session", session, "--name", name]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()),
           "CRAFT_SERVER_TOKEN": token}
    if os.environ.get("CRAFT_SERVER_URL"):
        env["CRAFT_SERVER_URL"] = os.environ["CRAFT_SERVER_URL"]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=60, env=env)
        receipt = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SystemExit("authenticated coordinator identity apply receipt unavailable") from exc
    if result.returncode != 0 or not isinstance(receipt, dict) or receipt.get("sessionId") != session or receipt.get("name") != name:
        raise SystemExit("authenticated coordinator identity apply receipt mismatch")
    print(json.dumps({"ok": True, "sessionId": session, "name": name, "receipt": receipt}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    # Preserve the v3.4.35 report-only invocation used by watchdogs and existing
    # operators; explicit subcommands add the authenticated mutation boundary.
    if len(sys.argv) == 1:
        return cmd_report(argparse.Namespace())
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report"); rep.set_defaults(func=cmd_report)
    apply = sub.add_parser("apply"); apply.add_argument("--project", required=True); apply.add_argument("--session"); apply.set_defaults(func=cmd_apply)
    args = parser.parse_args(); return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

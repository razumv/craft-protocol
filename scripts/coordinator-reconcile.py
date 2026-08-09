#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only coordinator metadata/provider/complexity drift reconciler."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("orch_common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore

PROJECT_NAMES = {"magicmarkets": "Magicmarkets", "twenty": "Twenty", "lineage-client": "Lineage Client",
                 "lineage-server": "Lineage Server", "magnetring": "Magnetring", "gta-kiev": "GTA-Kiev", "gve": "GVE"}


def report() -> dict:
    rows = []
    for path in sorted((common.RUNTIME / "coordinators").glob("*.json")):
        record = common.read_json(path) or {}; project = str(record.get("project") or path.stem)
        sid = str(record.get("coordinatorSessionId") or ""); m = common.read_manifest(sid); issues = []; warnings = []
        desired = f"Coordinator {PROJECT_NAMES.get(project, project)} (Codex/Sol) — v3.2.1"
        if not m: issues.append("manifest-missing")
        else:
            if m.get("name") != desired: warnings.append("canonical-name-drift")
            labels = set(m.get("labels") or [])
            required = {"coordinators", "agent-role::coordinator", f"project::{project}"}
            issues += [f"missing-label:{x}" for x in sorted(required-labels)]
            if not any(label == "protocol-version::3" or label.startswith("protocol-version::3.") for label in labels):
                issues.append("missing-label:protocol-version::3.x")
            if m.get("projectId") != record.get("projectId"): issues.append("native-project-binding-drift")
            if record.get("state") == "authoritative" and (m.get("llmConnection"), m.get("model")) != ("chatgpt-plus", "pi/gpt-5.6-sol"): issues.append("provider-policy-drift")
            token = m.get("tokenUsage") or {}; messages = int(m.get("messageCount") or 0); total = int(token.get("totalTokens") or 0)
            active_children = len(record.get("activeChildren") or []); open_gates = len(record.get("unresolvedGates") or [])
            reasons = []
            if messages >= 500: reasons.append("message-threshold")
            if total >= 200000: reasons.append("token-threshold")
            if active_children >= 3: reasons.append("lane-threshold")
            if open_gates >= 8: reasons.append("gate-threshold")
            if reasons and record.get("state") == "authoritative": warnings.append("rotation-recommended:" + ",".join(reasons))
        rows.append({"project": project, "sessionId": sid, "desiredName": desired, "currentName": m.get("name") if m else None,
                     "issues": issues, "warnings": warnings,
                     "renameRoute": f"action/rename-session/{sid}?name={desired}" if m and m.get("name") != desired else None})
    return {"healthy": all(not r["issues"] for r in rows), "coordinators": rows,
            "note": "rename routes are advisory; live session JSONL is never rewritten"}


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(); value = report(); print(json.dumps(value, ensure_ascii=False, indent=2)); return 0 if value["healthy"] else 2

if __name__ == "__main__": raise SystemExit(main())

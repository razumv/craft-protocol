#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Run deterministic lease reconciliation and post-archive harness cleanup."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def call(command: list[str]) -> dict:
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    try:
        payload = json.loads(result.stdout) if result.stdout else {}
    except Exception:
        payload = {"stdout": result.stdout}
    return {"command": command, "exitCode": result.returncode, "result": payload,
            "stderr": result.stderr[-2000:] if result.stderr else ""}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-groups", type=int, default=10)
    args = parser.parse_args()
    archive = [sys.executable, str(HERE / "post-archive-reaper.py")]
    leases = [sys.executable, str(HERE / "worker-lease.py"), "reconcile"]
    if args.apply:
        archive += ["--apply", "--all", "--max-groups", str(args.max_groups)]
        leases += ["--apply"]
    report = {
        "archiveReaper": call(archive),
        "leaseReconcile": call(leases),
        "coordinatorRegistry": call([sys.executable, str(HERE / "coordinator-registry.py"), "validate"]),
        "coordinatorMetadata": call([sys.executable, str(HERE / "coordinator-reconcile.py")]),
        "ownerGates": call([sys.executable, str(HERE / "owner-gate.py"), "inbox"]),
        "completionCertificates": call([sys.executable, str(HERE / "completion-certificate.py"), "scan"]),
    }
    # Runtime cleanup failures are fatal. Registry/metadata non-zero means drift was
    # detected and is surfaced in the report; it never authorizes destructive repair.
    fatal = any(report[key]["exitCode"] != 0 for key in ("archiveReaper", "leaseReconcile"))
    report["healthy"] = (not fatal and report["coordinatorRegistry"]["exitCode"] == 0
                         and report["coordinatorMetadata"]["exitCode"] == 0
                         and report["completionCertificates"]["exitCode"] == 0)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())

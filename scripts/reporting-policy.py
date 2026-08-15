#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Durable pull-only owner-facing reporting policy.

Craft runtimes do not expose an outbound-message interception hook.  This tool
therefore records policy and honestly performs best-effort transcript detection;
absence of a finding is never proof that no message was sent.
"""
from __future__ import annotations
import argparse, importlib.util, json
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("common",HERE/"orchestration-common.py");common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common) # type: ignore
PATH=common.RUNTIME/"reporting-policy.json"; LOCK=common.RUNTIME/"reporting-policy.lock"
def cmd_configure(a):
 with common.file_lock(LOCK):
  row={"schemaVersion":1,"mode":"pull-only","ownerFacingSessionId":a.owner_facing_session,"configuredAt":common.now_ms(),"interception":"unavailable","detection":"best-effort-session-transcript"};common.atomic_json(PATH,row)
 print(json.dumps({"ok":True,"policy":row},indent=2));return 0
def cmd_check(a):
 policy=common.read_json(PATH)
 if not policy: raise SystemExit("reporting policy not configured")
 sid=a.session; p=common.SESSIONS/sid/"session.jsonl"; hits=[]
 try:
  for n,line in enumerate(p.read_text(encoding="utf-8",errors="ignore").splitlines()[1:],2):
   if policy["ownerFacingSessionId"] in line and ("send_agent_message" in line or "sendAgentMessage" in line):hits.append(n)
 except OSError: raise SystemExit("session transcript unavailable")
 out={"compliant":not hits,"sessionId":sid,"violations":hits,"detectionCoverage":"best-effort-session-transcript","interception":"unavailable","absenceIsProof":False}
 print(json.dumps(out,indent=2));return 0 if not hits else 4
def cmd_query(_):
 policy=common.read_json(PATH)
 if not policy: raise SystemExit("reporting policy not configured")
 print(json.dumps({"policy":policy,"interception":"unavailable","detectionCoverage":"best-effort-session-transcript","absenceIsProof":False},indent=2));return 0
def parser():
 p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest="command",required=True);c=s.add_parser("configure");c.add_argument("--owner-facing-session",required=True);c.set_defaults(func=cmd_configure);k=s.add_parser("check");k.add_argument("--session",required=True);k.set_defaults(func=cmd_check);q=s.add_parser("query");q.set_defaults(func=cmd_query);return p
if __name__=="__main__":raise SystemExit(parser().parse_args().func(parser().parse_args()))

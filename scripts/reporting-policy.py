#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Durable pull-only owner-facing reporting policy and one-reply permits.

Craft exposes no outbound-message interception hook.  This tool never claims to
intercept sends: it checks the durable coordinator transcript after the fact.
A completed coordinator-to-owner send is a protocol violation unless it begins
with the one-use marker of a still-valid permit bound to that coordinator and
to one exact user request in the owner-facing session.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, secrets
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("common", HERE / "orchestration-common.py")
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)  # type: ignore
PATH = common.RUNTIME / "reporting-policy.json"; LOCK = common.RUNTIME / "reporting-policy.lock"
PERMITS = common.RUNTIME / "reporting-permits"; VIOLATIONS = common.RUNTIME / "reporting-violations"
PERMIT_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MARKER = re.compile(r"^\[\[craft-report-permit:([A-Za-z0-9_-]{16,128})\]\]")
SEND_TOOLS = {"mcp__session__send_agent_message", "send_agent_message", "sendAgentMessage"}
MAX_PERMIT_SECONDS = 3600
MAX_VIOLATIONS = 128


def policy_or_die() -> dict[str, Any]:
    row = common.read_json(PATH)
    if not common.valid_reporting_policy(row):
        raise SystemExit("reporting policy is malformed or not configured")
    return row


def nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512 or any(ch in value for ch in "\r\n\x00"):
        raise SystemExit(f"{name} is required")
    return value.strip()


def transcript(session: str) -> list[dict[str, Any]]:
    path = common.SESSIONS / session / "session.jsonl"
    try:
        rows = []
        for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("non-object transcript event")
            rows.append(value)
        if not rows:
            raise ValueError("empty transcript")
        return rows
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("session transcript unavailable or malformed") from exc


def exact_owner_request(owner: str, event_id: str, now: int) -> dict[str, Any]:
    event_id = nonempty(event_id, "owner request event identifier")
    matches = [row for row in transcript(owner) if row.get("id") == event_id]
    if len(matches) != 1:
        raise SystemExit("owner request event is missing or ambiguous")
    row = matches[0]
    timestamp = row.get("timestamp")
    if (row.get("type") != "user" or not isinstance(row.get("content"), str) or not row["content"].strip()
            or type(timestamp) is not int or timestamp <= 0 or timestamp > now):
        raise SystemExit("owner request event is not a prior exact user request")
    return row


def permit_path(permit_id: str) -> Path:
    if not PERMIT_ID.fullmatch(permit_id):
        raise ValueError("invalid permit identifier")
    return PERMITS / f"{permit_id}.json"


def event_digest(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def send_events(rows: list[dict[str, Any]], owner: str) -> list[dict[str, Any]]:
    result = []
    for row in rows[1:]:  # Session metadata cannot be a completed tool call.
        tool_input = row.get("toolInput")
        if (row.get("toolName") in SEND_TOOLS and row.get("toolStatus") == "completed"
                and isinstance(tool_input, dict) and tool_input.get("sessionId") == owner):
            result.append(row)
    return result


def violation_path(session: str) -> Path:
    return VIOLATIONS / f"{session}.json"


def durable_violations(session: str) -> list[dict[str, Any]]:
    row = common.read_json(violation_path(session)) or {}
    values = row.get("violations")
    return values if isinstance(values, list) and all(isinstance(item, dict) for item in values) else []


def audit_session(session: str) -> dict[str, Any]:
    """Check/consume permits and durably retain every detected protocol violation.

    The result deliberately says *detection*, not enforcement: outbound
    interception is unavailable and absence of a transcript finding cannot prove
    silence.  A malformed transcript is a fail-closed admission blocker.
    """
    policy = policy_or_die(); now = common.now_ms(); rows = transcript(session)
    known = {str(row.get("eventId")): row for row in durable_violations(session) if row.get("eventId")}
    fresh: list[dict[str, Any]] = []
    for event in send_events(rows, policy["ownerFacingSessionId"]):
        event_id = event.get("id")
        timestamp = event.get("timestamp")
        tool_input = event.get("toolInput")
        message = tool_input.get("message") if isinstance(tool_input, dict) else None
        reason = None; permit_id = None
        if not isinstance(event_id, str) or not event_id or type(timestamp) is not int or timestamp <= 0 or not isinstance(message, str):
            reason = "owner-report-transcript-binding-unprovable"
        else:
            marker = MARKER.match(message)
            if not marker:
                reason = "unsolicited-owner-report"
            else:
                permit_id = marker.group(1)
                permit = common.read_json(permit_path(permit_id))
                if not valid_permit(permit):
                    reason = "owner-report-permit-missing-or-malformed"
                elif permit["ownerFacingSessionId"] != policy["ownerFacingSessionId"]:
                    reason = "owner-report-permit-owner-mismatch"
                elif permit["coordinatorSessionId"] != session:
                    reason = "owner-report-permit-coordinator-mismatch"
                elif timestamp > permit["expiresAt"]:
                    reason = "owner-report-permit-expired"
                elif permit["state"] == "consumed":
                    if permit.get("consumedSendEventId") != event_id:
                        reason = "owner-report-permit-replayed"
                elif permit["state"] != "issued":
                    reason = "owner-report-permit-state-invalid"
                else:
                    permit.update({"state": "consumed", "consumedAt": now, "consumedSendEventId": event_id,
                                   "consumedSendFingerprint": event_digest(event)})
                    common.atomic_json(permit_path(permit_id), permit)
        if reason and isinstance(event_id, str) and event_id not in known:
            fresh.append({"eventId": event_id, "eventAt": timestamp if type(timestamp) is int else None,
                          "reason": reason, "permitId": permit_id, "eventFingerprint": event_digest(event),
                          "detectedAt": now})
    existing = durable_violations(session)
    if fresh:
        combined = existing + fresh
        overflow = len(combined) > MAX_VIOLATIONS
        common.atomic_json(violation_path(session), {"schemaVersion": 1, "sessionId": session,
                           "project": common.project_of(common.read_manifest(session) or {}), "violations": combined[-MAX_VIOLATIONS:],
                           "overflow": overflow, "updatedAt": now})
        existing = combined[-MAX_VIOLATIONS:]
    return {"compliant": not existing, "sessionId": session, "violations": existing,
            "detectionCoverage": "best-effort-session-transcript", "interception": "unavailable",
            "absenceIsProof": False, "admissionBlocker": "unresolved-owner-reporting-violation" if existing else None}


def valid_permit(row: dict[str, Any] | None) -> bool:
    now = common.now_ms()
    return bool(isinstance(row, dict) and row.get("schemaVersion") == 1 and PERMIT_ID.fullmatch(str(row.get("permitId") or ""))
                and isinstance(row.get("ownerFacingSessionId"), str) and row["ownerFacingSessionId"].strip()
                and isinstance(row.get("coordinatorSessionId"), str) and row["coordinatorSessionId"].strip()
                and isinstance(row.get("ownerRequestEventId"), str) and row["ownerRequestEventId"].strip()
                and type(row.get("issuedAt")) is int and type(row.get("expiresAt")) is int
                and row["issuedAt"] <= row["expiresAt"] <= row["issuedAt"] + MAX_PERMIT_SECONDS * 1000
                and row["issuedAt"] <= now and row.get("state") in {"issued", "consumed"})


def cmd_configure(a: argparse.Namespace) -> int:
    owner = nonempty(a.owner_facing_session, "owner-facing session identifier")
    with common.file_lock(LOCK):
        row = {"schemaVersion": 1, "mode": "pull-only", "ownerFacingSessionId": owner, "configuredAt": common.now_ms(),
               "interception": "unavailable", "detection": "best-effort-session-transcript"}
        if not common.valid_reporting_policy(row):
            raise SystemExit("refusing malformed reporting policy")
        common.atomic_json(PATH, row)
    print(json.dumps({"ok": True, "policy": row}, indent=2)); return 0


def cmd_issue_permit(a: argparse.Namespace) -> int:
    policy = policy_or_die(); coordinator = nonempty(a.coordinator, "coordinator session identifier")
    now = common.now_ms(); ttl = int(a.ttl_seconds)
    if not 1 <= ttl <= MAX_PERMIT_SECONDS:
        raise SystemExit(f"permit ttl must be 1..{MAX_PERMIT_SECONDS} seconds")
    manifest = common.read_manifest(coordinator)
    if not common.session_live(manifest) or common.role_of(manifest or {}) != "coordinator":
        raise SystemExit("permit coordinator is not a live coordinator session")
    request = exact_owner_request(policy["ownerFacingSessionId"], a.owner_request_event, now)
    permit_id = a.permit_id or secrets.token_urlsafe(24)
    if not PERMIT_ID.fullmatch(permit_id):
        raise SystemExit("permit identifier is invalid")
    with common.file_lock(LOCK):
        if permit_path(permit_id).exists():
            raise SystemExit("permit identifier already exists")
        row = {"schemaVersion": 1, "permitId": permit_id, "state": "issued", "ownerFacingSessionId": policy["ownerFacingSessionId"],
               "ownerRequestEventId": a.owner_request_event, "ownerRequestFingerprint": event_digest(request),
               "coordinatorSessionId": coordinator, "issuedAt": now, "expiresAt": now + ttl * 1000,
               "responseMarker": f"[[craft-report-permit:{permit_id}]]"}
        if not valid_permit(row):
            raise SystemExit("refusing malformed report permit")
        common.atomic_json(permit_path(permit_id), row)
    print(json.dumps({"ok": True, "permit": row}, indent=2)); return 0


def cmd_check(a: argparse.Namespace) -> int:
    sid = nonempty(a.session, "session identifier")
    with common.file_lock(LOCK):
        out = audit_session(sid)
    print(json.dumps(out, indent=2)); return 0 if out["compliant"] else 4


def cmd_query(_: argparse.Namespace) -> int:
    policy = policy_or_die()
    print(json.dumps({"policy": policy, "interception": "unavailable", "detectionCoverage": "best-effort-session-transcript",
                      "absenceIsProof": False, "permitProtocol": "one exact marker-bound reply per prior owner request"}, indent=2)); return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); s = p.add_subparsers(dest="command", required=True)
    c = s.add_parser("configure"); c.add_argument("--owner-facing-session", required=True); c.set_defaults(func=cmd_configure)
    i = s.add_parser("issue-permit"); i.add_argument("--coordinator", required=True); i.add_argument("--owner-request-event", required=True); i.add_argument("--ttl-seconds", type=int, required=True); i.add_argument("--permit-id"); i.set_defaults(func=cmd_issue_permit)
    k = s.add_parser("check"); k.add_argument("--session", required=True); k.set_defaults(func=cmd_check)
    q = s.add_parser("query"); q.set_defaults(func=cmd_query)
    return p


if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(args.func(args))

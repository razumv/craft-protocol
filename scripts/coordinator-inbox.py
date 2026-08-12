#!/opt/homebrew/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Durable coordinator inbox for worker/auditor reports (Protocol v3.4.0).

Worker and auditor reports are written here as durable, coalesced runtime state
instead of steering an active coordinator turn. A burst of identical or superseded
reports collapses to at most one pending item per stable
project+generation+sender+work-unit+attempt+kind key. Consumption is
generation-fenced: one exact authoritative coordinator generation claims a bounded
digest under a unique token, and acknowledgement requires that same token plus an
exact product-status revision published after the claim. Reports remain durable after
acknowledgement; unacknowledged items become available again on crash or expiry.

This tool never mutates session JSONL, worker leases, the coordinator registry, or
owner gates, and never grants merge/deploy/destructive/rotation authority.
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

RUNTIME = common.RUNTIME
INBOX = RUNTIME / "coordinator-inbox"
CLAIMS = RUNTIME / "coordinator-inbox-claims"
COORDINATORS = RUNTIME / "coordinators"
LEASES = RUNTIME / "worker-leases"
STATUS = RUNTIME / "coordinator-status"
LOCK = RUNTIME / "coordinator-inbox.lock"
SCHEMA = 1

KINDS = {"progress", "candidate", "audit-verdict", "terminal-handoff", "blocker", "observer-terminal"}
# Only these unclaimed kinds wake a coordinator; progress/candidate remain coalesced.
WAKING_KINDS = {"terminal-handoff", "audit-verdict", "blocker", "observer-terminal"}
SENDER_ROLES = {"worker", "auditor"}
FAILURE_CLASSES = {"admission-environment", "implementation-defect", "product-acceptance",
                   "integration-release", "irreversible-high-risk"}
FAILURE_KINDS = {"blocker", "terminal-handoff", "audit-verdict", "observer-terminal"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
CLAIM_TTL_DEFAULT = int(os.environ.get("CRAFT_INBOX_CLAIM_TTL_SECONDS", "900"))
DIGEST_LIMIT = int(os.environ.get("CRAFT_INBOX_DIGEST_LIMIT", "200"))
EVIDENCE_LIMIT = 16
CRED_MARKERS = ("authorization:", "bearer ", "token=", "api_key=", "apikey=", "secret=", "password=")


def fail(message: str) -> None:
    raise SystemExit(message)


def clean_project(raw: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-")
    if not value:
        fail("invalid project slug")
    return value


def valid_id(value: str, label: str) -> str:
    if not value or not SAFE_ID.fullmatch(value):
        fail(f"invalid {label}")
    return value


def valid_text(value: str, label: str, limit: int = 500) -> str:
    if value is None or not value or len(value) > limit or any(ord(ch) < 32 and ch not in "\t" for ch in value):
        fail(f"invalid {label}")
    lowered = value.lower()
    if any(marker in lowered for marker in CRED_MARKERS):
        fail(f"{label} may not contain credentials")
    return value


def workspace_local(raw: str) -> bool:
    try:
        resolved = Path(raw).expanduser().resolve()
    except Exception:
        return False
    roots = [common.WORKSPACE.resolve(), (common.HOME / ".craft-agent").resolve(), RUNTIME.resolve()]
    return any(str(resolved) == str(root) or str(resolved).startswith(str(root) + os.sep) for root in roots)


def valid_evidence(raw: list[str] | None) -> list[str]:
    items = raw or []
    if len(items) > EVIDENCE_LIMIT:
        fail(f"too many evidence references (max {EVIDENCE_LIMIT})")
    out: list[str] = []
    for item in items:
        valid_text(item, "evidence reference", limit=1024)
        looks_like_path = item.startswith(("/", "~", "./", "../")) or (os.sep in item and not re.match(r"^[a-z]+://", item))
        if looks_like_path and not workspace_local(item):
            fail("evidence path must be workspace/project-local")
        out.append(item)
    return out


def inbox_dir(project: str) -> Path:
    return INBOX / project


def claim_path(project: str) -> Path:
    return CLAIMS / f"{project}.json"


def event_key(project: str, generation: int, sender: str, work_unit: str, attempt: str, kind: str) -> str:
    raw = "\x1f".join([project, str(generation), sender, work_unit, attempt or "", kind])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def item_path(project: str, key: str) -> Path:
    return inbox_dir(project) / f"{key}.json"


def read_items(project: str) -> list[dict[str, Any]]:
    return [row for path in sorted(inbox_dir(project).glob("*.json")) if (row := common.read_json(path))]


def all_projects() -> list[str]:
    return sorted({p.name for p in INBOX.glob("*") if p.is_dir()})


def payload_fingerprint(kind: str, subject: str, evidence: list[str], extra: dict[str, Any]) -> str:
    raw = json.dumps({"kind": kind, "subject": subject, "evidence": evidence, "extra": extra},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def authoritative_coordinator(project: str, session: str, generation: int) -> dict[str, Any]:
    row = common.read_json(COORDINATORS / f"{project}.json")
    if not row:
        fail("no coordinator registry record for project")
    if row.get("state") != "authoritative":
        fail("coordinator is not authoritative")
    if row.get("coordinatorSessionId") != session:
        fail("coordinator session mismatch")
    if int(row.get("generation") or -1) != generation:
        fail("coordinator generation mismatch")
    manifest = common.read_manifest(session)
    if not common.session_live(manifest) or common.role_of(manifest) != "coordinator":
        fail("coordinator session is not live")
    return row


def registry_generation(project: str) -> tuple[str | None, int | None]:
    row = common.read_json(COORDINATORS / f"{project}.json")
    if not row or row.get("state") != "authoritative":
        return None, None
    return str(row.get("coordinatorSessionId") or ""), int(row.get("generation") or -1)


def item_available(item: dict[str, Any], now: int) -> bool:
    """An item is available for a fresh claim when it is pending, or claimed but the
    claim has expired, or a newer revision arrived after the claim (a new fact)."""
    if item.get("state") == "pending":
        return True
    if item.get("state") == "claimed":
        if int(item.get("claimExpiresAt") or 0) <= now:
            return True
        if int(item.get("revision") or 0) != int(item.get("claimedRevision") or -1):
            return True
    return False


# --------------------------------------------------------------------------- submit

def cmd_submit(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    coordinator = valid_id(args.coordinator, "coordinator")
    sender = valid_id(args.sender, "sender")
    work_unit = valid_id(args.work_unit, "work-unit")
    attempt = valid_id(args.attempt, "attempt") if args.attempt else ""
    if args.kind not in KINDS:
        fail("unsupported report kind")
    failure_class = args.failure_class
    if failure_class is not None:
        if args.kind not in FAILURE_KINDS:
            fail("failure class is allowed only for blocker, terminal, verdict, or observer reports")
        if failure_class not in FAILURE_CLASSES:
            fail("unsupported failure class")
    subject = valid_text(args.subject, "subject")
    evidence = valid_evidence(args.evidence)
    revision_hint = args.revision if args.revision is not None else None
    now = common.now_ms()

    with common.file_lock(LOCK):
        # Target coordinator + generation must match the live authoritative registry.
        reg_session, reg_generation = registry_generation(project)
        if reg_session != coordinator or reg_generation != args.generation:
            fail("target coordinator/generation does not match registry")
        manifest_c = common.read_manifest(coordinator)
        if not common.session_live(manifest_c) or common.role_of(manifest_c) != "coordinator":
            fail("target coordinator session is not live")
        # Sender must be a live worker/auditor bound to this coordinator by its lease.
        manifest_s = common.read_manifest(sender)
        if not common.session_live(manifest_s):
            fail("sender session is not live")
        if common.role_of(manifest_s) not in SENDER_ROLES:
            fail("sender must have worker/auditor role")
        lease = common.read_json(LEASES / f"{sender}.json")
        if not lease or lease.get("sessionId") != sender:
            fail("sender lease is missing")
        if lease.get("parentSessionId") != coordinator:
            fail("sender lease is not bound to this coordinator")
        if str(lease.get("workUnit") or "") != work_unit:
            fail("work-unit does not match sender lease")
        lease_attempt = str(lease.get("attempt") or "")
        if not lease_attempt:
            fail("sender lease is missing an exact attempt binding")
        if lease_attempt != attempt:
            fail("attempt does not match sender lease")

        key = event_key(project, args.generation, sender, work_unit, attempt, args.kind)
        path = item_path(project, key)
        existing = common.read_json(path)
        fingerprint = payload_fingerprint(args.kind, subject, evidence, {"failureClass": failure_class})
        waking = args.kind in WAKING_KINDS

        if existing:
            if existing.get("fingerprint") == fingerprint and revision_hint is None:
                # Identical resubmission: touch diagnostics only, never wake identity.
                existing["diagnosticsRevision"] = int(existing.get("diagnosticsRevision") or 0) + 1
                existing["lastSubmittedAt"] = now
                existing["updatedAt"] = now
                if args.apply:
                    common.atomic_json(path, existing)
                print(json.dumps({"applied": args.apply, "coalesced": True, "item": existing}, ensure_ascii=False, indent=2))
                return 0
            # Meaningful newer revision replaces the pending payload under the same key.
            revision = revision_hint if revision_hint is not None else int(existing.get("revision") or 0) + 1
            if revision <= int(existing.get("revision") or 0):
                fail("revision must be newer than the pending item")
            item = dict(existing)
            item.update({
                "subject": subject, "evidence": evidence, "failureClass": failure_class,
                "fingerprint": fingerprint, "revision": revision, "state": "pending", "waking": waking,
                "updatedAt": now, "lastSubmittedAt": now,
                "diagnosticsRevision": int(existing.get("diagnosticsRevision") or 0),
                # A new fact detaches the item from any prior claim/ack snapshot.
                "claimToken": None, "claimedByGeneration": None, "claimedAt": None,
                "claimExpiresAt": None, "claimedRevision": None, "claimedStatusRevision": None,
                "acknowledgedAt": None, "acknowledgedStatusRevision": None,
            })
        else:
            item = {
                "schemaVersion": SCHEMA, "project": project, "eventKey": key,
                "coordinatorSessionId": coordinator, "coordinatorGeneration": args.generation,
                "sender": sender, "senderRole": common.role_of(manifest_s),
                "workUnit": work_unit, "attempt": attempt or None, "kind": args.kind,
                "subject": subject, "evidence": evidence, "failureClass": failure_class,
                "fingerprint": fingerprint,
                "waking": waking, "revision": revision_hint if revision_hint is not None else 1,
                "state": "pending", "submittedAt": now, "lastSubmittedAt": now, "updatedAt": now,
                "diagnosticsRevision": 0,
                "claimToken": None, "claimedByGeneration": None, "claimedAt": None,
                "claimExpiresAt": None, "claimedRevision": None, "claimedStatusRevision": None,
                "acknowledgedAt": None, "acknowledgedStatusRevision": None,
            }
        if args.apply:
            common.atomic_json(path, item)
    print(json.dumps({"applied": args.apply, "coalesced": bool(existing), "item": item}, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- claim

def cmd_claim(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    now = common.now_ms()
    with common.file_lock(LOCK):
        authoritative_coordinator(project, args.session, args.generation)
        cpath = claim_path(project)
        claim = common.read_json(cpath) or {"schemaVersion": SCHEMA, "project": project, "claimSeq": 0}
        ttl = int(args.ttl) if args.ttl else CLAIM_TTL_DEFAULT
        # Reuse a live, non-expired claim held by the same generation so a crash-retry
        # is idempotent and newly-arrived items merge into one token rather than starving.
        reuse = (claim.get("token") and int(claim.get("expiresAt") or 0) > now
                 and claim.get("generation") == args.generation
                 and claim.get("coordinatorSessionId") == args.session)
        if reuse:
            token = claim["token"]; expires = int(claim["expiresAt"]); seq = int(claim.get("claimSeq") or 0)
            claimed_keys = list(claim.get("items") or [])
        else:
            seq = int(claim.get("claimSeq") or 0) + 1
            token = f"{project}-g{args.generation}-{now}-{seq}"
            expires = now + ttl * 1000
            claimed_keys = []
        status = common.read_json(STATUS / f"{project}.json") or {}
        status_revision_at_claim = (int(status.get("revision") or 0)
                                    if status.get("generation") == args.generation else 0)
        newly = 0
        for path in sorted(inbox_dir(project).glob("*.json")):
            if len(claimed_keys) >= DIGEST_LIMIT:
                break
            item = common.read_json(path)
            if not item or int(item.get("coordinatorGeneration") or -1) != args.generation:
                continue
            event_key_value = item.get("eventKey")
            if event_key_value in claimed_keys:
                # A meaningful newer revision detaches itself from the old claim
                # snapshot. Rebind it to the reused token so the digest is current
                # and its acknowledgement requires a fresh post-revision status.
                if item_available(item, now):
                    item.update({"state": "claimed", "claimToken": token,
                                 "claimedByGeneration": args.generation, "claimedAt": now,
                                 "claimExpiresAt": expires, "claimedRevision": int(item.get("revision") or 0),
                                 "claimedStatusRevision": status_revision_at_claim, "updatedAt": now})
                    if args.apply:
                        common.atomic_json(path, item)
                    newly += 1
                continue
            if not item_available(item, now):
                continue
            item.update({"state": "claimed", "claimToken": token, "claimedByGeneration": args.generation,
                         "claimedAt": now, "claimExpiresAt": expires, "claimedRevision": int(item.get("revision") or 0),
                         "claimedStatusRevision": status_revision_at_claim, "updatedAt": now})
            if args.apply:
                common.atomic_json(path, item)
            claimed_keys.append(item["eventKey"]); newly += 1
        claim.update({"coordinatorSessionId": args.session, "generation": args.generation, "token": token,
                      "claimedAt": claim.get("claimedAt", now) if reuse else now, "expiresAt": expires,
                      "claimSeq": seq, "items": sorted(claimed_keys)})
        if args.apply:
            common.atomic_json(cpath, claim)
        digest = [d for k in claim["items"] if (d := common.read_json(item_path(project, k)))]
    print(json.dumps({"applied": args.apply, "idempotent": reuse and newly == 0, "token": token,
                      "expiresAt": expires, "count": len(digest), "newlyClaimed": newly,
                      "waking": sum(1 for i in digest if i.get("waking")), "digest": digest},
                     ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- ack

def cmd_ack(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    now = common.now_ms()
    with common.file_lock(LOCK):
        authoritative_coordinator(project, args.session, args.generation)
        cpath = claim_path(project)
        claim = common.read_json(cpath)
        if not claim or claim.get("token") != args.token or claim.get("generation") != args.generation:
            # Idempotent: a duplicate ack after the claim is gone is not an error.
            print(json.dumps({"applied": args.apply, "idempotent": True, "acked": [], "skipped": [],
                              "reason": "no-active-claim-for-token"}, ensure_ascii=False, indent=2))
            return 0
        # Acknowledgement requires an exact status revision published after the
        # claimed item snapshot. Free-form terminal prose is never evidence.
        if args.status_revision is None:
            fail("ack requires --status-revision from a durable status published after claim")
        status = common.read_json(STATUS / f"{project}.json")
        has_status = bool(status and status.get("generation") == args.generation
                          and int(status.get("revision") or -1) == args.status_revision)
        if not has_status:
            fail("ack status revision is missing, stale, or belongs to another generation")

        requested = set(args.items) if args.items else set(claim.get("items") or [])
        acked: list[str] = []
        skipped: list[dict[str, Any]] = []
        remaining_items = list(claim.get("items") or [])
        for key in list(claim.get("items") or []):
            if key not in requested:
                continue
            path = item_path(project, key)
            item = common.read_json(path)
            if not item:
                acked.append(key)  # already consumed — idempotent
                if key in remaining_items:
                    remaining_items.remove(key)
                continue
            if item.get("claimToken") != args.token or int(item.get("revision") or 0) != int(item.get("claimedRevision") or -1):
                # A newer fact arrived after claim; leave it available rather than swallow it.
                skipped.append({"eventKey": key, "reason": "revision-changed-since-claim"})
                continue
            if args.status_revision <= int(item.get("claimedStatusRevision") or 0):
                skipped.append({"eventKey": key, "reason": "status-not-published-after-item-claim"})
                continue
            acked.append(key)
            if key in remaining_items:
                remaining_items.remove(key)
            item.update({"state": "acknowledged", "acknowledgedAt": now,
                         "acknowledgedStatusRevision": args.status_revision,
                         "claimToken": None, "claimedByGeneration": None, "claimedAt": None,
                         "claimExpiresAt": None, "claimedRevision": None, "claimedStatusRevision": None,
                         "updatedAt": now})
            if args.apply:
                common.atomic_json(path, item)
        claim["items"] = remaining_items
        claim["lastAckAt"] = now
        if not remaining_items:
            claim["token"] = None
            claim["expiresAt"] = None
        if args.apply:
            common.atomic_json(cpath, claim)
    print(json.dumps({"applied": args.apply, "acked": acked, "skipped": skipped,
                      "evidence": {"status": has_status, "statusRevision": args.status_revision}},
                     ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- release

def cmd_release(args: argparse.Namespace) -> int:
    project = clean_project(args.project)
    now = common.now_ms()
    with common.file_lock(LOCK):
        authoritative_coordinator(project, args.session, args.generation)
        cpath = claim_path(project)
        claim = common.read_json(cpath)
        if not claim or claim.get("token") != args.token:
            print(json.dumps({"applied": args.apply, "idempotent": True, "released": []}, ensure_ascii=False, indent=2))
            return 0
        released: list[str] = []
        for key in claim.get("items") or []:
            path = item_path(project, key)
            item = common.read_json(path)
            if not item or item.get("claimToken") != args.token:
                continue
            item.update({"state": "pending", "claimToken": None, "claimedByGeneration": None,
                         "claimedAt": None, "claimExpiresAt": None, "claimedRevision": None,
                         "claimedStatusRevision": None, "updatedAt": now})
            released.append(key)
            if args.apply:
                common.atomic_json(path, item)
        claim.update({"token": None, "expiresAt": None, "items": [], "releasedAt": now})
        if args.apply:
            common.atomic_json(cpath, claim)
    print(json.dumps({"applied": args.apply, "released": released}, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- reconcile

def cmd_reconcile(args: argparse.Namespace) -> int:
    now = common.now_ms()
    actions: list[dict[str, Any]] = []
    with common.file_lock(LOCK):
        for project in all_projects():
            reg_session, reg_generation = registry_generation(project)
            # Expire stale claims: unacknowledged items become available again.
            cpath = claim_path(project)
            claim = common.read_json(cpath)
            if claim and claim.get("token") and int(claim.get("expiresAt") or 0) <= now:
                for key in claim.get("items") or []:
                    path = item_path(project, key)
                    item = common.read_json(path)
                    if item and item.get("claimToken") == claim.get("token") and item.get("state") == "claimed":
                        item.update({"state": "pending", "claimToken": None, "claimExpiresAt": None,
                                     "claimedRevision": None, "claimedStatusRevision": None, "updatedAt": now})
                        if args.apply:
                            common.atomic_json(path, item)
                actions.append({"action": "expire-claim", "project": project, "token": claim.get("token")})
                claim.update({"token": None, "expiresAt": None, "items": []})
                if args.apply:
                    common.atomic_json(cpath, claim)
            # Flag items addressed to a superseded coordinator generation.
            for path in sorted(inbox_dir(project).glob("*.json")):
                item = common.read_json(path)
                if not item:
                    continue
                gen = int(item.get("coordinatorGeneration") or -1)
                orphaned = reg_generation is None or gen != reg_generation
                if orphaned and not item.get("orphaned"):
                    item["orphaned"] = True
                    item["orphanReason"] = "no-authoritative-generation" if reg_generation is None else "superseded-generation"
                    item["updatedAt"] = now
                    actions.append({"action": "orphan", "project": project, "eventKey": item.get("eventKey"),
                                    "generation": gen, "registryGeneration": reg_generation})
                    if args.apply:
                        common.atomic_json(path, item)
                elif not orphaned and item.get("orphaned"):
                    item.pop("orphaned", None); item.pop("orphanReason", None); item["updatedAt"] = now
                    if args.apply:
                        common.atomic_json(path, item)
    print(json.dumps({"applied": args.apply, "actions": actions}, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- list / report

def summarize(items: list[dict[str, Any]], now: int) -> dict[str, Any]:
    active = [i for i in items if i.get("state") != "acknowledged" and not i.get("orphaned")]
    pending = [i for i in active if item_available(i, now)]
    waking_pending = [i for i in pending if i.get("waking")]
    return {
        "total": len(active),
        "retained": len(items),
        "acknowledged": sum(1 for i in items if i.get("state") == "acknowledged"),
        "pending": len(pending),
        "claimed": sum(1 for i in active if i.get("state") == "claimed" and not item_available(i, now)),
        "wakingPending": len(waking_pending),
        "byKind": {k: sum(1 for i in active if i.get("kind") == k) for k in sorted(KINDS)},
    }


def cmd_list(args: argparse.Namespace) -> int:
    now = common.now_ms()
    projects = [clean_project(args.project)] if args.project else all_projects()
    rows: list[dict[str, Any]] = []
    for project in projects:
        for item in read_items(project):
            if args.state and item.get("state") != args.state:
                continue
            rows.append(item)
    rows.sort(key=lambda i: (str(i.get("project")), str(i.get("eventKey"))))
    print(json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    now = common.now_ms()
    projects = [clean_project(args.project)] if args.project else all_projects()
    out: list[dict[str, Any]] = []
    for project in sorted(projects):
        items = read_items(project)
        reg_session, reg_generation = registry_generation(project)
        pending_waking = [
            {"eventKey": i.get("eventKey"), "kind": i.get("kind"), "sender": i.get("sender"),
             "workUnit": i.get("workUnit"), "attempt": i.get("attempt"),
             "failureClass": i.get("failureClass"), "revision": i.get("revision"),
             "coordinatorGeneration": i.get("coordinatorGeneration")}
            for i in items
            if i.get("waking") and item_available(i, now) and not i.get("orphaned")
            and int(i.get("coordinatorGeneration") or -1) == (reg_generation if reg_generation is not None else -2)
        ]
        pending_waking.sort(key=lambda r: str(r["eventKey"]))
        wake_count = len(pending_waking)
        pending_waking = pending_waking[:DIGEST_LIMIT]
        out.append({
            "project": project,
            "coordinatorSessionId": reg_session,
            "coordinatorGeneration": reg_generation,
            "summary": summarize(items, now),
            "wakePending": pending_waking,
            "wakePendingCount": wake_count,
            "wakeTruncated": wake_count > DIGEST_LIMIT,
            "wakeReady": bool(wake_count),
        })
    print(json.dumps({"projects": out}, ensure_ascii=False, indent=2))
    return 0


def wake_observations(now: int) -> list[dict[str, Any]]:
    """Deterministic per-project wake rows for recovery-incident consumption.

    A project wakes only when it holds unclaimed waking items (terminal/verdict/
    blocker/observer-terminal) addressed to its current authoritative generation and
    its coordinator is a live coordinator session. Claimed, coalesced-progress, and
    superseded-generation items never wake."""
    out: list[dict[str, Any]] = []
    INBOX.mkdir(parents=True, exist_ok=True)
    for project in all_projects():
        reg_session, reg_generation = registry_generation(project)
        if not reg_session or reg_generation is None:
            continue
        manifest = common.read_manifest(reg_session)
        if not common.session_live(manifest) or common.role_of(manifest or {}) != "coordinator":
            continue
        items = [i for i in read_items(project)
                 if i.get("waking") and item_available(i, now) and not i.get("orphaned")
                 and int(i.get("coordinatorGeneration") or -1) == reg_generation]
        if not items:
            continue
        item_ids = sorted(str(i.get("eventKey")) for i in items)
        bounded = item_ids[:DIGEST_LIMIT]
        out.append({"project": project, "sessionId": reg_session, "generation": reg_generation,
                    "count": len(items), "itemIds": bounded, "truncated": len(items) > DIGEST_LIMIT,
                    "kinds": sorted({str(i.get("kind")) for i in items})})
    return out


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("submit")
    for name in ("project", "coordinator", "sender", "work-unit", "kind", "subject"):
        s.add_argument(f"--{name}", required=True)
    s.add_argument("--generation", type=int, required=True)
    s.add_argument("--attempt")
    s.add_argument("--failure-class", choices=sorted(FAILURE_CLASSES))
    s.add_argument("--evidence", action="append", default=[])
    s.add_argument("--revision", type=int)
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_submit)

    c = sub.add_parser("claim")
    c.add_argument("--project", required=True); c.add_argument("--session", required=True)
    c.add_argument("--generation", type=int, required=True); c.add_argument("--ttl", type=int, default=CLAIM_TTL_DEFAULT)
    c.add_argument("--apply", action="store_true"); c.set_defaults(func=cmd_claim)

    a = sub.add_parser("ack")
    a.add_argument("--project", required=True); a.add_argument("--session", required=True)
    a.add_argument("--generation", type=int, required=True); a.add_argument("--token", required=True)
    a.add_argument("--status-revision", type=int)
    a.add_argument("--items", nargs="*"); a.add_argument("--apply", action="store_true")
    a.set_defaults(func=cmd_ack)

    r = sub.add_parser("release")
    r.add_argument("--project", required=True); r.add_argument("--session", required=True)
    r.add_argument("--generation", type=int, required=True); r.add_argument("--token", required=True)
    r.add_argument("--apply", action="store_true"); r.set_defaults(func=cmd_release)

    q = sub.add_parser("reconcile")
    q.add_argument("--apply", action="store_true"); q.set_defaults(func=cmd_reconcile)

    l = sub.add_parser("list")
    l.add_argument("--project"); l.add_argument("--state"); l.set_defaults(func=cmd_list)

    z = sub.add_parser("report")
    z.add_argument("--project"); z.set_defaults(func=cmd_report)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))

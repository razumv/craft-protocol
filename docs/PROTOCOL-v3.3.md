# Coordinator Inbox and Product Observability — Protocol v3.3.0

## Why v3.3.0 exists

An autonomous coordinator running on a Pi connection could be steered mid-turn by
every worker/auditor report. A burst of a hundred child reports became a hundred
mid-stream steers, each extending the active turn, while the project's true state
lived only in the coordinator's transient context. An owner asking "what is
happening?" had to interrogate the coordinator, and a prose promise such as "I will
check CI later" had no durable observer behind it.

Protocol v3.3.0 removes these failure modes on the Protocol layer:

- worker/auditor reports become a durable, coalesced inbox instead of a live steer;
- a report burst collapses to one bounded wake turn per stable sender/work-unit/kind;
- each coordinator publishes a durable product-status snapshot with an explicit next plan;
- every future-tense wait binds to a durable observer with a deadline;
- the owner obtains one truthful all-project report sourced from runtime evidence;
- missing/stale plans and overdue commitments become deterministic incidents that wake the exact coordinator generation.

The v3.2.2 admission runtime contract is unchanged. The new coordinator wakes ride the
existing capability-v2 admission lane; the runtime capability version, delivery/inspect/
recover channels, and one-outstanding-envelope guarantees remain authoritative.

## Durable coordinator inbox

`coordinator-inbox.py` stores each report as atomic runtime state:

```text
~/.craft-agent/runtime/coordinator-inbox/<project>/<stable-event-key>.json
~/.craft-agent/runtime/coordinator-inbox.lock
~/.craft-agent/runtime/coordinator-inbox-claims/<project>.json
```

Commands: `submit`, `list`, `claim`, `ack`, `release`, `reconcile`, `report`.

Submission is fail-closed. The sender must be a live worker/auditor whose lease binds it
to the exact authoritative coordinator, project, work-unit, and attempt; the target
coordinator session and generation must match the registry; the kind must be one of
`progress`, `candidate`, `audit-verdict`, `terminal-handoff`, `blocker`,
`observer-terminal`; and evidence references must be non-secret, bounded, and
project/workspace-local where they are paths.

Coalescing key: `project + coordinator generation + sender + work-unit + attempt + kind`.
A newer meaningful revision replaces the pending payload under the same key; an identical
resubmission advances a diagnostics counter only and never changes wake identity. Because
kind is part of the key, a later `progress` report can never downgrade a pending
`terminal-handoff` or `blocker`. No report is deleted on claim.

Consumption is generation-fenced. One exact authoritative coordinator generation claims a
bounded digest under a unique token and TTL. Acknowledgement requires the same
token/generation plus the exact durable product-status revision published after that
claim. Reports remain retained after acknowledgement. A crash or claim expiry makes
unacknowledged items available again; a duplicate acknowledgement is idempotent;
ambiguous ownership or a stale generation fails closed.

## Digest wake integration

The deterministic watchdog reconciles the inbox every five minutes, before the incident
scan. Unclaimed `terminal-handoff`, `audit-verdict`, `blocker`, and `observer-terminal`
items for the current authoritative generation emit a stable `coordinator-inbox-ready`
incident. Routine `progress`/`candidate` items stay coalesced and never wake per update.
The incident carries the exact generation and stable item IDs, so the existing
capability-v2 admission lane fences the wake and keeps at most one pending envelope; a
continuously observed digest coalesces to one wake and only re-delivers when the pending
item set changes. A burst of 100 reports therefore yields zero mid-stream steers, one
pending admission message, at most one inbox item per stable key, and one bounded
coordinator turn after the current turn completes.

## Durable product-status snapshot

`coordinator-status.py` stores one declarative snapshot per project:

```text
~/.craft-agent/runtime/coordinator-status/<project>.json
~/.craft-agent/runtime/coordinator-status.lock
```

Commands: `publish`, `show`, `report --all [--format json|markdown]`, `reconcile`,
`validate`.

The coordinator declares the product objective, current phase/outcome, completed
outcomes bound to exact retained inbox evidence IDs, current focus, up to three ordered
next actions (each with a trigger, required evidence, and success/failure branch),
blocker/gate references, commitment references, and the next review time. Every
non-terminal status requires one bounded next review between 60 seconds and seven days.
Publishing fails closed on a stale generation, invented child/wait/gate/evidence
references, malformed actions or timestamps, secret-like content, unbounded fields, or a
`waiting` phase without an active observable commitment. Only an evidenced `complete`
phase may classify as `verified`; evidence during active phases remains `executing`.

Everything else is synthesized independently and cannot be caller-invented: exact
coordinator session/generation and lease health, active/terminal worker leases, external
waits and observable-job state, owner gates, inbox pressure, latest immutable
candidate/audit evidence, and a freshness/contradiction classification. Stale status
never renews authority.

## Observable coordinator commitments

`coordinator-commitment.py` binds every future-tense wait to a durable observer:

```text
~/.craft-agent/runtime/coordinator-commitments/<project>/<commitment-id>.json
~/.craft-agent/runtime/coordinator-commitments.lock
```

Commands: `register`, `resolve`, `list`, `reconcile`.

A commitment binds to an exact worker/auditor lease, an existing external-wait observer,
an owner gate, or a bounded scheduled review time, and records its project, exact
coordinator generation, subject, deadline/next-check, success action, timeout/failure
action, state, and evidence revision. Overdue, unobserved, missing-reference, and
terminal commitments emit stable incidents and wake the exact coordinator. Success and
failure require a terminal observer receipt; timeout requires the durable deadline to
pass. Commitments cannot be cancelled with prose.

## Owner-facing aggregate status

`coordinator-status.py report --all --format markdown` produces one concise report per
project — objective, phase/outcome, what is executing, worker/auditor progress, what is
awaited and how it is observed, blockers and owner gates, the next three actions, the
next automatic check, an evidence timestamp, and an explicit confidence classification of
`verified`, `executing`, `waiting-observed`, `blocked`, `stale`, or `contradictory`. This
is the source used when the owner asks "what is happening?" Coordinators send no periodic
reports to the architecture session.

## Plan-staleness and trust incidents

Watchdog/recovery detection adds `coordinator-status-missing`,
`coordinator-status-stale`, `coordinator-plan-unexecutable`,
`coordinator-commitment-overdue`, `coordinator-status-contradiction`, and
`coordinator-inbox-ready`. Staleness is evidence-aware: a long-running observed worker or
external wait remains trustworthy until its next-check/deadline; an active coordinator
with no status, no executable next action, or an unobserved prose wait is unhealthy even
if its lease is fresh; and an accurately represented owner HOLD stays healthy and never
auto-resumes.

## Installation and activation

The installer copies `coordinator-inbox.py`, `coordinator-status.py`, and
`coordinator-commitment.py` alongside the existing deterministic tools, restores the
kill switch before any payload mutation, verifies package hashes, and runs the regression
suite including `test_coordinator_v330.py`. The new runtime files are additive and
versioned; rollback never deletes inbox, status, or commitment state.

## Retained safety boundaries

Protocol v3.2.2 admission, coordinator ticks, and all preservation/split-brain
invariants remain authoritative. Inbox records never grant merge, deployment,
destructive, owner-gate, or rotation authority. Existing active v3.2.x workers are
adopted unchanged and protected by runtime queue-only fallback; no bulk message is sent
to existing children merely to announce the protocol. Owner HOLD/owner-only gates,
preservation-before-terminate, exact-head checks, unique worktrees, and direct authority
for irreversible actions are unchanged.

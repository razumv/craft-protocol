# Roadmap

This roadmap is directional, not a promise of dates. Contributions are welcome through design Issues and pull requests.

## Candidate in v3.3.0

- durable, coalesced coordinator inbox so worker/auditor report storms cannot steer or extend a coordinator turn;
- per-project product-status snapshot with independently synthesized runtime evidence and an owner-facing all-project report;
- observer-bound coordinator commitments and evidence-aware plan-staleness/trust incidents that wake the exact generation;
- reuse of the unchanged v3.2.2 capability-v2 admission lane with generation-fenced wakes and no new merge/rotation authority.

## Delivered in v3.2.2

- capability-v2 consumption receipts, queue-never-steer admission, and one guarded stuck-generation recovery CAS;
- stable incident identity and one coalesced outstanding envelope per target generation;
- direct routine coordinator ticks without making the recovery controller a liveness single point;
- fail-closed retention of all owner-gate, preservation, rotation, cleanup, and destructive-recovery boundaries.

## Delivered in v3.2.1

- exact recovery-controller harness self-registration;
- archive-first next-run reap with PID-reuse and Craft app hard refusals;
- deterministic bounded-count health invariant;
- scheduled-only automatic recovery topology that avoids terminal-event session storms.

## Delivered in v3.2.0

- strict separation between owner-facing infrastructure and autonomous project coordination;
- product-first one-outcome WIP and milestone-only reporting;
- Low/Medium/High risk-tiered focused acceptance instead of audit-on-by-default;
- one correction cycle, no audit-of-audit, and exact escalation after a second failure;
- bounded infrastructure detours and protection against silently replacing owner-requested work.

## Delivered in v3.1.1

- deterministic incident emission separated from bounded agentic recovery;
- single-controller lease, cooldown, retry budget, and kill switch;
- preservation-first terminal handoff reconciliation and heavy-lock retry semantics;
- opt-in Craft Automation templates and adversarial incident tests.

## Near term

- package and test a Linux/systemd-compatible watchdog backend;
- formal JSON Schemas for registries, gates, leases, ledgers, jobs, and certificates;
- safer gate states where HOLD is represented without hand-editing runtime JSON;
- automatic manifest generation/check tooling;
- improved coordinator rotation cooldown and fallback repatriation tests;
- portable label merge/validation helper;
- structured migration reports and owner decision views;
- additional adversarial process/cwd/preservation fixtures.

## Medium term

- pluggable process observers for macOS/Linux/Windows;
- repository/provider adapters that remain independent of core safety logic;
- signed completion certificates and evidence bundles;
- schema migration tooling;
- deterministic simulation harness for multi-project contention;
- documented API boundaries for session metadata without live JSONL mutation;
- metrics/export format for dashboards without exposing private runtime content.

## Long term

- cross-machine coordinator ownership with compare-and-swap storage;
- remote worker attestation and artifact provenance;
- policy language for standing authority and irreversible actions;
- formally modeled lifecycle/state-machine validation;
- reference integrations for additional agent runtimes.

## Out of scope

- embedding credentials or customer data;
- autonomous owner decisions;
- bypassing provider/platform safety controls;
- killing arbitrary processes;
- replacing Git, CI, issue trackers, or human review.

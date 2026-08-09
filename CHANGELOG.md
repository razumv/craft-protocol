# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [3.2.0] — 2026-08-09

### Changed

- separated the owner-facing infrastructure role from autonomous project coordination; routine coordinator updates, ACK loops, micro-polling, and central phase approvals are prohibited;
- made one primary visible/executable product outcome the default project WIP;
- replaced audit-on-by-default with risk-tiered acceptance: coordinator review for Low risk, one focused independent audit for Medium risk, and one focused audit plus immutable CI/readback/gates/certificate for High risk;
- capped failure recovery at one exact correction and one final focused re-acceptance; a second failure escalates instead of spawning attempt N+1;
- capped infrastructure detours at one safe attempt or 20 minutes before approved alternative/escalation;
- limited reports to candidate, verdict, merge/deploy/readback, or exact owner blocker milestones;
- prohibited replacing a direct owner-requested work unit with a related parent specification or coordinator interpretation.

### Delivery safeguards

- tests, audits, reports, gates, and certificates verify a finished candidate; they cannot become independent indefinite product work;
- audit-of-audit, evidence-only successor issues, and framework/ADR/measurement expansion require a concrete candidate defect;
- immutable accepted evidence is reused when SHA, inputs, environment, and claim boundary are unchanged;
- unrelated pre-existing debt remains outside the product lane;
- all v3.1.1 preservation, HOLD, unique-worktree, secret/privacy, heavy-lane, deterministic watchdog, and bounded self-healing safety invariants remain intact.

## [3.1.1] — 2026-08-09

### Added

- deterministic, idempotent recovery incident registry with CAS claims, cooldowns, retry budget, controller lease, and unresolved repeated Pi SIGTERM classification;
- bounded self-healing controller skill for coordinator wake/reconciliation, terminal slot release, heavy-lock retry, and preservation-first recovery;
- disabled-by-default scheduled and terminal-handoff Craft Automation template;
- v3.1.1 coordinator/worker incident-consumption and exit-75 semantics;
- synthetic adversarial self-healing regressions and CI enforcement of the non-agentic watchdog boundary.

### Safety

- owner gates/HOLD remain report-only for autonomous recovery;
- dirty, unpushed, shared-cwd, collision, ambiguous-PID, and preservation-unknown cleanup fails closed;
- coordinator rotation requires a verified project-bound bridge, two failed wake cycles, exact preservation snapshot, and adoption of all live children;
- live owners must heartbeat rather than reclaim; expired incident claims require deterministic reconciliation and expired controller sessions cannot self-reclaim;
- kill-switched claims/heartbeats/mutations fail closed, with controller release retained as the sole safe lock-relinquish exception;
- one coordinator session can own only one project scope globally; claims/transfers/validation reject duplicates and legacy ambiguous parents become global hard refusals;
- authoritative parent project mapping overrides conflicting child labels, emits a critical drift incident, and makes recovery-ledger membership exclusive to prevent dual-project adoption;
- single-controller lease, non-extendable 15-minute wall-time (including derived deadlines for legacy rows), deterministic wake/wake/rotation stages, action budgets, and cooldown prevent runaway loops;
- canonical `cwdCollisionSessions` from lease reconciliation emits critical hard-refusal incidents for every shared-cwd lane;
- runtime schemas remain version 1 and existing v3/v3.1 attempts remain compatible.

## [3.1.0] — 2026-08-08

### Added

- authoritative coordinator registry with two-phase transfer and split-brain refusal;
- provider fallback TTL and Codex repatriation policy;
- external recovery ledger with scope isolation;
- owner gates, exact project HOLD/RESUME, and compact decision inbox;
- completion certificate creation, validation, and global scan;
- metadata/provider/complexity reconciler;
- deterministic watchdog integration;
- worker leases, observable jobs, and global heavy-job lock;
- archive-first guarded harness reaper with Craft app and preservation checks;
- canonical coordinator/worker skills and kickoff prompt;
- portable installer, launchd template, labels, and 26 regressions.

### Safety fixes

- unique worktree per worker/auditor attempt;
- no routine `SubmitPlan` stalls;
- immutable CI run ID deduplication;
- shared native project IDs no longer cause cross-scope adoption;
- unscoped gates no longer block unrelated explicit work units;
- dirty/unpushed/shared-cwd/non-harness cleanup fails closed.

[Unreleased]: https://github.com/razumv/craft-protocol/compare/v3.2.0...HEAD
[3.2.0]: https://github.com/razumv/craft-protocol/compare/v3.1.1...v3.2.0
[3.1.1]: https://github.com/razumv/craft-protocol/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/razumv/craft-protocol/releases/tag/v3.1.0

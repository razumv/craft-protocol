# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
- authoritative parent project mapping overrides conflicting child labels, emits a critical drift incident, and makes recovery-ledger membership exclusive to prevent dual-project adoption;
- single-controller lease, deterministic wake/wake/rotation stages, action budgets, and cooldown prevent runaway loops;
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

[Unreleased]: https://github.com/razumv/craft-protocol/compare/v3.1.1...HEAD
[3.1.1]: https://github.com/razumv/craft-protocol/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/razumv/craft-protocol/releases/tag/v3.1.0

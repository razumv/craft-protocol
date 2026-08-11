# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Protocol v3.2.2 controller-liveness candidate

- replaced `prepared → notified → cooldown` with schema-v3 per-target `prepared`, `delivered`, `pending-consumption`, `consumed`, `recovering`, and `blocked` cycles; pending delivery is inspected until runtime-proven consumption and can never cooldown-rearm;
- require authenticated admission capability v2 with exact deliver/inspect/recover channels, target kind/ID/generation receipts, runtime identity pinning, queue-never-steer semantics, explicit delivery states, and a negotiated minimum recovery age; capability v1 and plain `queued` fail closed;
- coalesce meaningful incident-set changes into the same occurrence, message ID, and outstanding queue envelope; crash, busy, and discovery retries preserve the original prepared scope;
- add one guarded processing-generation recovery CAS after the configured 30-minute default deadline, then durably block a still-stuck cycle rather than silently repeating correction;
- route only exact-generation authoritative coordinator stale leases, current-child terminal handoffs, and terminal external waits directly to their coordinator; complex recovery, ambiguity, gates, HOLD, preservation, rotation, and cleanup remain recovery-controller-bound or fail closed;
- exclude volatile `agePastExpiryMs` from stale-lease evidence fingerprints while retaining it for diagnostics, and add durable `conditionRevision` so confirmed recurrence begins a distinct bounded cycle;
- add capability-v2 contract documentation, adversarial coalescing/consumption/recovery/direct-lane tests, v3.2.2 install/automation/version markers, and manifest coverage. No production activation is performed by this candidate;
- align the adapter and literal wire fixtures to corrected runtime commit `f8679cdcf47688a5a44e0fb9436ab2d6856d583f` (atop base `2889c0a051fe3859842123efb440e8a7ad63193e`): numeric durable idle generation, recovered `previousProcessingGeneration` plus strictly advanced generation, distinct admitted-envelope/final-assistant IDs in authoritative consumed-race content/completion proof without a prior-generation field, and retryable recovery-CAS `busy`;
- make kill-switch restoration the installer’s first safety mutation before any v3.2.2 payload copy, and add non-mutating `verify-runtime` exact capability/runtime/workspace verification as a mandatory runtime-first activation gate.

### Added

- durable `external-wait.py` registration for CI, auto-merge, deployment, and external checks, requiring a live parent-bound watcher lease plus active observable-job receipt; PID/PPID/start-token/process-command and job-command identities remain bound under a serialized lock/CAS lifecycle, clear requires a terminal receipt and uses a crash-recoverable `clearing` journal across wait/job files, and watchdog reconciliation emits semantic wake incidents for terminal receipts, missing observers, and deadlines.

### Fixed

- acknowledged observable-job exit-75 contention receipts no longer reopen `heavy-lock-wait` recovery indefinitely; unacknowledged contention remains actionable until durably consumed.
- pre-delivery Craft CLI/transport/JSON discovery failures now retain the exact prepared admission scope and retry with exit 75; authenticated capability/runtime identity mismatches remain hard-blocked.
- watchdog now renews an exact live authoritative coordinator lease from a completed non-intermediate assistant turn, preventing false stale incidents when model-authored heartbeat commands are omitted; HOLD/rotation/non-live sessions remain untouched.
- objectively cleared incidents reset their bounded recovery budget only after a five-minute/two-scan absence confirmation; admission pauses during confirmation, and transient observation gaps preserve the prior budget. A later confirmed recurrence starts at wake-1 instead of inheriting prior rotation/exhaustion.
- coordinators may no longer represent prose-only external waiting as autonomous progress; auto-merge requires an enablement receipt and terminal external jobs are deduplicated from generic job-exit incidents.
- current `activeChildren` terminal handoffs now trigger an immediate bounded coordinator wake instead of waiting for the one-hour coordinator lease; historical terminal backlog remains non-actionable. Recovery blockers are scoped to the exact session/work unit so unrelated preservation evidence or owner gates cannot deadlock an entire project; unknown preservation permits wake-for-verification only.
- post-archive reaping now treats every unarchived session role, including coordinators, as a live cwd owner; legacy archived workers sharing a repository-root cwd can no longer SIGTERM the live coordinator Pi subprocess.

### Changed

- standing owner policy now delegates reversible and evidence-backed technical choices, implementation architecture, environment repair, preservation-proven archive/reap, bounded correction, and executable-lane priority to authoritative coordinators; owner gates are reserved for explicit HOLD and narrow irreversible/high-blast-radius owner-only categories;
- coordinators keep candidate, gate, verdict, progress, archive, blocker, merge/deploy, completion, and owner-decision evidence project-local and send no unsolicited messages to the owner-facing architecture session; the architect responds only to explicit owner queries/instructions and discovers durable gates on demand;
- recovery admission now uses one authenticated, idempotent `automations:admissionDeliver` RPC directly to the proven persistent controller only when `automations:admissionCapabilities` exactly matches explicit runtime version/commit configuration and reports capability version 1; `system:versions` is not used for runtime identity; unsupported servers remain report-only/fail-closed;
- replaced scheduler arming/receipt reconciliation with an atomic prepared direct-delivery receipt, duplicate-safe replay, busy retry (exit 75), and hard refusal for blocked/capability errors; notified cycles now re-arm after cooldown or fingerprint change with a fresh per-cycle idempotency scope, preventing permanent recovery stalls while preserving crash deduplication; no notifier sessions or session JSONL/database mutations occur;
- require an explicit workspace ID, runtime version, runtime commit, trusted server URL, absolute executable RPC CLI, and an environment or owner-only token-file credential; bind the controller manifest root to the server workspace ID, recheck the absolute kill switch at the delivery linearization point, refuse PATH discovery and non-TLS remote WebSockets, force machine-JSON CLI responses, and expose no hidden/manual kill-switch bypass; package defaults contain no server URL, token, workspace ID, runtime version, runtime commit, or CLI path.

## [3.2.1] — 2026-08-09

### Fixed

- moved recovery admission outside the LLM lifecycle: no session exists before `recovery-admission.py` finds an actionable, permitted incident batch;
- replaced recurring recovery-controller prompts with one disabled exact-minute notifier and one reusable persistent recovery controller;
- added atomic admission receipts, incident-fingerprint cooldown, exact execution-history reconciliation, and duplicate/missed-tick fail-closed states;
- excluded owner gates, preservation-unknown lanes, cwd/project conflicts, and ambiguous ownership from automatic admission;
- retained exact harness PID/start-token/command fingerprint, caller binding, archive-first, PID-reuse, app-PID, non-harness, self-reap, live-session, and non-terminal hard refusals;
- added a report-only-by-default launchd service and adversarial tests for kill switch, no-op admission, duplicate execution, missed execution, invalid controller/config, and gate refusal.

### Operational result

- healthy report-only state uses exactly one persistent recovery controller and creates zero recurring controller sessions;
- current Craft builds remain hard-blocked from arming the notifier because no supported scheduler pre-fire idempotency claim exists;
- install/upgrade neutralizes legacy recovery prompts, restores the kill switch, and installs exactly one disabled notifier;
- prepared/armed transaction recovery, kill-switch disarm, and notifier lifecycle mechanics are tested for future supported integration;
- any duplicate/missed execution or cleanup ambiguity blocks rollout and preserves the kill switch;
- deterministic incident detection, owner-gate refusal, bounded actions, and v3.2.0 delivery-role separation remain unchanged.

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

[Unreleased]: https://github.com/razumv/craft-protocol/compare/v3.2.1...HEAD
[3.2.1]: https://github.com/razumv/craft-protocol/compare/v3.2.0...v3.2.1
[3.2.0]: https://github.com/razumv/craft-protocol/compare/v3.1.1...v3.2.0
[3.1.1]: https://github.com/razumv/craft-protocol/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/razumv/craft-protocol/releases/tag/v3.1.0

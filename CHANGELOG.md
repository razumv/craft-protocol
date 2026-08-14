# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [3.4.19] — 2026-08-14

### Neglect that only the owner used to notice becomes machine-detected

- `dead-lane-unreplaced:<work-units>` — a lane the *current* generation dispatched that is now `stalled`/`error` while no active worker exists. The coordinator dispatched it, so leaving it dead is neglect rather than inherited housekeeping debt; lanes created before this generation's acceptance stay `archivableBacklog` as before. Observed live: a focused-reaccept lane stalled for two hours while the project reported healthy;
- `exhausted-correction-without-escalation:<story-ids>` — a `failed` story with no planned next action, no open owner gate and no active lane. The correction budget is bounded, so a spent budget must reach the owner through a gate instead of stopping silently. Observed live: a project failed the same acceptance twice, emptied its plan, and waited for nobody;
- `predecessor-unarchived` becomes an incident with a `CRAFT_PREDECESSOR_ARCHIVE_GRACE_SECONDS` (900 s) grace window, wired into the wake kinds and the action matrix. Registry validate has flagged this since v3.4.8, but no lane acted on the flag, so a settled handoff kept leaving two live coordinators on one project until the owner spotted them — three times.

## [3.4.18] — 2026-08-14

### GitHub synchronisation becomes machine-observable

- product status accepts a validated `githubSync` declaration (`issue` as `owner/repo#N`, `commentRef`, optional `projectField`, `syncedStage`, past `syncedAt`) — bounded, secret-scanned and fail-closed on shape;
- any material increment stage (anything past `discovery`) without a sync, or with a sync naming an older stage, is the contradiction `github-sync-missing:<stage>` / `github-sync-stale:<synced>!=<stage>`. Updating the issue and Project board was previously only prose advice, so work could progress in Craft while GitHub — the declared source of truth — stayed silent;
- the coordinator skill and kickoff prompt state the duty: every material transition writes a short comment on the exact issue, updates its Project status field, and declares that sync. The protocol has no network or credentials, so it verifies the declaration against the coordinator's own progress rather than pretending to read GitHub.

## [3.4.17] — 2026-08-14

### Gate cards carry their title into the UI

- after creating a card the bridge issues an explicit `rename`. The runtime suppresses the created-event broadcast for sessions it did not create for a renderer, so a UI that learns about the card another way displayed its default title ("New chat") until it rehydrated — the card was correctly named on the server the whole time. An explicit rename emits an update the UI does apply.

## [3.4.16] — 2026-08-14

### Resolved gate cards stay readable

- a resolved owner-gate card is renamed to `✅ <project> · <gateId> → <choice>` and closed, then archived only after `CRAFT_BOARD_DONE_RETENTION_SECONDS` (default 3600 s). Archiving instantly made the card vanish the moment the owner answered, so the board never showed the outcome of their own decision; the retention window is tracked durably so a restart cannot lose or double-archive a card.

## [3.4.15] — 2026-08-14

### Promise commitments no longer mask idle work

- only `worker-lease` and `external-wait` commitments count as evidence that work is executing. A `scheduled-review` or `owner-gate` commitment is a promise to look later, and counting it let projects hide unassigned `ready` stories from the v3.4.14 `idle-ready-work` detector — exactly how two live projects reported healthy while dispatching nothing;
- flag `scheduled-review-churn:<n>` when at least `CRAFT_STATUS_SELF_REVIEW_CHURN_LIMIT` (2) scheduled reviews have timed out while no lane, wait or work-observer commitment exists. Observed live: a coordinator re-registered `rotation-handoff-review` r2 → r3 → r4, each timing out, instead of performing the rotation, with a `ready` audit story unassigned the whole time.

## [3.4.14] — 2026-08-14

### Unresolved-condition re-wake, dead-lane escalation, and idle-ready detection

- a consumed wake no longer closes an unchanged condition forever: while the condition persists the same cycle is re-issued up to `CRAFT_ADMISSION_MAX_REWAKES` (2) times after a `CRAFT_ADMISSION_REWAKE_QUIET_SECONDS` (1800 s) quiet period. Two coordinators sat dead for four hours overnight because they consumed one wake, died, and the incident set — being unchanged — never produced another;
- a direct lane that is durably blocked for the *current* target identity, or has spent its re-wakes, now escalates its incidents to the recovery-controller lane, which owns the wake/rotation stages that can replace a dead coordinator; a block belonging to a superseded generation still takes the v3.4.3 supersede path instead;
- `coordinator-status` flags `idle-ready-work:<story-ids>` when a declared `ready`/`executing` story has no live lane, observed wait or active commitment. An owner gate holds its own scope, so a whole increment parked behind one gate while ready stories sit unassigned is a contradiction, not health; the coordinator skill states the duty explicitly.

## [3.4.13] — 2026-08-14

### Restart-resilient controller admission

- delivery to the persistent recovery controller no longer requires an already-proven active harness receipt. A runtime restart kills every harness, and registration happens inside the turn that only a delivery can start, so the old rule self-deadlocked the controller lane until an owner sent a manual message (observed live during the 2026-08-14 runtime upgrade);
- the invariant enforced is the real one — *no other live controller*: an absent registration or a receipt whose PID is objectively gone both prove there is no competing controller, while duplicate receipts, a competing active controller, identity mismatch and unknown process lookups remain hard refusals. The deterministic controller lease continues to fence concurrent turns.

## [3.4.12] — 2026-08-14

### Gate cards finish their own identity

- `sessions:create` options echo back but only name and flag persist on the observed runtime, so a gate card now finishes itself explicitly: `setLabels` (`owner-gate`, `project::<slug>`), `setSessionStatus` (`todo`) and `session:setModel` with the configured cheap connection/model. Cards created by v3.4.11 were correctly named but unlabeled, statusless and still on the workspace default model.

## [3.4.11] — 2026-08-14

### Inert, correctly-identified gate cards

- fix v3.4.10 card creation: a gate card is now created through one `sessions:create` call carrying its owner-facing name, `owner-gate`/`project::<slug>` labels, flag and `todo` status — the previous two-step create-then-rename left every card titled "New chat" and unlabeled;
- a gate card is explicitly inert: its connection/model come from `CRAFT_BOARD_CONNECTION`/`CRAFT_BOARD_MODEL` instead of the workspace default (an accidental reply must never spend an expensive provider turn), the card has no working directory, and any turn the owner's choice message starts is cancelled immediately — the choice is data, not a prompt.

## [3.4.10] — 2026-08-14

### Owner-gate board bridge

- add `owner-gate-board.py`: a deterministic operator-side bridge that mirrors every open owner gate to one Craft session card (`🚦 <project> · <gateId>`, question and exact choices in the card notes) and resolves the gate when the owner types exactly one of its choices into the card — resolution goes through `owner-gate.py resolve` with `direct-owner` authority and the owner's message as auditable evidence; ambiguous or unrecognized replies never resolve, project HOLD cards accept only the exact `RESUME`, gates resolved elsewhere complete and archive their card on the next pass;
- the bridge is a projection, not a second decision surface: no LLM participates, the gate registry stays the single source of truth, and an optional launchd template (`config/launchd.gate-board.template.plist`) runs the sync on a 120-second interval.

## [3.4.9] — 2026-08-14

### Version-marker consistency

- skill headers, the kickoff prompt, spawn labels, and `CURRENT-DEFAULTS.md` now carry the current protocol version (a live coordinator had honestly reported an owner-visible "installed v3.4.0 vs owner-requested v3.4.7" discrepancy because patch releases only bumped the changelog/installer/readme); a regression test pins every marker to the latest released changelog version;
- admission messages carry the installed protocol version in the header — `COORDINATOR TICK v3.4.9 (admission lane v3.2.2)` — while the admission wire format and all occurrence/idempotency keys remain the stable v3.2.2 contract;
- a rotation successor renames its session to the canonical owner-facing form `Coordinator <PROJECT> (Codex/Sol) — v<version>, gen <N>` immediately after `accept-transfer`.

## [3.4.8] — 2026-08-14

### Bounded session housekeeping

- coordinators gain a standing bounded housekeeping duty: at every material transition archive up to five preservation-proven terminal children (`handoff-ready` + `pushed`/`merged`); `worker-lease.py report` exposes the machine-visible `archivableBacklog`, and letting it grow is a protocol violation (123 unarchived worker/auditor sessions had accumulated in production because cleanup was owed only "before replacement");
- a rotation now ends with the successor archiving the acknowledged predecessor; `coordinator-registry.py` validate flags `predecessor-not-archived:<sid>` (five live predecessors had accumulated after one rotation day);
- the recovery controller's startup housekeeping is distinct from its incident budget: archive up to five terminal prior recovery controller/notifier sessions per turn, with the guarded harness reap only for registered priors (29 unarchived controller sessions had accumulated under the old two-registered-only rule);
- every coordinator tick now instructs the target to re-read the installed coordinator skill when any rule is not immediately recalled: the installed protocol version is authoritative over the spawn-time copy, so fleet-wide protocol upgrades propagate on the next wake instead of the next respawn.

## [3.4.7] — 2026-08-14

### Completion-evidence continuity across rotation

- add `coordinator-inbox.py adopt`: the exact authoritative successor re-addresses the registry predecessor's durable events to the current generation with explicit provenance (`adoptedFromSession`/`adoptedFromGeneration`/`adoptedAt`); immutable `eventKey`/`revision`/`fingerprint` identity never changes, so every fail-closed Product Increment completion check keeps working unchanged and an in-flight increment completes after rotation without re-running acceptance; one registry predecessor hop only, dead-generation claim snapshots are dropped, pending items become claimable and acknowledged items stay final;
- `external-wait.py` reconcile rebinds a wait to the registry successor when its watcher is listed in the authoritative registry's `activeChildren` (explicit `adoptedFromCoordinator` provenance): without this, the v3.4.3 lease rebind made adopted watcher waits read `watcher-lease-missing-or-mismatched` and cleared readback waits lost their completion provenance;
- the coordinator skill and kickoff prompt add the post-`accept-transfer` adoption step.

## [3.4.6] — 2026-08-13

### Deaf-coordinator incident routing

- emit a deterministic `coordinator-worker-terminal-status` incident when an authoritative/rotating coordinator's session sits in a worker-terminal status (`needs-review`/`done`): such a session is deaf to queued admission wakes, so the incident always takes the recovery-controller lane (never the direct tick) and carries the standard coordinator stages — two direct-message wake attempts, then one bounded preservation-proven rotation;
- the v3.4.2 registry-validate flag remains; this release adds the missing wake path so a parked coordinator is recovered autonomously instead of waiting for a direct owner nudge (5 hours of production deafness were observed on 2026-08-13).

## [3.4.5] — 2026-08-13

### Complexity-threshold flagging and scoped reset

- `coordinator-registry.py` inspect/validate flag `coordinator-complexity-threshold` when an authoritative/rotating coordinator session passes the rotation guidance thresholds (default 500 messages / 200k tokens, tunable via `CRAFT_COORDINATOR_MAX_MESSAGES`/`CRAFT_COORDINATOR_MAX_TOKENS`): rotation pressure becomes machine-visible before context-exhaustion turn deaths instead of after (three silent mid-turn deaths were observed on a generation-6 coordinator before its rotation on 2026-08-13);
- `recovery-admission.py reset` accepts `--project` to clear one project's admission state without waiting for unrelated in-flight deliveries elsewhere.

## [3.4.4] — 2026-08-13

### Descendant process-tree liveness

- `worker-lease.py` measures observable-job progress across the job's whole descendant process tree instead of the direct child PID only: a supervisor whose nearly-idle driver delegates heavy work to a descendant (python → Blender) was repeatedly demoted to `suspect`/`stalled` despite objective CPU progress, terminating a 12-hour GTA A4 build at its ceiling without output;
- tree CPU aggregates over one `ps -axo pid,ppid,time` snapshot with cycle protection and falls back to the direct-child measurement when the snapshot is unavailable; flat tree CPU with stale evidence still classifies `stalled` exactly as before.

## [3.4.3] — 2026-08-13

### Rotation adoption rebind and generation-superseded blocks

- `worker-lease.py` now rebinds a child lease's `parentSessionId` to the coordinator registry's successor when the child is listed in an authoritative/rotating/hold registry's `activeChildren`: creation-time `parent-session::` labels permanently name the archived predecessor, which left adopted children unable to submit inbox reports and invisible to the successor's status synthesis after a rotation (observed live after the magicmarkets generation-7 rotation);
- `recovery-admission.py` supersedes a durable blocked cycle whose target identity/generation no longer matches the current batch: a dead generation's block no longer walls off the successor's wake lane until a manual reset, while same-identity blocks keep the full acknowledge/stable-degraded semantics.

## [3.4.2] — 2026-08-13

### Re-hold and coordinator role-status detection

- `owner-gate.py hold` after a resolved RESUME mints a fresh `project-hold-<ms>` gate instead of idempotently returning the immutable resolved gate and silently not holding; an already-open project-wide hold stays idempotent, and generated hold gates keep project-wide blocking and exact-RESUME semantics;
- `coordinator-registry.py` inspect/validate flag an authoritative/rotating coordinator whose session sits in a worker-terminal status (`needs-review`/`done`) as `coordinator-worker-terminal-status`: such a session is deaf to queued admission wakes until a direct owner message — role drift observed in production on 2026-08-13; intentionally parked HOLD projects are not flagged.

## [3.4.1] — 2026-08-13

### Evidence-aware admission deadline

- treat an idle pending admission whose target completed at least one full turn after the delivery timestamp as deterministic liveness-proven consumption (`consumedVia: completed-turn-liveness`) instead of hard-blocking `pending-admission-not-processing-at-deadline`; ordered message processing means the injected wake reached the session;
- this closes two production false-positive block loops observed on busy v3.4.0 coordinators: runtime consumption-attribution gaps under interleaved worker/controller messages, and stale duplicate delivery receipts returned for a recurring incident fingerprint whose original `deliveredAt` instantly exceeds the deadline;
- a genuinely deaf target — no completed turn after delivery — still hard-blocks exactly as before; recovery-CAS, stable-block acknowledgement, redelivery, and reset semantics are unchanged.

## [3.4.0] — 2026-08-13

### Product Increments and role fidelity

- change the delivery unit from issue-by-issue candidate churn to one customer-visible Product Increment with a bounded acyclic story DAG, one integrated immutable candidate, one batch CI, one merge/deploy/readback and one real-workflow demonstration;
- extend existing product status backward-compatibly with demonstrable-now, remaining outcome, ETA range, confidence, one real blocker and a validated 1–8-story increment object; customer-facing aggregate reports now lead with product meaning and place PR/SHA/CI/session/audit evidence last;
- allow up to two disjoint lightweight DAG lanes while retaining one integration candidate and the default single global heavy lane;
- move independent acceptance to the aggregate increment risk boundary: Low uses scoped story checks + coordinator integration review + batch CI, Medium/High use one focused final-candidate audit, and UI completion requires real desktop/mobile/user-workflow evidence;
- add optional durable failure classes for blocker/terminal/verdict/observer reports (`admission-environment`, `implementation-defect`, `product-acceptance`, `integration-release`, `irreversible-high-risk`) and keep recovery attempt accounting separate;
- document deliberate Geolance adoption and dispositions for all 129 DeepSeek problem statements while rejecting a new scheduler/database/queue/service, role hierarchy, vector-memory platform, semantic tool execution and stack-specific infrastructure remedies in the protocol core;
- preserve all v3.3 admission, generation-fencing, inbox claim/ack, status compatibility, observer, owner-gate, worktree and recovery behavior; no runtime server upgrade is required beyond the production-tested v3.3 capability-v2 runtime;
- harden role fidelity and autonomous continuation: lease creation refuses self-parented lanes, non-coordinator parents and live-lane worktree collisions; the inbox refuses `candidate` from auditors and `progress`/`candidate` from terminal lanes and echoes a binding `roleReminder` on every submit/claim; publishing `blocked` requires an open owner-gate reference or active observable commitment and `hold` requires an open explicit-hold gate; coordinator/worker/controller skills and the kickoff prompt gain explicit role-fidelity, re-anchoring and owner-question-discipline rules.

## [3.3.0] — 2026-08-12

### Coordinator inbox and product observability

- add `coordinator-inbox.py`: a durable, serialized, atomically-stored inbox for worker/auditor reports with validated `submit`/`list`/`claim`/`ack`/`release`/`reconcile`/`report`; submission is fail-closed on sender lease binding, exact coordinator/generation registry match, allowed kind, and non-secret workspace-local evidence;
- coalesce reports by `project + generation + sender + work-unit + attempt + kind`; a newer meaningful revision replaces the pending payload, identical resubmission advances diagnostics only, terminal/blocker items are never downgraded by later progress, and no report is deleted on claim;
- generation-fence consumption: one authoritative generation claims a bounded digest under a unique token/TTL, acknowledgement requires the same token plus a durable published status revision or exact terminal evidence, and crash/claim expiry returns unacknowledged items;
- add `coordinator-status.py`: a durable per-project product-status snapshot with `publish`/`show`/`report --all --format json|markdown`/`reconcile`/`validate`; publishing fails closed on stale generation, invented child/wait/gate references, malformed next actions, secret-like or unbounded content, or a `waiting` phase without an active observable commitment, while worker/wait/gate/inbox/evidence state is synthesized independently and classified `verified`/`executing`/`waiting-observed`/`blocked`/`stale`/`contradictory`;
- add `coordinator-commitment.py`: observer-bound commitments (`register`/`resolve`/`list`/`reconcile`) that bind every future-tense wait to an exact worker/auditor lease, external-wait observer, owner gate, or bounded scheduled review, with deadlines and durable-evidence resolution;
- extend deterministic detection with `coordinator-inbox-ready`, `coordinator-status-missing`, `coordinator-status-stale`, `coordinator-plan-unexecutable`, `coordinator-commitment-overdue`, and `coordinator-status-contradiction` incidents, each carrying the exact generation and stable fingerprint so the unchanged v3.2.2 capability-v2 admission lane fences the wake and coalesces to one envelope; the watchdog reconciles inbox/status/commitments before the incident scan;
- update coordinator/worker/self-healing skills, the kickoff prompt, `PROTOCOL-v3.3.md`, `CURRENT-DEFAULTS.md`, installer, and version markers to v3.3.0, add `test_coordinator_v330.py`, and preserve owner gates, exact-generation fencing, v3.2.x adoption, and the no-architecture-report boundary;
- classify observed `phase=blocked` as healthy only with an open owner gate or active bounded commitment; prose-only blocked plans remain stale;
- retain durable hard admission blocks without redelivery or auto-clear: the first unchanged observation records acknowledgement and remains exit 2, later identical observations report stable degraded state without poisoning unrelated cycles, and any changed fingerprint reopens exit 2;
- detect unresolved terminal coordinator completion errors beyond Pi SIGTERM and wake the exact authoritative generation; a later successful final response clears the condition, while recoverable tool errors inside a successfully completed turn cannot create wake loops;
- support a bounded 20–120 second admission RPC readback timeout for slow authenticated production links;
- production acceptance passed 188/188 tests before the final terminal-error regressions, exact manifest/install verification, queue-only busy coordinator delivery, coalesced inbox storm behavior, and stable-block canaries. The public release is suitable for external testing while the local multi-project soak continues.

### Protocol v3.2.2 controller-liveness candidate

- replaced `prepared → notified → cooldown` with schema-v3 per-target `prepared`, `delivered`, `pending-consumption`, `consumed`, `recovering`, and `blocked` cycles; pending delivery is inspected until runtime-proven consumption and can never cooldown-rearm;
- require authenticated admission capability v2 with exact deliver/inspect/recover channels, target kind/ID/generation receipts, runtime identity pinning, queue-never-steer semantics, explicit delivery states, and a negotiated minimum recovery age; capability v1 and plain `queued` fail closed;
- coalesce meaningful incident-set changes into the same occurrence, message ID, and outstanding queue envelope; crash, busy, and discovery retries preserve the original prepared scope;
- add one guarded processing-generation recovery CAS after the configured 30-minute default deadline, then durably block a still-stuck cycle rather than silently repeating correction;
- route only exact-generation authoritative coordinator stale leases, current-child terminal handoffs, and terminal external waits directly to their coordinator; complex recovery, ambiguity, gates, HOLD, preservation, rotation, and cleanup remain recovery-controller-bound or fail closed;
- exclude volatile `agePastExpiryMs` from stale-lease evidence fingerprints while retaining it for diagnostics, and add durable `conditionRevision` so confirmed recurrence begins a distinct bounded cycle;
- add capability-v2 contract documentation, adversarial coalescing/consumption/recovery/direct-lane tests, v3.2.2 install/automation/version markers, and manifest coverage. No production activation is performed by this candidate;
- align the adapter and literal wire fixtures to corrected runtime commit `db51340bfd4595178316f048b17c6cca552b2ad5` (atop base `2889c0a051fe3859842123efb440e8a7ad63193e`): numeric durable idle generation, recovered `previousProcessingGeneration` plus strictly advanced generation, distinct admitted-envelope/final-assistant IDs in authoritative consumed-race content/completion proof without a prior-generation field, and retryable recovery-CAS `busy`;
- make kill-switch restoration the installer’s first safety mutation before any v3.2.2 payload copy, and add non-mutating `verify-runtime` exact capability/runtime/workspace verification as a mandatory runtime-first activation gate;
- match the real `craft-cli automation capabilities` envelope exactly, including mandatory `available: true`; missing/false availability and extra fields remain fail-closed.

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

[Unreleased]: https://github.com/razumv/craft-protocol/compare/v3.4.19...HEAD
[3.4.19]: https://github.com/razumv/craft-protocol/compare/v3.4.18...v3.4.19
[3.4.18]: https://github.com/razumv/craft-protocol/compare/v3.4.17...v3.4.18
[3.4.17]: https://github.com/razumv/craft-protocol/compare/v3.4.16...v3.4.17
[3.4.16]: https://github.com/razumv/craft-protocol/compare/v3.4.15...v3.4.16
[3.4.15]: https://github.com/razumv/craft-protocol/compare/v3.4.14...v3.4.15
[3.4.14]: https://github.com/razumv/craft-protocol/compare/v3.4.13...v3.4.14
[3.4.13]: https://github.com/razumv/craft-protocol/compare/v3.4.12...v3.4.13
[3.4.12]: https://github.com/razumv/craft-protocol/compare/v3.4.11...v3.4.12
[3.4.11]: https://github.com/razumv/craft-protocol/compare/v3.4.10...v3.4.11
[3.4.10]: https://github.com/razumv/craft-protocol/compare/v3.4.9...v3.4.10
[3.4.9]: https://github.com/razumv/craft-protocol/compare/v3.4.8...v3.4.9
[3.4.8]: https://github.com/razumv/craft-protocol/compare/v3.4.7...v3.4.8
[3.4.7]: https://github.com/razumv/craft-protocol/compare/v3.4.6...v3.4.7
[3.4.6]: https://github.com/razumv/craft-protocol/compare/v3.4.5...v3.4.6
[3.4.5]: https://github.com/razumv/craft-protocol/compare/v3.4.4...v3.4.5
[3.4.4]: https://github.com/razumv/craft-protocol/compare/v3.4.3...v3.4.4
[3.4.3]: https://github.com/razumv/craft-protocol/compare/v3.4.2...v3.4.3
[3.4.2]: https://github.com/razumv/craft-protocol/compare/v3.4.1...v3.4.2
[3.4.1]: https://github.com/razumv/craft-protocol/compare/v3.4.0...v3.4.1
[3.4.0]: https://github.com/razumv/craft-protocol/compare/v3.3.0...v3.4.0
[3.3.0]: https://github.com/razumv/craft-protocol/compare/v3.2.0...v3.3.0
[3.2.1]: https://github.com/razumv/craft-protocol/compare/v3.2.0...v3.2.1
[3.2.0]: https://github.com/razumv/craft-protocol/compare/v3.1.1...v3.2.0
[3.1.1]: https://github.com/razumv/craft-protocol/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/razumv/craft-protocol/releases/tag/v3.1.0

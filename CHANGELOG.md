# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Release closure correction

- Replaced the impossible tagged-tree fleet-adoption self-reference with one authenticated GitHub Release-body receipt. Final non-draft Latest closure now binds schema/version/tag/the exact peeled tag commit, adopted state, owner-facing orchestrator session, adoption timestamp, and a canonical unique project/coordinator roster without changing the tagged commit after rollout.

## [3.4.38] — 2026-08-17

### Clean rotation hotfix

- Transfer adoption now accepts an existing, unique worker or auditor CWD written in protocol-trusted `~/...` syntax after canonical expansion. Relative non-tilde, missing, dangling, disagreeing, parent-shared, and colliding paths remain refused.
- A successful coordinator renewal refreshes the configured pull-only reporting mode, policy revision, and fingerprint while preserving generation, state, gates, children, and reporting-only quarantine recovery.
- Coordinator archival hygiene is now mandatory immediately after transfer adoption and at material transitions: archive first, then guarded-reap preservation-proven predecessors, terminal/unused children, and stale coordinators in bounded batches until machine-visible backlog and debt violations clear. Dirty, shared-CWD, unknown-holder work and history remain protected; cleanup is never reported to Fleet.

## [3.4.37] — 2026-08-16

### Operational stability and release closure

- Rotation metrics now bind to the current coordinator session with a post-claim/transfer grace window and lower hysteresis clear thresholds. Concrete request-buffer/context errors bypass both protections, so fresh-session noise is suppressed without hiding a real context failure.
- Complete increments consume exact immutable event bindings plus matching `candidateSha`, `verdict:PASS`, merge and readback markers. A completed project whose only remaining issue is a live predecessor is classified `healthy-with-maintenance-debt`; archive debt stays visible without being misreported as execution/context failure.
- Added `owner-plan-receipt.py`: direct-owner, digest-bound, increment/work-unit-scoped approvals for explicit reversible plan effects only, with expiry and immediate durable revocation. It refuses inference of credentials, spend, deploy, protected merge, release, remote access, or irreversible effects.
- Added `release-closure.py verify`: a read-only closure gate requiring local main/tag/manifest/installer-dry-run facts, captured GitHub API Release evidence marked Latest, and exact version adoption across packaged entry points. It never tags, pushes, installs, or contacts GitHub.
- Transfer acceptance now discovers live and `needs-review` predecessor workers/auditors from manifests where leases are absent/stale, records adoption, and lane admission refuses duplicate same-role/work-unit dispatch. The predecessor archive obligation remains durable maintenance debt.

## [3.4.36] — 2026-08-16

### Product delivery pressure and safe coordinator rollout

- Stories now declare a bounded `deliverableClass` (`product`, `contract`, `acceptance`, or `housekeeping`); omitted legacy values normalize to `product`, while a current v3.4.36 publish requires explicit classes and a product-bearing increment unless a resolved direct-owner `planning-only:` authority is named. Ready product work emits delivery-pressure metrics and fails closed when no-code lanes occupy capacity or a contract/acceptance PASS does not dispatch the ready product lane in the same cycle.
- Any active lane absent from the current increment, or outside `delivery.repoPath` / `delivery.worktreeRoots`, is active non-product work and cannot mask an idle ready product story. Contract-only correction remains bounded to one no-code correction. Reuse binds candidate SHA plus environment/input fingerprint to an observed inbox event or certificate rather than trusting schema strings.
- A resolved protected-merge gate must issue its merge and an observed readback in the same status cycle. Merge-authorized completion verifies the exact merge SHA against `origin/<targetBranch>`, exact resolved gate/standing-receipt work unit, and `merged-main:<sha>` readback evidence. Explicit HOLD still permits truthful status publication/reconciliation, but blocks new implementation jobs and standing-authority merges.
- Coordinator CWD rollout accepts trusted `~/` manifest syntax and stores its canonical absolute path; all other relative paths remain refused. v3.4.35 strict lane admission remains compatible while v3.4.36 labels receive the same checks.
- Recovery emits delivery-pressure and direct-owner/complexity-rotation incidents; rotation pressure is routed ahead of routine no-rotation ticks. Housekeeping remains quota-bounded. `coordinator-reconcile.py apply` replaces the advisory rename route with an authenticated runtime rename receipt and never rewrites session JSONL.

## [3.4.35] — 2026-08-15

### Admission, transfer identity, and durable owner memory

- Added `lane-admission.py`: workers and auditors reserve immutable lane identity before spawn and confirm the returned live manifest before lease creation; legacy lease records remain compatible.
- Coordinator transfers now bind and recheck successor manifest identity, including project binding, role, workspace, connection, and model.
- Owner gates canonicalize exact decision/preference identities (`--decision-key`) and reuse matching open or resolved records rather than creating duplicate gates.
- Added a durable pull-only owner-facing reporting policy with explicitly best-effort transcript detection where runtime interception is unavailable; corrected the conflicting coordinator name instruction.


## [3.4.34] — 2026-08-15

### Dead lanes finally have a way out

- `scan-reapable-workers.py` treats a lane's **lease** as the authority on whether it is over: `stalled` and `error` qualify alongside a terminal session status. A lane that dies never reaches `needs-review` — it keeps whatever status the board last set — so the reaper skipped it as `status=todo` indefinitely. Preservation still decides whether it may go: dirty or unpushed worktrees are skipped exactly as before;
- `increment-board.py` no longer marks `stalled`/`error` lanes as `todo`. The board was actively making dead lanes look alive, which is what kept them out of the reaper's reach;
- the scanner now honours `CRAFT_WORKSPACE`, `CRAFT_SESSIONS`, `CRAFT_PID_DIR`, `CRAFT_RUNTIME` and `CRAFT_REAP_IDLE_MINUTES` like every other protocol script. It was the only one with hardcoded paths, which is also why it had no test coverage.

Measured live: 23 dead lanes aged 70–110 hours, every one of them an unarchived session; 14 had clean worktrees. Reapable candidates went from 2 to 11 the moment the lease was consulted, with the 9 dirty ones still correctly refused.

## [3.4.33] — 2026-08-15

### A plan that promises a worker must have work to dispatch

- `worker-action-without-dispatchable-story:<indexes>` — a `nextActions` entry executed by a `worker` or `auditor` while no story is `ready`/`executing`, no lane runs and no result waits to be collected. The work exists in the coordinator's prose and not in the increment DAG, so nobody can pick it up while every other check reads healthy;
- actions carry `storyRef`, and one naming a story the increment does not contain is `plan-action-story-not-observed`.

Measured live across five unattended hours: four of seven projects sat in exactly this state — roughly 1800 coordinator events between them, three lanes dispatched, one completion certificate, and nothing at all reaching a protected branch. Their plans read "a fresh worker produces one clean candidate" and "one focused independent auditor accepts the candidate" while their increments held only `accepted`, `integrated`, `failed` and `blocked` stories, so `idle-ready-work` stayed correctly silent.

## [3.4.32] — 2026-08-15

### One coordinator per project, named so the owner can find it

- a coordinator session must be named exactly `[<project>] Coordinator v<protocol-version>`; `coordinator-registry.py inspect/validate` reports `coordinator-name-nonconforming` and `coordinator-name-project-mismatch`. Successors are spawned by their predecessor, which named them whatever it liked — "l2 client", "Coordinator Handoff", "Coordinator Lifecycle Protocol" — until the owner's coordinator list no longer said which project or protocol version a row belonged to;
- `stale-coordinator-session` flags **every** live coordinator-labelled session that is not its project's current one, not merely the immediate predecessor the registry still remembers. A chain of rotations (gen 8 → 9 → 10) drops earlier generations out of the registry's view and they stay open forever: five such sessions were live at once, three of them for a single project.

## [3.4.31] — 2026-08-15

### A gate names the effect, not the domain

- `owner-gate.py create` requires `--external-effect` from a closed list of things only the owner may cause (`publish-release`, `merge-protected-branch`, `deploy`, `spend-money-or-entitlement`, `use-credential`, `irreversible-data-change`, `physical-or-remote-access`, `legal-or-rights-decision`, `product-direction-decision`) and refuses the self-authorized ones (`none`, `local-repair`, `test-only`, `observation`, `investigation`, `documentation`) with the remedy in the message: do it on your own authority. An unrecognised value is refused with *"if none of these fits, you do not need a gate"*. The effect is stored on the gate, so the owner sees what they are actually permitting;
- observed live: three gates open simultaneously, none of which needed the owner — removing a stray `com.apple.provenance` xattr from an empty scratch file (declared `human-product-judgment-action`), adding two missing mounted-screen test cases in a wallet project (declared `money-entitlements`, with the evidence itself recording no merge, deploy, mutation or wager), and restarting a passive 25-minute observation window. Each named a category; none had an effect.

### Correction attempts are bounded by information, not by a counter

- a further self-granted extension is allowed whenever it names a `rootCauseRef` no previous attempt named; `correction-budget-extension-reused` now fires on a repeated cause rather than on the second attempt. What the owner needs protecting from is thrash, not progress — the fourth test-only correction above was escalated purely because a counter had run out, while each attempt had named a different proven omission.

## [3.4.30] — 2026-08-14

### Authorizing a merge no longer requires proof that only the merge can produce

- `standing-authority.py` now validates a **pre-merge** certificate: independent `PASS` on the exact candidate, green required CI, a head proven unchanged, no unresolved gates. It no longer demands `mergeSha`, `mergedMainRunIds` or `mergedMainAllSuccess` — evidence that exists only *after* the merge being authorized. `completion-certificate.py` gains `pre_merge_errors()` for exactly this judgement while `validate()` keeps its full post-merge contract;
- the standing-merge receipt records `readbackOwed`, and the coordinator skill states the split: merging is not finishing, and the completion certificate written after the merge still carries the readback.

Observed live: a project whose protected branch takes true merge commits reported the cycle precisely — "the certificate validator requires merged-master readback evidence before standing authority can authorize the merge that creates that evidence" — and stopped rather than working around it. Projects on squash merges had been hiding the same cycle by merging first and writing the receipt afterwards, which inverted authorization into a post-hoc stamp.

## [3.4.29] — 2026-08-14

### A gate exists to be answered

- `owner-gate.py create` accepts both `,` and `|` between choices and normalizes them, dropping duplicates. A pipe-joined list previously became one unselectable choice: the gate reached the owner looking normal, every meaningful answer was refused as "choice is not allowed", and the decision had to be recorded as free text against a literal nobody meant. Observed twice within an hour on 2026-08-14, on a money-entitlements gate and a credentials gate — the two categories where a lost decision costs the most.

## [3.4.28] — 2026-08-14

### An install that stops early no longer looks like a deliberate pause

- `install.sh` stamps the kill switch it arms with `armed-by=install.sh`, the timestamp, and `rearm-expected=1`. The switch is still armed *before* any payload mutation and removed only after the payload lands and its tests pass — but now the file says which of those two situations you are looking at;
- `drain`/`report` expose `killSwitch.armedBy` and `killSwitch.stranded` (an installer-armed switch still present after `CRAFT_KILL_SWITCH_STRANDED_SECONDS` (600 s)). Controller silence is excused by a *deliberate* pause only: behind a stranded switch, silence is reported as silence;
- the controller skill states it: a stranded switch is the cause of an outage to report, not a decision to respect.

Observed live: the v3.4.27 install stopped before its re-arm step, left the switch behind, and the recovery lane stayed dead for 75 minutes with six finished workers uncollected — while every check faithfully reported an intentional pause, because rest and outage were the same observation.

## [3.4.27] — 2026-08-14

### An idle executor goes before the bookkeeping

- `drain` splits the old pipeline rank in two: an executor idle right now (an uncollected finished worker, a coordinator that cannot own or publish, an unobserved wait) ranks above a bookkeeping mismatch (status missing/stale/contradictory, plan unexecutable, inbox ready, commitment overdue). Measured live: with a three-action turn budget, contradictions and overdue commitments consumed every turn while two finished workers waited 25 and 30 minutes to be collected. Lane recovery and housekeeping follow, housekeeping still under its quota.

### Research without its sources is not evidence

- a story declares `requiredSources`, and a live lane that cannot reach them is `lane-missing-required-sources`. Sources attach to a session, so a worker spawned without them returns plausible prose that no downstream check can tell from evidence. Observed live: a research coordinator ran for an hour with an empty source list while its own contract named five, and nothing anywhere said so.

### A saturated host is not a lost channel

- `drain` reports `transport.host` (1-minute load, cores, `saturated` past `CRAFT_HOST_SATURATION_RATIO` (2.5) per core) and withholds `lost` while the host is saturated, reporting `hostStarved` instead. Measured live: eight parallel builds from unrelated work drove the load to 59 on a 10-core host and every RPC timed out at 10 s while the channel was perfectly fine. Starvation is fixed by reducing load, never by recovery actions against coordinators that are answering.

## [3.4.26] — 2026-08-14

### A lost transport stops looking like a lazy fleet

- the admission tick records whether the channel answered: `runtime/self-healing/transport.json` keeps `lastSuccessAt`, `consecutiveFailures` and the last failure reason, incremented on every transient/discovery retry and cleared as soon as a tick completes without one;
- `recovery-incident.py drain` reports `transport` — including `lost`, set when failures stand with no success inside `CRAFT_TRANSPORT_LOST_SECONDS` (900). One failure after a fresh success is a hiccup, not a loss;
- the controller skill states the consequence: when the transport is lost, wakes cannot arrive and results cannot be collected however healthy the local records look, so the turn reports the cause instead of spending its budget on deliveries that cannot land.

Observed live on 2026-08-14: Tailscale logged out at ~19:02, the Craft server's listening address vanished with the tun interface, and for an hour the only visible symptom was a fleet that appeared to have stopped caring — coordinators silent, the ledger growing, finished workers uncollected. Nothing here restores a channel the host no longer has; naming the condition is the entire fix.

## [3.4.25] — 2026-08-14

### Being unable to look is not evidence of danger

- a failed *observation* now defers the admission cycle instead of condemning it. `ProbeUnavailable` (a controller-harness probe that times out or returns garbage) records `phase: probe-deferred` with a failure count and is retried next tick; only `CRAFT_ADMISSION_MAX_PROBE_FAILURES` (3) consecutive failures produce a durable block, under a reason that names the unavailable probe. Proven-unsafe conditions — ambiguous controller identity, runtime mismatch, foreign workspace — still block immediately and durably, exactly as before. Observed live: one failed probe hard-blocked the wake lane at 18:16, the controller went 56 minutes without a turn, and the open ledger grew to 74 while every project looked merely busy;
- a successful cycle clears the failure count, so a transient hiccup leaves no residue.

### Perception no longer depends on an agent

- the admission cron runs `recovery-incident.py detect --apply` every tick. Until now only the controller session ran detection, so a quiet controller froze the ledger too: admission kept deciding from a stale view while conditions piled up unseen. Detection is deterministic and needs no agent, so it must not depend on one;
- `drain` reports `controller` — when the lane last took a turn, how much delivery-blocking work waits, and whether it is `silent` (blocking work present, no turn within `CRAFT_CONTROLLER_SILENT_SECONDS` (1800), kill switch absent). A deliberate kill switch is rest, not silence. Nothing here restarts the controller; it stops the failure from being invisible, which is what made it expensive.

## [3.4.24] — 2026-08-14

### The recovery lane stops waiting for permission to work

- `recovery-incident.py drain` returns the open ledger in the order that unblocks delivery: safety, then pipeline blockers (an uncollected finished worker, a coordinator that cannot own or publish, an unobserved wait), then lane recovery, then housekeeping under `CRAFT_DRAIN_HOUSEKEEPING_QUOTA` (1) so it can never starve delivery. `requestImmediateCycle` says the turn ended with delivery still blocked;
- the controller skill no longer scopes a turn to the admission envelope. The envelope explains *why the controller woke*; it never defined what the ledger needs. Measured live: 73 open conditions with **none** claimed, turn after turn reporting nothing delivered, while four finished workers sat idle and a coordinator lease stayed stale for 67 minutes — the delivered cycle had named 3 incidents and been consumed.

### A plan says who performs it

- every `nextActions` entry carries `executor` (`worker`, `auditor`, `coordinator`, `owner-gate`, `external-observer`), and an `owner-gate` action carries `gateRef` naming an open gate, or it is `plan-awaits-owner-without-gate`. While nothing is in flight, an action with no executor is `plan-action-without-executor`. This closes a day-long stall whose plan led with "the owner authenticates inside a GUI" — unexecutable by any worker, yet indistinguishable from work to every check the protocol had;
- `idle-without-preparation` — a gate holds the increment, nothing is dispatchable, no lane runs, and not one action is executed by an agent. Waiting on the owner is not permission to stop: decompose the next increment, draft what either answer needs, warm clones, freeze the checks that run either way. Preparation must be reversible and must never take an external effect.

## [3.4.23] — 2026-08-14

### Idleness that hid in the seams

- `blocked-story-without-binding:<ids>` — a story held `blocked` while every dependency is `accepted`/`integrated` and it names no open gate, observed wait or declared blocker through the new story field `blockedByRef`. Holding a story `blocked` hid dispatchable work from `idle-ready-work` completely, so a project with available work read as legitimately stuck: observed live with a readback story blocked behind a dependency that was already accepted *and merged*. Binding is named, never inferred — measured first, four live stories were legitimately waiting on an open owner gate, so inference would have called healthy projects neglectful;
- `handoff-unconsumed:<workUnits>` — a lane at `handoff-ready` past `CRAFT_STATUS_HANDOFF_GRACE_SECONDS` (600 s). A finished worker is an idle worker until its result is taken, and consumption plus dispatching the next story belong to the same turn. Measured live: three workers waited 8–14 minutes with their work already pushed while not one lane in the fleet was running;
- `merge-receipt-unpublished:<workUnits>` — a standing-merge receipt exists but no story declares that merge, so the board keeps the next story parked behind finished work. Observed live minutes after the first standing-authority merge landed.

### One signal stops crying wolf

- `idle-ready-work` no longer fires for a story whose lane already reached `handoff-ready`. That window is a healthy hand-off with its own deadline (above), and counting it as idle devalued the signal that matters. No project-level `blocked-without-blocker-binding` check was added: publishing a `blocked` phase is already refused unless it names a gate, an observed wait or a bounded commitment, and a second check would only duplicate that guard.

## [3.4.22] — 2026-08-14

### The gate that repeats becomes a standing authority

- `standing-authority.py` lets the owner answer once: *while these conditions hold, merge it.* A grant is per project and per exact branch, by direct-owner authority only, with a reason, a risk ceiling and an expiry (default 7 days, max 30). `check` reports every refusal; `use` writes a durable receipt **before** the merge so the trail cannot be authored afterwards; `revoke` is immediate. Observed live: a project reached this exact gate on its third owner-authorized attempt with every condition already proven — acceptance PASS, required CI green, certificate valid;
- every condition is verified from runtime truth and the local clone, never from a coordinator's claim: the certificate validates through the same `completion-certificate.py` rules, the candidate must exist in the clone and must **not** already be in the branch, the branch must be authorised and present as `origin/<branch>`, the work unit's risk must sit within the ceiling, and a project HOLD or any open gate on that work unit refuses. An earlier grant never outranks the owner pausing the project now;
- the authority covers merging into a protected branch and nothing else. Publishing a release, deploying, spending money or entitlements, using credentials and irreversible data changes stay owner-only regardless of any grant.

### Merges name the authority that permitted them

- `delivery.protectedBranches` plus a story-level `mergeAuthorityRef` (a resolved gate id, or the work unit of a standing-merge receipt) makes `merge-without-named-authority` detectable. Authority is **named, never inferred**: measured on live state before writing the rule, owner gates bind to coarser work units than stories, so inferring authorisation from a nearby gate would have called three healthy merges unauthorised — and, worse, would have called an unauthorised merge authorised.

## [3.4.21] — 2026-08-14

### An upgrade no longer disables self-healing

- `install.sh` remembers whether the kill switch was absent before the install and removes it again after the payload lands and its tests pass. It still arms the switch *before* mutating anything, so a failed install leaves recovery disabled — but a successful one re-arms the fleet. Observed live: two upgrades left self-healing off for three hours while eleven conditions accumulated unacted, and nothing said so;
- `recovery-incident.py detect`/`report` now carry `killSwitch` (`present`, `ageMs`, `observedConditions`, `staleWithOpenConditions`). While the switch is present nothing may be claimed, so the disabled state cannot heal itself — what it can do is stop being invisible past `CRAFT_KILL_SWITCH_STALE_SECONDS` (1800 s).

### `accepted` and `delivered` become evidence-bound

- every `accepted` story names `acceptanceRef` — an observed audit-verdict/observer-terminal/terminal-handoff event key, or a completion certificate for its `workUnit` — plus the `workUnit`. Missing is `story-accepted-without-evidence`; named-but-unobserved is `story-acceptance-ref-not-observed`, which is worse because it reads as proof. Measured before the rule: fifteen accepted stories across six projects, none bound to anything. Certificates take `--story-id` so the binding is two-way;
- a new `delivery` declaration (`repoPath`, optional `targetBranch`, `openPullRequests`) makes delivery the **one field the protocol verifies itself**: a story's `mergeSha` must be an ancestor of `origin/<default branch>` in that clone, or it is `merge-claim-unverified`. The default branch is read from `origin/HEAD` rather than assumed, and only the merge commit is checked — under a squash merge the candidate commit legitimately never lands, so requiring it would flag healthy projects (measured against live certificates before the rule was written). At `deploying`/`demonstrating`/`complete`, an accepted story with no merge commit is `unmerged-delivery`;
- declared open pull requests carry a duty: `green-clean` → merge, `conflicting` → rebase, `checks-failing` → fix or close, `review-required` → keep the check fresh. An actionable PR parked past `CRAFT_STATUS_PR_ACTION_GRACE_SECONDS` (3600 s) is `pull-request-unfinished`; a check older than `CRAFT_STATUS_PR_CHECK_STALE_SECONDS` (21600 s) is `pull-request-check-stale`. Observed live: a green, conflict-free PR unmerged for three days while its project published `deploying`.

### Work that no machine could see

- `unregistered-child-lane` — a child of a live coordinator with no registered lease, older than `CRAFT_UNREGISTERED_CHILD_SECONDS` (600 s). Without a lease an executor is invisible to idle-ready detection, dead-lane detection, watchdog liveness, worktree uniqueness, preservation proof and archivable backlog simultaneously, so real work reports as an idle project. Observed live: six such children across three projects, one of them running the owner-authorized correction attempt.

## [3.4.20] — 2026-08-14

### Fewer gates for the owner, more the fleet fixes itself

- a coordinator may grant itself **exactly one** further bounded correction attempt when the cause is proven deterministic and the fix fits one named scope, declared as `correctionBudgetExtensions` (`storyId`, `rootCauseRef`, `correctionScope`, `grantedAt`). A second extension for the same story is `correction-budget-extension-reused` and returns to the owner. Observed live: a 38-character Alembic revision that cannot fit `varchar(32)` consumed an owner gate for a one-line fix;
- gates are for external effects only. Investigation, root-cause contracts, reproductions and audits are self-authorized; publishing, merging, deploying, spending and credential use stay owner-only. A coordinator had bundled "investigate this flaky CI" together with "release this" into one gate, so the owner could not authorize the cheap half;
- `complete-without-next-increment` — a `complete` phase left standing for `CRAFT_STATUS_COMPLETE_IDLE_SECONDS` (1800 s) with no next action and no gate asking what to take next. A finished increment is not a finished project, and silent idle looked exactly like health on the board;
- `coordinator-not-live` gains the actions `verify-session-absent` and `respawn-from-handoff-snapshot`; the controller skill states the exact respawn-and-transfer procedure. A coordinator session that vanished from both server and disk previously waited for the owner while its project stopped;
- `orphaned-dead-lane` — a `stalled`/`error` lane older than `CRAFT_ORPHANED_LANE_SECONDS` (86400 s) whose dispatching coordinator no longer owns any project. It can never become preservation-proven, so it stayed outside `archivableBacklog` forever: 23 accumulated live, the oldest 91 hours, each holding a worktree. Clean worktrees are now reapable; dirty ones raise one gate instead of vanishing.

### Increment cards show the work, not just a counter

- `increment-board.py` joins the protocol payload (it was an owner-local script) and renders **one subtask per Product Increment story** under each project card. Board status follows story state (`accepted`/`integrated` → done, `executing` → in progress, `failed`/`blocked` → needs review), a story leaving the increment is archived, a new increment rolls the whole set, and `--reset-cards` archives cards plus subtasks and rebuilds them in the same pass. `0/5` on a card now has five readable rows beneath it.

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

[Unreleased]: https://github.com/razumv/craft-protocol/compare/v3.4.36...HEAD
[3.4.36]: https://github.com/razumv/craft-protocol/compare/v3.4.35...v3.4.36
[3.4.34]: https://github.com/razumv/craft-protocol/compare/v3.4.33...v3.4.35
[3.4.33]: https://github.com/razumv/craft-protocol/compare/v3.4.32...v3.4.33
[3.4.32]: https://github.com/razumv/craft-protocol/compare/v3.4.31...v3.4.32
[3.4.31]: https://github.com/razumv/craft-protocol/compare/v3.4.30...v3.4.31
[3.4.30]: https://github.com/razumv/craft-protocol/compare/v3.4.29...v3.4.30
[3.4.29]: https://github.com/razumv/craft-protocol/compare/v3.4.28...v3.4.29
[3.4.28]: https://github.com/razumv/craft-protocol/compare/v3.4.27...v3.4.28
[3.4.27]: https://github.com/razumv/craft-protocol/compare/v3.4.26...v3.4.27
[3.4.26]: https://github.com/razumv/craft-protocol/compare/v3.4.25...v3.4.26
[3.4.25]: https://github.com/razumv/craft-protocol/compare/v3.4.24...v3.4.25
[3.4.24]: https://github.com/razumv/craft-protocol/compare/v3.4.23...v3.4.24
[3.4.23]: https://github.com/razumv/craft-protocol/compare/v3.4.22...v3.4.23
[3.4.22]: https://github.com/razumv/craft-protocol/compare/v3.4.21...v3.4.22
[3.4.21]: https://github.com/razumv/craft-protocol/compare/v3.4.20...v3.4.21
[3.4.20]: https://github.com/razumv/craft-protocol/compare/v3.4.19...v3.4.20
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

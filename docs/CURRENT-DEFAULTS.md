# Protocol v3.4.33 defaults

This file records the portable defaults represented by the packaged scripts. Replace model connection slugs and paths for the target workspace.

## Paths

```text
Craft root:          $HOME/.craft-agent
Workspace:           $HOME/.craft-agent/workspaces/general
Sessions:            $HOME/.craft-agent/workspaces/general/sessions
Scripts:             $HOME/.craft-agent/scripts
Runtime:             $HOME/.craft-agent/runtime
PID fallback:        $HOME/.craft-agent/pids
Logs:                $HOME/.craft-agent/logs
Workspace skills:    $HOME/.craft-agent/workspaces/general/skills
Workspace labels:    $HOME/.craft-agent/workspaces/general/labels/config.json
Watchdog LaunchAgent:          $HOME/Library/LaunchAgents/com.craft-protocol.worker-watchdog.plist
Admission LaunchAgent:         $HOME/Library/LaunchAgents/com.craft-protocol.recovery-admission.plist
```

## Models and permissions

```text
Coordinator connection:        chatgpt-plus (replace if needed)
Coordinator model:             pi/gpt-5.6-sol
Worker/auditor model:          pi/gpt-5.6-terra
Reasoning:                     medium
Permission mode:               allow-all
Fallback provider TTL:         3600 seconds
Coordinator lease:             3600 seconds
```

## Execution limits

```text
Primary Product Increments:    1 per project
Typical increment batch:       3–8 related stories or ~4–16 hours; 1 story allowed when complete
Parallel lightweight stories:  up to 2 disjoint DAG lanes
Workers per project ceiling:   2
Auditors per increment ceiling:1, only at Medium/High integrated risk boundary
Workers per story/work-unit:   1; duplicate live story lanes prohibited
Integration candidates:        1 at a time
Global heavy jobs:             1 by default; bounded resource-aware exceptions require explicit authority
Observable-job threshold:      expected runtime >10 minutes
Heartbeat interval:            10–15 minutes, internal lease only
Healthy evidence window:       900 seconds
Stalled evidence threshold:    1800 seconds
Progress CPU source (3.4.4):   whole descendant process tree of the observable job, not the direct child only
Infrastructure detour:         1 safe attempt or 20 minutes
Correction cycles:             1
Second acceptance failure:     exact escalation; no automatic attempt N+1
```

## Acceptance and communication

```text
Low increment risk:            scoped story checks + coordinator integration review + 1 batch CI
Medium increment risk:         1 focused independent audit of immutable integrated candidate
High increment risk:           1 focused integrated audit + CI/readback/gates/certificate
UI completion:                 real desktop/mobile/user-workflow evidence required
Audit-of-audit:                prohibited
Owner-facing routine polling:  prohibited
Coordinator routine updates:   prohibited
Owner-requested status query:  allowed
Exact owner decision request:  allowed
Material milestone report:     candidate/verdict/merge/readback/blocker only
Owner status order:             customer outcome → demonstrable now → remaining → ETA/confidence → one blocker → technical evidence
Owner status lead prohibited:   PR/SHA/CI/session/audit/protocol mechanics
```

See [Delivery Mode and Role Separation](DELIVERY-MODE-v3.2.0.md).

## Rotation guidance

```text
Tokens:                        approximately 200,000
Messages:                      approximately 500
Active lanes:                  3
Open gates:                    8
Immediate trigger:             request-buffer/context failure
Machine flag (3.4.5):          coordinator-complexity-threshold from registry validate at 500 msgs / 200k tokens
Provider trigger:              repeated connection/SIGTERM failure
```

## Watchdog

```text
Label:                         com.craft-protocol.worker-watchdog
RunAtLoad:                     true
Interval:                      300 seconds
Process type:                  Background
Python default:                /opt/homebrew/bin/python3
```

## Self-healing controller

```text
Public automation templates:   disabled by default
Legacy recurring prompts:      permanently disabled
Admission supervisor:          deterministic, before any LLM session
Admission interval:            300 seconds; report-only under kill switch
Production admission:          capability-v2 delivery/inspect/recover on exact runtime identity
Direct capability:             available=true, version=2; deliver + inspect + recover channels
Delivery completion:           consumed receipt + matching content/completion revision proof
Durable processing generation: numeric during processing and idle; idle timing fields are null
Recovery transition:           previous generation must equal request; resulting generation must advance
Recovery CAS busy:              exit 75 retry; does not spend the single correction attempt
Outstanding envelope:          one per target generation; meaningful incident changes coalesce in place
Stuck processing deadline:     1800 seconds by default; one guarded recovery attempt, then blocked
Idle deadline liveness (3.4.1): completed turn after deliveredAt → consumedVia completed-turn-liveness, not blocked
Unresolved re-wake (3.4.14): consumed wake re-issued up to 2x after 1800s quiet, then the lane escalates to the controller
Idle-ready detection (3.4.14): ready/executing story with no lane/wait/work-observer commitment → idle-ready-work
Work observers (3.4.15):       only worker-lease and external-wait commitments prove execution; review/gate promises do not
Self-review churn (3.4.15):    >=2 timed-out scheduled reviews with no execution → scheduled-review-churn
Gate card retention (3.4.16):  resolved card renamed ✅ + status done, archived after 3600s (CRAFT_BOARD_DONE_RETENTION_SECONDS)
GitHub sync (3.4.18):          material stage without githubSync, or naming an older stage → github-sync-missing/stale
Own dead lane (3.4.19):        lane dispatched this generation in stalled/error with no active worker → dead-lane-unreplaced
Exhausted correction (3.4.19): failed story with no plan, lane or open gate → exhausted-correction-without-escalation
Predecessor archive (3.4.19):  live predecessor 900s after an accepted handoff → predecessor-unarchived incident + wake
Correction extension (3.4.33): one self-granted bounded extension per story; the second → correction-budget-extension-reused
Gate scope (3.4.33):           gates only for external effects; investigation/root-cause/audit is self-authorized
Complete idle (3.4.33):        complete standing 1800s with no next action or gate → complete-without-next-increment (CRAFT_STATUS_COMPLETE_IDLE_SECONDS)
Vanished coordinator (3.4.33): verify-session-absent then respawn-from-handoff-snapshot before owner escalation
Orphaned lane (3.4.33):        dead lane >86400s whose dispatcher owns no project → orphaned-dead-lane (CRAFT_ORPHANED_LANE_SECONDS)
Increment cards (3.4.33):      one subtask per story; status follows story state; --reset-cards rebuilds cards + subtasks
Kill switch re-arm (3.4.33):   install.sh restores the pre-install self-healing state after tests pass
Kill switch visibility (3.4.33): detect/report expose killSwitch.staleWithOpenConditions past 1800s (CRAFT_KILL_SWITCH_STALE_SECONDS)
Story acceptance (3.4.33):     accepted story needs observed acceptanceRef + workUnit → story-accepted-without-evidence / -ref-not-observed
Delivery verification (3.4.33): story mergeSha must be ancestor of origin/<origin/HEAD branch>; merge commit only, never candidate
Delivered stages (3.4.33):     deploying/demonstrating/complete require a merge commit per accepted story → unmerged-delivery
Pull request duty (3.4.33):    actionable PR parked >3600s → pull-request-unfinished; check older than 21600s → pull-request-check-stale
Lease registration (3.4.33):   lease-less child of a live coordinator >600s → unregistered-child-lane (CRAFT_UNREGISTERED_CHILD_SECONDS)
Standing merge authority (3.4.33): per project + exact branch, direct-owner only, risk ceiling, TTL default 604800s (max CRAFT_STANDING_AUTHORITY_MAX_TTL_SECONDS 2592000)
Standing merge refusals (3.4.33): invalid certificate, candidate absent or already in branch, unauthorised branch, risk above ceiling, project HOLD or work-unit gate
Standing merge receipt (3.4.33): written by `use` before the merge; absence next to a delivered story is detectable
Named merge authority (3.4.33): story mergeAuthorityRef must be a resolved gate id or a standing-merge work unit → merge-without-named-authority
Blocked story binding (3.4.33): blocked story with satisfied deps needs blockedByRef naming an open gate/wait/blockerRef
Handoff deadline (3.4.33):     handoff-ready lane older than 600s → handoff-unconsumed (CRAFT_STATUS_HANDOFF_GRACE_SECONDS)
Merge publication (3.4.33):    standing-merge receipt without a declared mergeSha → merge-receipt-unpublished
Idle-ready scope (3.4.33):     a story with a handoff-ready lane is not idle; consumption has its own deadline
Ledger drain order (3.4.33): safety, pipeline blockers, lane recovery, housekeeping (quota CRAFT_DRAIN_HOUSEKEEPING_QUOTA=1, limit CRAFT_DRAIN_LIMIT=3)
Controller scope (3.4.33):     a valid lease works the open backlog; the admission envelope is a wake reason, not the work list
Plan executors (3.4.33):       nextActions[].executor required in flight-free turns; owner-gate actions need an open gateRef
Preparation duty (3.4.33):     gate-blocked idle with no agent-executed action → idle-without-preparation
Probe deferral (3.4.33):       unobservable safety fact defers the cycle; durable block after 3 consecutive failures (CRAFT_ADMISSION_MAX_PROBE_FAILURES)
Detection independence (3.4.33): the admission cron runs detect --apply every tick; perception never waits for a controller turn
Controller silence (3.4.33):   blocking backlog + no controller turn within 1800s + no kill switch → drain reports controller.silent (CRAFT_CONTROLLER_SILENT_SECONDS)
Transport health (3.4.33):     admission tick records channel success/failure; drain reports transport.lost after 900s without success (CRAFT_TRANSPORT_LOST_SECONDS)
Drain ranks (3.4.33):          safety, idle executors, bookkeeping, lane recovery, housekeeping (quota)
Required sources (3.4.33):     story requiredSources must be reachable by its live lane → lane-missing-required-sources
Host saturation (3.4.33):      load1/cores > 2.5 → transport.hostStarved instead of transport.lost (CRAFT_HOST_SATURATION_RATIO)
Kill switch provenance (3.4.33): install.sh stamps armed-by/rearm-expected; installer-armed switch older than 600s → killSwitch.stranded (CRAFT_KILL_SWITCH_STRANDED_SECONDS)
Silence behind a switch (3.4.33): only a deliberate pause excuses controller silence; a stranded switch does not
Gate choice separators (3.4.33): create accepts , and | and normalizes/dedupes, so a gate is always answerable
Merge authorization (3.4.33): pre-merge proof only (PASS on candidate, green required CI, head unchanged, no open gates); readback stays a post-merge duty recorded as readbackOwed
Gate external effect (3.4.33): create requires --external-effect from the owner-only list; self-authorized values are refused with the remedy
Correction budget (3.4.33):    extensions bounded by distinct proven rootCauseRef, not by count; repeated cause returns to the owner
Coordinator name (3.4.33):     exactly [<project>] Coordinator v<version> → coordinator-name-nonconforming / -project-mismatch
Stale coordinators (3.4.33):   every live coordinator session that is not the current one → stale-coordinator-session
Plan dispatchability (3.4.33): worker/auditor action with no ready or executing story and no lane → worker-action-without-dispatchable-story
Plan story refs (3.4.33):      nextActions[].storyRef must name a story the increment contains
Rotation adoption (3.4.3):     lease parent rebinds to registry successor via activeChildren; labels stay historical
Evidence adoption (3.4.7):     inbox adopt re-addresses predecessor events, immutable bindings survive; waits rebind on reconcile
Block supersede (3.4.3):       durable block of a superseded target identity yields to the new generation's cycle
Direct coordinator lane:       stale/current handoff/terminal wait and v3.3.0 inbox/status/commitment wakes; exact authoritative generation
Expected runtime version:      required deployment configuration; no package default
Expected runtime commit:       required deployment configuration; no package default
Runtime identity source:       automations:admissionCapabilities; never system:versions
Workspace ID:                  required deployment configuration; no package default
Server token:                  CRAFT_SERVER_TOKEN or owner-only CRAFT_SERVER_TOKEN_FILE
Scheduler prompt guard:        permanently disabled; never armed by capability-v2 admission
Persistent controllers:        exactly 1 reusable session
Controller model:              pi/gpt-5.6-sol / high / allow-all
Controller/claim TTL:          900 seconds
Maximum controller wall time:  900 seconds; heartbeat cannot extend it
Incident actions per turn:     3
Archive/reaps per turn:        2
Coordinator rotations/turn:    1
Worker attempts/incident:      2, then owner escalation
Coordinator recovery:          2 wake cycles + 1 bounded rotation, then escalation
Controller harness invariant:  exactly 1 persistent active; zero stale receipts
Harness identity:              PID + process start token + command SHA-256
Notifier cleanup:              archive first; exact guarded reap only
Kill-switch sentinel:          $HOME/.craft-agent/runtime/self-healing.disabled
Installer first mutation:      create/restore kill switch before copying the current payload
Activation order:              runtime f8679cdc first → Protocol → verify-runtime → report-only → canary approval
```

## Coordinator inbox, Product Increment status, and commitments (v3.4.0)

```text
Inbox storage:                 ~/.craft-agent/runtime/coordinator-inbox/<project>/<event-key>.json
Inbox claims:                  ~/.craft-agent/runtime/coordinator-inbox-claims/<project>.json
Report kinds:                  progress, candidate, audit-verdict, terminal-handoff, blocker, observer-terminal
Report kind roles:             audit-verdict auditor-only; candidate worker-only; progress/candidate refused from terminal lanes
Role re-anchor:                binding roleReminder echoed on every inbox submit/claim
Lease creation refusals:       self-parented lane, non-coordinator parent, live-lane worktree collision
Blocked/hold publish gates:    blocked needs open owner-gate ref or active commitment; hold needs open explicit-hold gate
Failure classes:               admission-environment, implementation-defect, product-acceptance, integration-release, irreversible-high-risk
Failure-class scope:           blocker/terminal/verdict/observer reports only; participates in payload fingerprint
Waking kinds:                  terminal-handoff, audit-verdict, blocker, observer-terminal
Coalescing key:                project + generation + sender + work-unit + attempt + kind
Claim TTL:                     900 seconds; unacked items return on expiry; no report deleted on claim
Ack evidence:                  same token/generation + published status revision or exact terminal evidence
Status storage:                ~/.craft-agent/runtime/coordinator-status/<project>.json
Status customer fields:        demonstrableNow, remainingOutcome, etaRange, confidence, realBlocker
Status Product Increment:      ID, stage, risk tier, real-workflow criterion, non-goals, 1..8 acyclic stories
GitHub sync (3.4.33):          githubSync {issue, commentRef, projectField, syncedStage, syncedAt}; material stage without it → github-sync-missing/stale
Status next actions:           up to 3 ordered; each needs trigger + required evidence + success/failure branch
Status classifications:        verified, executing, waiting-observed, blocked, stale, contradictory
Commitment storage:            ~/.craft-agent/runtime/coordinator-commitments/<project>/<id>.json
Commitment bindings:           worker-lease, external-wait, owner-gate, scheduled-review
Commitment deadline range:     60..604800 seconds
Owner aggregate report:        coordinator-status.py report --all --format markdown|json
Reconcile cadence:             watchdog, every 300 seconds, before the incident scan
Housekeeping (3.4.8):          up to 5 preservation-proven terminal children archived per material transition; predecessor archived after handoff; archivableBacklog in worker-lease report
Deaf-coordinator wake (3.4.6): coordinator-worker-terminal-status, controller-bound, wake-1/wake-2/rotation stages
New incidents:                 coordinator-inbox-ready, coordinator-status-missing, coordinator-status-stale,
                               coordinator-plan-unexecutable, coordinator-commitment-overdue, coordinator-status-contradiction
```

## Status IDs expected by the source workspace

```text
backlog
todo
needs-review
done
cancelled
```

## Schema versions

```text
Coordinator registry:          1
Owner gates:                   1
Recovery ledger:               1
Completion certificates:       1
Worker leases:                 1
Observable job receipts:       1
Recovery incidents:            1
Recovery controller lease:     1
Recovery admission receipt:    3 (prepared/delivered/pending/consumed/recovering/blocked)
Coordinator tick receipts:     3 (one project-keyed exact-generation target cycle)
Coordinator inbox item:        1
Coordinator product status:    1
Coordinator commitment:        1
Labels config:                 1
```

# Protocol v3.4.17 defaults

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

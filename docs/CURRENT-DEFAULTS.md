# Protocol v3.2.1 defaults

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
Primary outcomes per project:  1
Default implementation workers:1
Workers per project ceiling:   2
Auditors per project ceiling:  1
Workers per work-unit:         1
Auditors per work-unit:        1
Global heavy jobs:             1
Observable-job threshold:      expected runtime >10 minutes
Heartbeat interval:            10–15 minutes, internal lease only
Healthy evidence window:       900 seconds
Stalled evidence threshold:    1800 seconds
Infrastructure detour:         1 safe attempt or 20 minutes
Correction cycles:             1
Second acceptance failure:     exact escalation; no automatic attempt N+1
```

## Acceptance and communication

```text
Low risk:                      coordinator review + scoped CI
Medium risk:                   1 focused independent audit
High risk:                     1 focused audit + CI/readback/gates/certificate
Audit-of-audit:                prohibited
Owner-facing routine polling:  prohibited
Coordinator routine updates:   prohibited
Owner-requested status query:  allowed
Exact owner decision request:  allowed
Material milestone report:     candidate/verdict/merge/readback/blocker only
```

See [Delivery Mode and Role Separation](DELIVERY-MODE-v3.2.0.md).

## Rotation guidance

```text
Tokens:                        approximately 200,000
Messages:                      approximately 500
Active lanes:                  3
Open gates:                    8
Immediate trigger:             request-buffer/context failure
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
Production admission:          direct delivery only on exact configured runtime identity
Direct capability:             available=true, version=1, automations:admissionDeliver
Expected runtime version:      required deployment configuration; no package default
Expected runtime commit:       required deployment configuration; no package default
Runtime identity source:       automations:admissionCapabilities; never system:versions
Workspace ID:                  required deployment configuration; no package default
Server token:                  CRAFT_SERVER_TOKEN or owner-only CRAFT_SERVER_TOKEN_FILE
Notifier schedule:             permanently disabled legacy guard; never armed by direct admission
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
Recovery admission receipt:    2 (prepared/direct-delivery receipt)
Labels config:                 1
```

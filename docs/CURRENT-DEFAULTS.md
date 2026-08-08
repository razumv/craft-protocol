# Protocol v3.1 defaults

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
LaunchAgent:         $HOME/Library/LaunchAgents/com.craft-protocol.worker-watchdog.plist
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
Workers per project:           2
Auditors per project:          1
Workers per work-unit:         1
Auditors per work-unit:        1
Global heavy jobs:             1
Observable-job threshold:      expected runtime >10 minutes
Heartbeat interval:            10–15 minutes
Healthy evidence window:       900 seconds
Stalled evidence threshold:    1800 seconds
Audit failures before freeze:  2
```

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
Labels config:                 1
```

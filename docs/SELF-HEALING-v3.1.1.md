# Self-Healing Extension v3.1.1

Craft Protocol v3.1.1 adds bounded autonomous recovery without giving the deterministic watchdog agentic powers.

## Layers

1. `worker-watchdog.py` reconciles leases and invokes `recovery-incident.py detect`. Both are deterministic and non-LLM.
2. A short-lived Craft Automation session reads the `self-healing-controller` skill, claims incidents, and uses normal Craft session tools under a strict action budget.

## Incidents

The registry is stored under `~/.craft-agent/runtime/recovery-incidents/`. Stable keys deduplicate repeated observations. Incidents move through `open`, `claimed`, `deferred`, `resolved`, `escalated`, or `suppressed`. Claims and controller ownership use atomic file locks and expiry leases. Live owners must heartbeat rather than reclaim. Expired incident claims require deterministic `detect --apply` before a new claim; an expired controller session must release rather than reclaim itself. The kill switch blocks claims, heartbeats, and incident mutations; `controller-release` remains the sole allowed fail-safe mutation so a disabled controller can relinquish its lock. Coordinator claims expose deterministic `wake-1`, `wake-2`, then `rotation` stages with stage-specific allowed actions.

Detected classes include stale/error coordinators, unresolved exact Pi SIGTERM events after the last authoritative heartbeat, expired fallback, stuck transfer, suspect/stalled/error workers, missed terminal handoffs, unreported observable-job exits, heavy-lock exit 75, cwd collisions, unknown preservation, and open owner gates.

## Safety model

The controller may wake coordinators, request renewal/reconciliation, acknowledge and queue exit-75 retries, archive/reap a terminal child only after complete preservation proof, release a coordinator slot, and rotate a coordinator only through a verified project-bound Codex bridge after two failed wake attempts.

It may never decide owner gates/HOLD, merge/close/deploy/send, kill/restart Craft Agents, archive dirty/unpushed/shared-cwd work, infer completion from silence, or spawn an unbound coordinator. One coordinator session ID may own only one project scope globally; claim/transfer/validation reject duplicates. Authoritative parent-coordinator project mapping overrides a child's project label in both incident attribution and recovery-ledger synthesis. Conflicting labels emit a critical hard-refusal incident, remain visible in the authoritative parent's ledger, and cannot place the lane in a second project's ledger. A legacy duplicate parent is globally ambiguous: its children are adopted into no project until registry repair, and only global hard-refusal incidents are emitted.

## Budgets

Defaults:

- one scheduled sweep every 15 minutes;
- one controller lease at a time;
- at most 3 incidents, 2 archive/reaps, and 1 rotation per turn;
- 15-minute claim/cooldown;
- worker incidents: 2 automatic attempts before owner escalation;
- coordinator stale/error/SIGTERM: 2 wake cycles plus 1 bounded project-bound rotation attempt, then escalation.

## Kill switch

Immediate runtime refusal:

```bash
touch ~/.craft-agent/runtime/self-healing.disabled
```

Remove only after investigation:

```bash
rm ~/.craft-agent/runtime/self-healing.disabled
```

Also disable the two Craft Automation matchers. The public automation template ships disabled.

## Installation

```bash
./install.sh             # dry run
./install.sh --apply     # backup + install + verify
```

Review and merge `config/self-healing.automations.template.json` into the workspace automation config. Validate before enabling. Start report-only, then allow wake messages, then terminal cleanup, and enable rotation last.

## CLI

```bash
~/.craft-agent/scripts/recovery-incident.py detect --apply
~/.craft-agent/scripts/recovery-incident.py list --state open
~/.craft-agent/scripts/recovery-incident.py report
```

Do not treat message delivery as incident resolution. Resolve only after the underlying condition objectively clears.

## Patch compatibility

v3.1.1 is additive and opt-in. Runtime schemas remain version 1, v3.1 records/commands remain compatible, and existing v3/v3.1 attempts are adopted without restart. Any incompatible schema/default change requires a new version decision.

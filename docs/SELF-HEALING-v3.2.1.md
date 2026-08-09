# Self-Healing Harness Lifecycle — Protocol v3.2.1

Protocol v3.2.1 fixes a live operational defect in v3.1.1 automatic recovery: every scheduled or terminal-event prompt creates a new Craft session, and some Craft builds retain its Pi harness after the session stops. Status/archival alone therefore allowed recovery automation to increase RAM/swap/process pressure and contribute to provider instability.

## Safe topology

```text
deterministic watchdog (5 min)
  -> idempotent incidents
  -> one scheduled prompt controller (15 min)
     -> exact harness self-registration
     -> singleton controller lease
     -> archive-first reap prior registered controller
     -> bounded incident actions
     -> needs-review / stop
  -> next scheduled controller reaps it
```

`SessionStatusChange` prompt recovery remains disabled. Event storms would create controller sessions faster than bounded cleanup can prove and reap them.

## Exact registration

The first controller shell action is:

```bash
~/.craft-agent/scripts/controller-harness.py register --session <self>
```

The receipt stores mode-0600 runtime state only:

- session ID;
- exact harness PID;
- process start token;
- SHA-256 of the full process command;
- registration and session creation timestamps.

The nearest ancestor must be `pi-agent-server` or `claude-agent-sdk-binary/claude`. The Craft app process is an unconditional refusal.

## Next-run cleanup

A later singleton controller may reap a prior controller only after:

1. target session is a recovery controller;
2. it is terminal and not processing;
3. it is archived first;
4. its registered PID still has the exact start token and command fingerprint;
5. the process is a recognized harness and not the Craft app;
6. the target is not the current controller.

```bash
~/.craft-agent/scripts/controller-harness.py reap \
  --session <prior> --current-session <self> --apply
```

Process lookup is tri-state: `alive`, `absent`, or `unknown`. Only an OS-proven absent PID permits receipt deletion. Permission/transient `ps` failures are `unknown` and retain the receipt. After SIGTERM the guard polls the exact PID/start/command identity for up to five seconds. A still-running, unknown, or identity-changed PID remains registered and fails closed; SIGKILL is never used. `--current-session` is mandatory and its live receipt must match the nearest real harness ancestor of the executing reaper process. A different live controller receipt or arbitrary session string cannot authorize the call, and omitting self identity cannot bypass self-reap refusal.

No PID guessing, cwd inference, SIGKILL, or process-tree fallback is permitted. Missing receipt, PID reuse, unknown identity, live/non-terminal session, or app/non-harness command fails closed.

## Bounded invariant

Healthy steady state permits:

```text
active registered controller harnesses <= 1
terminal registered harnesses awaiting next-run reap <= 1
```

The deterministic watchdog calls `controller-harness.py report` and becomes unhealthy on growth, an exited process with an uncleared receipt, unknown ownership, or identity mismatch. A controller can archive/reap at most two prior registered controllers in one turn.

## Activation sequence

1. Install v3.2.1 and keep both prompt automations disabled.
2. Run source and installed lifecycle regressions.
3. Enable only the scheduled automation for one report-only canary.
4. Observe two or more full intervals:
   - exact registration exists;
   - singleton lease released;
   - prior controller archived before reap;
   - harness count invariant does not grow;
   - no product/session cleanup outside the permitted incidents.
5. Keep terminal-event prompt automation disabled.
6. Retain the kill switch and disable the schedule immediately on any invariant violation.

## Retained boundaries

v3.2.1 does not authorize owner-gate/HOLD decisions, product merges/deploys, dirty/unpushed cleanup, ambiguous PID action, duplicate workers, or Craft app restart/termination. Delivery Mode v3.2.0 role separation remains unchanged: automatic recovery restores execution health; it does not supervise product work.

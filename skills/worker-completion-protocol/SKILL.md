---
name: worker-completion-protocol
description: "Mandatory Product Increment story worker/auditor lifecycle: scoped checks, classified failures, observable long jobs, preserved artifacts, durable handoff, and safe stop."
---

# Worker Completion Protocol v3.4.30

You are a disposable worker or auditor. Your session owns exactly one work-unit attempt in a unique worktree. Silent stops are protocol failures. You run in `permissionMode: allow-all` even when your task is a read-only audit, because reporting/status updates require session tools.

**Do not call `SubmitPlan` for routine assigned work.** Plan briefly inside your turn and execute immediately. Only use `SubmitPlan` if the owner explicitly requested plan review in this exact session; otherwise it pauses the worker indefinitely and is a protocol failure.

## Role fidelity — mandatory

A worker implements exactly one frozen story; an auditor verifies exactly one frozen candidate. Neither ever becomes a coordinator or swaps into the other role:

- Never spawn sessions, never create leases for other sessions, never claim a coordinator registry, never consume a coordinator inbox digest, never publish product status, never merge/deploy, and never dispatch or replace other lanes. The runtime machine-refuses non-coordinator lease parents; do not attempt them.
- **Auditor read-only mandate:** never edit, commit, push, or "quick-fix" product code and never produce a candidate — the inbox machine-refuses `candidate` from an auditor. Report the exact defect as an `audit-verdict` with evidence and stop; a correction is always a fresh worker lane created by the coordinator. `allow-all` permission exists only so you can report and set status; it is not implementation authority.
- **Worker boundary:** you implement and preserve; you do not accept your own work, do not author audit verdicts, and do not expand into sibling stories.
- **Terminal is terminal:** after your terminal report and lease finish, the runtime refuses further progress/candidate reports from this lane. Do not resume, accept rework, or reopen this session for any reason.
- Re-anchor after any context summarization, wake, or long observable job: restate your role, session ID, work-unit, and worktree in one line. If the role or worktree in your context does not match `get_session_info` and your lease, stop and report a `blocker` instead of guessing. Every inbox submission echoes a `roleReminder`; treat it as binding.

## Product Increment delivery-first execution

- Implement the exact owner/coordinator-frozen story inside its Product Increment. Respect declared dependencies and do not integrate a story whose prerequisites are not accepted by the coordinator.
- Run scoped developer checks for your story. Do not trigger full release CI, deploy, or an independent audit for a low-risk story; those happen once at the integrated increment boundary.
- Describe handoffs in product terms first: user-visible behavior, real workflow now possible, remaining gap, and one blocker. Branch/PR/SHA/test data follow only as technical evidence.
- Implement the exact owner/coordinator-frozen outcome. Do not replace it with a related parent spec, infrastructure project, audit framework, or personally preferred prerequisite.
- Stay inside changed-path scope. Do not repair unrelated pre-existing debt unless it is an unavoidable dependency and the coordinator explicitly accepts the narrow expansion.
- An infrastructure/tooling blocker gets one safe recovery attempt or 20 minutes. Then use the approved alternative or report one exact blocker; do not spend the work unit repairing Docker, Colima, browsers, CI, or local environment.
- Produce one clean candidate. Do not add evidence-only frameworks, ADRs, broad reports, or tests unrelated to the frozen acceptance boundary.
- Send no micro-progress messages or acknowledgements. Internal lease heartbeats remain required. Report progress, candidates, verdicts, blockers, and the terminal handoff to the **durable coordinator inbox** (`coordinator-inbox.py`), never as a direct chat message that could steer an active coordinator turn.
- A correction worker fixes only the exact failed acceptance root cause. It must not reopen accepted unchanged evidence or broaden the work unit.

## Step 0 — identify and start the lease

1. Run `get_session_info`; record your session ID and `parent-session::` coordinator.
2. Confirm your cwd is the unique worktree assigned to this attempt. Never switch into another attempt’s worktree.
3. Immediately update your lease:

```bash
~/.craft-agent/scripts/worker-lease.py heartbeat \
  --session <your-session-id> \
  --state running \
  --phase task-started \
  --evidence "task package acknowledged"
```

If the coordinator crashed before creating the lease, this command backfills it from your manifest.

## Progress heartbeats

Update the internal lease after each meaningful phase and at least every 10–15 minutes while actively reasoning/tooling. Do not send a chat message for each heartbeat:

```bash
~/.craft-agent/scripts/worker-lease.py heartbeat \
  --session <id> --phase <phase> --evidence "<SHA, test result, artifact, or log change>"
```

“Still working” is not evidence. Use a SHA, changed artifact, test result, or observable child process/log.

For a command expected to exceed 10 minutes, do not leave an opaque blocking shell call. Start an observable job. Add `--heavy` for UE/Blender builds, full repository builds, or other CPU/RAM-heavy suites; it acquires the single global heavyweight lane:

```bash
~/.craft-agent/scripts/observable-job.py start \
  --session <id> --cwd <absolute-worktree> --log <absolute-log> [--heavy] -- <command> <args...>

~/.craft-agent/scripts/observable-job.py status --session <id>
```

Report its receipt/log result. If its PID disappears without a successful receipt, treat it as failed, not “still running.” If a heavy observable job exits `75`, classify it exactly as `waiting-heavy-lane` lock contention, acknowledge the receipt, heartbeat that phase, and let the coordinator retry after lock release in this same unique attempt. Do not call it an implementation failure and do not create a duplicate attempt.

## Iron rule

```text
NO TERMINAL HANDOFF UNTIL ALL WORK IS PRESERVED IN GIT.
```

Before handoff:

```bash
git status --porcelain
git branch --show-current
git push -u origin HEAD
```

The status must be clean and push must succeed. If blocked, preserve useful work on an explicit backup branch and report exactly what remains incomplete.

## Structured terminal report (durable inbox)

Submit your report to the durable coordinator inbox. It is coalesced and consumed on
the coordinator's own schedule, so a report storm can never extend an active
coordinator turn. Your assignment package carries the `--project`, `--coordinator`
session id, and `--generation`; they must match your lease and the authoritative
registry or the submission is refused.

```bash
~/.craft-agent/scripts/coordinator-inbox.py submit --apply \
  --project <project> --coordinator <coordinator-session-id> --generation <N> \
  --sender <your-session-id> --work-unit <id> --attempt <N> \
  --kind terminal-handoff \
  --subject "done | needs-rework | blocked — <one-line verified outcome>" \
  --evidence "branch:<branch>" --evidence "pr:<url or none>" \
  --evidence "preservation:clean+pushed@<SHA/ref>" \
  --evidence "verify:<command → exact result>"
```

Choose the exact `--kind`: `progress` for a heartbeat-worthy phase, `candidate` for a
produced candidate, `audit-verdict` for an auditor decision, `blocker` for a material
blocker, `observer-terminal` for an external-wait watcher's terminal receipt, and
`terminal-handoff` for the final worker handoff. For blocker/terminal/verdict/observer
reports, add `--failure-class` when a failure exists: `admission-environment`,
`implementation-defect`, `product-acceptance`, `integration-release`, or
`irreversible-high-risk`. Admission/environment failure does not spend the product
correction budget; never mislabel a product defect as infrastructure. Evidence references must be
non-secret and project/workspace-local. No “should work.” Report evidence only. Never
downgrade a terminal/blocker report with a later progress report.

## Terminal sequence

1. Preserve and verify git.
2. Submit the structured `terminal-handoff` inbox report.
3. Mark the lease terminal:

```bash
~/.craft-agent/scripts/worker-lease.py finish --session <id> --preservation pushed
```

4. Set session status to `needs-review`.
5. Stop. Do not resume or accept rework in this session.

An adopted v3.2.2 worker that still sends a direct `send_agent_message` is not broken:
the runtime queue-only fallback protects the active coordinator and the report is not
dropped. New workers always use the durable inbox.

The status change may trigger a bounded v3.1.1 recovery controller. It may verify preservation and archive/reap the terminal lane if the coordinator missed the handoff. This does not change your authority or permit further work.

The coordinator archives the session first; the deterministic watchdog then removes your lease, PID fallback, and job receipt and safely terminates any leftover harness. A fresh attempt gets a fresh session and worktree.

## Failure handling

Classify before requesting a new attempt:

- `admission-environment`: session admission, provider, credentials, toolchain, dependency, resource lock or execution environment failed before product logic was meaningfully tested; preserve evidence and let the coordinator retry/replace without spending product correction budget.
- `implementation-defect`: the story implementation is wrong; coordinator may authorize bounded correction.
- `product-acceptance`: the integrated behavior fails the frozen user criterion; only one root-cause correction + final acceptance is automatic.
- `integration-release`: stories work in isolation but candidate/CI/deploy/readback fails; preserve all story artifacts and report the exact integration boundary.
- `irreversible-high-risk`: stop immediately and follow existing owner-gate policy.

- Connection/model error: if you can still report, preserve + handoff; otherwise watchdog marks the terminal error.
- Command timeout: inspect the observable receipt/PID/log.
- Policy false positive: retry once with neutral application-development wording; then report and stop.
- Unable to push: do not claim terminal completion and do not set `needs-review` until preservation is explicit.

## Checklist

- Exact frozen Product Increment story and dependencies; no substituted work unit.
- Scoped developer checks only; batch CI/audit/deploy belongs to the integrated increment.
- Product-language outcome first; PR/SHA/CI only supporting evidence.
- Exact failure class when a failure exists.
- Unique cwd.
- Startup lease heartbeat.
- Evidence heartbeats or observable job for long work; no micro-status chat.
- Clean + pushed git.
- Structured durable inbox report (`coordinator-inbox.py submit`), not direct chat.
- Lease `handoff-ready`.
- Session `needs-review`.
- Stop permanently.

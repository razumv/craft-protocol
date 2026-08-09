# Delivery Mode and Role Separation — Protocol v3.2.0

Craft Protocol v3.2.0 corrects a live role-inversion failure mode: an owner-facing infrastructure session became a continuous supervisor for otherwise authoritative project coordinators, while tests, reports, audits, and infrastructure recovery grew into independent workstreams. The system produced evidence but delayed product completion.

## Role boundary

### Human owner

- chooses product priorities and substantive/irreversible decisions;
- may request a project status or send a direction through the owner-facing session;
- controls closed board states.

### Owner-facing infrastructure session

- relays exact owner instructions;
- queries coordinators only when the owner explicitly asks for status or a specific fact;
- maintains shared protocol/runtime safety when explicitly requested;
- does **not** acknowledge routine coordinator messages, poll progress, approve normal phases, or micromanage project execution;
- ignores unsolicited coordinator updates unless they contain an exact owner decision request.

### Project coordinator

- is the autonomous authoritative operator for one project scope;
- chooses and drives the next GitHub-sourced product outcome;
- reconciles its own workers, audits, CI, merge/readback, and preservation;
- sends owner-facing messages only for a requested status, a terminal product milestone, or one exact owner blocker;
- never treats infrastructure acknowledgement as permission to continue ordinary work.

### Worker/auditor

- executes one frozen work unit in one unique lane;
- uses internal lease heartbeats instead of chat micro-statuses;
- reports one material blocker or one terminal handoff.

## Product-first state machine

```text
owner-requested outcome
  -> one implementation candidate
  -> risk-tiered focused acceptance
  -> merge/deploy/readback/close when authorized

first acceptance FAIL
  -> one exact root-cause correction
  -> one final focused re-acceptance

second FAIL
  -> exact escalation; no automatic attempt N+1
```

Tests, audits, reports, certificates, and gates are acceptance instruments. They are not product outcomes and must not become independent indefinite work.

## Risk tiers

### Low

Reversible UI, documentation, local workflow, tests, and configuration with no authentication, money, durable state, migration, production/shared data, physical evidence, release, or destructive action.

Acceptance: coordinator diff review plus scoped required CI. No independent auditor unless the frozen issue requires one.

### Medium

Backend behavior, authorization/privacy, external integration, or durable local persistence.

Acceptance: exactly one focused independent auditor at the final immutable candidate. One correction cycle maximum.

### High

Money/entitlements, production/shared databases, migrations, irreversible/destructive actions, physical build/evidence, deployment, or release authority.

Acceptance: one focused independent audit, exact CI/readback, owner gates, and completion certificate where required. High risk does not authorize audit-of-audit.

## Anti-churn rules

1. One primary visible or executable outcome per project by default.
2. The owner-requested work unit is immutable until completed, explicitly reprioritized, or blocked. A related parent specification must never silently replace it.
3. Accepted immutable evidence is reused when SHA, inputs, environment, and claim boundary are unchanged.
4. No audit-of-audit, evidence-only successor issue, new framework, ADR, or measurement method without a concrete candidate defect.
5. Infrastructure recovery gets one safe attempt or 20 minutes, then an approved alternative or one exact escalation.
6. Unrelated pre-existing debt does not enter the product lane.
7. Reports are limited to candidate, verdict, merge/deploy/readback, or exact owner blocker.
8. Routine acknowledgements and status polling are prohibited across the owner-facing/coordinator boundary.

## Non-regression example

A directly requested Core subscriptions integration must not be cancelled because a broader sales specification is discovered. Preserve both owner intents. Continue the explicitly requested work and ask one exact question only if the two requests truly conflict.

## Safety retained

v3.2.0 does not weaken:

- owner HOLD/gates and direct authority for irreversible actions;
- preservation-before-terminate and Craft app PID refusal;
- fresh unique worktrees;
- exact-head CI/readback and immutable evidence;
- secret/privacy/data boundaries;
- one global heavy job;
- deterministic watchdog and bounded v3.1.1 self-healing semantics.

The change is organizational and acceptance-scoping: prove the result once at the appropriate risk level, then deliver it.

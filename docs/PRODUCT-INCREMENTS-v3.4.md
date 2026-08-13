# Product Increments and Integration Trains — Protocol v3.4.0

## Purpose

Protocol v3.4 changes the unit of delivery without creating a new orchestration platform. The unit is a **Product Increment**: one customer-visible outcome, a bounded dependency-valid story graph, one integrated candidate, one acceptance boundary, one batch CI, one merge, one deploy/readback, and one demonstration in the real user workflow.

All v3.3 safety and runtime primitives remain authoritative: coordinator generations, durable queue-only inboxes, leases, observable jobs, external waits, commitments, owner gates, preservation-first cleanup, and capability-v2 admission.

## Product contract

One active project has one primary increment by default. Its durable status declares:

- customer-visible outcome and explicit non-goals;
- what can be demonstrated now;
- what remains;
- an honest ETA range and `low|medium|high` confidence;
- one real blocker or none;
- increment ID, integration stage, aggregate risk tier and real-workflow demonstration criterion;
- 1–8 stories with state, risk contribution and dependency IDs;
- at `stage=complete`, four distinct current-generation immutable evidence refs: integrated candidate, risk-appropriate acceptance, release/deploy readback and real-workflow demonstration.

Normally an increment groups 3–8 coherent stories or roughly 4–16 hours of related work. This is a batching heuristic, not a quota. A single-story increment is correct when that story alone produces a complete demonstrable outcome. Padding is prohibited.

The story graph must be bounded and acyclic. Duplicate IDs, duplicate edges, unknown dependencies and self-dependencies fail closed. The protocol records the DAG in the existing status; it does not add a scheduler or database.

## Integration train

1. Freeze the customer-visible outcome, non-goals and real-workflow demonstration.
2. Build the smallest dependency DAG that produces it.
3. Dispatch only dependency-ready stories. Up to two disjoint lightweight lanes may run concurrently; no duplicate story lane is permitted.
4. Each worker runs scoped developer checks and preserves its artifacts even on failure.
5. The coordinator integrates ready stories into one immutable candidate.
6. Run one integrated acceptance and required release CI for that candidate.
7. Apply one aggregate risk tier at the increment boundary.
8. Merge once, deploy/read back once and execute the named real user workflow.
9. Reuse exact unchanged evidence; do not rerun acceptance for report freshness.
10. `stage=complete` is fail-closed: every story must be `accepted`, coordinator phase must also be `complete`, and `completionEvidence` must bind four distinct current-generation inbox events by exact `eventKey + revision + fingerprint`. Medium/High acceptance must bind an auditor-authored `audit-verdict`; release readback must bind `observer-terminal` from the exact terminal external-wait watcher; demonstration evidence must contain the exact case-sensitive named demonstration criterion.
11. Advance automatically across the DAG. PASS, CI completion, focused acceptance, merge, ordinary authorized deploy/readback, real-workflow evidence collection, and the next dependency-ready finite wave never require a fresh owner message merely because a stage boundary was crossed.

One global heavyweight lane remains the default. A bounded exception requires explicit resource-aware authority and practical memory/CPU guards. Integration remains serialized around one candidate.

## Three testing levels

- **Story checks:** fast, scoped developer checks during implementation. They catch local defects and do not claim product completion.
- **Integrated acceptance:** one pass over the immutable candidate at the aggregate risk boundary.
- **Release verification:** required batch CI, deploy/readback and real-workflow demonstration.

Full CI/deploy per tiny change, tests for tests’ sake, evidence-only successor work and audit-of-audit are prohibited.

## Risk-based acceptance

- **Low:** scoped story checks → coordinator integration/diff review → one batch CI → authorized merge/deploy/readback. No independent auditor by default.
- **Medium:** one focused independent review of the final integrated candidate at the material backend/auth/privacy/integration/persistence boundary.
- **High:** one focused independent audit plus exact CI/readback, existing certificate semantics and only genuinely owner-only gates.
- **UI:** test automation is supporting evidence. Completion requires evidence from the real desktop/mobile/user workflow in the increment criterion.

A low-risk story does not receive its own auditor because a sibling makes the aggregate increment Medium or High.

## Failure taxonomy

Durable blocker, terminal, verdict and observer reports may carry one `failureClass`:

- `admission-environment` — admission, provider, credential, toolchain, dependency, resource lock or environment failed before product logic was meaningfully tested;
- `implementation-defect` — story implementation is incorrect;
- `product-acceptance` — integrated behavior fails the frozen customer criterion;
- `integration-release` — story artifacts exist but integration, CI, deploy or readback fails;
- `irreversible-high-risk` — continuing may cause irreversible/high-blast-radius harm.

Accounting rules:

- admission/environment failure preserves evidence and retries/replaces; it does not spend product correction budget;
- implementation defects permit bounded coordinator-owned correction;
- first product-acceptance failure automatically executes one exact reversible root-cause correction and one final acceptance without creating an owner gate;
- second acceptance failure or repeated same-root failure escalates the exact blocker;
- irreversible/high-risk failure stops immediately under existing owner-gate rules;
- infrastructure repair remains bounded to one safe attempt or 20 minutes and cannot replace product work.

Recovery-controller attempt accounting remains separate. v3.4 does not rewrite recovery admission.

## Human-stop boundary

A coordinator may pause for the owner only when the next step requires an explicit HOLD resolution, human product judgment or physical action, irreversible/destructive effects, money/entitlements, production secrets or credentials, legal/privacy/security exception, high-blast-radius public release, conflicting direct owner priorities, product-goal change, or a repeated same-root/second final acceptance failure after the bounded correction is exhausted. New owner gates must declare one of these machine-validated owner-only categories. Technical stage completion, Medium/High risk by itself, first CI/audit failure, reversible correction, merge/readback, and ordinary authorized deploy are not owner-stop categories.

## Owner communication contract

When the owner asks for status, coordinators answer in this order:

1. **What the customer sees.**
2. **What can be demonstrated now.**
3. **What remains.**
4. **ETA range and confidence.**
5. **One real blocker** or none.
6. **Technical evidence** only afterward.

PR numbers, commits, SHAs, CI runs, sessions, audits and protocol mechanics are secondary evidence. They must never be the lead description of progress. Additional internal blockers remain project-local unless the owner asks for detail.

The aggregate `coordinator-status.py report --all --format markdown` uses the same order.

## Compatibility and anti-scope

Legacy v3.3 status snapshots remain valid; missing v3.4 fields render as not published. Inbox event identity, claim/ack retention, generation fencing and runtime schemas remain compatible.

Protocol v3.4 deliberately adds no new service, daemon, database, queue, scheduler, role hierarchy, vector memory, knowledge graph, judge layer, release dashboard or runtime authority. Docker/Redis/framework-specific operational remedies remain project/operator responsibilities rather than portable protocol rules.

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

The story graph must be bounded and acyclic. Duplicate IDs, duplicate edges, unknown dependencies and self-dependencies fail closed. Each story declares `deliverableClass`: `product`, `contract`, `acceptance`, or `housekeeping`; omitted legacy values normalize to `product`, but current v3.4.36+ publication requires the explicit field. Every increment must carry at least one `product` story, so enabling work cannot replace the customer outcome. A current null increment is allowed only for an observed resolved direct-owner `planning-only:` product-direction authority. The protocol records the DAG in the existing status; it does not add a scheduler or database.

### v3.4.37 exact completion consumption

For a v3.4.37 coordinator, immutable completion bindings are necessary but not sufficient: the integrated handoff supplies exactly one `candidateSha:<40-hex>` marker; acceptance repeats that exact candidate and `verdict:PASS`; the observer readback repeats the candidate and every delivered `merged-main:<mergeSha>`. The existing event key/revision/fingerprint tuple prevents revision substitution, while these markers prevent semantic substitution. A mismatch fails publication. Legacy v3.4.36 records remain readable.

A post-transfer predecessor that remains live is explicit `healthy-with-maintenance-debt` only after all delivery checks are healthy. It is not a completion claim, and it never suppresses context, lease, evidence, HOLD, or product-pressure failures.

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
11. Advance automatically across the DAG. PASS, CI completion, focused acceptance, merge, ordinary authorized deploy/readback, real-workflow evidence collection, and the next dependency-ready finite wave never require a fresh owner message merely because a stage boundary was crossed. A `contract` or `acceptance` PASS issues dependency-ready `product` code in the same coordinator cycle; non-product lanes may not occupy capacity while product work is ready.
12. Exact unchanged candidate SHA plus test-environment or input fingerprint may reuse immutable acceptance evidence only when the exact pair is present in the observed inbox evidence or certificate; report freshness never requires a rerun.
13. An active lane is product work only when its work unit belongs to the current increment and its canonical CWD is inside `delivery.repoPath` or an explicit `delivery.worktreeRoots` entry. Otherwise it is non-product work and cannot hide idle-ready product work.
14. A resolved protected-merge authority issues its merge and an observed readback in the same cycle. A merge-authorized completion verifies the SHA is an ancestor of `origin/<targetBranch>`, its resolved gate/standing receipt binds that exact work unit, and its readback names `merged-main:<sha>`. HOLD permits publication/reconciliation only, never a new spawn, implementation job, merge, or close.

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
- first product-acceptance failure automatically executes one exact reversible root-cause correction and one final acceptance without creating an owner gate; no-code `contract` correction has one bounded budget;
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

## Role fidelity

Coordinators coordinate, workers implement exactly one frozen story, auditors verify exactly one frozen candidate read-only, and the recovery controller only wakes and reconciles. Role drift — a coordinator implementing code, an auditor fixing defects, a worker spawning sub-lanes or continuing after handoff — is refused by the runtime rather than left to prompt memory:

- lease creation refuses self-parented lanes, parents that are not live coordinators, and worktrees already owned by another live lane;
- the inbox refuses `candidate` reports from auditors, `audit-verdict` reports from non-auditors, and `progress`/`candidate` reports from a terminal (`handoff-ready`) lane;
- every inbox submit/claim response echoes a binding `roleReminder` that re-anchors the agent's role contract against long-context drift;
- publishing a `blocked` phase requires an open owner-gate reference or an active observable commitment, and publishing `hold` requires an open explicit-hold gate — a coordinator cannot self-hold or idle behind a prose blocker;
- registry claims remain restricted to live `agent-role::coordinator` sessions, and a role-label change removes the lease at the next reconcile.

Skills additionally require each role to re-anchor after every wake, tick, and context summarization: restate the role in one line, verify the `agent-role::` label and (for coordinators) registry generation, and re-read the role skill whenever any rule is not immediately recalled. Spawn prompts must pin the child's role and its prohibited transitions explicitly.

## Compatibility and anti-scope

Legacy v3.3 status snapshots remain valid; missing v3.4 fields render as not published. Inbox event identity, claim/ack retention, generation fencing and runtime schemas remain compatible.

Protocol v3.4 deliberately adds no new service, daemon, database, queue, scheduler, role hierarchy, vector memory, knowledge graph, judge layer, release dashboard or runtime authority. Docker/Redis/framework-specific operational remedies remain project/operator responsibilities rather than portable protocol rules.

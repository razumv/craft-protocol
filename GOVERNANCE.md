# Governance

Craft Protocol is maintained as an open-source, evidence-first orchestration project.

## Roles

### Contributors

Anyone who participates through Discussions, Issues, documentation, tests, or pull requests while following the Code of Conduct.

### Maintainers

Maintainers triage issues, review pull requests, manage releases, protect security boundaries, and make final merge decisions. Current initial maintainer: `@razumv`.

Additional maintainers may be invited after sustained, high-quality contributions that demonstrate understanding of preservation, authority, and fail-closed invariants.

## Decision process

- Small fixes: pull request review and green required CI.
- Protocol behavior changes: design Issue or Discussion, explicit invariant analysis, tests, and maintainer approval.
- Security-sensitive changes: private advisory or restricted review until disclosure is safe.
- Breaking changes: documented migration plan, changelog entry, and semantic-versioned major release.

Consensus is preferred. When consensus cannot be reached, maintainers decide based on safety, evidence, compatibility, and project scope.

## Non-negotiable boundaries

Changes must not silently weaken:

- preserve-before-terminate;
- archive-before-reap;
- Craft app/non-harness PID guards;
- unique attempt worktrees;
- one authoritative coordinator per scope;
- direct-owner irreversible authority;
- exact SHA/audit/CI evidence;
- deterministic non-LLM watchdog behavior.

A proposal to intentionally change one of these boundaries requires explicit public design review and a major version decision.

## Releases

The project follows Semantic Versioning:

- PATCH: compatible fixes/docs/tests;
- MINOR: compatible new capabilities or tools;
- MAJOR: incompatible protocol, schema, authority, or safety behavior.

Release notes must list migrations, new/changed invariants, known limitations, and verification results.

## Security

Security reports follow `SECURITY.md`. Embargoed fixes may be developed privately and disclosed with an advisory after users have a reasonable upgrade path.

## Changes to governance

Governance changes use the same public pull-request process and require maintainer approval.

## Problem

<!-- What concrete failure or limitation does this change address? -->

## Change

<!-- Describe the focused implementation and affected protocol components. -->

## Safety invariants

- [ ] Preserve-before-terminate remains intact.
- [ ] Archive-before-reap remains intact.
- [ ] Craft app/non-harness PID guards remain intact.
- [ ] Unique worktree and split-brain protections remain intact.
- [ ] Direct-owner authority and HOLD semantics remain fail-closed.
- [ ] Exact SHA/audit/CI evidence is not weakened.
- [ ] Watchdog behavior remains deterministic and non-LLM.

## Verification

<!-- Exact commands and results. Include new adversarial regressions. -->

## Migration and rollback

<!-- State compatibility, runtime schema impact, migration, and rollback. -->

## Sensitive-data review

- [ ] No credentials, tokens, cookies, OAuth state, private keys, or `.env` files.
- [ ] No session transcripts, runtime registries, receipts, logs, captures, or databases.
- [ ] No personal paths, private repository URLs, session IDs, or project IDs.
- [ ] `manifest.sha256` was regenerated and validates.
- [ ] I reviewed the staged diff and `SECURITY.md` checklist.

## Documentation

- [ ] README/protocol/docs/changelog updated, or not applicable with explanation.

# Contributing

1. Preserve fail-closed behavior. Do not weaken app/PID, dirty-worktree, shared-cwd, owner-gate, or split-brain guards.
2. Add a regression test for every behavior change.
3. Keep scripts deterministic; watchdog code must not call an LLM or create sessions.
4. Never add live workspace runtime/session data or machine-specific identities.
5. Run:

```bash
python3 -m py_compile scripts/*.py
mkdir -p "$HOME/.craft-agent/scripts"
cp scripts/* "$HOME/.craft-agent/scripts/"
chmod 700 "$HOME/.craft-agent/scripts/"*.py "$HOME/.craft-agent/scripts/"*.sh
(cd tests && python3 -m unittest -v test_worker_reliability.py test_orchestration_v31.py)
shasum -a 256 -c manifest.sha256
```

6. Review the staged file list and sensitive-data scan described in `SECURITY.md`.
7. Explain safety invariants and rollback in the pull request.

Do not add a license or change redistribution terms without repository-owner approval.

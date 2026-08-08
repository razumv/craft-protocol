# Contributing to Craft Protocol

Thank you for helping make multi-agent orchestration safer and more reliable.

By submitting a contribution, you agree that it is licensed under the Apache License 2.0.

## Ways to contribute

- report a reproducible bug;
- propose a protocol invariant or failure mode;
- improve portability beyond macOS;
- add adversarial regression tests;
- improve docs, installation, observability, or security;
- contribute integrations without bundling credentials or private data.

Use GitHub Discussions for open-ended design questions and Issues for actionable defects/features.

## Ground rules

1. Preserve fail-closed behavior. Do not weaken app/PID, dirty-worktree, shared-cwd, owner-gate, split-brain, exact-SHA, or archive-first guards.
2. Add a regression test for every behavior change.
3. Keep watchdog/runtime tools deterministic. They must not call an LLM, create sessions, or decide owner questions.
4. Never add live workspace runtime/session data, credentials, customer/provider data, or machine-specific identities.
5. Keep changes focused. Protocol redesigns should begin as a Discussion or design Issue.
6. Be respectful and follow `CODE_OF_CONDUCT.md`.

## Development setup

Requirements: macOS, Python 3, Git, zsh, and standard macOS process tools.

```bash
git clone https://github.com/razumv/craft-protocol.git
cd craft-protocol
./install.sh                 # dry-run only
python3 -m py_compile scripts/*.py
```

Run tests against an isolated home:

```bash
TMPHOME=$(mktemp -d)
mkdir -p "$TMPHOME/.craft-agent/scripts"
find scripts -maxdepth 1 -type f -exec cp -p {} "$TMPHOME/.craft-agent/scripts/" \;
chmod 700 "$TMPHOME/.craft-agent/scripts/"*.py "$TMPHOME/.craft-agent/scripts/"*.sh
(cd tests && HOME="$TMPHOME" python3 -m unittest -v \
  test_worker_reliability.py test_orchestration_v31.py)
rm -rf "$TMPHOME"
```

Other checks:

```bash
zsh -n install.sh scripts/watchdog-cron.sh
plutil -lint config/launchd.watchdog.template.plist
python3 -m json.tool config/labels.config.json >/dev/null
./tools/generate-manifest.sh --check
git diff --check
```

## Pull request process

1. Fork the repository and create a descriptive branch.
2. Add tests and documentation with the implementation.
3. Regenerate `manifest.sha256` for the distributable protocol payload.
4. Run the full validation suite.
5. Review the sensitive-data checklist in `SECURITY.md`.
6. Open a pull request using the template.
7. Resolve review and CI findings without force-pushing over other contributors’ work.
8. A maintainer merges after required CI and review pass.

### Regenerate the manifest

```bash
./tools/generate-manifest.sh --write
./tools/generate-manifest.sh --check
```

The manifest intentionally excludes `.github` and community-only repository metadata; CI validates those files separately. This keeps Dependabot workflow updates compatible with package integrity checks.

## Commit and PR guidance

- Explain the observed failure, not just the proposed implementation.
- State which invariant changes or remains preserved.
- Include rollback and migration implications.
- Cite exact tests and results.
- Avoid generated bulk formatting unrelated to the change.
- Prefer squash merge for a clean public history.

## Security reports

Do not file public issues containing vulnerabilities, credentials, or sensitive data. Follow `SECURITY.md` and use a private GitHub security advisory.

## Maintainer expectations

Maintainers may decline changes that increase automation while weakening evidence, preservation, or owner authority. A smaller fail-closed change is preferred over a broad optimistic one.

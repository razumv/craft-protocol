# Security policy

## Supported versions

Security fixes are provided for the latest tagged release and current `main` where practical.

| Version | Supported |
|---|---|
| 3.1.x | Yes |
| Earlier/unreleased protocol snapshots | No |

## Reporting a vulnerability

Use a [private GitHub security advisory](https://github.com/razumv/craft-protocol/security/advisories/new). Do not disclose exploitable details, live secrets, or sensitive data in a public Issue or Discussion.

Include:

- affected version/commit;
- sanitized reproduction;
- safety impact;
- proposed mitigation if known.

Maintainers will acknowledge reports as soon as practical, assess severity, coordinate a fix, and credit reporters who wish to be credited. Public disclosure should wait until a fix or mitigation is available.

## Never commit

- Craft session transcripts or `session.jsonl` files;
- runtime registries, leases, jobs, gates, or receipts from a real workspace;
- credentials, tokens, OAuth state, cookies, MFA data, or `.env` files;
- customer/provider/private datasets;
- browser captures, HAR files, logs, databases, or generated private evidence;
- private repository URLs, project IDs, session IDs, or machine-specific absolute paths.

The repository `.gitignore` blocks common forms, but ignore rules are not a secret scanner. Review every staged diff.

## Before publishing

```bash
git status --short
git diff --cached --stat
git diff --cached
rg -n '/Users/|session.jsonl|Bearer |gh[pousr]_|sk-[A-Za-z0-9]|BEGIN .*PRIVATE KEY|oauth|cookie|password|secret|token' . \
  -g '!SECURITY.md' -g '!.git/**'
```

Run an independent secret scanner such as Gitleaks before tags/releases when available.

If a secret is committed, rotate or revoke it first. Rewriting Git history is not sufficient because clones, forks, logs, and caches may retain it.

## Security design boundaries

The following protections are security-relevant and require explicit review when changed:

- Craft application and non-harness PID guards;
- dirty/unpushed/shared-cwd cleanup refusal;
- owner authority and project HOLD;
- split-brain coordinator refusal;
- exact candidate/audited SHA binding;
- immutable CI/readback evidence;
- runtime file permissions and atomic writes.

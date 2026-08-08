# Security policy

## Never commit

- Craft session transcripts or `session.jsonl` files
- runtime registries, leases, jobs, gates, or receipts from a real workspace
- credentials, tokens, OAuth state, cookies, MFA data, `.env` files
- customer/provider/private datasets
- browser captures, HAR files, logs, databases, or generated private evidence
- private repository URLs, project IDs, session IDs, or machine-specific absolute paths

The repository `.gitignore` blocks common forms, but ignore rules are not a secret scanner. Review every staged diff.

## Before publishing

```bash
git status --short
git diff --cached --stat
git diff --cached
rg -n '/Users/|session.jsonl|Bearer |gh[pousr]_|sk-[A-Za-z0-9]|BEGIN .*PRIVATE KEY|oauth|cookie|password|secret|token' . \
  -g '!SECURITY.md' -g '!.git/**'
```

Run an independent secret scanner when available (for example Gitleaks) before tags/releases.

## Reporting

Open a private GitHub security advisory for suspected credential or sensitive-data exposure. Do not place live secrets in a public issue.

If a secret is committed, rotate/revoke it first. Rewriting Git history is not sufficient because clones and caches may retain it.

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only release closure verifier for a packaged Craft Protocol version.

Closure is evidence, not a release command.  The verifier never tags, pushes,
installs, or calls GitHub.  It verifies local main/tag/manifest/installer facts and
requires a captured GitHub API release record proving both publication and Latest.
It also checks that the shipped operational entry points adopted the exact version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

VERSION = re.compile(r"^\d+\.\d+\.\d+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
ADOPTION_FILES = ("README.md", "docs/CURRENT-DEFAULTS.md", "install.sh", "scripts/coordinator-registry.py",
                  "scripts/coordinator-reconcile.py", "scripts/coordinator-status.py", "scripts/recovery-admission.py",
                  "scripts/lane-admission.py", "scripts/worker-lease.py", "scripts/observable-job.py",
                  "skills/coordinator-lifecycle-protocol/SKILL.md", "skills/worker-completion-protocol/SKILL.md",
                  "skills/self-healing-controller/SKILL.md")


def git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, timeout=30)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def read_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def manifest_errors(repo: Path) -> list[str]:
    path = repo / "manifest.sha256"
    if not path.is_file(): return ["manifest-missing"]
    try:
        result = subprocess.run(["shasum", "-a", "256", "-c", str(path)], cwd=repo, text=True,
                                capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ["manifest-unverifiable"]
    return [] if result.returncode == 0 else ["manifest-mismatch"]


def installer_errors(repo: Path, version: str) -> list[str]:
    installer = repo / "install.sh"
    if not installer.is_file(): return ["installer-missing"]
    text = installer.read_text(encoding="utf-8", errors="ignore")
    if f"v{version}" not in text: return ["installer-version-mismatch"]
    try:
        result = subprocess.run(["zsh", str(installer)], cwd=repo, text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ["installer-dry-run-unavailable"]
    if result.returncode != 0: return ["installer-dry-run-failed"]
    if "No files changed." not in result.stdout: return ["installer-dry-run-not-proven"]
    return []


def adoption_errors(repo: Path, version: str) -> list[str]:
    missing = []
    for rel in ADOPTION_FILES:
        path = repo / rel
        if not path.is_file() or version not in path.read_text(encoding="utf-8", errors="ignore"):
            missing.append(rel)
    return ["version-adoption-missing:" + ",".join(missing)] if missing else []


def github_errors(release: dict[str, Any] | None, tag: str, tag_sha: str) -> list[str]:
    if not release: return ["github-release-evidence-missing"]
    errors = []
    if release.get("source") != "github-api": errors.append("github-release-source-invalid")
    if release.get("tagName") != tag: errors.append("github-release-tag-mismatch")
    if release.get("targetCommit") != tag_sha: errors.append("github-release-target-mismatch")
    if release.get("isDraft") is not False: errors.append("github-release-draft-or-unknown")
    if release.get("isPrerelease") is not False: errors.append("github-release-prerelease-or-unknown")
    if release.get("isLatest") is not True: errors.append("github-release-not-latest")
    if not isinstance(release.get("publishedAt"), str) or not release["publishedAt"].strip(): errors.append("github-release-published-at-missing")
    url = release.get("htmlUrl")
    if not isinstance(url, str) or not url.startswith("https://github.com/"): errors.append("github-release-url-invalid")
    return errors


def verify(repo: Path, version: str, release: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[str] = []
    if not VERSION.fullmatch(version):
        return {"closed": False, "version": version, "errors": ["version-invalid"]}
    if not (repo / ".git").exists():
        return {"closed": False, "version": version, "errors": ["repository-unreadable"]}
    tag = f"v{version}"
    code, main_sha = git(repo, "rev-parse", "refs/heads/main")
    if code or not SHA.fullmatch(main_sha):
        errors.append("main-missing-or-unreadable"); main_sha = ""
    code, tag_sha = git(repo, "rev-parse", f"{tag}^{{commit}}")
    if code or not SHA.fullmatch(tag_sha):
        errors.append("tag-missing-or-unreadable"); tag_sha = ""
    elif main_sha and git(repo, "merge-base", "--is-ancestor", tag_sha, "refs/heads/main")[0] != 0:
        errors.append("tag-not-reachable-from-main")
    errors += manifest_errors(repo)
    errors += installer_errors(repo, version)
    errors += adoption_errors(repo, version)
    if SHA.fullmatch(tag_sha or ""):
        errors += github_errors(release, tag, tag_sha)
    else:
        errors.append("github-release-uncheckable-without-tag")
    return {"closed": not errors, "version": version, "tag": tag, "mainSha": main_sha or None,
            "tagSha": tag_sha or None, "errors": sorted(set(errors)),
            "checks": {"main": "main-missing-or-unreadable" not in errors,
                       "tag": "tag-missing-or-unreadable" not in errors and "tag-not-reachable-from-main" not in errors,
                       "manifest": not any(x.startswith("manifest-") for x in errors),
                       "installer": not any(x.startswith("installer-") for x in errors),
                       "githubReleaseLatest": not any(x.startswith("github-release-") for x in errors),
                       "adoption": not any(x.startswith("version-adoption-") for x in errors)}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify"); v.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    v.add_argument("--version", required=True); v.add_argument("--github-release-file", required=True)
    args = parser.parse_args()
    result = verify(Path(args.repo).expanduser().resolve(), args.version, read_json(args.github_release_file))
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["closed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

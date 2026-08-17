#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed, read-only release closure verifier.

Closure trusts neither local refs nor caller-selected commands or endpoints.  It
uses the GitHub REST API over verified TLS at the fixed api.github.com authority,
with an explicit, permission-restricted token file.  It never prints credentials,
tags, pushes, installs, or mutates runtime state.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, stat, subprocess
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

VERSION = re.compile(r"^\d+\.\d+\.\d+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
GITHUB_ORIGIN = re.compile(r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
RECEIPT_FENCE = re.compile(r"^```json[ \t]*\r?\n(.*?)\r?\n```[ \t]*$", re.MULTILINE | re.DOTALL)
API_HOST = "https://api.github.com"


def run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=60)
        return proc.returncode, proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def git(repo: Path, *args: str) -> tuple[int, str]:
    return run(["git", "-C", str(repo), *args])


def repo_identity(repo: Path) -> str | None:
    """Accept only canonical GitHub origin syntax; no configured API override exists."""
    code, url = git(repo, "remote", "get-url", "origin")
    if code:
        return None
    match = GITHUB_ORIGIN.fullmatch(url.strip())
    return match.group(1) if match else None


def token_from_file(raw: str | None) -> str | None:
    """Read an explicit secret without accepting environment/CLI token material."""
    if not raw:
        return None
    try:
        path = Path(raw).expanduser().resolve()
        info = path.stat()
        if (not path.is_file() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            return None
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return token if 1 <= len(token) <= 4096 and "\x00" not in token else None


def api(token: str, endpoint: str) -> dict[str, Any] | None:
    """Authenticated request to the fixed GitHub API host; failures are opaque."""
    if not endpoint.startswith("repos/") or ".." in endpoint:
        return None
    request = Request(f"{API_HOST}/{endpoint}", headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "User-Agent": "craft-protocol-release-closure/3.4.38",
    })
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310: fixed HTTPS host
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (HTTPError, URLError, OSError, UnicodeError, json.JSONDecodeError):
        return None


def decode_github_content(content: str) -> bytes | None:
    """Decode GitHub Contents base64 without treating arbitrary Unicode as space."""
    # GitHub legitimately wraps Contents responses.  Remove only ASCII whitespace;
    # any other code point remains invalid input to the strict base64 decoder.
    ascii_whitespace = {" ", chr(9), chr(10), chr(11), chr(12), chr(13)}
    compact = "".join(ch for ch in content if ch not in ascii_whitespace)
    if len(compact) + sum(ch in ascii_whitespace for ch in content) != len(content):
        return None
    try:
        return base64.b64decode(compact, validate=True)
    except (ValueError, TypeError, base64.binascii.Error):
        return None


def remote_file(token: str, identity: str, rel: str, ref: str) -> bytes | None:
    row = api(token, f"repos/{identity}/contents/{quote(rel)}?ref={quote(ref, safe='')}")
    if not row or row.get("type") != "file" or not isinstance(row.get("content"), str):
        return None
    return decode_github_content(row["content"])


def remote_sha256(token: str, identity: str, rel: str, ref: str) -> str | None:
    value = remote_file(token, identity, rel, ref)
    return hashlib.sha256(value).hexdigest() if value is not None else None


def valid_manifest_path(path: str) -> bool:
    if not path or chr(0) in path or "\\" in path:
        return False
    pure = PurePosixPath(path)
    return (not pure.is_absolute() and path == pure.as_posix()
            and all(part not in {"", ".", ".."} for part in pure.parts))


def parse_manifest(raw: bytes) -> dict[str, str] | None:
    try:
        rows = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    entries: dict[str, str] = {}
    for line in rows:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or not valid_manifest_path(match.group(2)) or match.group(2) in entries:
            return None
        entries[match.group(2)] = match.group(1)
    return entries or None


def remote_manifest_errors(repo: Path, token: str, identity: str, tag: str) -> list[str]:
    local = parse_manifest((repo / "manifest.sha256").read_bytes()) if (repo / "manifest.sha256").is_file() else None
    if local is None:
        return ["local-manifest-invalid"]
    raw = remote_file(token, identity, "manifest.sha256", tag)
    if raw is None:
        return ["remote-manifest-missing"]
    remote = parse_manifest(raw)
    if remote is None:
        return ["remote-manifest-invalid"]
    if remote != local:
        return ["remote-manifest-coverage-mismatch"]
    bad = [path for path, digest in local.items() if remote_sha256(token, identity, path, tag) != digest]
    return ["remote-manifest-mismatch:" + ",".join(bad[:4])] if bad else []


def installer_errors(repo: Path, version: str) -> list[str]:
    path = repo / "install.sh"
    if not path.is_file() or f"v{version}" not in path.read_text(errors="ignore"):
        return ["installer-version-mismatch"]
    code, out = run(["zsh", str(path)], repo)
    return [] if code == 0 and "No files changed." in out else ["installer-dry-run-not-proven"]


def no_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def release_receipt(body: Any) -> dict[str, Any] | None:
    """Extract exactly one strict JSON receipt from the authenticated Release body."""
    if not isinstance(body, str):
        return None
    matches = RECEIPT_FENCE.findall(body)
    if len(matches) != 1:
        return None
    try:
        value = json.loads(matches[0], object_pairs_hook=no_duplicate_json_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def canonical_adoptions(value: Any) -> bool:
    """Require an ordered, unique project/coordinator roster, not a prose claim."""
    if not isinstance(value, list) or not value:
        return False
    pairs: list[tuple[str, str]] = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"project", "coordinatorSessionId"}:
            return False
        project, coordinator = row.get("project"), row.get("coordinatorSessionId")
        if not nonempty_string(project) or not nonempty_string(coordinator):
            return False
        pairs.append((project, coordinator))
    return (pairs == sorted(pairs) and len({project for project, _ in pairs}) == len(pairs)
            and len({coordinator for _, coordinator in pairs}) == len(pairs))


def adoption_errors(release_body: Any, tag: str, version: str, tag_sha: str) -> list[str]:
    """Validate the post-rollout Release receipt without changing the tagged tree."""
    value = release_receipt(release_body)
    if value is None:
        return ["fleet-adoption-receipt-missing-or-invalid"]
    required = {"schemaVersion": 1, "version": version, "tag": tag, "commit": tag_sha, "state": "adopted"}
    expected_keys = {*required, "ownerFacingOrchestratorSessionId", "adoptedAt", "adoptions"}
    if (set(value) != expected_keys or not all(value.get(key) == expected for key, expected in required.items())
            or not nonempty_string(value.get("ownerFacingOrchestratorSessionId"))
            or not nonempty_string(value.get("adoptedAt")) or not canonical_adoptions(value.get("adoptions"))):
        return ["fleet-adoption-receipt-mismatch"]
    return []


def asset_digest(release: dict[str, Any], name: str) -> str | None:
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == name and isinstance(asset.get("digest"), str):
            return asset["digest"]
    return None


def remote_refs(token: str, identity: str, tag: str) -> tuple[str | None, str | None, str | None]:
    main = api(token, f"repos/{identity}/git/ref/heads/main")
    tag_ref = api(token, f"repos/{identity}/git/ref/tags/{quote(tag, safe='')}")
    main_sha = ((main or {}).get("object") or {}).get("sha")
    tag_object = ((tag_ref or {}).get("object") or {})
    tag_object_sha = tag_object.get("sha")
    if tag_object.get("type") != "tag" or not isinstance(tag_object_sha, str):
        return main_sha if isinstance(main_sha, str) else None, None, None
    tag_object_row = api(token, f"repos/{identity}/git/tags/{tag_object_sha}")
    peeled = ((tag_object_row or {}).get("object") or {})
    tag_sha = peeled.get("sha") if peeled.get("type") == "commit" else None
    return main_sha if isinstance(main_sha, str) else None, tag_object_sha, tag_sha if isinstance(tag_sha, str) else None


def verify(repo: Path, version: str, token_file: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    tag = f"v{version}"
    if not VERSION.fullmatch(version):
        return {"closed": False, "version": version, "errors": ["version-invalid"]}
    identity = repo_identity(repo)
    token = token_from_file(token_file)
    if not identity:
        errors.append("github-origin-identity-unreadable")
    if not token:
        errors.append("github-auth-token-file-unavailable")
    if not identity or not token:
        return {"closed": False, "version": version, "tag": tag, "repository": identity,
                "remoteMainSha": None, "tagSha": None, "errors": errors}
    main_sha, tag_object_sha, tag_sha = remote_refs(token, identity, tag)
    if not SHA.fullmatch(main_sha or ""):
        errors.append("remote-main-unreadable")
    if not SHA.fullmatch(tag_object_sha or "") or not SHA.fullmatch(tag_sha or ""):
        errors.append("remote-annotated-tag-missing-or-unreadable")
    elif tag_sha != main_sha:
        errors.append("remote-tag-does-not-peel-exactly-to-remote-main")
    release = api(token, f"repos/{identity}/releases/tags/{tag}")
    latest = api(token, f"repos/{identity}/releases/latest")
    final_release = False
    if not release:
        errors.append("github-release-object-missing")
    else:
        if release.get("tag_name") != tag or release.get("draft") is not False or release.get("prerelease") is not False:
            errors.append("github-release-object-mismatch")
        if not release.get("published_at"):
            errors.append("github-release-freshness-missing")
        if not latest or latest.get("id") != release.get("id") or latest.get("tag_name") != tag:
            errors.append("github-release-not-latest")
        if release.get("target_commitish") not in {tag_sha, "main"}:
            errors.append("github-release-target-mismatch")
        final_release = (release.get("tag_name") == tag and release.get("draft") is False
                         and release.get("prerelease") is False and bool(release.get("published_at"))
                         and bool(latest) and latest.get("id") == release.get("id")
                         and latest.get("tag_name") == tag)
        for name in ("manifest.sha256", "install.sh"):
            digest = remote_sha256(token, identity, name, tag)
            if not digest or asset_digest(release, name) != f"sha256:{digest}":
                errors.append(f"github-release-asset-hash-mismatch:{name}")
    if tag_sha:
        errors += remote_manifest_errors(repo, token, identity, tag)
        if final_release:
            errors += adoption_errors(release.get("body"), tag, version, tag_sha)
    errors += installer_errors(repo, version)
    return {"closed": not errors, "version": version, "tag": tag, "repository": identity,
            "remoteMainSha": main_sha if SHA.fullmatch(main_sha or "") else None,
            "tagSha": tag_sha if SHA.fullmatch(tag_sha or "") else None,
            "errors": sorted(set(errors))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    verify_parser.add_argument("--version", required=True)
    verify_parser.add_argument("--github-token-file", help="owner-only mode-0600 GitHub token file")
    args = parser.parse_args()
    result = verify(Path(args.repo).expanduser().resolve(), args.version, args.github_token_file)
    print(json.dumps(result, indent=2))
    return 0 if result["closed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

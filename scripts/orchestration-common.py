#!/opt/homebrew/bin/python3
"""Shared deterministic primitives for orchestration runtime tools."""
from __future__ import annotations
import contextlib
import fcntl
import glob
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Iterator

HOME = Path.home()
WORKSPACE = Path(os.environ.get("CRAFT_WORKSPACE", HOME / ".craft-agent/workspaces/general")).expanduser()
SESSIONS = Path(os.environ.get("CRAFT_SESSIONS", WORKSPACE / "sessions")).expanduser()
RUNTIME = Path(os.environ.get("CRAFT_RUNTIME", HOME / ".craft-agent/runtime")).expanduser()


def now_ms() -> int:
    return int(time.time() * 1000)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def manifest_path(session_id: str) -> Path:
    return SESSIONS / session_id / "session.jsonl"


def read_manifest(session_id: str) -> dict[str, Any] | None:
    try:
        with manifest_path(session_id).open(encoding="utf-8", errors="ignore") as fh:
            value = json.loads(fh.readline())
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def all_manifests() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in glob.glob(str(SESSIONS / "*" / "session.jsonl")):
        try:
            with open(raw, encoding="utf-8", errors="ignore") as fh:
                value = json.loads(fh.readline())
            if isinstance(value, dict) and value.get("id"):
                result[str(value["id"])] = value
        except Exception:
            continue
    return result


def label_value(manifest: dict[str, Any], prefix: str) -> str | None:
    for label in manifest.get("labels") or []:
        if isinstance(label, str) and label.startswith(prefix):
            return label.split("::", 1)[1]
    return None


def role_of(manifest: dict[str, Any]) -> str:
    return label_value(manifest, "agent-role::") or "unknown"


def session_live(manifest: dict[str, Any] | None) -> bool:
    return bool(manifest and not manifest.get("isArchived") and manifest.get("sessionStatus") not in {"done", "cancelled"})


def project_of(manifest: dict[str, Any]) -> str | None:
    return label_value(manifest, "project::") or label_value(manifest, "work-scope::") or manifest.get("projectId")


def expand_path(raw: str | None) -> str | None:
    return str(Path(raw).expanduser().resolve()) if raw else None

"""PID registry stored in ~/.ipc/registry.json."""

import contextlib
import json
import os
import sys
from datetime import datetime, timezone

from ._dir import registry_path


# ── inter-process file lock (Unix only; no-op on Windows) ─────────────────────

@contextlib.contextmanager
def _lock():
    """Hold an exclusive lock on the registry while reading+writing."""
    if sys.platform == "win32":
        yield  # no portable locking on Windows; race is acceptable there
        return
    import fcntl
    lock_path = registry_path().with_suffix(".lock")
    lock_path.parent.mkdir(mode=0o700, exist_ok=True)
    with open(lock_path, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ── internal helpers ──────────────────────────────────────────────────────────

def _load() -> dict:
    p = registry_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(data: dict) -> None:
    registry_path().write_text(json.dumps(data, indent=2))


# ── public API ────────────────────────────────────────────────────────────────

def register(
    pid: int,
    label: str,
    script: str,
    language: str = "python",
    ppid: int | None = None,
) -> None:
    with _lock():
        data = _load()
        entry: dict = {
            "pid": pid,
            "label": label,
            "script": script,
            "language": language,
            "start_time": datetime.now(timezone.utc).isoformat(),
        }
        if ppid is not None:
            entry["ppid"] = ppid
        data[str(pid)] = entry
        _save(data)


def get_entry(pid: int) -> dict | None:
    return _load().get(str(pid))


def deregister(pid: int) -> None:
    with _lock():
        data = _load()
        data.pop(str(pid), None)
        _save(data)


def all_entries() -> list[dict]:
    return list(_load().values())


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def clean_stale() -> list[int]:
    """Remove entries whose process is no longer running. Returns removed PIDs."""
    with _lock():
        data = _load()
        removed = [int(k) for k in data if not is_alive(int(k))]
        for pid in removed:
            data.pop(str(pid), None)
        _save(data)
    return removed

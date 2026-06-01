"""Per-process state file in ~/.ipc/<pid>.state.json."""

import json
import os
from datetime import datetime, timezone

from ._dir import state_path


def write_state(pid: int, iteration: int, total: int | None,
                start_ts: float, tracked: dict) -> None:
    elapsed = datetime.now(timezone.utc).timestamp() - start_ts
    eta = None
    if total and iteration > 0:
        eta = elapsed / iteration * (total - iteration)

    payload = {
        "pid": pid,
        "iteration": iteration,
        "total": total,
        "elapsed_sec": round(elapsed, 1),
        "eta_sec": round(eta, 1) if eta is not None else None,
        "tracked": tracked,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    state_path(pid).write_text(json.dumps(payload, indent=2))


def read_state(pid: int) -> dict | None:
    p = state_path(pid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def remove_state(pid: int) -> None:
    p = state_path(pid)
    if p.exists():
        p.unlink(missing_ok=True)

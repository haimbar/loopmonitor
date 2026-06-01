"""
Tests for multiprocess (forked) workers monitored by loopmonitor.

Three child processes are forked; the test verifies that:
  1. All three register in the shared registry (with correct PPID).
  2. Each state file is readable and shows real progress.
  3. `ipc list` shows all three PIDs; `ipc list --group <ppid>` filters them.
  4. `ipc peek all` prints a summary for each process.
  5. `ipc continue all` stops every worker cleanly.
  6. Label-glob targeting (`ipc continue 'worker-*'`) also works.

Design notes:
- Uses multiprocessing "fork" context so children inherit the monkeypatched
  IPC_DIR without extra setup in the worker function.
- Each test kills surviving workers in a finally block.
- Skipped on Windows where os.fork() is unavailable.
"""

import os
import subprocess
import sys
import time

import pytest

from loopmonitor._registry import all_entries, is_alive
from loopmonitor._state import read_state

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="fork() not available on Windows"
)

# ── worker entry-point ────────────────────────────────────────────────────────

def _worker(worker_id: int, n_steps: int, delay: float) -> None:
    """Run inside a forked child: monitored loop with synthetic loss."""
    import math
    from loopmonitor import ipc_range

    loss = 1.0 - worker_id * 0.05
    for step in ipc_range(n_steps, label=f"worker-{worker_id}"):
        time.sleep(delay)
        loss *= 1.0 - 0.01 * (1.0 + 0.1 * math.sin(step.index * 0.3))
        loss = max(1e-6, loss)
        step.track(loss=round(loss, 6), worker=worker_id)


# ── shared helpers ────────────────────────────────────────────────────────────

def _start_workers(n: int = 3, n_steps: int = 200, delay: float = 0.1):
    ctx = __import__("multiprocessing").get_context("fork")
    procs = []
    for i in range(n):
        p = ctx.Process(target=_worker, args=(i, n_steps, delay), daemon=True)
        p.start()
        procs.append(p)
    return procs


def _wait_all_registered(pids: set, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive_registered = {e["pid"] for e in all_entries() if is_alive(e["pid"])}
        if pids.issubset(alive_registered):
            return True
        time.sleep(0.25)
    return False


def _kill_all(procs) -> None:
    for p in procs:
        if p.is_alive():
            p.kill()
    for p in procs:
        p.join(timeout=5)


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "loopmonitor.cli", *args],
        capture_output=True, text=True,
    )


# ── tests ─────────────────────────────────────────────────────────────────────

def test_three_workers_register_with_ppid(ipc_tmp_dir):
    """All 3 workers appear in the registry with correct language and PPID."""
    procs = _start_workers()
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)

        parent_pid = os.getpid()
        entries = {e["pid"]: e for e in all_entries() if e["pid"] in pids}
        assert len(entries) == 3
        for pid, entry in entries.items():
            assert entry["language"] == "python"
            assert "worker-" in entry["label"]
            assert entry.get("ppid") == parent_pid, (
                f"expected ppid={parent_pid}, got {entry.get('ppid')}"
            )
    finally:
        _kill_all(procs)


def test_three_workers_state_files_show_progress(ipc_tmp_dir):
    """Each worker writes a state file with iteration count and tracked values."""
    procs = _start_workers()
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)
        time.sleep(1.5)

        for pid in pids:
            state = read_state(pid)
            assert state is not None, f"No state file for PID {pid}"
            assert state["iteration"] >= 1
            assert "loss" in state["tracked"]
    finally:
        _kill_all(procs)


def test_ipc_list_shows_ppid_column(ipc_tmp_dir):
    """`ipc list` includes a PPID column when workers store their parent PID."""
    procs = _start_workers()
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)

        r = _cli("list")
        assert r.returncode == 0
        assert "PPID" in r.stdout
        parent_pid = str(os.getpid())
        assert parent_pid in r.stdout
        for pid in pids:
            assert str(pid) in r.stdout
    finally:
        _kill_all(procs)


def test_ipc_list_group_filters_by_ppid(ipc_tmp_dir):
    """`ipc list --group <ppid>` shows only processes with that parent PID."""
    procs = _start_workers()
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)

        parent_pid = os.getpid()
        r = _cli("list", "--group", str(parent_pid))
        assert r.returncode == 0
        # All 3 workers must appear.
        for pid in pids:
            assert str(pid) in r.stdout
        # A fictitious unrelated PPID should yield nothing.
        r2 = _cli("list", "--group", "1")
        assert "No processes" in r2.stdout or all(
            str(pid) not in r2.stdout for pid in pids
        )
    finally:
        _kill_all(procs)


def test_ipc_peek_all_shows_all_workers(ipc_tmp_dir):
    """`ipc peek all` prints a state-file summary for each registered process."""
    procs = _start_workers()
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)
        time.sleep(1.0)

        r = _cli("peek", "all")
        assert r.returncode == 0
        # Each worker's PID must appear in the output.
        for pid in pids:
            assert str(pid) in r.stdout, (
                f"PID {pid} missing from `ipc peek all`:\n{r.stdout}"
            )
        # The output should contain each worker's label.
        for i in range(3):
            assert f"worker-{i}" in r.stdout
    finally:
        _kill_all(procs)


def test_ipc_peek_label_glob(ipc_tmp_dir):
    """`ipc peek 'worker-*'` matches all three workers by label."""
    procs = _start_workers()
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)
        time.sleep(0.5)

        r = _cli("peek", "worker-*")
        assert r.returncode == 0
        for pid in pids:
            assert str(pid) in r.stdout
    finally:
        _kill_all(procs)


def test_ipc_continue_all_stops_workers(ipc_tmp_dir):
    """`ipc continue all` causes every worker to exit cleanly."""
    procs = _start_workers(n_steps=500, delay=0.1)
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)

        r = _cli("continue", "all")
        assert r.returncode == 0
        # One confirmation line per worker.
        assert r.stdout.count("'continue' sent") == 3

        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if all(not p.is_alive() for p in procs):
                break
            time.sleep(0.3)

        for p in procs:
            assert not p.is_alive(), f"Worker {p.pid} still alive after broadcast continue"
    finally:
        _kill_all(procs)


def test_ipc_continue_label_glob_stops_workers(ipc_tmp_dir):
    """`ipc continue 'worker-*'` stops all matching workers."""
    procs = _start_workers(n_steps=500, delay=0.1)
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)

        r = _cli("continue", "worker-*")
        assert r.returncode == 0
        assert r.stdout.count("'continue' sent") == 3

        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if all(not p.is_alive() for p in procs):
                break
            time.sleep(0.3)
        for p in procs:
            assert not p.is_alive()
    finally:
        _kill_all(procs)


def test_ipc_set_all_injects_value(ipc_tmp_dir):
    """`ipc set all lr=0.001` injects the value into every worker's context."""
    import math
    import multiprocessing as mp

    results: dict = mp.Manager().dict()

    def _worker_set(worker_id, n_steps, delay, results):
        from loopmonitor import ipc_range
        for step in ipc_range(n_steps, label=f"worker-{worker_id}"):
            time.sleep(delay)
            lr = step.get("lr", default=None)
            if lr is not None:
                results[worker_id] = lr
                break  # got the injected value; exit cleanly

    ctx = mp.get_context("fork")
    mgr = ctx.Manager()
    shared = mgr.dict()
    procs = []
    for i in range(3):
        p = ctx.Process(target=_worker_set, args=(i, 200, 0.05, shared), daemon=True)
        p.start()
        procs.append(p)

    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)

        r = _cli("set", "all", "lr=0.001")
        assert r.returncode == 0

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and len(shared) < 3:
            time.sleep(0.2)

        assert len(shared) == 3, f"Only {len(shared)} workers received the injected lr"
        for val in shared.values():
            assert val == pytest.approx(0.001)
    finally:
        _kill_all(procs)
        mgr.shutdown()


def test_ipc_peek_ppid_shows_all_workers(ipc_tmp_dir):
    """`ipc peek <ppid>` shows state for all children of that parent."""
    procs = _start_workers()
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)
        time.sleep(1.0)

        parent_pid = os.getpid()
        r = _cli("peek", str(parent_pid))
        assert r.returncode == 0
        for pid in pids:
            assert str(pid) in r.stdout, (
                f"PID {pid} missing from `ipc peek <ppid>`:\n{r.stdout}"
            )
        for i in range(3):
            assert f"worker-{i}" in r.stdout
    finally:
        _kill_all(procs)


def test_ipc_continue_ppid_stops_workers(ipc_tmp_dir):
    """`ipc continue <ppid>` fans out to all children of that parent."""
    procs = _start_workers(n_steps=500, delay=0.1)
    try:
        pids = {p.pid for p in procs}
        assert _wait_all_registered(pids)

        parent_pid = os.getpid()
        r = _cli("continue", str(parent_pid))
        assert r.returncode == 0
        assert r.stdout.count("'continue' sent") == 3

        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if all(not p.is_alive() for p in procs):
                break
            time.sleep(0.3)
        for p in procs:
            assert not p.is_alive(), f"Worker {p.pid} still alive after ppid-targeted continue"
    finally:
        _kill_all(procs)

"""
Shared fixtures for the loopmonitor test suite.

Key design decisions:
- ipc_tmp_dir (autouse): every test gets its own ~/.ipc equivalent in a
  temporary directory, so tests never touch the real registry or leave
  stale FIFOs on disk.
- delayed_command: sends a FIFO command from a background thread after a
  short delay, used by all tests that drive a live ipc_range loop.
"""

import os
import signal
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def ipc_tmp_dir(tmp_path, monkeypatch):
    """Redirect all IPC paths to a per-test temp directory."""
    import loopmonitor._dir as _dir
    d = tmp_path / "ipc"
    monkeypatch.setattr(_dir, "IPC_DIR", d)
    # Also expose as env var so CLI subprocesses and forked children use the
    # same temp dir (they initialize IPC_DIR from LOOPCTL_DIR at import time).
    monkeypatch.setenv("LOOPCTL_DIR", str(d))
    yield d


@pytest.fixture
def delayed_command():
    """
    Return a factory(pid, cmd, delay=0.15) that writes *cmd* to the process
    FIFO and sends SIGUSR1 after *delay* seconds, from a daemon thread.

    The thread waits up to 5 s for the FIFO to appear (the loop may not have
    started yet when the thread is spawned).  Returns the thread so callers
    can join it after the loop exits.
    """
    from loopmonitor._dir import fifo_path

    def factory(pid: int, cmd: str, delay: float = 0.15) -> threading.Thread:
        fp = str(fifo_path(pid))

        def _go() -> None:
            time.sleep(delay)
            deadline = time.time() + 5.0
            while not os.path.exists(fp) and time.time() < deadline:
                time.sleep(0.005)
            if not os.path.exists(fp):
                return
            fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, f"{cmd}\n".encode())
            finally:
                os.close(fd)
            os.kill(pid, signal.SIGUSR1)

        t = threading.Thread(target=_go, daemon=True)
        t.start()
        return t

    return factory

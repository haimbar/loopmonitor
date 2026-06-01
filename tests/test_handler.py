"""
Tests for _handler.py: FIFO lifecycle, signal dispatch, and cleanup.

Signal-based tests run the handler in the main thread (Python's signal
delivery requirement) and drive it from a daemon thread.
"""

import os
import signal
import stat
import threading
import time

import pytest

from loopmonitor._dir import fifo_path, ipc_dir
from loopmonitor._handler import install, uninstall


@pytest.fixture
def installed_handler(ipc_tmp_dir):
    """Install the handler for the current process; uninstall after the test."""
    pid = os.getpid()
    ctx = {"should_stop": False, "tracked": {}}
    install(pid, ctx)
    yield pid, ctx
    uninstall(pid)


# ── install / uninstall lifecycle ────────────────────────────────────────────

def test_install_creates_fifo(ipc_tmp_dir):
    pid = os.getpid()
    ctx = {"should_stop": False, "tracked": {}}
    try:
        install(pid, ctx)
        assert fifo_path(pid).exists()
        assert stat.S_ISFIFO(os.lstat(str(fifo_path(pid))).st_mode)
    finally:
        uninstall(pid)


def test_install_fifo_permissions(ipc_tmp_dir):
    pid = os.getpid()
    ctx = {"should_stop": False, "tracked": {}}
    try:
        install(pid, ctx)
        mode = stat.S_IMODE(os.lstat(str(fifo_path(pid))).st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    finally:
        uninstall(pid)


def test_install_registers_sigusr1_handler(ipc_tmp_dir):
    pid = os.getpid()
    ctx = {"should_stop": False, "tracked": {}}
    try:
        install(pid, ctx)
        assert signal.getsignal(signal.SIGUSR1) not in (
            signal.SIG_DFL, signal.SIG_IGN, None
        )
    finally:
        uninstall(pid)


def test_uninstall_removes_fifo(ipc_tmp_dir):
    pid = os.getpid()
    ctx = {"should_stop": False, "tracked": {}}
    install(pid, ctx)
    uninstall(pid)
    assert not fifo_path(pid).exists()


def test_uninstall_resets_signal_handler(ipc_tmp_dir):
    pid = os.getpid()
    ctx = {"should_stop": False, "tracked": {}}
    install(pid, ctx)
    uninstall(pid)
    assert signal.getsignal(signal.SIGUSR1) == signal.SIG_DFL


def test_install_cleans_stale_fifo(ipc_tmp_dir):
    pid = os.getpid()
    # Pre-create a FIFO to simulate a stale leftover from a crash
    ipc_dir()
    os.mkfifo(str(fifo_path(pid)), 0o600)
    ctx = {"should_stop": False, "tracked": {}}
    try:
        install(pid, ctx)  # must not raise
        assert fifo_path(pid).exists()
    finally:
        uninstall(pid)


# ── command dispatch via signal ───────────────────────────────────────────────

def _send_signal(pid: int, cmd: str, delay: float = 0.05) -> threading.Thread:
    """Write *cmd* to the FIFO and send SIGUSR1 after *delay* seconds."""
    fp = str(fifo_path(pid))

    def _go() -> None:
        time.sleep(delay)
        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, f"{cmd}\n".encode())
        finally:
            os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t


def test_continue_sets_should_stop(ipc_tmp_dir, installed_handler):
    pid, ctx = installed_handler
    t = _send_signal(pid, "continue")
    time.sleep(0.2)   # let the signal land
    t.join(timeout=2)
    assert ctx["should_stop"] is True


def test_peek_prints_status(ipc_tmp_dir, installed_handler, capsys):
    from loopmonitor._state import write_state

    pid, ctx = installed_handler
    write_state(pid, 10, 100, time.time() - 5, {"val": 1.23})

    t = _send_signal(pid, "peek")
    time.sleep(0.2)
    t.join(timeout=2)

    out = capsys.readouterr().out
    assert "[loopmonitor]" in out
    assert "val=1.23" in out


def test_unknown_command_is_ignored(ipc_tmp_dir, installed_handler):
    pid, ctx = installed_handler
    t = _send_signal(pid, "frobulate")   # not a real command
    time.sleep(0.2)
    t.join(timeout=2)
    assert ctx["should_stop"] is False   # no side effects


def test_multiple_commands_in_one_signal(ipc_tmp_dir, installed_handler):
    """Two commands written atomically before the signal fires — both run."""
    pid, ctx = installed_handler

    def _go() -> None:
        time.sleep(0.05)
        fp = str(fifo_path(pid))
        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, b"continue\ncontinue\n")  # two, but idempotent
        finally:
            os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    time.sleep(0.2)
    t.join(timeout=2)
    assert ctx["should_stop"] is True

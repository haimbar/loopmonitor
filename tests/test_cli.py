"""
Tests for the ipc CLI (cli.py).

Unit-level tests mock OS calls to avoid needing a live loop.
Integration-level tests spin up a real ipc_range loop in the main thread.
"""

import os
import signal
import time
import threading

import pytest

from loopmonitor._dir import fifo_path, ipc_dir
from loopmonitor._registry import register
from loopmonitor.cli import main


# ── ipc list ─────────────────────────────────────────────────────────────────

def test_list_no_processes(ipc_tmp_dir, capsys):
    main(["list"])
    assert "No registered processes" in capsys.readouterr().out


def test_list_shows_registered_entry(ipc_tmp_dir, capsys):
    register(os.getpid(), "my training run", "train.py")
    main(["list"])
    out = capsys.readouterr().out
    assert str(os.getpid()) in out
    assert "my training run" in out


def test_list_marks_dead_pid_as_no(ipc_tmp_dir, capsys):
    register(9_999_999, "ghost", "ghost.py")
    main(["list"])
    out = capsys.readouterr().out
    assert "no" in out.lower()


def test_list_marks_current_pid_as_yes(ipc_tmp_dir, capsys):
    register(os.getpid(), "alive", "me.py")
    main(["list"])
    out = capsys.readouterr().out
    assert "yes" in out.lower()


# ── ipc clean ────────────────────────────────────────────────────────────────

def test_clean_nothing_to_remove(ipc_tmp_dir, capsys):
    main(["clean"])
    assert "clean" in capsys.readouterr().out.lower()


def test_clean_removes_dead_entries(ipc_tmp_dir, capsys):
    register(9_999_999, "dead", "x.py")
    main(["clean"])
    out = capsys.readouterr().out
    assert "9999999" in out


# ── commands with nonexistent PID ─────────────────────────────────────────────

@pytest.mark.parametrize("cmd", ["peek", "plot", "continue", "break"])
def test_command_exits_1_for_dead_pid(ipc_tmp_dir, cmd):
    with pytest.raises(SystemExit) as exc_info:
        main([cmd, "9999999"])
    assert exc_info.value.code == 1


# ── commands with no FIFO (alive but no loop) ─────────────────────────────────

@pytest.mark.parametrize("cmd", ["peek", "plot", "continue", "break"])
def test_command_exits_1_when_no_fifo(ipc_tmp_dir, cmd):
    """Process is alive but hasn't started ipc_range (no FIFO exists)."""
    with pytest.raises(SystemExit) as exc_info:
        main([cmd, str(os.getpid())])
    assert exc_info.value.code == 1


# ── argument parsing ──────────────────────────────────────────────────────────

def test_missing_subcommand_exits(ipc_tmp_dir):
    with pytest.raises(SystemExit):
        main([])


def test_pid_must_be_integer(ipc_tmp_dir):
    with pytest.raises(SystemExit):
        main(["peek", "not-a-pid"])


# ── _send writes correct command to FIFO ──────────────────────────────────────

def _make_fifo(pid: int) -> None:
    """Create a real FIFO so _send() can open it."""
    ipc_dir()
    fp = str(fifo_path(pid))
    os.mkfifo(fp, 0o600)


def _read_fifo(pid: int) -> str:
    """Open the read end and drain it; must be called after _send writes."""
    fp = str(fifo_path(pid))
    rfd = os.open(fp, os.O_RDONLY | os.O_NONBLOCK)
    try:
        return os.read(rfd, 256).decode().strip()
    finally:
        os.close(rfd)


@pytest.mark.parametrize("cmd", ["peek", "plot", "continue", "break"])
def test_send_writes_correct_command(ipc_tmp_dir, monkeypatch, cmd):
    from loopmonitor.cli import _send

    pid = os.getpid()
    _make_fifo(pid)
    # Open read end so write doesn't block; also lets us read the command back
    rfd = os.open(str(fifo_path(pid)), os.O_RDONLY | os.O_NONBLOCK)

    kill_calls = []
    monkeypatch.setattr(os, "kill", lambda p, s: kill_calls.append((p, s)))

    try:
        _send(pid, cmd)
        data = os.read(rfd, 256).decode().strip()
    finally:
        os.close(rfd)
        fifo_path(pid).unlink(missing_ok=True)

    assert data == cmd
    assert (pid, signal.SIGUSR1) in kill_calls


# ── integration: full loop + CLI continue ────────────────────────────────────

def test_cli_continue_stops_loop(ipc_tmp_dir):
    from loopmonitor import ipc_range

    pid = os.getpid()

    def controller() -> None:
        fp = str(fifo_path(pid))
        deadline = time.time() + 5
        while not os.path.exists(fp) and time.time() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)
        main(["continue", str(pid)])

    t = threading.Thread(target=controller, daemon=True)
    t.start()

    iters = 0
    for step in ipc_range(500, label="cli-test"):
        time.sleep(0.01)
        iters = step.index + 1

    t.join(timeout=3)
    assert iters < 500

"""
Tests for ipc_range and IPCStep.

Signal-based tests (continue, break, peek, plot) always run the loop in the
main thread (Python requires signal handlers to be installed there) and drive
commands from the delayed_command fixture (a daemon thread).
"""

import json
import os
import signal
import threading
import time
from unittest.mock import patch

import pytest

from loopmonitor import IPCStep, ipc_range
from loopmonitor._dir import fifo_path, state_path
from loopmonitor._registry import all_entries
from loopmonitor._state import read_state


# ── basic iteration ───────────────────────────────────────────────────────────

def test_yields_correct_indices(ipc_tmp_dir):
    indices = []
    for step in ipc_range(5, label="t"):
        indices.append(step.index)
    assert indices == [0, 1, 2, 3, 4]


def test_yields_ipc_step_objects(ipc_tmp_dir):
    for step in ipc_range(1, label="t"):
        assert isinstance(step, IPCStep)


def test_empty_range_yields_nothing(ipc_tmp_dir):
    count = sum(1 for _ in ipc_range(0, label="t"))
    assert count == 0


def test_wraps_arbitrary_iterable(ipc_tmp_dir):
    items = ["a", "b", "c"]
    indices = []
    for step in ipc_range(items, label="t"):
        indices.append(step.index)
    assert indices == [0, 1, 2]


def test_total_inferred_from_len(ipc_tmp_dir):
    # __len__ is available for lists; ETA should be non-None after first iter
    pid = os.getpid()
    for step in ipc_range([1, 2, 3], label="t"):
        s = read_state(pid)
        if s and s["iteration"] > 0:
            assert s["total"] == 3
            break


def test_total_none_for_generator(ipc_tmp_dir):
    pid = os.getpid()
    gen = (x for x in range(5))
    for step in ipc_range(gen, label="t"):
        s = read_state(pid)
        if s:
            assert s["total"] is None
            break


# ── step.track() ─────────────────────────────────────────────────────────────

def test_track_values_appear_in_state(ipc_tmp_dir):
    # State is written AFTER each yield returns, so we read it in the *next*
    # iteration (index 1) to see what iteration 0 wrote.
    pid = os.getpid()
    values = {}
    for step in ipc_range(2, label="t"):
        if step.index == 0:
            step.track(x=42, y=99)
        elif step.index == 1:
            s = read_state(pid)
            if s:
                values = dict(s.get("tracked", {}))
    assert values.get("x") == 42
    assert values.get("y") == 99


def test_track_accumulates_across_calls(ipc_tmp_dir):
    pid = os.getpid()
    tracked = {}
    for step in ipc_range(2, label="t"):
        if step.index == 0:
            step.track(a=1)
            step.track(b=2)
        elif step.index == 1:
            s = read_state(pid)
            if s:
                tracked = s["tracked"]
    assert tracked.get("a") == 1
    assert tracked.get("b") == 2


def test_track_overwrites_previous_value(ipc_tmp_dir):
    pid = os.getpid()
    tracked = {}
    for step in ipc_range(2, label="t"):
        if step.index == 0:
            step.track(x=1)
            step.track(x=2)
        elif step.index == 1:
            s = read_state(pid)
            if s:
                tracked = s["tracked"]
    assert tracked["x"] == 2


# ── state_every ──────────────────────────────────────────────────────────────

def test_state_every_limits_writes(ipc_tmp_dir):
    # State is written after iterations where i % state_every == 0.
    # Reading inside the loop body sees the PREVIOUS write, so with
    # state_every=5 over 20 iterations we observe at most ~4 distinct
    # snapshot values — far fewer than 20.
    pid = os.getpid()
    seen = set()
    for step in ipc_range(20, label="t", state_every=5):
        s = read_state(pid)
        if s is not None:
            seen.add(s["iteration"])
    assert len(seen) <= 5


# ── registration and cleanup ─────────────────────────────────────────────────

def test_registers_during_loop(ipc_tmp_dir):
    pid = os.getpid()
    for step in ipc_range(1, label="reg-test"):
        entries = all_entries()
        assert any(e["pid"] == pid for e in entries)


def test_deregisters_after_loop(ipc_tmp_dir):
    pid = os.getpid()
    for step in ipc_range(1, label="t"):
        pass
    assert not any(e["pid"] == pid for e in all_entries())


def test_fifo_removed_after_loop(ipc_tmp_dir):
    pid = os.getpid()
    for step in ipc_range(1, label="t"):
        pass
    assert not fifo_path(pid).exists()


def test_state_file_removed_after_loop(ipc_tmp_dir):
    pid = os.getpid()
    for step in ipc_range(1, label="t"):
        pass
    assert not state_path(pid).exists()


# ── continue signal ───────────────────────────────────────────────────────────

def test_continue_exits_loop_early(ipc_tmp_dir, delayed_command):
    pid = os.getpid()
    t = delayed_command(pid, "continue", delay=0.15)

    iters = 0
    for step in ipc_range(500, label="t"):
        time.sleep(0.01)
        iters = step.index + 1

    t.join(timeout=3)
    assert iters < 500


def test_continue_code_after_loop_runs(ipc_tmp_dir, delayed_command):
    pid = os.getpid()
    t = delayed_command(pid, "continue", delay=0.1)
    after_ran = []

    for step in ipc_range(500, label="t"):
        time.sleep(0.01)

    after_ran.append(True)
    t.join(timeout=3)
    assert after_ran == [True]


def test_continue_cleans_up_fifo(ipc_tmp_dir, delayed_command):
    pid = os.getpid()
    t = delayed_command(pid, "continue", delay=0.1)
    for step in ipc_range(500, label="t"):
        time.sleep(0.01)
    t.join(timeout=3)
    assert not fifo_path(pid).exists()


# ── break signal ─────────────────────────────────────────────────────────────

def test_break_raises_system_exit(ipc_tmp_dir, delayed_command, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    pid = os.getpid()
    t = delayed_command(pid, "break", delay=0.1)

    with pytest.raises(SystemExit) as exc_info:
        for step in ipc_range(500, label="t"):
            time.sleep(0.01)

    t.join(timeout=3)
    assert exc_info.value.code == 0


def test_break_writes_json_snapshot(ipc_tmp_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    pid = os.getpid()

    def controller():
        fp = str(fifo_path(pid))
        sp = str(state_path(pid))
        for path in (fp, sp):
            deadline = time.time() + 5
            while not os.path.exists(path) and time.time() < deadline:
                time.sleep(0.005)
        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, b"break\n")
        os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=controller, daemon=True)
    t.start()

    with pytest.raises(SystemExit):
        for step in ipc_range(500, label="t"):
            step.track(sentinel=7.77)
            time.sleep(0.01)

    t.join(timeout=3)
    files = list(tmp_path.glob("loopmonitor_break_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["tracked"]["sentinel"] == 7.77


# ── peek signal ───────────────────────────────────────────────────────────────

def test_peek_prints_to_stdout(ipc_tmp_dir, capsys):
    pid = os.getpid()

    def two_commands():
        # Wait for FIFO then for at least one state write before peeking,
        # so the output contains the tracked value rather than "No state".
        fp = str(fifo_path(pid))
        sp = str(state_path(pid))
        for path in (fp, sp):
            deadline = time.time() + 5
            while not os.path.exists(path) and time.time() < deadline:
                time.sleep(0.005)
        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, b"peek\ncontinue\n")
        os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=two_commands, daemon=True)
    t.start()

    for step in ipc_range(500, label="peek-test"):
        step.track(loss=0.42)
        time.sleep(0.01)

    t.join(timeout=3)
    out = capsys.readouterr().out
    assert "[loopmonitor]" in out
    assert "loss=0.42" in out


# ── plot signal ───────────────────────────────────────────────────────────────

def test_plot_calls_snapshot(ipc_tmp_dir):
    pid = os.getpid()

    def two_commands():
        # Wait for state to exist so _do_plot has something to render.
        fp = str(fifo_path(pid))
        sp = str(state_path(pid))
        for path in (fp, sp):
            deadline = time.time() + 5
            while not os.path.exists(path) and time.time() < deadline:
                time.sleep(0.005)
        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, b"plot\ncontinue\n")
        os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=two_commands, daemon=True)
    t.start()

    with patch("loopmonitor._plot.snapshot") as mock_snap:
        for step in ipc_range(500, label="plot-test"):
            step.track(x=1.0)
            time.sleep(0.01)

    t.join(timeout=3)
    mock_snap.assert_called_once()


# ── queued commands ───────────────────────────────────────────────────────────

def test_two_rapid_commands_both_delivered(ipc_tmp_dir, capsys):
    """Two commands in one FIFO write are both processed by one SIGUSR1."""
    pid = os.getpid()
    import threading

    def two_peeks_then_continue():
        fp = str(fifo_path(pid))
        deadline = time.time() + 5
        while not os.path.exists(fp) and time.time() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)
        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, b"peek\npeek\ncontinue\n")
        os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=two_peeks_then_continue, daemon=True)
    t.start()

    for step in ipc_range(500, label="queue-test"):
        step.track(v=1)
        time.sleep(0.01)

    t.join(timeout=3)
    # Both peeks should have printed output
    out = capsys.readouterr().out
    assert out.count("[loopmonitor]") >= 2

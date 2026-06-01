"""
Tests for the new commands: set, pause/resume, tail, notify, checkpoint, stack, memory.

Signal-based tests run the loop in the main thread and drive commands from a
daemon thread (Python requires signal handlers to be installed in the main thread).
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
from loopmonitor._state import read_state


# ── ipc set ──────────────────────────────────────────────────────────────────

def test_set_injects_value_into_step(ipc_tmp_dir, delayed_command):
    """step.get() returns a value injected via 'set key=value'."""
    pid = os.getpid()
    t = delayed_command(pid, "set lr=0.001", delay=0.05)

    injected = {}
    for step in ipc_range(200, label="t"):
        v = step.get("lr")
        if v is not None:
            injected["lr"] = v
            break
        time.sleep(0.01)

    t.join(timeout=3)
    assert injected.get("lr") == pytest.approx(0.001)


def test_set_default_returned_when_not_set(ipc_tmp_dir):
    for step in ipc_range(1, label="t"):
        assert step.get("missing", 42) == 42


def test_set_invalid_identifier_does_not_crash(ipc_tmp_dir, delayed_command, capsys):
    pid = os.getpid()
    t = delayed_command(pid, "set 123bad=1", delay=0.05)

    for step in ipc_range(50, label="t"):
        time.sleep(0.01)
        if step.index > 10:
            break

    t.join(timeout=3)
    out = capsys.readouterr().out
    assert "failed" in out.lower() or "invalid" in out.lower()


def test_set_unsafe_expression_rejected(ipc_tmp_dir, delayed_command, capsys):
    """ast.literal_eval must reject arbitrary expressions like __import__."""
    pid = os.getpid()
    t = delayed_command(pid, "set x=__import__('os')", delay=0.05)

    for step in ipc_range(50, label="t"):
        time.sleep(0.01)
        if step.index > 10:
            break

    t.join(timeout=3)
    out = capsys.readouterr().out
    assert "failed" in out.lower()


def test_set_overwrites_previous_value(ipc_tmp_dir, delayed_command):
    pid = os.getpid()
    t1 = delayed_command(pid, "set x=1", delay=0.05)

    seen = []
    for step in ipc_range(500, label="t"):
        v = step.get("x")
        if v is not None:
            seen.append(v)
            if len(seen) >= 1:
                break
        time.sleep(0.01)

    t1.join(timeout=3)
    assert seen[0] == 1


# ── ipc pause / ipc resume ────────────────────────────────────────────────────

def test_cmd_pause_sends_sigstop(ipc_tmp_dir, monkeypatch):
    from loopmonitor.cli import cmd_pause

    kill_calls = []
    monkeypatch.setattr(os, "kill", lambda p, s: kill_calls.append((p, s)))
    monkeypatch.setattr("loopmonitor.cli.is_alive", lambda pid: True)
    # _require_targets looks up the registry, so register a fake entry.
    monkeypatch.setattr(
        "loopmonitor.cli.all_entries",
        lambda: [{"pid": 12345, "label": "t", "language": "python"}],
    )

    class Args:
        pid = "12345"

    cmd_pause(Args())
    assert (12345, signal.SIGSTOP) in kill_calls


def test_cmd_resume_sends_sigcont(ipc_tmp_dir, monkeypatch):
    from loopmonitor.cli import cmd_resume

    kill_calls = []
    monkeypatch.setattr(os, "kill", lambda p, s: kill_calls.append((p, s)))
    monkeypatch.setattr("loopmonitor.cli.is_alive", lambda pid: True)
    monkeypatch.setattr(
        "loopmonitor.cli.all_entries",
        lambda: [{"pid": 12345, "label": "t", "language": "python"}],
    )

    class Args:
        pid = "12345"

    cmd_resume(Args())
    assert (12345, signal.SIGCONT) in kill_calls


def test_cmd_pause_exits_1_for_dead_pid(ipc_tmp_dir):
    from loopmonitor.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["pause", "9999999"])
    assert exc_info.value.code == 1


def test_cmd_resume_exits_1_for_dead_pid(ipc_tmp_dir):
    from loopmonitor.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["resume", "9999999"])
    assert exc_info.value.code == 1


# ── ipc tail ──────────────────────────────────────────────────────────────────

def test_tail_prints_state_updates(ipc_tmp_dir, capsys):
    """cmd_tail polls the state file and prints formatted output."""
    from loopmonitor.cli import cmd_tail
    from loopmonitor._state import write_state
    import time as _time

    pid = 99999
    write_state(pid, 5, 100, _time.time() - 10, {"loss": 0.5})

    call_count = [0]
    original_sleep = _time.sleep

    def fake_sleep(n):
        call_count[0] += 1
        if call_count[0] >= 2:
            raise KeyboardInterrupt

    class Args:
        pid = 99999
        interval = 0.01

    with patch("loopmonitor.cli.is_alive", return_value=True), \
         patch("loopmonitor.cli.time") as mock_time:
        mock_time.sleep.side_effect = fake_sleep

        try:
            cmd_tail(Args())
        except KeyboardInterrupt:
            pass

    out = capsys.readouterr().out
    assert "[loopmonitor]" in out
    assert "loss" in out


def test_tail_stops_when_process_dies(ipc_tmp_dir, capsys):
    from loopmonitor.cli import cmd_tail

    class Args:
        pid = 9999999
        interval = 0.01

    with patch("loopmonitor.cli.read_state", return_value=None), \
         patch("loopmonitor.cli.is_alive", return_value=False):
        cmd_tail(Args())

    out = capsys.readouterr().out
    assert "no longer running" in out


# ── ipc notify ────────────────────────────────────────────────────────────────

def test_notify_fires_when_condition_met(ipc_tmp_dir, capsys):
    from loopmonitor.cli import cmd_notify
    from loopmonitor._state import write_state
    import time as _time

    pid = 99999
    write_state(pid, 10, 100, _time.time() - 5, {"loss": 0.05})

    class Args:
        pid = 99999
        condition = "loss < 0.1"
        interval = 0.01

    with patch("loopmonitor.cli.read_state", return_value={
        "iteration": 10, "total": 100, "elapsed_sec": 5,
        "tracked": {"loss": 0.05}
    }), patch("loopmonitor.cli._desktop_notify") as mock_notify:
        cmd_notify(Args())

    mock_notify.assert_called_once()
    out = capsys.readouterr().out
    assert "Condition met" in out


def test_notify_continues_when_condition_not_met(ipc_tmp_dir, capsys):
    from loopmonitor.cli import cmd_notify

    call_count = [0]

    def fake_sleep(n):
        call_count[0] += 1
        if call_count[0] >= 2:
            raise KeyboardInterrupt

    class Args:
        pid = 99999
        condition = "loss < 0.001"
        interval = 0.01

    with patch("loopmonitor.cli.read_state", return_value={
        "iteration": 1, "total": 100, "elapsed_sec": 1,
        "tracked": {"loss": 0.5}
    }), patch("loopmonitor.cli._desktop_notify") as mock_notify, \
       patch("loopmonitor.cli.time") as mock_time:
        mock_time.sleep.side_effect = fake_sleep
        try:
            cmd_notify(Args())
        except KeyboardInterrupt:
            pass

    mock_notify.assert_not_called()


def test_notify_exits_when_process_dies(ipc_tmp_dir, capsys):
    from loopmonitor.cli import cmd_notify

    class Args:
        pid = 9999999
        condition = "loss < 0.1"
        interval = 0.01

    with patch("loopmonitor.cli.read_state", return_value=None), \
         patch("loopmonitor.cli.is_alive", return_value=False):
        cmd_notify(Args())

    out = capsys.readouterr().out
    assert "no longer running" in out


def test_notify_reports_condition_error(ipc_tmp_dir, capsys):
    from loopmonitor.cli import cmd_notify

    class Args:
        pid = 99999
        condition = "undefined_var + 1"
        interval = 0.01

    with patch("loopmonitor.cli.read_state", return_value={
        "iteration": 1, "total": None, "elapsed_sec": 1,
        "tracked": {}
    }):
        cmd_notify(Args())

    out = capsys.readouterr().out
    assert "error" in out.lower()


# ── ipc checkpoint ────────────────────────────────────────────────────────────

def test_checkpoint_writes_json_file(ipc_tmp_dir, delayed_command, monkeypatch, tmp_path):
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
        os.write(fd, b"checkpoint\ncontinue\n")
        os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=controller, daemon=True)
    t.start()

    for step in ipc_range(500, label="chk-test"):
        step.track(val=3.14)
        time.sleep(0.01)

    t.join(timeout=3)
    files = list(tmp_path.glob("loopmonitor_checkpoint_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["tracked"]["val"] == pytest.approx(3.14)


def test_checkpoint_cli_command_sends_signal(ipc_tmp_dir):
    from loopmonitor.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["checkpoint", "9999999"])
    assert exc_info.value.code == 1


# ── ipc stack ─────────────────────────────────────────────────────────────────

def test_stack_prints_traceback(ipc_tmp_dir, delayed_command, capsys):
    pid = os.getpid()

    def two_commands():
        fp = str(fifo_path(pid))
        sp = str(state_path(pid))
        for path in (fp, sp):
            deadline = time.time() + 5
            while not os.path.exists(path) and time.time() < deadline:
                time.sleep(0.005)
        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, b"stack\ncontinue\n")
        os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=two_commands, daemon=True)
    t.start()

    for step in ipc_range(500, label="stack-test"):
        time.sleep(0.01)

    t.join(timeout=3)
    out = capsys.readouterr().out
    assert "Stack trace" in out


# ── ipc memory ────────────────────────────────────────────────────────────────

def test_memory_prints_rss(ipc_tmp_dir, delayed_command, capsys):
    pid = os.getpid()

    def two_commands():
        fp = str(fifo_path(pid))
        sp = str(state_path(pid))
        for path in (fp, sp):
            deadline = time.time() + 5
            while not os.path.exists(path) and time.time() < deadline:
                time.sleep(0.005)
        fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, b"memory\ncontinue\n")
        os.close(fd)
        os.kill(pid, signal.SIGUSR1)

    t = threading.Thread(target=two_commands, daemon=True)
    t.start()

    for step in ipc_range(500, label="mem-test"):
        time.sleep(0.01)

    t.join(timeout=3)
    out = capsys.readouterr().out
    assert "RSS" in out
    assert "MB" in out


# ── CLI argument parsing for new commands ────────────────────────────────────

def test_set_requires_assignment_argument(ipc_tmp_dir):
    from loopmonitor.cli import main

    with pytest.raises(SystemExit):
        main(["set", "1234"])


def test_tail_default_interval(ipc_tmp_dir):
    """tail --interval should default to 2.0; parser must accept the command."""
    from loopmonitor.cli import main

    with patch("loopmonitor.cli.cmd_tail") as mock_tail:
        main(["tail", "1234"])
        args = mock_tail.call_args[0][0]
        assert args.interval == 2.0
        assert args.pid == "1234"


def test_notify_requires_condition(ipc_tmp_dir):
    from loopmonitor.cli import main

    with pytest.raises(SystemExit):
        main(["notify", "1234"])

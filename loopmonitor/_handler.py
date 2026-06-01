"""SIGUSR1 handler: reads commands from a named FIFO and dispatches."""

import ast
import json
import os
import platform
import resource
import signal
import stat as _stat
import sys
import threading
import traceback
from datetime import datetime, timezone

from ._dir import fifo_path
from ._report import format_break, format_peek
from ._state import read_state, remove_state


# Reference to the live ctx dict owned by the active ipc_range instance.
_ctx: dict = {}

# File descriptors for the command FIFO.
# _rfd  — read end (non-blocking); used by the signal handler.
# _wfd  — dummy write end kept open so _rfd never sees EOF when the CLI
#          closes its write end between commands.
_rfd: int = -1
_wfd: int = -1


def _dispatch(pid: int, cmd: str, frame=None) -> None:
    if cmd == "peek":
        _do_peek(pid)
    elif cmd == "plot" or cmd.startswith("plot "):
        parts = cmd.split(None, 1)
        last = int(parts[1]) if len(parts) > 1 else 0
        _do_plot(pid, last)
    elif cmd == "continue":
        _do_continue()
    elif cmd == "break":
        _do_break(pid)
    elif cmd.startswith("set "):
        _do_set(cmd[4:])
    elif cmd == "checkpoint":
        _do_checkpoint(pid)
    elif cmd == "stack":
        _do_stack(frame)
    elif cmd == "memory":
        _do_memory(pid)


def _do_peek(pid: int) -> None:
    state = read_state(pid)
    if state:
        print(format_peek(state), flush=True)
    else:
        print(f"[loopmonitor] No state available for PID {pid}", flush=True)


def _do_plot(pid: int, last: int = 0) -> None:
    state = read_state(pid)
    if state is None:
        print(f"[loopmonitor] No state available for PID {pid}", flush=True)
        return
    from ._plot import snapshot
    snapshot(state, last=last)


def _do_continue() -> None:
    _ctx["should_stop"] = True
    print("[loopmonitor] 'continue' received — loop will exit after this iteration.",
          flush=True)


def _do_break(pid: int) -> None:
    state = read_state(pid)
    msg = format_break(state) if state else f"[loopmonitor] Breaking PID {pid}."

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    report_path = f"loopmonitor_break_{pid}_{ts}.json"
    try:
        with open(report_path, "w") as f:
            json.dump(state or {}, f, indent=2)
    except OSError:
        pass

    print(msg, flush=True)
    print(f"[loopmonitor] State written to {report_path}", flush=True)
    remove_state(pid)
    sys.exit(0)


def _do_set(assignment: str) -> None:
    try:
        key, _, raw = assignment.partition("=")
        key = key.strip()
        if not key.isidentifier():
            raise ValueError(f"invalid identifier: {key!r}")
        value = ast.literal_eval(raw.strip())
        _ctx.setdefault("injected", {})[key] = value
        print(f"[loopmonitor] set {key} = {value!r}", flush=True)
    except Exception as exc:
        print(f"[loopmonitor] 'set' failed: {exc}", flush=True)


def _do_checkpoint(pid: int) -> None:
    state = read_state(pid)
    if state is None:
        print(f"[loopmonitor] No state available for PID {pid}", flush=True)
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"loopmonitor_checkpoint_{pid}_{ts}.json"
    try:
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        print(f"[loopmonitor] Checkpoint saved to {path}", flush=True)
    except OSError as exc:
        print(f"[loopmonitor] Checkpoint failed: {exc}", flush=True)


def _do_stack(frame=None) -> None:
    if frame is None:
        main_tid = threading.main_thread().ident
        frame = sys._current_frames().get(main_tid)
    if frame is None:
        print("[loopmonitor] Could not retrieve main thread stack.", flush=True)
        return
    lines = traceback.format_stack(frame)
    print(f"[loopmonitor] Stack trace for PID {os.getpid()}:", flush=True)
    print("".join(lines), end="", flush=True)


def _do_memory(pid: int) -> None:
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = usage.ru_maxrss
        # macOS reports bytes; Linux reports kilobytes
        rss_mb = rss / 1024 / 1024 if platform.system() == "Darwin" else rss / 1024
        print(f"[loopmonitor] PID {pid} memory — RSS: {rss_mb:.1f} MB", flush=True)
    except Exception as exc:
        print(f"[loopmonitor] Memory info unavailable: {exc}", flush=True)


def install(pid: int, ctx: dict) -> None:
    """Create the command FIFO and install the SIGUSR1 handler."""
    global _ctx, _rfd, _wfd
    _ctx = ctx
    _ctx["pid"] = pid

    fp = fifo_path(pid)
    # Remove a stale FIFO left by a previous crash.
    # Use lstat so a symlink left by an attacker is removed, not followed.
    try:
        if _stat.S_ISFIFO(os.lstat(str(fp)).st_mode):
            fp.unlink()
        elif fp.exists() or fp.is_symlink():
            raise RuntimeError(
                f"[loopmonitor] {fp} exists but is not a FIFO — refusing to overwrite."
            )
    except FileNotFoundError:
        pass
    # 0o600: only the owning user can read or write this FIFO.
    os.mkfifo(fp, mode=0o600)

    # Open read end non-blocking so the handler never blocks.
    _rfd = os.open(str(fp), os.O_RDONLY | os.O_NONBLOCK)
    # Keep a dummy write end open so reads never see a premature EOF when
    # the CLI closes its write end between commands.
    _wfd = os.open(str(fp), os.O_WRONLY | os.O_NONBLOCK)

    def _handler(signum, frame):
        try:
            data = os.read(_rfd, 4096)
        except BlockingIOError:
            return
        for line in data.decode(errors="replace").splitlines():
            cmd = line.strip()
            if cmd:
                _dispatch(pid, cmd, frame)

    signal.signal(signal.SIGUSR1, _handler)


def uninstall(pid: int) -> None:
    """Close FIFO file descriptors and remove the FIFO file."""
    global _rfd, _wfd
    for fd in (_rfd, _wfd):
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    _rfd = _wfd = -1

    fp = fifo_path(pid)
    if fp.exists():
        fp.unlink(missing_ok=True)

    signal.signal(signal.SIGUSR1, signal.SIG_DFL)

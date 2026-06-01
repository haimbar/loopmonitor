"""
ipc CLI — send commands to monitored Python and R processes.

Commands:
  ipc list [--group PPID]              show registered processes
  ipc peek  <target>                   print current status
  ipc plot  <target>                   show a live-values snapshot
  ipc continue <target>                exit loop, continue the script
  ipc break <target>                   stop, save JSON snapshot
  ipc set <target> key=value           inject a value readable by ipc_get()
  ipc pause <target>                   suspend the process (SIGSTOP)
  ipc resume <target>                  resume a paused process (SIGCONT)
  ipc tail <pid> [--interval N]        stream live status to this terminal
  ipc notify <pid> "expr" [--interval] alert when condition is true
  ipc checkpoint <target>              save a snapshot without stopping
  ipc stack <target>                   print the process call stack
  ipc memory <target>                  print memory usage
  ipc clean                            remove stale registry entries

<target> accepts a PID, 'all', or a label glob (e.g. 'worker-*').
"""

import argparse
import fnmatch
import os
import platform
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

from ._dir import cmd_path, fifo_path
from ._registry import all_entries, clean_stale, get_entry, is_alive
from ._report import format_peek
from ._state import read_state


# ── target resolution ─────────────────────────────────────────────────────────

def _resolve_targets(pid_str: str) -> list[dict]:
    """Return alive registry entries matching *pid_str*.

    - Numeric string  → entry with that PID; if none, all children of that PPID
    - ``'all'``       → every alive registered process
    - Glob pattern    → entries whose label matches (e.g. ``'worker-*'``)
    """
    alive = [e for e in all_entries() if is_alive(e["pid"])]
    if pid_str == "all":
        return alive
    try:
        pid = int(pid_str)
        direct = [e for e in alive if e["pid"] == pid]
        if direct:
            return direct
        return [e for e in alive if e.get("ppid") == pid]
    except ValueError:
        return [e for e in alive if fnmatch.fnmatch(e.get("label", ""), pid_str)]


def _require_targets(pid_str: str) -> list[dict]:
    """Like _resolve_targets but exits with an error when the result is empty."""
    targets = _resolve_targets(pid_str)
    if not targets:
        print(f"[loopmonitor] No alive processes matching {pid_str!r}.", file=sys.stderr)
        sys.exit(1)
    return targets


def _target_name(entry: dict) -> str:
    label = entry.get("label", "")
    return f"PID {entry['pid']}" + (f" ({label})" if label else "")


# ── low-level send ────────────────────────────────────────────────────────────

def _send_python(pid: int, cmd: str, *, exit_on_error: bool = True) -> bool:
    """Write a command to the process's FIFO then raise SIGUSR1."""
    if not is_alive(pid):
        print(f"[loopmonitor] No process with PID {pid} found.", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False
    fp = fifo_path(pid)
    try:
        fd = os.open(str(fp), os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        print(f"[loopmonitor] No command FIFO for PID {pid} — is loopmonitor active?",
              file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False
    except OSError as exc:
        print(f"[loopmonitor] Could not open FIFO for PID {pid}: {exc}", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False

    try:
        st = os.fstat(fd)
        if not stat.S_ISFIFO(st.st_mode):
            raise OSError(f"{fp} is not a FIFO (mode {oct(st.st_mode)})")
        if st.st_uid != os.getuid():
            raise OSError(f"{fp} owned by uid {st.st_uid}, expected {os.getuid()}")
        os.write(fd, f"{cmd}\n".encode())
    except OSError as exc:
        print(f"[loopmonitor] Security check failed for PID {pid}: {exc}", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False
    finally:
        os.close(fd)

    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError as exc:
        print(f"[loopmonitor] Could not signal PID {pid}: {exc}", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False

    return True


def _send_r(pid: int, cmd: str, *, exit_on_error: bool = True) -> bool:
    """Append a command to the R process's command file (polled each iteration).

    Uses O_APPEND so concurrent CLI calls are atomic for small writes.
    No signal is sent — R polls the file at each iteration boundary.
    """
    if not is_alive(pid):
        print(f"[loopmonitor] No process with PID {pid} found.", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False
    cp = cmd_path(pid)
    try:
        fd = os.open(
            str(cp),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        print(f"[loopmonitor] Could not open command file for PID {pid}: {exc}",
              file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"{cp} is not a regular file")
        if st.st_uid != os.getuid():
            raise OSError(f"{cp} owned by uid {st.st_uid}, expected {os.getuid()}")
        os.write(fd, f"{cmd}\n".encode())
    except OSError as exc:
        print(f"[loopmonitor] Security check failed for PID {pid}: {exc}", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False
    finally:
        os.close(fd)
    return True


def _send(pid: int, cmd: str, *, exit_on_error: bool = True) -> bool:
    """Route a command to the appropriate channel based on process language."""
    entry = get_entry(pid)
    if entry and entry.get("language") == "R":
        return _send_r(pid, cmd, exit_on_error=exit_on_error)
    return _send_python(pid, cmd, exit_on_error=exit_on_error)


# ── command handlers ──────────────────────────────────────────────────────────

def cmd_list(args) -> None:
    entries = all_entries()
    group = getattr(args, "group", None)
    if group is not None:
        entries = [e for e in entries if str(e.get("ppid", "")) == str(group)]
    if not entries:
        print("No registered processes." if group is None
              else f"No processes with PPID {group}.")
        return

    has_ppid = any("ppid" in e for e in entries)
    if has_ppid:
        print(f"{'PID':>8}  {'PPID':>7}  {'ALIVE':>5}  {'LANG':<4}  "
              f"{'LABEL':<30}  STARTED")
        print("─" * 85)
        for e in entries:
            pid   = e["pid"]
            ppid  = str(e.get("ppid", "—"))
            alive = "yes" if is_alive(pid) else "no "
            lang  = "R" if e.get("language") == "R" else "Py"
            label = e.get("label", "")[:30]
            start = e.get("start_time", "")[:19].replace("T", " ")
            print(f"{pid:>8}  {ppid:>7}  {alive:>5}  {lang:<4}  {label:<30}  {start}")
    else:
        print(f"{'PID':>8}  {'ALIVE':>5}  {'LANG':<4}  {'LABEL':<30}  STARTED")
        print("─" * 76)
        for e in entries:
            pid   = e["pid"]
            alive = "yes" if is_alive(pid) else "no "
            lang  = "R" if e.get("language") == "R" else "Py"
            label = e.get("label", "")[:30]
            start = e.get("start_time", "")[:19].replace("T", " ")
            print(f"{pid:>8}  {alive:>5}  {lang:<4}  {label:<30}  {start}")


def cmd_peek(args) -> None:
    targets = _require_targets(args.pid)
    if len(targets) == 1:
        # Single target: show cached state then signal the process for a refresh.
        pid = targets[0]["pid"]
        state = read_state(pid)
        if state:
            print(format_peek(state))
        _send(pid, "peek")
    else:
        # Broadcast: read all state files cleanly (no per-process signal, which
        # would interleave output across processes' own terminals).
        for entry in targets:
            pid   = entry["pid"]
            label = entry.get("label", str(pid))
            bar   = "─" * max(0, 54 - len(label) - len(str(pid)))
            print(f"── {label}  PID {pid} {bar}")
            state = read_state(pid)
            if state:
                print(format_peek(state))
            else:
                print("   (no state available)")
            print()


def cmd_plot(args) -> None:
    last = getattr(args, "last", 0)
    cmd = f"plot {last}" if last else "plot"
    for entry in _require_targets(args.pid):
        _send(entry["pid"], cmd, exit_on_error=len(_resolve_targets(args.pid)) == 1)


def cmd_continue(args) -> None:
    targets = _require_targets(args.pid)
    single  = len(targets) == 1
    for entry in targets:
        if _send(entry["pid"], "continue", exit_on_error=single):
            print(f"[loopmonitor] 'continue' sent to {_target_name(entry)}.")


def cmd_break(args) -> None:
    targets = _require_targets(args.pid)
    single  = len(targets) == 1
    for entry in targets:
        if _send(entry["pid"], "break", exit_on_error=single):
            print(f"[loopmonitor] 'break' sent to {_target_name(entry)}.")


def cmd_set(args) -> None:
    targets = _require_targets(args.pid)
    single  = len(targets) == 1
    for entry in targets:
        _send(entry["pid"], f"set {args.assignment}", exit_on_error=single)


def _is_foreground_terminal_process(pid: int) -> bool:
    """Return True if pid is the foreground process group of a controlling terminal.

    Reads /proc/<pid>/stat (Linux only). Returns False on any error or on macOS,
    so the caller degrades gracefully on non-Linux platforms.
    """
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        # comm field is wrapped in parentheses and may contain spaces/parens;
        # strip it by finding the last ')' before the remaining fields.
        after_comm = data[data.rfind(")") + 2:]
        fields = after_comm.split()
        # Remaining fields: state(0) ppid(1) pgrp(2) session(3) tty_nr(4) tpgid(5) …
        pgrp  = int(fields[2])
        tpgid = int(fields[5])
        return tpgid != -1 and tpgid == pgrp
    except Exception:
        return False


def cmd_pause(args) -> None:
    targets = _require_targets(args.pid)
    single  = len(targets) == 1
    for entry in targets:
        pid = entry["pid"]
        if not is_alive(pid):
            print(f"[loopmonitor] No process with PID {pid}.", file=sys.stderr)
            if single:
                sys.exit(1)
            continue
        if _is_foreground_terminal_process(pid):
            print(
                f"[loopmonitor] Warning: PID {pid} is the foreground process of a "
                "terminal. Sending SIGSTOP will let the shell reclaim that terminal, "
                "and SIGCONT will resume the process in the background — the "
                "interactive prompt will not return.\n"
                "[loopmonitor] To suspend an interactive session safely, use Ctrl+Z "
                "in its terminal and 'fg' to resume.",
                file=sys.stderr,
            )
        try:
            os.kill(pid, signal.SIGSTOP)
            print(f"[loopmonitor] {_target_name(entry)} paused (SIGSTOP).")
        except OSError as exc:
            print(f"[loopmonitor] Could not pause PID {pid}: {exc}", file=sys.stderr)
            if single:
                sys.exit(1)


def cmd_resume(args) -> None:
    targets = _require_targets(args.pid)
    single  = len(targets) == 1
    for entry in targets:
        pid = entry["pid"]
        if not is_alive(pid):
            print(f"[loopmonitor] No process with PID {pid}.", file=sys.stderr)
            if single:
                sys.exit(1)
            continue
        try:
            os.kill(pid, signal.SIGCONT)
            print(f"[loopmonitor] {_target_name(entry)} resumed (SIGCONT).")
        except OSError as exc:
            print(f"[loopmonitor] Could not resume PID {pid}: {exc}", file=sys.stderr)
            if single:
                sys.exit(1)


def cmd_tail(args) -> None:
    pid      = int(args.pid)
    interval = args.interval
    print(f"[loopmonitor] Tailing PID {pid} every {interval}s — Ctrl+C to stop.")
    try:
        while True:
            state = read_state(pid)
            if state is None:
                if not is_alive(pid):
                    print(f"[loopmonitor] PID {pid} is no longer running.")
                    break
                print(f"[loopmonitor] No state yet for PID {pid}…")
            else:
                print(format_peek(state))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[loopmonitor] Stopped tailing.")


def _desktop_notify(title: str, message: str) -> None:
    try:
        if platform.system() == "Darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=False)
        elif platform.system() == "Linux":
            subprocess.run(["notify-send", title, message], check=False)
        else:
            print(f"[loopmonitor] {title}: {message}")
    except Exception:
        print(f"[loopmonitor] {title}: {message}")


def cmd_notify(args) -> None:
    pid       = int(args.pid)
    condition = args.condition
    interval  = args.interval
    print(f"[loopmonitor] Watching PID {pid} for: {condition!r}  "
          f"(every {interval}s — Ctrl+C to stop)")
    try:
        while True:
            state = read_state(pid)
            if state is None:
                if not is_alive(pid):
                    print(f"[loopmonitor] PID {pid} is no longer running.")
                    break
            else:
                ns = dict(state.get("tracked", {}))
                ns.update({
                    "iteration": state.get("iteration"),
                    "total":     state.get("total"),
                    "elapsed":   state.get("elapsed_sec", 0),
                })
                try:
                    result = eval(condition, {"__builtins__": {}}, ns)  # noqa: S307
                except Exception as exc:
                    print(f"[loopmonitor] Condition error: {exc}")
                    break
                if result:
                    msg = f"PID {pid}: {condition}"
                    _desktop_notify("loopmonitor", msg)
                    print("[loopmonitor] Condition met — notification sent.")
                    break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[loopmonitor] Stopped watching.")


def cmd_checkpoint(args) -> None:
    targets = _require_targets(args.pid)
    single  = len(targets) == 1
    for entry in targets:
        if _send(entry["pid"], "checkpoint", exit_on_error=single):
            print(f"[loopmonitor] 'checkpoint' sent to {_target_name(entry)}.")


def cmd_stack(args) -> None:
    targets = _require_targets(args.pid)
    single  = len(targets) == 1
    for entry in targets:
        if _send(entry["pid"], "stack", exit_on_error=single):
            print(f"[loopmonitor] 'stack' sent to {_target_name(entry)} "
                  f"— output appears in the process terminal.")


def cmd_memory(args) -> None:
    targets = _require_targets(args.pid)
    single  = len(targets) == 1
    for entry in targets:
        if _send(entry["pid"], "memory", exit_on_error=single):
            print(f"[loopmonitor] 'memory' sent to {_target_name(entry)} "
                  f"— output appears in the process terminal.")


def cmd_clean(_args) -> None:
    removed = clean_stale()
    if removed:
        print(f"Removed stale entries: {removed}")
    else:
        print("Registry is clean.")


# ── argument parser ───────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ipc",
        description="Send commands to loopmonitor-monitored processes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Commands with no target argument
    sub.add_parser("clean", help="Remove stale registry entries")

    p_list = sub.add_parser("list", help="List registered processes")
    p_list.add_argument(
        "--group", metavar="PPID", type=int,
        help="show only processes whose parent PID matches PPID",
    )

    # Help text shared by broadcast-capable commands
    _target_help = (
        "PID, 'all', or a label glob (e.g. 'worker-*') — "
        "see 'ipc list' for registered processes"
    )

    p_plot = sub.add_parser(
        "plot",
        help="Open a live-values snapshot",
        description="Open a live-values snapshot (window opens in the process display).",
    )
    p_plot.add_argument("pid", metavar="TARGET", help=_target_help)
    p_plot.add_argument(
        "--last", type=int, default=0, metavar="K",
        help="show only the last K steps of each trace (0 = show all, default: 0)",
    )

    for name, fn, help_text, note in [
        ("peek",       cmd_peek,       "Print current status",
         "reads state file; for single PID also signals the process"),
        ("continue",   cmd_continue,   "Exit the loop, continue the script",
         "loop stops after the current iteration"),
        ("break",      cmd_break,      "Stop the program, save a JSON snapshot",
         "calls sys.exit(0) in the process"),
        ("checkpoint", cmd_checkpoint, "Save a JSON snapshot without stopping",
         "file written in the process working directory"),
        ("stack",      cmd_stack,      "Print the call stack",
         "output appears in the process terminal"),
        ("memory",     cmd_memory,     "Print RSS memory usage",
         "output appears in the process terminal"),
        ("pause",      cmd_pause,      "Suspend the process (SIGSTOP)",
         "resume with 'ipc resume'"),
        ("resume",     cmd_resume,     "Resume a paused process (SIGCONT)",
         "counterpart to 'ipc pause'"),
    ]:
        p = sub.add_parser(name, help=help_text,
                           description=f"{help_text} ({note}).")
        p.add_argument("pid", metavar="TARGET", help=_target_help)

    p_set = sub.add_parser(
        "set",
        help="Inject a value into the running loop",
        description=(
            "Write a key=value pair into the loop context. "
            "Read it with step.get('key') in Python or ipc_get('key') in R. "
            "The value is parsed with ast.literal_eval / .safe_eval, so numbers, "
            "strings, lists, and dicts are accepted."
        ),
    )
    p_set.add_argument("pid", metavar="TARGET", help=_target_help)
    p_set.add_argument("assignment", metavar="key=value",
                       help="e.g. lr=0.001  or  tags=['a','b']")

    p_tail = sub.add_parser(
        "tail",
        help="Stream live status updates to this terminal",
        description=(
            "Poll the state file and print a formatted status line every "
            "--interval seconds. Does not signal the process. Stop with Ctrl+C."
        ),
    )
    p_tail.add_argument("pid", metavar="PID", type=str,
                        help="PID of the monitored process (single target only)")
    p_tail.add_argument("--interval", type=float, default=2.0, metavar="SEC",
                        help="seconds between updates (default: 2)")

    p_notify = sub.add_parser(
        "notify",
        help="Send a desktop alert when a condition becomes true",
        description=(
            "Poll the state file every --interval seconds and evaluate EXPR "
            "against tracked values. When truthy, send a desktop notification "
            "and exit. Available names: all step.track() keys, plus "
            "'iteration', 'total', and 'elapsed' (seconds)."
        ),
    )
    p_notify.add_argument("pid", metavar="PID", type=str,
                          help="PID of the monitored process (single target only)")
    p_notify.add_argument("condition", metavar="EXPR",
                          help='e.g. "loss < 0.05"  or  "iteration > 5000"')
    p_notify.add_argument("--interval", type=float, default=5.0, metavar="SEC",
                          help="seconds between checks (default: 5)")

    args = parser.parse_args(argv)

    dispatch = {
        "list":       cmd_list,
        "clean":      cmd_clean,
        "peek":       cmd_peek,
        "plot":       cmd_plot,
        "continue":   cmd_continue,
        "break":      cmd_break,
        "set":        cmd_set,
        "pause":      cmd_pause,
        "resume":     cmd_resume,
        "tail":       cmd_tail,
        "notify":     cmd_notify,
        "checkpoint": cmd_checkpoint,
        "stack":      cmd_stack,
        "memory":     cmd_memory,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

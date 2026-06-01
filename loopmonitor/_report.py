"""Format state dicts for terminal display."""

from datetime import timedelta


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    td = timedelta(seconds=int(seconds))
    h, rem = divmod(td.seconds, 3600)
    m, s = divmod(rem, 60)
    if td.days:
        return f"{td.days}d {h:02d}:{m:02d}:{s:02d}"
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_peek(state: dict) -> str:
    it = state.get("iteration", "?")
    total = state.get("total")
    elapsed = state.get("elapsed_sec")
    eta = state.get("eta_sec")
    tracked = state.get("tracked", {})
    pid = state.get("pid", "?")

    pct = f"  ({100*it/total:.1f}%)" if total and isinstance(it, int) else ""
    progress = f"iter {it}/{total}{pct}" if total else f"iter {it}"

    lines = [
        f"[loopmonitor] PID {pid}  {progress}",
        f"         elapsed {_fmt_time(elapsed)}  ETA {_fmt_time(eta)}",
    ]
    if tracked:
        vals = "  ".join(f"{k}={v}" for k, v in tracked.items())
        lines.append(f"         {vals}")
    return "\n".join(lines)


def format_break(state: dict) -> str:
    header = format_peek(state)
    return header + "\n[loopmonitor] Stopping — state saved."

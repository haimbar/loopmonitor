"""On-demand matplotlib snapshot triggered by the 'plot' command."""

import json
import os
import subprocess
import sys
import tempfile


# The plotting code runs in a subprocess so that:
#   - matplotlib gets a proper main-thread event loop (required on macOS/Cocoa)
#   - the user can close the window normally at any time
#   - the monitored loop keeps running while the window is open
#   - no arbitrary timeout is needed

_PLOT_SCRIPT = """
import json, os, sys
import matplotlib.pyplot as plt

with open(sys.argv[1]) as _f:
    state = json.load(_f)
os.unlink(sys.argv[1])
last = int(sys.argv[2]) if len(sys.argv) > 2 else 0
tracked = state.get("tracked", {})
pid     = state.get("pid", "?")
it      = state.get("iteration", "?")
total   = state.get("total")
elapsed = state.get("elapsed_sec", 0)

title = f"PID {pid}  iter {it}"
if total:
    title += f"/{total}"
title += f"  elapsed {int(elapsed)}s"
if last:
    title += f"  (last {last} steps)"

if not tracked:
    print(f"[loopmonitor] {title}  (no tracked values to plot)", flush=True)
    sys.exit(0)

n = len(tracked)
fig, axes = plt.subplots(n, 1, figsize=(7, 2.5 * n), squeeze=False)
fig.suptitle(title, fontsize=10)

for ax, (name, val) in zip(axes[:, 0], tracked.items()):
    if isinstance(val, (list, tuple)):
        data = val[-last:] if last else val
        ax.plot(data, linewidth=0.8)
        ax.set_ylabel(name, fontsize=9)
        if last and len(val) > last:
            ax.set_xlabel(f"step (last {last} of {len(val)})", fontsize=8)
    else:
        ax.text(0.5, 0.5, f"{name} = {val}",
                ha="center", va="center", fontsize=14,
                transform=ax.transAxes)
        ax.set_axis_off()

fig.tight_layout()
plt.show()
"""


def snapshot(state: dict, last: int = 0) -> None:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(state, tmp)
    tmp.close()
    subprocess.Popen(
        [sys.executable, "-c", _PLOT_SCRIPT, tmp.name, str(last)],
        close_fds=True,
    )

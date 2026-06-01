"""
Smoke test: verifies that ipc_range registers/deregisters,
updates state, and responds to peek/continue signals.

Run with:  python smoke_test.py
"""

import json
import os
import signal
import sys
import threading
import time

sys.path.insert(0, ".")
from loopmonitor import ipc_range
from loopmonitor._dir import fifo_path, state_path
from loopmonitor._registry import all_entries
from loopmonitor.cli import main as ipc


def _write_fifo(pid: int, cmd: str) -> None:
    fp = str(fifo_path(pid))
    fd = os.open(fp, os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(fd, f"{cmd}\n".encode())
    finally:
        os.close(fd)


def controller(pid: int) -> None:
    """Runs in a background thread and sends commands to the main loop."""
    time.sleep(0.3)

    # ---- peek -----------------------------------------------------------
    _write_fifo(pid, "peek")
    os.kill(pid, signal.SIGUSR1)
    time.sleep(0.3)

    # ---- continue -------------------------------------------------------
    _write_fifo(pid, "continue")
    os.kill(pid, signal.SIGUSR1)


def main() -> None:
    pid = os.getpid()
    t = threading.Thread(target=controller, args=(pid,), daemon=True)
    t.start()

    iterations_done = 0
    for step in ipc_range(200, label="smoke-test"):
        step.track(loss=round(1.0 - step.index * 0.005, 4))
        time.sleep(0.02)
        iterations_done = step.index + 1

    t.join(timeout=2)

    assert iterations_done < 200, (
        f"Loop ran all 200 iterations — 'continue' signal was not honoured "
        f"(ran {iterations_done})"
    )
    print(f"\nSmoke test passed: loop stopped after {iterations_done} iterations "
          f"(expected < 200).")

    # Registry should be clean after the loop exits.
    entries = all_entries()
    assert not entries, f"Registry not clean after loop: {entries}"
    print("Registry clean: OK")

    # State file should be gone too.
    assert not state_path(pid).exists(), "State file not cleaned up"
    print("State file cleaned up: OK")

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()

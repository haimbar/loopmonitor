#!/usr/bin/env python3
"""
fork_workers.py — 3 parallel workers monitored by loopmonitor.

Run this script, then in another terminal:

    ipc list                         # shows 3 registered Python processes
    ipc peek   <ppid>                # progress for all 3 workers at once
    ipc peek   <pid>                 # progress for one worker
    ipc plot   <pid>                 # live loss curve for one worker
    ipc set    all lr=0.001          # inject a new learning rate into every worker
    ipc set    <pid> lr=0.001        # inject into one worker
    ipc continue <ppid>              # stop all workers after their current iteration
    ipc continue <pid>               # stop one worker

<ppid> is the PID printed when this script starts.
<pid>  is the individual worker PID from `ipc list`.

Each worker independently tracks its own loss and learning rate.
"""

import math
import multiprocessing
import os
import time

from loopmonitor import ipc_range


def worker(worker_id: int, n_steps: int = 300) -> None:
    """Simulate a long-running training loop."""
    loss = 1.0 - worker_id * 0.05
    for step in ipc_range(n_steps, label=f"worker-{worker_id}"):
        lr = step.get("lr", default=0.01)
        time.sleep(0.2)
        loss *= 1.0 - lr * (1.0 + 0.1 * math.sin(step.index * 0.3 * (worker_id + 1)))
        loss = max(1e-6, loss)
        step.track(loss=round(loss, 6), lr=lr)


if __name__ == "__main__":
    n_workers = 3
    # Use fork so workers inherit the parent's Python path and environment.
    ctx = multiprocessing.get_context("fork")
    procs: list[multiprocessing.Process] = []

    ppid = os.getpid()
    print(f"Starting {n_workers} workers.  Parent PID: {ppid}")
    print(f"  → `ipc peek {ppid}` shows all workers at once")
    for i in range(n_workers):
        p = ctx.Process(target=worker, args=(i,), daemon=True)
        p.start()
        print(f"  worker-{i}  PID {p.pid}")
        procs.append(p)

    print("\nPress Ctrl-C to kill all workers, or let them run to completion.")
    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        print("\nInterrupted — terminating workers.")
        for p in procs:
            p.terminate()
        for p in procs:
            p.join()

    print("Done.")

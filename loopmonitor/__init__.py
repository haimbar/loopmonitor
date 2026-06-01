"""
loopmonitor — on-demand status queries and graceful loop control for long-running Python programs.

Quick start::

    from loopmonitor import ipc_range

    for step in ipc_range(1000, label="my training run"):
        loss = train()
        step.track(loss=loss, accuracy=acc)

Then from another terminal::

    ipc list
    ipc peek <pid>
    ipc plot <pid>
    ipc continue <pid>   # exit loop, continue program
    ipc break <pid>      # stop program, save state to JSON
"""

from .range import IPCStep, ipc_range

__all__ = ["ipc_range", "IPCStep"]
__version__ = "0.1.2"

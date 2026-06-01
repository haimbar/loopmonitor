"""ipc_range: a drop-in for range() that enables IPC monitoring."""

import os
import sys
import time
from collections.abc import Iterable, Iterator
from typing import Any


class IPCStep:
    """Yielded by ipc_range each iteration; call .track() to update watched values."""

    def __init__(self, index: int, ctx: dict) -> None:
        self._index = index
        self._ctx = ctx

    @property
    def index(self) -> int:
        return self._index

    def track(self, **kwargs: Any) -> None:
        self._ctx["tracked"].update(kwargs)

    def get(self, key: str, default: Any = None) -> Any:
        """Return a value injected by `ipc set`, or *default* if not set."""
        return self._ctx.get("injected", {}).get(key, default)

    @property
    def should_stop(self) -> bool:
        return self._ctx.get("should_stop", False)


class ipc_range:
    """
    Drop-in replacement for range() (or any iterable) that:
    - auto-registers the process in ~/.ipc/registry.json
    - installs a SIGUSR1 handler for on-demand peek/plot/continue/break
    - writes a state file after each iteration
    - stops yielding when a 'continue' command is received

    Usage::

        from loopmonitor import ipc_range

        for step in ipc_range(1000, label="training"):
            loss = train()
            step.track(loss=loss)
    """

    def __init__(
        self,
        iterable_or_stop: int | Iterable,
        *,
        label: str = "",
        state_every: int = 1,
    ) -> None:
        if isinstance(iterable_or_stop, int):
            self._iterable = range(iterable_or_stop)
            self._total: int | None = iterable_or_stop
        else:
            self._iterable = iterable_or_stop
            try:
                self._total = len(iterable_or_stop)  # type: ignore[arg-type]
            except TypeError:
                self._total = None

        self._label = label or sys.argv[0]
        self._state_every = max(1, state_every)

    def __iter__(self) -> Iterator[IPCStep]:
        from ._handler import install, uninstall
        from ._registry import deregister, register
        from ._state import remove_state, write_state

        pid = os.getpid()
        ctx: dict = {"should_stop": False, "tracked": {}, "injected": {}}

        register(pid, self._label, sys.argv[0], ppid=os.getppid())
        install(pid, ctx)

        start_ts = time.time()
        last_i = -1

        try:
            for i, item in enumerate(self._iterable):
                last_i = i
                if ctx["should_stop"]:
                    break

                step = IPCStep(i, ctx)
                yield step

                if i % self._state_every == 0:
                    write_state(pid, i + 1, self._total, start_ts, dict(ctx["tracked"]))

        finally:
            write_state(pid, last_i + 1, self._total, start_ts, dict(ctx["tracked"]))
            uninstall(pid)
            deregister(pid)
            remove_state(pid)

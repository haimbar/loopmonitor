"""Manages the ~/.ipc/ working directory."""

import os
from pathlib import Path

IPC_DIR: Path = (
    Path(os.environ["LOOPCTL_DIR"])
    if "LOOPCTL_DIR" in os.environ
    else Path.home() / ".ipc"
)


def ipc_dir() -> Path:
    IPC_DIR.mkdir(mode=0o700, exist_ok=True)
    return IPC_DIR


def fifo_path(pid: int) -> Path:
    return ipc_dir() / f"{pid}.fifo"


def state_path(pid: int) -> Path:
    return ipc_dir() / f"{pid}.state.json"


def registry_path() -> Path:
    return ipc_dir() / "registry.json"


def cmd_path(pid: int) -> Path:
    """Command file for R processes (polled at each iteration boundary)."""
    return ipc_dir() / f"{pid}.cmd"

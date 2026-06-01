import os
import stat

import pytest

from loopmonitor._dir import fifo_path, ipc_dir, registry_path, state_path


def test_creates_directory(ipc_tmp_dir):
    d = ipc_dir()
    assert d.exists() and d.is_dir()


def test_directory_permissions(ipc_tmp_dir):
    d = ipc_dir()
    mode = stat.S_IMODE(os.stat(d).st_mode)
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


def test_idempotent(ipc_tmp_dir):
    ipc_dir()
    ipc_dir()  # second call must not raise or change permissions


def test_fifo_path_contains_pid(ipc_tmp_dir):
    p = fifo_path(99)
    assert "99" in p.name
    assert p.suffix == ".fifo"


def test_state_path_contains_pid(ipc_tmp_dir):
    p = state_path(99)
    assert "99" in p.name
    assert p.name.endswith(".json")


def test_paths_are_children_of_ipc_dir(ipc_tmp_dir):
    ipc_dir()
    assert fifo_path(1).parent == ipc_dir()
    assert state_path(1).parent == ipc_dir()
    assert registry_path().parent == ipc_dir()


def test_different_pids_give_different_paths(ipc_tmp_dir):
    assert fifo_path(1) != fifo_path(2)
    assert state_path(1) != state_path(2)

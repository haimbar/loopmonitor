import os

import pytest

from loopmonitor._registry import (
    all_entries,
    clean_stale,
    deregister,
    is_alive,
    register,
)


def test_register_creates_entry(ipc_tmp_dir):
    register(12345, "my label", "/path/to/script.py")
    entries = all_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["pid"] == 12345
    assert e["label"] == "my label"
    assert e["script"] == "/path/to/script.py"
    assert "start_time" in e


def test_register_multiple_processes(ipc_tmp_dir):
    register(1, "a", "a.py")
    register(2, "b", "b.py")
    pids = {e["pid"] for e in all_entries()}
    assert pids == {1, 2}


def test_register_overwrites_existing_pid(ipc_tmp_dir):
    register(1, "first", "a.py")
    register(1, "second", "b.py")
    entries = all_entries()
    assert len(entries) == 1
    assert entries[0]["label"] == "second"


def test_deregister_removes_entry(ipc_tmp_dir):
    register(12345, "test", "script.py")
    deregister(12345)
    assert all_entries() == []


def test_deregister_missing_is_noop(ipc_tmp_dir):
    deregister(99999)  # must not raise


def test_all_entries_empty_when_no_registry(ipc_tmp_dir):
    assert all_entries() == []


def test_is_alive_current_process(ipc_tmp_dir):
    assert is_alive(os.getpid())


def test_is_alive_nonexistent_pid(ipc_tmp_dir):
    assert not is_alive(9_999_999)


def test_clean_stale_removes_dead_entries(ipc_tmp_dir):
    register(9_999_999, "dead", "x.py")
    register(os.getpid(), "alive", "y.py")
    removed = clean_stale()
    assert 9_999_999 in removed
    alive_pids = {e["pid"] for e in all_entries()}
    assert os.getpid() in alive_pids
    assert 9_999_999 not in alive_pids


def test_clean_stale_returns_empty_when_nothing_to_remove(ipc_tmp_dir):
    register(os.getpid(), "alive", "y.py")
    assert clean_stale() == []

import time

import pytest

from loopmonitor._state import read_state, remove_state, write_state


def test_write_and_read_roundtrip(ipc_tmp_dir):
    start = time.time()
    write_state(1, 50, 200, start, {"loss": 0.42, "acc": 0.91})
    s = read_state(1)
    assert s["pid"] == 1
    assert s["iteration"] == 50
    assert s["total"] == 200
    assert s["tracked"] == {"loss": 0.42, "acc": 0.91}


def test_elapsed_is_recorded(ipc_tmp_dir):
    start = time.time() - 10
    write_state(1, 5, 100, start, {})
    s = read_state(1)
    assert s["elapsed_sec"] >= 10


def test_eta_computed_when_total_known(ipc_tmp_dir):
    # 10 s elapsed at iter 50 of 200 → 30 s remaining
    start = time.time() - 10
    write_state(1, 50, 200, start, {})
    s = read_state(1)
    assert s["eta_sec"] is not None
    assert 25 <= s["eta_sec"] <= 35


def test_eta_none_when_total_unknown(ipc_tmp_dir):
    write_state(1, 50, None, time.time(), {})
    assert read_state(1)["eta_sec"] is None


def test_eta_none_at_iteration_zero(ipc_tmp_dir):
    write_state(1, 0, 100, time.time(), {})
    assert read_state(1)["eta_sec"] is None


def test_updated_field_is_present(ipc_tmp_dir):
    write_state(1, 1, 10, time.time(), {})
    s = read_state(1)
    assert "updated" in s
    assert "T" in s["updated"]  # ISO 8601 format


def test_read_returns_none_if_missing(ipc_tmp_dir):
    assert read_state(99999) is None


def test_remove_deletes_file(ipc_tmp_dir):
    write_state(1, 1, 10, time.time(), {})
    remove_state(1)
    assert read_state(1) is None


def test_remove_is_noop_if_missing(ipc_tmp_dir):
    remove_state(99999)  # must not raise


def test_empty_tracked_dict_is_stored(ipc_tmp_dir):
    write_state(1, 1, 10, time.time(), {})
    assert read_state(1)["tracked"] == {}


def test_overwrite_updates_values(ipc_tmp_dir):
    start = time.time()
    write_state(1, 10, 100, start, {"x": 1})
    write_state(1, 20, 100, start, {"x": 2})
    assert read_state(1)["iteration"] == 20
    assert read_state(1)["tracked"]["x"] == 2

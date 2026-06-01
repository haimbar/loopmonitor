"""
Security property tests.

Verifies the three-layer protection model described in the README:
  1. Filesystem permissions (0o700 directory, 0o600 FIFO)
  2. O_NOFOLLOW prevents following symlinks in the CLI
  3. Post-open fstat checks reject non-FIFOs and wrong-owner files
"""

import os
import signal
import stat

import pytest

from loopmonitor._dir import fifo_path, ipc_dir
from loopmonitor._handler import install, uninstall


# ── filesystem permissions ────────────────────────────────────────────────────

def test_ipc_directory_is_owner_only(ipc_tmp_dir):
    d = ipc_dir()
    mode = stat.S_IMODE(os.stat(str(d)).st_mode)
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


def test_fifo_is_owner_only(ipc_tmp_dir):
    pid = os.getpid()
    ctx = {"should_stop": False, "tracked": {}}
    try:
        install(pid, ctx)
        mode = stat.S_IMODE(os.lstat(str(fifo_path(pid))).st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    finally:
        uninstall(pid)


def test_fifo_is_owned_by_current_user(ipc_tmp_dir):
    pid = os.getpid()
    ctx = {"should_stop": False, "tracked": {}}
    try:
        install(pid, ctx)
        st = os.lstat(str(fifo_path(pid)))
        assert st.st_uid == os.getuid()
    finally:
        uninstall(pid)


# ── install() rejects non-FIFO paths ─────────────────────────────────────────

def test_install_raises_if_symlink_at_fifo_path(ipc_tmp_dir, tmp_path):
    """install() must refuse to overwrite a symlink — never follow it."""
    ipc_dir()
    decoy = tmp_path / "decoy.txt"
    decoy.write_text("sensitive")
    fifo_path(os.getpid()).symlink_to(decoy)

    with pytest.raises(RuntimeError, match="not a FIFO"):
        install(os.getpid(), {"should_stop": False, "tracked": {}})

    assert decoy.read_text() == "sensitive"  # decoy untouched


def test_install_raises_if_regular_file_at_fifo_path(ipc_tmp_dir):
    """install() must refuse to overwrite any non-FIFO path component."""
    ipc_dir()
    fifo_path(os.getpid()).write_text("not a fifo")

    with pytest.raises(RuntimeError, match="not a FIFO"):
        install(os.getpid(), {"should_stop": False, "tracked": {}})


# ── CLI O_NOFOLLOW and fstat checks ──────────────────────────────────────────

def test_o_nofollow_blocks_symlink_open(ipc_tmp_dir, tmp_path):
    """os.open with O_NOFOLLOW raises OSError when path is a symlink."""
    target = tmp_path / "target.txt"
    target.write_text("safe")
    link = ipc_dir() / "99.fifo"
    ipc_dir()
    link.symlink_to(target)

    with pytest.raises(OSError):
        os.open(str(link), os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)

    assert target.read_text() == "safe"  # target untouched


def test_cli_send_rejects_regular_file_at_fifo_path(ipc_tmp_dir):
    """_send() must exit with code 1 when the FIFO path is a regular file."""
    from loopmonitor._registry import register
    from loopmonitor.cli import _send

    pid = os.getpid()
    register(pid, "test", "test.py")
    ipc_dir()
    fifo_path(pid).write_text("not a fifo")

    with pytest.raises(SystemExit) as exc_info:
        _send(pid, "peek")
    assert exc_info.value.code == 1


def test_cli_send_rejects_symlink_at_fifo_path(ipc_tmp_dir, tmp_path):
    """_send() must exit with code 1 when the FIFO path is a symlink."""
    from loopmonitor._registry import register
    from loopmonitor.cli import _send

    pid = os.getpid()
    register(pid, "test", "test.py")
    ipc_dir()
    target = tmp_path / "other.txt"
    target.write_text("safe")
    fifo_path(pid).symlink_to(target)

    with pytest.raises(SystemExit) as exc_info:
        _send(pid, "peek")
    assert exc_info.value.code == 1
    assert target.read_text() == "safe"

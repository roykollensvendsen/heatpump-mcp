# SPDX-License-Identifier: GPL-3.0-or-later
"""Each test run keeps its temporary files in a folder of its own, removed when the run ends.

Pytest's default keeps them under one folder that every project on the computer
shares, and a day of runs filled it. Here a run gets a folder of its own, and
everything a test makes in a temporary place goes in it: pytest's ``tmp_path``,
and whatever uses ``tempfile`` or ``TMPDIR``. The folder is removed at the end,
also when the run fails, is interrupted, or is stopped with a signal.

While the run lasts it holds a lock on the folder's ``.lock`` file. A folder
whose lock anyone can take was left by a run that was killed, and the next run
sweeps it. A lock, unlike a process number, means the same inside a sandbox
with its own process numbers, and is never handed on to another process. Only
a real folder of this user's is swept, never a link, and never pytest's shared
folder, which other projects' runs are using. A run given ``--basetemp``, as
the mutation run gives each of its runs, keeps to that folder, and whoever gave
it removes it.
"""

import contextlib
import fcntl
import importlib.util
import os
import pathlib
import signal
import stat
import tempfile
import time

import pytest

PREFIX = "heatpump-mcp-pytest-"
#: A folder this young with no lock file may be a run that has not taken its lock yet. A guess:
#: a run takes its lock a moment after it makes the folder, so any margin far above that will do.
UNLOCKED_GRACE_SECONDS = 600
STOPS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name))
_RUN = pytest.StashKey[dict]()


def _mutate():
    """The mutation runner, loaded by path, for its one way of removing a folder."""
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mutate.py"
    spec = importlib.util.spec_from_file_location("heatpump_mutate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_remove = _mutate()._remove  # noqa: SLF001 - one way of removing, kept in mutate.py


def _left_by_a_killed_run(folder):
    """True for a real folder of this user's whose run no longer holds its lock."""
    try:
        seen = os.lstat(folder)
    except OSError:
        return False
    # RULE: only a real folder of this user's is swept
    if not stat.S_ISDIR(seen.st_mode) or seen.st_uid != os.getuid():
        return False
    try:
        lock = os.open(folder / ".lock", os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return time.time() - seen.st_mtime > UNLOCKED_GRACE_SECONDS
    except OSError:
        return False
    try:
        # RULE: a folder whose lock is held is never swept
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    finally:
        os.close(lock)
    return True


def _sweep(where):
    for left in where.glob(f"{PREFIX}*"):
        if _left_by_a_killed_run(left):
            _remove(left)


def _stop(signum, _frame):
    """Turn a stop signal into an interruption, so the run still cleans up."""
    for later in (*STOPS, signal.SIGINT):
        signal.signal(later, signal.SIG_IGN)
    msg = f"stopped by signal {signum}"
    raise KeyboardInterrupt(msg)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Give the run a folder of its own, before pytest picks its shared one."""
    if config.option.basetemp:
        return
    where = pathlib.Path(tempfile.gettempdir())
    _sweep(where)
    folder = pathlib.Path(tempfile.mkdtemp(prefix=PREFIX, dir=where))
    lock = os.open(folder / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    # pytest empties the folder it is given, so it gets one inside, beside the lock.
    (folder / "tmp").mkdir()
    config.option.basetemp = str(folder / "pytest")
    config.stash[_RUN] = {
        "folder": folder,
        "lock": lock,
        "tempdir": tempfile.tempdir,
        "TMPDIR": os.environ.get("TMPDIR"),
        "signals": {stop: signal.getsignal(stop) for stop in STOPS},
    }
    tempfile.tempdir = os.environ["TMPDIR"] = str(folder / "tmp")
    for stop in STOPS:
        signal.signal(stop, _stop)


def pytest_unconfigure(config):
    """Put back what the run changed and remove its folder; this runs after a failure or a stop too."""
    run = config.stash.get(_RUN, None)
    if run is None:
        return
    tempfile.tempdir = run["tempdir"]
    if run["TMPDIR"] is None:
        os.environ.pop("TMPDIR", None)
    else:
        os.environ["TMPDIR"] = run["TMPDIR"]
    for stop, handler in run["signals"].items():
        signal.signal(stop, handler)
    try:
        # RULE: a test run's folder is removed when the run ends
        _remove(run["folder"])
    finally:
        with contextlib.suppress(OSError):
            os.close(run["lock"])

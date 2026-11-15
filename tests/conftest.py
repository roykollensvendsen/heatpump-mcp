# SPDX-License-Identifier: GPL-3.0-or-later
"""Each test run keeps its temporary files in a folder of its own, removed when the run ends.

Pytest's default keeps them under one folder that every project on the computer
shares, and a day of runs filled it. Here a run gets a folder named after this
project and the run's process, removed at the end, also when the run fails or is
interrupted. One a killed run left is swept at the start of the next run, unless
its process is still running. Pytest's shared folder is never swept: other
projects' runs are using it. A run given ``--basetemp``, as the mutation run
gives each of its runs, keeps to that folder, and whoever gave it removes it.
"""

import contextlib
import os
import pathlib
import shutil
import stat
import tempfile

import pytest

PREFIX = "heatpump-mcp-pytest-"
_MADE = pytest.StashKey[pathlib.Path]()


def _open_up(place):
    if place.is_symlink():
        return
    with contextlib.suppress(OSError):
        place.chmod(place.stat().st_mode | stat.S_IRWXU)


def _writable_again(func, path, _exc):
    for place in (pathlib.Path(path).parent, pathlib.Path(path)):
        _open_up(place)
    func(path)


def _remove(folder):
    """Delete a folder, even where a test took away the rights to read or delete in it.

    The same as ``_remove`` in ``scripts/mutate.py``; scripts/ is not a package to import from.
    """
    if not folder.exists():
        return
    for place, folders, _ in os.walk(folder):
        for name in folders:
            _open_up(pathlib.Path(place) / name)
    shutil.rmtree(folder, onexc=_writable_again)


def _running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sweep():
    """Remove what this project's killed runs left, and nothing else."""
    for left in pathlib.Path(tempfile.gettempdir()).glob(f"{PREFIX}*"):
        pid = left.name.removeprefix(PREFIX).split("-", 1)[0]
        if pid.isdigit() and not _running(int(pid)):
            _remove(left)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Give the run a folder of its own, before pytest picks its shared one."""
    if config.option.basetemp:
        return
    _sweep()
    made = pathlib.Path(tempfile.mkdtemp(prefix=f"{PREFIX}{os.getpid()}-"))
    config.option.basetemp = str(made)
    config.stash[_MADE] = made


def pytest_unconfigure(config):
    """Remove the run's folder; this runs also after a failure or an interruption."""
    made = config.stash.get(_MADE, None)
    if made is not None:
        _remove(made)

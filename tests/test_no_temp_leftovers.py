# SPDX-License-Identifier: GPL-3.0-or-later
"""A test run leaves nothing in the computer's temporary folder.

Pytest's default keeps every run's temporary files under one folder that all
projects on the computer share, and a day of runs filled it. So each run here
keeps them in a folder of its own, removed when the run ends, also when the run
fails, is interrupted or is stopped. The run holds a lock on its folder while it
lasts. A folder whose lock nobody holds was left by a run that was killed, and is
swept at the start of the next run. Only real folders of this project's own
user are swept, never a link, and never pytest's shared folder, which other
projects' runs are using.

Most tests run a small pytest project with this suite's own conftest, in a
process of its own, with the temporary folder pointed at an empty one.
"""

import fcntl
import importlib.util
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types

import pytest
from bounded import SECONDS, line_within, run_bounded

CONFTEST = pathlib.Path(__file__).with_name("conftest.py")
ROOT = CONFTEST.parent.parent
WRITES = "def test_it(tmp_path):\n    (tmp_path / 'f').write_text('x')\n"
PREFIX = "heatpump-mcp-pytest-"


@pytest.fixture
def run(tmp_path):
    """Run one test file in a small project; give back how it ended and the temporary folder."""
    tmpdir, project = tmp_path / "tmp", tmp_path / "project"
    tmpdir.mkdir()
    (project / "tests").mkdir(parents=True)
    (project / "scripts").mkdir()
    shutil.copy(CONFTEST, project / "tests" / "conftest.py")
    shutil.copy(ROOT / "scripts" / "mutate.py", project / "scripts" / "mutate.py")

    def _run(test, *args):
        (project / "tests" / "test_one.py").write_text(test)
        ran = run_bounded(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests", *args],
            cwd=project,
            env={**os.environ, "TMPDIR": str(tmpdir)},
            capture_output=True,
            text=True,
        )
        return ran, tmpdir

    return _run


def test_a_test_run_s_folder_is_removed_when_the_run_ends(run):
    ran, tmpdir = run(WRITES)
    assert ran.returncode == 0, ran.stdout[-1500:]
    assert list(tmpdir.iterdir()) == []


@pytest.mark.parametrize(
    "ending",
    [
        "    assert False\n",
        "    raise KeyboardInterrupt\n",
        "    import os, signal\n    os.kill(os.getpid(), signal.SIGTERM)\n",
        "    import os, signal\n    os.kill(os.getpid(), signal.SIGHUP)\n",
    ],
    ids=["failed", "interrupted", "stopped", "hung-up"],
)
def test_a_failed_interrupted_or_stopped_test_run_leaves_nothing_either(run, ending):
    ran, tmpdir = run(WRITES + ending)
    assert ran.returncode != 0
    assert list(tmpdir.iterdir()) == [], ran.stdout[-1500:]


HOLD = """
import fcntl, sys, time
f = open(sys.argv[1], "w")
fcntl.flock(f, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(60)
"""


def _holding(lock):
    """A process that holds `lock` until it is ended, as a running test run does."""
    holder = subprocess.Popen([sys.executable, "-c", HOLD, str(lock)], stdout=subprocess.PIPE, text=True)
    assert line_within(holder.stdout).strip() == "held"
    return holder


def _left(tmpdir, name, *, locked=True):
    """A folder a killed run left, with a mode-0 folder inside, and its lock file."""
    left = tmpdir / f"{PREFIX}{name}"
    (left / "shut").mkdir(parents=True)
    if locked:
        (left / ".lock").write_text("")
    (left / "shut" / "inside.txt").write_text("x")
    (left / "shut").chmod(0)
    return left


def test_a_killed_run_s_temporary_folder_is_swept_at_the_next_run(run, tmp_path):
    tmpdir = tmp_path / "tmp"
    _left(tmpdir, "killed")
    shared = tmpdir / "pytest-of-someone"
    shared.mkdir()
    ran, _ = run(WRITES)
    assert ran.returncode == 0, ran.stdout[-1500:]
    assert [p.name for p in tmpdir.iterdir()] == [shared.name]


def test_a_folder_whose_lock_is_held_is_never_swept(run, tmp_path):
    tmpdir = tmp_path / "tmp"
    running = _left(tmpdir, "running")
    holder = _holding(running / ".lock")
    try:
        ran, _ = run(WRITES)
    finally:
        holder.kill()
        holder.wait(SECONDS)
    assert ran.returncode == 0, ran.stdout[-1500:]
    assert [p.name for p in tmpdir.iterdir()] == [running.name]


def test_only_a_real_folder_of_this_user_s_is_swept(run, tmp_path):
    """A link is never followed, so a folder it points at keeps its files and its rights."""
    tmpdir, outside = tmp_path / "tmp", tmp_path / "outside"
    (outside / "kept").mkdir(parents=True)
    (outside / ".lock").write_text("")
    (outside / "kept").chmod(0o555)
    linked = tmpdir / f"{PREFIX}linked"
    linked.symlink_to(outside)
    ran, _ = run(WRITES)
    assert ran.returncode == 0, ran.stdout[-1500:]
    assert [p.name for p in tmpdir.iterdir()] == [linked.name]
    assert ((outside / "kept").stat().st_mode & 0o777, (outside / ".lock").exists()) == (0o555, True)


def test_a_run_given_its_own_temporary_folder_keeps_to_it(run, tmp_path):
    """The mutation run passes a folder of its own and removes it itself."""
    given = tmp_path / "given"
    ran, tmpdir = run(WRITES, f"--basetemp={given}")
    assert ran.returncode == 0, ran.stdout[-1500:]
    assert list(tmpdir.iterdir()) == []
    assert given.is_dir()


def test_files_a_test_makes_with_tempfile_are_in_the_run_s_folder_too(run):
    ran, tmpdir = run(
        "import os, tempfile\n"
        "def test_it():\n"
        "    made = tempfile.mkdtemp()\n"
        "    assert os.environ['TMPDIR'] == tempfile.gettempdir()\n"
        f"    assert '{PREFIX}' in made\n"
    )
    assert ran.returncode == 0, ran.stdout[-1500:]
    assert list(tmpdir.iterdir()) == []


def _conftest():
    spec = importlib.util.spec_from_file_location("heatpump_conftest", CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_after_a_run_the_temporary_folder_and_the_stop_signals_are_as_they_were(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    before = {name: signal.getsignal(getattr(signal, name)) for name in ("SIGTERM", "SIGHUP")}
    conftest = _conftest()
    config = types.SimpleNamespace(option=types.SimpleNamespace(basetemp=None), stash=pytest.Stash())
    conftest.pytest_configure(config)
    assert tempfile.gettempdir() != str(tmp_path)
    with (
        pytest.raises(BlockingIOError),
        (pathlib.Path(config.option.basetemp).parent / ".lock").open() as lock,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    conftest.pytest_unconfigure(config)
    assert (tempfile.tempdir, os.environ["TMPDIR"]) == (str(tmp_path), str(tmp_path))
    assert {name: signal.getsignal(getattr(signal, name)) for name in ("SIGTERM", "SIGHUP")} == before
    assert list(tmp_path.iterdir()) == []


INSTALLS_THEN_CHECKS = """
import signal

def test_a_main_installs_its_stop_handlers():
    for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(each, lambda *_: None)

def test_the_next_test_starts_with_the_run_s_own():
    for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        assert getattr(signal.getsignal(each), "__name__", "") != "<lambda>", each
"""


def test_a_test_that_installs_stop_handlers_leaves_none_for_the_next_test(run):
    """A test that runs a script's `main()` installs its stop handlers in the test process.

    Left there, Ctrl-C no longer stops pytest, and a later test of those handlers starts from
    the wrong ones, which the mutation run reports as a rule killed by another test.
    """
    ran, _ = run(INSTALLS_THEN_CHECKS)
    assert ran.returncode == 0, ran.stdout[-1500:]


def test_a_run_s_lock_never_stands_unlocked_under_its_own_name(tmp_path, monkeypatch):
    """A sweep that found the lock file before the run had locked it would take the folder."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    conftest = _conftest()
    locked = []
    flock = fcntl.flock

    def watched(fd, operation):
        locked.append(pathlib.Path(f"/proc/self/fd/{fd}").readlink().name)
        return flock(fd, operation)

    monkeypatch.setattr(conftest.fcntl, "flock", watched)
    config = types.SimpleNamespace(option=types.SimpleNamespace(basetemp=None), stash=pytest.Stash())
    conftest.pytest_configure(config)
    try:
        folder = pathlib.Path(config.option.basetemp).parent
        assert sorted(p.name for p in folder.iterdir()) == [".lock", "tmp"]
    finally:
        conftest.pytest_unconfigure(config)
    assert locked == [".lock-starting"]


def test_the_sweep_never_makes_a_lock(run, tmp_path):
    """A lock the sweep made itself is one nobody holds, so it would take a starting run's folder."""
    young = tmp_path / "tmp" / f"{PREFIX}young"
    young.mkdir()
    ran, _ = run(WRITES)
    assert ran.returncode == 0, ran.stdout[-1500:]
    assert young.is_dir()
    assert list(young.iterdir()) == []


def test_a_folder_with_no_lock_is_left_for_an_hour(run, tmp_path):
    """It may be a run between making its folder and locking it; after an hour it is not."""
    tmpdir = tmp_path / "tmp"
    recent, old = tmpdir / f"{PREFIX}recent", tmpdir / f"{PREFIX}old"
    for folder, age in ((recent, 30 * 60), (old, 2 * 60 * 60)):
        folder.mkdir()
        then = time.time() - age
        os.utime(folder, (then, then))
    ran, _ = run(WRITES)
    assert ran.returncode == 0, ran.stdout[-1500:]
    assert [p.name for p in tmpdir.iterdir()] == [recent.name]


def test_a_folder_another_sweep_removed_meanwhile_is_passed_over(tmp_path, monkeypatch):
    """Two runs starting together sweep the same folders; the one that loses the race goes on."""
    conftest = _conftest()
    for name in ("first", "second"):
        (tmp_path / f"{PREFIX}{name}").mkdir()
    removed = []

    def remove(folder):
        if folder.name.endswith("first"):
            raise FileNotFoundError(folder)
        removed.append(folder.name)

    monkeypatch.setattr(conftest, "_left_by_a_killed_run", lambda _folder: True)
    monkeypatch.setattr(conftest, "_remove", remove)
    conftest._sweep(tmp_path)  # noqa: SLF001
    assert removed == [f"{PREFIX}second"]

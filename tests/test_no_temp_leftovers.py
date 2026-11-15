# SPDX-License-Identifier: GPL-3.0-or-later
"""A test run leaves nothing in the computer's temporary folder.

Pytest's default keeps every run's temporary files under one folder that all
projects on the computer share, and a day of runs filled it. So each run here
keeps them in a folder of its own, removed when the run ends, also when the run
is interrupted. A folder a killed run left behind is swept at the start of the
next run, unless the run that made it is still going. The shared folder is never
swept, because other projects' runs are using it.

Each test runs a small pytest project with this suite's own conftest, in a
process of its own, with the temporary folder pointed at an empty one.
"""

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

CONFTEST = pathlib.Path(__file__).with_name("conftest.py")
WRITES = "def test_it(tmp_path):\n    (tmp_path / 'f').write_text('x')\n"


@pytest.fixture
def run(tmp_path):
    """Run one test file in a small project; give back how it ended and the temporary folder."""
    tmpdir, project = tmp_path / "tmp", tmp_path / "project"
    tmpdir.mkdir()
    project.mkdir()
    shutil.copy(CONFTEST, project / "conftest.py")

    def _run(test, *args):
        (project / "test_one.py").write_text(test)
        ran = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
            cwd=project,
            env={**os.environ, "TMPDIR": str(tmpdir)},
            capture_output=True,
            text=True,
            check=False,
        )
        return ran.returncode, tmpdir

    return _run


def test_a_test_run_leaves_nothing_in_the_temporary_folder(run):
    ended, tmpdir = run(WRITES)
    assert ended == 0
    assert list(tmpdir.iterdir()) == []


@pytest.mark.parametrize("ending", ["    assert False\n", "    raise KeyboardInterrupt\n"])
def test_a_failed_or_interrupted_test_run_leaves_nothing_either(run, ending):
    ended, tmpdir = run(WRITES + ending)
    assert ended != 0
    assert list(tmpdir.iterdir()) == []


def test_a_killed_run_s_temporary_folder_is_swept_at_the_next_run(run, tmp_path):
    tmpdir = tmp_path / "tmp"
    killed = tmpdir / "heatpump-mcp-pytest-999999999-abc"
    (killed / "shut").mkdir(parents=True)
    (killed / "shut" / "inside.txt").write_text("x")
    (killed / "shut").chmod(0)
    running = tmpdir / f"heatpump-mcp-pytest-{os.getpid()}-abc"
    running.mkdir()
    shared = tmpdir / "pytest-of-someone"
    shared.mkdir()
    ended, _ = run(WRITES)
    assert ended == 0
    assert sorted(p.name for p in tmpdir.iterdir()) == sorted([running.name, shared.name])


def test_a_run_given_its_own_temporary_folder_keeps_to_it(run, tmp_path):
    """The mutation run passes a folder of its own and removes it itself."""
    given = tmp_path / "given"
    ended, tmpdir = run(WRITES, f"--basetemp={given}")
    assert ended == 0
    assert list(tmpdir.iterdir()) == []
    assert given.is_dir()

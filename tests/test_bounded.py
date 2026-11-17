# SPDX-License-Identifier: GPL-3.0-or-later
"""The bounded waits every other test leans on, so a hang fails one test, not the run."""

import os
import signal
import subprocess
import sys
import time

import pytest
from bounded import line_within, run_bounded

TWO_LINES = "import sys; sys.stdout.write('one\\ntwo\\n'); sys.stdout.flush(); import time; time.sleep(600)"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are a POSIX thing")
def test_a_child_that_runs_too_long_leaves_nothing_of_its_own_running(tmp_path):
    """A timeout used to end the child alone, and what it had started ran on into later tests.

    The shell's own grandchild holds the output open, as `uv run` or `shell=True` would.
    """
    grandchild = tmp_path / "grandchild"
    script = f"sleep 600 & echo $! > {grandchild}; wait"
    with pytest.raises(pytest.fail.Exception, match="still ran after 1 s"):
        run_bounded(["sh", "-c", script], within=1, capture_output=True, text=True)
    left = int(grandchild.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(left, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(left, signal.SIGKILL)  # by its own number: it is this test's leftover, nothing else
    pytest.fail("the child's own child still ran after it")


def test_a_child_that_never_writes_a_line_fails_within_the_bound():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"], stdout=subprocess.PIPE, text=True
    )
    try:
        started = time.monotonic()
        with pytest.raises(pytest.fail.Exception, match="no line within 1 s"):
            line_within(child.stdout, 1)
        assert time.monotonic() - started < 30
    finally:
        child.kill()
        child.wait(60)


def test_two_lines_written_at_once_are_both_read():
    """A line already held in the stream's own buffer is ready, though the pipe below is empty."""
    child = subprocess.Popen([sys.executable, "-c", TWO_LINES], stdout=subprocess.PIPE, text=True)
    try:
        assert line_within(child.stdout, 5) == "one\n"
        assert line_within(child.stdout, 5) == "two\n"
    finally:
        child.kill()
        child.wait(60)

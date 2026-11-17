# SPDX-License-Identifier: GPL-3.0-or-later
"""Waits a test sets its own limit on, so a hang fails that test instead of the whole run.

A mutant that makes the code wait for ever would otherwise hang the mutation run, and in CI
nothing said so for hours. Running out here fails the test, which counts the mutant as
killed, as a crash does. This is a limit on one wait inside one test, never on a run.
"""

import asyncio
import os
import queue
import signal
import subprocess
import threading
from collections.abc import Coroutine, Sequence
from typing import IO, Any

import pytest

#: One bound for every repo: generous enough that it fires only on a real hang.
SECONDS = 300


def within[T](work: Coroutine[Any, Any, T], seconds: float = SECONDS) -> T:
    """Run `work` to its end, or fail once `seconds` have gone by."""
    return asyncio.run(asyncio.wait_for(work, seconds))


def line_within(stream: IO[str], seconds: float = SECONDS) -> str:
    """The next line from `stream`, or a failure if none comes within `seconds`.

    A pipe's readline can't be given a limit, so it runs on a thread of its own. Asking the
    pipe below whether it has data would miss a line already held in the stream's buffer. A
    thread still waiting when the test fails stays until the test's cleanup ends the child.
    """
    got: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: got.put(stream.readline()), daemon=True).start()
    try:
        return got.get(timeout=seconds)
    except queue.Empty:
        pytest.fail(f"no line within {seconds} s")


def run_bounded(
    command: Sequence[str] | str, within: float = SECONDS, **kwargs: Any
) -> subprocess.CompletedProcess:
    """Run `command` to its end, or fail once `within` seconds have gone by, leaving nothing.

    A timeout alone ends the child but not what it started, such as the command under
    `shell=True` or the Python under `uv run`, which then runs on into later tests. So the
    child gets a session of its own, and on time the whole session is killed. SIGKILL can't
    be caught, so a launcher can't pass it on twice, as it does with Ctrl-C.
    """
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    with subprocess.Popen(command, start_new_session=True, **kwargs) as child:
        try:
            out, err = child.communicate(timeout=within)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)  # its own session, never this test's group
            out, err = child.communicate()
            pytest.fail(f"{command!r} still ran after {within} s; it wrote:\n{out}\n{err}")
    return subprocess.CompletedProcess(command, child.returncode, out, err)

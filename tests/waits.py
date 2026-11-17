# SPDX-License-Identifier: GPL-3.0-or-later
"""Waits a test sets its own limit on, so a hang fails that test instead of the whole run.

A mutant that makes the code wait for ever would otherwise hang the mutation run, and in CI
nothing said so for hours. Running out here fails the test, which counts the mutant as
killed, as a crash does. This is a limit on one wait inside one test, never on a run.
"""

import asyncio
import select
from collections.abc import Coroutine
from typing import IO, Any

#: Long enough for a slow machine, short enough that a hang is seen.
SECONDS = 60


def within[T](work: Coroutine[Any, Any, T], seconds: float = SECONDS) -> T:
    """Run `work` to its end, or fail once `seconds` have gone by."""
    return asyncio.run(asyncio.wait_for(work, seconds))


def line_within(stream: IO[str], seconds: float = SECONDS) -> str:
    """The next line `stream` gives, or a failure if none comes within `seconds`."""
    ready, _, _ = select.select([stream], [], [], seconds)
    assert ready, f"no line came within {seconds} s"
    return stream.readline()

# SPDX-License-Identifier: GPL-3.0-or-later
"""The wait after a refused login still holds after the plugin restarts.

Without this, a plugin that crashed and was started again would try a wrong
password at once, every time, and the wait would protect nothing. So the time
the wait ends is kept in a file, and a plugin started again reads it. The file
holds that one time and nothing about the login. A login entered after the
wait began is a new login and is tried at once. No network is touched.
"""

import asyncio
import os
import stat
import time
from datetime import UTC, datetime

import pytest
from test_fglair import FakeDevice
from test_login_backoff import Clock, Refusing

from heatpump_mcp.fglair import LOGIN_BACKOFF_LONGEST, LOGIN_BACKOFF_SECONDS, FglairUnit, LoginWait
from heatpump_mcp.settings import state_folder
from heatpump_mcp.unit import UnitUnavailableError


def _unit(api, clock, wait):
    return FglairUnit(api, is_heat_pump=lambda d: isinstance(d, FakeDevice), clock=clock, wait=wait)


def _refused(call):
    with pytest.raises(UnitUnavailableError) as caught:
        asyncio.run(call())
    return str(caught.value)


@pytest.fixture
def login(tmp_path):
    """A login file, written before any wait began."""
    path = tmp_path / "fglair-password"
    path.write_text("wrong")
    _age(path, 3600)
    return path


def _age(path, seconds):
    stamp = path.stat().st_mtime_ns - int(seconds * 1e9)
    os.utime(path, ns=(stamp, stamp))


def _refused_then_restarted(tmp_path, login, clock):
    """A login refused once, and a plugin started again with a fresh cloud connection."""
    wait = tmp_path / "state" / "login-wait"
    _refused(_unit(Refusing(), clock, LoginWait(wait, login_files=[login])).read)
    after = Refusing()
    return after, _unit(after, clock, LoginWait(wait, login_files=[login])), wait


def test_a_refused_login_is_not_tried_again_after_a_restart_until_its_wait_is_over(tmp_path, login):
    clock = Clock()
    api, unit, _ = _refused_then_restarted(tmp_path, login, clock)
    clock.now += 60
    say = _refused(unit.read)
    assert api.tries == 0
    assert "wait 4 minutes" in say
    clock.now += LOGIN_BACKOFF_SECONDS
    _refused(unit.read)
    assert api.tries == 1


def test_the_wait_file_holds_only_the_time_the_wait_ends(tmp_path, login):
    clock = Clock()
    _, _, wait = _refused_then_restarted(tmp_path, login, clock)
    assert float(wait.read_text()) == clock.now + LOGIN_BACKOFF_SECONDS
    assert "wrong" not in wait.read_text()


def test_a_login_entered_after_the_wait_began_ends_the_wait(tmp_path, login):
    clock = Clock()
    _, _, wait = _refused_then_restarted(tmp_path, login, clock)
    login.write_text("entered again")
    api = Refusing()
    _refused(_unit(api, clock, LoginWait(wait, login_files=[login])).read)
    assert api.tries == 1


def test_a_login_that_works_removes_the_wait_file(tmp_path, login):
    clock = Clock()
    api, unit, wait = _refused_then_restarted(tmp_path, login, clock)
    clock.now += LOGIN_BACKOFF_SECONDS
    api.refuse_login = False
    asyncio.run(unit.read())
    assert not wait.exists()


def test_a_wait_far_in_the_future_is_held_no_longer_than_the_longest(tmp_path, login):
    clock = Clock()
    wait = tmp_path / "login-wait"
    wait.write_text(f"{clock.now + 10 * LOGIN_BACKOFF_LONGEST}\n")
    api = Refusing()
    unit = _unit(api, clock, LoginWait(wait, login_files=[login]))
    clock.now += LOGIN_BACKOFF_LONGEST
    _refused(unit.read)
    assert api.tries == 1


@pytest.mark.parametrize("content", ["", "not a time\n"])
def test_a_wait_file_that_makes_no_sense_is_no_wait(tmp_path, login, content):
    wait = tmp_path / "login-wait"
    wait.write_text(content)
    api = Refusing()
    _refused(_unit(api, Clock(), LoginWait(wait, login_files=[login])).read)
    assert api.tries == 1


def test_a_wait_file_that_cannot_be_written_still_leaves_the_wait_in_memory(tmp_path, login):
    blocked = tmp_path / "a-file"
    blocked.write_text("")
    api, clock = Refusing(), Clock()
    unit = _unit(api, clock, LoginWait(blocked / "login-wait", login_files=[login]))
    _refused(unit.read)
    _refused(unit.read)
    assert api.tries == 1


def test_the_wait_file_is_in_the_state_folder():
    assert state_folder({"HOME": "/home/someone"}).as_posix() == "/home/someone/.local/state/heatpump-mcp"
    assert state_folder({"XDG_STATE_HOME": "/elsewhere"}).as_posix() == "/elsewhere/heatpump-mcp"


def test_a_kept_wait_ends_at_most_the_longest_wait_after_it_began(tmp_path, login):
    """A file that says the year 2100, written two hours ago, is no wait."""
    wait = tmp_path / "login-wait"
    wait.write_text(f"{datetime(2100, 1, 1, tzinfo=UTC).timestamp()}\n")
    _age(wait, 2 * 3600)
    _age(login, 3 * 3600)
    api = Refusing()
    _refused(_unit(api, time.time, LoginWait(wait, login_files=[login])).read)
    assert api.tries == 1


def test_the_wait_file_is_readable_by_the_owner_alone(tmp_path, login):
    _, _, wait = _refused_then_restarted(tmp_path, login, Clock())
    assert stat.S_IMODE(wait.stat().st_mode) == 0o600
    assert [p.name for p in wait.parent.iterdir()] == ["login-wait"]


def test_a_wait_written_while_the_clock_was_ahead_is_no_wait(tmp_path, login):
    """The file's change time is later than now, so the clock was wrong when it was written."""
    wait = tmp_path / "login-wait"
    wait.write_text(f"{time.time() + LOGIN_BACKOFF_SECONDS}\n")
    _age(wait, -3600)
    api = Refusing()
    _refused(_unit(api, time.time, LoginWait(wait, login_files=[login])).read)
    assert api.tries == 1

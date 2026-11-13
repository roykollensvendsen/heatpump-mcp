# SPDX-License-Identifier: GPL-3.0-or-later
"""A login the cloud refused is not sent again on every question.

A wrong password tried again at each status or change could get the FGLair
account locked, and the owner's own app with it. So after a refusal the driver
waits before it signs in again, longer each time, and says why; a login entered
again is a new login and is tried at once. No network is touched.
"""

import asyncio

import pytest
from test_fglair import FakeApi, FakeDevice

from heatpump_mcp.fglair import LOGIN_BACKOFF_LONGEST, LOGIN_BACKOFF_SECONDS, FglairUnit
from heatpump_mcp.unit import Change, ChangeNotSentError, Setting, UnitUnavailableError


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Refusing(FakeApi):
    """A cloud that refuses the login, and counts every time it is tried."""

    def __init__(self):
        super().__init__(refuse_login=True)
        self.tries = 0

    async def async_sign_in(self):
        self.tries += 1
        await super().async_sign_in()


def _unit(api, clock):
    return FglairUnit(api, is_heat_pump=lambda d: isinstance(d, FakeDevice), clock=clock)


def _refused(call):
    with pytest.raises(UnitUnavailableError) as caught:
        asyncio.run(call())
    return str(caught.value)


def test_a_refused_login_is_not_tried_again_until_its_wait_is_over():
    api, clock = Refusing(), Clock()
    unit = _unit(api, clock)
    for _ in range(5):
        _refused(unit.read)
        clock.now += 10
    assert api.tries == 1


def test_while_waiting_the_reason_and_the_wait_are_said():
    api, clock = Refusing(), Clock()
    unit = _unit(api, clock)
    _refused(unit.read)
    say = _refused(unit.read)
    assert "refused the login" in say
    assert "minutes" in say
    assert "locked" in say
    assert "claude-voice manager" in say


def test_the_last_minute_of_the_wait_is_said_as_one_minute():
    api, clock = Refusing(), Clock()
    unit = _unit(api, clock)
    _refused(unit.read)
    clock.now += LOGIN_BACKOFF_SECONDS - 30
    assert "wait 1 minute before" in _refused(unit.read)


def test_after_the_wait_the_login_is_tried_once_more():
    api, clock = Refusing(), Clock()
    unit = _unit(api, clock)
    _refused(unit.read)
    clock.now += LOGIN_BACKOFF_SECONDS
    _refused(unit.read)
    assert api.tries == 2


def test_each_refusal_doubles_the_wait_up_to_the_longest():
    api, clock = Refusing(), Clock()
    unit = _unit(api, clock)
    wait = LOGIN_BACKOFF_SECONDS
    for tries in range(1, 8):
        _refused(unit.read)
        assert api.tries == tries
        clock.now += wait - 1
        _refused(unit.read)
        assert api.tries == tries
        clock.now += 1
        wait = min(wait * 2, LOGIN_BACKOFF_LONGEST)
    assert wait == LOGIN_BACKOFF_LONGEST


def test_a_change_while_waiting_is_never_sent_and_does_not_sign_in():
    api, clock = Refusing(), Clock()
    unit = _unit(api, clock)
    _refused(unit.read)
    with pytest.raises(ChangeNotSentError):
        asyncio.run(unit.ready())
    with pytest.raises(ChangeNotSentError):
        asyncio.run(unit.apply(Change(Setting.TEMPERATURE, 22.0)))
    assert api.tries == 1
    assert api.devices[0].calls == []


def test_a_login_that_works_again_ends_the_wait():
    api, clock = Refusing(), Clock()
    unit = _unit(api, clock)
    _refused(unit.read)
    clock.now += LOGIN_BACKOFF_SECONDS
    api.refuse_login = False
    asyncio.run(unit.read())
    api.refuse_login = True
    api.token_expired = True
    _refused(unit.read)
    assert api.tries == 3  # the wait starts again from the shortest


def test_a_login_entered_again_is_tried_at_once(tmp_path):
    from heatpump_mcp.fglair import LoginFolder  # noqa: PLC0415

    clock, apis = Clock(), []

    def build(_settings):
        apis.append(Refusing())
        return _unit(apis[-1], clock)

    for name, text in (("fglair-username", "someone@example.com"), ("fglair-password", "wrong")):
        (tmp_path / name).write_text(text)
        (tmp_path / name).chmod(0o600)
    folder = LoginFolder(tmp_path, build=build)
    _refused(folder.read)
    (tmp_path / "fglair-password").write_text("entered again")
    _refused(folder.read)
    assert [api.tries for api in apis] == [1, 1]

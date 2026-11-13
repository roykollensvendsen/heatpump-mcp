# SPDX-License-Identifier: GPL-3.0-or-later
"""Saying yes to a change fits its time, and the change still leaves in a token's last minutes.

At the owner's yes, signing in and finding the unit has only READY_SECONDS
before the change must be sent, so the whole answer fits the time the voice
allows. The cloud library refuses to send anything on a token in its last ten
minutes, so a token in that window must be renewed before the change goes.
A renewal is a call to the cloud, which can be slow, so the question renews the
token early enough that it is never in that window at the yes. No network is
touched; the cloud here is a fake that keeps the library's limit and, where
said, really takes time.
"""

import asyncio
from datetime import datetime, timedelta

from ayla_iot_unofficial import AylaAuthError
from ayla_iot_unofficial.exc import AylaAuthExpiringError, AylaNotAuthedError
from test_fglair import FakeDevice

import heatpump_mcp.service as service_module
from heatpump_mcp.fglair import LIBRARY_REFUSES_WITHIN, RENEW_AHEAD_SECONDS, FglairUnit
from heatpump_mcp.service import STATUS_CACHE_SECONDS, TOKEN_SECONDS, Done, HeatpumpService, Refused
from heatpump_mcp.unit import Change, Setting

#: Longer than the sign-in budget the slow tests set, so a renewal at the yes misses it.
SLOW = 0.3
#: Seconds a token lasts once signed in or renewed.
LIFETIME = 3600.0
#: A token outside the library's window, but too close to it to last until a yes.
JUST_OUTSIDE = LIBRARY_REFUSES_WITHIN + 20


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class LibraryLike:
    """The cloud as ayla_iot_unofficial sees it: every request checks the token first.

    `left` is the token's remaining life in seconds; tests move time by lowering it.
    """

    def __init__(self, *, renewal_takes=0.0, refuse_login=False):
        self.device = Heeding(self)
        self.renewal_takes = renewal_takes
        self.refuse_login = refuse_login
        self.left = 0.0
        self.sign_ins = 0
        self.refreshes = 0

    @property
    def auth_expiration(self):
        return None if self.left <= 0 else datetime.now() + timedelta(seconds=self.left)  # noqa: DTZ005 - naive, as the library

    @property
    def token_expired(self):
        return self.left <= 0

    @property
    def token_expiring_soon(self):
        return self.left < LIBRARY_REFUSES_WITHIN

    def check_auth(self):
        """The library's own check, run for the headers of every request it makes."""
        if self.token_expired:
            raise AylaNotAuthedError
        if self.token_expiring_soon:
            raise AylaAuthExpiringError

    async def async_sign_in(self):
        if self.refuse_login:
            raise AylaAuthError
        self.sign_ins += 1
        self.left = LIFETIME

    async def async_refresh_auth(self):
        await asyncio.sleep(self.renewal_takes)
        self.refreshes += 1
        self.left = LIFETIME

    async def async_get_devices(self):
        self.check_auth()
        return [self.device]


class Heeding(FakeDevice):
    """A unit that takes the temperature it is sent, behind the library's token check."""

    def __init__(self, api):
        super().__init__()
        self.api = api

    async def async_update(self):
        self.api.check_auth()
        await super().async_update()

    async def _sent(self, name, value):
        self.api.check_auth()
        await super()._sent(name, value)

    async def async_set_set_temp(self, value):
        await super().async_set_set_temp(value)
        self.set_temp = value


def _unit(api):
    return FglairUnit(api, is_heat_pump=lambda d: isinstance(d, FakeDevice))


def _read_with(api, left):
    """A unit read once, as a question does, with the token at `left` seconds."""
    unit = _unit(api)
    asyncio.run(unit.read())
    api.left = left
    return unit


def _change(unit):
    asyncio.run(unit.ready())
    asyncio.run(unit.apply(Change(Setting.TEMPERATURE, 22.0)))


def test_a_change_is_sent_without_renewing_a_login_the_library_still_accepts():
    api = LibraryLike()
    unit = _read_with(api, JUST_OUTSIDE)
    _change(unit)
    assert (api.sign_ins, api.refreshes) == (1, 0)
    assert api.device.calls == [("set_temp", 22.0)]


def test_a_change_in_the_tokens_last_ten_minutes_still_goes_out():
    api = LibraryLike()
    unit = _read_with(api, LIBRARY_REFUSES_WITHIN - 300)
    _change(unit)
    assert api.refreshes == 1
    assert api.device.calls == [("set_temp", 22.0)]


def test_a_question_renews_a_login_that_would_run_out_before_its_yes():
    api = LibraryLike()
    unit = _read_with(api, JUST_OUTSIDE)
    asyncio.run(unit.read())
    assert api.refreshes == 1
    assert api.left == LIFETIME


def test_the_renewal_ahead_outlasts_a_cached_reading_and_the_question():
    assert RENEW_AHEAD_SECONDS > STATUS_CACHE_SECONDS + TOKEN_SECONDS


def _asked(api, clock):
    service = HeatpumpService(_unit(api), clock=clock, sleep=clock.sleep)
    question = asyncio.run(service.request(Setting.TEMPERATURE, "22"))
    return service, question


def test_a_yes_soon_after_a_question_is_carried_out_on_a_day_the_cloud_is_slow(monkeypatch):
    monkeypatch.setattr(service_module, "READY_SECONDS", SLOW / 3)
    api, clock = LibraryLike(renewal_takes=SLOW), Clock()
    asyncio.run(_unit(api).read())
    api.left = JUST_OUTSIDE  # the token nears the library's window before the question
    service, question = _asked(api, clock)
    api.left -= TOKEN_SECONDS  # the yes comes as late as a question allows
    clock.now += TOKEN_SECONDS - 1
    answer = asyncio.run(service.confirm(question.token))
    assert isinstance(answer, Done), answer.say
    assert answer.confirmed, answer.say
    assert api.device.calls == [("set_temp", 22.0)]


def test_a_renewal_at_the_yes_that_misses_its_time_is_said_to_be_not_sent(monkeypatch):
    monkeypatch.setattr(service_module, "READY_SECONDS", SLOW / 3)
    api, clock = LibraryLike(renewal_takes=SLOW), Clock()
    service, question = _asked(api, clock)
    api.left = LIBRARY_REFUSES_WITHIN - 300  # only if the token ran short some other way
    answer = asyncio.run(service.confirm(question.token))
    assert isinstance(answer, Refused)
    assert answer.say.startswith("I could not reach the heat pump, so nothing changed.")
    assert api.device.calls == []

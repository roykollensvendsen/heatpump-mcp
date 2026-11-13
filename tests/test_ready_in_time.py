# SPDX-License-Identifier: GPL-3.0-or-later
"""Saying yes to a change never waits on renewing a login that still works.

At the owner's yes, signing in and finding the unit has only READY_SECONDS
before the change must be sent, so the whole answer fits the time the voice
allows. The cloud's token is renewed early, while it still has minutes to run.
A renewal is a call to the cloud, and on a slow day it can take longer than
that budget, which would refuse every change as "nothing changed". A change
takes seconds, so it is sent on the token that is still valid. No network is
touched; the slow cloud here is a fake that really takes time.
"""

import asyncio

from test_fglair import FakeApi, FakeDevice

import heatpump_mcp.service as service_module
from heatpump_mcp.fglair import FglairUnit
from heatpump_mcp.service import Done, HeatpumpService, Question
from heatpump_mcp.unit import Change, Setting

#: Longer than the sign-in budget the test sets, so a renewal at the yes times out.
SLOW = 0.3


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class Heeding(FakeDevice):
    """A unit that takes the temperature it is sent, so reading back shows it."""

    async def async_set_set_temp(self, value):
        await super().async_set_set_temp(value)
        self.set_temp = value


class SlowRenewal(FakeApi):
    """A cloud whose renewal of the token takes longer than the yes may wait."""

    async def async_refresh_auth(self):
        await asyncio.sleep(SLOW)
        await super().async_refresh_auth()


def _unit(api):
    return FglairUnit(api, is_heat_pump=lambda d: isinstance(d, FakeDevice))


def _signed_in_and_read(api):
    unit = _unit(api)
    asyncio.run(unit.read())
    api.token_expiring_soon = True  # minutes pass; the token still works
    return unit


def test_a_change_is_sent_without_renewing_a_login_that_is_still_valid():
    api = FakeApi()
    unit = _signed_in_and_read(api)
    sign_ins, refreshes = api.sign_ins, api.refreshes
    asyncio.run(unit.ready())
    asyncio.run(unit.apply(Change(Setting.TEMPERATURE, 22.0)))
    assert (api.sign_ins, api.refreshes) == (sign_ins, refreshes)
    assert api.devices[0].calls == [("set_temp", 22.0)]


def test_a_token_that_has_run_out_is_signed_in_again_before_a_change():
    api = FakeApi()
    unit = _signed_in_and_read(api)
    api.token_expired = True
    asyncio.run(unit.ready())
    assert api.sign_ins == 2


def test_a_yes_is_carried_out_on_a_day_the_cloud_is_slow_to_renew(monkeypatch):
    monkeypatch.setattr(service_module, "READY_SECONDS", SLOW / 3)
    api = SlowRenewal(Heeding())
    clock = Clock()
    service = HeatpumpService(_unit(api), clock=clock, sleep=clock.sleep)
    question = asyncio.run(service.request(Setting.TEMPERATURE, "22"))
    assert isinstance(question, Question)
    api.token_expiring_soon = True
    answer = asyncio.run(service.confirm(question.token))
    assert isinstance(answer, Done), answer.say
    assert "Nothing changed" not in answer.say
    assert api.devices[0].calls == [("set_temp", 22.0)]

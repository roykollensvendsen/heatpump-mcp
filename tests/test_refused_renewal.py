# SPDX-License-Identifier: GPL-3.0-or-later
"""A login the cloud will not renew is signed in again before the owner is asked for it.

The cloud can refuse to renew a login token for reasons of its own, while the
password is still right. Then one full sign-in is tried, and only if the cloud
refuses that too is the owner asked to enter the login again. That one sign-in
goes through the wait after a refused login, so a wrong password is still not
sent over and over. No network is touched.
"""

import asyncio

import pytest
from ayla_iot_unofficial import AylaAuthError
from test_fglair import FakeApi, FakeDevice

from heatpump_mcp.fglair import LOGIN_BACKOFF_SECONDS, FglairUnit
from heatpump_mcp.unit import UnitUnavailableError


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class WontRenew(FakeApi):
    """A cloud that refuses to renew the token, and counts every sign-in tried."""

    def __init__(self, *, refuse_login=False):
        super().__init__(refuse_login=refuse_login)
        self.tries = 0

    async def async_sign_in(self):
        self.tries += 1
        await super().async_sign_in()

    async def async_refresh_auth(self):
        self.refreshes += 1
        raise AylaAuthError


def _signed_in_once(api, clock):
    """A unit read once, so the login is in use, and then nearing its end."""
    unit = FglairUnit(api, is_heat_pump=lambda d: isinstance(d, FakeDevice), clock=clock)
    asyncio.run(unit.read())
    api.token_expiring_soon = True
    api.tries = 0
    return unit


def _refused(call):
    with pytest.raises(UnitUnavailableError) as caught:
        asyncio.run(call())
    return str(caught.value)


def test_a_refused_renewal_is_followed_by_one_full_sign_in():
    api, clock = WontRenew(), Clock()
    unit = _signed_in_once(api, clock)
    asyncio.run(unit.read())
    assert (api.refreshes, api.tries) == (1, 1)
    assert not api.token_expiring_soon


def test_a_refused_renewal_and_a_refused_sign_in_ask_for_the_login_once():
    api, clock = WontRenew(), Clock()
    unit = _signed_in_once(api, clock)
    api.refuse_login = True
    say = _refused(unit.read)
    assert "refused the login" in say
    assert "claude-voice manager" in say
    assert (api.refreshes, api.tries) == (1, 1)


def test_after_a_refused_renewal_and_sign_in_nothing_is_tried_until_the_wait_is_over():
    api, clock = WontRenew(), Clock()
    unit = _signed_in_once(api, clock)
    api.refuse_login = True
    for _ in range(5):
        _refused(unit.read)
        clock.now += 10
    assert (api.refreshes, api.tries) == (1, 1)
    clock.now += LOGIN_BACKOFF_SECONDS
    _refused(unit.read)
    assert (api.refreshes, api.tries) == (2, 2)

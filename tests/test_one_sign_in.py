# SPDX-License-Identifier: GPL-3.0-or-later
"""Two questions at the same moment sign in to the cloud only once.

The voice can ask for the status twice at once, or a status and a change can
overlap. Each needs a signed-in login. If both signed in, the cloud would get
the password twice, and a refused one would count as two tries. So one waits
for the other and uses the login it got. No network is touched.
"""

import asyncio

import pytest
from test_fglair import FakeApi, FakeDevice

from heatpump_mcp.fglair import FglairUnit
from heatpump_mcp.unit import UnitUnavailableError


class SlowToSignIn(FakeApi):
    """A cloud whose sign-in takes a moment, long enough for a second question to arrive."""

    def __init__(self, *, refuse_login=False):
        super().__init__(refuse_login=refuse_login)
        self.tries = 0

    async def async_sign_in(self):
        self.tries += 1
        await asyncio.sleep(0.01)
        await super().async_sign_in()


def _unit(api):
    return FglairUnit(api, is_heat_pump=lambda d: isinstance(d, FakeDevice))


async def _together(unit):
    return await asyncio.gather(unit.read(), unit.read(), return_exceptions=True)


def test_one_sign_in_at_a_time():
    api = SlowToSignIn()
    answers = asyncio.run(_together(_unit(api)))
    assert not [a for a in answers if isinstance(a, BaseException)]
    assert api.tries == 1


def test_two_questions_at_once_on_a_refused_login_send_it_once():
    api = SlowToSignIn(refuse_login=True)
    answers = asyncio.run(_together(_unit(api)))
    assert all(isinstance(a, UnitUnavailableError) for a in answers)
    assert all("refused the login" in str(a) for a in answers)
    assert api.tries == 1


@pytest.mark.parametrize("first", ["read", "ready"])
def test_a_change_and_a_status_at_once_sign_in_once(first):
    api = SlowToSignIn()
    unit = _unit(api)

    async def both():
        await asyncio.gather(getattr(unit, first)(), unit.read())

    asyncio.run(both())
    assert api.tries == 1

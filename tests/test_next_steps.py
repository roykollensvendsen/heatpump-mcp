# SPDX-License-Identifier: GPL-3.0-or-later
"""Every refusal and failure says what is wrong and what the owner can do next.

A bare "not answering" leaves the owner with nothing to try, so it counts as a
bug. Each case below makes one failure happen and checks the sentence it gives.
"""

import asyncio
import re

import pytest
from test_fglair import FakeApi, FakeDevice, _unit

from heatpump_mcp.service import TOKEN_SECONDS, HeatpumpService, Question, Refused
from heatpump_mcp.settings import SettingsError, load_settings
from heatpump_mcp.simulator import SimulatedUnit
from heatpump_mcp.unit import (
    ChangeNotSentError,
    ChangeNotSupportedError,
    Setting,
    UnitUnavailableError,
)

#: A next step starts a sentence, or follows a colon, with what to do.
NEXT_STEP = re.compile(r"(^|[.:;] )(Ask|Say|Check|Run|Write|Fix|Move)\b")


def tells_what_to_do(say: str) -> bool:
    """Whether `say` holds a next step for the owner."""
    return NEXT_STEP.search(say) is not None


def _run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def _service(unit=None, clock=None):
    clock = clock or Clock()
    return HeatpumpService(unit or SimulatedUnit(), clock=clock, sleep=clock.sleep)


def _confirmed(unit):
    service = _service(unit)
    question = _run(service.request(Setting.TEMPERATURE, "23"))
    assert isinstance(question, Question)
    return _run(service.confirm(question.token)).say


class Silent(SimulatedUnit):
    async def read(self):
        raise UnitUnavailableError


class Unsent(SimulatedUnit):
    def __init__(self, reason=""):
        super().__init__()
        self.reason = reason

    async def apply(self, _change):
        raise ChangeNotSentError(self.reason)


class Unsupported(SimulatedUnit):
    async def apply(self, _change):
        raise ChangeNotSupportedError


class Ignoring(SimulatedUnit):
    async def apply(self, _change):
        return


def _too_soon():
    service = _service()
    first = _run(service.request(Setting.TEMPERATURE, "23"))
    _run(service.confirm(first.token))
    again = _run(service.request(Setting.TEMPERATURE, "24"))
    return _run(service.confirm(again.token)).say


def _expired():
    clock = Clock()
    service = _service(clock=clock)
    question = _run(service.request(Setting.POWER, "off"))
    clock.now += TOKEN_SECONDS
    return _run(service.confirm(question.token)).say


def _refused(setting, value, unit=None):
    answer = _run(_service(unit).request(setting, value))
    assert isinstance(answer, Refused)
    return answer.say


def _cloud_read(api, name=None):
    with pytest.raises(UnitUnavailableError) as caught:
        _run(_unit(api, name).read())
    return str(caught.value)


def _no_units():
    api = FakeApi()
    api.devices = []
    return api


def _sent_and_lost():
    """A cloud that loses touch with the unit after it was asked once."""
    return _confirmed(Ignoring())


SERVICE_FAILURES = {
    "power that is not on or off": lambda: _refused(Setting.POWER, "maybe"),
    "a mode the unit has not": lambda: _refused(Setting.MODE, "turbo"),
    "a fan speed the unit has not": lambda: _refused(Setting.FAN, "hurricane"),
    "a temperature that is not a number": lambda: _refused(Setting.TEMPERATURE, "warm"),
    "a temperature outside 16 to 28": lambda: _refused(Setting.TEMPERATURE, "30"),
    "a unit that does not answer": lambda: _refused(Setting.POWER, "off", Silent()),
    "a status the unit does not answer": lambda: _run(_service(Silent()).status()).say,
    "a yes with no question": lambda: _run(_service().confirm("nothing")).say,
    "a yes after the question expired": _expired,
    "a second change too soon": _too_soon,
    "a change the unit cannot make": lambda: _confirmed(Unsupported()),
    "a change that never left": lambda: _confirmed(Unsent()),
    "a change that never left, said why": lambda: _confirmed(Unsent("Offline. Check that it has power.")),
    "a change the unit does not show": _sent_and_lost,
}


@pytest.mark.parametrize("make_it_fail", SERVICE_FAILURES.values(), ids=SERVICE_FAILURES.keys())
def test_every_refusal_says_what_to_do_next(make_it_fail):
    say = make_it_fail()
    assert tells_what_to_do(say), say


DRIVER_FAILURES = {
    "a refused login": lambda: _cloud_read(FakeApi(refuse_login=True)),
    "no heat pump on the login": lambda: _cloud_read(_no_units()),
    "no heat pump by the name set": lambda: _cloud_read(FakeApi(FakeDevice("Hall")), name="Attic"),
    "several heat pumps and none named": lambda: _cloud_read(FakeApi(FakeDevice("A"), FakeDevice("B"))),
    "an offline heat pump": lambda: _cloud_read(FakeApi(FakeDevice(online=False))),
}


@pytest.mark.parametrize("make_it_fail", DRIVER_FAILURES.values(), ids=DRIVER_FAILURES.keys())
def test_every_cloud_failure_says_what_to_do_next(make_it_fail):
    say = make_it_fail()
    assert tells_what_to_do(say), say


def test_a_refused_login_asks_for_it_to_be_entered_again_through_the_manager():
    say = _cloud_read(FakeApi(refuse_login=True))
    assert "refused the login" in say
    assert "claude-voice manager" in say


def _write(folder, name, text, mode=0o600):
    path = folder / name
    path.write_text(text)
    path.chmod(mode)
    return path


def _login(folder):
    _write(folder, "fglair-username", "someone@example.com")
    _write(folder, "fglair-password", "made-up")


def _settings_failure(folder):
    with pytest.raises(SettingsError) as caught:
        load_settings(folder)
    return str(caught.value)


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param(lambda _f: None, id="no login files"),
        pytest.param(
            lambda f: (_login(f), (f / "fglair-password").chmod(0o644)), id="a login others can read"
        ),
        pytest.param(lambda f: (_login(f), _write(f, "fglair-password", "")), id="an empty login file"),
        pytest.param(
            lambda f: (_login(f), _write(f, "config.toml", "region = ")), id="settings that do not parse"
        ),
        pytest.param(
            lambda f: (_login(f), _write(f, "config.toml", 'region = "mars"')), id="a region with no cloud"
        ),
    ],
)
def test_every_settings_problem_says_what_to_do_next(tmp_path, spoil):
    spoil(tmp_path)
    say = _settings_failure(tmp_path)
    assert tells_what_to_do(say), say


def test_a_login_others_can_read_gives_the_exact_command(tmp_path):
    _login(tmp_path)
    path = tmp_path / "fglair-password"
    path.chmod(0o644)
    assert f"chmod 600 {path}" in _settings_failure(tmp_path)


def test_a_change_that_never_left_says_why_when_the_cloud_gave_a_reason():
    say = _confirmed(Unsent("The FGLair cloud refused the login. Ask the claude-voice manager."))
    assert say.startswith("Nothing changed: The FGLair cloud refused the login.")

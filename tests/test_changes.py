# SPDX-License-Identifier: GPL-3.0-or-later
"""Changing the heat pump: a question first, a spoken yes, and a reading afterwards."""

import asyncio
import dataclasses

import pytest

from heatpump_mcp.service import (
    CHANGE_GAP_SECONDS,
    TOKEN_SECONDS,
    Done,
    HeatpumpService,
    Question,
    Refused,
)
from heatpump_mcp.simulator import SimulatedUnit
from heatpump_mcp.unit import ChangeNotSentError, Fan, Mode, Reading, Setting, UnitUnavailableError

START = Reading(on=True, mode=Mode.HEAT, set_temp=21.0, room_temp=20.5, outdoor_temp=3.0, fan=Fan.AUTO)


class Clock:
    """A clock the test moves; sleeping moves it too, so nothing really waits."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class IgnoringUnit(SimulatedUnit):
    """Takes the change and never shows it, like a unit the cloud lost touch with."""

    async def apply(self, change):
        self.applied.append(change)


class FailingUnit(SimulatedUnit):
    """The cloud refuses the first change it is sent, then works."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.failures = 1

    async def apply(self, change):
        if self.failures:
            self.failures -= 1
            raise ChangeNotSentError
        await super().apply(change)


def _service(unit=None, clock=None):
    clock = clock or Clock()
    unit = unit or SimulatedUnit(start=START)
    return HeatpumpService(
        unit, clock=clock, sleep=clock.sleep, new_token=iter(f"t{i}" for i in range(99)).__next__
    )


def _run(coro):
    return asyncio.run(coro)


def _ask_and_confirm(service, setting, value):
    question = _run(service.request(setting, value))
    assert isinstance(question, Question), question
    return _run(service.confirm(question.token))


# --- The question --------------------------------------------------------------


def test_a_request_says_how_things_are_and_then_asks():
    question = _run(_service().request(Setting.TEMPERATURE, "23"))
    assert isinstance(question, Question)
    assert question.say == "The heat pump is set to 21 degrees. Do you want me to set it to 23 degrees?"


@pytest.mark.parametrize(
    ("setting", "value", "say"),
    [
        (Setting.POWER, "off", "The heat pump is on. Do you want me to switch it off?"),
        (Setting.MODE, "cool", "The heat pump is heating. Do you want me to switch it to cooling?"),
        (Setting.FAN, "low", "The fan is on auto. Do you want me to set it to low?"),
    ],
)
def test_every_kind_of_change_is_asked_as_a_question(setting, value, say):
    question = _run(_service().request(setting, value))
    assert isinstance(question, Question)
    assert question.say == say


def test_nothing_changes_on_a_request_alone():
    unit = SimulatedUnit(start=START)
    _run(_service(unit).request(Setting.TEMPERATURE, "23"))
    assert unit.applied == []


# --- What is refused -----------------------------------------------------------


@pytest.mark.parametrize("value", ["15.5", "28.5", "40", "-3"])
def test_a_temperature_outside_16_to_28_degrees_is_refused(value):
    unit = SimulatedUnit(start=START)
    answer = _run(_service(unit).request(Setting.TEMPERATURE, value))
    assert isinstance(answer, Refused)
    assert "between 16 and 28 degrees" in answer.say


@pytest.mark.parametrize("value", ["16", "28", "22.5"])
def test_a_temperature_from_16_to_28_degrees_is_asked(value):
    assert isinstance(_run(_service().request(Setting.TEMPERATURE, value)), Question)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        (Setting.TEMPERATURE, "warm"),
        (Setting.TEMPERATURE, "nan"),
        (Setting.MODE, "turbo"),
        (Setting.POWER, "maybe"),
    ],
)
def test_a_value_the_setting_does_not_have_is_refused(setting, value):
    answer = _run(_service().request(setting, value))
    assert isinstance(answer, Refused)
    assert "can be" in answer.say


def test_a_change_the_unit_already_shows_is_refused():
    answer = _run(_service().request(Setting.TEMPERATURE, "21"))
    assert isinstance(answer, Refused)
    assert answer.say == "The heat pump is already set to 21 degrees."


def test_a_request_is_refused_when_the_unit_cannot_be_read():
    class Silent(SimulatedUnit):
        async def read(self):
            raise UnitUnavailableError

    answer = _run(_service(Silent()).request(Setting.POWER, "off"))
    assert isinstance(answer, Refused)
    assert "could not reach" in answer.say


# --- The yes -------------------------------------------------------------------


def test_a_confirmed_change_is_read_back_from_the_unit():
    unit = SimulatedUnit(start=START)
    done = _ask_and_confirm(_service(unit), Setting.TEMPERATURE, "23")
    assert isinstance(done, Done)
    assert done.confirmed
    assert unit.reading.set_temp == 23.0
    assert done.say == "Done. The heat pump is now set to 23 degrees."


def test_a_change_the_unit_does_not_show_is_said_to_be_not_confirmed():
    done = _ask_and_confirm(_service(IgnoringUnit(start=START)), Setting.TEMPERATURE, "23")
    assert isinstance(done, Done)
    assert not done.confirmed
    assert "not confirmed" in done.say
    assert "21 degrees" in done.say


def test_a_token_works_once():
    service = _service()
    question = _run(service.request(Setting.POWER, "off"))
    _run(service.confirm(question.token))
    again = _run(service.confirm(question.token))
    assert isinstance(again, Refused)


def test_a_wrong_token_cancels_the_question():
    unit = SimulatedUnit(start=START)
    service = _service(unit)
    question = _run(service.request(Setting.POWER, "off"))
    assert isinstance(_run(service.confirm("not-it")), Refused)
    assert isinstance(_run(service.confirm(question.token)), Refused)
    assert unit.applied == []


def test_a_token_expires_after_thirty_seconds():
    clock = Clock()
    unit = SimulatedUnit(start=START)
    service = _service(unit, clock)
    question = _run(service.request(Setting.POWER, "off"))
    clock.now += TOKEN_SECONDS
    answer = _run(service.confirm(question.token))
    assert isinstance(answer, Refused)
    assert "expired" in answer.say
    assert unit.applied == []


def test_a_new_request_replaces_the_open_question():
    service = _service()
    first = _run(service.request(Setting.POWER, "off"))
    _run(service.request(Setting.FAN, "low"))
    assert isinstance(_run(service.confirm(first.token)), Refused)


def test_a_change_that_never_reached_the_unit_can_be_confirmed_again():
    unit = FailingUnit(start=START)
    service = _service(unit)
    question = _run(service.request(Setting.TEMPERATURE, "23"))
    failed = _run(service.confirm(question.token))
    assert isinstance(failed, Refused)
    assert "try again" in failed.say
    done = _run(service.confirm(question.token))
    assert isinstance(done, Done)
    assert done.confirmed


# --- At most one change per ten seconds ---------------------------------------


def test_only_one_change_is_sent_per_ten_seconds():
    clock = Clock()
    unit = SimulatedUnit(start=START)
    service = _service(unit, clock)
    _ask_and_confirm(service, Setting.TEMPERATURE, "23")
    sent_at = clock.now
    too_soon = _run(service.request(Setting.FAN, "low"))
    if isinstance(too_soon, Question):  # asked in time, confirmed too soon
        too_soon = _run(service.confirm(too_soon.token))
    assert isinstance(too_soon, Refused)
    assert "seconds" in too_soon.say
    assert len(unit.applied) == 1
    clock.now = sent_at + CHANGE_GAP_SECONDS
    assert isinstance(_ask_and_confirm(service, Setting.FAN, "low"), Done)


# --- The status after a change ------------------------------------------------


def test_the_status_after_a_change_is_read_fresh():
    unit = SimulatedUnit(start=START)
    service = _service(unit)
    _run(service.status())
    _ask_and_confirm(service, Setting.MODE, "cool")
    status = _run(service.status())
    assert status.reading == dataclasses.replace(START, mode=Mode.COOL)

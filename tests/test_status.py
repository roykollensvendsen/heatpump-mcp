# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading the heat pump: what it says, and how long a reading is kept."""

import asyncio

from heatpump_mcp.service import STATUS_CACHE_SECONDS, HeatpumpService
from heatpump_mcp.simulator import SimulatedUnit
from heatpump_mcp.unit import Fan, Mode, Reading, UnitUnavailableError


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CountingUnit(SimulatedUnit):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reads = 0

    async def read(self):
        self.reads += 1
        return await super().read()


class SilentUnit(SimulatedUnit):
    async def read(self):
        raise UnitUnavailableError


def _status(service):
    return asyncio.run(service.status())


def test_the_status_says_power_mode_temperatures_and_fan():
    unit = SimulatedUnit(
        start=Reading(on=True, mode=Mode.HEAT, set_temp=21.0, room_temp=20.5, outdoor_temp=3.0, fan=Fan.AUTO)
    )
    answer = _status(HeatpumpService(unit))
    assert answer.reading is not None
    assert answer.say == (
        "The heat pump is on, heating to 21 degrees. The room is 20.5 degrees, and it is 3 degrees "
        "outside. The fan is on auto."
    )


def test_a_switched_off_unit_says_so_before_anything_else():
    unit = SimulatedUnit(
        start=Reading(on=False, mode=Mode.COOL, set_temp=24.0, room_temp=22.0, outdoor_temp=None, fan=Fan.LOW)
    )
    answer = _status(HeatpumpService(unit))
    assert answer.say.startswith("The heat pump is off.")
    assert "outside" not in answer.say


def test_a_status_is_kept_for_thirty_seconds():
    clock, unit = Clock(), CountingUnit()
    service = HeatpumpService(unit, clock=clock)
    _status(service)
    clock.now += STATUS_CACHE_SECONDS - 1
    _status(service)
    assert unit.reads == 1
    clock.now += 1
    _status(service)
    assert unit.reads == 2


def test_a_unit_that_does_not_answer_is_said_to_be_unknown():
    answer = _status(HeatpumpService(SilentUnit()))
    assert answer.reading is None
    assert answer.say.startswith("I could not reach the heat pump")
    assert "Ask again in a minute" in answer.say


def test_a_reason_the_unit_gives_is_read_aloud_instead():
    class Refusing(SimulatedUnit):
        async def read(self):
            msg = "The FGLair cloud refused the login; the owner needs to enter it again."
            raise UnitUnavailableError(msg)

    answer = _status(HeatpumpService(Refusing()))
    assert answer.say == "The FGLair cloud refused the login; the owner needs to enter it again."

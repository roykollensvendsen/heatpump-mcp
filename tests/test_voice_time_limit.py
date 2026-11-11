# SPDX-License-Identifier: GPL-3.0-or-later
"""Each tool answers within the time the voice gives it (the plugin standard's rule 3).

The Claude app gives up on a tool after about 15 seconds, and the voice then
hears only that the call failed, with nothing to tell the owner. The cloud can
be slow, so every wait on it has a limit, and each tool's longest path, added
up from those limits, leaves room inside the 15 seconds.
"""

import asyncio

import pytest

from heatpump_mcp import service as service_module
from heatpump_mcp.service import (
    POLL_SECONDS,
    READ_BACK_SECONDS,
    READ_SECONDS,
    SEND_SECONDS,
    Done,
    HeatpumpService,
    Question,
)
from heatpump_mcp.simulator import SimulatedUnit
from heatpump_mcp.unit import Setting

#: How long the Claude app waits for a tool, in seconds.
VOICE_LIMIT = 15.0
#: Room kept for everything not counted: the bridge, the MCP layer, the network.
MARGIN = 2.0

LONGEST = {
    "status": READ_SECONDS,
    "request": READ_SECONDS,
    "confirm: send, then read back until the window closes": SEND_SECONDS + READ_BACK_SECONDS,
}


@pytest.mark.parametrize("seconds", LONGEST.values(), ids=LONGEST.keys())
def test_every_tool_answers_within_the_voices_time(seconds):
    assert seconds + MARGIN <= VOICE_LIMIT


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class SlowReads(SimulatedUnit):
    """Every read takes the longest a read may take."""

    def __init__(self, clock, **kwargs):
        super().__init__(**kwargs)
        self.clock = clock

    async def read(self):
        self.clock.now += READ_SECONDS
        return await super().read()

    async def apply(self, change):
        self.applied.append(change)  # never shows it


def test_reading_back_never_starts_a_read_that_would_end_after_the_window():
    clock = Clock()
    unit = SlowReads(clock)
    service = HeatpumpService(unit, clock=clock, sleep=clock.sleep)
    question = asyncio.run(service.request(Setting.FAN, "low"))
    assert isinstance(question, Question)
    started = clock.now
    done = asyncio.run(service.confirm(question.token))
    assert isinstance(done, Done)
    assert clock.now - started <= READ_BACK_SECONDS
    assert clock.now - started >= POLL_SECONDS + READ_SECONDS  # it did read back once


class Hanging(SimulatedUnit):
    def __init__(self, *, hang_read=False, hang_apply=False):
        super().__init__()
        self.hang_read, self.hang_apply = hang_read, hang_apply

    async def read(self):
        if self.hang_read:
            await asyncio.sleep(60)
        return await super().read()

    async def apply(self, change):
        self.applied.append(change)
        if self.hang_apply:
            await asyncio.sleep(60)


@pytest.fixture
def short_limits(monkeypatch):
    monkeypatch.setattr(service_module, "READ_SECONDS", 0.05)
    monkeypatch.setattr(service_module, "SEND_SECONDS", 0.05)
    monkeypatch.setattr(service_module, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(service_module, "READ_BACK_SECONDS", 0.2)


@pytest.mark.usefixtures("short_limits")
def test_a_read_that_hangs_is_given_up_and_said_to_be_unreachable():
    status = asyncio.run(HeatpumpService(Hanging(hang_read=True)).status())
    assert status.reading is None
    assert "could not reach" in status.say


@pytest.mark.usefixtures("short_limits")
def test_a_change_that_may_have_reached_the_unit_is_read_back_and_never_sent_again():
    unit = Hanging(hang_apply=True)
    service = HeatpumpService(unit)

    async def ask_and_confirm():
        question = await service.request(Setting.FAN, "low")
        first = await service.confirm(question.token)
        second = await service.confirm(question.token)
        return first, second

    first, second = asyncio.run(ask_and_confirm())
    assert isinstance(first, Done)
    assert not first.confirmed
    assert "no open question" in second.say
    assert len(unit.applied) == 1

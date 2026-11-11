# SPDX-License-Identifier: GPL-3.0-or-later
"""A pretend heat pump, for tests and for a first try by voice (the plugin standard's rule 11)."""

from __future__ import annotations

import dataclasses

from heatpump_mcp.unit import Change, Fan, Mode, Reading, Setting

#: Where the pretend unit starts unless told otherwise: a neutral winter's day.
DEFAULT_START = Reading(
    on=True, mode=Mode.HEAT, set_temp=21.0, room_temp=20.5, outdoor_temp=3.0, fan=Fan.AUTO
)


class SimulatedUnit:
    """A unit that lives in memory and touches no network."""

    def __init__(self, *, start: Reading = DEFAULT_START) -> None:
        """Start the pretend unit at `start`."""
        self.reading = start
        self.applied: list[Change] = []

    async def read(self) -> Reading:
        """The pretend unit's reading now."""
        return self.reading

    async def apply(self, change: Change) -> None:
        """Make the change at once, as a unit that always answers would."""
        self.applied.append(change)
        now, value = self.reading, change.value
        if change.setting is Setting.POWER and isinstance(value, bool):
            self.reading = dataclasses.replace(now, on=value)
        elif isinstance(value, Mode):  # choosing a mode switches a Fujitsu unit on
            self.reading = dataclasses.replace(now, on=True, mode=value)
        elif isinstance(value, Fan):
            self.reading = dataclasses.replace(now, fan=value)
        else:
            self.reading = dataclasses.replace(now, set_temp=float(value))

    async def close(self) -> None:
        """Nothing to close: the pretend unit has no connection."""

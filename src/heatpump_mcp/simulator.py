# SPDX-License-Identifier: GPL-3.0-or-later
"""A pretend heat pump, for tests and for a first try by voice (the plugin standard's rule 11)."""

from __future__ import annotations

from heatpump_mcp.unit import Fan, Mode, Reading

#: Where the pretend unit starts unless told otherwise: a neutral winter's day.
DEFAULT_START = Reading(
    on=True, mode=Mode.HEAT, set_temp=21.0, room_temp=20.5, outdoor_temp=3.0, fan=Fan.AUTO
)


class SimulatedUnit:
    """A unit that lives in memory and touches no network."""

    def __init__(self, *, start: Reading = DEFAULT_START) -> None:
        """Start the pretend unit at `start`."""
        self.reading = start

    async def read(self) -> Reading:
        """The pretend unit's reading now."""
        return self.reading

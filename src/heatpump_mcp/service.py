# SPDX-License-Identifier: GPL-3.0-or-later
"""The rules between a voice request and the heat pump."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from heatpump_mcp.unit import Mode, Reading, Unit, UnitUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

#: How long a reading is reused before the unit is read again; the cloud is slow and rate-limited.
STATUS_CACHE_SECONDS = 30.0

_DOING = {
    Mode.HEAT: "heating to {t}",
    Mode.COOL: "cooling to {t}",
    Mode.AUTO: "on auto at {t}",
    Mode.DRY: "drying the air at {t}",
    Mode.FAN: "only blowing air",
}


@dataclass(frozen=True)
class Status:
    """A reading and the sentence to read aloud; `reading` None when the unit did not answer."""

    reading: Reading | None
    say: str


def degrees(value: float) -> str:
    """A temperature as it is read aloud: 21 degrees, 20.5 degrees."""
    return f"{value:g} degrees"


def describe(reading: Reading) -> str:
    """The reading in a few short sentences, the most important first."""
    if not reading.on:
        first = "The heat pump is off."
    elif reading.mode is None:
        first = "The heat pump is on."
    else:
        target = degrees(reading.set_temp) if reading.set_temp is not None else "an unknown temperature"
        first = f"The heat pump is on, {_DOING[reading.mode].format(t=target)}."
    parts = [first]
    if reading.room_temp is not None and reading.outdoor_temp is not None:
        parts.append(
            f"The room is {degrees(reading.room_temp)}, and it is {degrees(reading.outdoor_temp)} outside."
        )
    elif reading.room_temp is not None:
        parts.append(f"The room is {degrees(reading.room_temp)}.")
    if reading.on and reading.fan is not None:
        parts.append(f"The fan is on {reading.fan}.")
    return " ".join(parts)


class HeatpumpService:
    """Reads the unit for the voice, keeping each reading for a while."""

    def __init__(self, unit: Unit, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Wire the service to `unit`; `clock` is injected so tests need not wait."""
        self._unit = unit
        self._clock = clock
        self._cached: tuple[float, Reading] | None = None

    async def status(self) -> Status:
        """The unit's state, from a reading at most STATUS_CACHE_SECONDS old."""
        now = self._clock()
        # RULE: a status is kept for thirty seconds
        if self._cached is not None and now - self._cached[0] < STATUS_CACHE_SECONDS:
            reading = self._cached[1]
        else:
            try:
                reading = await self._unit.read()
            except UnitUnavailableError:
                return Status(
                    None, "I could not reach the heat pump through the cloud. Please try again soon."
                )
            self._cached = (now, reading)
        return Status(reading, describe(reading))

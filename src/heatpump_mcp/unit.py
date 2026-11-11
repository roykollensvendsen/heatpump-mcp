# SPDX-License-Identifier: GPL-3.0-or-later
"""What a heat pump is, to the rest of this package, whichever way it is reached.

The core knows no cloud and no MCP. A unit is anything that can be read and
given one change at a time: the simulator, or Fujitsu's FGLair cloud.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Mode(StrEnum):
    """What the unit does while it is on."""

    AUTO = "auto"
    COOL = "cool"
    DRY = "dry"
    FAN = "fan"
    HEAT = "heat"


class Fan(StrEnum):
    """How hard the indoor fan blows."""

    QUIET = "quiet"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    AUTO = "auto"


@dataclass(frozen=True)
class Reading:
    """One reading of the unit; a temperature it does not report is None, in degrees Celsius."""

    on: bool
    mode: Mode | None
    set_temp: float | None
    room_temp: float | None
    outdoor_temp: float | None
    fan: Fan | None


class UnitUnavailableError(Exception):
    """The unit, or the cloud in front of it, did not answer."""


class Unit(Protocol):
    """A heat pump that can be read."""

    async def read(self) -> Reading:
        """The unit's settings and temperatures now; raises UnitUnavailableError."""
        ...

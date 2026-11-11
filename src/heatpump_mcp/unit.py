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


class Setting(StrEnum):
    """The only things this plugin ever changes on the unit, one at a time."""

    POWER = "power"
    MODE = "mode"
    TEMPERATURE = "temperature"
    FAN = "fan"


@dataclass(frozen=True)
class Change:
    """One change to send: `value` is a bool for power, a Mode, a temperature in Celsius, or a Fan."""

    setting: Setting
    value: bool | Mode | float | Fan

    def shown_by(self, reading: Reading) -> bool:
        """Whether `reading` shows this change made."""
        if self.setting is Setting.POWER:
            return reading.on is self.value
        if self.setting is Setting.MODE:
            return reading.on and reading.mode is self.value
        if self.setting is Setting.TEMPERATURE:
            return reading.set_temp == self.value
        return reading.fan is self.value


class UnitUnavailableError(Exception):
    """The unit, or the cloud in front of it, did not answer."""


class Unit(Protocol):
    """A heat pump that can be read and changed."""

    async def read(self) -> Reading:
        """The unit's settings and temperatures now; raises UnitUnavailableError."""
        ...

    async def apply(self, change: Change) -> None:
        """Send one change; raises UnitUnavailableError when it did not reach the unit."""
        ...

# SPDX-License-Identifier: GPL-3.0-or-later
"""The real heat pump, through Fujitsu's FGLair cloud (decisions/ADR-HM-001).

The calls follow Home Assistant's ``fujitsu_fglair`` integration, on the same
pinned version of ayla-iot-unofficial: sign in, keep the token fresh, find the
FujitsuHVAC unit, update it, and change it only through the setters for mode,
temperature and fan. Nothing here says what went wrong in the library's own
words, so neither the login nor the unit's identity can reach the voice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiohttp
from ayla_iot_unofficial import AylaAuthError, AylaError, new_ayla_api
from ayla_iot_unofficial.fujitsu_consts import FGLAIR_APP_CREDENTIALS, FanSpeed, OpMode
from ayla_iot_unofficial.fujitsu_hvac import DeviceOffline, FujitsuHVAC, SettingNotSupportedError

from heatpump_mcp.settings import ENTER_LOGIN
from heatpump_mcp.unit import (
    Change,
    ChangeNotSentError,
    ChangeNotSupportedError,
    Fan,
    Mode,
    Reading,
    Setting,
    UnitUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from heatpump_mcp.settings import Settings

#: The library's own limit on each call to the cloud, in seconds; the service
#: holds each whole reading and change to tighter limits of its own.
API_TIMEOUT = 10

#: The library's errors that mean the cloud or the network did not answer.
CLOUD_ERRORS = (AylaError, aiohttp.ClientError, KeyError, ValueError, TypeError)

_MODES = {
    OpMode.AUTO: Mode.AUTO,
    OpMode.COOL: Mode.COOL,
    OpMode.DRY: Mode.DRY,
    OpMode.FAN: Mode.FAN,
    OpMode.HEAT: Mode.HEAT,
}
_OP_MODES = {mode: op for op, mode in _MODES.items()}
_FANS = {FanSpeed[fan.name]: fan for fan in Fan}

LOGIN_REFUSED = f"The FGLair cloud refused the login. {ENTER_LOGIN}"


def _is_heat_pump(device: object) -> bool:
    return isinstance(device, FujitsuHVAC)


class FglairUnit:
    """One Fujitsu heat pump on an FGLair account."""

    def __init__(
        self,
        api: Any,  # noqa: ANN401 - the library's AylaApi has no type information
        *,
        unit_name: str | None = None,
        is_heat_pump: Callable[[object], bool] = _is_heat_pump,
    ) -> None:
        """Use `api`, signed in or not; `unit_name` picks the unit when the account has several."""
        self._api = api
        self._unit_name = unit_name
        self._is_heat_pump = is_heat_pump
        self._device: Any = None

    async def close(self) -> None:
        """Close the cloud connection, if one was opened; nothing is sent."""
        session = getattr(self._api, "websession", None)
        if session is not None:
            await session.close()

    async def _signed_in(self) -> None:
        try:
            if self._api.token_expired:
                await self._api.async_sign_in()
            elif self._api.token_expiring_soon:
                await self._api.async_refresh_auth()
        except AylaAuthError:
            raise UnitUnavailableError(LOGIN_REFUSED) from None

    async def _find(self) -> Any:  # noqa: ANN401 - a library FujitsuHVAC
        await self._signed_in()
        units = [d for d in await self._api.async_get_devices() if self._is_heat_pump(d)]
        if self._unit_name is not None:
            wanted = self._unit_name.casefold()
            units = [d for d in units if str(d.device_name).casefold() == wanted]
        if not units:
            msg = (
                "Fujitsu's cloud lists no heat pump with the name in config.toml. "
                'Write it as the FGLair app shows it, such as: unit = "Living room"'
                if self._unit_name
                else "Fujitsu's cloud lists no heat pump for this login. "
                "Check that the FGLair app shows the heat pump when signed in with the same login."
            )
            raise UnitUnavailableError(msg)
        if len(units) > 1:
            msg = (
                "Fujitsu's cloud lists more than one heat pump. Write the one to use in config.toml, "
                'as the FGLair app names it, such as: unit = "Living room"'
            )
            raise UnitUnavailableError(msg)
        if not units[0].is_online():
            msg = (
                "The heat pump is offline in the FGLair cloud. "
                "Check that it has power and that the FGLair app shows it online."
            )
            raise UnitUnavailableError(msg)
        return units[0]

    async def read(self) -> Reading:
        """The unit as the cloud reports it now."""
        try:
            device = await self._find()
            await device.async_update()
            op = OpMode(device.op_mode)
            reading = Reading(
                on=op is not OpMode.OFF,
                mode=_MODES.get(op),
                set_temp=float(device.set_temp),
                room_temp=device.sensed_temp,
                outdoor_temp=device.outdoor_temperature,
                fan=_FANS.get(FanSpeed(device.fan_speed)),
            )
        except (*CLOUD_ERRORS, DeviceOffline):
            raise UnitUnavailableError from None
        self._device = device
        return reading

    async def apply(self, change: Change) -> None:
        """Send `change` through the one setter Home Assistant uses for it."""
        try:
            await self._signed_in()
            device = self._device if self._device is not None else await self._find()
        except UnitUnavailableError as error:
            raise ChangeNotSentError(str(error)) from None
        except CLOUD_ERRORS:
            raise ChangeNotSentError from None
        try:
            await _send(device, change)
        except (SettingNotSupportedError, KeyError):
            raise ChangeNotSupportedError from None
        except DeviceOffline:
            raise ChangeNotSentError from None
        except CLOUD_ERRORS:
            raise UnitUnavailableError from None  # it may have arrived


async def _send(device: Any, change: Change) -> None:  # noqa: ANN401 - a library FujitsuHVAC
    value = change.value
    if change.setting is Setting.POWER:
        await device.async_set_op_mode(OpMode.ON if value else OpMode.OFF)
    elif change.setting is Setting.MODE and isinstance(value, Mode):
        await device.async_set_op_mode(_OP_MODES[value])
    elif change.setting is Setting.TEMPERATURE and isinstance(value, float):
        await device.async_set_set_temp(value)
    elif change.setting is Setting.FAN and isinstance(value, Fan):
        await device.async_set_fan_speed(FanSpeed[value.name])
    else:
        raise KeyError(change.setting)


def connect(settings: Settings) -> FglairUnit:
    """The unit on the account in `settings`; nothing is sent until it is first read."""
    app_id, app_secret = FGLAIR_APP_CREDENTIALS[settings.region]
    api = new_ayla_api(
        settings.username,
        settings.password,
        app_id,
        app_secret,
        europe=settings.region == "eu",
        timeout=API_TIMEOUT,
    )
    return FglairUnit(api, unit_name=settings.unit_name)


class MissingLogin:
    """Stands in for the unit until the login is set up, saying what to do."""

    def __init__(self, reason: str) -> None:
        """`reason` names the file to fix, never its contents."""
        self._reason = reason

    async def read(self) -> Reading:
        """Refuse, with the reason."""
        raise UnitUnavailableError(self._reason)

    async def apply(self, change: Change) -> None:  # noqa: ARG002 - the Unit interface
        """Refuse, with the reason; nothing is sent."""
        raise ChangeNotSentError(self._reason)

    async def close(self) -> None:
        """Nothing to close: nothing was ever opened."""

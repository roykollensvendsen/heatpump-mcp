# SPDX-License-Identifier: GPL-3.0-or-later
"""The real heat pump, through Fujitsu's FGLair cloud (decisions/ADR-HM-001).

The calls follow Home Assistant's ``fujitsu_fglair`` integration, on the same
pinned version of ayla-iot-unofficial: sign in, keep the token fresh, find the
FujitsuHVAC unit, update it, and change it only through the setters for mode,
temperature and fan. Nothing here says what went wrong in the library's own
words, so neither the login nor the unit's identity can reach the voice.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from ayla_iot_unofficial import AylaAuthError, AylaError, new_ayla_api
from ayla_iot_unofficial.fujitsu_consts import FGLAIR_APP_CREDENTIALS, FanSpeed, OpMode
from ayla_iot_unofficial.fujitsu_hvac import DeviceOffline, FujitsuHVAC, SettingNotSupportedError

from heatpump_mcp.settings import ENTER_LOGIN, SettingsError, load_settings
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
    import pathlib
    from collections.abc import Callable

    from heatpump_mcp.settings import Settings

#: The library's own limit on each call to the cloud, in seconds; the service
#: holds each whole reading and change to tighter limits of its own.
API_TIMEOUT = 10

#: The library sends nothing on a token with fewer seconds than this left: every
#: request's headers go through its check_auth, which refuses a token expiring soon.
LIBRARY_REFUSES_WITHIN = 600
#: How much earlier than that a reading renews the token, so one read at a question
#: is still outside the library's window at the yes: it covers a reading kept in the
#: status cache and the question's own life, thirty seconds each, with room to spare.
RENEW_AHEAD_SECONDS = 120


def _renew_due(api: Any) -> bool:  # noqa: ANN401 - the library's AylaApi
    """Whether a reading should renew the token now, ahead of the library's window."""
    expires = api.auth_expiration
    if expires is None:
        return True
    # The library compares with its own naive datetime.now(); the expiry's class gives it.
    left: float = (expires - type(expires).now()).total_seconds()
    # RULE: a question renews a login that would run out before its yes
    return left < LIBRARY_REFUSES_WITHIN + RENEW_AHEAD_SECONDS


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

#: Seconds to wait before a refused login is tried again; each refusal in a row
#: doubles it, up to the longest, so a wrong password cannot get the account locked.
LOGIN_BACKOFF_SECONDS = 300
LOGIN_BACKOFF_LONGEST = 3600


def _login_refused(wait: float) -> str:
    minutes = math.ceil(wait / 60)
    return (
        f"The FGLair cloud refused the login. I will wait {minutes} "
        f"minute{'' if minutes == 1 else 's'} before trying again, "
        f"so the FGLair login is not locked. {ENTER_LOGIN}"
    )


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
        config_file: str = "config.toml",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Use `api`, signed in or not; `unit_name` picks the unit when the account has several."""
        self._api = api
        self._unit_name = unit_name
        self._is_heat_pump = is_heat_pump
        self._config_file = config_file
        self._device: Any = None
        self._clock = clock
        self._refusals = 0
        self._retry_at: float | None = None

    async def close(self) -> None:
        """Close the cloud connection, if one was opened; nothing is sent."""
        session = getattr(self._api, "websession", None)
        if session is not None:
            await session.close()

    async def _signed_in(self) -> None:
        now = self._clock()
        # RULE: a refused login is not tried again until its wait is over
        if self._retry_at is not None and now < self._retry_at:
            raise UnitUnavailableError(_login_refused(self._retry_at - now))
        try:
            if self._api.token_expired:
                await self._api.async_sign_in()
            elif _renew_due(self._api):
                await self._api.async_refresh_auth()
        except AylaAuthError:
            wait = min(LOGIN_BACKOFF_SECONDS * 2**self._refusals, LOGIN_BACKOFF_LONGEST)
            self._refusals += 1
            self._retry_at = now + wait
            raise UnitUnavailableError(_login_refused(wait)) from None
        self._refusals, self._retry_at = 0, None

    async def _find(self) -> Any:  # noqa: ANN401 - a library FujitsuHVAC
        await self._signed_in()
        units = [d for d in await self._api.async_get_devices() if self._is_heat_pump(d)]
        if self._unit_name is not None:
            wanted = self._unit_name.casefold()
            units = [d for d in units if str(d.device_name).casefold() == wanted]
        if not units:
            msg = (
                f"Fujitsu's cloud lists no heat pump with the name in {self._config_file}. "
                'Write it there as the FGLair app shows it, such as: unit = "Living room"'
                if self._unit_name
                else "Fujitsu's cloud lists no heat pump for this login. "
                "Check that the FGLair app shows the heat pump when signed in with the same login."
            )
            raise UnitUnavailableError(msg)
        if len(units) > 1:
            msg = (
                "Fujitsu's cloud lists more than one heat pump. "
                f"Write the one to use in {self._config_file}, "
                'as the FGLair app names it, such as: unit = "Living room"'
            )
            raise UnitUnavailableError(msg)
        if not units[0].is_online():
            msg = (
                "The heat pump is offline in the FGLair cloud. "
                "Check that it has power and a network connection, and that the FGLair app shows it online."
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

    async def _valid(self) -> None:
        """Renew the token only if the library would refuse it; a question renewed it ahead."""
        # RULE: a change is sent without renewing a login the library still accepts
        if not (self._api.token_expired or self._api.token_expiring_soon):
            return
        await self._signed_in()

    async def ready(self) -> None:
        """Sign in and find the unit, so the send that follows is only the send."""
        try:
            await self._valid()
            if self._device is None:
                self._device = await self._find()
        except UnitUnavailableError as error:
            raise ChangeNotSentError(str(error)) from None
        except CLOUD_ERRORS:
            raise ChangeNotSentError from None

    async def apply(self, change: Change) -> None:
        """Send `change` through the one setter Home Assistant uses for it."""
        try:
            await self._valid()
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
    return FglairUnit(api, unit_name=settings.unit_name, config_file=settings.config_file)


class LoginFolder:
    """The unit, from the login files as they are at each call, so a fixed login needs no restart."""

    def __init__(
        self,
        folder: pathlib.Path,
        *,
        build: Callable[[Settings], FglairUnit] = connect,
    ) -> None:
        """Read the login from `folder` on every call; `build` turns a login into a unit."""
        self._folder = folder
        self._build = build
        self._settings: Settings | None = None
        self._unit: FglairUnit | None = None

    async def _current(self, failure: type[UnitUnavailableError]) -> FglairUnit:
        try:
            settings = load_settings(self._folder)
        except SettingsError as error:
            # The reason names the file to fix, never its contents.
            msg = f"The heat pump's login is not set up: {error}"
            raise failure(msg) from None
        # RULE: a login entered again is used without a restart
        if self._unit is None or settings != self._settings:
            await self.close()
            self._settings, self._unit = settings, self._build(settings)
        return self._unit

    async def read(self) -> Reading:
        """Read the unit with the login as it is now."""
        return await (await self._current(UnitUnavailableError)).read()

    async def ready(self) -> None:
        """Sign in with the login as it is now; nothing is sent."""
        await (await self._current(ChangeNotSentError)).ready()

    async def apply(self, change: Change) -> None:
        """Send `change` with the login as it is now; nothing is sent without one."""
        await (await self._current(ChangeNotSentError)).apply(change)

    async def close(self) -> None:
        """Close the cloud connection, if one was opened."""
        if self._unit is not None:
            await self._unit.close()

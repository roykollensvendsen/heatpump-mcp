# SPDX-License-Identifier: GPL-3.0-or-later
"""The FGLair driver, against a fake of the library's objects; no network is touched."""

import asyncio
from datetime import datetime, timedelta

import aiohttp
import pytest
from ayla_iot_unofficial import AylaAuthError
from ayla_iot_unofficial.fujitsu_consts import FanSpeed, OpMode
from ayla_iot_unofficial.fujitsu_hvac import DeviceOffline, SettingNotSupportedError

from heatpump_mcp.fglair import FglairUnit
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


class FakeDevice:
    def __init__(self, name="Living room", *, online=True):
        self.device_name = name
        self.online = online
        self.op_mode = OpMode.HEAT
        self.set_temp = 21.0
        self.sensed_temp = 20.5
        self.outdoor_temperature = 3.0
        self.fan_speed = FanSpeed.AUTO
        self.updates = 0
        self.calls = []
        self.fail_send = None

    def is_online(self):
        return self.online

    async def async_update(self):
        self.updates += 1

    async def _sent(self, name, value):
        if self.fail_send:
            raise self.fail_send
        self.calls.append((name, value))

    async def async_set_op_mode(self, value):
        await self._sent("op_mode", value)

    async def async_set_set_temp(self, value):
        await self._sent("set_temp", value)

    async def async_set_fan_speed(self, value):
        await self._sent("fan_speed", value)


class FakeApi:
    def __init__(self, *devices, refuse_login=False):
        self.devices = list(devices) or [FakeDevice()]
        self.refuse_login = refuse_login
        self.token_expired = True
        self.token_expiring_soon = True
        self.sign_ins = 0
        self.refreshes = 0

    @property
    def auth_expiration(self):
        """When the token runs out, as the library keeps it, from the two flags tests set."""
        if self.token_expired:
            return None
        return datetime.now() + timedelta(seconds=300 if self.token_expiring_soon else 86400)  # noqa: DTZ005 - naive, as the library

    async def async_sign_in(self):
        if self.refuse_login:
            raise AylaAuthError
        self.sign_ins += 1
        self.token_expired = self.token_expiring_soon = False

    async def async_refresh_auth(self):
        self.refreshes += 1
        self.token_expiring_soon = False

    async def async_get_devices(self):
        return self.devices


def _unit(api, name=None):
    return FglairUnit(api, unit_name=name, is_heat_pump=lambda d: isinstance(d, FakeDevice))


def _read(unit):
    return asyncio.run(unit.read())


def test_a_reading_carries_what_the_cloud_reports():
    assert _read(_unit(FakeApi())) == Reading(
        on=True, mode=Mode.HEAT, set_temp=21.0, room_temp=20.5, outdoor_temp=3.0, fan=Fan.AUTO
    )


def test_a_unit_switched_off_reads_as_off_with_no_mode():
    device = FakeDevice()
    device.op_mode = OpMode.OFF
    reading = _read(_unit(FakeApi(device)))
    assert not reading.on
    assert reading.mode is None


def test_the_login_is_signed_in_once_and_refreshed_when_it_expires_soon():
    api = FakeApi()
    unit = _unit(api)
    _read(unit)
    _read(unit)
    assert api.sign_ins == 1
    api.token_expiring_soon = True
    _read(unit)
    assert (api.sign_ins, api.refreshes) == (1, 1)


def test_a_refused_login_is_said_without_repeating_it():
    with pytest.raises(UnitUnavailableError) as raised:
        _read(_unit(FakeApi(refuse_login=True)))
    assert "login" in str(raised.value)


def test_an_offline_unit_cannot_be_read():
    with pytest.raises(UnitUnavailableError, match="offline"):
        _read(_unit(FakeApi(FakeDevice(online=False))))


def test_with_several_units_the_one_named_in_the_settings_is_used():
    api = FakeApi(FakeDevice("Bedroom"), FakeDevice("Living room"))
    _read(_unit(api, "living room"))
    assert [d.updates for d in api.devices] == [0, 1]


def test_with_several_units_and_none_named_nothing_is_guessed():
    with pytest.raises(UnitUnavailableError, match="more than one"):
        _read(_unit(FakeApi(FakeDevice("Bedroom"), FakeDevice("Living room"))))


@pytest.mark.parametrize(
    ("change", "call"),
    [
        (Change(Setting.POWER, value=True), ("op_mode", OpMode.ON)),
        (Change(Setting.POWER, value=False), ("op_mode", OpMode.OFF)),
        (Change(Setting.MODE, Mode.COOL), ("op_mode", OpMode.COOL)),
        (Change(Setting.TEMPERATURE, 22.5), ("set_temp", 22.5)),
        (Change(Setting.FAN, Fan.QUIET), ("fan_speed", FanSpeed.QUIET)),
    ],
)
def test_each_change_uses_the_one_setter_home_assistant_uses(change, call):
    device = FakeDevice()
    asyncio.run(_unit(FakeApi(device)).apply(change))
    assert device.calls == [call]


def test_a_change_that_failed_before_it_was_sent_says_so():
    with pytest.raises(ChangeNotSentError):
        asyncio.run(_unit(FakeApi(refuse_login=True)).apply(Change(Setting.FAN, Fan.LOW)))


def test_a_change_the_unit_cannot_make_is_refused_as_not_supported():
    device = FakeDevice()
    device.fail_send = SettingNotSupportedError("no quiet fan")
    with pytest.raises(ChangeNotSupportedError):
        asyncio.run(_unit(FakeApi(device)).apply(Change(Setting.FAN, Fan.QUIET)))


def test_an_offline_unit_is_never_sent_a_change():
    device = FakeDevice()
    device.fail_send = DeviceOffline()
    with pytest.raises(ChangeNotSentError):
        asyncio.run(_unit(FakeApi(device)).apply(Change(Setting.FAN, Fan.LOW)))


def test_a_change_that_failed_while_being_sent_may_have_arrived():
    device = FakeDevice()
    device.fail_send = aiohttp.ClientError("connection reset")
    with pytest.raises(UnitUnavailableError) as raised:
        asyncio.run(_unit(FakeApi(device)).apply(Change(Setting.FAN, Fan.LOW)))
    assert not isinstance(raised.value, ChangeNotSentError)


def test_connecting_builds_the_cloud_client_without_sending_anything():
    from heatpump_mcp.fglair import connect  # noqa: PLC0415
    from heatpump_mcp.settings import Settings  # noqa: PLC0415

    unit = connect(Settings("someone@example.com", "made-up", "eu", "Living room"))
    assert isinstance(unit, FglairUnit)


def _write_login(folder, username="someone@example.com", password="made-up"):  # noqa: S107 - made up
    for name, value in (("fglair-username", username), ("fglair-password", password)):
        path = folder / f"{name}"
        path.write_text(value)
        path.chmod(0o600)


class Builds:
    """Stands in for connecting: each new login gets a fresh fake cloud."""

    def __init__(self):
        self.apis = []

    def __call__(self, settings):
        api = FakeApi()
        api.settings = settings
        api.websession = FakeSession()
        self.apis.append(api)
        return _unit(api)


def _from_folder(folder):
    from heatpump_mcp.fglair import LoginFolder  # noqa: PLC0415

    builds = Builds()
    return LoginFolder(folder, build=builds), builds


def test_until_the_login_is_set_up_nothing_is_sent_and_the_reason_is_given(tmp_path):
    unit, builds = _from_folder(tmp_path)
    with pytest.raises(UnitUnavailableError, match="not set up"):
        asyncio.run(unit.read())
    with pytest.raises(ChangeNotSentError, match="not set up"):
        asyncio.run(unit.apply(Change(Setting.FAN, Fan.LOW)))
    assert builds.apis == []


def test_a_login_written_after_the_start_is_used_without_a_restart(tmp_path):
    unit, builds = _from_folder(tmp_path)
    with pytest.raises(UnitUnavailableError):
        asyncio.run(unit.read())
    _write_login(tmp_path)
    assert asyncio.run(unit.read()).set_temp == 21.0
    assert len(builds.apis) == 1


def test_a_login_entered_again_is_used_without_a_restart(tmp_path):
    _write_login(tmp_path, password="the-old-one")  # noqa: S106 - made up
    unit, builds = _from_folder(tmp_path)

    async def read_twice():
        await unit.read()
        _write_login(tmp_path, password="the-new-one")  # noqa: S106 - made up
        await unit.read()

    asyncio.run(read_twice())
    old, new = builds.apis
    assert old.websession.closed
    assert new.settings.password == "the-new-one"  # noqa: S105 - made up


def test_an_unchanged_login_keeps_the_signed_in_cloud_client(tmp_path):
    _write_login(tmp_path)
    unit, builds = _from_folder(tmp_path)

    async def read_twice():
        await unit.read()
        await unit.read()

    asyncio.run(read_twice())
    assert len(builds.apis) == 1
    assert builds.apis[0].sign_ins == 1


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_closing_the_unit_closes_the_cloud_connection():
    api = FakeApi()
    api.websession = FakeSession()
    asyncio.run(_unit(api).close())
    assert api.websession.closed


def test_closing_a_unit_never_read_sends_nothing():
    api = FakeApi()
    api.websession = None
    asyncio.run(_unit(api).close())
    assert api.sign_ins == 0


def test_getting_ready_signs_in_and_finds_the_unit_without_sending_anything():
    api = FakeApi()
    unit = _unit(api)
    asyncio.run(unit.ready())
    assert api.sign_ins == 1
    assert api.devices[0].calls == []


def test_a_unit_that_cannot_get_ready_was_certainly_sent_nothing():
    with pytest.raises(ChangeNotSentError, match="refused the login"):
        asyncio.run(_unit(FakeApi(refuse_login=True)).ready())


def test_without_a_login_getting_ready_says_what_to_set_up(tmp_path):
    unit, builds = _from_folder(tmp_path)
    with pytest.raises(ChangeNotSentError, match="not set up"):
        asyncio.run(unit.ready())
    assert builds.apis == []

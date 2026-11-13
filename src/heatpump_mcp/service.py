# SPDX-License-Identifier: GPL-3.0-or-later
"""The rules between a voice request and the heat pump.

Reading is one call. A change takes two: ``request`` reads the unit and returns
a question to read aloud with a one-time token, and ``confirm`` sends the
change, and is called with that token only after the owner's spoken yes.
Nothing changes on a request alone. After a change the unit is read back, and
the answer says "not confirmed" unless the reading shows it.
"""

from __future__ import annotations

import asyncio
import math
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from heatpump_mcp import speech
from heatpump_mcp.unit import (
    Change,
    ChangeNotSentError,
    ChangeNotSupportedError,
    Fan,
    Mode,
    Reading,
    Setting,
    Unit,
    UnitUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

#: How long a reading is reused before the unit is read again; the cloud is slow.
STATUS_CACHE_SECONDS = 30.0
#: The temperatures the voice may set, in degrees Celsius, both ends included.
LOWEST, HIGHEST = 16.0, 28.0
#: The shortest time between two changes sent to the unit.
CHANGE_GAP_SECONDS = 10.0
#: How long a question stays answerable.
TOKEN_SECONDS = 30.0
#: The longest one reading of the unit may take, signing in to the cloud included.
READ_SECONDS = 6.0
#: The longest sending one change may take.
SEND_SECONDS = 3.0
#: The longest signing in and finding the unit may take, before the send starts.
#: A token that still works is not renewed for a change, so this is instant unless
#: the token has run out or the login was just entered again.
READY_SECONDS = 2.0
#: How long a confirm reads the unit back for, after sending. With READY_SECONDS and
#: SEND_SECONDS it stays inside the Claude app's 15 seconds for a tool (the plugin
#: standard, rule 3); it leaves room for one read, as the ten seconds before it did.
READ_BACK_SECONDS = 8.0
#: How long between two readings while waiting for the change to show.
POLL_SECONDS = 1.5

UNREACHABLE = (
    "I could not reach the heat pump through the cloud. "
    "Ask again in a minute, and if it keeps failing, check that the FGLair app can reach it."
)


@dataclass(frozen=True)
class Status:
    """A reading and the sentence to read aloud; `reading` None when the unit did not answer."""

    reading: Reading | None
    say: str


@dataclass(frozen=True)
class Question:
    """A change is possible; read `say` aloud and confirm with `token` only on a yes."""

    token: str
    change: Change
    say: str


@dataclass(frozen=True)
class Refused:
    """Nothing was changed, and why, in a sentence to read aloud."""

    say: str


@dataclass(frozen=True)
class Done:
    """The change was sent; `confirmed` is whether the unit, read back, shows it."""

    change: Change
    confirmed: bool
    reading: Reading | None
    say: str


@dataclass(frozen=True)
class _Pending:
    token: str
    change: Change
    asked_at: float


def parse(setting: Setting, value: str) -> Change | Refused:
    """The change `value` names for `setting`, or why it cannot be made."""
    return _PARSERS[setting](value.strip().casefold())


def _power(text: str) -> Change | Refused:
    if text not in {"on", "off"}:
        return Refused("The power can be on or off. Say which one.")
    return Change(Setting.POWER, text == "on")


def _mode(text: str) -> Change | Refused:
    if text not in set(Mode):
        return Refused("The mode can be heat, cool, dry, fan or auto. Say one of those.")
    return Change(Setting.MODE, Mode(text))


def _fan(text: str) -> Change | Refused:
    if text not in set(Fan):
        return Refused("The fan can be quiet, low, medium, high or auto. Say one of those.")
    return Change(Setting.FAN, Fan(text))


def _temperature(text: str) -> Change | Refused:
    try:
        # "21,5" is how a Norwegian says and writes 21.5.
        number = text.removesuffix("degrees").removesuffix("°c").strip().replace(",", ".")
        degrees = float(number)
    except ValueError:
        degrees = math.nan
    if math.isnan(degrees):
        return Refused("The temperature can be a number of degrees, from 16 to 28. Say a number.")
    # RULE: a temperature outside 16 to 28 degrees is refused
    if not LOWEST <= degrees <= HIGHEST:
        return Refused(
            f"I can only set the heat pump between 16 and 28 degrees, not {speech.degrees(degrees)}. "
            "Ask for a temperature in that range."
        )
    return Change(Setting.TEMPERATURE, round(degrees * 2) / 2)  # the unit takes half degrees


#: Each setting as named in "Ask for a different ...".
_WHAT = {
    Setting.POWER: "setting",
    Setting.MODE: "mode",
    Setting.TEMPERATURE: "temperature",
    Setting.FAN: "fan speed",
}


def _not_sent(reason: str) -> str:
    """Why a change never left: the driver's reason, which says what to do, or else a retry."""
    if reason:
        return f"Nothing changed: {reason}"
    return "I could not reach the heat pump, so nothing changed. Say yes to try again."


_PARSERS: dict[Setting, Callable[[str], Change | Refused]] = {
    Setting.POWER: _power,
    Setting.MODE: _mode,
    Setting.TEMPERATURE: _temperature,
    Setting.FAN: _fan,
}


class HeatpumpService:
    """The rules between the voice and one heat pump."""

    def __init__(
        self,
        unit: Unit,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        new_token: Callable[[], str] = lambda: secrets.token_urlsafe(16),
    ) -> None:
        """Wire the service to `unit`; the clock, sleep and tokens are injected so tests need not wait."""
        self._unit = unit
        self._clock = clock
        self._sleep = sleep
        self._new_token = new_token
        self._cached: tuple[float, Reading] | None = None
        self._pending: _Pending | None = None
        self._last_sent: float | None = None
        self._why = UNREACHABLE  # why the last reading failed, in words safe to read aloud

    async def status(self) -> Status:
        """The unit's state, from a reading at most STATUS_CACHE_SECONDS old."""
        reading = await self._reading()
        if reading is None:
            return Status(None, self._why)
        return Status(reading, speech.describe(reading))

    async def close(self) -> None:
        """Let go of the unit's connection, when the service is done."""
        await self._unit.close()

    async def _reading(self) -> Reading | None:
        now = self._clock()
        # RULE: a status is kept for thirty seconds
        if self._cached is not None and now - self._cached[0] < STATUS_CACHE_SECONDS:
            return self._cached[1]
        return await self._fresh()

    async def _fresh(self) -> Reading | None:
        try:
            async with asyncio.timeout(READ_SECONDS):
                reading = await self._unit.read()
        except UnitUnavailableError as error:
            # The unit's own reasons are written for the voice and hold no login or address.
            self._why = str(error) or UNREACHABLE
            return None
        except TimeoutError:
            self._why = UNREACHABLE
            return None
        self._cached = (self._clock(), reading)
        return reading

    async def request(self, setting: Setting, value: str) -> Question | Refused:
        """Check that the change is allowed, and return the question to ask, or why not."""
        self._pending = None  # a new request always replaces an older question
        change = parse(setting, value)
        if isinstance(change, Refused):
            return change
        reading = await self._reading()
        if reading is None:
            return Refused(self._why)
        if change.shown_by(reading):
            return Refused(speech.already(change, reading))
        token = self._new_token()
        self._pending = _Pending(token, change, self._clock())
        return Question(token, change, speech.asked(change, reading))

    async def confirm(self, token: str) -> Done | Refused:
        """Send the change once, if `token` is the open question's, then read the unit back."""
        pending, self._pending = self._pending, None
        # RULE: a wrong token cancels the question
        if pending is None or not secrets.compare_digest(pending.token, token):
            return Refused("There is no open question to confirm. Ask for the change again.")
        now = self._clock()
        # RULE: a token expires after thirty seconds
        if now - pending.asked_at >= TOKEN_SECONDS:
            return Refused("That question expired. Ask for the change again.")
        # RULE: only one change is sent per ten seconds
        if self._last_sent is not None and now - self._last_sent < CHANGE_GAP_SECONDS:
            self._pending = pending  # nothing was sent, so the yes still stands
            wait = math.ceil(CHANGE_GAP_SECONDS - (now - self._last_sent))
            return Refused(
                f"I change the heat pump only once every 10 seconds. Say yes again in {wait} seconds."
            )
        refused = await self._send(pending)
        if refused is not None:
            return refused
        self._last_sent = self._clock()
        return await self._read_back(pending.change)

    async def _send(self, pending: _Pending) -> Refused | None:
        """Sign in, then send the change once; a Refused when it certainly never left."""
        try:
            async with asyncio.timeout(READY_SECONDS):
                await self._unit.ready()
        # RULE: a sign-in that times out is never reported as sent
        except (UnitUnavailableError, TimeoutError) as error:
            self._pending = pending  # nothing was sent, so the yes still stands
            return Refused(_not_sent(str(error)))
        try:
            async with asyncio.timeout(SEND_SECONDS):
                await self._unit.apply(pending.change)
        except ChangeNotSupportedError:
            what = _WHAT[pending.change.setting]
            return Refused(f"This heat pump cannot do that, so nothing changed. Ask for a different {what}.")
        except ChangeNotSentError as error:
            # RULE: a change that never reached the unit can be confirmed again
            self._pending = pending
            return Refused(_not_sent(str(error)))
        except (UnitUnavailableError, TimeoutError):
            pass  # it may have arrived: read it back, and never send it again
        return None

    async def _read_back(self, change: Change) -> Done:
        """Read the unit until it shows `change`, for at most READ_BACK_SECONDS; never send again."""
        self._cached = None
        due = self._clock() + READ_BACK_SECONDS
        reading: Reading | None = None
        # RULE: reading back never starts a read that would end after the window
        while self._clock() + POLL_SECONDS + READ_SECONDS <= due:
            await self._sleep(POLL_SECONDS)
            reading = await self._fresh()
            # RULE: a change the unit does not show is said to be not confirmed
            if reading is not None and change.shown_by(reading):
                return Done(change, confirmed=True, reading=reading, say=speech.done(change, reading))
        return Done(change, confirmed=False, reading=reading, say=speech.not_confirmed(change, reading))

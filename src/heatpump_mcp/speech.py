# SPDX-License-Identifier: GPL-3.0-or-later
"""The sentences the voice reads aloud, kept apart from the rules that choose them."""

from __future__ import annotations

from heatpump_mcp.unit import Change, Mode, Reading, Setting

#: What the unit is doing in each mode, as it is said: "The heat pump is heating."
DOING = {
    Mode.HEAT: "heating",
    Mode.COOL: "cooling",
    Mode.AUTO: "on auto",
    Mode.DRY: "drying the air",
    Mode.FAN: "only blowing air",
}

#: Each mode as it is asked for: "Do you want me to switch it to cooling?"
TO = {
    Mode.HEAT: "heating",
    Mode.COOL: "cooling",
    Mode.AUTO: "auto",
    Mode.DRY: "drying",
    Mode.FAN: "fan only",
}


def degrees(value: float) -> str:
    """A temperature as it is read aloud: 21 degrees, 20.5 degrees."""
    return f"{value:g} degrees"


#: What the unit is doing, with the temperature it aims for, in a status.
_AIMING = {
    Mode.HEAT: "heating to {t}",
    Mode.COOL: "cooling to {t}",
    Mode.AUTO: "on auto at {t}",
    Mode.DRY: "drying the air at {t}",
    Mode.FAN: "only blowing air",
}


def describe(reading: Reading) -> str:
    """The reading in a few short sentences, the most important first."""
    if not reading.on:
        first = "The heat pump is off."
    elif reading.mode is None:
        first = "The heat pump is on."
    else:
        target = degrees(reading.set_temp) if reading.set_temp is not None else "an unknown temperature"
        first = f"The heat pump is on, {_AIMING[reading.mode].format(t=target)}."
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


def shown(setting: Setting, reading: Reading) -> tuple[str, str]:
    """What `reading` shows for `setting`, as a subject and what it is: ("The fan", "on auto")."""
    if setting is Setting.POWER:
        return "The heat pump", "on" if reading.on else "off"
    if setting is Setting.MODE:
        if not reading.on:
            return "The heat pump", "off"
        return "The heat pump", DOING[reading.mode] if reading.mode else "on, in a mode I cannot read"
    if setting is Setting.TEMPERATURE:
        if reading.set_temp is None:
            return "The heat pump", "set to a temperature I cannot read"
        return "The heat pump", f"set to {degrees(reading.set_temp)}"
    return "The fan", f"on {reading.fan}" if reading.fan else "at a speed I cannot read"


def asked(change: Change, reading: Reading) -> str:
    """The question for `change`, saying first how things are."""
    subject, now = shown(change.setting, reading)
    return f"{subject} is {now}. Do you want me to {_verb(change, reading)}?"


def _verb(change: Change, reading: Reading) -> str:
    value = change.value
    if change.setting is Setting.POWER:
        return "switch it on" if value else "switch it off"
    if change.setting is Setting.MODE:
        assert isinstance(value, Mode)  # noqa: S101 - a Change is built with the right type
        return f"switch it on, {TO[value]}" if not reading.on else f"switch it to {TO[value]}"
    if change.setting is Setting.TEMPERATURE:
        assert isinstance(value, float)  # noqa: S101
        return f"set it to {degrees(value)}"
    return f"set it to {value}"


def already(change: Change, reading: Reading) -> str:
    """Why a change the unit already shows is not asked."""
    subject, now = shown(change.setting, reading)
    return f"{subject} is already {now}."


def done(change: Change, reading: Reading) -> str:
    """The unit was read back and shows the change."""
    subject, now = shown(change.setting, reading)
    return f"Done. {subject} is now {now}."


def not_confirmed(change: Change, reading: Reading | None) -> str:
    """The change was sent, but the unit, read back, does not show it."""
    if reading is None:
        return (
            "I sent the change, but it is not confirmed: I could not read the heat pump back. "
            "Ask for the status in a minute."
        )
    subject, now = shown(change.setting, reading)
    return (
        f"I sent the change, but it is not confirmed: {subject.lower()} is still {now}. "
        "Ask for the status in a minute."
    )

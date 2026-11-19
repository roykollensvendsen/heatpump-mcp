# SPDX-License-Identifier: GPL-3.0-or-later
"""What the voice says when a reading lacks something: it says what it can't read, never a guess."""

from heatpump_mcp.speech import describe, not_confirmed, shown
from heatpump_mcp.unit import Change, Fan, Mode, Reading, Setting

ON_BUT_UNREADABLE = Reading(on=True, mode=None, set_temp=None, room_temp=None, outdoor_temp=None, fan=None)
OFF = Reading(on=False, mode=Mode.HEAT, set_temp=21.0, room_temp=20.5, outdoor_temp=None, fan=Fan.AUTO)


def test_a_unit_on_in_a_mode_that_cannot_be_read_is_only_said_to_be_on():
    assert describe(ON_BUT_UNREADABLE) == "The heat pump is on."


def test_a_room_temperature_without_an_outdoor_one_is_said_alone():
    assert describe(OFF) == "The heat pump is off. The room is 20.5 degrees."


def test_asked_about_its_mode_a_unit_that_is_off_is_said_to_be_off():
    assert shown(Setting.MODE, OFF) == ("The heat pump", "off")


def test_a_set_temperature_that_cannot_be_read_is_said_so():
    said = shown(Setting.TEMPERATURE, ON_BUT_UNREADABLE)
    assert said == ("The heat pump", "set to a temperature I cannot read")


def test_a_change_sent_but_not_read_back_says_to_ask_again():
    said = not_confirmed(Change(Setting.FAN, Fan.LOW), None)
    assert said.startswith("I sent the change, but it is not confirmed: I could not read the heat pump back.")

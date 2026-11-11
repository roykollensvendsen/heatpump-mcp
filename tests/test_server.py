# SPDX-License-Identifier: GPL-3.0-or-later
"""The tools the voice sees, called as the bridge calls them."""

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from heatpump_mcp.server import build_server
from heatpump_mcp.service import HeatpumpService
from heatpump_mcp.simulator import SimulatedUnit


def _call(server, name, **arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    assert not result.is_error
    return result.structured_content


def test_the_simulated_status_says_it_is_simulated():
    server = build_server(HeatpumpService(SimulatedUnit()), simulated=True)
    answer = _call(server, "heatpump_status")
    assert answer["say"].startswith("Simulated: The heat pump is")
    assert answer["simulated"] is True
    assert answer["success"] is True


def test_the_status_gives_each_field_a_program_can_read():
    server = build_server(HeatpumpService(SimulatedUnit()))
    answer = _call(server, "heatpump_status")
    assert set(answer["unit"]) == {"on", "mode", "set_temp", "room_temp", "outdoor_temp", "fan"}
    assert "simulated" not in answer


def test_the_server_offers_exactly_status_request_and_confirm():
    names = {t.name for t in asyncio.run(build_server(HeatpumpService(SimulatedUnit())).list_tools())}
    assert names == {"heatpump_status", "heatpump_set_request", "heatpump_set_confirm"}


def test_a_spoken_yes_changes_the_simulated_unit_and_says_so():
    unit = SimulatedUnit()
    server = build_server(HeatpumpService(unit, sleep=_no_wait), simulated=True)
    question = _call(server, "heatpump_set_request", setting="temperature", value="23")
    assert (
        question["say"]
        == "Simulated: The heat pump is set to 21 degrees. Do you want me to set it to 23 degrees?"
    )
    done = _call(server, "heatpump_set_confirm", token=question["token"])
    assert done["success"] is True
    assert done["say"] == "Simulated: Done. The heat pump is now set to 23 degrees."
    assert unit.reading.set_temp == 23.0


def test_a_refused_request_carries_no_token():
    server = build_server(HeatpumpService(SimulatedUnit()))
    answer = _call(server, "heatpump_set_request", setting="temperature", value="30")
    assert answer["success"] is False
    assert "token" not in answer


def test_a_setting_outside_the_four_is_refused_by_the_schema():
    server = build_server(HeatpumpService(SimulatedUnit()))
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("heatpump_set_request", {"setting": "timer", "value": "8"}))


async def _no_wait(_seconds):
    return None


def test_every_simulated_answer_says_simulated_first_refusals_included():
    unit = SimulatedUnit()
    server = build_server(HeatpumpService(unit, sleep=_no_wait), simulated=True)
    question = _call(server, "heatpump_set_request", setting="fan", value="low")
    answers = [
        _call(server, "heatpump_status"),
        question,
        _call(server, "heatpump_set_confirm", token=question["token"]),
        _call(server, "heatpump_set_request", setting="temperature", value="35"),
        _call(server, "heatpump_set_confirm", token="no-such-question"),  # noqa: S106 - not a secret
    ]
    for answer in answers:
        assert answer["say"].startswith("Simulated: "), answer
        assert answer["simulated"] is True


def _tools():
    return {t.name: t for t in asyncio.run(build_server(HeatpumpService(SimulatedUnit())).list_tools())}


def test_the_confirm_tool_opens_by_naming_what_it_sends():
    assert _tools()["heatpump_set_confirm"].description.splitlines()[0] == "Send the change on a yes."


def test_the_value_input_says_which_forms_it_takes():
    said = _tools()["heatpump_set_request"].input_schema["properties"]["value"]["description"]
    for form in ("on", "off", "heat", "21.5", "21,5", "quiet"):
        assert form in said

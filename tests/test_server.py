# SPDX-License-Identifier: GPL-3.0-or-later
"""The tools the voice sees, called as the bridge calls them."""

import asyncio

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

# SPDX-License-Identifier: GPL-3.0-or-later
"""The tools that only look are marked read-only, and only those (the plugin standard's rule 9)."""

import asyncio

from heatpump_mcp.server import build_server
from heatpump_mcp.service import HeatpumpService
from heatpump_mcp.simulator import SimulatedUnit

READ_ONLY = {"heatpump_status"}


def _tools():
    return {t.name: t for t in asyncio.run(build_server(HeatpumpService(SimulatedUnit())).list_tools())}


def test_the_tools_that_only_look_are_marked_read_only_and_no_others():
    marked = {name for name, tool in _tools().items() if tool.annotations and tool.annotations.read_only_hint}
    assert marked == READ_ONLY


def test_every_other_tool_is_marked_as_one_that_changes_something():
    for name, tool in _tools().items():
        if name not in READ_ONLY:
            assert tool.annotations is not None, name
            assert tool.annotations.read_only_hint is False, name

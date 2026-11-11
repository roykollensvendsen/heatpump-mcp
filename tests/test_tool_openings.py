# SPDX-License-Identifier: GPL-3.0-or-later
"""Each tool's first sentence is what the voice sees before it loads the tool.

The Claude app lists a tool it has not loaded yet as one line, cut short, so
the voice chooses by the first couple of dozen characters (the plugin
standard's rule 2). Every tool opens with a whole, short sentence on a line of
its own; no two open with the same words. The figure is the one tv-mcp
measured on 2026-11-10.
"""

import asyncio
import re

from heatpump_mcp.server import build_server
from heatpump_mcp.service import HeatpumpService
from heatpump_mcp.simulator import SimulatedUnit

OPENING_LIMIT = 26


def _tools():
    return asyncio.run(build_server(HeatpumpService(SimulatedUnit())).list_tools())


def _opening(tool):
    return (tool.description or "").split("\n", 1)[0]


def test_every_tool_opens_with_a_whole_sentence_the_voice_sees_in_full():
    for tool in _tools():
        opening = _opening(tool)
        assert len(opening) <= OPENING_LIMIT, f"{tool.name}: {opening!r} is {len(opening)} characters"
        assert re.fullmatch(r"[A-Z].*[.?]", opening), f"{tool.name}: {opening!r} is not one sentence"


def test_no_two_tools_open_with_the_same_words():
    openings = {}
    for tool in _tools():
        start = " ".join(_opening(tool).casefold().split()[:2])
        assert openings.setdefault(start, tool.name) == tool.name, f"{tool.name} and {openings[start]}"


def test_every_tool_is_named_after_the_plugin():
    for tool in _tools():
        assert tool.name.startswith("heatpump_"), tool.name

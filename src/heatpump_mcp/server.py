# SPDX-License-Identifier: GPL-3.0-or-later
"""The heat pump as MCP tools, for a voice session.

``heatpump_status`` reads the unit. Every answer has ``say``, to read aloud as
it is, and ``success``, which a program can read.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from heatpump_mcp.tools import tool

if TYPE_CHECKING:
    from heatpump_mcp.service import HeatpumpService

INSTRUCTIONS = (
    "Controls the owner's heat pump through the Fujitsu cloud. Every answer has 'say': read it aloud "
    "as it is. heatpump_status tells whether it is on, its mode, the temperature it is set to, the "
    "room and outdoor temperature, and the fan."
)

SIMULATED = "SIMULATED: this is a pretend heat pump, not a real one. Say so when you report on it. "


def build_server(service: HeatpumpService, *, simulated: bool = False) -> MCPServer[Any]:
    """The MCP server with the heat pump's tools."""
    server: MCPServer[Any] = MCPServer(
        name="heatpump-simulated" if simulated else "heatpump",
        instructions=(SIMULATED + INSTRUCTIONS) if simulated else INSTRUCTIONS,
    )

    @tool(
        server,
        simulated=simulated,
        annotations=ToolAnnotations(read_only_hint=True),
        description=(
            "Read the unit; no change.\n"
            "Say whether the heat pump is on, its mode (heat, cool, dry, fan, auto), the temperature it "
            "is set to, the room and outdoor temperature, and the fan speed. A reading is reused for "
            "30 seconds. Read 'say' aloud."
        ),
    )
    async def heatpump_status() -> dict[str, Any]:
        status = await service.status()
        if status.reading is None:
            return {"success": False, "say": status.say, "unit": None}
        return {"success": True, "say": status.say, "unit": asdict(status.reading)}

    return server

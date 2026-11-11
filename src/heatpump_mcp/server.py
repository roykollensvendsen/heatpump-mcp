# SPDX-License-Identifier: GPL-3.0-or-later
"""The heat pump as MCP tools, for a voice session.

``heatpump_status`` reads the unit. ``heatpump_set_request`` checks one change
and returns a question to read aloud with a token; ``heatpump_set_confirm``
sends it, and is called only after the owner's spoken yes. Every answer has
``say``, to read aloud as it is, and ``success``, which a program can read.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from heatpump_mcp.service import Done, Question
from heatpump_mcp.tools import tool
from heatpump_mcp.unit import Setting

if TYPE_CHECKING:
    from heatpump_mcp.service import HeatpumpService

INSTRUCTIONS = (
    "Controls the owner's heat pump through the Fujitsu cloud. Every answer has 'say': read it aloud "
    "as it is. heatpump_status tells whether it is on, its mode, the temperature it is set to, the "
    "room and outdoor temperature, and the fan. Never change anything without asking: for a change, call "
    "heatpump_set_request, read its 'say' question to the owner exactly, and call heatpump_set_confirm "
    "with its token only if the owner clearly answers yes. If they say anything else, do not confirm. "
    "One change per request; never ask again while a question is open. Temperatures from 16 to 28 "
    "degrees only, and at most one change every 10 seconds. Read every refusal and result aloud; "
    "'not confirmed' means the unit did not show the change when read back."
)

#: What `value` may be, for each setting, as the voice is shown it.
VALUE_FORMS = (
    "The new value. For power: on or off. For mode: heat, cool, dry, fan or auto. For temperature: "
    "degrees from 16 to 28, in half degrees, such as 21, 21.5 or 21,5. For fan: quiet, low, medium, "
    "high or auto."
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

    @tool(
        server,
        simulated=simulated,
        annotations=ToolAnnotations(read_only_hint=False),
        description=(
            "Ask before one change.\n"
            "Ask to change one setting: power (on, off), mode (heat, cool, dry, fan, auto), temperature "
            "(16 to 28 degrees) or fan (quiet, low, medium, high, auto). Changes nothing. Returns a "
            "question ('say') to read to the owner, with a token, or a refusal ('say') to read aloud."
        ),
    )
    async def heatpump_set_request(
        setting: Literal["power", "mode", "temperature", "fan"],
        value: Annotated[str, Field(description=VALUE_FORMS)],
    ) -> dict[str, Any]:
        answer = await service.request(Setting(setting), value)
        if isinstance(answer, Question):
            return {"success": True, "say": answer.say, "token": answer.token}
        return {"success": False, "say": answer.say}

    @tool(
        server,
        simulated=simulated,
        annotations=ToolAnnotations(read_only_hint=False),
        description=(
            "Send the change on a yes.\n"
            "Send the change asked about. Call only with the token from the most recent "
            "heatpump_set_request, and only after the owner has said yes to its question. A wrong or "
            "old token is refused and cancels the question. The unit is read back afterwards: "
            "success is true only when it shows the change. Read 'say' aloud."
        ),
    )
    async def heatpump_set_confirm(token: str) -> dict[str, Any]:
        answer = await service.confirm(token)
        if isinstance(answer, Done):
            unit = asdict(answer.reading) if answer.reading else None
            return {"success": answer.confirmed, "say": answer.say, "unit": unit}
        return {"success": False, "say": answer.say}

    return server

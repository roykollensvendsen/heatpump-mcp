# SPDX-License-Identifier: GPL-3.0-or-later
"""How each MCP tool is added: on the simulated unit, every answer says so first.

The plugin standard's rule 11: a change can be tried by voice without the
pretend heat pump ever being taken for the real one.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, ParamSpec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

Answer = dict[str, Any]
P = ParamSpec("P")


def tool(
    server: MCPServer[Any],
    *,
    simulated: bool,
    description: str,
    annotations: ToolAnnotations,
) -> Callable[[Callable[P, Awaitable[Answer]]], Callable[P, Awaitable[Answer]]]:
    """`server.tool`, and on the simulated unit every answer says so first."""
    register = server.tool(description=description, annotations=annotations)

    def add(fn: Callable[P, Awaitable[Answer]]) -> Callable[P, Awaitable[Answer]]:
        if not simulated:
            return register(fn)

        @functools.wraps(fn)
        async def marked(*args: P.args, **kwargs: P.kwargs) -> Answer:
            return simulated_answer(await fn(*args, **kwargs))

        return register(marked)

    return add


def simulated_answer(answer: Answer) -> Answer:
    """`answer` with "Simulated: " before what is said."""
    return {**answer, "say": f"Simulated: {answer['say']}", "simulated": True}

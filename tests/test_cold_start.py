# SPDX-License-Identifier: GPL-3.0-or-later
"""A freshly started server lists its tools in time, and never needs the cloud to do it.

The bridge starts each plugin as a process of its own and asks for its tools;
one that does not answer within 15 seconds is not offered, and the voice sees
nothing. So listing the tools must not wait on Fujitsu's cloud or the login:
a server with no login at all must still start and say what to set up. Both
runs start the real command, as the bridge does, from a fresh process.
"""

import asyncio
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

#: Seconds a cold start may take to list the tools, kept well inside the bridge's 15.
COLD_START_WITHIN = 10.0
TOOLS = {"heatpump_status", "heatpump_set_request", "heatpump_set_confirm"}


def _listed(args, env=None):
    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "heatpump_mcp.cli", *args], env=env
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}

    started = time.monotonic()
    names = asyncio.run(run())
    return names, time.monotonic() - started


def test_a_cold_start_lists_the_tools_in_time():
    names, seconds = _listed(["serve", "--simulate"])
    assert names == TOOLS
    assert seconds <= COLD_START_WITHIN


def test_a_cold_start_of_the_real_server_with_no_login_lists_the_tools_in_time(tmp_path):
    # An empty folder and an empty home: no login, and nothing the cloud could be asked with.
    env = {**os.environ, "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "config")}
    names, seconds = _listed(["serve", "--config-dir", str(tmp_path)], env=env)
    assert names == TOOLS
    assert seconds <= COLD_START_WITHIN

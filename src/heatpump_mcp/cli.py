# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for heatpump-mcp: serve the tools to a voice session, or read the unit once."""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

from heatpump_mcp.server import build_server
from heatpump_mcp.service import HeatpumpService, Status
from heatpump_mcp.settings import SettingsError, config_folder, load_settings
from heatpump_mcp.simulator import SimulatedUnit

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "status")


def _service(*, simulate: bool, folder: pathlib.Path) -> HeatpumpService:
    if simulate:
        return HeatpumpService(SimulatedUnit())
    # The library is imported only for the real unit, so --simulate never loads it.
    from heatpump_mcp.fglair import MissingLogin, connect  # noqa: PLC0415

    try:
        return HeatpumpService(connect(load_settings(folder)))
    except SettingsError as error:
        # The server still starts, so the voice can say what to set up.
        return HeatpumpService(MissingLogin(f"The heat pump's login is not set up: {error}"))


async def _status_once(service: HeatpumpService) -> Status:
    try:
        return await service.status()
    finally:
        await service.close()  # an open cloud connection would be reported on exit


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="heatpump-mcp", description=__doc__)
    parser.add_argument("command", choices=COMMANDS, help="serve: MCP over stdio; status: read once")
    parser.add_argument("--simulate", action="store_true", help="use a pretend unit; touches no network")
    parser.add_argument(
        "--config-dir", type=pathlib.Path, help="where the login files are; default ~/.config/heatpump-mcp"
    )
    args = parser.parse_args(argv)
    service = _service(simulate=args.simulate, folder=args.config_dir or config_folder(os.environ))
    if args.command == "status":
        status = asyncio.run(_status_once(service))
        prefix = "Simulated: " if args.simulate else ""
        sys.stdout.write(f"{prefix}{status.say}\n")
        return 0 if status.reading is not None else 1
    build_server(service, simulated=args.simulate).run()  # pragma: no cover - blocks on stdio
    return 0  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())

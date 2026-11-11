# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for heatpump-mcp: serve the tools to a voice session, or read the unit once."""

from __future__ import annotations

import argparse
import asyncio
import sys

from heatpump_mcp.server import build_server
from heatpump_mcp.service import HeatpumpService
from heatpump_mcp.simulator import SimulatedUnit

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "status")


def _service(*, simulate: bool) -> HeatpumpService:
    if simulate:
        return HeatpumpService(SimulatedUnit())
    sys.stderr.write("heatpump-mcp: only --simulate works so far\n")
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="heatpump-mcp", description=__doc__)
    parser.add_argument("command", choices=COMMANDS, help="serve: MCP over stdio; status: read once")
    parser.add_argument("--simulate", action="store_true", help="use a pretend unit; touches no network")
    args = parser.parse_args(argv)
    service = _service(simulate=args.simulate)
    if args.command == "status":
        status = asyncio.run(service.status())
        prefix = "Simulated: " if args.simulate else ""
        sys.stdout.write(f"{prefix}{status.say}\n")
        return 0 if status.reading is not None else 1
    build_server(service, simulated=args.simulate).run()  # pragma: no cover - blocks on stdio
    return 0  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())

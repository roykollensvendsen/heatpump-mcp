#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refuse a commit that would publish something about the owner's home or login.

This repository is public, so nothing in it may hold the owner's FGLair login,
the name of their unit, or how to reach their home network. Two checks on
every file given (by default: the files staged for commit):

- no home-network address: a private IPv4 address or a hardware (MAC)
  address, except the ranges set aside for documentation (RFC 5737 and
  RFC 7042), which are what examples use;
- no value from the owner's settings folder: the FGLair username and password
  in their own files, and the unit's name in ``config.toml``, however they are
  written.

What is found is reported by file and line only, never by the value itself, so
the scan's own output cannot leak what it guards.

    python3 scripts/private_data_scan.py            # the staged files (pre-commit)
    python3 scripts/private_data_scan.py --tracked  # every tracked file (the tests, CI)

The settings folder is ``$HEATPUMP_MCP_CONFIG``, or else
``$XDG_CONFIG_HOME/heatpump-mcp``, normally ``~/.config/heatpump-mcp``.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIVATE_IPV4 = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])"
)
MAC = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")
DOCUMENTATION_MAC = re.compile(r"^00[:-]00[:-]5E[:-]00[:-]53[:-]", re.IGNORECASE)
MIN_SECRET = 6  # shorter values ("true", a region) would match ordinary text

#: The files in the settings folder that each hold one private value.
SECRET_FILES = ("fglair-username", "fglair-password")
#: The settings in config.toml that are private. The region is not: it is one of three words.
PRIVATE_KEYS = {"unit"}


def settings_folder(env: Mapping[str, str] | None = None) -> pathlib.Path:
    """The owner's settings folder, as the server finds it, or as HEATPUMP_MCP_CONFIG names it."""
    env = os.environ if env is None else env
    if env.get("HEATPUMP_MCP_CONFIG"):
        return pathlib.Path(env["HEATPUMP_MCP_CONFIG"]).expanduser()
    base = env.get("XDG_CONFIG_HOME") or "~/.config"
    return pathlib.Path(base).expanduser() / "heatpump-mcp"


def private_values(folder: pathlib.Path) -> set[str]:
    """The private values in the owner's settings folder; an absent file holds none."""
    out: set[str] = set()
    for name in SECRET_FILES:
        path = folder / name
        value = path.read_text().strip() if path.is_file() else ""
        if len(value) >= MIN_SECRET:
            out.add(value)
    config = folder / "config.toml"
    try:
        settings = tomllib.loads(config.read_text()) if config.is_file() else {}
    except tomllib.TOMLDecodeError:
        settings = {}
    out |= {
        value
        for key, value in settings.items()
        if key in PRIVATE_KEYS and isinstance(value, str) and len(value) >= MIN_SECRET
    }
    return out


def offences(text: str, secrets: set[str]) -> list[str]:
    """What in `text` would say something private, as "line N: what", never the value."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        # RULE: a home-network address is never committed
        found += [f"line {number}: a home-network address" for _ in PRIVATE_IPV4.findall(line)]
        found += [
            f"line {number}: a hardware address" for m in MAC.findall(line) if not DOCUMENTATION_MAC.match(m)
        ]
        # RULE: a value from the settings folder is never committed
        found += [f"line {number}: a value from the settings folder" for s in secrets if s in line]
    return found


def _files(*, tracked: bool) -> list[pathlib.Path]:
    command = ["git", "ls-files"] if tracked else ["git", "diff", "--cached", "--name-only"]
    names = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)  # noqa: S603
    return [ROOT / n for n in names.stdout.splitlines() if (ROOT / n).is_file()]


def main(argv: list[str] | None = None) -> int:
    """Scan the staged (or every tracked) file; 1 when anything is found."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--tracked", action="store_true", help="scan every tracked file")
    args = parser.parse_args(argv)
    secrets = private_values(settings_folder())
    bad = 0
    for path in _files(tracked=args.tracked):
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for offence in offences(text, secrets):
            sys.stderr.write(f"{path.relative_to(ROOT)}: {offence}\n")
            bad += 1
    if bad:
        sys.stderr.write("Use 192.0.2.x and 00:00:5E:00:53:xx in examples, and never a real login.\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

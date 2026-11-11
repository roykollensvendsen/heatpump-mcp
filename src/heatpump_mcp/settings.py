# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the FGLair login lives, and how it is read without ever being shown.

The folder is ``$XDG_CONFIG_HOME/heatpump-mcp``, normally
``~/.config/heatpump-mcp``. It holds:

- ``fglair-username`` and ``fglair-password``: the FGLair app's login, one
  value per file, readable by the owner alone (``chmod 600``).
- ``config.toml``, optional: ``region`` (``eu``, the default, ``default`` for
  the Americas and elsewhere, or ``cn``), and ``unit``, the unit's name in the
  FGLair app, needed only when the account has more than one.

An error names the file to fix and never its contents.
"""

from __future__ import annotations

import pathlib
import stat
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

REGIONS = ("eu", "default", "cn")


#: How the owner enters the login again: the manager opens two masked boxes.
ENTER_LOGIN = "Ask the claude-voice manager to open the FGLair login boxes, and enter the login there."


class SettingsError(Exception):
    """The settings cannot be used; the message says which file to fix."""


@dataclass(frozen=True)
class Settings:
    """The login and where to use it; the password never appears in a repr."""

    username: str = field(repr=False)
    password: str = field(repr=False)
    region: str = "eu"
    unit_name: str | None = None
    #: Where the optional settings are, so a message can name the file to edit.
    config_file: str = "config.toml"


def config_folder(env: Mapping[str, str]) -> pathlib.Path:
    """The settings folder, following XDG_CONFIG_HOME."""
    base = env.get("XDG_CONFIG_HOME") or str(pathlib.Path(env.get("HOME", "~")) / ".config")
    return pathlib.Path(base).expanduser() / "heatpump-mcp"


def _secret(folder: pathlib.Path, name: str) -> str:
    path = folder / name
    if not path.is_file():
        msg = f"{path} is missing. {ENTER_LOGIN}"
        raise SettingsError(msg)
    # RULE: a login file others can read is refused
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        msg = f"{path} can be read by others, so I will not use it. Run: chmod 600 {path}"
        raise SettingsError(msg)
    value = path.read_text().strip()
    if not value:
        msg = f"{path} is empty. {ENTER_LOGIN}"
        raise SettingsError(msg)
    return value


def load_settings(folder: pathlib.Path) -> Settings:
    """Read the settings from `folder`; raises SettingsError naming what to fix."""
    username, password = _secret(folder, "fglair-username"), _secret(folder, "fglair-password")
    config_path = folder / "config.toml"
    try:
        table = tomllib.loads(config_path.read_text()) if config_path.is_file() else {}
    except tomllib.TOMLDecodeError:
        msg = (
            f"{config_path} is not valid TOML. "
            f"Fix it, or move it aside to use the defaults: mv {config_path} {config_path}.bad"
        )
        raise SettingsError(msg) from None
    region = table.get("region", "eu")
    if region not in REGIONS:
        msg = (
            f"region in {config_path} can only be {', '.join(REGIONS)}. "
            'Write one of them, such as: region = "eu"'
        )
        raise SettingsError(msg)
    unit = table.get("unit")
    return Settings(username, password, region, str(unit) if unit else None, str(config_path))

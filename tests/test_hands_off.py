# SPDX-License-Identifier: GPL-3.0-or-later
"""What this plugin never touches: firmware, Wi-Fi, schedules, timers and the account.

The owner's design allows four changes and nothing else. The settings are an
enum of exactly those four, and no name, attribute or string anywhere in the
package so much as mentions the rest, so a call to them cannot creep in
unnoticed. Docstrings and comments may explain; code may not.
"""

import ast
import pathlib
import re

from heatpump_mcp.unit import Setting

SRC = pathlib.Path(__file__).parent.parent / "src" / "heatpump_mcp"
FORBIDDEN = re.compile(
    r"firmware|(^|_)ota(_|$)|wi_?fi|wlan|ssid|schedul|timer|account|change_password|password_reset|sign_?up|delete",
    re.IGNORECASE,
)


def _words_in_code(tree):
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.alias)):
            yield node.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


def test_only_power_mode_temperature_and_fan_can_ever_be_changed():
    assert {s.value for s in Setting} == {"power", "mode", "temperature", "fan"}


def test_nothing_in_the_code_touches_firmware_wifi_schedules_timers_or_the_account():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "_version.py":
            continue
        found += [
            f"{path.name}: {word!r}"
            for word in _words_in_code(ast.parse(path.read_text()))
            if FORBIDDEN.search(word)
        ]
    assert not found, "\n".join(found)

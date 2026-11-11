# SPDX-License-Identifier: GPL-3.0-or-later
"""The layers: core, then drivers and tools, then the server and the command line.

The core (the unit, the service, what is said, the simulator and the settings)
knows no cloud library and no MCP; the FGLair driver knows its cloud and the
core; the tool helper knows the core and MCP, and never a driver. Only the
server and the command line put them together. Every import, including those
made only for type checking, is counted, because the driver must be swappable
without touching anything else.

The map of the layers is in `scripts/ratchet.py`, which also counts the breaches
for the code-health page; here any breach at all is refused.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
PACKAGE = ROOT / "src" / "heatpump_mcp"


def _ratchet():
    spec = importlib.util.spec_from_file_location("ratchet", ROOT / "scripts/ratchet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ratchet = _ratchet()


def test_every_module_has_a_layer_and_imports_only_what_its_layer_may():
    assert ratchet.layer_breaches(PACKAGE) == []


def test_the_core_never_imports_a_driver():
    core = [p for p in PACKAGE.rglob("*.py") if ratchet.layer(p.relative_to(PACKAGE).stem) == "core"]
    assert core, "no core modules found; the layer map is out of date"
    assert ratchet.layer("fglair") not in ratchet.ALLOWED["core"]
    assert "ayla_iot_unofficial" in ratchet.FORBIDDEN_OUTSIDE["core"]

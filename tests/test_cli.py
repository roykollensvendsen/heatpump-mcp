# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line, run in-process."""

import pytest

from heatpump_mcp import cli


def test_status_reads_the_pretend_unit_once(capsys):
    assert cli.main(["status", "--simulate"]) == 0
    assert capsys.readouterr().out.startswith("Simulated: The heat pump is on")


def test_without_simulate_nothing_is_reached_yet(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["status"])
    assert stopped.value.code == 2
    assert "--simulate" in capsys.readouterr().err

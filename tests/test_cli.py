# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line, run in-process."""

from heatpump_mcp import cli


def test_status_reads_the_pretend_unit_once(capsys):
    assert cli.main(["status", "--simulate"]) == 0
    assert capsys.readouterr().out.startswith("Simulated: The heat pump is on")


def test_without_a_login_the_status_says_which_file_to_write(capsys, tmp_path):
    assert cli.main(["status", "--config-dir", str(tmp_path)]) == 1
    said = capsys.readouterr().out
    assert said.startswith("The heat pump's login is not set up:")
    assert "fglair-username" in said

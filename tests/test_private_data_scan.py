# SPDX-License-Identifier: GPL-3.0-or-later
"""Nothing committed holds the owner's FGLair login, their unit's name, or a home address."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import private_data_scan


def test_a_home_network_address_is_never_committed():
    # Built at run time, so this file does not itself hold what it forbids.
    parts = (("10", "0", "0", "7"), ("172", "20", "1", "2"), ("192", "168", "1", "50"))
    for address in (".".join(p) for p in parts):
        assert private_data_scan.offences(f"unit at http://{address}:80", set()), address
    mac = ":".join(["F0", "12", "34", "56", "78", "9A"])  # noqa: FLY002
    assert private_data_scan.offences(f"mac {mac}", set())


def test_documentation_addresses_are_allowed():
    text = "unit = 192.0.2.10, mac = 00:00:5E:00:53:01, version 1.2.3.4, 8.8.8.8"
    assert private_data_scan.offences(text, set()) == []


def test_a_value_from_the_settings_folder_is_never_committed():
    assert private_data_scan.offences("password = 'abc123secret'", {"abc123secret"})
    assert private_data_scan.offences("nothing here", {"abc123secret"}) == []


def test_a_finding_names_the_line_and_never_the_value():
    found = private_data_scan.offences("first\nsecond abc123secret\n", {"abc123secret"})
    assert found == ["line 2: a value from the settings folder"]
    address = ".".join(("192", "168", "1", "50"))  # noqa: FLY002
    assert all(address not in f for f in private_data_scan.offences(f"at {address}", set()))


def test_the_tracked_tree_is_clean():
    assert private_data_scan.main(["--tracked"]) == 0


def test_the_login_files_and_the_unit_name_count_as_private(tmp_path):
    (tmp_path / "fglair-username").write_text("owner@example.com\n")
    (tmp_path / "fglair-password").write_text("pass-from-its-own-file\n")
    (tmp_path / "config.toml").write_text('region = "eu"\nunit = "Upstairs hallway"\n')
    assert private_data_scan.private_values(tmp_path) == {
        "owner@example.com",
        "pass-from-its-own-file",
        "Upstairs hallway",
    }


def test_a_missing_or_broken_settings_folder_holds_nothing(tmp_path):
    assert private_data_scan.private_values(tmp_path / "absent") == set()
    (tmp_path / "config.toml").write_text("not = [toml")
    assert private_data_scan.private_values(tmp_path) == set()


def test_the_settings_folder_follows_the_override_then_xdg():
    folder = private_data_scan.settings_folder
    assert folder({"HEATPUMP_MCP_CONFIG": "/somewhere"}) == pathlib.Path("/somewhere")
    assert folder({"XDG_CONFIG_HOME": "/cfg"}) == pathlib.Path("/cfg/heatpump-mcp")
    assert folder({}) == pathlib.Path("~/.config/heatpump-mcp").expanduser()

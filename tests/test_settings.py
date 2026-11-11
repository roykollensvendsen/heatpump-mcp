# SPDX-License-Identifier: GPL-3.0-or-later
"""The FGLair login, read from the owner's own files and never shown."""

import pytest

from heatpump_mcp.settings import SettingsError, config_folder, load_settings

LOGIN = "someone@example.com"
SECRET = "correct-horse-battery"  # noqa: S105 - a made-up test value


def _write(folder, name, text, mode=0o600):
    path = folder / name
    path.write_text(text)
    path.chmod(mode)
    return path


@pytest.fixture
def folder(tmp_path):
    _write(tmp_path, "fglair-username", LOGIN + "\n")
    _write(tmp_path, "fglair-password", SECRET + "\n")
    return tmp_path


def test_the_login_is_read_from_two_files_in_the_folder(folder):
    settings = load_settings(folder)
    assert (settings.username, settings.password) == (LOGIN, SECRET)
    assert settings.region == "eu"
    assert settings.unit_name is None


def test_the_region_and_the_unit_come_from_the_optional_config_file(folder):
    _write(folder, "config.toml", 'region = "default"\nunit = "Living room"\n', mode=0o644)
    settings = load_settings(folder)
    assert (settings.region, settings.unit_name) == ("default", "Living room")


def test_a_region_fglair_does_not_have_is_refused(folder):
    _write(folder, "config.toml", 'region = "mars"\n', mode=0o644)
    with pytest.raises(SettingsError, match="region"):
        load_settings(folder)


def test_the_login_is_never_shown_when_the_settings_are_printed(folder):
    settings = load_settings(folder)
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)


def test_a_login_file_others_can_read_is_refused(folder):
    (folder / "fglair-password").chmod(0o644)
    with pytest.raises(SettingsError) as refused:
        load_settings(folder)
    assert "chmod 600" in str(refused.value)
    assert SECRET not in str(refused.value)


def test_a_missing_login_says_which_file_to_write(tmp_path):
    with pytest.raises(SettingsError, match="fglair-username"):
        load_settings(tmp_path)


def test_the_folder_follows_xdg_config_home(tmp_path):
    assert config_folder({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "heatpump-mcp"
    assert config_folder({"HOME": "/home/someone"}).as_posix() == "/home/someone/.config/heatpump-mcp"

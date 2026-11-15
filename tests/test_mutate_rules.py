# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation run's own rules, each with the test its rule names.

`scripts/mutate.py` proves every other rule is guarded, so a mistake in it would
make every other rule look guarded when nothing guards it. Its own rules get the
same treatment: a marker, a row that switches it off, and a test named after it.
"""

import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).parent.parent


def _mutate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mutate_rules", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path / "project")
    monkeypatch.setattr(module, "MUTATE_TMP", tmp_path / "mutate-tmp")
    (tmp_path / "project").mkdir()
    return module


def _suite_says(module, monkeypatch, stdout):
    """Make the suite run answer with `stdout`, and keep the environment it was given."""
    seen = {}

    def run(command, **kwargs):
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 1, stdout, "")

    monkeypatch.setattr(module.subprocess, "run", run)
    return seen


def test_a_file_changed_during_the_run_is_never_restored_over(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    source = tmp_path / "source.py"
    source.write_text("edited while the suite ran\n")
    assert mutate.restore(source, "original\n", "mutated\n") is False
    assert source.read_text() == "edited while the suite ran\n"


def test_two_mutations_never_share_a_bytecode_cache(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    seen = _suite_says(mutate, monkeypatch, "1 passed\n")
    mutate.run_suite()
    assert seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_a_coloured_failure_line_is_still_attributed(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    _suite_says(mutate, monkeypatch, "\x1b[31mFAILED\x1b[0m tests/test_x.py::test_y - boom\n1 failed\n")
    assert mutate.run_suite() == (1, ["test_y"])


def test_an_empty_rule_table_is_not_a_crash(tmp_path, monkeypatch, capsys):
    mutate = _mutate(tmp_path, monkeypatch)
    monkeypatch.setattr(mutate, "stop_cleanly", lambda: None)
    monkeypatch.setattr(mutate, "ROWS", tmp_path / "project" / "scripts" / "mutations")
    monkeypatch.setattr(mutate, "all_rows", list)
    monkeypatch.setattr(sys, "argv", ["mutate.py"])
    assert mutate.main() == 0
    assert "no rows in scripts/mutations/ yet" in capsys.readouterr().out

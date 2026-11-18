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

from bounded import SECONDS

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
    mutate.run_suite(timeout=SECONDS)
    assert seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_a_coloured_failure_line_is_still_attributed(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    _suite_says(mutate, monkeypatch, "\x1b[31mFAILED\x1b[0m tests/test_x.py::test_y - boom\n1 failed\n")
    assert mutate.run_suite(timeout=SECONDS) == (1, ["test_y"])


def test_a_test_that_errors_is_named_as_an_error(tmp_path, monkeypatch):
    """A test whose setup broke went red without testing anything, so it is told apart."""
    mutate = _mutate(tmp_path, monkeypatch)
    _suite_says(mutate, monkeypatch, "ERROR tests/test_x.py::test_y - OSError\n1 error\n")
    assert mutate.run_suite(timeout=SECONDS) == (1, ["test_y (error)"])


def test_an_empty_rule_table_is_not_a_crash(tmp_path, monkeypatch, capsys):
    mutate = _mutate(tmp_path, monkeypatch)
    monkeypatch.setattr(mutate, "stop_cleanly", lambda: None)
    monkeypatch.setattr(mutate, "ROWS", tmp_path / "project" / "scripts" / "mutations")
    monkeypatch.setattr(mutate, "all_rows", list)
    monkeypatch.setattr(sys, "argv", ["mutate.py"])
    assert mutate.main() == 0
    assert "no rows in scripts/mutations/ yet" in capsys.readouterr().out


class _Pipe:
    """Output that, like a pipe in CI, shows only what has been flushed."""

    def __init__(self):
        self.held, self.shown = "", ""

    def write(self, text):
        self.held += text
        return len(text)

    def flush(self):
        self.shown, self.held = self.shown + self.held, ""


def test_a_mutation_run_names_each_rule_before_it_tests_it(tmp_path, monkeypatch):
    """The rule's name must be out of the process, not in its buffer, when its tests start.

    In CI the output is a pipe, which Python holds back until it is full. A run that hung on
    one rule left nothing in the log to say which. Its result line has to get out as well.
    """
    mutate = _mutate(tmp_path, monkeypatch)
    source = tmp_path / "project" / "guard.py"
    source.write_text("holds = True\n")
    monkeypatch.setattr(mutate, "RECOVERY", tmp_path / "recovery.json")
    monkeypatch.setattr(mutate, "check_own_code", lambda: None)
    rule = {"name": "a guard holds", "file": "guard.py", "find": "^holds", "replace": "holds = False"}
    monkeypatch.setattr(mutate, "all_rows", lambda: [rule])
    pipe, seen = _Pipe(), []
    monkeypatch.setattr(sys, "stdout", pipe)

    def suite():
        seen.append(pipe.shown)
        return 1, ["test_a_guard_holds"]

    monkeypatch.setattr(mutate, "run_suite", suite)
    mutate.measure_all(None)
    assert "testing  a guard holds\n" in seen[0]
    assert "KILLED   a guard holds: 1" in pipe.shown


def test_a_real_mutation_run_has_no_time_limit(tmp_path, monkeypatch):
    """Roy's rule: a mutation run is never capped. Only a test's scratch run passes a bound."""
    mutate = _mutate(tmp_path, monkeypatch)
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "1 passed\n", "")

    monkeypatch.setattr(mutate.subprocess, "run", run)
    mutate.run_suite()
    assert seen["timeout"] is None


def test_a_kill_by_another_test_names_the_tests_that_went_red(tmp_path, monkeypatch, capsys):
    """A rule caught by a neighbour can flip between runs, so the run says which tests it was."""
    mutate = _mutate(tmp_path, monkeypatch)
    source = tmp_path / "project" / "guard.py"
    source.write_text("holds = True\n")
    monkeypatch.setattr(mutate, "RECOVERY", tmp_path / "recovery.json")
    monkeypatch.setattr(mutate, "check_own_code", lambda: None)
    rule = {"name": "a guard holds", "file": "guard.py", "find": "^holds", "replace": "holds = False"}
    monkeypatch.setattr(mutate, "all_rows", lambda: [rule])
    monkeypatch.setattr(mutate, "run_suite", lambda: (2, ["test_a_neighbour", "test_another (error)"]))
    mutate.measure_all(None)
    said = capsys.readouterr().out
    assert "BY LUCK  a guard holds: 2, red: test_a_neighbour, test_another (error) (" in said

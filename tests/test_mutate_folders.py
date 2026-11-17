# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the mutation run works, and that only one works in a checkout at a time.

Its folder has a name anyone can guess, in a temporary folder everyone shares, so it is used
only when it is this user's and nobody else may enter it. Two runs in one checkout would
switch lines off under each other, so the second stops at once. And a run measures only this
checkout's code: two checkouts sharing one environment measured each other's.
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest
from waits import SECONDS, line_within

ROOT = pathlib.Path(__file__).parent.parent

HOLD = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(60)
"""


def _mutate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mutate_folders", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    monkeypatch.setattr(module, "ROOT", project)
    monkeypatch.setattr(module, "MUTATE_TMP", tmp_path / "mutate-tmp")
    monkeypatch.setattr(module, "RECOVERY", project / "scripts/.mutate-recovery.json")
    monkeypatch.setattr(module, "RUNNING", project / "scripts/.mutate-running.lock")
    monkeypatch.setattr(module, "stop_cleanly", lambda: None)
    return module


def _left_mutated(mutate):
    """A rule's file as a killed run left it, with its recovery note."""
    (mutate.ROOT / "rule.py").write_text("x = 2\n")
    mutate.RECOVERY.write_text(json.dumps({"file": "rule.py", "original": "x = 1\n", "mutated": "x = 2\n"}))


def test_the_mutation_run_works_only_in_a_private_folder_of_its_own(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    mutate.own_folder()
    assert mutate.MUTATE_TMP.stat().st_mode & 0o777 == 0o700
    mutate.MUTATE_TMP.chmod(0o755)
    for step in (mutate.sweep, mutate.run_suite):
        with pytest.raises(SystemExit) as refused:
            step()
        assert str(mutate.MUTATE_TMP) in str(refused.value)
    mutate.MUTATE_TMP.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    mutate.MUTATE_TMP.symlink_to(elsewhere)
    with pytest.raises(SystemExit):
        mutate.own_folder()


def test_only_one_mutation_run_at_a_time_in_a_checkout(tmp_path, monkeypatch):
    """A second run would put back the first one's switched-off line and measure nothing."""
    mutate = _mutate(tmp_path, monkeypatch)
    _left_mutated(mutate)
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD, str(mutate.RUNNING)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert line_within(holder.stdout).strip() == "held"
        monkeypatch.setattr(sys, "argv", ["mutate.py"])
        with pytest.raises(SystemExit) as refused:
            mutate.main()
    finally:
        holder.kill()
        holder.wait(SECONDS)
    assert "already running" in str(refused.value)
    assert mutate.RECOVERY.exists()
    assert (mutate.ROOT / "rule.py").read_text() == "x = 2\n"


def test_a_repair_only_run_puts_back_a_left_file_and_does_nothing_else(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    _left_mutated(mutate)
    (mutate.MUTATE_TMP / "run-left").mkdir(parents=True)

    def never(*_args, **_kwargs):
        msg = "a repair-only run measured something"
        raise AssertionError(msg)

    monkeypatch.setattr(mutate, "all_rows", never)
    monkeypatch.setattr(mutate, "run_suite", never)
    monkeypatch.setattr(sys, "argv", ["mutate.py", "--repair-only"])
    assert mutate.main() == 0
    assert (mutate.ROOT / "rule.py").read_text() == "x = 1\n"
    assert not mutate.RECOVERY.exists()
    assert (mutate.MUTATE_TMP / "run-left").is_dir()


def test_the_mutation_run_measures_only_this_checkout_s_code(tmp_path, monkeypatch):
    """Two checkouts sharing one environment measured each other's code, and every rule failed."""
    mutate = _mutate(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as refused:
        mutate.check_own_code()
    assert str(mutate.ROOT / "src") in str(refused.value)
    assert "uv sync" in str(refused.value)
    monkeypatch.setattr(mutate, "ROOT", ROOT)
    mutate.check_own_code()
    assert os.environ.get("UV_PROJECT_ENVIRONMENT") in (None, str(ROOT / ".venv"))

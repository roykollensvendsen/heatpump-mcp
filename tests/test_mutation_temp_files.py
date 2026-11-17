# SPDX-License-Identifier: GPL-3.0-or-later
"""A mutation run leaves no temporary files behind.

`scripts/mutate.py` runs the whole suite once per rule, and every run made its
own pytest folder of temporary files. Over a day of runs those folders filled
the machine's temporary space until no program could start a command. Each run
now gets a folder of its own, outside the checkout, removed when the run ends;
whatever a killed run left is swept away at the next start.
"""

import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest
from waits import SECONDS

ROOT = pathlib.Path(__file__).parent.parent

LEAVES_FILES = """
import os

def test_it_writes_files_it_cannot_delete(tmp_path):
    (tmp_path / "plain.txt").write_text("x")
    (tmp_path / "locked.txt").write_text("x")
    os.chmod(tmp_path / "locked.txt", 0)
    (tmp_path / "shut").mkdir()
    (tmp_path / "shut" / "inside.txt").write_text("x")
    os.chmod(tmp_path / "shut", 0)
"""


def _mutate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mutate_temp", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_leaves_files.py").write_text(LEAVES_FILES)
    monkeypatch.setattr(module, "ROOT", project)
    monkeypatch.setattr(module, "MUTATE_TMP", tmp_path / "mutate-tmp")
    return module


def test_a_suite_run_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    failed, _ = mutate.run_suite()
    assert failed == 0
    assert list(mutate.MUTATE_TMP.glob("run-*")) == []


LEAVES_A_FOLDER = """
import tempfile

def test_it_forgets_to_clean_up():
    tempfile.mkdtemp()
"""


def test_a_switched_off_cleanup_leaves_nothing_in_the_computer_s_temporary_folder(tmp_path, monkeypatch):
    """A rule switched off may be a cleanup, so what the tests make must stay in the run's folder.

    Given its folder, the test setup keeps out of the way, so the tests would otherwise write
    to the computer's own temporary folder, and a switched-off cleanup would leave it there.
    """
    mutate = _mutate(tmp_path, monkeypatch)
    (mutate.ROOT / "tests" / "test_forgets.py").write_text(LEAVES_A_FOLDER)
    outer = tmp_path / "outer"
    outer.mkdir()
    monkeypatch.setenv("TMPDIR", str(outer))
    failed, _ = mutate.run_suite()
    assert failed == 0
    assert list(outer.iterdir()) == []
    assert not mutate.MUTATE_TMP.exists()


def test_the_mutation_run_s_own_folder_is_removed_once_it_is_empty(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    mutate.run_suite()
    assert not mutate.MUTATE_TMP.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows needs extra rights for a link, and has no 0755")
def test_removing_a_run_s_files_never_changes_a_folder_it_links_to(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (outside / "kept.txt").write_text("x")
    (mutate.ROOT / "tests" / "test_links_out.py").write_text(
        f"def test_it_links_out(tmp_path):\n    (tmp_path / 'link').symlink_to({str(outside)!r})\n"
    )
    failed, _ = mutate.run_suite()
    assert failed == 0
    assert (outside.stat().st_mode & 0o777, (outside / "kept.txt").exists()) == (0o755, True)


def test_a_killed_run_s_temporary_files_are_swept_at_the_next_start(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    mutate.MUTATE_TMP.mkdir(mode=0o700)
    left = mutate.MUTATE_TMP / "run-left-by-a-killed-run"
    (left / "shut").mkdir(parents=True)
    (left / "shut" / "inside.txt").write_text("x")
    (left / "shut").chmod(0)
    mutate.sweep()
    assert not left.exists()


def test_the_temporary_files_live_outside_the_checkout():
    """Inside it, a test that makes a git repository of its own would find this one instead."""
    spec = importlib.util.spec_from_file_location("mutate_where", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert ROOT.resolve() not in module.MUTATE_TMP.resolve().parents


def test_a_passing_test_keeps_no_temporary_files():
    settings = (ROOT / "pyproject.toml").read_text()
    assert 'tmp_path_retention_policy = "failed"' in settings
    assert "tmp_path_retention_count = 1" in settings


SLOW = "import time\n\ndef test_it_takes_a_while():\n    time.sleep(60)\n"

STOPPED_RUN = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("mutate_stopped", sys.argv[1])
mutate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutate)
mutate.ROOT = pathlib.Path(sys.argv[2])
mutate.MUTATE_TMP = pathlib.Path(sys.argv[3])
mutate.stop_cleanly()
mutate.run_suite()
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no polite stop signal to send")
def test_a_stopped_mutation_run_still_cleans_up(tmp_path):
    """A run stopped politely, as CI cancels one or a terminal closes, removes its folder too.

    Python runs no `finally` on a stop signal unless it is told to, so a cancelled run
    left its whole folder of temporary files behind for the next start to find.
    """
    project, scratch = tmp_path / "project", tmp_path / "mutate-tmp"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_slow.py").write_text(SLOW)
    script = tmp_path / "stopped_run.py"
    script.write_text(STOPPED_RUN)
    run = subprocess.Popen(
        [sys.executable, str(script), str(ROOT / "scripts/mutate.py"), str(project), str(scratch)]
    )
    try:
        deadline = time.monotonic() + 60
        while not (scratch.is_dir() and any(scratch.iterdir())):
            assert time.monotonic() < deadline, "the run never made its folder"
            time.sleep(0.1)
        time.sleep(0.5)  # let pytest start inside the run's folder
        run.send_signal(signal.SIGTERM)
        assert run.wait(60) == 128 + signal.SIGTERM
    finally:
        run.kill()
    assert not scratch.exists() or list(scratch.iterdir()) == []


def test_the_mutation_run_turns_a_stop_into_an_exit_before_it_starts(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(mutate, "stop_cleanly", lambda: seen.append("stop"))
    monkeypatch.setattr(mutate, "all_rows", list)
    monkeypatch.setattr(mutate, "ROWS", mutate.ROOT / "scripts/mutations")
    monkeypatch.setattr(sys, "argv", ["mutate.py"])
    mutate.main()
    assert seen == ["stop"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no hang-up signal")
def test_a_second_stop_cannot_cut_the_mutation_run_s_cleanup_short(tmp_path, monkeypatch):
    """GitHub sends an interrupt and then a stop; the second must not end the cleanup halfway."""
    mutate = _mutate(tmp_path, monkeypatch)
    kept = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        mutate.stop_cleanly()
        with pytest.raises(SystemExit):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert {s: signal.getsignal(s) for s in kept} == dict.fromkeys(kept, signal.SIG_IGN)
    finally:
        for s, handler in kept.items():
            signal.signal(s, handler)


INTERRUPTED_THEN_STOPPED = """
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location("stopped_twice", sys.argv[1])
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
here = pathlib.Path(sys.argv[2])
script.stop_cleanly()
try:
    (here / "ready").touch()
    time.sleep(60)
finally:
    (here / "cleaning").touch()
    time.sleep(2)
    (here / "cleaned").touch()
"""


def interrupt_then_stop(script, here):
    """Interrupt `script`, then stop it while it cleans up, as GitHub cancels a job; True if it finished."""
    run = subprocess.Popen([sys.executable, "-c", INTERRUPTED_THEN_STOPPED, str(script), str(here)])
    try:
        for step, sent in (("ready", signal.SIGINT), ("cleaning", signal.SIGTERM)):
            deadline = time.monotonic() + 60
            while not (here / step).exists():
                assert run.poll() is None, f"never {step}"
                assert time.monotonic() < deadline, f"never {step}"
                time.sleep(0.05)
            run.send_signal(sent)
        run.wait(60)
    finally:
        run.kill()
    return (here / "cleaned").exists()


STARTS_THEN_WAITS = """
import pathlib, time

def test_it_takes_a_while():
    pathlib.Path("started").touch()
    time.sleep(60)
"""

WHOLE_RUN_STOPPED = """
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location("mutate_stopped_twice", sys.argv[1])
mutate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutate)
here = pathlib.Path(sys.argv[2])
mutate.ROOT, mutate.ROWS = here / "project", here / "rows"
mutate.RECOVERY, mutate.MUTATE_TMP = here / "recovery.json", here / "mutate-tmp"
sys.argv = ["mutate.py"]
rows = mutate.all_rows
mutate.all_rows = lambda folder=None: rows(here / "rows")  # its default is the real tree's
assert [r["file"] for r in mutate.all_rows()] == ["rule.py"], "would mutate the real tree"
restore = mutate.restore

def slow_restore(*args):
    (here / "cleaning").touch()
    time.sleep(2)
    return restore(*args)

mutate.restore = slow_restore
mutate.check_own_code = lambda: None  # the small project has no package
sys.exit(mutate.main())
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no polite stop signal to send")
def test_an_interrupt_and_then_a_stop_still_let_the_mutation_run_clean_up(tmp_path):
    """GitHub cancels a job with an interrupt, then a stop; the stop must not end the cleanup halfway.

    A whole run is stopped while the tests run against a switched-off rule: the rule's line
    must be back, and the run's folder gone, although the stop came during the cleanup.
    """
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "rule.py").write_text("x = 1\n")
    (project / "tests" / "test_slow.py").write_text(STARTS_THEN_WAITS)
    (tmp_path / "rows").mkdir()
    (tmp_path / "rows" / "a.toml").write_text(
        "[[rule]]\nname = \"a\"\nfile = \"rule.py\"\nfind = '^x = 1$'\nreplace = 'x = 2'\n"
    )
    run = subprocess.Popen(
        [sys.executable, "-c", WHOLE_RUN_STOPPED, str(ROOT / "scripts/mutate.py"), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not (project / "started").exists():
            assert run.poll() is None, run.communicate(timeout=SECONDS)[0]
            assert time.monotonic() < deadline, "the tests never started"
            time.sleep(0.05)
        assert (project / "rule.py").read_text() == "x = 2\n", "the rule was switched off"
        run.send_signal(signal.SIGINT)
        while not (tmp_path / "cleaning").exists():
            assert run.poll() is None, run.communicate(timeout=SECONDS)[0]
            assert time.monotonic() < deadline, "the cleanup never started"
            time.sleep(0.05)
        run.send_signal(signal.SIGTERM)
        out, _ = run.communicate(timeout=60)
    finally:
        run.kill()
    assert (project / "rule.py").read_text() == "x = 1\n", out
    assert not (tmp_path / "recovery.json").exists()
    assert not (tmp_path / "mutate-tmp").exists()


RUNNER_LEFT_MUTATED = "no rows in"


@pytest.mark.skipif(sys.platform == "win32", reason="the runner is only run on Linux")
def test_a_runner_left_mutated_starts_again_from_its_repaired_file(tmp_path):
    """A run killed while it had mutated its own runner must not go on with the mutated code.

    The next start repairs the file on disk, but the code it is running was read before the
    repair, so it starts itself again to run the repaired one.
    """
    project = tmp_path / "project"
    (project / "scripts" / "mutations").mkdir(parents=True)
    runner = project / "scripts" / "mutate.py"
    original = (ROOT / "scripts" / "mutate.py").read_text()
    assert original.count(RUNNER_LEFT_MUTATED) == 1
    mutated = original.replace(RUNNER_LEFT_MUTATED, "MUTATED " + RUNNER_LEFT_MUTATED)
    runner.write_text(mutated)
    (project / "scripts" / ".mutate-recovery.json").write_text(
        json.dumps({"file": "scripts/mutate.py", "original": original, "mutated": mutated})
    )
    (tmp_path / "tmp").mkdir()
    ran = subprocess.run(
        [sys.executable, str(runner)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TMPDIR": str(tmp_path / "tmp")},
        timeout=60,
    )
    assert "repaired scripts/mutate.py" in ran.stdout, ran.stdout + ran.stderr
    assert runner.read_text() == original
    assert "MUTATED" not in ran.stdout, ran.stdout
    assert RUNNER_LEFT_MUTATED in ran.stdout

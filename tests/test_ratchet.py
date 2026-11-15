# SPDX-License-Identifier: GPL-3.0-or-later
"""The quality ratchet: a change may make the code better or leave it as it is, never worse without a reason.

Ported from claude-voice's own ratchet tests. The layers among the modules are
counted here too, so that the code-health page can show them, and
`tests/test_layers.py` refuses a breach outright.
"""

import importlib.util
import json
import pathlib
import signal
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def _load():
    """The ratchet, loaded by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("ratchet", ROOT / "scripts/ratchet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ratchet = _load()

BASE = {
    "coverage": 85.0,
    "mutation": {"killed": 20, "total": 20},
    "tests": 400,
    "lint_errors": 0,
    "type_errors": 0,
    "cycles": 0,
    "layer_violations": 0,
    "duplication": 2.0,
    "complexity": {"pkg/a.py": 10, "pkg/b.py": 5},
    "test_seconds": 40.0,
    "test_budget": {"seconds": 100, "tolerance": 0.25},
}
BUDGET = {"seconds": 100, "tolerance": 0.25}


def measured(**changes):
    return {**json.loads(json.dumps(BASE)), **changes}


def problems(base, now, exceptions=()):
    return ratchet.compare(base, now, set(exceptions), BUDGET)


# -- what gets worse ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "metric"),
    [
        ({"coverage": 83.0}, "coverage"),
        ({"mutation": {"killed": 19, "total": 20}}, "mutation"),
        ({"tests": 399}, "tests"),
        ({"lint_errors": 1}, "lint_errors"),
        ({"type_errors": 2}, "type_errors"),
        ({"cycles": 1}, "cycles"),
        ({"layer_violations": 1}, "layer_violations"),
        ({"duplication": 3.0}, "duplication"),
        ({"complexity": {"pkg/a.py": 11, "pkg/b.py": 5}}, "complexity"),
    ],
)
def test_a_measure_that_gets_worse_fails_the_check(change, metric):
    found = problems(BASE, measured(**change))
    assert found
    assert all(metric in p for p in found)


def test_coverage_may_wobble_a_little_between_machines():
    assert problems(BASE, measured(coverage=84.7)) == []


def test_a_new_module_starts_its_own_baseline():
    now = measured(complexity={"pkg/a.py": 10, "pkg/b.py": 5, "pkg/c.py": 12})
    assert problems(BASE, now) == []


def test_a_new_module_must_keep_every_function_at_fifteen_or_below():
    now = measured(complexity={"pkg/a.py": 10, "pkg/b.py": 5, "pkg/new.py": 16})
    found = problems(BASE, now)
    assert found
    assert "pkg/new.py" in found[0]
    assert problems(BASE, measured(complexity={"pkg/a.py": 10, "pkg/b.py": 5, "pkg/new.py": 15})) == []


def test_a_base_without_a_new_measure_has_nothing_to_compare_with():
    base = {k: v for k, v in BASE.items() if k != "duplication"}
    assert problems(base, measured(duplication=9.0)) == []


def test_a_measure_may_get_worse_when_the_change_says_why():
    now = measured(coverage=80.0, tests=390)
    assert problems(BASE, now, exceptions={"coverage", "tests"}) == []
    assert problems(BASE, now, exceptions={"coverage"}) != []


def test_an_exception_is_written_in_the_pull_request_as_a_line_of_its_own():
    body = (
        "Some text.\n\nRatchet-Exception: coverage - the old module was deleted\n"
        "ratchet-exception: tests: merged two\n"
    )
    assert ratchet.exceptions(body) == {"coverage", "tests"}
    assert ratchet.exceptions("We talked about a Ratchet-Exception: coverage once.") == set()
    assert ratchet.exceptions(None) == set()


# -- the baseline moves with the code ----------------------------------------------------


def test_a_pull_request_need_not_record_the_levels_it_reached():
    better = measured(coverage=88.0, tests=410, complexity={"pkg/a.py": 8, "pkg/b.py": 5})
    assert problems(BASE, better) == []


def test_the_suite_must_stay_inside_its_time_budget():
    assert problems(BASE, measured(test_seconds=124.0)) == []  # inside the tolerance
    found = problems(BASE, measured(test_seconds=126.0))
    assert found
    assert "test_seconds" in found[0]


def test_the_time_budget_is_the_one_thing_quality_json_keeps(tmp_path):
    path = tmp_path / "quality.json"
    path.write_text(json.dumps({"test_budget": {"seconds": 90, "tolerance": 0.1}}))
    assert ratchet.budget(path) == {"seconds": 90, "tolerance": 0.1}
    assert ratchet.budget(tmp_path / "missing.json") == ratchet.DEFAULT_BUDGET
    assert set(json.loads((ROOT / "quality.json").read_text())) == {"test_budget"}


def test_measured_levels_are_written_for_main_to_keep(tmp_path):
    out = tmp_path / "levels.json"
    ratchet.write_levels(out, {**measured(), "test_budget": None})
    assert json.loads(out.read_text()) == {k: v for k, v in BASE.items() if k != "test_budget"}


def test_the_levels_main_kept_are_the_base_when_they_are_there(tmp_path):
    kept = tmp_path / "levels.json"
    kept.write_text(json.dumps(BASE))
    asked = []
    assert ratchet.base_levels(kept, lambda: asked.append(1) or {}) == BASE
    assert asked == []


def test_when_the_levels_main_kept_are_missing_the_base_is_measured(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    for kept in (None, tmp_path / "missing.json", tmp_path / "broken.json"):
        assert ratchet.base_levels(kept, lambda: BASE) == BASE


# -- what is measured --------------------------------------------------------------------


def test_complexity_is_counted_per_function_and_a_nested_function_counts_on_its_own(tmp_path):
    source = textwrap.dedent(
        """
        def outer(x):
            if x:
                return 1
            def inner(y):
                for i in y:
                    if i and y:
                        return i
            return inner

        def flat():
            return 0
        """,
    )
    (tmp_path / "m.py").write_text(source)
    assert ratchet.complexity(tmp_path) == {"m.py": 4}  # inner: 1 + for + if + and


def _package(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_a_cycle_among_the_modules_is_counted_and_a_lazy_import_is_not(tmp_path):
    pkg = tmp_path / "pkg"
    _package(
        pkg,
        {
            "__init__.py": "",
            "a.py": "from . import b\n",
            "b.py": "from .a import thing\n",
            "c.py": "def later():\n    from . import a\n",
            "d.py": "from . import c\n",
        },
    )
    assert ratchet.cycles(pkg) == 1


def test_a_cycle_through_a_subpackage_by_absolute_imports_is_counted(tmp_path):
    pkg = tmp_path / "pkg"
    _package(
        pkg,
        {
            "__init__.py": "",
            "core.py": "from pkg.drivers.tv import Tv\n",
            "drivers/__init__.py": "",
            "drivers/tv.py": "import pkg.core\n",
            "tools/__init__.py": "from pkg.tools import say\n",
            "tools/say.py": "from pkg import core\n",
            "other.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import core\n",
        },
    )
    assert ratchet.cycles(pkg) == 1


def _layered(tmp_path, files):
    """A package named like this one, so its imports read as they do in src/."""
    pkg = tmp_path / "heatpump_mcp"
    _package(pkg, {"__init__.py": "", **files})
    return pkg


def test_an_import_a_layer_may_not_use_is_a_breach(tmp_path):
    allowed = {
        "unit.py": "import json\n",
        "settings.py": "",
        "service.py": "from heatpump_mcp.unit import Reading\n",
        "fglair.py": "import ayla_iot_unofficial\nfrom heatpump_mcp.unit import Reading\n",
        "tools.py": "from heatpump_mcp.service import x\nfrom mcp import types\n",
        "server.py": "from heatpump_mcp.tools import tool\nimport mcp\n",
        "cli.py": "from heatpump_mcp.fglair import connect\nfrom heatpump_mcp.server import build\n",
    }
    assert ratchet.layer_breaches(_layered(tmp_path, allowed)) == []
    wrong = {
        "service.py": "from heatpump_mcp.fglair import connect\n",
        "fglair.py": "import mcp.types\n",
        "tools.py": "def later():\n    from . import fglair\n",
        "unit.py": "import ayla_iot_unofficial\n",
    }
    assert ratchet.layer_breaches(_layered(tmp_path / "b", {**allowed, **wrong})) == [
        "fglair (drivers) imports mcp.types",
        "service (core) imports heatpump_mcp.fglair (drivers)",
        "tools (tools) imports heatpump_mcp.fglair (drivers)",
        "unit (core) imports ayla_iot_unofficial",
    ]


def test_a_module_with_no_layer_is_a_breach(tmp_path):
    assert ratchet.layer_breaches(_layered(tmp_path, {"stray.py": ""})) == ["stray has no layer"]


def test_duplicated_code_is_the_share_of_lines_in_blocks_that_appear_twice(tmp_path):
    block = "\n".join(f"x{i} = compute({i})" for i in range(6))
    (tmp_path / "a.py").write_text(
        block + "\n\nunique_a = 1\n" + "\n".join(f"y{i} = {i}" for i in range(3)) + "\n"
    )
    (tmp_path / "b.py").write_text("    " + block.replace("\n", "\n    ") + "\nunique_b = 2\n")
    assert ratchet.duplication(tmp_path) == round(100 * 12 / 17, 2)  # 12 of 17 non-blank lines


def test_the_mutation_tally_is_read_from_the_mutation_scripts_last_line():
    out = "KILLED  a rule: 1\n\n7/9 rules turned off the test that names them\n"
    assert ratchet.mutation_tally(out, returncode=1) == {"killed": 7, "total": 9}
    full = "9/9 rules turned off the test that names them\n"
    assert ratchet.mutation_tally(full, returncode=0) == {"killed": 9, "total": 9}
    # A run that failed for any other reason is never a full score.
    assert ratchet.mutation_tally(full, returncode=1) == {"killed": 8, "total": 9}
    assert ratchet.mutation_tally("Traceback ...", returncode=1) == {"killed": 0, "total": 1}


STOPPED_RATCHET = """
import sys, tempfile, time
sys.path.insert(0, sys.argv[1])
import ratchet
ratchet.stop_cleanly()
with tempfile.TemporaryDirectory(dir=sys.argv[2]):
    print("ready", flush=True)
    time.sleep(60)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no polite stop signal to send")
def test_a_stopped_ratchet_still_removes_its_copy_of_main(tmp_path):
    """The ratchet measures main in a copy of it, and a stop signal used to leave that copy.

    Python runs no `finally` and no `with` exit on a stop signal unless it is told to.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    script = tmp_path / "stopped.py"
    script.write_text(STOPPED_RATCHET)
    where = str(ROOT / "scripts")
    run = subprocess.Popen(
        [sys.executable, str(script), where, str(scratch)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert run.stdout.readline().strip() == "ready"
        run.send_signal(signal.SIGTERM)
        assert run.wait(60) == 128 + signal.SIGTERM
    finally:
        run.kill()
    assert list(scratch.iterdir()) == []


def test_the_ratchet_turns_a_stop_into_an_exit_before_it_starts(monkeypatch):
    seen = []
    monkeypatch.setattr(ratchet, "stop_cleanly", lambda: seen.append("stop"))
    monkeypatch.setattr(sys, "argv", ["ratchet.py", "check"])
    assert ratchet.main() == 1  # no --base: it stops before measuring anything
    assert seen == ["stop"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no hang-up signal")
def test_a_second_stop_cannot_cut_the_cleanup_short():
    """GitHub sends an interrupt and then a stop; the second must not end the cleanup halfway."""
    kept = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        ratchet.stop_cleanly()
        with pytest.raises(SystemExit):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert {s: signal.getsignal(s) for s in kept} == dict.fromkeys(kept, signal.SIG_IGN)
    finally:
        for s, handler in kept.items():
            signal.signal(s, handler)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no polite stop signal to send")
def test_an_interrupt_and_then_a_stop_still_let_the_ratchet_clean_up(tmp_path):
    """GitHub cancels a job with an interrupt, then a stop; the stop must not end the cleanup halfway."""
    from test_mutation_temp_files import interrupt_then_stop  # noqa: PLC0415

    assert interrupt_then_stop(ROOT / "scripts/ratchet.py", tmp_path)


CLEANS_UP_SLOWLY = """
import pathlib, sys, time
here = pathlib.Path(sys.argv[1])
try:
    (here / "ready").touch()
    time.sleep(60)
finally:
    time.sleep(1)
    (here / "cleaned").touch()
"""

STOPPED_WHILE_RUNNING = """
import sys
sys.path.insert(0, sys.argv[1])
import ratchet
ratchet.stop_cleanly()
ratchet._run(sys.executable, "-c", sys.argv[2], sys.argv[3])
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no polite stop signal to send")
def test_a_stopped_ratchet_lets_the_command_it_runs_clean_up_first(tmp_path):
    """The ratchet runs the tests and the mutation run; stopping it used to kill them outright.

    Killed, the mutation run cannot put its switched-off line back and the tests cannot
    remove their folder. Now the command is interrupted, as Ctrl-C would, and waited for.
    """
    run = subprocess.Popen(
        [sys.executable, "-c", STOPPED_WHILE_RUNNING, str(ROOT / "scripts"), CLEANS_UP_SLOWLY, str(tmp_path)]
    )
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "ready").exists():
            assert run.poll() is None, "the command never started"
            assert time.monotonic() < deadline, "the command never started"
            time.sleep(0.05)
        run.send_signal(signal.SIGTERM)
        assert run.wait(60) == 128 + signal.SIGTERM
    finally:
        run.kill()
    assert (tmp_path / "cleaned").exists()


def test_the_base_is_measured_where_no_other_branch_can_be_seen(tmp_path, monkeypatch):
    """Main's own checks, such as its history scan, must not read the branch under review."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.org"],
        ["config", "user.name", "t"],
    ):
        git(*args)
    (repo / "notes.txt").write_text("main\n")
    git("add", "notes.txt")
    git("commit", "-q", "-m", "main")
    git("switch", "-q", "-c", "under-review")
    (repo / "notes.txt").write_text("under review\n")
    git("commit", "-q", "-am", "under review")
    git("switch", "-q", "main")
    monkeypatch.setattr(ratchet, "ROOT", repo)
    copy = tmp_path / "base"
    ratchet.copy_of("main", copy)
    seen = subprocess.run(
        ["git", "log", "--all", "--format=%s"], cwd=copy, check=True, capture_output=True, text=True
    ).stdout
    assert (copy / "notes.txt").read_text() == "main\n"
    assert "under review" not in seen


OUTLIVES = """
import os, pathlib, sys, time
here = pathlib.Path(sys.argv[1])
(here / "session").write_text(str(os.getsid(0) == os.getpid()))
(here / "ready").touch()
time.sleep(1)
for _ in range(2000):
    print("x" * 100, flush=True)
(here / "finished").touch()
"""

KILLED_WHILE_RUNNING = """
import sys
sys.path.insert(0, sys.argv[1])
import ratchet
ratchet._run(sys.executable, "-c", sys.argv[2], sys.argv[3])
"""


def _killed_while_running(tmp_path):
    """Start the ratchet on a command, kill it with no warning, and let the command run on."""
    run = subprocess.Popen(
        [sys.executable, "-c", KILLED_WHILE_RUNNING, str(ROOT / "scripts"), OUTLIVES, str(tmp_path)]
    )
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "ready").exists():
            assert run.poll() is None, "the command never started"
            assert time.monotonic() < deadline, "the command never started"
            time.sleep(0.05)
        run.kill()
        run.wait(60)
        while not (tmp_path / "finished").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        run.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="only run on Linux")
def test_a_command_outlives_a_killed_ratchet_to_finish_its_own_cleanup(tmp_path):
    """Killed with `kill -9`, the ratchet can do nothing, so its command must manage alone.

    Writing to a pipe whose reader is gone kills the writer, which for the mutation run means
    a line left switched off. Its output goes to a file, which nobody has to read.
    """
    _killed_while_running(tmp_path)
    assert (tmp_path / "finished").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="only run on Linux")
def test_the_ratchet_s_command_gets_a_stop_only_through_the_ratchet(tmp_path):
    """Ctrl-C in a terminal reaches every process in the foreground group at once.

    The command runs in a session of its own, so it hears the stop once, from the ratchet,
    which then waits for it.
    """
    _killed_while_running(tmp_path)
    assert (tmp_path / "session").read_text() == "True"

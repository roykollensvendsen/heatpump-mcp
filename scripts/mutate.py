#!/usr/bin/env python3
"""Turn every rule off in turn and see which tests notice.

Green on a first run proves nothing: a test that has never been red tests
nothing. Where a rule is written before its test, the test is run red first.
Where a rule already exists, this is the substitute. Every rule has a row in
a file of its own under scripts/mutations/. Run it from the repository root:

    uv run python scripts/mutate.py [--write evidence/tests/rule-mutations.md]

A kill is not enough on its own. A mutation breaks whatever it breaks, and it
can break a test for an incidental reason — a documented example whose output
happened to change — leaving a rule looking guarded when nothing guards it. So
this also checks that the test named after the rule is one of the ones that went
red, and fails the run when it is not. `tests/test_rules_have_evidence.py` is
what requires that test to exist in the first place.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: One row per file: scripts/mutations/<the row's file, whole>/<the rule's words>.toml. Every pull
#: request that added a row at the end of one shared table collided with every
#: other; a file per row only meets another pull request adding the same rule.
ROWS = ROOT / "scripts/mutations"
#: Where the one mutated file is recorded while it is mutated. A `finally` does
#: not run when the process is killed, so without this a hard stop leaves a
#: source file switched off and the next person wondering why the suite is red.
RECOVERY = ROOT / "scripts/.mutate-recovery.json"
#: Held locked for the whole of a run, so a second run in the same checkout stops at once.
RUNNING = ROOT / "scripts/.mutate-running.lock"
#: The package the tests import, which must come from this checkout.
PACKAGE = "heatpump_mcp"
#: Where each suite run keeps its temporary files, one folder per run, removed when it
#: ends. Outside the checkout, where a test making a git repository of its own would
#: find this one instead; named after the checkout, so two never sweep each other's.
MUTATE_TMP = pathlib.Path(tempfile.gettempdir()) / (
    "heatpump-mcp-mutate-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]
)


def test_name_for(rule: str) -> str:
    """The test name a rule requires: its own words, as an identifier.

    Imported by ``tests/test_rules_have_evidence.py`` so the rule is written
    once. A rule named "a spoken reply is capped" wants a test called
    ``test_a_spoken_reply_is_capped``.
    """
    words = re.sub(r"[^a-z0-9]+", "_", rule.lower())
    return "test_" + re.sub(r"_+", "_", words).strip("_")


def row_path(rule: dict) -> pathlib.PurePosixPath:
    """Where a rule's row belongs under ``ROWS``: its source's whole path, then its own words.

    A rule named "a status is kept for thirty seconds" in
    ``src/heatpump_mcp/service.py`` goes in
    ``src/heatpump_mcp/service.py/a-status-is-kept-for-thirty-seconds.toml``. The whole
    path, extension included, is the same layout in every repository and every
    language, and no two source files can share a folder.
    """
    words = re.sub(r"[^a-z0-9]+", "-", str(rule["name"]).lower()).strip("-")
    if not words:
        msg = f"the rule {rule['name']!r} has no letter or digit to file its row under; reword it"
        raise SystemExit(msg)
    return pathlib.PurePosixPath(rule["file"]) / f"{words}.toml"


def _rows_in(path: pathlib.Path) -> list[dict]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("rule", [])


def all_rows(folder: pathlib.Path = ROWS) -> list[dict]:
    """Every row, one file per row, in path order."""
    found = [
        (f"{folder.name}/{path.relative_to(folder).as_posix()}", rule)
        for path in (sorted(folder.rglob("*.toml")) if folder.is_dir() else [])
        for rule in _rows_in(path)
    ]
    where: dict[str, str] = {}
    for place, rule in found:
        if rule["name"] in where:
            msg = (
                f"the rule {rule['name']!r} has two rows, in {where[rule['name']]} and in {place};"
                f" delete one and keep {folder.name}/{row_path(rule)}"
            )
            raise SystemExit(msg)
        where[rule["name"]] = place
    return [rule for _, rule in found]


def misfiled(folder: pathlib.Path = ROWS) -> list[str]:
    """Row files that hold other than one row, or sit where their rule does not put them."""
    found = []
    for path in sorted(folder.rglob("*.toml")) if folder.is_dir() else []:
        here = pathlib.PurePosixPath(path.relative_to(folder).as_posix())
        rows = _rows_in(path)
        if len(rows) != 1:
            found.append(f"{here} holds {len(rows)} rows; give each rule a file of its own")
        elif here != (belongs := row_path(rows[0])):
            found.append(f"{here} should be {belongs}; move it there")
    return found


def line_of(text: str, pattern: str, occurrence: int) -> int:
    """Find the one line a rule lives on, by pattern rather than by number."""
    seen = 0
    for i, line in enumerate(text.splitlines()):
        if re.search(pattern, line):
            seen += 1
            if seen == occurrence:
                return i
    msg = f"no line {occurrence} matching {pattern!r}"
    raise SystemExit(msg)


def _open_up(place: pathlib.Path) -> None:
    """Give the owner back every right on one folder or file, adding to the rights it has.

    A link is left alone: changing its rights would change whatever it points to,
    which may be outside the run's folder.
    """
    if place.is_symlink():
        return
    with contextlib.suppress(OSError):
        place.chmod(place.stat().st_mode | stat.S_IRWXU)


def _writable_again(func: Callable[[str], object], path: str, _exc: object) -> None:
    """Let a refused delete through: open up the path and its folder, then try again."""
    for place in (pathlib.Path(path).parent, pathlib.Path(path)):
        _open_up(place)
    func(path)


def _remove(folder: pathlib.Path) -> None:
    """Delete a run's folder, even where a test took away the rights to read or delete it."""
    if not folder.exists():
        return
    # A folder a test shut (mode 0) cannot even be listed, so open every folder first;
    # the handler then covers anything that still refuses.
    for place, folders, _ in os.walk(folder):
        for name in folders:
            _open_up(pathlib.Path(place) / name)
    shutil.rmtree(folder, onexc=_writable_again)


def _not_ours(why: str) -> str:
    return (
        f"{MUTATE_TMP} {why}. Its name is easy to guess in a temporary folder everyone shares,"
        f" so this run will not use it: look at what is there, remove it if it is not yours"
        f" (rm -r '{MUTATE_TMP}'), and run again."
    )


def own_folder() -> None:
    """Make the run's folder if needed, and use it only if it is this user's and private.

    Its name is easy to guess, so somebody else could make it first, or put a link there,
    and steer what the sweep removes or where the tests write.
    """
    MUTATE_TMP.mkdir(mode=0o700, exist_ok=True)
    try:
        fd = os.open(MUTATE_TMP, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:  # a link, or something else, at the name
        raise SystemExit(_not_ours(f"is not a folder of yours ({error.strerror})")) from None
    try:
        held = os.fstat(fd)
    finally:
        os.close(fd)
    # RULE: the mutation run works only in a private folder of its own
    if not stat.S_ISDIR(held.st_mode) or held.st_uid != os.getuid() or held.st_mode & 0o077:
        raise SystemExit(_not_ours(f"is not a private folder of yours (mode {stat.S_IMODE(held.st_mode):o})"))


def sweep() -> None:
    """Remove what an earlier run, killed before it could clean up, left behind."""
    if not os.path.lexists(MUTATE_TMP):
        return
    own_folder()
    # RULE: a killed run's temporary files are swept at the next start
    for left in MUTATE_TMP.iterdir():
        _remove(left)


@contextlib.contextmanager
def checkout_held() -> Iterator[None]:
    """Hold this checkout's run lock for as long as the run lasts, or stop if another has it."""
    fd = os.open(RUNNING, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            # RULE: only one mutation run at a time in a checkout
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            msg = f"A mutation run is already running in {ROOT}; let it finish, then run again."
            raise SystemExit(msg) from None
        yield
    finally:
        os.close(fd)


def check_own_code(python: tuple[str, ...] = (sys.executable,)) -> None:
    """Refuse to measure when the tests would import the package from another checkout.

    Two checkouts sharing one environment measured each other's code, so every rule failed
    alike. It is asked in a separate process, the way the tests will import it.
    """
    found = subprocess.run(  # noqa: S603 - this interpreter, with arguments written here
        [*python, "-c", f"import {PACKAGE}; print({PACKAGE}.__file__)"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    own = (ROOT / "src").resolve()
    where = pathlib.Path(found.stdout.strip()).resolve() if found.returncode == 0 else None
    # RULE: the mutation run measures only this checkout's code
    if where is None or not where.is_relative_to(own):
        seen = where or f"nowhere ({found.stderr.strip()[-300:]})"
        msg = (
            f"The tests would import {PACKAGE} from {seen}, not from {own}. Run `uv sync` in"
            f" {ROOT}, so this checkout has its own environment, and run again."
        )
        raise SystemExit(msg)


def repair() -> pathlib.Path | None:
    """Put back a file an earlier run was killed in the middle of mutating, and say which."""
    if not RECOVERY.is_file():
        return None
    kept = json.loads(RECOVERY.read_text())
    path = ROOT / kept["file"]
    if path.read_text() == kept["mutated"]:
        path.write_text(kept["original"])
        print(f"repaired {kept['file']}, left mutated by a run that was killed", flush=True)  # noqa: T201
        RECOVERY.unlink()
        return path
    print(f"{kept['file']} was left mutated and has since changed; not touching it")  # noqa: T201
    RECOVERY.unlink()
    return None


def restore(path: pathlib.Path, original: str, mutated: str) -> bool:
    """Put a file back, unless somebody else changed it while the suite ran."""
    if path.read_text() != mutated:
        # Restoring now would overwrite an edit made while this was running,
        # which is a silent way to lose somebody's work.
        # RULE: a file changed during the run is never restored over
        return False
    path.write_text(original)
    return True


#: Colour codes pytest writes around FAILED, which a line-anchored pattern
#: would otherwise never match.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run_suite() -> tuple[int, list[str]]:
    """Run the tests and give back how many failed, and which ones."""
    # Two mutations that replace equal-length text leave files of identical
    # size, and CPython validates a cached .pyc from the source's modification
    # time and size -- both at one-second resolution. Inside the same second
    # the next run would import the previous mutation's bytecode and measure
    # the wrong rule, so no bytecode is written at all.
    # RULE: two mutations never share a bytecode cache
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    own_folder()
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="run-", dir=MUTATE_TMP))
    try:
        r = subprocess.run(  # noqa: S603 - this interpreter, with arguments written here
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=no",
                "--color=no",
                "-p",
                "no:cacheprovider",
                f"--basetemp={temporary}",
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
            env=env,
        )
    finally:
        # RULE: a suite run leaves no temporary files behind
        _remove(temporary)
        with contextlib.suppress(OSError):  # still in use by another run in this checkout
            # RULE: the mutation run's own folder is removed once it is empty
            MUTATE_TMP.rmdir()
    # --color=no covers the usual run; stripping covers a configuration that
    # turns colour back on, which would otherwise make every kill unattributable.
    # RULE: a coloured failure line is still attributed
    plain = ANSI.sub("", r.stdout)
    m = re.search(r"(\d+) (?:failed|error)", plain)
    failed = re.findall(r"^FAILED (\S+)", plain, re.MULTILINE)
    return int(m.group(1)) if m else 0, [f.split("::")[-1].split("[")[0] for f in failed]


def prepare(*, repair_only: bool = False) -> bool:
    """Undo what a killed run left, a mutated file and temporary files; say whether to go on."""
    repaired = repair()
    # RULE: a repair-only run puts back a left file and does nothing else
    if repair_only:
        return False
    if repaired is not None and repaired.resolve() == pathlib.Path(__file__).resolve():
        # This file was read before the repair, so the code running is still the mutated one.
        # RULE: a runner left mutated starts again from its repaired file
        os.execv(sys.executable, [sys.executable, str(repaired), *sys.argv[1:]])  # noqa: S606
    sweep()
    return True


def stop_cleanly() -> None:
    """Turn a polite stop into an ordinary exit, so every `with` and `finally` still runs.

    CI cancels a run, and a closed terminal ends one, with a stop signal. Python's own answer to
    it is to end at once, cleaning up nothing, which left whole folders in the temporary folder.
    """

    def stop(signum: int, _frame: object) -> None:
        # A second signal (GitHub sends an interrupt, then a stop) must not cut the cleanup short.
        for later in ("SIGTERM", "SIGHUP", "SIGINT"):
            if hasattr(signal, later):
                signal.signal(getattr(signal, later), signal.SIG_IGN)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + signum)

    # The interrupt is handled too, or the stop that follows it would end its cleanup.
    # RULE: an interrupt and then a stop still let the mutation run clean up
    handled = ("SIGINT", "SIGTERM", "SIGHUP")
    for name in handled:
        if hasattr(signal, name):  # Windows has only the interrupt
            # RULE: a stopped mutation run still cleans up
            signal.signal(getattr(signal, name), stop)


def main() -> int:
    """Mutate every rule in turn, restore the tree, and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=pathlib.Path, help="write the evidence table here")
    parser.add_argument(
        "--repair-only", action="store_true", help="put back what a killed run left mutated, and stop"
    )
    args = parser.parse_args()

    stop_cleanly()
    with checkout_held():
        return measure_all(args.write) if prepare(repair_only=args.repair_only) else 0


def measure_all(write: pathlib.Path | None) -> int:
    """Switch every rule off in turn, run the tests each time, restore the tree, and report."""
    # RULE: an empty rule table is not a crash
    # A scaffolded repository starts with a folder that holds no rows.
    rules = all_rows()
    if not rules:
        print(f"no rows in {ROWS.relative_to(ROOT)}/ yet; add one per rule the code enforces")  # noqa: T201
        return 0
    check_own_code()
    results = []
    lost: list[str] = []
    standing: tuple[pathlib.Path, str, str] | None = None
    try:
        for rule in rules:
            path = ROOT / rule["file"]
            # Read each file as it is now, not as it was when the run began: a
            # bulk snapshot taken up front is restored over anything edited
            # since, and losing an edit that way costs more than the run.
            text = path.read_text()
            lines = text.splitlines()
            i = line_of(text, rule["find"], rule.get("occurrence", 1))
            # Keep the line's own indentation: a replacement that carries its own
            # would have to be kept in step with the source, and silently would not be.
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = indent + rule["replace"].strip()
            mutated = "\n".join(lines) + "\n"
            RECOVERY.write_text(json.dumps({"file": rule["file"], "original": text, "mutated": mutated}))
            path.write_text(mutated)
            standing = (path, text, mutated)
            count, failed = run_suite()
            if not restore(path, text, mutated):
                lost.append(rule["file"])
            standing = None
            RECOVERY.unlink(missing_ok=True)
            # A kill by some other test is not evidence about this rule.
            own = test_name_for(rule["name"])
            by_its_own = own in failed
            results.append((rule["name"], count, own, by_its_own))
            verdict = "KILLED  " if by_its_own else ("BY LUCK " if count else "SURVIVED")
            print(f"{verdict} {rule['name']}: {count}")  # noqa: T201
    finally:
        if standing is not None:
            restore(*standing)
        RECOVERY.unlink(missing_ok=True)

    survived = [name for name, count, _, _ in results if count == 0]
    lucky = [name for name, count, _, own in results if count and not own]
    proven = len(results) - len(survived) - len(lucky)
    print(f"\n{proven}/{len(results)} rules turned off the test that names them")  # noqa: T201
    if survived:
        print("SURVIVED, so nothing protects them:", ", ".join(survived))  # noqa: T201
    if lucky:
        print("KILLED BY ANOTHER TEST, so the evidence is not about them:", ", ".join(lucky))  # noqa: T201
    if lost:
        print("CHANGED WHILE MUTATED, so left as they are:", ", ".join(sorted(set(lost))))  # noqa: T201
        print("Edit the tree while this runs and the result is not about your code.")  # noqa: T201
    if write:
        write.write_text(report(results, survived, lucky))
    return 1 if survived or lucky or lost else 0


def report(results: list[tuple[str, int, str, bool]], survived: list[str], lucky: list[str]) -> str:
    """Render the run as the evidence page."""
    rows = "\n".join(
        f"| {name} | {count} | `{own}` | {'yes' if by_own else 'NO'} |"
        for name, count, own, by_own in results
    )
    proven = len(results) - len(survived) - len(lucky)
    verdict = (
        f"**Result: {proven} of {len(results)} rules turned off the test that names them.**"
        + (" None survived." if not survived else f" Survived: {', '.join(survived)}.")
        + ("" if not lucky else f" Killed only by other tests: {', '.join(lucky)}.")
    )
    return f"""# Every rule, turned off in turn

*Run {dt.datetime.now(dt.UTC).date()} by `python scripts/mutate.py --write
evidence/tests/rule-mutations.md`, which is how to repeat it. The script
restores every file it touched before it exits.*

Green on a first run proves nothing: a test that has never been red tests
nothing. Where a rule is written before its test, the test is run red first.
Where a rule already exists, this is the substitute. Each rule is disabled in
turn, by replacing its one line with something that can never hold, and the
suite is run. A rule whose removal breaks no test is a rule nothing protects.

{verdict}

| Rule turned off | Tests that went red | The test that names it | Did that one go red? |
|---|---|---|---|
{rows}

## What this does not prove

That the rules are the right rules, or that a rule catches every case of what
it names. It proves each rule is load-bearing: switch it off and a named test
says so. The last column is why that is worth more than a count: a mutation can
break a test for an incidental reason, and a `NO` there fails this script even
though something went red. A new rule follows the order in `CONTRIBUTING.md`
instead, a failing test first, and gets a row in a file of its own under
`scripts/mutations/`, which `tests/test_rules_have_evidence.py` requires.
"""


if __name__ == "__main__":
    sys.exit(main())

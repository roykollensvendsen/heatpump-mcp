"""Every rule this project enforces has evidence, and the evidence names it.

A rule here is one line of source marked ``# RULE: what it says``. Three things
have to be true of each, and none of them is true by writing the rule:

* it has a row, in a file of its own under ``scripts/mutations/``, so
  something switches it off;
* a test carries the rule's own words as its name;
* switching it off turns *that* test red, which ``scripts/mutate.py`` checks.

The second is what makes the third readable. A mutation breaks whatever it
breaks, and a kill only means something when the test that went red is the one
that names the rule — otherwise a documented example that happened to change can
leave a rule looking guarded while nothing guards it.
"""

import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
MARKER = re.compile(r"^[ \t]*#\s*RULE:\s*(.+?)\s*$")


def _mutate():
    """The mutation runner, loaded by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("mutate", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def marked_rules() -> set[str]:
    """Every rule the source marks, by the words the marker gives it."""
    found = set()
    # The checks this project added count too, and so do the mutation run and the
    # test setup that clean up after a run; the other scripts the start-repo skill
    # ships carry rules that skill proves.
    scripts = [
        ROOT / "scripts" / n
        for n in ("private_data_scan.py", "ratchet.py", "mutate.py", "no_session_links.py", "red_first.py")
    ]
    scripts.append(ROOT / "tests" / "conftest.py")
    for source in sorted([*(ROOT / "src").rglob("*.py"), *scripts]):
        for line in source.read_text().splitlines():
            match = MARKER.search(line)
            if match:
                found.add(match.group(1))
    return found


def rowed_rules() -> set[str]:
    """Every rule the mutation rows claim to switch off."""
    return {str(rule["name"]) for rule in _mutate().all_rows()}


def defined_test_names() -> set[str]:
    """Every test function this suite defines.

    Not called ``test_names``: pytest collects anything starting with ``test_``,
    and a helper that returns a set would be reported as a test that forgot to
    assert.
    """
    found = set()
    for module in sorted((ROOT / "tests").rglob("test_*.py")):
        found.update(re.findall(r"^(?:async )?def (test_\w+)", module.read_text(), re.MULTILINE))
    return found


def test_every_rule_in_the_source_has_a_row_in_the_mutation_table():
    """Without this the evidence falls behind the code and nobody sees it happen."""
    marked, rowed = marked_rules(), rowed_rules()
    assert marked - rowed == set(), f"marked in the source and never switched off: {marked - rowed}"
    assert rowed - marked == set(), f"a row for a rule no longer in the source: {rowed - marked}"


def test_every_rule_has_a_test_named_after_it():
    """A kill is only evidence about a rule when the test that went red names it."""
    name_for = _mutate().test_name_for
    named = defined_test_names()
    missing = sorted(rule for rule in marked_rules() if name_for(rule) not in named)
    wanted = ", ".join(f"{name_for(rule)}()" for rule in missing)
    assert not missing, f"rules with no test named after them — write {wanted}"


def _row(name, file="src/heatpump_mcp/service.py"):
    return f"[[rule]]\nname = \"{name}\"\nfile = \"{file}\"\nfind = 'x'\nreplace = 'y'\n"


def test_a_row_file_sits_in_its_source_s_folder_under_its_own_name(tmp_path):
    """One row per file, named after the rule, in the folder of the source it switches off.

    Then where a row lives is never a choice, and two pull requests only meet
    when they add the same rule.
    """
    folder = tmp_path / "mutations"
    service, fglair = folder / "src/heatpump_mcp/service.py", folder / "src/heatpump_mcp/fglair.py"
    service.mkdir(parents=True)
    fglair.mkdir(parents=True)
    (service / "in-place.toml").write_text(_row("in place"))
    (fglair / "wrong-folder.toml").write_text(_row("wrong folder"))
    (service / "a-wrong-name.toml").write_text(_row("named otherwise"))
    (service / "two-rows.toml").write_text(_row("two rows") + _row("in one file"))
    found = " ".join(_mutate().misfiled(folder))
    there = "src/heatpump_mcp/service.py"
    assert f"src/heatpump_mcp/fglair.py/wrong-folder.toml should be {there}/wrong-folder.toml" in found
    assert f"{there}/a-wrong-name.toml should be {there}/named-otherwise.toml" in found
    assert f"{there}/two-rows.toml holds 2 rows" in found
    assert "in-place" not in found


def test_every_row_file_in_the_repository_is_where_its_rule_says():
    misfiled = _mutate().misfiled()
    assert not misfiled, "row files out of place: " + "; ".join(misfiled)


def test_the_same_rule_in_two_row_files_is_refused_naming_both(tmp_path):
    """A row moved and its old copy left behind would switch the rule off twice, and count it twice."""
    folder = tmp_path / "mutations"
    for source in ("service.py", "fglair.py"):
        (folder / "src/heatpump_mcp" / source).mkdir(parents=True)
        (folder / "src/heatpump_mcp" / source / "twice.toml").write_text(_row("twice"))
    with pytest.raises(SystemExit) as refused:
        _mutate().all_rows(folder)
    said = str(refused.value)
    assert "src/heatpump_mcp/fglair.py/twice.toml" in said
    assert "src/heatpump_mcp/service.py/twice.toml" in said
    assert "delete" in said


def test_a_rule_whose_words_give_no_file_name_is_refused():
    """A name with no letter or digit would be filed as ".toml", and the next one on top of it."""
    with pytest.raises(SystemExit) as refused:
        _mutate().row_path({"name": "?!", "file": "src/heatpump_mcp/service.py"})
    assert "?!" in str(refused.value)
    assert "letter" in str(refused.value)


def test_a_row_s_folder_is_the_whole_path_of_the_file_it_switches_off():
    """The same layout in every repository, whatever the language.

    A shorter folder name would put src/app.ts and src/app/ together, and x.h
    and x.c together.
    """
    row_path = _mutate().row_path
    words = {"name": "a change needs a yes"}
    assert str(row_path({**words, "file": "src/heatpump_mcp/service.py"})) == (
        "src/heatpump_mcp/service.py/a-change-needs-a-yes.toml"
    )
    assert str(row_path({**words, "file": "src/app.ts"})) == "src/app.ts/a-change-needs-a-yes.toml"
    assert row_path({**words, "file": "firmware/unit.h"}) != row_path({**words, "file": "firmware/unit.c"})


def test_the_shared_table_is_gone():
    """Rows added to the end of one shared table collided with every other pull request."""
    assert not (ROOT / "scripts/mutations.toml").exists()

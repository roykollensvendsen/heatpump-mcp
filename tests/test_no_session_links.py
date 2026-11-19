"""No commit message names a Claude session, and its only trailers are credit."""

import pathlib
import subprocess
import sys

import pytest
from bounded import SECONDS

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import no_session_links

#: Built from parts, so this file's own text never holds what the check refuses.
SESSION_LINE = "Claude-" + "Session: https://claude.ai/code/" + "session_01Ab"


@pytest.mark.parametrize(
    "message",
    [
        f"feat: Add a thing\n\nBody.\n\n{SESSION_LINE}\n",
        f"fix: Mend it\n\n{SESSION_LINE.lower()}\n",  # however it is written
        "docs: Note\n\nSee https://claude.ai/code/" + "session_01AbCd for how it went.\n",
    ],
)
def test_a_commit_message_never_links_to_a_claude_session(message):
    assert no_session_links.offence(message)


@pytest.mark.parametrize(
    "message",
    [
        "feat: Add a thing\n\nCo-Authored-By: Some Helper <helper@example.invalid>\n",
        "feat: Add a thing\n\nBody.\n\nSigned-off-by: A Contributor <them@example.invalid>\n",
        "feat: Add a thing\n\nco-authored-by: A <a@example.invalid>\nSigned-off-by: B <b@example.invalid>\n",
        "docs: Explain the claude.ai connector\n\nThe app reaches the bridge through claude.ai.\n",
        "fix: Mend it\n\nThe value was: wrong, and the line above says so in prose.\n",
    ],
    ids=["co-authored-by", "signed-off-by", "both, any case", "a plain mention", "prose with a colon"],
)
def test_credit_and_ordinary_text_pass(message):
    assert no_session_links.offence(message) is None


@pytest.mark.parametrize(
    "trailer",
    ["Reviewed-by: Someone <s@example.invalid>", "Ratchet-Exception: coverage - why", "Fixes: #12"],
)
def test_a_commit_carries_no_trailer_but_co_authored_by_and_signed_off_by(trailer):
    message = f"feat: Add a thing\n\nBody.\n\nCo-Authored-By: A <a@example.invalid>\n{trailer}\n"
    said = no_session_links.offence(message)
    assert said
    assert trailer.split(":")[0] in said
    assert "pull request" in said


def test_the_subject_alone_is_never_read_as_a_trailer():
    assert no_session_links.trailers("Fixes: the subject line\n") == []


def git(folder, *args):
    return subprocess.run(
        ["git", "-C", str(folder), *args], check=True, capture_output=True, text=True, timeout=SECONDS
    ).stdout


def test_a_range_names_every_commit_refused_and_why(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    for message in ("chore: Start", f"feat: One\n\n{SESSION_LINE}", "fix: Two\n\nFixes: #3", "fix: Three"):
        git(
            tmp_path,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
        )
    found = no_session_links.offenders("HEAD~3..HEAD", cwd=str(tmp_path))
    assert [subject for _, subject, _ in found] == ["fix: Two", "feat: One"]
    assert "Fixes" in found[0][2]


def test_the_hook_form_reads_a_message_file_and_drops_comments(tmp_path, monkeypatch, capsys):
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text("feat: Add a thing\n\n# Reviewed-by: a comment git drops\n")
    monkeypatch.setattr(sys, "argv", ["no_session_links.py", "--file", str(path)])
    assert no_session_links.main() == 0
    path.write_text("feat: Add a thing\n\nReviewed-by: Someone <s@example.invalid>\n")
    assert no_session_links.main() == 1
    assert "Reviewed-by" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("ending", "refused"),
    [
        ("Reviewed-by: Someone <s@example.invalid>\n(cherry picked from commit 0123abc)", True),
        ("Reviewed-by: Someone\n  <s@example.invalid>, folded onto a second line", True),
        ("Co-Authored-By: A <a@example.invalid>\n(cherry picked from commit 0123abc)", False),
    ],
    ids=["after a cherry-pick note", "folded", "credit and a cherry-pick note"],
)
def test_a_trailer_block_is_read_as_git_reads_it(ending, refused):
    """Git keeps a cherry-pick note and a folded line inside the trailer block, and so does this."""
    said = no_session_links.offence(f"fix: Mend it\n\nBody.\n\n{ending}\n")
    assert bool(said) is refused


@pytest.mark.parametrize(
    "ending",
    [
        "Signed-off-by: A <a@example.invalid>\nReviewed-by: B <b@example.invalid>\nand a line of free text",
        "Co-Authored-By: A <a@example.invalid>\nNote: left for the reviewer\nand a line of free text",
        "Reviewed-by: B <b@example.invalid>\nand a line of free text\n(cherry picked from commit 0123abc)",
    ],
    ids=["signed-off-by, then free text", "co-authored-by, then free text", "a cherry-pick note"],
)
def test_a_last_paragraph_with_credit_in_it_is_judged_line_by_line(ending):
    """Git reads such a paragraph as a trailer block though one line is free text, so every key is judged."""
    said = no_session_links.offence(f"fix: Mend it\n\nBody.\n\n{ending}\n")
    assert said
    assert ("Reviewed-by" in said) or ("Note" in said)


def test_a_lone_trailer_shaped_line_at_the_end_stays_refused():
    """Existing behaviour, kept on purpose: a last paragraph that is all trailers is a trailer block."""
    assert "Note" in no_session_links.offence("fix: Mend it\n\nBody.\n\nNote: left for the reviewer\n")


def test_the_hook_form_drops_comments_by_the_clone_s_own_comment_character(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=SECONDS)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "core.commentChar", ";"], check=True, timeout=SECONDS
    )
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(
        "feat: Add a thing\n\n; See https://claude.ai/code/" + "session_01Ab, a comment git drops\n"
    )
    monkeypatch.setattr(sys, "argv", ["no_session_links.py", "--file", str(path)])
    assert no_session_links.main() == 0


def test_a_hung_git_config_is_read_as_auto_and_said(monkeypatch, capsys):
    """Refusing is the safe side, and a traceback would not say why."""

    def hangs(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

    monkeypatch.setattr(no_session_links.subprocess, "run", hangs)
    assert no_session_links.comment_character() is None
    assert "git config" in capsys.readouterr().err


def test_with_a_comment_character_git_picks_itself_the_comment_lines_are_judged_too(tmp_path, monkeypatch):
    """Under `auto` git may keep the `#` lines, so the hook can't drop them unread."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=SECONDS)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "core.commentChar", "auto"], check=True, timeout=SECONDS
    )
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "COMMIT_EDITMSG"
    monkeypatch.setattr(sys, "argv", ["no_session_links.py", "--file", str(path)])
    path.write_text(f"feat: Add a thing\n\n# {SESSION_LINE}\n")
    assert no_session_links.main() == 1
    path.write_text("feat: Add a thing\n\nBody.\n\n# Please enter the commit message for your changes.\n#\n")
    assert no_session_links.main() == 0

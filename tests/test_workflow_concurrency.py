"""A newer push to a pull request stops that pull request's stale run; every other run ends by itself.

GitHub keeps only one waiting run per concurrency group. So the group is the pull request's ref
only for a pull request, and the run's own number for anything else, such as a push to main, a
tag or a run by hand. Otherwise a commit on main that lands between two others would never run.
"""

import pathlib

import pytest

WORKFLOWS = pathlib.Path(__file__).parent.parent / ".github" / "workflows"

#: The block, as every workflow a pull request starts must carry it.
BLOCK = (
    "concurrency:\n"
    "  group: ${{ github.workflow }}-"
    "${{ github.event_name == 'pull_request' && github.ref || github.run_id }}\n"
    "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
)


def started_by_a_pull_request(text: str) -> bool:
    """Whether a workflow's trigger names pull_request, outside a comment."""
    return any(line.split("#")[0].strip().startswith("pull_request") for line in text.splitlines())


STARTED_BY_A_PULL_REQUEST = sorted(
    w for w in WORKFLOWS.glob("*.yml") if started_by_a_pull_request(w.read_text())
)


@pytest.mark.parametrize("workflow", STARTED_BY_A_PULL_REQUEST, ids=lambda w: w.name)
def test_a_newer_push_stops_only_a_pull_request_s_stale_run(workflow):
    assert [w.name for w in STARTED_BY_A_PULL_REQUEST] == ["checks.yml", "red-first.yml"]
    assert BLOCK in workflow.read_text()

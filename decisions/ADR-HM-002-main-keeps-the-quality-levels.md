# ADR-HM-002: Main keeps the quality levels, so a pull request never records them

## Status

Accepted 2026-11-11: the owner accepted rule 16 of the voice plugins' shared
standard (claude-voice docs/plugin-standard.md) on 2026-11-10, and this follows
it. It takes over claude-voice's
[ADR-CV-009](https://github.com/roykollensvendsen/claude-voice/blob/main/decisions/ADR-CV-009-main-keeps-the-quality-levels.md)
and tv-mcp's
[ADR-TM-002](https://github.com/roykollensvendsen/tv-mcp/blob/main/decisions/ADR-TM-002-main-keeps-the-quality-levels.md),
and adapts them to this repository.

## Context

Until now nothing here noticed the code slowly getting worse. The gates pass or
fail: a suite that loses tests, coverage that drifts down, a function that grows
more tangled with each change all stay green. claude-voice, the project this
server plugs into, has a quality ratchet that refuses exactly that: a change may
leave each level as it is or make it better, and makes one worse only when its
pull request says why. Its code-health page reads each plugin's levels from the
`quality-levels` result that plugin's main keeps.

claude-voice first kept the levels in a file that every pull request had to
update. That made the file a queue: when one pull request merged, every other
open one held stale numbers and had to be measured again. ADR-CV-009 is how it
got out of that, and tv-mcp took the same way the same day.

What holds here:

- The useful part of the check is that nothing gets worse than main.
- `main` is protected: every change arrives by pull request, rebased, with every
  check green.
- A pull request's check runs on the pull request merged onto main, so the first
  parent of what it checks is exactly the main it would land on.
- Measuring is cheap here: the suite takes seconds, and the mutation check well
  under a minute.

## Options considered

- **Do nothing.** The gates stay pass-or-fail and slow decay goes unseen, and
  the code-health page has nothing to show for this plugin. Not taken.
- **Keep the levels in a file each pull request updates.** Every merge breaks
  every other open pull request. Not taken.
- **Measure main and the pull request in every check.** Nothing stored, but
  every check takes twice as long. Not taken, except as the fallback below.
- **Main keeps its measured levels with each of its runs; a pull request
  compares with them.** **Chosen.**

## Decision

After every push to main, continuous integration measures the levels and keeps
them with that run as `quality-levels`. A pull request's check takes the levels
main measured for the commit it is merged onto, and fails only when a level is
worse, unless a `Ratchet-Exception:` line in the pull request names it. When
those levels are missing, the check measures that commit itself in a copy of
it: slower, and never skipped. `quality.json` keeps only the suite's time
budget, a limit set by hand. `quality.toml` holds the code-health page's
exemptions, and starts empty.

## Consequences

- Merging one pull request never breaks another, and a contributor never runs
  the ratchet to fix a number.
- **Worse:** the levels do not show in a pull request's diff. They are in the
  check's output and in main's runs.
- **Worse:** the check depends on GitHub keeping main's run results, 90 days by
  default. Past that it measures the base, which doubles the job's time.
- **Worse:** a pull request whose check ran before main moved on is not checked
  again until it changes.
- The layers among the modules are counted for the page, but
  `tests/test_layers.py` already refuses any breach outright, so the level can
  only be zero.

## Related

claude-voice's ADR-CV-009, tv-mcp's ADR-TM-002, `scripts/ratchet.py`,
`quality.json`, `quality.toml`, the `quality` job in
`.github/workflows/checks.yml`, and "The quality ratchet" in
[`CONTRIBUTING.md`](../CONTRIBUTING.md#the-quality-ratchet).

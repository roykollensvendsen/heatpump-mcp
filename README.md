# heatpump-mcp

Voice control for a Fujitsu heat pump through the FGLair cloud, as an MCP
server and a [claude-voice](https://github.com/roykollensvendsen/claude-voice)
plugin.

You ask "how warm is it in the living room?", and the voice reads the answer
from the heat pump: whether it is on, what it is doing, the temperature it is
set to, the room and outdoor temperatures, and the fan.

## Try it without a heat pump

The pretend unit touches no network, and every answer says it is simulated:

```console
$ uv run heatpump-mcp status --simulate
Simulated: The heat pump is on, heating to 21 degrees. The room is 20.5 degrees, and it is 3 degrees outside. The fan is on auto.
```

The same tools are served to a voice session over stdio, and the server stops
when its input closes:

```console
$ uv run heatpump-mcp serve --simulate < /dev/null
```

## The real heat pump

The real unit is reached through Fujitsu's FGLair cloud, the same way Home
Assistant's built-in FGLair integration reaches it
([ADR-HM-001](decisions/ADR-HM-001-reach-the-unit-through-fglair-with-ayla-iot-unofficial.md)).
It needs the login of the FGLair app, in two files that only the owner can
read:

<!-- not run: it writes the owner's real login -->
```bash
mkdir -p ~/.config/heatpump-mcp && cd ~/.config/heatpump-mcp
printf '%s' 'the-email-you-sign-in-with' > fglair-username
printf '%s' 'your-fglair-password' > fglair-password
chmod 600 fglair-username fglair-password
```

An optional `config.toml` beside them holds `region` (`eu`, the default;
`default` for the Americas and elsewhere; or `cn`) and `unit`, the unit's
name in the FGLair app, which is needed only when the account has more than
one. A file anyone else can read is refused.

Until the login is there, the server still starts, and every answer says
what to set up. Every refusal and failure says what is wrong and what to do
next, with the exact command where there is one:

```console
$ uv run heatpump-mcp status --config-dir /nonexistent
The heat pump's login is not set up: /nonexistent/fglair-username is missing. Ask the claude-voice manager to open the FGLair login boxes, and enter the login there.
```

With the login in place, `uv run heatpump-mcp serve` serves the real unit,
and `uv run heatpump-mcp status` reads it once.

## The tools

| Tool | What it does |
| --- | --- |
| `heatpump_status` | Reads the unit, changing nothing. A reading is reused for 30 seconds. |
| `heatpump_set_request` | Checks one change (power, mode, temperature or fan) and returns a question to read aloud, with a token. Changes nothing. |
| `heatpump_set_confirm` | Sends that change, with the token, after the owner's spoken yes, then reads the unit back. |

Every answer has `say`, a sentence to read aloud as it is, and `success`,
which a program can read. After a change, `success` is true only when the
unit, read back, shows it; otherwise `say` tells the owner it is not
confirmed.

## The safety rules

- Nothing changes without a question and a spoken yes. A token works for
  the most recent question only, once, for 30 seconds; a wrong one cancels
  the question.
- Temperatures from 16 to 28 °C only; anything else is refused.
- At most one change every 10 seconds.
- Only power, mode, temperature and fan. Nothing in the code touches the
  unit's firmware, Wi-Fi, schedules, timers or the cloud account, and a test
  refuses any code that so much as names them.
- A change that never reached the unit can be confirmed again with the same
  token while it is fresh; nothing is retried on the plugin's own initiative.

## The gates

Every one of these runs in continuous integration, and all of them run here:

```console
$ uv run ruff check .
All checks passed!
```

<!-- not run: the workflow runs each of these; nesting them here would run the suite inside itself -->
```bash
uv run ruff format --check .   # layout
uv run mypy src                # types
uv run pytest -q               # behaviour
python scripts/mutate.py       # that each rule's own test can fail
```

`CONTRIBUTING.md` says what each one is for, and
[`decisions/`](decisions/README.md) records the choices that shaped them,
including how a change is made here.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).

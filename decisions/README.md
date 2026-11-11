# Decision records

A decision that costs more to reverse than to take is written down here before
the code that rests on it. A decision that only implements one already taken
is not; it is a commit message.

## The form

Every record is `ADR-HM-NNN-a-short-title.md`, with these sections in
this order:

| Section | Holds |
|---|---|
| Status | Accepted or Proposed, who decided, when; an objection returns it to Proposed |
| Context | what was true when the question arose, with sources where the facts are checkable |
| Options considered | every option weighed, including doing nothing, each with why it was not taken |
| Decision | what was chosen, in one paragraph |
| Consequences | what this costs, what it forecloses, and at least one thing that gets worse |
| Related | the pages it shapes |

Numbers are one sequence and never reused. A record is superseded by a later
one, never edited into a different decision.

Record the process choices too, not only the artefact ones. How a change is
made, and why, is what costs time on every future contribution, and it is the
decision nobody writes down.

## The records

| Id | Decides | Status |
|---|---|---|
| [ADR-HM-001](ADR-HM-001-reach-the-unit-through-fglair-with-ayla-iot-unofficial.md) | Reach the unit through the FGLair cloud with ayla-iot-unofficial, as Home Assistant does | Accepted |
| [ADR-HM-002](ADR-HM-002-main-keeps-the-quality-levels.md) | Main keeps the quality levels with its own runs, so a pull request never records them | Accepted |
| [ADR-HM-003](ADR-HM-003-no-night-or-presence-rule-for-the-heat-pump.md) | The heat pump has no night or presence rule; every change still needs the spoken yes | Accepted |

What is deliberately left undone, and what would make each worth doing, is
[`deferred.md`](deferred.md). A thing left undone with no trigger is a thing
forgotten.

# ADR-HM-003: The heat pump has no night or presence rule

## Status

Proposed 2026-11-11, for the owner to accept or change. The write-plugin
checklist (item 8) asks each plugin to decide its night and presence rules when
it is designed, and to write the decision down, even when the answer is none.

## Context

A night rule keeps a device from doing something unwelcome at night, such as
switching a screen on. A presence rule keeps it from acting when nobody is home,
such as opening a door. What holds for this heat pump:

- Nothing changes without a question read aloud and the owner's spoken yes in
  the same conversation, and only one change every 10 seconds.
- A change is to power, mode, a temperature from 16 to 28 °C, or the fan. None
  of these opens anything, makes much noise, or can harm the house or the unit.
  The worst case is a room a few degrees warmer or cooler, which the next
  request undoes.
- Changing it from away is the point: warming the house on the drive home is
  exactly what the voice is for, often while driving.

## Decision

No night rule and no presence rule. Every change at any hour and from anywhere
still needs the spoken yes. The limits above hold at all times.

## Consequences

- Nothing in the code checks the time of day or whether anyone is home, and no
  test needs to.
- If a mode should ever be refused at night, the quiet fan forced at night for
  example, it becomes a rule here, with a `# RULE:` comment, a test and a mutation
  row, and this record is replaced.

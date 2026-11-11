# ADR-HM-001: Reach the unit through the FGLair cloud with ayla-iot-unofficial

## Status

Accepted, 2026-11-11, by the owner, in the design the manager session passed
on; the local option below was put to the manager the same day and it agreed
to defer it.

## Context

The heat pump is a Fujitsu wall unit with an FGLair Wi-Fi module. The module
talks to Fujitsu's FGLair cloud, which runs on the Ayla Networks IoT platform.
The question was how this plugin should reach it, and whether it could do so
without the cloud. Everything below was read, not tried: no device or network
was probed.

- Home Assistant has a built-in integration, `fujitsu_fglair`. Its manifest
  says `iot_class: cloud_polling` and pins `ayla-iot-unofficial==1.5.2`
  ([manifest](https://github.com/home-assistant/core/blob/dev/homeassistant/components/fujitsu_fglair/manifest.json)).
- [ayla-iot-unofficial](https://github.com/rewardone/ayla-iot-unofficial)
  1.5.2 (MIT) talks to Ayla's cloud servers only. It reads the unit's LAN
  address but never uses it.
- Ayla modules do have a "LAN mode", and community projects use it on FGLair
  modules (for example `Dr-Blank/fujitsu-lan-ha` and `deiger/AirCon`). It
  still needs the cloud login once to fetch a key for each unit, and the unit
  needs a fixed address. The unit then opens a plain HTTP connection back to
  the controller. Only one controller can use it at a time. It conflicts with
  polling the cloud, and the unit sends no confirmation after a change.

## What was reused from Home Assistant, and why

Home Assistant's integration has been used with this library on many units,
so following its calls closely is the cheapest way to avoid surprises.

- **The library and its version**, pinned exactly as Home Assistant pins it.
  A version Home Assistant ships has been run on many units.
- **Signing in**: `new_ayla_api(username, password, app_id, app_secret,
  europe=...)`, with the app id and secret taken from the library's own
  `FGLAIR_APP_CREDENTIALS` for the chosen region (`eu`, `default` or `cn`).
  They are not copied into this repository.
- **Keeping the login fresh** as the coordinator does: sign in again when the
  token has expired, and refresh it when it expires soon. This happens before
  each call.
- **Finding the unit**: `async_get_devices()`, keeping only `FujitsuHVAC`
  devices that are online, then `async_update()` on the one in use.
- **Reading** the same properties the climate entity and sensor read:
  `op_mode`, `set_temp`, `sensed_temp`, `outdoor_temperature`, `fan_speed`.
- **Writing** with only the setters the climate entity uses for these four
  settings: `async_set_op_mode` (which also switches the unit on and off,
  because `OpMode.OFF` and `OpMode.ON` are modes), `async_set_set_temp` and
  `async_set_fan_speed`.

What was **not** reused:

- **Swing.** It is not in the owner's design.
- **The five-minute polling.** This plugin reads only when asked, and keeps a
  reading for 30 seconds.
- **Home Assistant's own error flow.** A refused login becomes a sentence the
  voice reads aloud, and the owner enters the login again.

One side effect is kept knowingly. The library's `async_update` writes the
unit's `refresh` property to ask for a fresh room temperature, as it does for
Home Assistant. It is a request to measure, not a setting.

## Options considered

- **Ayla LAN mode, without the cloud.** Not taken now. It needs the cloud
  once anyway, and it needs a fixed address and a connection back from the
  unit. It also conflicts with the cloud. And it gives no confirmation after a
  change, which is the read-back this plugin promises. It is on the deferred
  list, with its trigger.
- **Write the Ayla calls ourselves with httpx.** Not taken. The sign-in,
  token refresh, property names and temperature scaling are already in a
  library Home Assistant keeps working.
- **Go through Home Assistant.** Not taken. It would add a home server to
  run, and claude-voice already takes plugins.
- **Replace the module with a board on the unit's serial port.** Not taken.
  It means opening the unit and changing its hardware.

## Decision

Reach the unit through the FGLair cloud with `ayla-iot-unofficial==1.5.2`.
Use it the way Home Assistant's `fujitsu_fglair` integration does, through
the four setters named above and no others. Keep it behind the package's own
`Unit` interface, so that another way in can replace it without touching the
rules.

## Consequences

- The heat pump can be read and changed only while Fujitsu's cloud and the
  house's internet connection both work. That is worse than a local
  connection.
- The library is one maintainer's work and has no type information. It is
  isolated in one module, and mypy is told not to check its insides.
- A change in Fujitsu's or Ayla's cloud can break this plugin until the
  library is fixed. When that happens, Home Assistant's users see it first,
  and a fixed version arrives there first.
- The library can write any property. This plugin calls only the four setters,
  and a test refuses code that names firmware, Wi-Fi, schedules, timers or the
  account.

## Related

[`deferred.md`](deferred.md), `src/heatpump_mcp/fglair.py`.

# Device safety: what can harm the machine, and the four layers

Verified against the GaggiMate firmware source.

**The prototype writes nothing to the device at all.** Not a profile, not a
setting, not a mode change. That is a property of the code, not a convention:
`GaggimateClient`'s entire public surface is ten reads listed in
`READ_ONLY_METHODS`, `_send` is private, and `tests/device/test_public_surface.py`
fails the build if an eleventh method appears or if a write request type shows
up anywhere in the module — including in a docstring. Even the simulator
end-to-end test, which genuinely needs the machine to brew, opens a throwaway
socket of its own rather than widening that surface.

This page describes what the four layers *will* be when writing to the machine
is built, and why the bar is where it is.

## What can actually go wrong

**Shot data cannot be damaged.** `req:history:delete` is the only history write,
and the firmware performs the same deletion itself under storage pressure.
Deleting archived shots is safe.

**Profiles can wedge a machine.** They are JSON files the display re-reads at
boot and on every list. Known failure modes, from the firmware's own parser:

* a profile with **zero phases** crashes brew start;
* `pump: 100.0` — a float — is parsed as an object with zero targets, leaving a
  profile that never runs the pump;
* unknown target types are silently dropped;
* temperatures up to 150 °C and phase durations up to 300 s are accepted;
* an `operator` other than `gte` parses as `lte`, silently inverting a stop
  condition.

A wedged display is recoverable by reflash plus filesystem erase, and
gaggiclanker makes that cheap because it holds every profile — but the goal is
never to need it.

**`POST /api/settings` is the genuinely dangerous endpoint.** It clears every
boolean key absent from its body and it can change WiFi and PID. gaggiclanker
does not write device settings, and there is no plan for it to.

**The machine is small.** About 300 KB of heap and three WebSocket clients
total. Bounded concurrency on fetches — at most two in flight — and exactly one
socket.

## The four layers

Applied in order to anything bound for the device, and the first of them applies
to everything entering the database too.

**1. Schema validity — pydantic, strict.**
A `Profile` model mirroring the firmware's own `schema/profile.json`:
`extra="forbid"` except underscore-prefixed annotation keys and the undocumented
`transition.target`; `pump` an `int` percentage or a `{target, pressure, flow}`
object; `phases` non-empty; `operator` restricted to `gte`/`lte`; target `type`
restricted to `volumetric|pressure|flow|pumped`; ids matching `^[A-Za-z0-9_-]+$`
and at most 31 characters. Equivalent models exist for shot notes, the `.slog`
header and samples, and the index entry.

The same models validate *inbound* data, which is where the layer earns its keep
today: a shot that fails parsing or validation is stored as a raw blob with
`quarantined = 1` and never becomes sample rows. Nothing is lost and nothing bad
is queryable.

**2. A safety policy narrower than the firmware's.**
Kept in a settings table so it can be tuned: temperature 60–100 °C, pressure
0–12 bar, flow 0–10 ml/s, phase duration 0.5–120 s, at most 10 phases,
transition duration no longer than its phase, every profile ending in a
volumetric or pumped stop or a bounded duration, and an explicit acknowledgement
in the UI when a draft changes a stop condition. Anything an LLM drafts is
clamped to these bounds and then re-validated.

**3. Round-trip verification.**
After `req:profiles:save`, load the returned id back and compare canonical JSON
— numbers normalised, firmware-added fields tolerated. A mismatch marks the push
`failed`, records both documents, and offers a one-click delete of the device's
copy. "The machine says it saved it" is not the same as "the machine stored what
we sent".

**4. A simulator gate in CI.**
Every profile fixture and every generated draft is saved to the firmware's
`display-sim` and brewed to completion. The simulator runs the same parser and
the same brew code as the device, so what it accepts, the device accepts.
`scripts/sim.sh` is what builds and runs it.

## Why the layers are ordered this way

Each one catches a class the next cannot see. A schema cannot tell that 140 °C
is valid JSON and a ruined shot. A policy cannot tell that the firmware silently
dropped a target type it did not recognise. A round trip cannot tell that the
profile it faithfully stored will crash on brew start. Only the simulator can,
and only the first three are cheap enough to run on every request.

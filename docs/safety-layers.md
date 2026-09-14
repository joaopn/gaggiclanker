# Device safety: what can harm the machine, and the four layers

Verified against the GaggiMate firmware source.

**gaggiclanker writes exactly seven things to a machine, and only when a person
has switched writes on.** Five are profile operations — save, delete, select,
favourite, unfavourite — each a `req:profiles:*` frame. Two are history
operations added later: `req:history:delete` (deleting a shot the
archive already holds) and `req:history:notes:save` (a judgement
sent to the machine's notes card). Not a setting, not a mode change, not
an index rebuild.

**Who may start a write is a rule of its own: profiles may be pushed by the app;
everything else written to or deleted from the machine happens only from the
Sync page, by a person.** A profile push goes through the four layers below and
is the one class of write that may ever be automated (replacing an old version
on the machine with its approved successor, say); nothing automates one today.
A shot delete runs only when a person confirms a cleanup plan on the Sync page,
and the request carries the planned shot ids so a plan that moved in between is
refused rather than run. A notes save runs only when a person selects
judgements there and sends them; saving a judgement never contacts the machine.
No timer, no hook after a pull, and no tool a language model can call starts
either.

That is a property of the code, not a convention. `GaggimateClient`'s public
surface is two closed lists — ten reads in `READ_ONLY_METHODS`, seven writes in
`GATED_WRITE_METHODS` — `_send` is private, no write method can reach it except
through the gate, and `tests/device/test_public_surface.py` fails the build if
an eleventh read or an eighth write appears, or if a request type outside those
seven shows up anywhere in the module, including in a docstring. Widening that
list is what storage cleanup and notes write-back each did deliberately, by moving a request
type from the test's forbidden-grep list into its "appears exactly once" list —
an edit nobody makes by accident. Even the
simulator end-to-end test, which genuinely needs the machine to brew, opens a
throwaway socket of its own rather than widening that surface.

The gate is `deviceWritesEnabled`, **off by default**, re-read on every single
write rather than cached at boot — the person turning it off is usually the
person who has just seen something they did not like. It is the only switch:
the two history writes have no second one, because the consent for each is a
person confirming it on the Sync page, and a request made there with writes off
is refused before anything is queued (and audited). `deviceCleanupMode` is not a
switch either; it shapes the plan the page proposes, and `off` proposes nothing.
The gate's per-kind branch is where the narrower rules live — a delete is
refused unless the archive already holds that shot intact. A client built without a
gate (in a test, in a script) gets `DenyAllWrites` and can write nothing at all,
so read-only is what you get by forgetting. Every attempt, authorised or
refused, leaves a row in `device_writes`, which the Sync page lists.

**The chat and MCP add callers, not writes, and the MCP server is read-only by
design.** Every tool declares a permission class, and there are exactly two:
`read`, and `propose`, which writes to gaggiclanker — a Set version, a profile
draft, an unconfirmed insight — and to nothing else. The registry refuses to
register a tool declaring anything else, so the in-app chat and every MCP client
are handed the same set and no setting widens it. The connection to the machine
is this application's own HTTP API and nothing more. Pushing a draft to the
machine stays what it was: a button a person presses, on a page showing the diff
they are approving.

This page describes the four layers between a profile and the machine, and why
the bar is where it is.

## What can actually go wrong

**Shot data can be lost, and that is the risk device storage cleanup manages.**
`req:history:delete` removes the `.slog`, the notes file and the index entry,
and there is no undo on the display — which is why it happens only after a
person has seen the list of shots and confirmed it, and why the confirmation
says so. What makes it acceptable is that the
firmware performs exactly the same deletion itself whenever free space drops
below 500 KB, archived or not — the machine loses these shots either way, and
the only question is whether this box has them first. So the gate refuses the
delete unless the archive holds that shot, for that machine, unquarantined — a
shot whose bytes are stored but did not parse stays on the display, because a
parser fix can still re-derive it — and with a stored blob exactly the length
its header implies. The rule is `gaggiclanker/cleanup/eligibility.py`, it is applied
by the plan step *and* by the gate, and a refusal is audited with its reason.

**A notes save overwrites somebody's typing if it is careless.**
`req:history:notes:save` stores the document verbatim and rewrites the index's
rating and volume as a side effect, so a send from the Sync page writes the
machine's own document with our fields laid over it (unknown keys survive), and
only when our judgement is newer than the card's `timestamp` — a selected shot
whose card is newer is skipped. A judgement that came *from* the machine
and was never edited is never sent back.

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
to everything entering the database too. `scripts/profile_gate.py` runs all four
over a profile file from a shell, which is the cheapest way to check a
hand-written one before it goes anywhere near a machine.

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
`gaggiclanker/domain/profile_policy.py`, with its bounds in the settings
registry (Settings → Profile safety policy) so they can be tuned: temperature
60–100 °C, pressure 0–12 bar, flow 0–10 ml/s, phase duration 0.5–120 s, at most
10 phases, transition duration no longer than its phase, and every profile
ending in a volumetric or pumped stop or a bounded duration.

Two functions, and the difference between them is the design. `clamp()` moves
numbers into range **and says what it moved** — the list is stored on the draft
and rendered beside the approve button, because a silent clamp is a profile
nobody approved presented as one they did. `check()` reports what a clamp cannot
fix, and that list is a refusal: eleven phases is *rejected*, never trimmed to
ten, because truncating a profile would change what it brews while claiming to
have made it safe.

On top of the bounds, crema's rule: a draft that adds, removes or moves a
`targets` entry needs an explicit acknowledgement before it can be approved.
Everything else in a profile changes how a shot is pulled; a stop condition
changes how much coffee ends up in the cup. The diff normalises numbers, so `9`
and `9.0` are not a change anybody is asked to tick a box for.

**3. Round-trip verification.**
After `req:profiles:save`, load the returned id back and compare canonical JSON
— numbers normalised, firmware-added fields tolerated. `writeProfile` never
echoes what you sent: it parses into a struct and serialises the struct, so the
document that comes back has an `id`, a `favorite`, a `selected`, a
`transition.target` and a spelled-out phase `temperature` of 0 that the document
going out did not. `canonical_profile_json` drops exactly that set, which is why
a faithful machine compares equal and an unfaithful one does not.

A mismatch marks the push `failed`, records both documents on the draft, and
offers a one-click delete of the device's copy. "The machine says it saved it"
is not the same as "the machine stored what we sent", and the difference is only
visible by reading it back.

Two more rules live at this layer rather than in the policy, because they are
about the machine rather than about the document. A save **never overwrites**:
`saveProfile` upserts on `/p/<id>.json`, so `save_profile` refuses a profile
carrying an id at all and the firmware generates its own. A delete needs **two
independent proofs** that the profile is ours — the label on the machine right
now ends in ` [AI]`, and the `device_writes` audit holds a successful save for
that id. A person can rename a profile to end in "[AI]"; an id can be reused
after a delete. Together they mean it is the profile we pushed and it is still
ours.

**4. A simulator gate in CI.**
`tests/simulator/test_profile_push.py`: every profile fixture is saved to the
firmware's `display-sim`, read back and compared, then one drafted profile is
pushed through the whole flow, selected, brewed to completion and rolled back.
Everything it creates it deletes. The simulator runs the same parser and the
same brew code as the device, so what it accepts, the device accepts.
`scripts/sim.sh test` is what builds and runs it.

This layer is also what makes layer 3 trustworthy: if the real `writeProfile`
emitted a field `canonical_profile_json` does not drop, every push would report
a mismatch and the feature would be unusable. The fake device reproduces
`writeProfile` from the source; this checks the source.

## Why the layers are ordered this way

Each one catches a class the next cannot see. A schema cannot tell that 140 °C
is valid JSON and a ruined shot. A policy cannot tell that the firmware silently
dropped a target type it did not recognise. A round trip cannot tell that the
profile it faithfully stored will crash on brew start. Only the simulator can,
and only the first three are cheap enough to run on every request.

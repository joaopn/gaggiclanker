# Device safety: what can harm the machine, and the four layers

Verified against the GaggiMate firmware source.

**gaggiclanker writes exactly five things to a machine, all of them profile
operations, and only when a person has switched writes on.** Save, delete,
select, favourite and unfavourite: each a `req:profiles:*` frame. Only profiles
are ever written. Not a shot delete, not a notes card, not a setting, not a
mode change, not an index rebuild. (Earlier versions could delete shots the
archive already held and write a judgement to a shot's notes card, both from the
Sync page; both were removed. The firmware deletes its own oldest shots when
free space runs low and the archive pulls before it does, which is accepted.)

**Who may start a write:** a profile push goes through the four layers below,
from a button a person presses on a page showing the diff they are approving. It
is the one class of write that may ever be automated (replacing an old version on
the machine with its approved successor, say); nothing automates one today. No
timer, no hook after a pull, and no tool a language model can call starts one.

That is a property of the code, not a convention. `GaggimateClient`'s public
surface is two closed lists — ten reads in `READ_ONLY_METHODS`, five writes in
`GATED_WRITE_METHODS` — `_send` is private, no write method can reach it except
through the gate, and `tests/device/test_public_surface.py` fails the build if
an eleventh read or a sixth write appears, or if a request type outside those
five shows up anywhere in the module, including in a docstring. `req:history:delete`
and `req:history:notes:save` are in that test's forbidden list, alongside the
index rebuild, the profile reorder and everything that moves the hardware.
Widening the surface means moving a request type from the forbidden list into
the "appears exactly once" list — an edit nobody makes by accident. Even the
simulator end-to-end test, which genuinely needs the machine to brew, opens a
throwaway socket of its own rather than widening that surface.

The gate is `deviceWritesEnabled`, **off by default**, re-read on every single
write rather than cached at boot — the person turning it off is usually the
person who has just seen something they did not like. It is the only switch.
The gate's per-kind branch is where the narrower rules live — a profile delete
is refused unless the audit holds a successful save for that id. A client built
without a gate (in a test, in a script) gets `DenyAllWrites` and can write
nothing at all, so read-only is what you get by forgetting. Every attempt,
authorised or refused, leaves a row in `device_writes`, which the Sync page
lists; rows of the two removed history kinds (`shot_delete`, `notes_save`) from
an older archive are still listed as history.

**The chat adds a caller, not writes, and its tools cannot reach the machine.**
Every tool declares a permission class, and there are exactly two: `read`, and
`propose`, which writes to gaggiclanker — a Set version, a profile draft, an
unconfirmed insight — and to nothing else. The registry refuses to register a
tool declaring anything else, so the chat is handed the same set whichever
provider runs it — including `claude_code`, whose tool loop reaches the registry
through the stdio MCP server it spawns — and no setting widens it. Nor does a
tool hold anything that could write: the context it is handed carries the
object that creates drafts, never the service that pushes them, and no path from
it leads to the device client or the connection that owns it (a test walks the
graph). The connection to the machine is this application's own HTTP API and
nothing more. Pushing a draft to the machine stays what it was: a button a
person presses, on a page showing the diff they are approving.

This page describes the four layers between a profile and the machine, and why
the bar is where it is.

## What can actually go wrong

**Shot data can be lost, and the machine does it on its own.** The firmware
deletes its oldest shot files (`cleanupHistory` in `ShotHistoryPlugin.cpp`)
whenever free space drops below 500 KB, archived or not, and there is no undo.
This box does not delete shots and does not try to get ahead of that: the
defence is a pull that has run before the machine gets there, and the accepted
cost is that a shot never pulled is one the rotation may take. Notes are the
same: the machine's notes card is read into the archive on a pull and never
written back, so a card edited on the display after the last pull is only in
the archive once the next pull has seen it.

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

**`POST /api/settings` is the genuinely dangerous endpoint.** It can change WiFi and PID,
and up to firmware v1.8.x it also cleared every boolean key absent from its body
(v1.9.0 made it a partial update). gaggiclanker
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
about the machine rather than about the document. A save **never overwrites** (a replace is a new save followed by a guarded delete):
`saveProfile` upserts on `/p/<id>.json`, so `save_profile` refuses a profile
carrying an id at all and the firmware generates its own. A delete needs **two
independent proofs** that the profile is ours — the label on the machine right
now ends in ` [AI]`, and the `device_writes` audit holds a successful save for
that id. A person can rename a profile to end in "[AI]"; an id can be reused
after a delete. Together they mean it is the profile we pushed and it is still
ours.

**A push replaces, a rollback restores.** The machine is listed and every profile
loaded again before each write; nothing the archive remembers about it is trusted
without that read. The steps, in this order, and any failure before the last one
leaves both profiles on the machine:

1. If a profile already holds the canonical content being pushed, nothing is
   saved and that id is used (two identical profiles are clutter and a needless
   write).
2. Save the new profile and read it back (layer 3).
3. Star and select the new profile if the one it replaces was starred or
   selected, so the display looks the same to whoever stands at it.
4. Remove the predecessor, only within one lineage: for a push recorded as a
   Set's next version, what the Set's current version has on the machine;
   otherwise the profile the draft was made from, and only when the label on the
   machine is the label being pushed (a fork under a new name is a new profile and
   removes nothing). It goes only when **all** of these hold on a fresh load, read
   again immediately before the delete: the audit holds a successful save of
   that id by this box and its label ends in ` [AI]` (the two proofs above), and
   its content is exactly what the archive recorded for it. A person's own
   profile is never removed: the first push made from a hand-made profile adds
   beside it, and later pushes of that lineage replace the app's copy. A copy
   edited on the display since, one this app did not create, one any Set's current
   version still brews (by device id or by stored profile, so a Set that picked it
   from the library or whose latest version is a grind change counts), or one already gone stays, and the push result
   says which. A gate refusal part-way leaves both profiles and is reported the
   same way. Drafts that pushed the removed profile can no longer be rolled back.

Outside a Set the new profile keeps the predecessor's label (the same label is what
makes it a predecessor); inside a Set it carries the label the draft was approved with. The firmware clears its
startup-profile setting when that profile is deleted (`ProfileManager::deleteProfile`)
and this app never writes settings, so the push result says when that happened. A
**rollback** first checks that the draft's profile can be removed (it is this app's,
unchanged, and no other pushed draft or Set still uses it), and touches nothing if
not; then puts the predecessor back (saved from its archived content, by the same
steps, unless an identical profile is still there; a copy that does not verify is
removed again); then removes the draft's profile under the same checks. A push that
only reused a profile already on the machine has no profile of its own to remove. A draft's base counts as unchanged when
the machine holds its content under any id; a base that is gone (a machine reset by
an update) is not stale and the push simply adds.

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

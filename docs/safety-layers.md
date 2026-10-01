# Device safety: what can harm the machine, and the four layers

Verified against the GaggiMate firmware source.

**gaggiclanker writes exactly five things to a machine, all of them profile
operations, and only when a person has switched writes on.** Save, delete,
select, favourite and unfavourite: each a `req:profiles:*` frame. Only profiles
are ever written. Not a shot delete, not a notes card, not a setting, not a
mode change, not an index rebuild. (Earlier versions could delete shots the
archive already held and write a judgement to a shot's notes card, both from the
Sync page; both were removed. The firmware deletes its own oldest shots when
free space runs low and the archive syncs before it does, which is accepted.)

**Who may start a write:** one thing, and nothing else. Only while the writes switch is
on, **the board sync at the end of a sync**: the app keeps its own profile board, and a
sync makes the machine's profiles match it (see "The board sync" below). That is the one
automatic write, and it is the only one: no timer, no machine event, no push or rollback
route, no other hook after a sync, and no tool a language model can call starts a write.
What a person decides is what is on the board (putting a draft on it, going back to a
profile's previous version, deleting a profile, the home-screen flag), on pages that show
the diff they are putting there; none of those sends a byte to the machine, and each works
with the switch off. The profile pass of a sync is started only by the Sync button's
route (a test pins the call sites).

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
write rather than cached at boot (it is the **Writes** switch in the top bar) — the person turning it off is usually the
person who has just seen something they did not like. It is the only switch.
The gate's per-kind branch is where the narrower rules live — a profile delete
is refused unless the audit holds a successful save for that id. A client built
without a gate (in a test, in a script) gets `DenyAllWrites` and can write
nothing at all, so read-only is what you get by forgetting. Every attempt,
authorised or refused, leaves a row in `device_writes`, which the Sync page
lists; rows of the two removed history kinds (`shot_delete`, `notes_save`) from
an older archive are still listed as history.

**The board sync is the only path to a write, and it goes through two primitives.**
It runs only when `deviceWritesEnabled` is on, at the end of the profile pass of a
sync, inside the sync engine's lock; with the switch off a sync sends nothing and
reads nothing for writing. It has no machine path of its own: every save is `place`
(`drafts/machine.py`: no duplicate, read back and compared), every removal
`remove_if_ours` (an `ok` save of that id by this app in the audit, the app label,
exactly the content recorded, no Set still brewing it, the star and the selection
moved first), every star change a gated `favorite_profile`/`unfavorite_profile`, and
every one leaves its `device_writes` row. Its plan builder (`drafts/board_plan.py`) only
reads; the same list is served as the preview before the first write. (The app used to
have a second path, a staged push and rollback a person pressed on a draft; it was
removed, so the primitives have one caller and the plan is the only description of what
will be written.)

**The chat adds a caller, not writes, and its tools cannot reach the machine.**
Every tool declares a permission class, and there are exactly two: `read`, and
`propose`, which writes to gaggiclanker — a Set version, a profile draft, an
unconfirmed insight — and to nothing else. The registry refuses to register a
tool declaring anything else, so the chat is handed the same set whichever
provider runs it — including `claude_code`, whose tool loop reaches the registry
through the stdio MCP server it spawns — and no setting widens it. Nor does a
tool hold anything that could write: the context it is handed carries the
object that creates drafts, and the draft service holds no machine connection at
all; no path from the context leads to the device client, the connection that owns it
or the board that writes through them (a test walks the graph). The connection to
the machine is this application's own HTTP API and nothing more. Putting a draft on the
board is one button a person presses, on a page showing the diff they are putting
there (it approves the draft, and asks for the stop-condition acknowledgement when a stop
condition moved); the next sync with the switch on does the writing.

This page describes the four layers between a profile and the machine, and why
the bar is where it is.

## The board sync

The app owns a **profile board**: one row per profile it means the machine to hold, with
the version that is current, whether it belongs on the machine's home screen, and which
file on the machine stands for it. Profiles are edited in the app, never on the machine.
Editing the board (putting a draft on it, going back to a profile's previous version,
turning the home-screen flag on or off, deleting a profile) writes to the archive only, and
works with the switch off.

**Two live profiles never share a label.** A put that would add a second profile beside
one with its label, or rename a profile onto a label another holds, is refused (the card says
the board already has that profile and offers refine or discard), and so is taking a machine
profile whose label is already on the board. Both checks run inside the transaction that
writes the row, so two requests at once leave one profile. It is a transactional check and
not a unique index on purpose: adoption takes the machine as it is, and a machine can hold
two profiles with one name, which an index could not admit (nor could it be created on a board
that already holds such a pair). A pair that adoption took is reported on the board, not
refused and not acted on, and a new version of one of them is allowed, since it does not
make the pair worse.

**Going back.** A profile the app wrote remembers the version it was before its newest put,
and a person can go back to it. Going back edits the board only; the sync then does what any
replacement does (saves the earlier version, removes the newer copy through the guards below),
and the record stays true: the draft that made the newer version is discarded, the Set
versions that named the removed copy stop naming it, and the ones the replacement had cleared
name the copy put back. A profile of the person's cannot go back, and a profile with no earlier
version, or whose earlier version would repeat a label on the board, cannot either.

With the switch on, every sync ends by making the machine's profiles match the board,
in this order:

1. **Adoption**, once, on the first sync with the switch on: every profile the machine
   holds becomes a board row exactly as it is, its favourite star being the home-screen
   flag. A file this app saved itself (an `ok` save of that id on this host in the audit,
   the app label, and content equal to what that save sent) becomes an app row; every other
   profile, a label ending in the app suffix included and a copy edited on the display since
   the app saved it, is the person's, and a person's profile is never removed. Nothing is written to the machine, and the sync ends
   there.
2. **Each live row**, but **only the app's own versions are ever pushed**: a row whose
   current version came from a draft a person put on the board. When the machine holds no file with that
   content, the version is run through the schema and the safety policy with the bounds as
   they are now (a version that fails stays off the machine and is reported), saved, and read
   back and compared. The file the row stood on before is then removed if it is the app's.
   A round trip that does not match removes the copy just written through the same guarded
   path and keeps the previous version; if that copy cannot be removed the version is not
   tried again until the profile changes. A profile you made yourself (an adopted row) is
   never pushed: when its file is missing or was changed on the machine the sync reports it
   and does nothing, and its home-screen flag is still applied while the file exists. An app
   profile edited on the machine gets the board's version saved beside the edit, and the edit
   is left and reported.
3. **Each deleted row**: its file is removed if it is the app's.
4. **The home screen**: each file's star is set to its row's flag (a profile off the home
   screen stays on the machine and leaves the carousel; the firmware has no disabled
   state), from a fresh read when anything was written above.

**A profile the app did not write is never removed, and neither is one that no longer holds
exactly what the archive recorded for the app's save.** The delete guards are `remove_if_ours`'s
own: an `ok` save of that id by this app in the audit, the app label, exactly that content on
a fresh load immediately before the delete, no live board profile standing on the file (asked
again at the moment of the delete, deleted rows' files included), no Set brewing it. A file
that fails them stays and the sync reports which and why. The machine's selected profile is
never removed before its successor (never a utility profile) is selected. A file the machine
listed but could not load is neither pushed again nor forgotten: the row keeps it and the
run records a failure.

**A machine that looks reset pauses the phase.** If the app's profiles were on the machine at
the last sync and none of their files is there now, the sync writes nothing, records "the
machine looks reset; nothing written", and the board shows it. A person resumes it
(`POST /api/profile-board/resume`, a route only: no chat or MCP tool reaches it); the next sync
then pushes the board's app profiles. Adopted profiles are never pushed back. A person who
deleted every file by hand looks the same, which is why the answer is theirs.

A push made by the staged box the app used to have, which failed to verify, leaves a copy that
adoption takes as the person's (it no longer holds what the app saved), so only the display can
remove it; none can arise now. A sync that stops halfway leaves every profile old or new: a push
is a save followed by a removal, a failure between them leaves both files on the machine, and the
next sync finds the new one by its content and finishes the removal. Failures are values
(events, an error count on the run), and three device failures in a row end the phase. The order
of profiles on the machine is not synced.

The firmware simulator gate (`tests/simulator/test_profile_push.py`) covers a sync that
pushes, replaces and clears a star, and going back, on the real firmware.

## What can actually go wrong

**Shot data can be lost, and the machine does it on its own.** The firmware
deletes its oldest shot files (`cleanupHistory` in `ShotHistoryPlugin.cpp`)
whenever free space drops below 500 KB, archived or not, and there is no undo.
This box does not delete shots and does not try to get ahead of that: the
defence is a sync that has run before the machine gets there, and the accepted
cost is that a shot never synced is one the rotation may take. Notes are the
same: the machine's notes card is read into the archive on a sync and never
written back, so a card edited on the display after the last sync is only in
the archive once the next sync has seen it.

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
and rendered beside the put-on-the-board button, because a silent clamp is a profile
nobody approved presented as one they did. `check()` reports what a clamp cannot
fix, and that list is a refusal: eleven phases is *rejected*, never trimmed to
ten, because truncating a profile would change what it brews while claiming to
have made it safe.

On top of the bounds, crema's rule: a draft that adds, removes or moves a
`targets` entry needs an explicit acknowledgement before it can be put on the board (the put is the
approval, so the checkbox is on that click).
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

A mismatch removes the copy just written (through the guarded path below), keeps the
profile's previous version on the machine, and records the failure on the sync's run with both
documents' difference; if that copy cannot be removed the version is not tried again until the
profile changes. "The machine says it saved it" is not the same as "the machine stored what we
sent", and the difference is only visible by reading it back.

Two more rules live at this layer rather than in the policy, because they are
about the machine rather than about the document. A save **never overwrites** (a replace is a new save followed by a guarded delete):
`saveProfile` upserts on `/p/<id>.json`, so `save_profile` refuses a profile
carrying an id at all and the firmware generates its own. A delete needs **two
independent proofs** that the profile is ours — the label on the machine right
now ends in ` [AI]`, and the `device_writes` audit holds a successful save for
that id. A person can rename a profile to end in "[AI]"; an id can be reused
after a delete. Together they mean it is the profile we pushed and it is still
ours.

**A sync replaces, going back restores.** The machine is listed and every profile
loaded again before each write; nothing the archive remembers about it is trusted
without that read. For a board profile whose current version the machine does not hold,
the steps, in this order, and any failure before the last one leaves both files on the
machine:

1. If a file already holds the canonical content, and no other board profile stands on it,
   nothing is saved and that id is used (two identical profiles are clutter and a needless
   write).
2. Save the new version and read it back (layer 3).
3. Star and select the new file if the one it replaces was starred or selected, so the
   display looks the same to whoever stands at it.
4. Remove the file the profile stood on before, and only that one: it goes only when **all**
   of these hold on a fresh load, read again immediately before the delete: the audit holds
   a successful save of that id by this box and its label ends in ` [AI]` (the two proofs
   above), its content is exactly what the archive recorded for it, no other live board
   profile stands on it, and no Set's current version still brews it (by device id or by
   stored profile, so a Set that picked it from the library or whose latest version is a
   grind change counts; the Set that recorded the version being left, or that a person is
   going back from, does not count). A person's own profile is never removed. A copy edited
   on the display since, one this app did not create, one a Set is brewing, or one already
   gone stays, and the sync says which.

The firmware clears its startup-profile setting when that profile is deleted
(`ProfileManager::deleteProfile`) and this app never writes settings, so the primitive reports
when that happened. **Going back** is the same sync with the profile's previous version as the
current one: it is saved again (a new file, since the firmware always assigns an id), the
newer file is removed under the guards above, and the selection and star go back with it.
Nothing is restored from a copy kept elsewhere: the earlier version is the archive's stored
document, and what lands on the machine is checked by the same read-back.

**4. A simulator gate in CI.**
`tests/simulator/test_profile_push.py`: every profile fixture is saved to the
firmware's `display-sim`, read back and compared, then one drafted profile is
put on the board, synced to the machine, verified, selected, brewed to completion and
deleted from the board and synced off; a second version replaces the first and going
back restores it; and one sync pushes, replaces and clears a star. Everything it creates it
deletes. The simulator runs the same parser and the
same brew code as the device, so what it accepts, the device accepts.
`scripts/sim.sh test` is what builds and runs it.

This layer is also what makes layer 3 trustworthy: if the real `writeProfile`
emitted a field `canonical_profile_json` does not drop, every sync that pushed would report
a mismatch and the feature would be unusable. The fake device reproduces
`writeProfile` from the source; this checks the source.

## Why the layers are ordered this way

Each one catches a class the next cannot see. A schema cannot tell that 140 °C
is valid JSON and a ruined shot. A policy cannot tell that the firmware silently
dropped a target type it did not recognise. A round trip cannot tell that the
profile it faithfully stored will crash on brew start. Only the simulator can,
and only the first three are cheap enough to run on every request.

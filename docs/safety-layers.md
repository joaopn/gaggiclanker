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
What a person decides is what is in the profile list (switching a profile on or off the
machine, making a version active, making a proposal active, choosing a side of a conflict, the
star), on pages that show
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
The gate holds one rule, the switch; the narrower rule for a delete lives in the client
(`delete_profile` loads the profile fresh and refuses unless it holds exactly the content the
caller recorded for it). A client built
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
`remove_profile` (a fresh load, twice, that must hold exactly the content the archive
recorded for the file; no other profile standing on it; the selection moved first), every star change a gated `favorite_profile`/`unfavorite_profile`, and
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
the machine is this application's own HTTP API and nothing more. Making a proposal active
is one button a person presses, on a page showing the diff they are putting
there (it approves the draft, and asks for the stop-condition acknowledgement when a stop
condition moved); the next sync with the switch on does the writing.

This page describes the four layers between a profile and the machine, and why
the bar is where it is.

## The profile list and its sync

The app owns a **list of profiles**: one row per profile a person has had, switched **on the
machine** or off, with the versions it has been, the one that is **active**, an independent
**starred** flag (the machine's home-screen carousel), and which file on the machine stands for
it. Editing the list (switching a profile on or off, starring it, making one of its versions
active, putting an approved draft on it, resolving a conflict) writes to the archive only, and
works with the switch off. A sync with the switch on makes the machine hold **exactly the
profiles that are on**.

**Every profile the app has synced is the app's to manage**: a firmware default or a profile
made on the display is pushed, replaced, starred and removed like one the agent made. The
`[AI]` suffix marks profiles the agent wrote from scratch, and renames, and decides nothing. What stands between a profile and
its removal is whether the app has *seen* what the file holds: the one removal guard is a
fresh load, immediately before the delete (and again inside the client's `delete_profile`),
that must hash to the content the archive recorded for that file.

**Two live profiles never share a label.** A put that would add a second profile beside one
with its label, or rename a profile onto a label another holds, is refused, and so is making
a version active that would; a transactional check, not a unique index, so a database from
before the rule that holds such a pair keeps working (the pair is reported).

**Conflicts.** A profile edited outside the app is never overwritten silently. When a machine
file standing for a profile (or a never-seen file carrying its name) holds content that is
neither what the app last recorded for it, nor the profile's active version, nor a version it
has had, the profile is **in conflict**: the content is kept as a version
(`edited_on_machine`, not active), the sync does nothing at all for that profile (no push,
removal, star or selection) and says so, and the others sync as usual. Detection is by
content, because the firmware keeps a profile's id when it is edited on the display. A person
chooses (`POST /api/profile-board/{id}/conflict`): the **machine**'s version becomes the active
one and nothing is pushed, or the **app**'s version is kept, in which case that exact machine
content is remembered as overruled (it is not flagged again; a further edit is a new conflict)
and the next sync replaces the file under the removal guard. The route refuses a hash that is
not the content the person saw.

With the switch on, every sync ends by making the machine match the list, in this order:

1. **The first sync with the switch on** takes the machine's files into the list by the rules
   below and writes nothing; the sync ends there.
2. **Files the app has never seen** (no profile stands on them) join the list. A file whose
   content is a version of an existing profile, or that fills an existing profile's missing
   file with content it has had, is attached to it; one carrying a profile's name but other
   content is the machine's side of a conflict; anything else becomes a new profile, **on**,
   starred as the machine has it. A second file for a profile that already has one is reported
   and left. **The sync that finds a file never removes or replaces it.**
3. **Each profile that is on**, pushed with its active version whoever made it: when the
   machine holds no file with that content, the version is run through the schema and the
   safety policy with the bounds as they are now (a version that fails stays off the machine,
   the plan says why, and the rest is not blocked), saved, and read back and compared. The file
   the profile stood on before is then removed under the guard. A round trip that does not
   match removes the copy just written through the same guarded path and keeps the previous
   version; if that copy cannot be removed the version is not tried again until another is made
   active.
4. **Each profile that is off**: its file is removed under the guard. A Set brewing it does not
   hold it back (switching a profile off, or changing its active version, is the person's
   explicit choice; the list serves the Sets so the page can warn beforehand).
5. **Each deleted row** (the old Delete) the same way.
6. **Stars**, only for profiles that are on and from a fresh read when anything was written
   above; a star is remembered while a profile is off.

**The machine's selected profile is never removed before its successor** (the first profile
that is on, never a utility profile) is selected; with none, it stays and the plan says why. A
file the machine listed but could not load is neither pushed again nor forgotten.

**A machine that looks reset pauses the phase.** If files the last sync left on the machine
were there and none is now, the sync writes nothing, records "the machine looks reset; nothing
written", and the list serves what resuming would do ("put back N profiles and remove M") so
one button asks once. A person resumes it (`POST /api/profile-board/resume`, a route only: no
chat or MCP tool reaches it); the next sync then does exactly what was served. An empty list
(nothing synced yet) is not a reset.

A sync that stops halfway leaves every profile old or new: a push is a save followed by a
removal, a failure between them leaves both files on the machine, and the next sync finds the
new one by its content and finishes the removal. Failures are values (events, an error count
on the run), and three device failures in a row end the phase. The order of profiles on the
machine is not synced.

The firmware simulator gate (`tests/simulator/test_profile_push.py`) covers a sync that
pushes, replaces and clears a star, going back, a file the app never saw that joins the list
and is removed when switched off, an older version made active, and a profile edited on the
display becoming a conflict.

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
and rendered beside the Make active button, because a silent clamp is a profile
nobody approved presented as one they did. `check()` reports what a clamp cannot
fix, and that list is a refusal: eleven phases is *rejected*, never trimmed to
ten, because truncating a profile would change what it brews while claiming to
have made it safe.

On top of the bounds, crema's rule: a draft that adds, removes or moves a
`targets` entry needs an explicit acknowledgement before it can be made active (making it active is the
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
carrying an id at all and the firmware generates its own. A delete needs **one
proof**, read from the machine and not from the archive: the profile loaded fresh, immediately
before the delete, must hold exactly the content the archive recorded for it. Who made it
(the label, the audit of saves) decides nothing: every profile the app has synced is the app's
to manage. A file holding content the profile never had (edited on the display) is a conflict
and is never deleted or replaced until a person chooses; one holding an older version of the
profile is recorded and then handled as usual.

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
   of these hold on a fresh load, read again immediately before the delete: its content is
   exactly what the archive recorded for it (the one guard), no other live profile stands on
   it, and it is not the machine's selected profile with nothing to take the selection. A
   file edited on the display since stays (it is a conflict, see above), and the sync says
   which.

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
put on the list, synced to the machine, verified, selected, brewed to completion and
switched off and synced away; a second version replaces the first and making the first active
again restores it; and one sync pushes, replaces and clears a star. Everything it creates it
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

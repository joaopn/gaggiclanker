# gaggiclanker

A self-hosted archive and analyst for a [GaggiMate](https://gaggimate.eu)
espresso machine. Press a button and it brings in every shot the machine holds —
raw `.slog` bytes, every sample, phases, the device's own notes and profiles —
shows them with curves and deterministic diagnostics, lets you judge each shot
from the list and group shots into versioned **Sets** (bean + hardware + profile
+ grind/dose/yield), reviews a shot with a language model when you ask it to, and
talks the Sets through with a tool-using chat, through whichever provider you
point it at.

It does not watch the machine. Nothing leaves the GaggiMate until you ask for
it: the machine's own web UI already draws the shot that is happening now, and
what an archive is for is the hundred before it.

The machine holds a few hundred KB of flash and deletes old shots when it runs
low. This is the thing that remembers them.

> **Status: 0.1.0, the prototype, plus the profile list.** Everything in this README
> works: sync, the shots UI, Sets and judgement, the LLM layer, a shot's review,
> the chat, optional authentication and the container. It can now
> also hold a list of your profiles and make the machine match it — you switch a profile on or
> off the machine and make one of its versions active, and the next sync puts the active
> version on the machine and removes the profiles that are off, behind a switch that is off by
> default and through the four layers in `docs/safety-layers.md`.
> `CHANGELOG.md` has what landed in each release.

## Quick start

```bash
git clone <this repo> && cd gaggiclanker
docker compose up -d --build         # builds the image and starts on :8042
curl localhost:8042/health           # {"ok":true,"data":{"status":"ok",...}}
open http://localhost:8042           # the UI
```

Nothing to copy, rename or edit first. Everything is configured from the UI and
kept in the database, so a checkout is ready to start as it comes and a later
`git pull` has no file of yours to conflict with.

**The port is the one thing still given on the command line.** It defaults to
8042 and cannot be a setting — the process has to bind a socket before it can
open the database and read one — so it is passed at spawn and remembered
nowhere. Give it again every time you start the app, from your shell or from a
`.env` in this directory that Compose substitutes from:

```bash
PORT=9000 docker compose up -d --build   # host networking, the default here
HOST_PORT=9000 docker compose up -d      # bridge: published on 9000, container stays on 8042
PORT=9000 uv run gaggiclanker            # a source checkout, no Docker
```

`compose.yml` uses **host networking** by default, so the app is on port 8042 of
the box you started it on and mDNS names resolve. Docker Desktop does not
support that; swap in the `ports:` block the file documents next to it and use
an IP for the machine's host.

### First run

1. **Open the UI** at `http://<this box>:8042`.
2. **Set the machine's address in Settings → Machine access.** `gaggimateHost` is the
   display board's IP or hostname, with no scheme (`192.168.1.50`, or
   `192.168.1.50:80`). It connects on save, with no restart: the pill in the
   header goes green within a few seconds of the WebSocket connecting. Prefer a
   fixed IP or a DHCP reservation — see Troubleshooting for why the name your
   browser resolves may not resolve here.
3. **Press "Sync with machine"** on the Shots page. The first sync walks the
   machine's whole history, which for a few hundred shots takes a minute or
   two; when it finishes it says what it archived, how many profiles it read from the
   machine and how many it wrote to it ("Read 9 profiles from the machine; no writes
   (writes are off)"). Shots under 7.5 seconds
   never appear: the firmware discards them. After that, sync whenever you have
   pulled some coffee — or drop exported files on the strip under the header,
   which is the only way back for shots the machine has already deleted.
4. **Turn on authentication** if this box is reachable by anything you do not
   trust — see below. Off by default, and that is the right default for a
   machine only your own LAN can reach.
5. **Set up the model.** A review and the chat need one provider. The cheapest to get
   working is `claude_code`, which spends a Claude subscription you may already
   have: run `claude setup-token` on any machine with the CLI installed and
   paste the token into **Settings → LLM**.
   An interactive `claude login` is **not** enough — every call runs with a
   scratch `HOME`, which is what keeps this repository out of the prompt and
   hides `~/.claude` along with it. Any OpenAI-compatible gateway works too;
   see LLM settings below.
6. **Take a backup** once there is something worth keeping: **Settings →
   System → Backup**, or `curl -X POST localhost:8042/api/backup`.

Your data lives in `./data` — one SQLite file plus `backups/`. Back it up by
copying that directory, or call `POST /api/backup` for a consistent snapshot
taken while the app is running.

### The pages

The sidebar has five rows: Shots, Chat, **Brew setup** (Sets, Beans, Hardware, Taste wheel),
**Machine** (Profiles, Sync, Device) and **Settings** (one page per heading,
with Knowledge just before Prompts). A group opens when you click it, and on its own
when you are on one of its pages; one you open by hand stays open next time. The
pages, in the order the sidebar lists them:

| Page | `g` | What it is |
| --- | --- | --- |
| **Shots** | `g s` | The archive: the list, the filters, one shot with its curve and diagnostics. A row opens in place with the shot page's judgement and curves boxes, laid out as on the page: the full judgement across the top (saved with its button, the notes in its right-hand column) and the curves with their toggles and downloads on a row of their own below it. Each row's Decision column records Keep, Improve or Discard; the Set column can be dragged narrower. A shot is reviewed from its own page, and the review is shown only there. The filters narrow by date, profile, Set, score, rating, source and readability; a Set's experiment log links each version's shot count straight at *that version's* shots, and the filter says which version is on and removes it in one click. The sync button and the import drop zone are both here. A new shot is filed under the one Set that brews its profile: exactly one Set set to collect shots, whose current version names that profile (never when two do, never over a Set you picked). **Match by profile** runs the same rule over the shots already waiting; a shot's own page has the button too. Above the table, **Chat about** has one button per Set you are brewing (not archived, not being designed), labelled with its current version: it opens or continues that version's conversation with a question already typed, so after judging the shots in the table you only press Enter. |
| **Chat** | `g c` | The tool-using conversation, in a folder per Set. New inside a folder starts one already pointed at that Set. |
| **Sets** | `g e` | Bean + hardware + profile + recipe, versioned, with the trend across versions and the experiment log: what each version changed, what you predicted it would do, how its shots were labelled, and whether the prediction held. One click rolls an old recipe back. Each Set says whether new shots on its profile are filed under it — any number of Sets can, which is how two bags on two grinders both collect — and a finished bag is archived. |
| **Beans** | `g b` | The coffees: roaster, origin, process, roast level, decaf, acidity, intensity and sweetness (each a clickable 1-to-5 scale; click the chosen step again to clear it) and a free-form description. Roaster and origin suggest the values already recorded; a coffee is archived when you stop buying it, and one no Set uses can be deleted. |
| **Hardware** | `g h` | The machine — what it says it is, and the name and notes you give it — and the grinders. |
| **Taste wheel** | `g w` | The SCA/WCR Coffee Taster's Flavor Wheel, all three tiers. Pick which of its notes the shot panel offers, one list for taste and one for aroma. |
| **Profiles** | `g p` | One list of every profile you have had. Each row has two switches, **On the machine** (the next sync puts it there or removes it) and **Starred** (the machine's home-screen carousel, kept while the profile is off), what it brews, where it stands on the machine and what the next sync will do about it; profiles that are off are hidden unless you ask. A row opens to its versions, newest first: when each was made and where it came from, its shots and Sets, its information (the first as a summary, every later one as what changed from the version before it), **Make active** and **Edit a copy**. A version the agent proposed sits above them, marked **Proposed**, with **Make active** (and the Set it would be recorded on) and **Decline**; a proposed new profile is a row of its own. A profile whose file was edited on the machine shows **Conflict** and opens on both sides to choose from. After a suspected reset one banner asks whether to put the profiles back. |
| **Sync** | `g y` | Sync with the machine, what the last sync did to its profiles, and the record of every write this box has made to it. Nothing but a profile is ever written to the machine. |
| **Device** | | What the machine is: its versions and its connection. The status pill in the header leads here too. |
| **Settings** | `g ,` | One page of collapsible cards per heading, the ones you must fill in first: Machine access and LLM, then Authentication, Prompts, Profile safety, System and Import. The LLM page also holds the knowledge and chat budgets. |
| **Knowledge** | `g k` | Under Settings. The dial-in rules, the prose documents, and your **general** insights: the ones you write by hand and any agent-written one the app could not place on a single Set. Insights learned in a Set's conversation live on that Set's page instead. |

The sidebar folds. The button at the foot of it, or the `[` chord, collapses it
to an icon rail and back; the choice is remembered in the browser. Folded, every
entry keeps its name — a tooltip for a mouse, the accessible name for everything
else — so nothing is lost but the fourteen rems.

The old import page is now the drop zone on the Shots page, and the old drafts
page is Profiles, where a proposed profile now opens on its own row; `/import` and `/drafts` still resolve,
by redirecting. The device page's old storage, notes, sync and writes anchors
redirect to the Sync page.

### The experiment log

A Set page is a record of experiments, one per version. A version's recipe is
five things: the **profile** version, the grind as text and as a number, the
dose and the target yield. A change of profile is recorded by hand from **Change something** on the Set page, or on
its own when a proposal is made active for that Set and a sync puts it on the machine.
Recording one there sends nothing to the machine — that is still the Profiles page, and still you — but it
is what keeps the next shots landing in the Set, because a shot joins its Set by
the profile it was pulled with.

**The brew temperature is not one of the five**, and there is nowhere to type
one: the machine heats to what the profile says, so a number written on a Set
would have been a claim about a document it does not control. Both recipe forms
show what the picked profile brews at, read-only, and changing it means changing
the profile — edit it on the machine and record the new version here, or draft
one on the Profiles page. When a version switches to a profile that brews at a
different temperature, the log shows "Temperature 93 → 94 °C" beside the profile
change, marked as coming from it.

Each version says what changed against the version before it, what you were
trying, and — optionally —
what you **predicted** it would do, compared to which version. Once the shots
are in and you have labelled them Keep or Improve, you record the **outcome**:
held, partly held, failed or inconclusive, with a line saying why. Above the log
sits the only number that matters, "6 of 10 predictions held".

**Versions are named v1, v1.1, v1.2, v2.** Dialling in — the grind, the dose,
the yield, a profile draft that only tunes a parameter such as a degree of
temperature — is a **minor** version: it keeps the major and takes the next
minor (v1.2 → v1.3). A functional change to what the profile does is a **major**
version: the next whole number (v1.2 → v2). You decide which, with the **Major
change** box on the Add a version form, on a change card before you accept it,
and on a draft's put for its Set; each button names the version it will record
("Accept as v1.3", "Accept as v2"). The box starts where the rule puts it:
switching to a different profile is major, everything else — a proposal made
active for the Set included — is minor, and a roll back is major exactly when it goes back to
another profile. The agent may suggest major on its card, with its reason
beside the box, but the box is yours. Versions recorded before this existed
keep their numbers: v3 is still v3. Everywhere a version is named — the log,
the chat, the agent's context and tools, the shots table — it is by this name.

Two rules keep that number honest:

* **A prediction can only be written before the version's first shot**, and
  before its outcome has been recorded. After either, the app refuses it and
  says which. A guess typed once the cup has been tasted is a memory, and a
  track record built from memories measures nothing; a guess rewritten under a
  grade would leave that grade attached to something nobody ever predicted, so
  the grade has to be cleared first.
* **The prediction is hidden while you judge the shot.** On the shot page and in
  the shots list's panel, a shot whose version predicted something says so but
  does not show the words until the shot carries a decision. "Show prediction"
  reveals it for that view if you want it; nothing is remembered. Reading "less
  bitter" while deciding whether the cup is bitter is how a prediction becomes
  an instruction.

The outcome is the other way round: it can be changed or taken back whenever you
like, because a second opinion about a grade is ordinary.

**The agent can propose the outcome for you to accept.** At the end of its grade
a conversation about a version proposes one outcome for the whole version,
worked out from all its counted (Keep or Improve) shots, with the per-claim lines
under it. It shows as a card in the chat: **Accept** records it as written,
**Record another outcome** records your own choice of the four, **Dismiss**
records nothing and may say why. Nothing is recorded until you press one, the
Set page shows a waiting proposal beside the outcome and never as one, and
nothing an agent graded reaches a later conversation, the experiment log or the
track record unless you accepted it. You can also set or clear the outcome on the
Set page as before; a waiting card then shows both. When the same answer also
proposes the next version, accepting that version records the waiting grade
first, in the same step: one press, as long as the outcome is still open; an
outcome you recorded yourself is left alone and the grade stays waiting. If you dismissed the grade, the version
cannot be accepted until the version is graded.

### The spread: how much your shots vary anyway

Above the log the Set page says how much its shots differ **when nothing in the
recipe changed** — "Shot time ±1.8 s · from 9 repeat shots of 3 recipes". It is the number
every comparison on that page is held against, because "31 s against 34 s" means
nothing until you know whether three seconds is a lot for this grinder, this
basket and this bag. It is arithmetic the app does, the same way every time; no
model is involved.

**What counts as a repeat.** Shots brewed with the same five recipe fields —
profile version, grind text, grind number, dose, target yield — are repeats of
each other, whichever version they were filed under. That means a roll back's
shots count as repeats of the version whose recipe it copied, with no special
case, and so do the shots of a version that only changed the words. Each group's
shots are measured against their own average and those distances are pooled
across the whole Set, so many versions of two or three shots each still add up
to one usable figure. A group of one contributes nothing: a single shot has no
distance from itself.

**Which shots count.** Everything filed under the Set that is not quarantined,
not incomplete and not labelled Discard. Shots you have not labelled count too —
they are plain data, and leaving them out would make the number depend on how
diligent you have been with the buttons.

**Six measures**, each used only where the archive already holds it: shot time,
time to first drip, yield, peak pressure, average brew flow and your rating. The
yield is the one you typed into the judgement if you did, then the machine's
final weight, then the volume its own index recorded — the same order the
starting-point wizard scores a recipe by, because a machine with no scale
records nothing and the only yield that exists is the one you wrote down. A
measure your machine or your scale records nothing for is left off the block
rather than carried as an empty line, and so is a number the archive wrote as
zero to mean "there was nothing to average".

**Before it is measured.** It takes three degrees of freedom — four shots pulled
with one recipe, or two recipes with three shots and two, since each recipe
spends one degree on its own average — before the figure is worth trusting. Until then the
line says "not measured yet" and names a conservative floor instead: 2 s for the
shot time, 1 s for the first drip, 1 g for the yield, 0.3 bar for the peak
pressure, 0.2 ml/s for the brew flow and half a star for the rating. These are
first numbers, to be tuned with use.

**The evidence behind a prediction.** Every version that predicted something
carries an **Evidence** disclosure in the log: all of that version's counted
shots against all of the compared version's, measure by measure, with the mean
and the count on each side, the difference with its sign, and what that
difference shows — *beyond the spread* or *inside it*, and what it was held
against. All the shots on both sides, never a chosen one. The yardstick is the
floor while the spread is not measured yet, and two standard errors of the
difference once it is (`2 × spread × sqrt(1/n + 1/n_other)`), never less than
the floor: a difference exactly the size of the yardstick counts as inside it,
and the comparison is made on the numbers rather than on what is printed. A
difference and its yardstick are written one decimal finer than the means they
came from, so a row can never read "+2.0 s, beyond 2.0 s" when what happened was
"+2.04 s, beyond 2.00 s".
Under the table sit the balance counts and the Keep / Improve / unlabelled
counts for both sides, as plain facts with no verdict on them. It is closed
unless the prediction is still ungraded and there are shots to grade it with.
None of this appears on the shot page: the prediction is hidden there until you
have labelled the shot, and a table of what the version has been doing would
give it away whole.

**Roll back to this version** appends a new version whose recipe is the old
one's, with the version that was current as its parent — so the log shows the
reversal field by field rather than an empty entry. When the current version has
shots you want to improve on and none you kept, the page offers the way back to
the last version you did keep shots from. **A roll back writes nothing to the
machine**, even when the restored version names a different profile: putting
that profile back on the machine stays a separate, deliberate act.

Each version's shot count is a link into the shots list narrowed to **that
version**, not to the whole Set: the Keep / Improve / Discard counts beside it
are that version's too.

Versions off the line you are now brewing are marked **dead ends** and muted,
still fully readable: they were real attempts. The line is read backwards from
the current version — from a roll back to the version it restored, from anything
else to its parent — and everything it does not pass through is a dead end. Roll
back from v5 to v3 and v4 and v5 are dead ends; roll back again onto a version
that was itself a roll back, or onto one that had been a dead end, and the muting
follows: what is live is whatever the current recipe actually came from.

### What gaggiclanker writes to the machine, and who starts it

Nothing, until the **Writes** switch in the top bar, beside the machine status, is on. It
is off by default; turning it on asks first, turning it off is immediate. With it
on, **the only thing this box ever writes to the machine is a profile** (see
below). It never deletes a shot from the machine, never writes a judgement to a
shot's notes card and never changes a device setting.

Nothing an agent does is on that list. A proposed change to a Set is a row in
this archive waiting for you, and accepting it records a version — it sends
nothing. A profile a conversation proposes is a proposed version on the Profiles page, which
you make active yourself (that is the approval too). When it was proposed in a Set's conversation, the button records it as
that Set's next version with its prediction once it reaches the machine; the
button without the Set tries it without touching the Set.

The machine is a buffer, not an archive: when its storage runs low its own
firmware deletes its oldest shots, whether or not this box has them. That is
accepted rather than managed from here, so sync from the Sync page often enough
that nothing waits on the machine for long. The machine's notes cards are read
on a sync too, and never written back.

Nothing runs on its own: a sync is something you ask for, and there is no timer. With
the Writes switch on, the one thing a sync writes is the profile list's write phase at its
end (see below); nothing else, and no tool a model calls, starts a write. Every write
attempt, refused ones included, is listed under **Recent writes** on the Sync page.

### The profile list

With the **Writes** switch on, every sync ends by making the machine hold exactly the
profiles that are **on the machine** in the list on the Profiles page. The first sync with the
switch on takes the machine's profiles into the list as they are (the home screen is each
profile's star) and writes nothing; the switch's confirmation says so before you turn it on,
and after that shows what the next sync would do, read from the machine at that moment. From
then on a sync pushes each profile's active version when the machine lacks it, **removes the
ones switched off (the firmware's own profiles and ones you made on the display included)**,
and sets the stars (a profile's **Starred** flag, applied only while it is on the machine). A
file the app has never seen joins the list, on, and is never touched by the sync that finds
it. A profile changed on the display is never overwritten silently: it becomes a **conflict**,
nothing is done for it, and you choose which side to keep (**Keep the app's**: the next sync
replaces the machine's file; **Keep the machine's**: its content becomes the profile's active
version; either way it is stored as a version). Every removal is guarded by a fresh load that
must hold exactly what was last recorded. If the machine looks reset (none of the files the
last sync left is on it), syncs stop writing and the Profiles page asks once whether to put the
profiles back, with one button.

**No two profiles in the list share a name.** A proposal that would make a second profile with
a name the list already has is refused (its panel says the list already has that profile, and
offers Decline), and a second file on the machine with a profile's name is reported and left as
it is.

### Putting a profile on the machine

A profile reaches the machine only by being on in the list with an active version. A change
starts as a proposal: **Edit a copy** on a version (the JSON editor validates what you type
against the strict schema and the safety policy; save it unchanged to propose a version as it
is), or the chat or the starting-point wizard proposing one. A change that keeps a profile's
name is a new version of that profile, whoever made it and whichever version you edited: the
machine's own profiles and ones made on its display included, under their exact name. A
changed name, or a profile written from scratch, is a profile of its own named with the `[AI]`
suffix. It waits inside its profile's dropdown, marked **Proposed**, or as a row marked **New**
when it is a new profile. **Make
active** is one click: it approves the proposal (asking you to tick that you understand when it
changes when the machine stops pumping), and for a proposal made for a Set it records the Set's
next version, with the **Major change** box, once the sync has put the profile on the machine.
Any older version can be made active the same way, from the dropdown; there is no separate
going back, and no Delete: switching a profile off removes it from the machine at the next sync,
and the profile and its versions stay in the list.

The next sync with the **Writes** switch in the top bar on saves the active version as a new
profile under its name, never over an existing file. The machine is
read again first: a profile already holding the same content is reused, and a new version of a
profile replaces this app's previous copy, carrying its star and selection, instead of piling
up versions. A copy you edited on the display since is never deleted unseen: it is a conflict
you settle. What came back off the machine is compared against what was sent, and a mismatch
removes the copy just written and keeps the previous version. `docs/safety-layers.md` is the
whole contract.

Profile exports can be uploaded straight into the library with **Upload
profile** in the Profiles header — it runs them through the same importer as the
shots page, and the profile appears in the list switched off (or, when a profile already has
its name, as one of its versions), so importing never changes what a sync does.

You do not need to create `./data` first. Docker creates a missing bind-mount
source as `root:root`, so the container's entrypoint starts as root, hands that
one directory to the unprivileged app user, and drops to it before any
application code runs. If you would rather not have the entrypoint touch
ownership at all, `compose.yml` documents a named-volume alternative next to the
mount, including how to find and back up the file inside it.

## Development

```bash
uv sync                                                        # install into .venv/
uv run uvicorn gaggiclanker.main:app --reload --no-access-log  # dev server on :8042
uv run pytest                                                  # the suite, offline, on every core
uv run pytest -n 0 tests/sync/test_pull.py                     # one file, without the worker start-up
scripts/gates.sh                                               # the checks your change owes
```

`scripts/gates.sh` reads what your branch changed since `origin/dev`, committed
or not, and runs the checks that change owes: ruff, mypy, the suite and an API
schema check for the back end, the front end's own checks and build for `web/`,
and it tells you when the firmware simulator suite is owed as well. `--dry-run`
prints the plan without running it; `scripts/README.md` has the rest.

The API documents itself at `/api/docs`. Every response uses the envelope
`{ok, data | error, meta}` — including an unhandled crash; `meta.request_id`
matches the `x-request-id` header and every log line for that request.

Running the app outside Docker, `DATA_DIR` defaults to `./data` and the app
creates it. If it cannot write there it says so at startup, with the path and
the uid, rather than failing later with SQLite's "unable to open database file".

### Working without a machine

There is a fake GaggiMate in the package. It is a real HTTP + WebSocket server
loaded from `tests/fixtures`, with the firmware's quirks reproduced — 2 Hz
telemetry, `evt:history-shot-saved`, half-written `.slog` files, the SPA served
where binary was asked for, the three-client limit:

```bash
uv run python -m gaggiclanker.device.fake --port 8090   # in one terminal
uv run uvicorn gaggiclanker.main:app --reload           # in another
curl -X PATCH localhost:8042/api/settings \
  -H 'content-type: application/json' -d '{"gaggimateHost": "127.0.0.1:8090"}'
```

The address is a setting like any other, so it is entered once — in the UI under
Settings → Machine access, or with the `PATCH` above — and stays in the archive's
database file for every later run. The connection is rebuilt on save, so the app
does not need restarting.

It holds the fixture archive, so pressing "Sync with machine" against it fills
the UI with real shots and real curves.

Or seed the archive from files, with no machine at all. The web UI on the
display exports a shot as `shot-<id>.json` and a profile as `profile-<id>.json`;
those files are the only way back for a shot the machine has already deleted,
and the importer reads them into the same tables the sync engine writes.
Importing first and connecting the machine afterwards is an ordinary order to do
things in: the archive holds one machine, it exists before the first sync, and a
shot the sync engine later serves is recognised as the one already stored.

```bash
uv run gaggiclanker import tests/fixtures/exports       # or any folder, file or zip
uv run gaggiclanker import ~/exports --replace          # overwrite what is already stored
```

The same thing is `POST /api/import` and the strip at the top of the **Shots**
page: drop a folder of exports on it, or press **Choose files**, and it reports
what each file did. Importing the same shot twice is a no-op, and a file that
does not parse is reported on its own — the rest of the batch still lands. A
profile export dropped on the **Profiles** page lands the same way.

The whole offline suite runs against it, so "works against the fake" means
rather more than it usually does. `scripts/sim.sh test` is the next step up: it
clones the firmware to a scratch tree, patches its simulator shim, builds the
real display firmware natively and runs the `-m simulator` tests against it.
The reference checkout under `external/` is never modified.

## Configuration

The Settings pages are where everything is configured; the one setting that
matters is the machine's host — the IP or hostname of the display board. Prefer a
fixed IP: mDNS (`gaggimate.local`) does not resolve from inside a Docker bridge
network, and the firmware disables mDNS entirely when HomeKit is on.

**There is no configuration file, and no environment variable for any of it.**
A setting is what the Settings page saved, or the default this release ships;
those are the only two possibilities, and `GET /api/settings` says which of them
each key came from. Nothing is copied, nothing is edited before the first start,
and nothing you change in the UI reverts on restart.

The one exception is the handful of values the process needs *before* it can
open the database, which the image already sets and most people never touch:

| Variable | Default | What it is |
|---|---|---|
| `DATA_DIR` | `./data` (`/app/data` in the image) | Where the SQLite file and `backups/` live. |
| `HOST` | `0.0.0.0` | Bind address. |
| `PORT` | `8042` | Bind port; the healthcheck reads it too. The one value given at spawn — see the Quick start. |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`. |
| `LOG_JSON` | `true` | JSON log lines, or human-readable console output for development. |
| `WEB_DIST` | `<repo>/web/dist` | Where the built SPA is; the image sets it, a checkout does not need to. |
| `CORS_ORIGINS` | empty | Comma-separated origins allowed to call the API. Only the Vite dev server needs one. |

Each also accepts a `GAGGICLANKER_`-prefixed spelling (`GAGGICLANKER_DATA_DIR`)
for a box running several services. The image sets the ones a container needs.
`compose.yml`'s `environment:` block carries exactly one of them — `PORT`,
defaulting to 8042, because the app must bind before it can read a database, so
it is given at spawn (`PORT=9000 docker compose up -d --build`) and stored
nowhere. Everything else in that block is not configuration at all: it forwards
the variable names a boot refuses, so that a credential or the retired
device-writes switch left in an old `.env` or in your shell stops the container
instead of being silently ignored. There is a commented-out `LOG_LEVEL` line
there if you want a different log level.

**Upgrading from a release that read settings from the environment.** Before you
pull, store anything you had set — in an old `.env`, in `compose.yml`'s
`environment:`, or in the shell — in the database, because none of it is read any
more. A boot that still finds one of those variables set logs
`setting_env_ignored` with the names (never the values) and carries on with the
database's values. Two are refused rather than ignored: any variable that used
to carry a credential, and `GAGGICLANKER_DEVICE_WRITES_ENABLED`, which used to
open the only path from this box to the machine — the container exits naming it,
because silently ignoring either would leave the box less protected than its
owner believes. If `git pull` complains about your own `.env`, move it aside —
nothing parses one any more, however you run this: not the settings, not the
bootstrap values, not the credential check. A boot that finds a file called
`.env` beside it logs `dotenv_file_ignored` with its full path, so it is not
mistaken for configuration.

Compose is the one thing that still looks at such a file, and only in one narrow
way: it substitutes `${VAR}` in `compose.yml` from a `.env` in the project
directory, and `compose.yml` names exactly the variables a boot refuses. So a
stale `.env` holding `AUTH_USER`, a provider key or
`GAGGICLANKER_DEVICE_WRITES_ENABLED` still stops the container, loudly, with the
names in the log — while a stale `DATA_DIR` or `PORT` in the same file reaches
nothing.

**Typing the same value into the old Settings page and pressing Save does not
store it.** The form sends only the fields whose value differs from the one it is
showing you, and on the old version a value coming from the environment is
already the value shown — so Save sends an empty change and the value is lost at
the upgrade. Two ways round it, both on the old version, before you pull:

* in the UI: change the field to something else, **Save**, change it back to what
  you want, **Save** again; or
* store the values in one request:

  ```bash
  curl -X PATCH http://localhost:8000/api/settings \
    -H 'content-type: application/json' \
    -d '{"gaggimateHost": "192.168.1.50", "gaggimateTimeoutSeconds": 20}'
  ```

  Add `-H "Authorization: Bearer <token>"` if sign-in is on. Check what stuck
  with `curl -s localhost:8000/api/settings`: every key you moved should read
  `"source": "database"`.

  (Port 8000 because that is the old version's default; the new one is 8042.)

**The machine settings apply immediately.** Saving a new host, protocol, timeout
or the sync switch under Settings → Machine access closes the connection and opens the
new one, with no restart; the header pill follows within a few seconds. While a
sync (the profile list's write phase included) is using the machine, such a
change is refused with the reason and nothing is saved — wait for it to finish
and save again.

**Credentials never come from the environment.** The sign-in user and password,
and every LLM provider's API key or token, are entered in the Settings page and
live in the database only — and nothing else can supply one: the provider
clients ignore the SDKs' own key, header, organisation and base-URL variables,
profile files and `.netrc`, and a proxy is used only if it names no user or
password. The variables that used to carry a credential, or that the SDKs would
read one from, are refused: a boot that finds one set in the environment (in any
letter case), or a proxy variable with a user or password in it,
logs `auth_env_refused` with the variable names (never a value) and exits, rather
than start with authentication silently switched off. Empty values count as
unset.

### Authentication

Off until you turn it on, because the common case is a box on a home network
where the espresso machine itself has no auth, no TLS and no CORS —
gaggiclanker is already the strictest thing on that wire. Turn it on if this box
is port-forwarded, on a shared network, or behind a reverse proxy the internet
can reach.

It is configured in one place: **Settings → Authentication**. Set the password
first, then the username — the username is what turns the lock. The password
box posts the plain password to `POST /api/auth/password`, which hashes it with
argon2id on the server and stores only the hash. Nothing in the environment can
set, seed or override either of them. The settings API refuses to store a hash
directly (`authPasswordHash` is read-only there): a masked box labelled
"password hash" invites typing the *password* into it, and a stored value argon2
cannot verify is a credential that authenticates nobody.

A change of password — or of username — revokes every open session, including
the one that made the change. The tokens are the credential once they are
issued; leaving a thirty-day token working after a password change would be
theatre.

The switch is re-read on every request, so turning auth on from the Settings
page takes effect immediately with no restart.

If the stored hash is somehow not a hash, auth stays **on** and refuses every
sign-in, with the fix named in the log. A configuration mistake must never be
the thing that takes the lock off the door.

With it on, **every** route under `/api` needs a bearer token — the event
streams and the OpenAPI docs included. `/health` stays public, because a
healthcheck that needs a token is not a healthcheck, and so does the web app
itself; everything the app can actually *do* is an API call. Signing out revokes
the session on the server rather than only forgetting it in the browser, so a
token that leaked can be killed from any other tab. Five failed sign-ins from
one address buy a sixty-second lock.

```bash
TOKEN=$(curl -sX POST localhost:8042/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"barista","password":"something long"}' | jq -r .data.token)
curl -H "Authorization: Bearer $TOKEN" localhost:8042/api/shots
```

Auth is the boundary, not a full security posture: there is one user, no roles,
and no TLS of its own. Put it behind a reverse proxy that terminates TLS if it
is going to face the internet.

### Backup and restore

Everything is in one SQLite file under `DATA_DIR` (`./data` by default), so a
backup is a file copy and a restore is a file copy back.

```bash
curl -X POST localhost:8042/api/backup     # or Settings -> Backup in the UI
ls data/backups/
```

`POST /api/backup` uses SQLite's `VACUUM INTO`, so the copy is consistent and
can be taken while the app is running and syncing — which a plain `cp` of a
database with a hot WAL cannot promise. To restore:

```bash
docker compose down
cp data/backups/gaggiclanker-<timestamp>.db data/gaggiclanker.db
rm -f data/gaggiclanker.db-wal data/gaggiclanker.db-shm   # stale sidecars
docker compose up -d
```

Stop the app first. The `-wal` and `-shm` sidecars belong to the file they were
written next to; leaving them beside a restored database is how a restore
silently reinstates the state you were trying to undo. Copying the whole `data/`
directory while the app is **stopped** works too, and is the simplest thing to
put in a cron job.

The backup carries the signing secret for auth sessions as well, so restoring
one does not sign everybody out.

### LLM settings

A review, the chat and the drafts go through one provider, chosen with `GAGGICLANKER_LLM_PROVIDER`:
`claude_code` (the default — it runs the Claude Code CLI against a Claude
subscription, so there is no API key to buy), `anthropic`, `openrouter`,
`openai`, `ollama`, `lmstudio`, or `openai_compatible` for any other gateway.

Each provider has its own credential, entered under **Settings → LLM** and kept
in the database only, and none is ever lent to another: `llmApiKey` for the
OpenAI-compatible presets, `anthropicApiKey`, `claudeCodeOauthToken`. None of
them is read from the environment. For `claude_code` the token comes from
`claude setup-token` — an interactive `claude login` is not enough, because every
call runs the CLI with a scratch `HOME` and an environment that carries only the
stored token, so nothing on the box leaks into the prompt, and that hides
`~/.claude` too. The Docker image ships the CLI; a source checkout needs it
installed (`npm install -g @anthropic-ai/claude-code`) or `claudeCodeBin`
pointed at it. The image's CLI is pinned; to take a newer (or older) release
without rebuilding, the Claude Code panel under **Settings → LLM** installs the
`stable` or `latest` channel, or an exact version, straight from npm into the
data directory. The release is checked against the registry's sha512 and run
once before the app switches to it, it survives restarts, and it steps aside
once a newer image carries that release or a later one. It is used while
`claudeCodeBin` is left at `claude`; **Use the image's version** removes it.
Nothing updates on its own. **Validate credentials** tries what the form holds, so a pasted
token can be checked before it is saved, and for `claude_code` it makes one
one-word call to haiku: presence alone (`claude auth status`) cannot tell a
working token from a revoked one.

`modelDefault` is the default model; `modelReview`, `modelDraft`, `modelChat`
and `modelStartingPoint` override it per kind of call, and an empty value lets the
provider choose. `GAGGICLANKER_LLM_TIMEOUT_S` bounds one attempt, and
`GAGGICLANKER_LLM_RATE_LIMIT_RETRIES` is a process-wide budget: when the provider
throttles the account the whole app stops rather than failing every queued shot
in turn, and Settings → LLM has the button that starts it again. Settings →
Prompts edits the prompts themselves — they are rows in the database, seeded
from the YAML files in `gaggiclanker/prompts/`, and an edit takes effect on the
next call without a restart.

### Review

**Review** on a shot's page asks a model to read that one shot. It is one
structured call, started only by that button: no chat tool, batch, timer or
sync step starts one. The model is handed the shot's own information (every
line the shot tools can show, whatever you set under Settings → Shot
information, except your judgement, the note typed on the machine, the Set
version's recipe and which Set the shot is filed under), the profile the shot
brewed, the detected shot style, and the knowledge rules and reference excerpts
its telemetry selects. It is never shown your judgement, the Set, its versions,
another shot or an insight, so its taste prediction is blind.

It writes three things to the shot and nothing else: what it expects the cup to
taste like (sour, balanced or bitter; thin, medium or heavy body; low, medium
or high confidence), one paragraph describing what the telemetry shows and why,
with its figures, and a one-sentence summary. It proposes no change, no insight
and no question. The Review card shows the summary first, the prediction beside
your own balance, the description, the rules and excerpts it cited (each links
to the Knowledge page, which is how a rule that misleads gets found and turned
off), the model and the time. **Review again** writes a fresh one; the newest
finished review is the one shown and served, and the earlier ones stay stored,
each with exactly what it was told.

What a review writes is shot information: seven items in a **Review** group
under Settings → Shot information, all at the extended tier, so the chat reads
them through `get_shot_extended`, `get_shot_full` and `compare_shots` and you
can move them like any other item. The glossary and the chat's rules both say
what they are: a model's reading of one shot, weighed below the measured numbers
and your judgement, never on its own a reason to change a Set.

The knowledge rules are on the **Knowledge** page: a small tier of dial-in
heuristics — temperature by roast, the pressure matrix by roast and process,
ratio and time by style, what each diagnostic band means,
taste → suspect, telemetry → cause — each with its source and confidence, each
editable, each with a switch. They are adapted from
[gaggimate-barista](https://github.com/chall-tech/gaggimate-barista) (Charlie
Hall, MIT) by way of gaggimate-mcp; the attribution is in the seed file.

The call runs as a background task rather than inside the request: pressing the
button answers at once with a `running` row and the page follows the event
stream, so a restart cannot kill a review with the browser still waiting on
it. Pressing it twice while one runs gets the same run back rather than paying
for two.

A failed review is a stored row carrying the provider's error code rather than
an exception, and a run cut off by a restart is marked `interrupted` at the next
boot — neither silently disappears.

### The chat

The **Chat** page (`g c`) is the other half of the LLM layer, and it is the
opposite shape from a review: instead of one call with everything in front
of it, the model is given a set of tools and asks the archive its own questions.

**A folder is a scope, not a filing cabinet.** The list is **General** first,
then every Set you have not archived — including the ones nobody has asked about
yet, because an empty folder with a **New** button in it is how you start.
Conversations about a Set you have since archived move to a last folder of their
own and stay readable.

**Keep a conversation.** With a conversation open, **Download log** in its header
saves it as one JSON file (`chat-<id>-<title>.json`,
`GET /api/chat/threads/{id}/transcript`, sent with your sign-in like any other
call). It holds every message in order with its time and run, the tools the
agent called with their arguments, what they answered, and a record per run
with the model, its tokens and any error. It leaves out what you never typed:
the instructions and the context the app gives the agent each turn (the Set's
ledger and its recent shots, the shot glossary) are not stored with the
conversation and so are not in the file. The answer of a tool that renders
shots (`get_shot`, `get_shot_extended`, `get_shot_full`, `compare_shots`,
`list_set_shots`), or one the provider could not pair with its call, is reduced
to its size, since that is where whole shot renderings arrive; every other
answer, and every refused or failed one, is kept word for word. If the button
says the download was blocked before it reached the app, a content-blocking
extension dropped the request: allow this site in it.

A conversation in a Set's folder is about **one version of that Set** — the
change being argued — and it can see that Set and nothing else: its versions
with their predictions and outcomes, its shots, its spread, the knowledge base
and the confirmed insights that apply. It cannot run archive-wide SQL, list your
other coffees, or read a shot filed under another Set; a tool that would is not
offered to it, and a shot of somebody else's Set is refused in the same words
whether or not it exists. What it *can* do is propose how this version turned out, propose the next
version of this Set and record what has been learned about it — each a card
waiting for you to answer.

**A proposed change is a question, not a change.** The agent writes down one
change and what it expects that change to do; the Set stays exactly where it is
until you press **Accept**, and your next shot is still filed under the recipe
in the hopper. The card appears in the conversation and again above the
experiment log, with **Accept** and **Decline** on it, and it says what would
move, why, and what is predicted — *compared to v4, expect 3 to 5 s longer and
less sour*. Accepting records the change as the Set's next version, with that
prediction on it, and sends nothing to the machine. The accepted card says what
you do instead: for a grind, dose or yield change the profile on the machine is
already the right one, so there is nothing to push — *set the grinder to 1 and
brew; the next shots on this profile are filed under v2 by themselves*. A change
to another profile says to select it on the machine, and the agent can only
propose a profile the machine has (`list_profiles` says which); any other goes
through a profile draft. Declining creates nothing, and the note you leave is
what the next conversation is told.

The agent cannot dodge any of that. A change with no prediction is refused, and
so is one that moves two things at once unless it says why they cannot be
separated — and then it has to tell you the prediction cannot say which of them
did anything. While the current version's own prediction is still ungraded it
cannot propose the next change at all: it grades that one with you, or asks for
another shot on the same recipe, which needs no version. Only one proposal waits
at a time.

**An insight belongs to the Set it was learned in.** What the agent learns in a
Set's conversation is stored with that Set and the version the conversation was
about, shown as a card in the chat with **Add** and **Dismiss**, and listed on
that Set's page under the version it was learned at (waiting ones with their
buttons, added ones with **Take back**). Added, it is told to that Set's later
conversations and to no other Set's, even one on the same bean and grinder; the
design chat of a new Set and Review never see it. Nothing waiting or dismissed
reaches a prompt. The Knowledge page holds general knowledge only: what you write
by hand there, and any agent-written one the app could not place on a single Set;
those keep matching by bean, grinder, roast and process and still reach every
conversation whose Set they match. The first start of this release placed the
agent-written insights you already had about one coffee on the one Set each fits
(its scope names a bean, or is empty with evidence shots; it matches that Set and
one of its evidence shots is filed there); anything scoped only by equipment, roast,
process or origin, and anything that fits none or several Sets, stayed general.

**What an insight rests on, and what happens when it ages.** An insight names the
shots it comes from and the versions of its Set whose recorded outcome it depends
on. The Set page and the chat card show each version with its outcome **as it
stands now**, and when you re-recorded or cleared that outcome after the insight
was written they show the old one beside it with a "changed since" mark ("held →
failed", "no outcome now"); the outcome it had when written is never rewritten. An
insight has to rest on at least one shot or one version with a recorded outcome.
The agent is told each added insight as a line with its number, the version it was
learned at, what it rests on and its shots, and is asked to weigh an old insight,
or one resting on a changed outcome, below the current shots. Nothing expires by
itself: **only you remove an added insight.** When the shots contradict one, the
agent proposes its **replacement** (a card showing the old text above the new, "Adding
it deletes the old insight"): your Add adds the new one and deletes the old one in
one step, and if the old one changed in the meantime the new one is added on its
own and the card says so. When one is no longer supported and nothing replaces it,
the agent proposes **deleting** it with its reason (a card with **Delete** and
**Keep**; new tool `propose_insight_deletion`, Set conversations only). Until you
press Delete the insight stays and is still told to every conversation. At the start
of a new version's conversation the agent is asked to look over the added insights
and propose replacing or deleting the few the shots no longer support, never as a
sweep of all of them. Removed means removed: nothing is kept as a retired state, a
history row or a restore; only the conversation that proposed the removal still shows
the text on its card and is told what you did. Deleting a conversation also deletes
the insights it proposed that you dismissed (they existed only to stop it offering
them again); waiting and added ones stay.

**A proposal stops waiting the moment you change the Set another way.** Record a
version on the form, roll back, make a profile proposal active for the Set — whichever it
is, a change that was argued against the
recipe you have just left is retired unanswered rather than sitting there with
an Accept button that could only refuse. The log says so, and the next
conversation is told, so the agent can propose afresh against what you are
brewing now. **Accept and Decline are yours**: there is no tool, in the chat or
over MCP, that reaches either, and a test walks the registry and the tool
package's own bytecode to keep it that way.

A profile change is a change to the recipe too — the temperature and the
pressure curve are as much of it as the grind — so a draft proposed inside a
Set's conversation carries a prediction and obeys the same rules. It still lands
on the Profiles page as a proposal inside its profile: you read the diff and make it active
(one click, which approves it), and the prediction is recorded on the Set when the sync puts
the proposal you made active for that Set on the machine.

A conversation in **General** is the other way round: the whole archive,
read-only. It runs SQL over the curated views, compares shots across Sets and
drafts a profile, and sends a bag with no Set yet to New Set's design path — and it
cannot change a Set, because a change to a Set is an argument that belongs in
that Set's own room, where the ledger and the evidence are in front of the
model. It will tell you which folder to open.

**One conversation per change.** New inside a folder starts a fresh one on the
Set's current version and it stays on that version afterwards, so a folder reads
as a history of what was argued rather than a pile of rooms all claiming to be
about today's recipe. Rows are labelled `v6`, and a version a later roll back
stepped over is muted and says *dead end*. **Discuss in chat** on a Set, the
**Chat** link on every entry in the experiment log, and the Set's button in the
**Chat about** bar above the shots table open or continue that version's
conversation with the question already typed — press any of them twice and you
land in the same room. **Accept** on a card in the conversation also tells
the agent, as your next message ("Accepted: your proposed change (Grind 2 → 1)
is now version 2 of this Set"), and it
answers by sending you to a new conversation for the new version: the one you
are in stays with the version it was opened on. **Decline** tells it too, with
your reason ("Declined: the dose is not the problem", or "Declined: no reason
given."), and it answers in the same conversation without sending that card
again. Answering on the Set page tells no conversation.

**The agent is handed the experiment before it says a word:** the Set and the
recipe, every version with what changed, what was predicted, against which
version and how it turned out, the track record, how much this Set's shots vary
when nothing changed, the evidence table this version's prediction is graded on,
this version's newest shots (twenty by default, `chatRecentShots`) with the
discards marked, the Keep shots that are the target, and the insights you have
confirmed. So the first turn is about the coffee rather than about learning what
Set 3 is. Beside the composer, the page lists exactly what the agent can do in
*this* conversation.

**Every shot is told in two tiers, and every field is explained.** The
*base* information of a shot — what it is and where it is filed, its outcome,
the headline diagnostics and your judgement — is what the agent sees for every
shot in the opening context and in its search; the *extended* information — the
execution score's working, the temperature, pressure and flow statistics, every
channeling indicator, profile compliance, one line per phase and the curve —
is what it asks for, one shot at a time. The curve comes as about sixty rows
(`chatCurvePoints`) chosen to keep its shape, and it always keeps the moments
the diagnostics are about: each phase's start and end, peak pressure, first
drip and the largest pressure drop. A value the machine did not
record (no scale, no pressure sensor) is left out, never shown as zero. The
agent's instructions carry a glossary of every field it can be shown: what it
measures, its unit, which way is better, and every band label with the threshold
behind it, read from the diagnostics engine itself.

**Which item sits in which tier is yours to choose**, under **Settings → Shot
information**: one table per group, each item with what it means, a base |
extended | excluded control and its value on your newest judged shot, written by
the same renderer the agent reads. An excluded item is left out of the opening
context, the shot tools, the search and the glossary; a General chat's SQL tool
can still read the archive's views. Shot id, Set version and whether a shot counts stay in base, since
the agent cannot search or cite without them. The page shows the cost in
approximate tokens (base and extended per shot, the curve at `chatCurvePoints`,
the glossary, and the opening context's shots at `chatRecentShots`), a change
applies from the next turn, and
**Reset to defaults** puts every item back. Only the items you moved are stored,
so an item a later release adds arrives at its default.

**Seventeen tools in a Set's conversation** — twelve reads and five that propose:
how this conversation's version turned out (`propose_outcome`), waiting for you
to accept, record another outcome or dismiss; one change to this Set, waiting
for you, with the prediction that makes it gradable (the grind, the dose, the
yield or the profile: a temperature change is a profile change, and the tool
says so); an insight about this Set stored **unconfirmed** that reaches no
future prompt until you add it (naming the shots and versions it rests on, and the added
insight it replaces, if any); the deletion of one added insight, waiting for you to
delete or keep it; and a
profile draft that goes through the same schema, safety-policy and clamp checks
as one typed by hand. One of the reads is `get_profile`, a profile version's
whole document, so the agent reads the profile it is about to change, and three
read one shot: `get_shot` its base information, `get_shot_extended` the rest and
`get_shot_full` both, beside `list_set_shots`, which searches the Set's shots on
their base information (filters, ranges, bands, a sort, at most ten back).
**Seventeen in General** — sixteen reads and one proposal, the profile draft. A
new bag is not worked out there: the General chat sends you to New Set →
**Design it with the agent**, whose conversation ends in a first recipe you
accept. **Eight while a Set is being designed** (below). The registry holds
twenty-three in total: twelve both kinds have, ten that belong to one kind or the
other, and `propose_initial_recipe`, which only a design has. None of them
starts a shot's review: only its button does. Nothing in the chat can touch
the machine — making a profile active stays a button you press.

**A Set can be designed in its own conversation.** `POST /api/sets/design`
takes a bean and a grinder (both required), optionally a profile to fork, your
usual grind on that grinder and what you want from the coffee, and creates a
Set whose version 1 has no recipe yet, with that version's conversation open.
While the Set is being designed the agent is told what you asked for, the
profile to fork in full, how this bean went in your other Sets, similar Sets on
this grinder and the matching rules; it asks what it needs and then proposes
**the initial recipe** with `propose_initial_recipe`: a profile of its own
(never a copy of one in the library — two Sets on one profile make the shot
matcher ambiguous) plus grind, dose and yield, as one card you accept or
decline. The profile starts from the one you picked to fork, or, when you
picked none, from nothing: the agent writes it whole, and never builds on a
profile of yours you did not choose. A newer card replaces the waiting one. Accepting fills
version 1 in place and ends the design conversation: the agent is told, and
tells you to talk version 1's shots through in a new conversation (Discuss in chat on
the Set page opens one rather than the design); the profile waits on the
Profiles page for you to make active. Writing a version by hand, or making a proposal active for the Set, ends the design
the same way. A design nobody brewed anything under can be discarded
(`DELETE /api/sets/{id}/design`).

Every answer shows what was called, with the input and the output one click
away, and citations are links: a shot id goes to the shot, a knowledge passage's
heading path goes to the passage. A turn is bounded by `chatMaxToolRounds` and
`chatMaxToolCalls`; the Stop button cancels a run mid-answer, and the transcript
survives a reload because the stream is replayed from the database rather than
held in the tab.

### A starting point for a new coffee

**New Set** is one form: the bean, a name, the grinder, the profile version, the
recipe and an optional intent. Picking a profile fills the target yield from its
largest volumetric stop, never over a number you typed, and shows the
temperature it brews at beside the recipe — that one is the profile's to state,
not yours to type.

Open a coffee nobody has brewed and **Suggest a starting point instead**, folded
under that form, answers the question you actually have. It reads the bean and
grinder already picked and shows what this archive has already brewed on
*this grinder* that resembles it — same roast level, same process, same
origin — with how each one went: shots, mean rating, mean execution score, ratio
and time. That half is one SQL query, costs nothing, and is worth reading on its
own. A recipe with no shots behind it is never offered: it records an intention,
not a result.

Press **Ask for suggestions** and the model turns that plus the rule tier into
three complete first recipes — conservative, recommended, adventurous — each
with a grind, a dose, a yield, a temperature, a profile and a rationale citing
what it leaned on.

An option's temperature has to be true once you take it, and only a profile can
make it so. When an option points at a profile you already have and suggests a
temperature that profile does not brew at, the card says that taking it will
**make a draft** of that profile at the suggested temperature, and the new
Set's first version points at the draft. Nothing is sent to the machine: you
make it active from the Profiles page, exactly as you would any other
proposal, and the next sync puts it on the machine.

It will not invent a grind number. A grinder's scale is arbitrary and there is
no conversion between two of them, so a figure on your dial is offered only when
your usual setting or a past Set on the same grinder anchors it; otherwise the
answer is relative and the card says so.

Taking one creates the Set with `origin=starting_point`, and — when the option
authored a whole profile rather than pointing at one you already have — a proposal
waiting on the **Profiles** page. Nothing is pushed; you make it active. The Beans
page has the same shortcut for the coffee you are looking at. The chat does not
ask for starting points: a run it started had nowhere to be taken, and
designing a Set with the agent (below) is the conversation for a new bag.

### Designing a Set with the agent

The third path in **New Set** is **Design it with the agent**, folded under the
form like the suggestion. It is for a new profile rather than a first guess: a
fork of one you have, or a bean you already brew in another Set. It uses the
bean, name and grinder from the form, reads the form's profile as the one to
fork from, and asks only what the form does not: your usual grind on that
grinder and what you want from the coffee. The grinder is required here — a
grind is only a number on one grinder's dial — so pre-ground coffee keeps the
plain form and the suggestion. The form's grind, dose, yield and intent are not
sent: working those out is what the conversation is for.

**Start designing** creates the Set with an empty version 1 and takes you to
that version's conversation, with what you wanted as the first message (or
"Help me design this Set." when you left it empty, so the agent starts by
asking). The agent already has your brief, the profile to fork, this bean's
other Sets and similar Sets on this grinder; it asks what it needs, then
proposes the whole first recipe as one card — a profile of its own, the grind
(said to be relative when nothing anchors a number on your dial), the dose and
the yield. Nothing exists until you accept it, in the conversation or on the
Set page. Accepting makes it version 1 and ends the design: the agent tells you
to start a new conversation about the shots, since one conversation is one
version; the profile is then a proposal on the Profiles page for you to make active,
and once a sync has put it on the machine, shots brewed on it are filed under the new Set.

Until then the Set carries a **Designing** badge on the Sets list, on its page
and on its folder in the Chat page, with **Continue designing** back into the
conversation. Recording a version by hand on the Set page ends the design the
same way. **Discard design** on the Set page deletes a design nobody brewed
anything under, after asking; one with a shot filed on it is kept.

The conversation needs an LLM provider (Settings → LLM); everything up to it,
and the card, work without one.

### The chat's database tool (MCP)

The `claude_code` provider runs the chat's tool loop inside the Claude Code CLI,
and the CLI calls tools only through an MCP server. So for each chat turn it
starts `gaggiclanker mcp` as a child process over stdio, pointed at the same
`DATA_DIR` and told which conversation it is serving, and the model gets exactly
the tools the chat has with any other provider — the Set's tools inside a Set's
folder, the archive's in General. That server is internal to the chat: it opens the database and nothing
else — no network endpoint, no machine connection, no setting — and like every
tool it only reads the archive or proposes something a person confirms. The API
providers call the same tools directly and never start it.

The command stays available so the provider can spawn it, and it deliberately
runs no migrations, because a second process migrating a database the
application is also using is a race: start the server once first. There are no
device-write tools and no switch that adds any: the machine is written only by
gaggiclanker's own HTTP API, behind buttons a person presses.

## Troubleshooting

**The device pill never goes green.**
Check the host under Settings → Machine access first: `curl http://<host>/api/status`
should answer a small JSON document. Then check you are not out of WebSocket
slots — see below.
`GET /api/device/status` reports what the client thinks, and the container log
carries a `device_connection_failed` line with the actual error on every
attempt.

**`gaggimate.local` works in my browser but not here.**
mDNS does not resolve from inside a Docker *bridge* network, which is why
`compose.yml` defaults to host networking. The firmware also turns mDNS off
entirely when HomeKit is enabled, so the name can be dead everywhere. Use the IP,
and give the machine a DHCP reservation so it stays the same one.

**The machine's own web UI stops working when gaggiclanker is running.**
The firmware allows **three** WebSocket clients in total and evicts the *oldest*
when a fourth connects — it does not refuse the newcomer. gaggiclanker holds
exactly one, the machine's own browser UI is another, so a second browser tab is
the third and anything after that starts evicting. Close the spare tabs. If you
want the machine left alone entirely, set
`GAGGICLANKER_DEVICE_SYNC_ENABLED=false` and use the archive you already have.

**Everything under `/api/history` returns 503.**
The machine is doing an OTA update. It is not an error and it is not lost: the
sync records the failure and nothing is half-written. Wait for the update to
finish and sync again.

**Shots appear with no pressure and no flow.**
Those are zero on **Standard** boards — the sensor is a Pro part. Every
pressure-derived diagnostic is gated on the board's capability flag, so the
curves are honest rather than flat lines pretending to be data. Weight needs a
BLE scale paired with the machine; without one there is no `final_weight_g`.

**A shot is listed as quarantined.**
Its `.slog` did not parse. The raw bytes are stored anyway — that is the whole
point — so the shot is recoverable once the parser learns the version it is in.
`GET /api/shots/{id}/raw` downloads exactly what the machine sent. Please open
an issue with that file attached.

**A short shot never appeared at all.**
The firmware discards anything under 7.5 seconds and never records the utility
profiles (backflush, flush). Nothing here can see a shot the machine did not
save.

**"unable to open database file" on the first `docker compose up`.**
Docker created the bind-mount source as `root:root` and the app runs as uid
1000. The image's entrypoint normally fixes this itself; if you have overridden
`user:` in compose, either `chown $(id -u):$(id -g) ./data` or set `APP_UID` and
`APP_GID` to your own.

**A review spins for ever / says `interrupted`.**
`interrupted` means the process stopped mid-call — a restart, an OOM, a power
cut — and the next boot said so rather than leaving a spinner. Press the button
again. If it fails instead, the row carries the provider's error; a rate limit
latches the whole app on purpose and Settings → LLM has the button that
clears it.

**I am locked out after mistyping the password.**
Five failures from one address lock that address for sixty seconds, and each
further attempt extends it. Wait a minute. If you have genuinely lost the
password, delete the stored hash, which turns auth off on the very next request
(no restart):

```bash
docker compose exec gaggiclanker python -c \
  "import sqlite3;sqlite3.connect('/app/data/gaggiclanker.db').execute(\
   \"DELETE FROM settings WHERE key='authPasswordHash'\").connection.commit()"
```

then set a new password under Settings → Authentication. The username is still
set, so auth is back on the moment the new password is stored.

**Sign-in says "the stored password hash is not an argon2 hash".**
Something wrote a password, not a hash, into the `authPasswordHash` row by hand.
Auth is on and refusing everybody, which is the correct thing for a broken
credential to do. Delete the row as for a lost password above, then set the
password you want under Settings → Authentication.

**The container exits at boot with `device_writes_env_refused`.**
`GAGGICLANKER_DEVICE_WRITES_ENABLED` is still set — in `compose.yml`, in a
leftover `.env` compose passes through, or in the shell that started it. It no
longer allows anything: writes to the machine are switched on with the **Writes**
switch in the top bar and the switch lives in the database. Remove the variable, start the
app, and use the switch if you want writes on. The boot refuses rather
than ignoring it because a switch that used to open the only path to your
espresso machine must not change meaning quietly.

**The container exits at boot with `auth_env_refused`.**
A credential variable is still set — one of the old sign-in variables, or an LLM
provider's API key or token — in `compose.yml` or in the shell that started it. The log line names which. Remove them, start the app, and enter
sign-in under Settings → Authentication and the key under Settings → LLM. An
install that had
sign-in configured only through the environment starts with auth off until you
set it again, so do that first if the box is reachable from outside.

## Documentation

* [`docs/architecture.md`](docs/architecture.md) — what the pieces are and why
  they are arranged this way.
* [`docs/device-gotchas.md`](docs/device-gotchas.md) — the twelve firmware
  behaviours that shaped the sync engine. Read before touching `device/` or
  `sync/`.
* [`docs/safety-layers.md`](docs/safety-layers.md) — what can harm the machine,
  and the four layers that will guard a write when there is one.
* `CHANGELOG.md` — what is in each release.

## Contributing

[`docs/architecture.md`](docs/architecture.md) holds the conventions — the
response envelope, pydantic on every database write, repro-first bug fixing,
the migration rules and the list of device gotchas worth knowing before
touching the sync code. [`CHANGELOG.md`](CHANGELOG.md) has what landed in each
release.

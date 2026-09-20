# gaggiclanker

A self-hosted archive and analyst for a [GaggiMate](https://gaggimate.eu)
espresso machine. Press a button and it pulls in every shot the machine holds —
raw `.slog` bytes, every sample, phases, the device's own notes and profiles —
shows them with curves and deterministic diagnostics, lets you judge each shot
from the list and group shots into versioned **Sets** (bean + hardware + profile
+ grind/dose/yield), and runs a per-shot LLM analysis through whichever provider
you point it at.

It does not watch the machine. Nothing leaves the GaggiMate until you ask for
it: the machine's own web UI already draws the shot that is happening now, and
what an archive is for is the hundred before it.

The machine holds a few hundred KB of flash and deletes old shots when it runs
low. This is the thing that remembers them.

> **Status: 0.1.0, the prototype, plus profile drafts and push.** Everything in this README
> works: sync, the shots UI, Sets and judgement, the LLM layer, the per-shot
> analyzer, optional authentication and the container. It can now
> also put a profile *on* the machine — as a new `[AI]`-suffixed file, never
> over an existing one, never selected for you, behind a switch that is off by
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
3. **Press "Pull from machine"** on the Shots page. The first pull walks the
   machine's whole history, which for a few hundred shots takes a minute or
   two; when it finishes it says what it archived. Shots under 7.5 seconds
   never appear: the firmware discards them. After that, pull whenever you have
   pulled some coffee — or drop exported files on the strip under the header,
   which is the only way back for shots the machine has already deleted.
4. **Turn on authentication** if this box is reachable by anything you do not
   trust — see below. Off by default, and that is the right default for a
   machine only your own LAN can reach.
5. **Set up the model.** The analyzer needs one provider. The cheapest to get
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
| **Shots** | `g s` | The archive: the list, the filters, one shot with its curve and diagnostics. A row opens in place with its curve, a quick judgement (rating, balance, aroma and taste notes from the flavour wheel, a line of notes; every click saves) and the machine's notes. Each row's Decision column records Keep, Improve or Discard; the Set column can be dragged narrower. An analysis is started from the shot page. The filters narrow by date, profile, Set, score, rating, source and readability; a Set's experiment log links each version's shot count straight at *that version's* shots, and the filter says which version is on and removes it in one click. The pull button and the import drop zone are both here. |
| **Chat** | `g c` | The tool-using conversation, in a folder per Set. New inside a folder starts one already pointed at that Set. |
| **Sets** | `g e` | Bean + hardware + profile + recipe, versioned, with the trend across versions and the experiment log: what each version changed, what you predicted it would do, how its shots were labelled, and whether the prediction held. One click rolls an old recipe back. |
| **Beans** | `g b` | The coffees: roaster, origin, process, roast level, decaf and a free-form description. Roaster and origin suggest the values already recorded; a coffee is archived when you stop buying it, and one no Set uses can be deleted. |
| **Hardware** | `g h` | The machine — what it says it is, and the name and notes you give it — and the grinders. |
| **Taste wheel** | `g w` | The SCA/WCR Coffee Taster's Flavor Wheel, all three tiers. Pick which of its notes the shot panel offers, one list for taste and one for aroma. |
| **Profiles** | `g p` | What is on the machine, what is staged for it, and every version a shot can resolve to. |
| **Sync** | `g y` | Every exchange with the machine that you start: pull from it, send your judgements to its notes cards, clean up its storage, and the record of every write. The only place anything but a profile is written to or deleted from the machine. |
| **Device** | | What the machine is: its versions and its connection. The status pill in the header leads here too. |
| **Settings** | `g ,` | One page of collapsible cards per heading, the ones you must fill in first: Machine access and LLM, then Authentication, Prompts, Profile safety, System and Import. The LLM page also holds the analysis and chat budgets. |
| **Knowledge** | `g k` | Under Settings. The dial-in rules, the prose documents, and the insights learned from your shots; insights an analysis proposes are confirmed on that analysis. |

The sidebar folds. The button at the foot of it, or the `[` chord, collapses it
to an icon rail and back; the choice is remembered in the browser. Folded, every
entry keeps its name — a tooltip for a mouse, the accessible name for everything
else — so nothing is lost but the fourteen rems.

The old import page is now the drop zone on the Shots page, and the old drafts
page is the staging section of Profiles; `/import` and `/drafts` still resolve,
by redirecting. The device page's old storage, notes, sync and writes anchors
redirect to the Sync page.

### The experiment log

A Set page is a record of experiments, one per version. A version's recipe is
five things: the **profile** version, the grind as text and as a number, the
dose and the target yield. A change of profile is recorded by hand from **Change something** on the Set page, or on
its own when a staged profile is pushed for that Set. Recording one there sends
nothing to the machine — that is still the Profiles page, and still you — but it
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

Nothing, until **Device writes enabled** is on under Settings → Machine access. With it
on: **profiles may be pushed by the app** (see below); **everything else written
to or deleted from the machine happens only from the Sync page, by a person.**

* **Send notes to the machine** lists the judgements the machine's notes cards
  do not have yet. Tick the ones to send and confirm; nothing is ticked for you.
  Saving a judgement never sends it. A card edited on the machine more recently
  than your verdict is left alone, and a verdict that came from the machine and
  was never edited is never sent back. `notesWritebackFields` picks the fields.
  Taste and aroma notes stay here: the machine's notes card has no field for
  them, and the balance goes as its own sour/balanced/bitter.
* **Clean up the machine's storage** shows the plan your cleanup policy
  (`deviceCleanupMode`) proposes: which shots would be deleted and why, and which
  are kept and why. Confirming deletes exactly those shots, oldest first — if the
  plan changed since you looked, nothing is deleted and you are asked to look
  again. There is no undo on the machine; the archive keeps every shot.

Nothing runs either on its own: a pull never deletes anything, and there is no
timer. Every attempt, refused ones included, is listed under **Recent writes**.

### Putting a profile on the machine

Nothing reaches the machine without passing through the **Staged for the
machine** section of the Profiles page. A version gets there in one of three
ways: **Stage as is**, for a profile that is already right and only needs to be
on the machine; **Edit**, which opens the JSON editor and validates what you
type against the strict schema and the safety policy; or an analysis, the chat
or the starting-point wizard proposing one, which lands in the same place with
the same buttons on it.

A staged profile is then approved and pushed, and the push is refused before
anything reaches the wire unless **Device writes enabled** is on. It is always
saved as a new profile with an `[AI]` suffix, never over an existing one and
never selected for you; what came back off the machine is compared against what
was sent, and a mismatch offers a rollback. `docs/safety-layers.md` is the whole
contract.

Profile exports can be uploaded straight into the library with **Upload
profile** in the Profiles header — it runs them through the same importer as the
shots page, so the new version appears below with its own staging button.

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

It holds the fixture archive, so pressing "Pull from machine" against it fills
the UI with real shots and real curves.

Or seed the archive from files, with no machine at all. The web UI on the
display exports a shot as `shot-<id>.json` and a profile as `profile-<id>.json`;
those files are the only way back for a shot the machine has already deleted,
and the importer reads them into the same tables the sync engine writes.
Importing first and connecting the machine afterwards is an ordinary order to do
things in: the archive holds one machine, it exists before the first pull, and a
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
    -d '{"gaggimateHost": "192.168.1.50", "deviceCleanupMode": "keep_newest"}'
  ```

  Add `-H "Authorization: Bearer <token>"` if sign-in is on. Check what stuck
  with `curl -s localhost:8000/api/settings`: every key you moved should read
  `"source": "database"`.

  (Port 8000 because that is the old version's default; the new one is 8042.)

**The machine settings apply immediately.** Saving a new host, protocol, timeout
or the sync switch under Settings → Machine access closes the connection and opens the
new one, with no restart; the header pill follows within a few seconds. While a
profile push, a cleanup run, a notes send or a pull is using the machine, such a
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

Per-shot analysis goes through one provider, chosen with `GAGGICLANKER_LLM_PROVIDER`:
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
`~/.claude` too.

`GAGGICLANKER_MODEL` is the default model; `..._MODEL_ANALYSIS`, `..._MODEL_DRAFT`
and `..._MODEL_CHAT` override it per kind of call, and an empty value lets the
provider choose. `GAGGICLANKER_LLM_TIMEOUT_S` bounds one attempt, and
`GAGGICLANKER_LLM_RATE_LIMIT_RETRIES` is a process-wide budget: when the provider
throttles the account the whole app stops rather than failing every queued shot
in turn, and Settings → LLM has the button that starts it again. Settings →
Prompts edits the prompts themselves — they are rows in the database, seeded
from the YAML files in `gaggiclanker/prompts/`, and an edit takes effect on the
next call without a restart.

### The analysis

One structured call per shot. It is handed the diagnostics with their band
labels, the Set (the bean with its roast level and process, the grinder with its
own step unit, the profile JSON, the grind/dose/yield targets), the previous five shots in the same
Set with your verdict on each and the advice that followed them, your verdict on
this one — marked as ground truth for taste — and the knowledge rules that match.
It answers with a diagnosis and prioritised suggestions, and accepting one
records a new Set version with that single field changed, so "did following the
advice help" is a question the trend chart answers. Three of them can be
accepted that way — grind, dose and yield, the numbers a Set version records.
Advice about the temperature, the pressure, the flow or the pre-infusion is
about the profile: the refusal says so and points at drafting one from the
analysis, which you then review and push yourself.

The knowledge rules are on the **Knowledge** page: a small tier of dial-in
heuristics — temperature by roast, the pressure matrix by roast and process,
ratio and time by style, what each diagnostic band means,
taste → suspect, telemetry → cause — each with its source and confidence, each
editable, each with a switch. The model is asked to name the rules it used and
the analysis links them back here, which is how a rule that misleads gets found
and turned off. They are adapted from
[gaggimate-barista](https://github.com/chall-tech/gaggimate-barista) (Charlie
Hall, MIT) by way of gaggimate-mcp; the attribution is in the seed file.

The call runs as a background task rather than inside the request: pressing the
button answers at once with a `running` row and the page follows the event
stream, so a restart cannot kill an analysis with the browser still waiting on
it. Pressing it twice, or in two tabs, gets the same run back rather than paying
for two.

A failed analysis is a stored row carrying the provider's error code rather than
an exception, and a run cut off by a restart is marked `interrupted` at the next
boot — neither silently disappears. Token usage is recorded per analysis; the
`cost_estimate` column stays empty until there are pricing tables to fill it
from, because a made-up number in a money column is worse than a blank one.

### The chat

The **Chat** page (`g c`) is the other half of the LLM layer, and it is the
opposite shape from the analysis: instead of one call with everything in front
of it, the model is given a small set of tools and asks the archive its own
questions. It can run read-only SQL over a curated set of views, read a shot's
curve, compare shots, walk a Set's versions, search the knowledge base, and read
what this box has learned about this kitchen.

A conversation scoped to a Set starts with that Set's recipe, its recent shots
and the confirmed insights that apply, so "how is it going?" is a question with
an answer. Which is why the conversation list is a **folder per Set** rather
than one chronological run: **General** first for questions about nothing in
particular, then every Set you have not archived — including the ones nobody has
asked about yet, because an empty folder with a **New** button in it is how you
start. New inside a folder creates the conversation there and then, already
pointed at the right archive; a folder opens itself when it holds the
conversation you are reading. Conversations about a Set you have since archived
move to a last folder of their own and stay readable. The **Discuss in chat**
button on a shot and on a Set opens that Set's folder with the question already
typed, and the first question lands in that Set.

Nineteen tools, and fourteen of them only read. The other five are `propose`:
they either write something you still have to decide about — a new Set version
with `origin=chat` (the grind, the dose, the yield or the profile: a
temperature change is a profile change, and the tool says so), a profile draft
that goes through the same schema, safety-policy and clamp checks as one typed
by hand, an insight stored **unconfirmed** that reaches no future prompt until
you confirm it — or they queue work that spends provider tokens, which is why `run_analysis` and
`starting_point` are in that class rather than filed as reads. Both are rate
limited on the same bucket as the routes they shortcut. Nothing in the chat can
touch the machine — pushing a profile and deleting a shot off the display stay
buttons you press.

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
**stage a draft** of that profile at the suggested temperature, and the new
Set's first version points at the draft. Nothing is sent to the machine: you
approve and push it on the Profiles page, exactly as you would any other
draft.

It will not invent a grind number. A grinder's scale is arbitrary and there is
no conversion between two of them, so a figure on your dial is offered only when
your usual setting or a past Set on the same grinder anchors it; otherwise the
answer is relative and the card says so.

Taking one creates the Set with `origin=starting_point`, and — when the option
authored a whole profile rather than pointing at one you already have — a draft
staged on the **Profiles** page. Nothing is pushed; you approve it. The Beans
page has the same shortcut for the coffee you are looking at, and the chat can
ask through the `starting_point` tool.

### The chat's database tool (MCP)

The `claude_code` provider runs the chat's tool loop inside the Claude Code CLI,
and the CLI calls tools only through an MCP server. So for each chat turn it
starts `gaggiclanker mcp` as a child process over stdio, pointed at the same
`DATA_DIR`, and the model gets exactly the tools the chat has with any other
provider. That server is internal to the chat: it opens the database and nothing
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
pull records the failure and nothing is half-written. Wait for the update to
finish and pull again.

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

**An analysis spins for ever / says `interrupted`.**
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
longer allows anything: writes to the machine are switched on under **Settings →
Machine access** and the switch lives in the database. Remove the variable, start the
app, and set the switch there if you want writes on. The boot refuses rather
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

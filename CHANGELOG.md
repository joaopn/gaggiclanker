# Changelog

Notable changes per release. Dates are the day the release was cut.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the versions are [semantic](https://semver.org/). Until 1.0 the database schema
may change between releases; migrations are forward-only and run at boot, so an
upgrade is `docker compose pull && docker compose up -d` — but take a backup
first (`POST /api/backup`), because there is no down-migration.

## [Unreleased]

### The chat's conversations, in a folder per Set

- **A folder per Set** replaces the chronological list: General first, then
  every Set you have not archived — including ones with no conversation yet —
  and a last folder for conversations about Sets you have archived. A folder
  opens itself when it holds the conversation you are reading, or when a
  **Discuss in chat** link names its Set.
- **New inside a folder** creates the conversation there and then, already
  scoped to that Set. The "Scope a new conversation" select under the list is
  gone: it was the thing that scoped a conversation, and it overflowed the
  card it lived in.
- With nothing selected, the composer's card says where a first question will
  go — "A new conversation in <Set>" or "A new general conversation".

### The Set page is an experiment log

- **A version can change the profile.** "Change something" now offers a
  Profile field, first and preselected to the one the Set is brewing with, so
  switching profiles — or editing one on the machine — is something you can
  record. Without it those shots landed in "needs a Set", because a shot joins
  its Set by the profile it was pulled with. Picking one carries its target
  yield and temperature across, never over a number you typed. It sends
  nothing to the machine: putting a profile there is still the Profiles page.

- **A version prediction.** A Set version can say what you expect it to do
  differently and which earlier version that is against — the parent by
  default, or nothing at all if you would rather grade it on the numbers the
  version itself states. It is optional, and it can only be written **before
  the version's first shot** and before its prediction has been graded: one
  typed after the cup was tasted grades itself, so the app refuses it with a
  message saying why.
- **An outcome.** Once a version has a prediction and a shot you labelled Keep
  or Improve, you grade it: held, partly held, failed or inconclusive, with a
  line saying why. A grade can be changed or taken back at any time.
- **The track record.** Above the log: "6 of 10 predictions held", with how
  many are still open and how many versions predicted nothing.
- **Roll back to an earlier version.** One click, with a confirm step,
  appends a new version whose recipe is the old one's. Versions that are no
  longer on the line you are brewing — read backwards from the current version,
  through what each roll back restored — are marked dead ends and muted, still
  fully readable. When the current version has shots you want to improve on and
  none you kept, the page offers the way back to the last version you did keep
  shots from. **Nothing is sent to the machine**, even when the restored version
  names a different profile.
- **Each version shows how its shots were labelled** — "2 Keep · 1 Improve" —
  beside the shot count, and that count links into the shots list narrowed to
  **that version** rather than to the whole Set. The filter panel says which
  version is on ("Guji on the Niche v5") and removes it in one click; changing
  or clearing the Set drops it.
- **The prediction is hidden while you judge a shot.** On the shot page and in
  the shots list's panel, a shot whose version predicted something says so but
  does not show the words until the shot carries a decision. "Show prediction"
  reveals it for that view only; nothing is remembered.
- **The chat can read the ledger.** `v_set_versions` carries the prediction,
  what it is compared to, what a version restores and the outcome.

### A judgement for every shot, on the flavour wheel

- **Quick judgement under a shot row.** The panel that opens under a row is no
  longer the full form with a Save button. It holds what you can fill in for
  every shot in a few clicks: rating, balance, aroma notes, taste notes and a
  line of notes. Every click saves; the notes save when you leave the field or
  press Ctrl/Cmd+Enter. Doses and grind stay on the shot page, whose form
  writes the same verdict.
- **The flavour wheel.** Taste and aroma are recorded as notes on the SCA/WCR
  Coffee Taster's Flavor Wheel, all three tiers, from "Fruity" to
  "Blackberry". It replaces the old taste chips (sour side, dialled in, bitter
  side, strength). The balance — sour, balanced, bitter, the GaggiMate's own
  three words — stays a control of its own, and it is still what goes to the
  machine's notes card; the wheel's notes stay here.
- **Taste wheel page** under Brew setup (`g w`): the wheel, drawn, where you
  pick which notes the shot panel offers for taste and for aroma. A fresh
  archive starts with ten or so of each. The chips under a shot show your
  picks plus anything already recorded on that shot.
- **Decision on the row.** The shots table's Analyse column is replaced by a
  Decision column: Keep, Improve or Discard, one click each, a second click
  to clear. "Adjust" is now "Improve". Start an analysis from the shot page;
  the Flags column still shows where it got to. If you had Analyse among your
  columns, Decision takes its place.
- **A narrower Set column.** The Set column has a drag handle like the others
  and starts narrower; a long Set name truncates, with the whole name on
  hover.
- **The analyzer reads the wheel.** An analysis sees your notes with their
  path on the wheel, and the taste rules on the Knowledge page are keyed on
  wheel notes and the balance. The rules about mouthfeel, strength and finish
  (astringent, watery, flat, too intense) have no note on the wheel, so no
  analysis selects them for now; their text is still there.

### Settings and the sidebar, regrouped

The sidebar has five rows instead of nine: Shots, Chat, **Brew setup** (Sets,
Beans, Hardware), **Machine** (Profiles, Sync, Device) and **Settings**. A
group opens when you click it, and on its own while you are on one of its
pages; one you open by hand stays open next time. Every `g` chord still goes
where it did, and the device page has a row of its own under Machine.

Settings is a group of pages rather than one long page, in the order you are
likely to need them: **Machine access** and **LLM** first, since nothing works
until both are filled in, then Authentication, Knowledge, Prompts, Profile
safety, System and Import. On each page the settings sit in cards under
subheadings, all closed until you open one; a save that fails validation opens
the card with the problem. The page that used to be called Machine is Machine
access, so it is not confused with the sidebar's Machine group, and the old
General page's analysis and chat budgets are cards on the LLM page. Knowledge
keeps its own address, so every citation link into it still works.

### The Beans page after using it

- **No variety.** The field is gone from the bean, the form, the card, the API,
  `v_beans` and both prompts: it was blank on most beans and nothing reasoned
  from it.
- **Description.** "What the bag claims it tastes of" is now `description`, a
  free-form description of the coffee in your own words (what the bag or the
  roaster says, tasting notes, anything worth knowing), in a three-row box, up
  to 2000 characters. The API field and the `v_beans` column are `description`
  (formerly `tasting_notes_bag`), and the analysis and starting-point prompts
  receive it as `description`.
- **Decaf** sits in the grid with a label like the other fields.
- **Roaster and origin suggest what you have already recorded**, archived beans
  included: type to filter, pick with the mouse or with the arrows and Enter.
  Anything else you type is kept as typed.
- **A bean can be deleted**, after an inline confirmation: `DELETE
  /api/beans/{id}`. A bean any Set uses cannot (409, with the number of Sets);
  archive it instead. Starting-point runs about a deleted bean go with it.

### MCP has no network endpoint

The Streamable HTTP endpoint at `/mcp` is gone, and so is its switch. MCP is the
chat's own database tool: the `claude_code` provider spawns `gaggiclanker mcp`
and talks to it over stdio, and nothing listens on the network for it. `/mcp` now
answers like any other unknown path. The README no longer documents wiring the
command into Claude Desktop or other outside agents; the command itself is
unchanged for the provider that uses it.

The chat's tools are handed nothing that can reach the machine: creating a draft
goes through an object built without the machine connection, which the
starting-point wizard uses too, and a test walks what a tool is given to prove
no device client or connection is reachable. `draft_profile` now works when the
chat runs on the `claude_code` provider, where it used to answer that it needed
the running application.

Removed setting: `mcpEnabled` (and its `GAGGICLANKER_MCP_ENABLED` variable). A
stored value is deleted at upgrade (migration `0019`), and a boot with the
variable still set names it in the `setting_env_ignored` line described below.

### Clone and start, and settings live only in the database

**Starting is `git clone`, then `docker compose up -d --build`**, then entering
the machine's address under Settings → Machine access. There is no file to copy, rename
or edit first: `.env.example` and the `.env` that briefly replaced it are both
gone, and the repository ships no configuration file at all.

**Breaking: the default port is now 8042**, not 8000 — it was the one number in
this project likely to collide with something else on a home server. It is still
the only thing given at spawn, because the process has to bind before it can read
the database, and it is remembered nowhere: pass it again each time, from your
shell or from a `.env` Compose substitutes from. To keep 8000, start with
`PORT=8000 docker compose up -d --build`; with the bridge arrangement,
`HOST_PORT=8000 docker compose up -d` publishes on 8000 and leaves the container
on 8042. The image, its healthcheck, compose's healthcheck and the Vite dev proxy
all moved together.

**Breaking: no runtime setting is read from the environment any more.** A
setting is what the Settings page saved, or the shipped default — those are the
only two possibilities, and `GET /api/settings` reports `source` as `database` or
`default` accordingly (`environment` is gone from the field and from the badge on
the Settings page). Every variable that used to configure one — `GAGGIMATE_HOST`,
`GAGGIMATE_PROTOCOL`, `GAGGIMATE_TIMEOUT_S`, `GAGGICLANKER_DEVICE_SYNC_ENABLED`,
the `GAGGICLANKER_DEVICE_CLEANUP_*`, `GAGGICLANKER_NOTES_WRITEBACK_FIELDS`, the
`GAGGICLANKER_PROFILE_POLICY_*`, `GAGGICLANKER_LLM_*`, `GAGGICLANKER_MODEL*`,
`GAGGICLANKER_ANALYSIS_CHUNK_TOKEN_BUDGET`, `GAGGICLANKER_CHAT_*`,
`CLAUDE_CODE_BIN` and `CLAUDE_CODE_EFFORT` — now does nothing. Two
configuration surfaces that could disagree silently, on a box whose whole
configuration fits on one page, was one too many.

Compose no longer passes a machine address through. The only variable it still
sets is `LOG_LEVEL`; the image sets `DATA_DIR`, `HOST`, `PORT` and `WEB_DIST`,
and `LOG_JSON` and `CORS_ORIGINS` fall back to their defaults. Those seven are
the whole of what the process reads from the environment, and the README's
Configuration section documents each one.

Nothing parses a `.env` any more — not the settings, not the bootstrap values,
not the credential check — so a file left next to the compose file is logged once
as `dotenv_file_ignored` with its full path and read by nothing.

`compose.yml`'s `environment:` block is not configuration either: it forwards
exactly the variable names a boot refuses (the sign-in and LLM credentials, and
`GAGGICLANKER_DEVICE_WRITES_ENABLED`) and nothing else, each as `${NAME:-}`.
Compose substitutes those from a `.env` in the project directory as well as from
your shell, so a box upgrading from the `.env.example` era with credentials still
in that file stops at boot with the names in the log, rather than coming up with
the sign-in that file used to configure silently off — while a stale `DATA_DIR`
or `PORT` in the same file reaches nothing. An unset or empty variable
substitutes to empty, which counts as unset, so a clean box boots.

**Upgrade note: store your configuration in the database before you pull.**
While the old version is still running, move anything you had set — in a `.env`,
in `compose.yml`'s `environment:`, or in the shell — into the database, and then
remove the variable. A boot that still finds one set logs `setting_env_ignored`
once with the names (never the values) and starts on the database's values.

Note that simply typing the value into the old Settings page and pressing Save
does **not** store it: the form sends only the fields whose value differs from
the one displayed, and a value coming from the environment is already displayed,
so Save sends an empty change. Either change the field to something else, save,
change it back and save again — or store it directly, which is one request:

```bash
curl -X PATCH http://localhost:8000/api/settings \
  -H 'content-type: application/json' \
  -d '{"gaggimateHost": "192.168.1.50", "deviceCleanupMode": "keep_newest"}'
```

Add `-H "Authorization: Bearer <token>"` if sign-in is on, and check what stuck
with `curl -s localhost:8000/api/settings`: every key you moved should read
`"source": "database"`. Port 8000 because that is the old version's default —
see the port change below.

Two are refused rather than ignored, and the container exits naming them: any
variable that used to carry a credential (see *Credentials leave the environment*
below) and `GAGGICLANKER_DEVICE_WRITES_ENABLED`, which used to open the only path
from this box to the machine. Turn writes on under Settings → Machine access instead.
Silently ignoring either would leave a box less protected than its owner
believes. An empty value counts as unset in both cases, so an old compose file
passing `${GAGGICLANKER_DEVICE_WRITES_ENABLED:-}` through still starts.

If `git pull` stops on your own `.env`, move it aside — nothing reads it now.

### Machine settings apply live

Changing the machine's host, protocol, timeout or **Device sync enabled** under
Settings → Machine access now takes effect on save: the connection to the old machine is
closed and the new one opened, with no restart, and the header pill and the Sync
page follow straight away. A save that leaves the effective values as they were
does nothing to the connection. While a profile push or rollback, a cleanup run,
a notes send or a pull is using the machine, a change that would move the
connection is refused with `409` naming what is running, and nothing in that save
is stored; other settings save as usual. A pull asked for while such a save is in
progress waits for it. A pull cut short — by a connection change or by stopping
the app — is now recorded as an error saying it was stopped; it used to be filed
as `ok` with nothing archived.

### Credentials leave the environment

**Breaking: move sign-in and provider keys into Settings before you upgrade.**
The sign-in user and password and every LLM provider's API key or token are now
configured only in the Settings page and kept only in the database. A boot that
finds one of the variables that used to carry them, or that the provider SDKs
would read a credential from, set non-empty — `AUTH_USER`, `AUTH_PASSWORD`,
`AUTH_PASSWORD_HASH`, `AUTH_TOKEN_TTL_S`, `AUTH_JWT_SECRET`,
`GAGGICLANKER_AUTH_PASSWORD`, `GAGGICLANKER_AUTH_JWT_SECRET`,
`GAGGICLANKER_LLM_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_CUSTOM_HEADERS`, `OPENAI_API_KEY`,
`OPENAI_ADMIN_KEY`, `OPENAI_CUSTOM_HEADERS` or `OPENROUTER_API_KEY`, in any
letter case, in the process environment — refuses to start: it logs
`auth_env_refused` with the variable names (never a value) and exits non-zero.
So does an `HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY` (either case) carrying a
user or password; a proxy without one is still used.
Ignoring them instead would have switched authentication off on an install that
had configured it only there. Empty values count as unset, so a compose file that
still passes `${AUTH_USER:-}` through starts normally.

To upgrade an install that set any of these: while the old version is still
running, set the password and then the username under **Settings →
Authentication**, and paste each key or token under **Settings → LLM**; then
remove the variables and upgrade. The session signing key is always generated
into the database now (backups carry it), so sessions survive restarts as before;
the one case that changes is two processes sharing sessions through a common
`AUTH_JWT_SECRET`, which is no longer possible.

A lost password is recovered by deleting the stored `authPasswordHash` row, which
turns sign-in off on the next request with no restart, and then setting a new
password under Settings → Authentication; the README has the one-liner. The
`claude_code` provider's CLI now gets its token only from the setting, never from
the app's own environment, and no proxy with credentials in it. The OpenAI and
Anthropic clients are handed the stored key and an explicit base URL, ignore the
SDKs' environment headers, organisation and project variables, profile files and
`.netrc`, and do not follow redirects. An `openai_compatible` provider with no
base URL stored now reports "set llmBaseUrl" instead of calling OpenAI's
default address.

### Every write to the machine is explicit

**Profiles may be pushed by the app; everything else written to or deleted from
the machine happens only from the new Sync page, by a person.** Three things that
used to happen on their own no longer can.

**A Sync page.** A new **Sync** entry in the sidebar (`g y`) holds every exchange
with the machine that you start: **Pull from the machine**, **Send notes to the
machine**, **Clean up the machine's storage** and **Recent writes**. Each write
action says what is in the way when it cannot start — no machine configured,
device writes off, or the machine not connected. The Device page keeps what the
machine is, its versions and its connection, and links to the Sync page; its old
storage, notes, sync and writes anchors redirect there.

**Saving a judgement never contacts the machine.** Notes go to the machine's
notes cards only when you tick judgements on the Sync page and confirm; nothing is
ticked for you. The rules about what may be sent are unchanged: a card edited on
the machine more recently is left alone, and a verdict that came from the machine
and was never edited is never sent back. The **Sync notes to machine** button on
a shot is gone.

**A cleanup never runs by itself, and runs exactly what you confirmed.** The Sync
page shows the plan with the reason each shot is in it and, folded, the shots
kept and why. Confirming names the count and says it cannot be undone on the
machine (the archive keeps every shot). If the plan changed between the preview
and the confirmation — a pull landed, a setting moved — nothing is deleted and you
are asked to look again.

**MCP is read-only by design.** MCP clients get exactly the in-app chat's tools:
read, and propose something a person confirms. No tool can write to the machine,
and no setting adds one.

Removed settings: `mcpDeviceWrites`, `deviceCleanupAuto` and
`notesWritebackEnabled` (and their `GAGGICLANKER_MCP_DEVICE_WRITES`,
`GAGGICLANKER_DEVICE_CLEANUP_AUTO` and `GAGGICLANKER_NOTES_WRITEBACK_ENABLED`
variables). **Device writes enabled** is the one switch in front of every write;
`deviceCleanupMode` and its two numbers now shape the plan the Sync page proposes,
and `notesWritebackFields` picks what a send writes. Stored values for the removed
settings are deleted at upgrade (migration `0018`), and a boot with one of the
variables still set logs `setting_removed_env_ignored` naming it.

API: `POST /api/device/cleanup/run` requires `{"shot_ids": [...]}`, the planned
shot ids you confirmed — at least one, an empty list is a 400 — and answers 409
when the plan has changed; each planned shot carries a `reason`, and the plan's
policy loses `auto`. `POST /api/device/notes/push` requires `{"shot_ids": [...]}`,
the judgements you ticked — at least one; there is no form that sends everything
pending — and answers 409 when a selected one is no longer pending.
`GET /api/device/notes/pending` returns `items` — each with the shot's device id,
time, profile and verdict — in place of `shot_ids`, and loses `enabled`. Both
write routes answer 403 when device writes are off, and record the refusal.
`POST /api/shots/{id}/notes-writeback` is removed.

### The shots table

**A row opens in place.** Clicking a row no longer opens the shot page: it
unfolds a panel under the row with the shot's curve, the full judgement form and
the machine's device notes, so a shot can be judged without leaving the list.
Clicking the row again, or pressing Escape, closes it; opening another row closes
the first. Clicking the curve or the notes, or **Open shot page**, goes to the
shot page. A quarantined shot shows its reason instead of a curve.

**An Analyse column, in place of Flags.** Each row says whether it has been
analysed and offers the action: **Analyse**, **Analysing…** while it runs,
**Analysed** linking to the analysis on the shot page, or **Retry** with the
failure's reason on hover. A quarantined shot cannot be analysed, as on the shot
page. Flags is still in the column chooser. If you had stored exactly the old
default columns, you get the new default; any other choice of columns is kept.

**Columns can be resized.** Drag the edge of a heading, or focus it and use the
arrow keys; double-click the edge to reset that column, or use **Reset widths**
in the column chooser. Widths are remembered in the browser.

**Compact time, centred columns, Set first.** The time shows day, month and time
for this year's shots and the date with its year for older ones, with the full
timestamp on hover, in a narrower column. Headings and values are centred, and
the Set is the first column. Columns no longer drift out of line when a Set name
is long.

The shots API's list rows gain `analysis_error`: the newest analysis's error when
it failed, otherwise null.

### Filing a shot from the list

**"needs a Set" is a button.** In the shots list the dashed badge opens a small
menu anchored to it with the first three Sets the Set list returns (the active
one first, then the newest), each at its latest version with its bean, grinder
and profile on one line. Choosing one files the shot under that version and the
row's badge becomes the Set's. With no Sets the menu links to the Sets page;
with more than three, **Another Set…** opens the shot page scrolled to its Set
panel (`/shots/<id>#set`). A failed assignment says why and leaves the menu
open.

**An assigned badge's link works in the list.** It was painted under the row's
own link, so clicking it opened the shot instead of the Set.

### Starting a Set is one form

**New Set is a single dialog** with every manual option on one screen: the bean
(with its facts line), a name defaulting to the bag's, the grinder, the profile
version, grind, dose, target yield, temperature and an optional intent, and one
**Start the Set** button that waits for a bean. The five-step wizard (Suggest,
Bean, Hardware, Profile, Recipe) is gone. The Beans page shortcut still opens it
with the coffee picked, and a refusal from the server still keeps what you
typed.

**The AI starting point is folded under the form** as **Suggest a starting
point instead**. It uses the bean and grinder already picked; asking, the three
options and taking one behave as before, including landing on the staged draft
when the option authored a profile.

**Picking a profile fills the recipe.** Target yield and temperature come from
the profile version when it states them: the temperature is the profile's own
(0 means not set), and the yield is its largest volumetric stop — `pumped`
targets are water, not coffee, and utility profiles stop on nothing, so they
offer no yield. A field is filled only when it is empty or still holds what the
previous profile filled; anything typed stays, and a hint says which numbers
came from which profile. `GET /api/profile-versions` rows carry the two numbers
as `temperature_c` and `target_yield_g`, and the style detector's allongé check
reads the yield through the same helper.

### A bean has no altitude

Almost no bag prints the growing altitude, so the field was empty on most beans,
and the only thing it fed was one rule nudging dense high-grown coffee a degree
or two hotter and a step finer — the move the roast level and the taste of the
cup already lead to.

- **Removed**: the Altitude field on the Beans form and the metres on the bean
  card, `altitude_m` on the bean API and the chat's `list_beans`, the altitude
  line in the analysis and starting-point prompts, and the `altitude:high`
  signal.
- **One rule leaves the seeded knowledge tier**: `temperature_by_roast`/
  `high_altitude`. An archive that already holds it keeps the row — seeding
  never deletes a row somebody may have edited — but nothing emits the signal it
  matches on, so it is never selected. The seeded reference prose is unchanged.

Migration `0017` drops the column. `v_beans` names it, and SQLite re-parses every
view when a table is altered, so the view is dropped and re-created without the
column; the chat's SQL views answer everything else as before.

### One machine

The archive was built to hold several, and the generality cost correctness
rather than surface. Identity was the configured host, so a display board that
changed address became a *second* machine: its shots, profiles and Sets split
from the old ones, the active Set stopped auto-assigning, and nothing merged
them back. An import done before the machine was configured — the natural order
for a new install — landed on a synthetic `import:default` machine, and the same
shot could then exist twice, because the unique key included the machine.

`machines` is now a one-row table describing whatever host is configured. The
host is a setting, not an identity: pointing the container at a new address
updates that row and everything stays attached. A shot is unique by the id the
device gave it, one Set is active overall, and no route, form, chat tool or MCP
schema takes a machine id. Grinders stay plural — a kitchen really does have
several, and a grind number only means something on the grinder it was set on.

**Upgrading merges what you have, and it is worth knowing the rules.** Migration
`0016` keeps the machine with a real host, and the most recently seen of several
real hosts; the importer's `import:default` placeholder always loses. Everything
that pointed at the others is re-pointed at the survivor. Shots are then
de-duplicated by their device id: the copy whose bytes came off the machine
wins, the newest otherwise, and before the loser goes its samples, its verdict,
its notes card, its analyses and its Set assignment move across wherever the
survivor has none. The profile mirror de-duplicates the same way, keeping the
live mapping over a tombstone. If two Sets were active, the survivor machine's
stays active and the others are simply no longer *the* one — nothing is
archived. An archive with no machine at all gets the row with an empty host, so
a fresh install and an import-only install both have "the machine" before the
first pull. Take a backup first (`POST /api/backup`); there is no
down-migration.

**`GET /api/machines` is `GET /api/machine`**, answering the row with its shot
counts, and `PATCH /api/machine` still takes only the name and the notes. The
`machine_id` query on `GET /api/shots`, the field on `POST /api/sets`, the form
field on `POST /api/import`, the starting-point request body and the
similar-Sets query are all gone. The Hardware page leads with one **Machine**
card above the grinders, and the New Set wizard has no machine step.

### The sidebar folds

The button at the foot of the rail, or the `[` chord, collapses the sidebar to
an icon rail and back, and the choice is remembered. Folded, every entry keeps
its name — a tooltip for a mouse, the accessible name for everything else — and
the active entry is still marked. The mobile sheet is unchanged.

### Fewer pages, and beans are coffees

Twelve destinations in the sidebar, three of which were not places you decide to
go. They are folded into the page you are already on when you want them.

**The sidebar is eight entries**, in the order they are used: Shots, Chat,
Profiles, Sets, Beans, Hardware, Knowledge, Settings. The `g` chords are
unchanged; `g i`, `g d` and `g r` do nothing now.

- **Import is the drop zone on the Shots page.** It already took the files; it
  now shows what each one did, collapsed to a summary line with a **Show files**
  toggle. `/import` redirects to `/shots`.
- **The device page is behind the header pill**, which is where you are looking
  when you want it. The page, its route and its tests are unchanged, and the
  pill now names its destination for screen readers.
- **Drafts are the staging queue on the Profiles page**, under **Staged for the
  machine**. A draft is the step between a profile version and the machine, not
  a destination: everything that creates one starts from a version or ends by
  linking back to it. `/drafts` redirects to `/profiles#staged`, and every link
  that used to point at the queue points at that anchor.

**Two new ways to stage a profile**, both through the existing manual draft
path, so the schema, the safety policy and the audit are unchanged:

- **Stage as is** on a version row, for a profile that is already right and only
  needs to get onto the machine without a trip through the JSON editor.
- **Upload profile** in the Profiles header runs a profile export through the
  importer, so a file becomes a version with its own staging button.

**A bean is a type of coffee, not a bag**, and `beans.roast_date` is gone.
Roaster, origin, process and roast level stay true of every bag you
ever buy of that coffee; the date was true of one of them, so re-buying either
aged the old row silently or forced a duplicate bean.

- **Removed**: the field on the Beans form, the freshness pill on the Beans and
  Sets pages, `bean_roast_date` on the Set list row, `roast_date` on the bean
  API, the days-off-roast line in the analysis prompt, and the rest note in the
  starting point. The bean list is alphabetical now that there is no freshest to
  put first.
- **Ten rules leave the seeded knowledge tier**, and with them two whole
  categories: the five `freshness_windows` and the five `rest_times`. Both are
  advice about how long a bag has been open, which is unactionable without a
  date and would only be prompt weight. An archive that already holds those
  rows keeps them — seeding never deletes a row somebody may have edited — but
  a retired category is never selected and sorts last in the rule list. The
  seeded prose on bean freshness and storage stays; it is retrieved by a
  question, not injected.
- **Bag ageing is not tracked at all for now.** It is a real thing about coffee
  and it may come back as its own row with its own dates.

Migration `0015` drops the column, and with it the two curated views that name
it, re-creating them underneath — SQLite re-parses every view when a table is
altered, so the drop fails outright while they stand. The chat's SQL views lose
`roast_date` and answer everything else as before.

### The archive is pulled into, not pushed at

The prototype mirrored the machine continuously: a pass every fifteen minutes,
a pass on every reconnect, a pass on every `evt:history-shot-saved`, a live shot
view redrawing at 2 Hz, and a fake device that brewed on a timer to feed it. In
use none of it earned its place — the GaggiMate's own web UI already draws the
shot that is happening now, and duplicating it here was a second, worse copy
that spent the machine's two HTTP slots on it. What a person wants from an
archive is: press a button and have the new shots, drop a file and have it
archived, then say what the cup was like without leaving the list.

**Getting data is a request.**

- `POST /api/sync/run` is the only trigger for shots, profiles and notes. No
  timer, no pass on a device event, no pass at startup. The worker loops wait on
  their poke with no timeout at all, so there is no interval left to set.
- Identity stays automatic — one `res:ota-settings` frame plus one
  `GET /api/settings`, at startup and on every connect. It is what tells the
  header whether the machine is there, and a pull has nowhere to store a shot
  until the machines row exists.
- The WebSocket is still held: it is what the header pill reads, and what a
  profile push, a notes write-back and a storage cleanup travel over.
- Automatic cleanup, where it is switched on, now runs after a pull — which is
  the right moment for it.
- **Removed:** `GET /api/device/live` (the 2 Hz telemetry stream), the
  `devicePollIntervalSeconds` setting, and the fake device's `--brew-every`. An
  archive that still holds a row for the removed setting ignores it.

**No live view.** The `/live` page, its nav entry and its `g l` chord are gone,
along with the streaming chart, the live-status store and the device page's
"Right now" and "Warnings" cards. The header pill keeps its four states from the
status poll alone. Chart.js stays for the shot, compare and Set trend charts.

**The shots page is a dataset.**

- **"Pull from machine"** in the header: disabled with a reason when no machine
  is configured or it is unreachable, a spinner while a pull is running —
  whoever started it — and a toast when the one you started finishes, counted
  off the ledger ("3 new shots, 1 updated", "Nothing new", or what the machine
  said when it failed). The subtitle says when the archive was last pulled into,
  or "Never pulled".
- **A drop zone** under the header takes shot and profile exports — `.json`,
  `.slog` or a zip of either — with a file picker for keyboards and phones and a
  result line that unfolds into a row per file.
- **Filters behind one button** with a count of how many are on, instead of
  eight dropdowns across the top of the page. The URL contract is unchanged.
- **Column headers sort**, with an arrow and `aria-sort`; the sort dropdown is
  gone.
- **A column chooser**, remembered per browser. Profile and Curve are off by
  default — a sparkline is a request and a canvas per row, and the profile name
  is the same string on nearly every row — and Set is a column of its own.
- **Rating, notes and Set can be set from a row**: click a star to rate, click
  the lit one to clear, or open a small panel at the end of the row for all
  three. A rating set from the list merges into whatever verdict already exists,
  so taste tags, doses, grind and decision typed on the detail page survive it.
- The list row carries the verdict's rating and notes, and the rating a row
  shows is the verdict's, falling back to the machine's notes card and then to
  the index — the same expression the "rating" sort and the minimum-rating
  filter use, so a page that can set a rating agrees with itself about what the
  rating is.

### Profile drafts and push to the machine

The first thing gaggiclanker writes to a GaggiMate. It turns an analysis's
`profile_patch`, or a hand edit, into a validated profile draft, and — once a
person has approved it — saves it to the display as a **new** profile.

- **Off by default.** `deviceWritesEnabled` gates every write method and is
  re-read on every write, not cached at boot. A device client built without the
  app's gate can write nothing at all, so read-only is what forgetting gives you.
- **Never overwrites, never selects.** `save_profile` refuses a profile carrying
  an id — the firmware upserts on the filename — so the machine always assigns
  its own. The pushed profile sits beside whatever you were brewing with.
- **Deletes only what it created.** Two independent proofs: the label on the
  machine ends in ` [AI]`, and the `device_writes` audit holds a successful save
  for that id.
- **A safety policy narrower than the firmware**, tunable from Settings:
  60–100 °C, 0–12 bar, 0–10 ml/s, phases of 0.5–120 s, at most ten of them. It
  clamps and **says what it moved**; anything a clamp cannot fix is refused
  rather than quietly rewritten.
- **Stop conditions need an explicit acknowledgement.** A draft that adds,
  removes or moves a `targets` entry changes how much coffee ends up in the cup,
  and cannot be approved until somebody says they meant it. `9` and `9.0` are not
  a change.
- **Round-trip verified.** The push reads the profile back and compares canonical
  JSON. A mismatch is a stored `failed` draft carrying both documents plus one
  button that deletes the machine's copy.
- **Every attempt audited**, refusals included, and listed on the Device page.
- **A simulator gate**: every profile fixture and one generated draft saved to
  the real firmware compiled natively, read back, brewed, and deleted.
  `scripts/profile_gate.py` runs the same four layers over a file from a shell.

New: `GET/POST /api/profile-drafts` and its approve / push / rollback / discard
/ refine routes, `GET /api/device/writes`, a staging section on the Profiles
page, "Draft profile" on the shot analysis panel, and "Stage as is" and "Edit"
on a profile version. Migration `0008`.

### Device storage cleanup

The machine is a buffer: its firmware deletes the oldest shot whenever free
space drops below 500 KB, archived or not. This makes shots leave the display
*when the archive has them* instead.

- **A shot is only deleted once this box holds it, intact.** The eligibility
  rule is one function, applied by the plan step so a preview means something and
  by the write gate so it is actually enforced: the shot must be in the archive
  for this machine, not quarantined, and its stored blob must be exactly the
  length its header implies (or already recorded as `incomplete`).
- **A policy, off by default.** `deviceCleanupMode` is `off`, `keep_newest`
  (default 50, at least 5) or `free_space` (default 2048 KB free, at least
  1024 — the firmware's own threshold is 500). `deviceCleanupAuto` runs it after
  each successful index read; without it, cleanup is a button.
- **Oldest first, two a second, stopping on the first refusal.** The firmware
  deletes in id order, so anything else would fight its own retention; the pace
  is because the display's web server is pumped from its main loop.
- **A preview that lists what it will *not* delete, with the reason.** "Why is
  that shot still on my machine" is otherwise unanswerable.
- Every delete is audited in `device_writes`; every run is a `cleanup_runs` row
  carrying both figures (planned and deleted) and the error that stopped it.

### Judgements written back to the machine

Your verdict on a shot, mirrored onto the display's own notes card so the
touchscreen shows it.

- **Off by default and gated twice**: `notesWritebackEnabled` on top of
  `deviceWritesEnabled`. `notesWritebackFields` picks what is sent; `notes` is
  offered and not on by default.
- **The document is the machine's, with our fields laid over it.** Unknown keys
  another client wrote survive; a field not in the policy keeps what the machine
  has. `doseOut` goes as a **string** — the firmware only honours it as an
  override for the index volume when it is one — and `timestamp` is set by us,
  because the firmware never sets it.
- **Newest wins, and a device note is never echoed back.** A verdict is written
  only when it is newer than the machine's card; one that was *seeded* from the
  machine and never edited is never sent. The read path's rule is unchanged: a
  sync can create a judgement, never overwrite one.

New: `GET /api/device/cleanup/plan`, `POST /api/device/cleanup/run`,
`GET /api/device/cleanup/runs`, `GET /api/device/notes/pending`,
`POST /api/device/notes/push`, `POST /api/shots/{id}/notes-writeback`, a Storage
card and a notes card on the Device page, "Sync notes to machine" on a shot, and
six settings under Settings → Machine access. Migration `0010`.

### Knowledge base tiers 2 and 3

The rule tier is a few hundred one-sentence facts. This adds the two tiers
either side of it: the prose behind the rules, retrieved a few passages at a
time, and what this archive has learned about *your* kitchen.

- **Tier 2 — 25 documents, 63 chunks, about 31 000 words.** The gaggimate-mcp
  knowledge files (MIT; see `gaggiclanker/knowledge/seed/docs/ATTRIBUTION.md`),
  split at their H2/H3 headings into 200–600-word chunks and indexed with FTS5
  (BM25, `porter unicode61`, headings weighted ten to one). Seeded on boot with
  the same three-way rule the prompts and the rules have: new documents are
  inserted, unedited ones take the new text, edited ones keep yours and only the
  shipped default moves.
- **A chunk id is a citation.** `heading_path` — e.g.
  `ESPRESSO_BREWING_BASICS#adjustment-strategies/variable-hierarchy` — is derived
  from the headings, so it survives a re-seed, a reset and an upgrade. An
  analysis prints the ones it used and the panel links each straight to the
  passage. Tables and fenced blocks are never split: half a table still looks
  complete, which is worse than no table.
- **Retrieval is deterministic and budgeted.** Queries are built from the
  judgement's taste and balance, the channeling indicators that fired, the
  diagnostic bands that were not normal, then the bean and the style — in that
  order. Top hits merge into a stable total order, at most one chunk per
  document, under `analysisChunkTokenBudget` (default 1500 estimated tokens; 0
  turns retrieval off). The same shot gets the same excerpts twice running.
- **Excerpts are supporting context; the rules stay authoritative.** The prompt
  says so, and `excerpts_used` is checked against what the shot was actually
  given — an invented citation is dropped rather than shown.
- **Tier 3 — learned insights.** Scoped by any of bean, roast level, process,
  origin, grinder, profile style and machine; an insight applies when **every**
  key it states matches. The analyzer proposes at most two per analysis, with
  the shots they were drawn from, and they land **unconfirmed**: nothing reaches
  a later prompt until somebody presses confirm, because a model that
  generalises from one shot and is then believed by the next analysis has
  manufactured its own evidence. Confirmed ones are rendered above the rules as
  "what you have learned".

New: `GET /api/knowledge/docs`, `GET|PUT /api/knowledge/docs/{slug}`,
`POST /api/knowledge/docs/{slug}/reset`, `GET /api/knowledge/search`,
`GET|POST /api/knowledge/insights`, `PATCH|DELETE /api/knowledge/insights/{id}`,
a Knowledge page with Rules / Docs / Insights tabs, "Reference excerpts" and
"Proposed insights" on the analysis panel, learned insights on the Set page, and
one setting (`analysisChunkTokenBudget`). Migrations `0011` and `0012`.

### A starting point for a new coffee

The first shot with a coffee nobody has brewed, answered from what this archive
already knows rather than from a chart.

- **Similar past Sets, by SQL and for free.** The wizard shows what you have
  already brewed on *this grinder* that resembles the new coffee — same roast
  level, same process, same origin — with how each one actually went: shots,
  mean rating, mean execution score, ratio and time. It costs no tokens and it
  is worth reading on its own. A recipe with no shots behind it is never
  offered: it records an intention, not a result.
- **Three options, not one.** Conservative, recommended, adventurous, each with
  a grind, a dose, a yield, a temperature, a profile and a rationale citing the
  rules and Sets it leaned on. Nobody knows what a new coffee wants yet, and a
  single confident answer hides that.
- **It will not invent a grind number.** A grinder's scale is arbitrary and
  there is no conversion between two of them, so a number is offered only when
  your usual setting or a past Set on the same grinder anchors it. Otherwise the
  answer is relative — "a little finer than your usual" — and the card says so.
- **Taking one creates the Set** with `origin = starting_point`, and — when the
  option authored a whole profile — a draft through the same schema, safety
  policy and clamp a hand-typed one goes through. Nothing is pushed to the
  machine; a person approves it. An option whose profile the policy refuses is
  rejected with every violation and creates nothing at all.
- **The Beans page has a shortcut** straight into the wizard for the coffee you
  are looking at, and the chat can ask for one through the `starting_point` tool.

### Fixed

- **Phase bands no longer hide the shot curves.** On the shot chart every
  second phase was shaded with an opaque colour and painted on top of the
  lines, so a soak or a long decline blanked out pressure, flow, weight and
  temperature across its whole span. The bands now sit behind the curves in a
  faint, see-through wash of their own (`--chart-band`, one per palette); the
  phase names and the dashed end-of-shot line still draw above them.

## [0.1.0] — 2026-09-11

The prototype. It archives every shot a GaggiMate has taken, shows the curves
and deterministic diagnostics, lets you judge each shot and group shots into
versioned Sets, and produces a per-shot LLM analysis you can act on.

### The archive

- Every shot the machine has, with its **raw `.slog` bytes kept verbatim**. A
  shot that fails to parse is stored quarantined rather than dropped: the
  machine deletes old shots under storage pressure, so by the time a parser bug
  is fixed its copy is gone.
- A persistent WebSocket to the display board with reconnect and backoff, plus
  index polling as the safety net behind `evt:history-shot-saved`. Exactly one
  socket, because the firmware allows three clients in total and the machine's
  own browser UI is one of them.
- Profiles, profile versions, the device's own shot notes and the machine's
  identity, all mirrored.
- **The device client is read-only** — ten read methods and nothing else,
  enforced by a test rather than by convention.
- Import of the web UI's JSON exports, from a file, a folder or a zip, on the
  command line (`gaggiclanker import`) or through the UI. That is the only way
  back for a shot the machine has already deleted.

### Reading a shot

- Full curves at the machine's own sample interval, with the phases the firmware
  recorded.
- Deterministic diagnostics — resistance, channeling risk, temperature
  stability, pressure and flow adherence, overshoot — with band labels, and an
  execution score that says which component capped it. Everything
  pressure-derived is gated on the board's capability flag, because Standard
  boards report zero.
- A live view that follows a brew at 2 Hz and links to the saved shot when the
  file lands.
- Judgement per shot: rating, balance, taste tags from a fixed vocabulary, dose
  in and out, grind, notes and a decision.
- **Sets**: a bean, a grinder, a machine, a profile and a recipe, versioned.
  Shots are assigned to the version that was current when they were pulled, and
  the trend chart reads across versions.

### The analysis

- One structured LLM call per shot, given the diagnostics with their band
  labels, the Set, the previous five shots with your verdict on each and the
  advice that followed them, and the knowledge rules that match. It answers with
  a diagnosis and prioritised suggestions.
- Providers: `claude_code` (the default — spends a Claude subscription, no API
  key), `anthropic`, `openrouter`, `openai`, `ollama`, `lmstudio` and any other
  OpenAI-compatible gateway. Each has its own credential and none is ever lent
  to another.
- An editable **knowledge tier** of dial-in heuristics, each with its source, a
  confidence and a switch. The model names the rules it used and the analysis
  links back to them, which is how a rule that misleads gets found. Adapted from
  [gaggimate-barista](https://github.com/chall-tech/gaggimate-barista) (Charlie
  Hall, MIT).
- Accepting a suggestion creates a new Set version with that one field changed
  and the old version as its parent, so "did the advice help" is a question the
  trend chart answers.
- The call runs as a background task, not inside the request. A failure is a
  stored row carrying the provider's error, never an exception; a run cut off by
  a restart is marked `interrupted` at the next boot.
- Prompts are rows in the database, seeded from YAML and editable from the
  Settings page; an edit takes effect on the next call.

### Running it

- A multi-stage image: the front-end build, a uv-installed venv, and a runtime
  stage carrying neither node nor a compiler. Non-root, `cap_drop: ALL` with an
  empty capability bounding set, `no-new-privileges`, and a healthcheck that
  needs no extra package.
- `compose.yml` defaults to host networking, with a bridge alternative
  documented next to it.
- One SQLite file under `DATA_DIR`, so a backup is a file copy. `POST
  /api/backup` uses `VACUUM INTO`, which is consistent while the app is running.
- `.env.example` documents every variable, and a test asserts it stays in step
  with the settings registry in both directions.

### Security

- **Optional single-user authentication**, off unless `AUTH_USER` and a password
  are set. HS256 bearer tokens with a server-side session row, so signing out
  revokes rather than merely forgets; argon2id password hashing with a
  constant-time compare; five failed sign-ins per address buy a sixty-second
  lock; a public status probe so the UI can offer a sign-in form before the
  first 401. The guard covers every `/api/*` route including the event streams
  and the OpenAPI document, with `/health` and the web bundle public.
- **Auth fails closed.** A stored password hash that argon2 cannot verify leaves
  authentication *on* and refusing every sign-in, with the fix named in the log
  — never off. `POST /api/auth/password` is the only way a browser sets the
  password: it takes the plain value and hashes it on the server, and the
  settings API refuses the hash field outright. Changing the password or the
  username revokes every open session.
- CORS off by default; request body limits; `nosniff`, `DENY` and
  `no-referrer` on every response; a log sanitiser that redacts secrets by field
  name and by value shape; a rate limit on the two routes that spend money.
- Every response carries a request id, in a header and in the body, matching
  every log line for that request.

### Known limitations

- **Nothing is ever written to the machine.** No profile pushes, no settings, no
  mode changes. The four-layer write path in `docs/safety-layers.md` has to
  exist first.
- One user, no roles, and no TLS of its own. Put it behind a reverse proxy if it
  is going to face the internet.
- `cost_estimate` is recorded as NULL: nothing here knows what a token costs on
  the provider you happen to be using, and a made-up figure in a money column is
  worse than an empty one.
- The archive is a single SQLite file on one box. There is no replication and no
  off-site copy but the one you make.

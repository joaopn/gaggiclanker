# Changelog

Notable changes per release. Dates are the day the release was cut.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the versions are [semantic](https://semver.org/). Until 1.0 the database schema
may change between releases; migrations are forward-only and run at boot, so an
upgrade is `docker compose pull && docker compose up -d` — but take a backup
first (`POST /api/backup`), because there is no down-migration.

## [Unreleased]

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
Roaster, origin, variety, process and roast level stay true of every bag you
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
six settings under Settings → Machine. Migration `0010`.

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

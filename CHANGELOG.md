# Changelog

Notable changes per release. Dates are the day the release was cut.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the versions are [semantic](https://semver.org/). Until 1.0 the database schema
may change between releases; migrations are forward-only and run at boot, so an
upgrade is `docker compose pull && docker compose up -d` — but take a backup
first (`POST /api/backup`), because there is no down-migration.

## [Unreleased]

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
/ refine routes, `GET /api/device/writes`, a Drafts page, "Draft profile" on the
shot analysis panel, and "Edit as draft" on a profile version. Migration `0008`.

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

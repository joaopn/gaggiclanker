# Changelog

Notable changes per release. Dates are the day the release was cut.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the versions are [semantic](https://semver.org/). Until 1.0 the database schema
may change between releases; migrations are forward-only and run at boot, so an
upgrade is `docker compose pull && docker compose up -d` — but take a backup
first (`POST /api/backup`), because there is no down-migration.

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

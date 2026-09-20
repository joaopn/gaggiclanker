# Architecture

One process. A FastAPI app on uvicorn serving a React bundle from the same
origin, one SQLite file, one WebSocket to the machine, and an LLM call that runs
as a background task. Nothing else is running, and that is the design rather
than a stage it has not grown out of: the whole thing lives next to an espresso
machine in a kitchen and has to survive a power cut, a restart and a person who
has forgotten how it works.

```
  GaggiMate display board                  browser
        │  ws + http (gated writes)           │  http + SSE
        ▼                                     ▼
  ┌──────────────┐    events    ┌──────────────────────────────┐
  │ device/      │─────────────▶│ infra/  request ids, errors, │
  │  client.py   │              │         the SSE bus, tasks   │
  └──────┬───────┘              └───────────┬──────────────────┘
         │ shots, profiles, notes           │
         ▼                                  ▼
  ┌──────────────┐              ┌──────────────────────────────┐
  │ sync/engine  │─────────────▶│ api/  one router per resource│
  └──────┬───────┘              └───────────┬──────────────────┘
         │                                  │
         ▼                                  ▼
  ┌──────────────┐   diagnostics ┌─────────────────────────────┐
  │ domain/      │◀─────────────▶│ analyzer/ ──▶ llm/ ──▶ model │
  │  slog, index │               │  context, accept            │
  └──────┬───────┘               └───────────┬─────────────────┘
         │                                   │
         ▼                                   ▼
  ┌──────────────────────────────────────────────────────────┐
  │ db/  repositories, migrations, VACUUM INTO backups       │
  │      one SQLite file under DATA_DIR                      │
  └──────────────────────────────────────────────────────────┘
```

## The one fact that drives everything

**The machine is a buffer with about 300 KB of heap that deletes old shots under
storage pressure. gaggiclanker is the archive.**

That is why the raw `.slog` bytes are stored before anything tries to parse
them, why a shot that fails validation is quarantined rather than dropped, and
why the device client is read-only. Losing a shot to a parser bug is worse than
storing one nobody can read yet: the bug can be fixed next week, and by then the
machine's copy is gone.

**Filling the archive is something a person asks for.** `POST /api/sync/run` —
the button on the shots page — is the only thing that starts a pass over the
shots, the profiles or the notes. There is no timer and no pass triggered by a
device event. The engine that ran on its own duplicated what the machine's own
web UI already shows and spent the device's two HTTP slots doing it; what an
archive is actually asked for is "I have pulled some coffee, take it". The one
exception is identity — one `res:ota-settings` frame plus one `GET
/api/settings` at startup and on every connect — because it is what tells the
header whether the machine is there at all.

**The archive holds one machine.** `machines` is a one-row table — `CHECK (id =
1)` — describing whatever host is configured now, and nothing else in the schema
carries a machine id. The host is a setting rather than an identity, so pointing
the container at a new address updates that row and every shot, profile and Set
stays attached; a shot is unique by the id the device gave it, and one Set is
active at a time. Grinders stay plural, because a kitchen really does have
several and a grind number only means something on the grinder it was set on.

## The layers

| Layer | What it owns |
|---|---|
| `api/` | One router per resource. Routes parse input and call services; they never build a response by hand. |
| `analyzer/`, `llm/`, `knowledge/` | One structured LLM call per shot, the context it is given, and the three tiers it is told: the rules, a few retrieved passages of prose, and the insights you have confirmed. |
| `drafts/` | Profile drafts: the write gate, generation from advice, the four safety layers, the push and its rollback. Holds the gate every write to a machine passes. Creating a draft lives apart, in `DraftProposals`, which is built from the database and the settings alone; the chat's tools and the starting-point wizard get that, and only the route-facing service holds the machine connection. |
| `starting/` | The starting-point wizard: the similar-Set query, the context it assembles, the three-option output contract, and the accept that turns one into a Set and a draft. |
| `cleanup/` | Device storage: which shots are eligible to delete off the machine, the plan the Sync page shows, and the run of a plan a person confirmed. |
| `notes/` | Judgements a person sends from the Sync page to the machine's own notes card, and only when ours is newer than its. |
| `tools/` | The tool registry — one definition per tool, three consumers — and the SQL sandbox behind `query_shots`. `tools/mcp/` is the chat's database tool: the registry as an MCP server over stdio (`gaggiclanker mcp`), which the `claude_code` provider spawns for its tool loop. It opens the archive and nothing else — no network endpoint, no machine connection, no setting. Read and propose only; never a write to the machine. |
| `chat/` | The tool loop, the Set-scoped context a conversation starts from, and the streamed, resumable run. |
| `sync/` | The index diff, the shot download, the profile and notes mirrors. |
| `domain/` | The `.slog` and index parsers, diagnostics, scoring. Pure functions over bytes and numbers. |
| `device/` | `DeviceConnection`: the one owner of the client and the sync engine, rebuilt live when the machine settings change. `GaggimateClient`: one WebSocket, bounded HTTP, ten read methods and seven gated write methods — nothing else. `save_profile` is reached only by `POST /api/profile-drafts/{id}/push`, `delete_profile` only by `POST /api/profile-drafts/{id}/rollback`, `delete_shot` only by `POST /api/device/cleanup/run` and `save_shot_notes` only by `POST /api/device/notes/push`; `select_profile`, `favorite_profile` and `unfavorite_profile` have no route (only `scripts/profile_gate.py` selects). Every write passes the gate behind `deviceWritesEnabled` and leaves a `device_writes` row. |
| `db/` | Repositories — the only code that writes SQL — plus migrations and backups. |
| `infra/` | Request ids, the error envelope, the SSE bus, the task registry, the auth guard's neighbours. |
| `auth/` | Optional single-user auth: the policy, the password hashing, the ASGI guard. |

Dependencies point downward and never back up. `domain/` knows nothing about
HTTP; `db/` knows nothing about the device; `device/` knows nothing about the
database.

## Decisions worth knowing

**Every response is the same envelope**, success or failure, including an
unhandled crash: `{ok, data | error, meta}`. `meta.request_id` matches the
`x-request-id` header and every log line for that request, so a user reporting a
failure hands you the one string that finds it. The boundary that guarantees
this lives in a pure-ASGI middleware rather than a FastAPI exception handler,
because Starlette installs its own error handler *outside* every application
middleware — anything caught there has already lost the request-id context.

**Middleware is pure ASGI throughout.** `BaseHTTPMiddleware` buffers responses
through an anyio task group, which is fatal to a server-sent event stream, and
the sync feed a pull reports its progress on is one.

**Every write to the database goes through a pydantic model.** No dicts into
SQL. Repositories are the only code that writes SQL, services hold the logic,
routes parse and delegate.

**A Set version's recipe is immutable; its prediction and its outcome are not,
and each is writable at a different time.** The recipe is written once and
changed only by appending another version. The prediction — what the version was
expected to do, against which earlier version — can be written and re-written
only while the version has no shots and no grade, because one typed afterwards
would grade itself. The outcome, somebody's grade of that prediction, needs a
prediction and a shot labelled Keep or Improve before it can be recorded, and
can be changed or cleared for ever after. `set_versions` carries all three and
the comment on it in `0005_sets.sql` says which is which.

Both windows are enforced in `SetsRepository`, beside the other rules that
depend on rows in another table. A trigger could raise on the first one, and
that is the reason it does not: the rule would then be written twice, in two
wordings that can drift; the refusal would reach the client as a constraint
failure rather than as an error code the route turns into a sentence
(`VERSION_HAS_SHOTS`, `VERSION_HAS_OUTCOME`); and a test of it would have to go
through SQL instead of through the method every caller uses. The window is
honest rather than airtight — unfiling every shot and clearing the grade opens
it again — and it is meant to be: it guards against writing a prediction down
after the fact, not against somebody setting out to deceive themselves.

**A version is a dead end when it is not on the live line.** The live line is
walked backwards from the Set's current version: from a version that restores an
earlier one, the step goes to what it restored; from any other, to its parent.
Everything the walk does not pass through is a dead end, and the log mutes it.
Stated as a walk rather than as "the versions between a roll back and its
target", because the two stop agreeing the moment roll backs overlap — with v5
restoring v2, v6 restoring v4 and v7 restoring v3, the line is v7, v3, v2, v1
and v6 is a dead end although nothing later spans it. `dead_end_ids` is a pure
function over the version list the page already holds, and it terminates on data
no route can write (a forward or self reference, a parent cycle) because a
`while` over a linked list is the shape that would otherwise hang the page.

**Settings are one declaration each.** A row in `SETTINGS_REGISTRY` gives you
the database column, the API field, the validation and the UI control. A setting
is what the database holds or what the declaration defaults to — nothing else,
and no environment variable, so there is no second surface that can disagree with
the Settings page. Only what the process needs before it can open the database
(`DATA_DIR`, `HOST`, `PORT`, `LOG_LEVEL`, `LOG_JSON`, `WEB_DIST`, `CORS_ORIGINS`)
comes from the environment; a variable that used to configure a setting is named
once at boot (`setting_env_ignored`) and otherwise ignored, and the one that used
to allow writes to the machine stops the boot instead.
Secrets are write-only through the API: a `GET` returns a four-character hint.

**Credentials never come from the environment.** No setting reads a variable at
all, the sign-in settings included: they are entered in the Settings page and
live in the database. A boot that finds one of the variables that used to carry a
credential set refuses to start, naming it, rather than start with authentication
silently off or a key still sitting in a compose file.

**Every credential for an external service lives only in the database** — and
that includes what libraries and child processes would pick up on their own.
Outbound HTTP clients are built by `infra/outbound.py` with `trust_env` off (no
`.netrc`, no proxy taken behind the app's back), honour an environment proxy only
when it names no user or password, and do not follow redirects. The SDK clients
on top (`llm/providers/`) are always handed the stored key and an explicit base
URL, and discard the SDKs' environment headers, organisation and project
variables and profile discovery; the Claude Code CLI child gets an allow-listed
environment whose only credential is the stored token. The boot refusal covers
the SDKs' credential variables and any proxy variable carrying credentials, as
an upgrade guard; `tests/llm/test_outbound_isolation.py` sets every candidate to a
sentinel and inspects the requests. The GaggiMate has no authentication today,
and its client does not read the environment either. Any future outbound
connection follows the same rule: its credential is a secret setting, and its
HTTP client comes from `infra/outbound.py`.

**The machine connection is one object, and a settings change rebuilds it.**
`DeviceConnection` owns the device client and the sync engine; routes and the
cleanup, notes and profile-draft services ask it for the current client at the
moment they act rather than keeping one. Changing the host, the protocol, the
timeout or the sync switch through `PATCH /api/settings` compares the effective
values before and after and, if they moved, stops the engine's loops and the
client and builds new ones — with no restart, and with the same write gate over
the same settings. The change and the rebuild happen under one lock that every
machine-bound operation also registers through, so a change that would move the
connection while a profile push or rollback, a cleanup run, a notes send or a
pull is using the machine is a 409 naming it, and nothing is stored. A pull asked
for while a change is being stored waits for the change and goes to whatever
connection it leaves. An identity read is not held to that: it is cut, and the
new connection reads identity again on connect. A pass cut short — by a rebuild
or by shutdown — is recorded as an error saying it was stopped, never as `ok`,
and a rebuild that fails part-way leaves no connection at all, so the next save
tries again.

**Migrations are forward-only and immutable once shipped.** Each file is applied
inside a transaction that also carries its `schema_migrations` row and its
sha256, so a failure half-way leaves nothing behind. Editing a shipped migration
is a hard error at boot, because otherwise two installs quietly end up with
different schemas and nobody finds out until a query returns the wrong answer.

**The LLM call does not run inside the HTTP request.** The route opens a
`running` row, hands the work to the app's `TaskRegistry` and answers 202; the
page follows the event stream. `docker stop` allows ten seconds, and a request
holding a two-minute provider call would be killed mid-flight with the browser
still waiting. The task name is the idempotency rule — claimed synchronously, so
a second tab pressing the button gets the running row rather than a second bill.

**A provider failure is a stored row, not an exception.** A batch of sixty shots
cannot be taken down by the eleventh. A run cut off by a restart is marked
`interrupted` at the next boot, along with any sync run left `running`, so
nothing is left as a spinner nobody can clear.

**Auth is off unless configured, and the guard covers everything.** Sign-in
is configured in the database only — Settings → Authentication — and never
through the environment. One
pure-ASGI middleware in front of every `/api/*` route — SSE streams and the
OpenAPI document included — with `/health`, the two public auth routes and the
web bundle outside it. A test enumerates the application's own OpenAPI document
and asserts each route answers 401, so a router added later is covered the day
it is mounted.

**What can reach the machine is two closed lists, enforced by a test.** Ten
reads, and seven writes behind a switch that is off by default: five profile
operations, plus a shot delete and a notes save that each carry a rule of their
own. **And who may start one is a rule too**: profiles may be pushed by the app;
everything else written to or deleted from the machine happens only from the
Sync page, by a person — no timer, no hook after a pull, no judgement save and
no tool a model calls starts one. See [`safety-layers.md`](safety-layers.md).

## What runs where

* **In the lifespan, before any request**: the configuration check (which
  refuses to start while a retired credential variable is set), migrations, prompt
  and rule seeding, boot reconciliation, then the services, then the background
  tasks. Shutdown is the reverse, and it cancels the task registry
  before closing the database so nothing is mid-write when the file is released.
* **In a background task**: the device supervisor, the sync loops — which do
  nothing until something pokes them — and every analysis.
* **In the request**: everything else, which is all SQLite reads a millisecond
  wide.

## Testing shape

A real app, a real SQLite file, a temp `DATA_DIR` per test, `httpx.ASGITransport`
in process. No in-memory database and no mocked repositories, because WAL,
foreign keys, `VACUUM INTO` and the migration ledger only exist on a real file.
The device layer runs against `gaggiclanker/device/fake.py`, a real server that
reproduces the firmware's quirks; the opt-in `-m simulator` suite then runs the
same code against the actual firmware compiled natively.

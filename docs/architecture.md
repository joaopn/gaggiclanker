# Architecture

One process. A FastAPI app on uvicorn serving a React bundle from the same
origin, one SQLite file, one WebSocket to the machine, and an LLM call that runs
as a background task. Nothing else is running, and that is the design rather
than a stage it has not grown out of: the whole thing lives next to an espresso
machine in a kitchen and has to survive a power cut, a restart and a person who
has forgotten how it works.

```
  GaggiMate display board                  browser
        │  ws + http (read only)              │  http + SSE
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
them, why a shot that fails validation is quarantined rather than dropped, why
sync is prompt rather than scheduled, and why the device client is read-only.
Losing a shot to a parser bug is worse than storing one nobody can read yet: the
bug can be fixed next week, and by then the machine's copy is gone.

## The layers

| Layer | What it owns |
|---|---|
| `api/` | One router per resource. Routes parse input and call services; they never build a response by hand. |
| `analyzer/`, `llm/`, `knowledge/` | One structured LLM call per shot, the context it is given, and the rules it is told. |
| `sync/` | The index diff, the shot download, the profile and notes mirrors. |
| `domain/` | The `.slog` and index parsers, diagnostics, scoring. Pure functions over bytes and numbers. |
| `device/` | `GaggimateClient`: one WebSocket, bounded HTTP, ten read methods and nothing else. |
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
the live shot view is one.

**Every write to the database goes through a pydantic model.** No dicts into
SQL. Repositories are the only code that writes SQL, services hold the logic,
routes parse and delegate.

**Settings are one declaration each.** A row in `SETTINGS_REGISTRY` gives you
the database column, the API field, the validation, the environment variable and
the UI control. Precedence is database > environment > default, so a value
changed in the UI is not silently reverted by a variable in the compose file.
Secrets are write-only through the API: a `GET` returns a four-character hint.

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

**Auth is off unless configured, and the guard covers everything.** One
pure-ASGI middleware in front of every `/api/*` route — SSE streams and the
OpenAPI document included — with `/health`, the two public auth routes and the
web bundle outside it. A test enumerates the application's own OpenAPI document
and asserts each route answers 401, so a router added later is covered the day
it is mounted.

**The device is read-only, enforced by a test.** See
[`safety-layers.md`](safety-layers.md).

## What runs where

* **In the lifespan, before any request**: migrations, prompt and rule seeding,
  the auth password bootstrap, boot reconciliation, then the services, then the
  background tasks. Shutdown is the reverse, and it cancels the task registry
  before closing the database so nothing is mid-write when the file is released.
* **In a background task**: the device supervisor, the sync loops, and every
  analysis.
* **In the request**: everything else, which is all SQLite reads a millisecond
  wide.

## Testing shape

A real app, a real SQLite file, a temp `DATA_DIR` per test, `httpx.ASGITransport`
in process. No in-memory database and no mocked repositories, because WAL,
foreign keys, `VACUUM INTO` and the migration ledger only exist on a real file.
The device layer runs against `gaggiclanker/device/fake.py`, a real server that
reproduces the firmware's quirks; the opt-in `-m simulator` suite then runs the
same code against the actual firmware compiled natively.

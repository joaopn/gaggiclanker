# gaggiclanker

A self-hosted archive and analyst for a [GaggiMate](https://gaggimate.eu)
espresso machine. It keeps every shot the machine has ever pulled — raw `.slog`
bytes, every sample, phases, the device's own notes and profiles — shows them
with curves and deterministic diagnostics, lets you judge each shot and group
shots into versioned **Sets** (bean + hardware + profile + grind/dose/yield), and
runs a per-shot LLM analysis through whichever provider you point it at.

The machine holds a few hundred KB of flash and deletes old shots when it runs
low. This is the thing that remembers them.

> **Status: early.** The backend scaffold is in place: the API skeleton,
> settings, the database with a migrations ledger, backups and the container.
> The device client, the shots UI and the analyzer are not built yet.

## Quick start

```bash
git clone <this repo> && cd gaggiclanker
cp .env.example .env                 # set GAGGIMATE_HOST to your machine's IP
docker compose up -d --build         # builds the image and starts on :8000
curl localhost:8000/health           # {"ok":true,"data":{"status":"ok",...}}
open http://localhost:8000           # the UI (once the front end is built)
```

Your data lives in `./data` — one SQLite file plus `backups/`. Back it up by
copying that directory, or call `POST /api/backup` for a consistent snapshot
taken while the app is running.

You do not need to create `./data` first. Docker creates a missing bind-mount
source as `root:root`, so the container's entrypoint starts as root, hands that
one directory to the unprivileged app user, and drops to it before any
application code runs. If you would rather not have the entrypoint touch
ownership at all, `compose.yml` documents a named-volume alternative next to the
mount, including how to find and back up the file inside it.

## Development

```bash
uv sync                                                        # install into .venv/
uv run uvicorn gaggiclanker.main:app --reload --no-access-log  # dev server on :8000
uv run pytest                                                  # the suite, offline
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The API documents itself at `/api/docs`. Every response uses the envelope
`{ok, data | error, meta}` — including an unhandled crash; `meta.request_id`
matches the `x-request-id` header and every log line for that request.

Running the app outside Docker, `DATA_DIR` defaults to `./data` and the app
creates it. If it cannot write there it says so at startup, with the path and
the uid, rather than failing later with SQLite's "unable to open database file".

## Configuration

`.env.example` documents every variable with the reasoning behind it. The one
that matters is `GAGGIMATE_HOST` — the IP or hostname of the display board.
Prefer a fixed IP: mDNS (`gaggimate.local`) does not resolve from inside a Docker
bridge network, and the firmware disables mDNS entirely when HomeKit is on.

Anything you change in the Settings page is stored in the database and wins over
the environment, so a value edited in the UI does not revert on restart.

## Contributing

The conventions are the response envelope, pydantic on every database write,
repro-first bug fixing, the migration rules and the list of device gotchas worth
knowing before touching the sync code. `CHANGELOG.md` has what landed in each
release.

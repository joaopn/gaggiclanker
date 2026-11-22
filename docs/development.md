---
title: Development
---
# Development

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

## Working without a machine

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

It holds the fixture archive, so pressing Sync in the top bar against it fills
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

## Documentation

* [`architecture.md`](architecture.md) — what the pieces are and why
  they are arranged this way.
* [`device-gotchas.md`](device-gotchas.md) — the twelve firmware
  behaviours that shaped the sync engine. Read before touching `device/` or
  `sync/`.
* [`safety-layers.md`](safety-layers.md) — what can harm the machine,
  and the four layers that will guard a write when there is one.
* [`CHANGELOG.md`](../CHANGELOG.md) — what is in each release.

## Contributing

[`architecture.md`](architecture.md) holds the conventions — the
response envelope, pydantic on every database write, repro-first bug fixing,
the schema rule and the list of device gotchas worth knowing before
touching the sync code. [`CHANGELOG.md`](../CHANGELOG.md) has what landed in each
release.

# gaggiclanker

A self-hosted archive and analyst for a [GaggiMate](https://gaggimate.eu)
espresso machine. It keeps every shot the machine has ever pulled — raw `.slog`
bytes, every sample, phases, the device's own notes and profiles — shows them
with curves and deterministic diagnostics, lets you judge each shot and group
shots into versioned **Sets** (bean + hardware + profile + grind/dose/yield), and
runs a per-shot LLM analysis through whichever provider you point it at.

The machine holds a few hundred KB of flash and deletes old shots when it runs
low. This is the thing that remembers them.

> **Status: early.** The archive works end to end — sync, the shots UI, Sets and
> judgement, the LLM layer and the per-shot analyzer. Authentication and the
> release hardening are not built yet.

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

### Working without a machine

There is a fake GaggiMate in the package. It is a real HTTP + WebSocket server
loaded from `tests/fixtures`, with the firmware's quirks reproduced — 2 Hz
telemetry, `evt:history-shot-saved`, half-written `.slog` files, the SPA served
where binary was asked for, the three-client limit:

```bash
uv run python -m gaggiclanker.device.fake --port 8090   # in one terminal
GAGGIMATE_HOST=127.0.0.1:8090 uv run uvicorn gaggiclanker.main:app --reload
```

Add `--brew-every SECONDS` and the fake pulls a shot on that interval — fill,
soak, ramp and decline at 2 Hz against a volumetric target, then the firmware's
own save sequence — which is what the live view and the "a new shot appeared"
refresh are developed against:

```bash
uv run python -m gaggiclanker.device.fake --port 8090 --brew-every 30
```

Or seed the archive from files, with no machine at all. The web UI on the
display exports a shot as `shot-<id>.json` and a profile as `profile-<id>.json`;
those files are the only way back for a shot the machine has already deleted,
and the importer reads them into the same tables the sync engine writes:

```bash
uv run gaggiclanker import tests/fixtures/exports       # or any folder, file or zip
uv run gaggiclanker import ~/exports --replace          # overwrite what is already stored
```

The same thing is `POST /api/import` and the Import page in the UI. Importing
the same shot twice is a no-op, and a file that does not parse is reported on
its own — the rest of the batch still lands.

The whole offline suite runs against it, so "works against the fake" means
rather more than it usually does. `scripts/sim.sh test` is the next step up: it
clones the firmware to a scratch tree, patches its simulator shim, builds the
real display firmware natively and runs the `-m simulator` tests against it.
The reference checkout under `external/` is never modified.

## Configuration

`.env.example` documents every variable with the reasoning behind it. The one
that matters is `GAGGIMATE_HOST` — the IP or hostname of the display board.
Prefer a fixed IP: mDNS (`gaggimate.local`) does not resolve from inside a Docker
bridge network, and the firmware disables mDNS entirely when HomeKit is on.

Anything you change in the Settings page is stored in the database and wins over
the environment, so a value edited in the UI does not revert on restart.

### LLM settings

Per-shot analysis goes through one provider, chosen with `GAGGICLANKER_LLM_PROVIDER`:
`claude_code` (the default — it runs the Claude Code CLI against a Claude
subscription, so there is no API key to buy), `anthropic`, `openrouter`,
`openai`, `ollama`, `lmstudio`, or `openai_compatible` for any other gateway.

Each provider has its own credential and none is ever lent to another:
`GAGGICLANKER_LLM_API_KEY` for the OpenAI-compatible one, `ANTHROPIC_API_KEY`,
`CLAUDE_CODE_OAUTH_TOKEN`. For `claude_code` that token comes from
`claude setup-token` — an interactive `claude login` is not enough, because every
call runs the CLI with a scratch `HOME` so nothing on the box leaks into the
prompt, and that hides `~/.claude` too.

`GAGGICLANKER_MODEL` is the default model; `..._MODEL_ANALYSIS`, `..._MODEL_DRAFT`
and `..._MODEL_CHAT` override it per kind of call, and an empty value lets the
provider choose. `GAGGICLANKER_LLM_TIMEOUT_S` bounds one attempt, and
`GAGGICLANKER_LLM_RATE_LIMIT_RETRIES` is a process-wide budget: when the provider
throttles the account the whole app stops rather than failing every queued shot
in turn, and the Settings page has the button that starts it again. The Settings
page also edits the prompts themselves — they are rows in the database, seeded
from the YAML files in `gaggiclanker/prompts/`, and an edit takes effect on the
next call without a restart.

### The analysis

One structured call per shot. It is handed the diagnostics with their band
labels, the Set (bean with days off roast, grinder with its own step unit, the
profile JSON, the grind/dose/yield targets), the previous five shots in the same
Set with your verdict on each and the advice that followed them, your verdict on
this one — marked as ground truth for taste — and the knowledge rules that match.
It answers with a diagnosis and prioritised suggestions, and accepting one
records a new Set version with that single field changed, so "did following the
advice help" is a question the trend chart answers.

The knowledge rules are on the **Knowledge** page: a small tier of dial-in
heuristics — temperature by roast, the pressure matrix by roast and process,
ratio and time by style, freshness windows, what each diagnostic band means,
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

## Contributing

The conventions are the response envelope, pydantic on every database write,
repro-first bug fixing, the migration rules and the list of device gotchas worth
knowing before touching the sync code. `CHANGELOG.md` has what landed in each
release.

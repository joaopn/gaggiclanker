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
cp .env.example .env                 # set GAGGIMATE_HOST to your machine's IP
docker compose up -d --build         # builds the image and starts on :8000
curl localhost:8000/health           # {"ok":true,"data":{"status":"ok",...}}
open http://localhost:8000           # the UI
```

`compose.yml` uses **host networking** by default, so the app is on port 8000 of
the box you started it on and mDNS names resolve. Docker Desktop does not
support that; swap in the `ports:` block the file documents next to it and put
an IP in `GAGGIMATE_HOST`.

### First run

1. **Point it at the machine.** `GAGGIMATE_HOST` is the display board's IP or
   hostname, with no scheme (`192.168.1.50`, or `192.168.1.50:80`). Prefer a
   fixed IP or a DHCP reservation — see Troubleshooting for why the name your
   browser resolves may not resolve here.
2. **Open the UI** at `http://<this box>:8000`. The pill in the header goes
   green within a few seconds of the WebSocket connecting.
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
   paste the token into the Settings page (or set `CLAUDE_CODE_OAUTH_TOKEN`).
   An interactive `claude login` is **not** enough — every call runs with a
   scratch `HOME`, which is what keeps this repository out of the prompt and
   hides `~/.claude` along with it. Any OpenAI-compatible gateway works too;
   see LLM settings below.
6. **Take a backup** once there is something worth keeping: **Settings →
   Backup**, or `curl -X POST localhost:8000/api/backup`.

Your data lives in `./data` — one SQLite file plus `backups/`. Back it up by
copying that directory, or call `POST /api/backup` for a consistent snapshot
taken while the app is running.

### The pages

Eight of them, in the order the sidebar lists them:

| Page | `g` | What it is |
| --- | --- | --- |
| **Shots** | `g s` | The archive: the list, the filters, one shot with its curve and diagnostics. The pull button and the import drop zone are both here. |
| **Chat** | `g c` | The tool-using conversation, optionally scoped to a Set. |
| **Profiles** | `g p` | What is on the machine, what is staged for it, and every version a shot can resolve to. |
| **Sets** | `g e` | Bean + hardware + profile + recipe, versioned, with the trend across versions. |
| **Beans** | `g b` | The coffees: roaster, origin, variety, process, roast level. |
| **Hardware** | `g h` | Grinders and machines. |
| **Knowledge** | `g k` | The dial-in rules, the prose documents, and the insights waiting for a decision. |
| **Settings** | `g ,` | The machine, the provider, the prompts, backup, and the device-write switch. |

Two more pages exist without a sidebar row, because each has a better way in.
The device page is behind the status pill in the header — you go there when the
pill says something is wrong — and the old import page is now the drop zone on
the Shots page. `/device`, `/import` and `/drafts` all still resolve, the last
two by redirecting.

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

It holds the fixture archive, so pressing "Pull from machine" against it fills
the UI with real shots and real curves.

Or seed the archive from files, with no machine at all. The web UI on the
display exports a shot as `shot-<id>.json` and a profile as `profile-<id>.json`;
those files are the only way back for a shot the machine has already deleted,
and the importer reads them into the same tables the sync engine writes:

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

`.env.example` documents every variable with the reasoning behind it. The one
that matters is `GAGGIMATE_HOST` — the IP or hostname of the display board.
Prefer a fixed IP: mDNS (`gaggimate.local`) does not resolve from inside a Docker
bridge network, and the firmware disables mDNS entirely when HomeKit is on.

Anything you change in the Settings page is stored in the database and wins over
the environment, so a value edited in the UI does not revert on restart.

### Authentication

Off unless you set two variables, because the common case is a box on a home
network where the espresso machine itself has no auth, no TLS and no CORS —
gaggiclanker is already the strictest thing on that wire. Turn it on if this box
is port-forwarded, on a shared network, or behind a reverse proxy the internet
can reach.

```bash
AUTH_USER=barista AUTH_PASSWORD='something long' docker compose up -d
```

`AUTH_PASSWORD` **seeds** the first password: the first boot that finds none
stored hashes it with argon2id, keeps the hash, and never writes the plain value
anywhere. After that the line is inert, because a boot that re-applied it would
silently undo a password changed in the UI. Set `AUTH_PASSWORD_HASH` instead if
you would rather the plain password never appeared in a file or a
`docker inspect`:

```bash
docker compose run --rm --entrypoint python gaggiclanker -c \
  "from argon2 import PasswordHasher; print(PasswordHasher().hash(input()))"
```

After that, change it under **Settings → Authentication**, which posts the plain
password to `POST /api/auth/password` and lets the server hash it. The settings
API refuses to store a hash directly (`authPasswordHash` is read-only there):
a masked box labelled "password hash" invites typing the *password* into it, and
a stored value argon2 cannot verify is a credential that authenticates nobody.

A change of password — or of username — revokes every open session, including
the one that made the change. The tokens are the credential once they are
issued; leaving a thirty-day token working after a password change would be
theatre.

The username switch is re-read on every request, so turning auth on from the
Settings page takes effect immediately with no restart. Set the password first
and the username second: the username is what turns the lock.

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
TOKEN=$(curl -sX POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"barista","password":"something long"}' | jq -r .data.token)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/shots
```

Auth is the boundary, not a full security posture: there is one user, no roles,
and no TLS of its own. Put it behind a reverse proxy that terminates TLS if it
is going to face the internet.

### Backup and restore

Everything is in one SQLite file under `DATA_DIR` (`./data` by default), so a
backup is a file copy and a restore is a file copy back.

```bash
curl -X POST localhost:8000/api/backup     # or Settings -> Backup in the UI
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
labels, the Set (the bean with its roast level and process, the grinder with its
own step unit, the profile JSON, the grind/dose/yield targets), the previous five shots in the same
Set with your verdict on each and the advice that followed them, your verdict on
this one — marked as ground truth for taste — and the knowledge rules that match.
It answers with a diagnosis and prioritised suggestions, and accepting one
records a new Set version with that single field changed, so "did following the
advice help" is a question the trend chart answers.

The knowledge rules are on the **Knowledge** page: a small tier of dial-in
heuristics — temperature by roast, the pressure matrix by roast and process,
ratio and time by style, rest times, what each diagnostic band means,
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

Scope a conversation to a Set and it starts with that Set's recipe, its recent
shots and the confirmed insights that apply, so "how is it going?" is a question
with an answer. The **Discuss in chat** button on a shot and on a Set is the same
thing with the question already typed.

Nineteen tools, and fourteen of them only read. The other five are `propose`:
they either write something you still have to decide about — a new Set version
with `origin=chat`, a profile draft that goes through the same schema,
safety-policy and clamp checks as one typed by hand, an insight stored
**unconfirmed** that reaches no future prompt until you confirm it — or they
queue work that spends provider tokens, which is why `run_analysis` and
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

Open a coffee nobody has brewed and the first step of **New Set** answers the
question you actually have. It shows what this archive has already brewed on
*this grinder* that resembles it — same roast level, same process, same
origin — with how each one went: shots, mean rating, mean execution score, ratio
and time. That half is one SQL query, costs nothing, and is worth reading on its
own. A recipe with no shots behind it is never offered: it records an intention,
not a result.

Press **Ask for suggestions** and the model turns that plus the rule tier into
three complete first recipes — conservative, recommended, adventurous — each
with a grind, a dose, a yield, a temperature, a profile and a rationale citing
what it leaned on.

It will not invent a grind number. A grinder's scale is arbitrary and there is
no conversion between two of them, so a figure on your dial is offered only when
your usual setting or a past Set on the same grinder anchors it; otherwise the
answer is relative and the card says so.

Taking one creates the Set with `origin=starting_point`, and — when the option
authored a whole profile rather than pointing at one you already have — a draft
staged on the **Profiles** page. Nothing is pushed; you approve it. The Beans
page has the same shortcut for the coffee you are looking at, and the chat can
ask through the `starting_point` tool.

### MCP: the same tools, for other agents

Everything the chat can do is also exposed over the Model Context Protocol, so
Claude Desktop, `claude -p`, or anything else that speaks MCP gets exactly the
capabilities the in-app chat has. Two transports:

**Streamable HTTP at `/mcp`**, behind the same bearer token as `/api`. **Off by
default** (`mcpEnabled` in Settings) and on purpose: it hands an outside agent
the whole archive, so it is a switch you throw when you want it, the way
`deviceWritesEnabled` is. With it on, authenticate with a token from
`POST /api/auth/login`:

```bash
TOKEN=$(curl -sX POST http://gaggiclanker.local:8000/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"username": "barista", "password": "..."}' | jq -r .data.token)
# Any MCP client that can send a header:
#   endpoint: http://gaggiclanker.local:8000/mcp
#   header:   Authorization: Bearer $TOKEN
```

The token is a session token and lasts `authTokenTtlSeconds` (thirty days by
default), so it is long-lived enough to paste into a client's configuration;
signing out of the browser does not revoke it, but changing the password revokes
every session including this one. With auth off — the default on a home LAN —
no header is needed.

**stdio**, for a client that launches the server itself. This is what Claude
Desktop wants, what the `claude_code` chat provider generates for itself, and it
needs no running server and no switch:

```json
{
  "mcpServers": {
    "gaggiclanker": {
      "command": "/path/to/gaggiclanker/.venv/bin/python",
      "args": ["-m", "gaggiclanker", "mcp"],
      "env": { "DATA_DIR": "/path/to/gaggiclanker/data" }
    }
  }
}
```

Point `DATA_DIR` at the same directory the server uses and start the server once
first: the stdio entry point deliberately runs no migrations, because a second
process migrating a database the application is also using is a race. Add
`GAGGICLANKER_MCP_SET_ID` to the `env` block to scope it to one Set.

Device-write tools are excluded from MCP unless **both** `deviceWritesEnabled`
and `mcpDeviceWrites` are on. None ship yet; that is the gate they will be
behind.

## Troubleshooting

**The device pill never goes green.**
Check `GAGGIMATE_HOST` first: `curl http://<host>/api/status` should answer a
small JSON document. Then check you are not out of WebSocket slots — see below.
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
latches the whole app on purpose and the Settings page has the button that
clears it.

**I am locked out after mistyping the password.**
Five failures from one address lock that address for sixty seconds, and each
further attempt extends it. Wait a minute. If you have genuinely lost the
password, set `AUTH_USER=` empty and restart to turn auth off, then set a new
one under Settings → Authentication and put the username back.

**Sign-in says "the stored password hash is not an argon2 hash".**
Something wrote a password, not a hash, into `AUTH_PASSWORD_HASH`. Auth is on
and refusing everybody, which is the correct thing for a broken credential to
do. Clear that variable and set `AUTH_PASSWORD` to the password you want, then
restart.

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

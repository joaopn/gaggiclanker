---
title: Configuration
parent: User guide
nav_order: 5
---
# Configuration

The Settings pages are where everything is configured; the one setting that
matters is the machine's host — the IP or hostname of the display board. Prefer a
fixed IP: mDNS (`gaggimate.local`) does not resolve from inside a Docker bridge
network, and the firmware disables mDNS entirely when HomeKit is on.

*How this works: [Configuration and data](configuration-and-data.md).*

**There is no configuration file, and no environment variable for any of it.**
A setting is what the Settings page saved, or the default this release ships;
those are the only two possibilities, and `GET /api/settings` says which of them
each key came from. Nothing is copied, nothing is edited before the first start,
and nothing you change in the UI reverts on restart.

The one exception is the handful of values the process needs *before* it can
open the database, which the image already sets and most people never touch:

| Variable | Default | What it is |
|---|---|---|
| `DATA_DIR` | `./data` (`/app/data` in the image) | Where the SQLite file lives. |
| `HOST` | `0.0.0.0` | Bind address. |
| `PORT` | `8042` | Bind port; the healthcheck reads it too. The one value given at spawn — see [Quick start](getting-started.md#quick-start). |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`. |
| `LOG_JSON` | `true` | JSON log lines, or human-readable console output for development. |
| `WEB_DIST` | `<repo>/web/dist` | Where the built SPA is; the image sets it, a checkout does not need to. |
| `CORS_ORIGINS` | empty | Comma-separated origins allowed to call the API. Only the Vite dev server needs one. |

Each also accepts a `GAGGICLANKER_`-prefixed spelling (`GAGGICLANKER_DATA_DIR`)
for a box running several services. The image sets the ones a container needs.
`compose.yml`'s `environment:` block carries exactly one of them — `PORT`,
defaulting to 8042, because the app must bind before it can read a database, so
it is given at spawn (`PORT=9000 docker compose up -d --build`) and stored
nowhere. Everything else in that block is not configuration at all: it forwards
the variable names a boot refuses, so that a credential or the retired
device-writes switch left in an old `.env` or in your shell stops the container
instead of being silently ignored. There is a commented-out `LOG_LEVEL` line
there if you want a different log level.

**Upgrading from a release that read settings from the environment.** Before you
pull, store anything you had set — in an old `.env`, in `compose.yml`'s
`environment:`, or in the shell — in the database, because none of it is read any
more. A boot that still finds one of those variables set logs
`setting_env_ignored` with the names (never the values) and carries on with the
database's values. Two are refused rather than ignored: any variable that used
to carry a credential, and `GAGGICLANKER_DEVICE_WRITES_ENABLED`, which used to
open the only path from this box to the machine — the container exits naming it,
because silently ignoring either would leave the box less protected than its
owner believes. If `git pull` complains about your own `.env`, move it aside —
nothing parses one any more, however you run this: not the settings, not the
bootstrap values, not the credential check. A boot that finds a file called
`.env` beside it logs `dotenv_file_ignored` with its full path, so it is not
mistaken for configuration.

Compose is the one thing that still looks at such a file, and only in one narrow
way: it substitutes `${VAR}` in `compose.yml` from a `.env` in the project
directory, and `compose.yml` names exactly the variables a boot refuses. So a
stale `.env` holding `AUTH_USER`, a provider key or
`GAGGICLANKER_DEVICE_WRITES_ENABLED` still stops the container, loudly, with the
names in the log — while a stale `DATA_DIR` or `PORT` in the same file reaches
nothing.

**Typing the same value into the old Settings page and pressing Save does not
store it.** The form sends only the fields whose value differs from the one it is
showing you, and on the old version a value coming from the environment is
already the value shown — so Save sends an empty change and the value is lost at
the upgrade. Two ways round it, both on the old version, before you pull:

* in the UI: change the field to something else, **Save**, change it back to what
  you want, **Save** again; or
* store the values in one request:

  ```bash
  curl -X PATCH http://localhost:8000/api/settings \
    -H 'content-type: application/json' \
    -d '{"gaggimateHost": "192.168.1.50", "gaggimateTimeoutSeconds": 20}'
  ```

  Add `-H "Authorization: Bearer <token>"` if sign-in is on. Check what stuck
  with `curl -s localhost:8000/api/settings`: every key you moved should read
  `"source": "database"`.

  (Port 8000 because that is the old version's default; the new one is 8042.)

**The machine settings apply immediately.** Saving a new host, protocol, timeout
or the sync switch under Settings → Machine access closes the connection and opens the
new one, with no restart; the header pill follows within a few seconds. While a
sync (the profile list's write phase included) is using the machine, such a
change is refused with the reason and nothing is saved — wait for it to finish
and save again.

**Credentials never come from the environment.** The sign-in user and password,
and every LLM provider's API key or token, are entered in the Settings page and
live in the database only — and nothing else can supply one: the provider
clients ignore the SDKs' own key, header, organisation and base-URL variables,
profile files and `.netrc`, and a proxy is used only if it names no user or
password. The variables that used to carry a credential, or that the SDKs would
read one from, are refused: a boot that finds one set in the environment (in any
letter case), or a proxy variable with a user or password in it,
logs `auth_env_refused` with the variable names (never a value) and exits, rather
than start with authentication silently switched off. Empty values count as
unset.

## Authentication

*How this works: [Configuration and data](configuration-and-data.md#sign-in).*

Off until you turn it on, because the common case is a box on a home network
where the espresso machine itself has no auth, no TLS and no CORS —
gaggiclanker is already the strictest thing on that wire. Turn it on if this box
is port-forwarded, on a shared network, or behind a reverse proxy the internet
can reach.

It is configured in one place: **Settings → Authentication**. Set the password
first, then the username — the username is what turns the lock. The password
box posts the plain password to `POST /api/auth/password`, which hashes it with
argon2id on the server and stores only the hash. Nothing in the environment can
set, seed or override either of them. The settings API refuses to store a hash
directly (`authPasswordHash` is read-only there): a masked box labelled
"password hash" invites typing the *password* into it, and a stored value argon2
cannot verify is a credential that authenticates nobody.

A change of password — or of username — revokes every open session, including
the one that made the change. The tokens are the credential once they are
issued; leaving a thirty-day token working after a password change would be
theatre.

The switch is re-read on every request, so turning auth on from the Settings
page takes effect immediately with no restart.

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
TOKEN=$(curl -sX POST localhost:8042/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"barista","password":"something long"}' | jq -r .data.token)
curl -H "Authorization: Bearer $TOKEN" localhost:8042/api/shots
```

Auth is the boundary, not a full security posture: there is one user, no roles,
and no TLS of its own. Put it behind a reverse proxy that terminates TLS if it
is going to face the internet.

## Backup and restore

*How this works: [Configuration and data](configuration-and-data.md#backup-restore-reset).*

Everything is in one SQLite file under `DATA_DIR` (`./data` by default): shots with
their raw bytes, Recipes, beans, profiles, chats, knowledge and every setting. **Settings →
System → Backup & restore** downloads it as one file and puts one back.

**Download.** *Download backup* gives you `gaggiclanker-<UTC>.db`, a complete gaggiclanker
database with a small `backup_manifest` table that says when and by which version it was
taken. It is a consistent snapshot (SQLite's `VACUUM INTO`, on its own connection), so it
can be taken while the app is running and syncing, which a plain `cp` of a database with a
hot WAL cannot promise. By default the API keys and tokens are **not** in it (the LLM API
key, the Anthropic key, the Claude Code token and the session signing key; nothing of them
is left in the file's bytes). Tick *Include API keys and tokens* to put them in, in plain
text: keep that file somewhere safe. The sign-in user and password hash are always in the
file, so a restored app still asks for a password. Nobody's open sign-in is: sessions are
never in a backup.

For a scripted backup:

```bash
curl -o backup.db 'localhost:8042/api/backup?include_keys=false'
```

With sign-in on the request needs the session token (`-H "Authorization: Bearer <token>"`,
from `POST /api/auth/login`).

**Restore from file.** *Restore from file…* takes a file you uploaded, never one kept on the
server: a gaggiclanker download, or a plain copy of a gaggiclanker database. The server checks
it first and changes nothing in the app: it must be a SQLite file that passes its integrity
check, hold the app's tables, have this version's database structure, and be under 1 GB. A
backup made by an older version, in the state that version left it, is first upgraded on the
uploaded copy by the same steps an upgrade at start runs, and the page says it will be
upgraded; a backup from a newer version is refused (update gaggiclanker first), and so is
any other structure. The page then shows what the file holds beside what the app holds
now. Confirming:

- **replaces everything** in the app with the file;
- **does not keep what was there**: the replaced database is gone, so download a backup
  first if you want to be able to go back;
- switches **Writes to the machine off** (switch it on again when you are ready), signs
  everyone out, and for each API key or token the file does not hold, keeps the one this app
  has now;
- **restarts the app**: it stops itself after answering, and Docker's
  `restart: unless-stopped` (already in `compose.yml`) starts it again on the restored data.
  Under `uv run` or any other launcher that does not restart a stopped process, start it
  again by hand; the restore is done either way, and the next start opens the restored data.

An interruption cannot lose both: the new file is renamed over the old one in a single step,
so a crash leaves the old data (and a staging file the next start deletes) or the new.
Restoring is refused with a message while a sync, a chat answer, a review or another
background job is running. The app's own chat runs are stopped before the swap. An MCP client
started outside the app (stdio) keeps the old file open until it reconnects.

By hand, with the app stopped, a restore is still a file copy: copy a downloaded file over
`data/gaggiclanker.db` and delete `data/gaggiclanker.db-wal` and `data/gaggiclanker.db-shm`.
The sidecars belong to the file they were written next to; leaving them beside a restored
database reinstates the state you were trying to undo.

## Reset

*How this works: [Configuration and data](configuration-and-data.md#backup-restore-reset).*

**Settings → System → Reset** puts the app back to a fresh install. It deletes every shot,
Recipe, bean, profile, chat, insight, edited prompt and setting, **the API keys and tokens**, the
downloaded Claude Code program, and any leftover upload or export; **sign-in is switched off
afterwards** (anyone who can reach the app can use it until you set a password again). Nothing
on the machine changes: its profiles and shots stay on it, and after you enter its address
again the next sync pulls its shots back in as new. It cannot be undone, so download a backup
(above) first if you want to keep any of it. The button asks once more, inline, before it
does anything, and is refused with a message while a sync, a chat answer, a review or another
background job runs, or while a restore is pending.

The route first writes a `reset-requested` file into `DATA_DIR`, answers, and stops the app.
The deleting happens at the **next start, before the database is opened**, so a crash or a
kill after the answer still ends in a fresh install, and no old write-ahead log is ever
replayed onto the new database. Docker's `restart: unless-stopped` starts it again at once;
under `uv run` or any launcher that does not restart a stopped process, start it again by
hand and the reset completes then. Until then `gaggiclanker import` and `gaggiclanker mcp`
refuse to start (they would write to a database that is about to be deleted). What is left
in `DATA_DIR` is a new `gaggiclanker.db`.

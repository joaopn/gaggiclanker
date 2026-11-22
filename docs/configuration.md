---
title: Configuration
parent: User guide
nav_order: 5
---
# Configuration

Everything is configured on the Settings pages and stored in the database. The one setting you must give is the machine's host: the IP or hostname of the display board. Prefer a fixed IP. mDNS (`gaggimate.local`) does not resolve from inside a Docker bridge network, and the firmware turns mDNS off when HomeKit is on.

*How this works: [Configuration and data](configuration-and-data.md).*

There is no configuration file. A setting is what you saved on the Settings page, or the default this release ships. Nothing you change in the UI reverts on restart.

**The machine settings apply immediately.** Saving a new host, protocol, timeout or the sync switch (**Device sync enabled**) under Settings → Machine access closes the connection and opens the new one, with no restart. The header pill follows within a few seconds. While a sync is using the machine (the profile list's write phase included), the change is refused with the reason and nothing is saved. Wait for the sync to finish and save again.

## Environment variables

The process reads only these values, before it can open the database. The image already sets them and most people never touch them.

| Variable | Default | What it is |
|---|---|---|
| `DATA_DIR` | `./data` (`/app/data` in the image) | Where the SQLite file lives. |
| `HOST` | `0.0.0.0` | Bind address. |
| `PORT` | `8042` | Bind port; the healthcheck reads it too. Give it every time you start the app; see [Quick start](getting-started.md#quick-start). |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`. |
| `LOG_JSON` | `true` | JSON log lines, or human-readable console output. |
| `WEB_DIST` | `<repo>/web/dist` | Where the built web app is; the image sets it. |
| `CORS_ORIGINS` | empty | Comma-separated origins allowed to call the API. Only the Vite dev server needs one. |

Each also accepts a `GAGGICLANKER_`-prefixed spelling (`GAGGICLANKER_DATA_DIR`) for a box running several services. `compose.yml` has a commented-out `LOG_LEVEL` line if you want a different log level.

Nothing else is read from the environment, and nothing reads a `.env` file. A `.env` beside the app is logged as ignored (`dotenv_file_ignored`). Compose itself fills `${VAR}` in `compose.yml` from a `.env` in the project directory, so a stale `.env` holding a sign-in variable, a provider key or `GAGGICLANKER_DEVICE_WRITES_ENABLED` still stops the container, with the names in the log. A stale `DATA_DIR` or `PORT` in that file does nothing.

**Credentials never come from the environment.** The sign-in user and password and every LLM provider's key or token are entered on the Settings page and live in the database. A boot that finds a credential variable set, or a proxy variable with a user or password in it, logs `auth_env_refused` with the variable names and exits. See [Troubleshooting](troubleshooting.md).

**Upgrading from a release that read settings from the environment.** Before you pull, store everything you had set (in an old `.env`, in `compose.yml`'s `environment:`, or in the shell) on the old version's Settings page, because none of it is read any more. A boot that still finds one of those variables logs `setting_env_ignored` with the names and uses the database's values. Typing the same value into the old Settings page and pressing Save does not store it, because the form sends only fields that changed. Change the field to something else, **Save**, change it back, **Save** again, or store the values in one request on the old version (port 8000 was its default):

```bash
curl -X PATCH http://localhost:8000/api/settings \
  -H 'content-type: application/json' \
  -d '{"gaggimateHost": "192.168.1.50", "gaggimateTimeoutSeconds": 20}'
```

Add `-H "Authorization: Bearer <token>"` if sign-in is on. Check with `curl -s localhost:8000/api/settings`: every key you moved should read `"source": "database"`. If `git pull` complains about your own `.env`, move it aside.

## Authentication

*How this works: [Configuration and data](configuration-and-data.md#sign-in).*

Sign-in is off until you turn it on. That is right for a box on a home network. Turn it on if this box is port-forwarded, on a shared network, or behind a reverse proxy the internet can reach.

1. Open **Settings → Authentication**.
2. Set the password.
3. Set the username. Setting the username turns sign-in on.

It takes effect at once, with no restart. Changing the password or the username signs out every open session, yours included. A sign-in lasts thirty days. Signing out ends the session on the server, so a token that leaked can be killed from any other tab. Five failed sign-ins from one address lock that address for sixty seconds.

With sign-in on, every route under `/api` needs a bearer token, including the event streams. `/health` and the web app itself stay public. To call the API from a script:

```bash
TOKEN=$(curl -sX POST localhost:8042/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"barista","password":"something long"}' | jq -r .data.token)
curl -H "Authorization: Bearer $TOKEN" localhost:8042/api/shots
```

Sign-in is the boundary, not a full security posture: there is one user, no roles, and no TLS. Put the app behind a reverse proxy that terminates TLS if it will face the internet. If you lose the password, see [Troubleshooting](troubleshooting.md).

## Backup and restore

*How this works: [Configuration and data](configuration-and-data.md#backup-restore-reset).*

Everything is in one SQLite file under `DATA_DIR` (`./data` by default): shots with their raw bytes, Recipes, beans, profiles, chats, knowledge and every setting. **Settings → System → Backup & restore** downloads it as one file and puts one back.

**Download.** *Download backup* gives you `gaggiclanker-<UTC>.db`. It can be taken while the app is running and syncing. By default the API keys and tokens are **not** in it. Tick *Include API keys and tokens* to put them in, in plain text, and keep that file somewhere safe. The sign-in user and password hash are always in the file, so a restored app still asks for a password. Open sign-ins are never in a backup.

For a scripted backup:

```bash
curl -o backup.db 'localhost:8042/api/backup?include_keys=false'
```

With sign-in on, add `-H "Authorization: Bearer <token>"` (a token from `POST /api/auth/login`).

**Restore from file.** *Restore from file…* takes a file you upload: a gaggiclanker download, or a plain copy of a gaggiclanker database. It must be a SQLite file under 1 GB. A backup from an older version is upgraded on the uploaded copy, and the page says so. A backup from a newer version is refused: update gaggiclanker first. The page then shows what the file holds beside what the app holds now. Confirming:

- **replaces everything** in the app with the file. The replaced database is gone, so download a backup first if you want to be able to go back.
- switches **Writes to the machine off**. Switch it on again when you are ready.
- signs everyone out. For each API key or token the file does not hold, the app keeps the one it has now.
- **restarts the app**. Docker's `restart: unless-stopped` (already in `compose.yml`) starts it again on the restored data. Under `uv run` or any other launcher that does not restart a stopped process, start it again by hand.

A crash during a restore leaves either the old data or the new, never neither. Restoring is refused with a message while a sync, a chat answer, a review or another background job is running.

By hand, with the app stopped, copy a downloaded file over `data/gaggiclanker.db` and delete `data/gaggiclanker.db-wal` and `data/gaggiclanker.db-shm`. Leaving them beside a restored database brings back the state you were trying to undo.

## Reset

*How this works: [Configuration and data](configuration-and-data.md#backup-restore-reset).*

**Settings → System → Reset** puts the app back to a fresh install. It deletes every shot, Recipe, bean, profile, chat, insight, edited prompt and setting, **the API keys and tokens**, the downloaded Claude Code program, and any leftover upload or export. **Sign-in is switched off afterwards**: anyone who can reach the app can use it until you set a password again. Nothing on the machine changes. Its profiles and shots stay on it, and after you enter its address again the next sync pulls its shots back in as new.

Reset cannot be undone, so download a backup first if you want to keep any of it. The button asks once more before it does anything. It is refused with a message while a sync, a chat answer, a review or another background job runs, or while a restore is pending.

The app stops and the deleting happens at its next start. Docker restarts it at once. Under `uv run` or any launcher that does not restart a stopped process, start it again by hand and the reset completes then. Until then `gaggiclanker import` and `gaggiclanker mcp` refuse to start.

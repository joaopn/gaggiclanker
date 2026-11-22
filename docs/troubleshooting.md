---
title: Troubleshooting
---
# Troubleshooting

**The device pill never goes green.**
Check the host under Settings → Machine access first: `curl http://<host>/api/status`
should answer a small JSON document. Then check you are not out of WebSocket
slots — see below.
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
sync records the failure and nothing is half-written. Wait for the update to
finish and sync again.

**Shots appear with no pressure and no flow.**
Those are zero on **Standard** boards — the sensor is a Pro part. Every
pressure-derived diagnostic is gated on the board's capability flag, so the
curves are honest rather than flat lines pretending to be data. Weight and the
**cup flow** (the flow the scale sees, which is what reaches the cup) need a BLE
scale paired with the machine; without one there is no `final_weight_g`, and the
first drip is the **puck flow's estimate** (labelled so). Puck flow is an estimate
from the pump model: it runs at about the pump flow, well above the cup flow, and
does not track when coffee reaches the cup (on most shots it starts seconds later,
on a long pre-infusion much earlier). A shot counts as having a scale when it logged
a weight above zero during the brew, whatever the connection flag says. On a board with a scale
the shot page opens on pressure, target pressure, cup flow, weight and temperature.

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

**A review spins for ever / says `interrupted`.**
`interrupted` means the process stopped mid-call — a restart, an OOM, a power
cut — and the next boot said so rather than leaving a spinner. Press the button
again. If it fails instead, the row carries the provider's error; a rate limit
latches the whole app on purpose and Settings → LLM has the button that
clears it.

**I am locked out after mistyping the password.**
Five failures from one address lock that address for sixty seconds, and each
further attempt extends it. Wait a minute. If you have genuinely lost the
password, delete the stored hash, which turns auth off on the very next request
(no restart):

```bash
docker compose exec gaggiclanker python -c \
  "import sqlite3;sqlite3.connect('/app/data/gaggiclanker.db').execute(\
   \"DELETE FROM settings WHERE key='authPasswordHash'\").connection.commit()"
```

then set a new password under Settings → Authentication. The username is still
set, so auth is back on the moment the new password is stored.

**Sign-in says "the stored password hash is not an argon2 hash".**
Something wrote a password, not a hash, into the `authPasswordHash` row by hand.
Auth is on and refusing everybody, which is the correct thing for a broken
credential to do. Delete the row as for a lost password above, then set the
password you want under Settings → Authentication.

**The container exits at boot with `device_writes_env_refused`.**
`GAGGICLANKER_DEVICE_WRITES_ENABLED` is still set — in `compose.yml`, in a
leftover `.env` compose passes through, or in the shell that started it. It no
longer allows anything: writes to the machine are switched on with the **Writes**
switch in the top bar and the switch lives in the database. Remove the variable, start the
app, and use the switch if you want writes on. The boot refuses rather
than ignoring it because a switch that used to open the only path to your
espresso machine must not change meaning quietly.

**The container exits at boot with `auth_env_refused`.**
A credential variable is still set — one of the old sign-in variables, or an LLM
provider's API key or token — in `compose.yml` or in the shell that started it. The log line names which. Remove them, start the app, and enter
sign-in under Settings → Authentication and the key under Settings → LLM. An
install that had
sign-in configured only through the environment starts with auth off until you
set it again, so do that first if the box is reachable from outside.

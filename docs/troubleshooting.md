---
title: Troubleshooting
parent: User guide
nav_order: 6
---
# Troubleshooting

**The device pill never goes green.**
Check the host under Settings → Machine access. `curl http://<host>/api/status` should answer a small JSON document. Then check that the machine's connections are not all in use (see below). The container log has a `device_connection_failed` line with the actual error on every attempt.

**`gaggimate.local` works in my browser but not here.**
mDNS does not resolve from inside a Docker *bridge* network, which is why `compose.yml` defaults to host networking. The firmware also turns mDNS off when HomeKit is on, so the name can be dead everywhere. Use the IP, and give the machine a DHCP reservation so it stays the same.

**The machine's own web UI stops working when gaggiclanker is running.**
The machine allows three connections at once and drops the oldest when a fourth arrives. gaggiclanker holds one and the machine's own web page is another, so a second browser tab on the machine is the third. Close spare tabs. If you want the machine left alone entirely, turn off **Device sync enabled** under Settings → Machine access and use the archive you already have.

**A sync fails with 503 from `/api/history`.**
The machine is installing a firmware update and refuses history requests until it is done. Nothing is lost and nothing is half-written. Wait for the update to finish and sync again.

**Shots appear with no pressure and no flow.**
These are zero on **Standard** boards: the pressure sensor is a Pro part. The curves show that rather than drawing made-up lines. Weight and the **cup flow** (the flow the scale sees, which is what reaches the cup) need a Bluetooth scale paired with the machine. Without one there is no final weight, and the first drip comes from the **puck flow estimate**, labelled as such. That estimate is not a measurement and does not tell you when coffee reached the cup. On a board with a scale the shot page opens on pressure, target pressure, cup flow, weight and temperature.

**A shot is listed as quarantined.**
Its `.slog` file did not parse. The raw bytes are stored anyway, so the shot can be recovered once the parser learns its format. `GET /api/shots/{id}/raw` downloads exactly what the machine sent. Please open an issue with that file attached.

**A short shot never appeared at all.**
The firmware discards anything under 7.5 seconds and never records the utility profiles (backflush, flush). Nothing here can see a shot the machine did not save.

**"unable to open database file" on the first `docker compose up`.**
Docker created `./data` as `root:root` and the app runs as uid 1000. The image normally fixes this itself. If you have overridden `user:` in compose, either run `chown $(id -u):$(id -g) ./data` or set `APP_UID` and `APP_GID` to your own.

**A review spins for ever, or says `interrupted`.**
`interrupted` means the app stopped mid-call (a restart, running out of memory, a power cut). Press the button again. If it fails instead, the row shows the provider's error. After a rate limit the app stops calling the provider on purpose, and Settings → LLM has the button that clears it.

**I am locked out after mistyping the password.**
Five failures from one address lock that address for sixty seconds, and each further attempt extends it. Wait a minute. If you have lost the password, delete the stored hash. That turns sign-in off on the very next request, with no restart:

```bash
docker compose exec gaggiclanker python -c \
  "import sqlite3;sqlite3.connect('/app/data/gaggiclanker.db').execute(\
   \"DELETE FROM settings WHERE key='authPasswordHash'\").connection.commit()"
```

Then set a new password under Settings → Authentication. The username is still set, so sign-in is back on the moment the new password is stored.

**Sign-in says "the stored password hash is not an argon2 hash".**
A plain password was written into the `authPasswordHash` row by hand. Sign-in stays on and refuses everybody. Delete the row as for a lost password above, then set the password you want under Settings → Authentication.

**The container exits at boot with `device_writes_env_refused`.**
`GAGGICLANKER_DEVICE_WRITES_ENABLED` is still set, in `compose.yml`, in a leftover `.env`, or in the shell that started it. It no longer does anything. Writes to the machine are switched on with the **Writes** switch in the top bar. Remove the variable and start the app again. Use the switch if you want writes on.

**The container exits at boot with `auth_env_refused`.**
A credential variable is still set: an old sign-in variable, or an LLM provider's key or token. It is in `compose.yml` or in the shell that started it, and the log names which. Remove it, start the app, then enter sign-in under Settings → Authentication and the key under Settings → LLM. An install that had sign-in set only through the environment starts with sign-in off until you set it again, so do that first if the box can be reached from outside.

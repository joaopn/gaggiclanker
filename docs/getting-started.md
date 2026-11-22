---
title: Getting started
parent: User guide
nav_order: 1
---
# Getting started

A self-hosted archive and analyst for a [GaggiMate](https://gaggimate.eu)
espresso machine. Press a button and it brings in every shot the machine holds —
raw `.slog` bytes, every sample, phases, the device's own notes and profiles —
shows them with curves and deterministic diagnostics, lets you judge each shot
from the list and group shots into versioned **Recipes** (bean + hardware + profile
+ grind/dose/yield), reads a shot with a language model when you ask it to (you confirm what it
says), and
talks the Recipes through with a tool-using chat, through whichever provider you
point it at.

*How this works: [Architecture](architecture.md).*

It does not watch the machine. Nothing leaves the GaggiMate until you ask for
it: the machine's own web UI already draws the shot that is happening now, and
what an archive is for is the hundred before it.

The machine holds a few hundred KB of flash and deletes old shots when it runs
low. This is the thing that remembers them.

It works with GaggiMate firmware 1.10 (the simulator suite is pinned to that release): shot files
in its new format, profiles with a dose and the ratio stop, the machine's own backflush and
descale profiles and its limit of ten starred profiles. Shots and profiles from older firmware read as they did.

> **Status: 0.1.0, the prototype, plus the profile list.** Everything in this guide
> works: sync, the shots UI, Recipes and judgement, the LLM layer, a shot's review,
> the chat, optional authentication and the container. It can now
> also hold a list of your profiles and make the machine match it — you switch a profile on or
> off the machine and make one of its versions active, and the next sync puts the active
> version on the machine and removes the profiles that are off, behind a switch that is off by
> default and through the four layers in [`safety-layers.md`](safety-layers.md).
> `CHANGELOG.md` has what landed in each release.

## Quick start

```bash
git clone <this repo> && cd gaggiclanker
docker compose up -d --build         # builds the image and starts on :8042
curl localhost:8042/health           # {"ok":true,"data":{"status":"ok",...}}
open http://localhost:8042           # the UI
```

Nothing to copy, rename or edit first. Everything is configured from the UI and
kept in the database, so a checkout is ready to start as it comes and a later
`git pull` has no file of yours to conflict with.

**The port is the one thing still given on the command line.** It defaults to
8042 and cannot be a setting — the process has to bind a socket before it can
open the database and read one — so it is passed at spawn and remembered
nowhere. Give it again every time you start the app, from your shell or from a
`.env` in this directory that Compose substitutes from:

```bash
PORT=9000 docker compose up -d --build   # host networking, the default here
HOST_PORT=9000 docker compose up -d      # bridge: published on 9000, container stays on 8042
PORT=9000 uv run gaggiclanker            # a source checkout, no Docker
```

`compose.yml` uses **host networking** by default, so the app is on port 8042 of
the box you started it on and mDNS names resolve. Docker Desktop does not
support that; swap in the `ports:` block the file documents next to it and use
an IP for the machine's host.

## First run

1. **Open the UI** at `http://localhost:8042`.
2. **Set the machine's address in Settings → Machine access.** `gaggimateHost` is the
   display board's IP or hostname, with no scheme (`192.168.1.50`, or
   `192.168.1.50:80`). It connects on save, with no restart: the pill in the
   header goes green within a few seconds of the WebSocket connecting. Prefer a
   fixed IP or a DHCP reservation — see [Troubleshooting](troubleshooting.md) for why the name your
   browser resolves may not resolve here.
3. **Press Sync** in the top bar, on any page. The first sync walks the
   machine's whole history, which for a few hundred shots takes a minute or
   two; when it finishes it says what it archived, how many profiles it read from the
   machine and how many it wrote to it ("Read 9 profiles from the machine; no writes
   (writes are off)"). Shots under 7.5 seconds
   never appear: the firmware discards them. After that, sync whenever you have
   pulled some coffee — or drop exported files on the strip under the header,
   which is the only way back for shots the machine has already deleted.
4. **Turn on authentication** if this box is reachable by anything you do not
   trust — see [Authentication](configuration.md#authentication). Off by default, and that is the right default for a
   machine only your own LAN can reach.
5. **Set up the model.** A review and the chat need one provider. The cheapest to get
   working is `claude_code`, which spends a Claude subscription you may already
   have: run `claude setup-token` on any machine with the CLI installed and
   paste the token into **Settings → LLM**.
   An interactive `claude login` is **not** enough — every call runs with a
   scratch `HOME`, which is what keeps this repository out of the prompt and
   hides `~/.claude` along with it. Any OpenAI-compatible gateway works too;
   see [LLM settings](review-and-chat.md#llm-settings).
6. **Take a backup** once there is something worth keeping: **Settings →
   System → Backup & restore → Download backup**, or
   `curl -o backup.db 'localhost:8042/api/backup?include_keys=false'`.

Your data lives in `./data` — one SQLite file. Back it up from the Settings page
(a consistent snapshot, taken while the app is running), or copy that directory
while the app is stopped.

You do not need to create `./data` first. Docker creates a missing bind-mount
source as `root:root`, so the container's entrypoint starts as root, hands that
one directory to the unprivileged app user, and drops to it before any
application code runs. If you would rather not have the entrypoint touch
ownership at all, `compose.yml` documents a named-volume alternative next to the
mount, including how to find and back up the file inside it.

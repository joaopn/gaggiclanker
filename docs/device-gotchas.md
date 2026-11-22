---
title: Firmware behaviours
parent: Architecture
nav_order: 3
---
# Firmware behaviours

Fifteen firmware behaviours that shaped the sync engine. Every one of them was
verified against the GaggiMate firmware source
rather than inferred from the machine's behaviour, and most of them cost
somebody an evening before they were written down.

Read this before touching `gaggiclanker/device/` or `gaggiclanker/sync/`.

---

**1. No auth, no TLS, no CORS on the device.**
Anything on the LAN can read the machine's history and write its settings.
gaggiclanker is the security boundary; the machine is not one.

**2. Three WebSocket clients, and the eviction is of the oldest.**
`cleanupClients()` runs once a second and drops the *oldest* client when a
fourth connects — it does not refuse the newcomer. The machine's own browser UI
is one of the three. So:

* hold exactly one persistent connection, with reconnect and backoff;
* fall back to polling the shot index, never to a second socket;
* **never reset a reconnect backoff until the connection has actually lasted.**
  A successful handshake is not proof of a slot: on a full device, resetting on
  connect turns "we were evicted" into an endless connect/evict loop that
  knocks a real client off every second.

**3. `evt:history-shot-saved {id}` carries an *unpadded* int.**
Ids are six-digit zero-padded in URLs and in the notes files the display writes,
and unpadded on the event. One helper converts, and it is tested:
`gaggiclanker/domain/ids.py`.
Nothing here acts on the event any more — a sync is a request — but the id is
remembered, because the firmware writes the index entry and the event in quick
succession and not atomically, and somebody pressing sync the moment the
machine beeps is inside that window.

**3a. Notes saved from the machine's web page may sit under the unpadded name.**
Up to v1.9 the page sends the id as the index shows it, so its notes are
`/h/31.json`, not `/h/000031.json`, and the index still flags the shot as having
notes (it does so only when the card was saved with a rating, or after the
machine rebuilds its index). `/api/history/` is a static route, so the file name is the URL:
`GET /api/history/000031.json` finds nothing and `GET /api/history/31.json`
finds the notes (firmware 1.10 serves that file too, and moves it onto the
padded name only on a socket notes read or save, or an index rebuild). The sync
asks for the unpadded name when the padded one is missing for a shot the index
flags, over HTTP like the rest of the notes pass, so it needs no WebSocket slot.
The document may carry the id the page wrote (`"31"`), and that is the id it
keeps.

**4. A `.slog` may be header-only while it is still being written.**
Retry when `byteLength <= headerSize` rather than treating it as a corrupt
file. The machine writes the header first and the samples after.

**5. Shots of 7.5 seconds or less never appear.**
The firmware discards them, and it never records the utility profiles
(backflush, flush) at all. A shot the machine did not save cannot be archived,
by anybody.

**6. Everything under `/api/history` returns 503 during an OTA update.**
Back off; it is not a failure. The firmware also serves its own SPA's HTML for
unknown paths, so **a 200 whose content type is not what you asked for is an
error** — treating it as data is how a sync run "succeeds" with an HTML page
parsed as a shot. This includes a file that is not there: `/api/history/` is a
static route that declines a missing file, so the request falls through to the
catch-all and the machine answers the web page with **200, not 404**. For a
notes file that means "no such file" (the sync stores nothing and asks again on
the next pass); under memory pressure it can also mean a file that exists.

**7. Pressure and flow are zero on Standard boards.**
The sensor is a Pro part. Gate every pressure-derived diagnostic on the `cp`
capability flag, or the archive fills with confident nonsense about a machine
that cannot measure the thing being diagnosed.

**8. Know which signals were real.**
Pressure is measured at the pump, not at the puck. Flow is *modelled* from the
pump, not measured. Weight needs a BLE scale. Each shot records which signals
were genuine (the `si` bits and the capability flags), because a diagnostic on a
modelled signal means less than one on a measured signal and the UI has to be
able to say so.

Puck flow is an estimate from the pump model: it runs at about the pump flow, well above the cup flow, and does not track when coffee reaches the cup (on most shots it starts seconds later, on a long pre-infusion much earlier). The cup flow is the flow the scale sees, which is what reaches the cup, so it needs a BLE scale paired with the machine; without one there is no final weight and the first drip shown is the puck flow's estimate, labelled so. Every pressure-derived diagnostic is gated on the board's capability flag, so the curves of a Standard board are honest rather than flat lines pretending to be data. On a board with a scale the shot page opens on pressure, target pressure, cup flow, weight and temperature.

**9. The machine deletes old shots when free space drops below 500 KB.**
It is a buffer, not an archive. Sync often enough that a few hundred shots
never accumulate on it, keep the raw bytes, and never lose a shot to a parse
failure — by the time a parser bug is fixed, the machine's copy is gone. That
single fact is why quarantine exists, and why a sync fetches the *oldest*
missing shots first.

`cleanupHistory()` in `ShotHistoryPlugin.cpp` is the firmware deleting its oldest
`.slog` (in filename order, with the notes file) on its own when storage runs
low, and it does not care whether anything has archived the shot. gaggiclanker
does not delete shots from the machine and does not try to get ahead of it: this
is accepted, and the defence is to sync before it does. A quarantined shot is
kept in the archive as raw bytes, so a parser fix can still re-derive it, but
once the machine has rotated the shot out there is no other copy to fetch.

Deleting leaves the index row behind with the DELETED flag set and removes the
files, which is why the reconcile treats the flag rather than the absence as the
signal — and why `gaggiclanker/device/fake.py` reproduces exactly that.

**10. `POST /api/settings` changes WiFi and PID, and up to v1.8.x it cleared any boolean key you omit.**
It is the one endpoint that can change WiFi and PID. Up to firmware v1.8.x a
partial write also turned off HomeKit, boiler fill and the momentary buttons;
v1.9.0 made it a partial update that leaves an omitted boolean alone, so the
clearing is not something to rely on either way. gaggiclanker never writes
device settings — the only writes it *can* make are the five `req:profiles:*`
frames, and every one of them is off by default (`docs/safety-layers.md`). It
sends neither `req:history:delete` nor `req:history:notes:save`.

**11. Profile JSON has undocumented fields, and `pump` must be an integer.**
The firmware includes `transition.target`, which a strict validator has to
tolerate. Send integer `pump` percentages: a float like `100.0` is parsed as an
*object* with zero targets, leaving a profile that never runs the pump.

Three more that only matter once you write one. `req:profiles:save`
**upserts on the filename** `/p/<id>.json`, so a save carrying somebody else's
id silently replaces their profile — send no id and let `generateShortID()`
assign one. A **new profile is auto-favourited** (`ProfileManager.cpp:186-188`),
so a push puts an unreviewed draft on the machine's home screen unless you
unstar it. And `writeProfile` **never echoes what you sent**: it parses into a
struct and serialises the struct, adding `id`, `favorite`, `selected`,
`transition.target` and a spelled-out phase `temperature` of `0`, and dropping
`targets` when the list is empty. A round-trip comparison has to be done in a
canonical form that tolerates exactly that set — `canonical_document_json` is it.

**12. Trust the header's `sampleInterval` and each sample's own `t`.**
Not the nominal 250 ms, and not the 100 ms that gaggimate-mcp's research
document assumes. The samples carry real `millis()` values; a chart that
assumed a fixed interval draws a shot with a gap in it as a shot that ran short.

**13. `GET /api/settings` returns the machine's passwords in plain text.**
`wifiPassword` (only outside access-point mode), `apPassword` and `haPassword`
(empty when Home Assistant is unused) sit next to the settings worth keeping
(`WebUIPlugin.cpp:486-495`). The archive removes them, and any key named for a
password, token or secret, before storing or showing the document
(`domain/secrets.py`); the fake serves all three so the suite exercises it.

**14. The shot number is not an identity: the counter lives in NVS and restarts after an erase.**
The next shot's number is the `hi` setting (`Settings.h`, default 0), stored in
NVS beside the machine's other settings, and it is only ever raised again by a
history rebuild. A reflash, a factory reset or a replacement board therefore
numbers its shots from 0 again while the clock keeps going, and its new shots
carry numbers an archive already holds for different shots. What does tell them
apart is the start epoch: every index row's `timestamp` and its `.slog` header's
`startEpoch` are written from the same `header.startEpoch`
(`ShotHistoryPlugin.cpp`, both in the recorder and in the rebuild from files).
So the archive identifies a shot by number **and** start epoch (`UNIQUE
(device_id, start_epoch)`), pairs an index row with an archived shot only when
both agree, and treats an archived shot whose number the machine has given to a
later one as gone from the machine. The number stays what is displayed and what
`/h/<id>.slog` and `/h/<id>.json` are addressed by. The pair is unique only
for a machine whose clock is set, and the firmware sets it from NTP, which needs
internet: on a machine without one `startEpoch` is seconds since boot, two
shots can share both number and epoch, and the later one is then taken for the
earlier one.

**15. Firmware 1.10 changed the shot file, the profile and what the machine will let you do to its list.**
All read from the v1.10.0 source (`shot_log_format.h`, `profile.h`, `ProfileManager.cpp`,
`WebSocketHandler.cpp`, `system_profiles.h`); the simulator is pinned to that release.

* **Shot files are version 8**, byte-for-byte the v7 layout with more `si` bits: `0x0020` the
  process is a brew, `0x0040` the target is volumetric, `0x0080` the phase has a volumetric
  target, `0x0100` the active scale (hardware or Bluetooth) is connected and healthy. The
  codec reads and writes v8 exactly as v7. The firmware also reads an old **hardware-scale
  version 6** (`reserved0 == 26` and `fieldsMask == 0x1FFF`: 26-byte samples whose `t` is the
  2-byte tick count of v1 to v5, not milliseconds); this codec reads it by the same rule
  (`shotLogHasElapsedTimestamp`) and never writes it. Exit reason 9 is a **ratio target**.
* **A version 8 weight is a scale's only where `si` bit `0x0100` says so.** `v` and `vf` hold the
  *active* weight: with no physical scale that is the flow estimator's value (`vf` the puck flow),
  and a scale that stops reporting mid-shot hands over to the estimator, which carries on from the
  last reading plus an offset (`Controller::onVolumetricMeasurement`,
  `ShotHistoryPlugin::selectWeightFlow`). The header's `finalWeight` is the highest spike-filtered
  active weight, so it is the estimate too when no scale answered. The machine's own analyzer reads
  `0x0100` the same way. This app reads a v8 sample's weight and weight flow as **absent** (never
  zero: a zero is a reading) unless it has the bit, counts the header weight only when some *brew* sample
  had it (the extended recording after the stop, `si` bit `0x0010`, runs with the pump off: a scale
  that comes up only there did not weigh the brew, and the shot has no weights at all), and reads
  `scale_connected` from it, from the stored rows, instead of `0x0004`. The estimated weight `ev` is its
  own column and stays. Version 7 and older are read as they always were. A scale lost before the
  end of the brew (the last brew sample that holds a weight lacks the bit while an earlier one has
  it; the extended recording is not judged) leaves the header at the peak including the estimate, so that shot has no final weight
  either, while its earlier weights stay and it still counts as having a scale. A scale that
  comes up part-way through the brew with coffee already in the cup (its first reading is half a
  gram or more) leaves no baseline: the cup's first drip is absent, and a phase's cup gain is
  absent when no weight was recorded at its start (never counted from zero). A first reading under
  half a gram is a baseline whenever the scale first answered. The index entry
  copies the header's `finalWeight` into its `volume` and a dose out typed on the notes card
  replaces it (`updateIndexMetadata`), so where the header is the estimate the stored index volume
  is kept only when it differs from the header's weight in the firmware's tenths of a gram (and is
  absent when the header weight cannot be read at all).
* **Profiles carry a `dose`**, written on every profile (`0` is none; the schema says 1-100),
  and a **`ratio` target type**: it fires on `volume / dose`, is skipped while the dose is 0
  (`Phase::isFinished`), and counts as volumetric everywhere else (`hasVolumetricTarget`,
  `getTotalVolume` is ratio times dose). A file the machine rewrote with `dose: 0` is the same
  document as the stored one without it, so `canonical_document_json` drops a dose of 0 (the
  same sentinel rule as a phase `temperature` of 0) and no recorded hash changed.
* **Two system profiles, `flush` and `descale`,** are seeded on every boot when missing,
  forced to `utility`, editable, and refused by `req:profiles:delete` (`Delete failed`). They
  come back under the same ids after a wipe, so they say nothing about whether the machine was
  reset. The firmware's own descale (a 0 degree setpoint, 200 s waits) is outside this app's
  safety policy, so a change to it is reported as not pushed.
* **`req:profiles:delete` also refuses the selected profile,** `req:profiles:select` of an id it
  cannot load answers `Profile not found`, and `req:profiles:favorite` past **ten** answers
  `Favorite limit reached` (a new profile saved at the limit is saved and not starred). The
  minimal list now carries `utility`; the app asks for full documents.

---

These are enforced, not merely documented. `gaggiclanker/device/fake.py` is a
real HTTP + WebSocket server that reproduces the awkward ones — half-written
`.slog` files, the SPA served where binary was asked for, 503 during an OTA, the
three-client limit, the 1.10 refusals above — and the whole offline suite runs against it.
`tests/device/test_simulator.py`, `tests/simulator/test_e2e.py` and
`tests/simulator/test_profile_push.py` then check the same client against the
actual firmware compiled natively, which is what caught `OtaSettings` rejecting
every real identity frame.

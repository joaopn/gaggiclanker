# Device gotchas

Twelve firmware behaviours that shaped the sync engine. Every one of them was
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
Ids are six-digit zero-padded in URLs and in the notes files, and unpadded on
the event. One helper converts, and it is tested: `gaggiclanker/domain/ids.py`.

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
parsed as a shot.

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

**9. The machine deletes old shots when free space drops below 500 KB.**
It is a buffer, not an archive. Sync promptly, keep the raw bytes, and never
lose a shot to a parse failure — by the time a parser bug is fixed, the
machine's copy is gone. That single fact is why quarantine exists.

**10. `POST /api/settings` on the device clears any boolean key you omit.**
It is the one endpoint that can change WiFi and PID, and a partial write turns
off HomeKit, boiler fill and the momentary buttons. gaggiclanker never writes
device settings — the five writes it *can* make are all `req:profiles:*`, and
they are off by default (`docs/safety-layers.md`).

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
canonical form that tolerates exactly that set — `canonical_profile_json` is it.

**12. Trust the header's `sampleInterval` and each sample's own `t`.**
Not the nominal 250 ms, and not the 100 ms that gaggimate-mcp's research
document assumes. The samples carry real `millis()` values; a chart that
assumed a fixed interval draws a shot with a gap in it as a shot that ran short.

---

These are enforced, not merely documented. `gaggiclanker/device/fake.py` is a
real HTTP + WebSocket server that reproduces the awkward ones — half-written
`.slog` files, the SPA served where binary was asked for, 503 during an OTA, the
three-client limit — and the whole offline suite runs against it.
`tests/device/test_simulator.py`, `tests/simulator/test_e2e.py` and
`tests/simulator/test_profile_push.py` then check the same client against the
actual firmware compiled natively, which is what caught `OtaSettings` rejecting
every real identity frame.

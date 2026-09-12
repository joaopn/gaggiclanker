"""`evt:status` merge semantics, against frames shaped like the firmware's.

The rule is three words long and every one of them matters: **absent keeps,
null clears**. The frames below carry the exact key sets from
the GaggiMate firmware's `docs/websocket-api.yaml` (telemetry, every 500 ms, and
state, on change and on connect), because the bug this guards against
is not "the merge is wrong in general" but "the merge is wrong for the two
specific frames the device sends".
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.device.events import merge_status

# §5.1 — the fast frame. Every key here changes twice a second.
TELEMETRY_FRAME: dict[str, Any] = {
    "tp": "evt:status",
    "ct": 92.4,
    "tt": 93.0,
    "pr": 8.7,
    "fl": 2.1,
    "pt": 9.0,
    "wl": 62,
    "tof": 118,
    "rssi": -58,
    "lat": 12,
    "pw": 74.0,
    "hp": 21.5,
    "bw": 18.2,
    "cw": 18.2,
    "process": {
        "a": 1,
        "s": "brew",
        "l": "Ramp",
        "e": 12_500,
        "tt": "time",
        "pt": 25_000,
        "pp": 12_500,
    },
    "pkr": 4.4,
    "pf": 1.9,
    "tf": 2.0,
}

# §5.2 — the slow frame. Sent on change, every 10 s, and in full on connect.
STATE_FRAME: dict[str, Any] = {
    "tp": "evt:status",
    "m": 1,
    "p": "Medium 18g",
    "puid": "dCs4AOOcBn",
    "cp": True,
    "cd": True,
    "gp": False,
    "led": True,
    "tw": 36.0,
    "bta": 1,
    "bt": 1,
    "btd": 28.0,
    "gtd": 12_000,
    "gtv": 18.0,
    "gt": 0,
    "gact": 0,
    "up": False,
    "sys": {"s": "ready", "m": "", "c": 0},
    "bc": True,
    "sbat": 74,
    "warn": [
        {"k": "water", "l": 1, "a": False},
        {"k": "flush", "l": 1, "a": True},
    ],
}


def test_a_state_frame_then_a_telemetry_frame_gives_the_whole_picture() -> None:
    """Neither frame alone is a status; the merge is where a status exists."""
    state = merge_status(None, STATE_FRAME)
    merged = merge_status(state, TELEMETRY_FRAME)

    # From the telemetry frame.
    assert merged.ct == 92.4
    assert merged.process is not None
    assert merged.process.label == "Ramp"
    # From the state frame, untouched by the telemetry frame that did not mention it.
    assert merged.p == "Medium 18g"
    assert merged.puid == "dCs4AOOcBn"
    assert merged.cp is True
    assert merged.sys is not None
    assert merged.sys.s == "ready"


def test_an_absent_key_keeps_its_previous_value() -> None:
    """The half of the rule that makes a partial frame usable at all."""
    first = merge_status(None, {"tp": "evt:status", "ct": 92.0, "m": 1})
    second = merge_status(first, {"tp": "evt:status", "ct": 93.5})
    assert second.ct == 93.5
    assert second.m == 1


def test_an_explicit_null_clears_the_value() -> None:
    """`"process": null` is how the machine says the finished shot was cleared.

    A merge that skipped nulls — the obvious implementation — would leave the
    last shot on screen for ever, which is exactly the bug this test exists for.
    """
    brewing = merge_status(None, TELEMETRY_FRAME)
    assert brewing.process is not None

    cleared = merge_status(brewing, {"tp": "evt:status", "process": None})
    assert cleared.process is None
    # And nothing else moved.
    assert cleared.ct == 92.4


def test_the_envelope_key_never_becomes_a_status_field() -> None:
    merged = merge_status(None, TELEMETRY_FRAME)
    assert "tp" not in merged.model_dump()


def test_a_frame_with_an_unknown_key_is_kept_rather_than_dropped() -> None:
    """LiveStatus is open: a firmware that grows a key must not go dark.

    At 2 Hz, rejecting the frame would mean the live view stops on the first
    release that adds a telemetry field we have not read about yet.
    """
    merged = merge_status(None, {"tp": "evt:status", "ct": 92.0, "brandNewKey": 7})
    assert merged.model_dump()["brandNewKey"] == 7


def test_the_process_object_is_replaced_wholesale_not_field_by_field() -> None:
    """The device sends the whole `process` object every time it sends one.

    Merging its fields individually would leave `pp` from the previous phase
    sitting inside the current one.
    """
    first = merge_status(None, TELEMETRY_FRAME)
    second = merge_status(first, {"tp": "evt:status", "process": {"a": 0, "l": "Finished"}})
    assert second.process is not None
    assert second.process.label == "Finished"
    assert second.process.pp is None


def test_warnings_survive_a_round_trip_with_their_level_alias() -> None:
    """`l` is the wire name and `level` is ours; a merge must not lose either."""
    merged = merge_status(None, STATE_FRAME)
    assert merged.warn is not None
    flush = next(w for w in merged.warn if w.k == "flush")
    assert flush.level == 1
    assert flush.a is True
    # Merging again feeds our own dump back in, which is where an alias
    # mismatch would show up as a warning silently losing its level.
    again = merge_status(merged, {"tp": "evt:status", "ct": 90.0})
    assert again.warn is not None
    assert next(w for w in again.warn if w.k == "flush").level == 1


def test_pressure_capability_gates_the_pressure_reading() -> None:
    """A Standard board reports a hard zero for `pr`; `cp` is what says so."""
    standard = merge_status(None, {**STATE_FRAME, "cp": False})
    standard = merge_status(standard, {**TELEMETRY_FRAME, "pr": 0.0})
    assert standard.has_pressure is False

    pro = merge_status(None, STATE_FRAME)
    pro = merge_status(pro, TELEMETRY_FRAME)
    assert pro.has_pressure is True

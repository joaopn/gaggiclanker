"""Device-document models: profiles, notes, status, OTA.

Half of these tests are rejections. That is the point of the layer — anything
we accept and hand back to the machine has to be something the machine's own
parser reads the way we meant it, and the firmware fails *silently* at exactly
the places these tests poke.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from gaggiclanker.domain.models import (
    LiveStatus,
    OtaSettings,
    Phase,
    Profile,
    Pump,
    ShotNotes,
    canonical_profile_json,
    profile_content_hash,
)
from tests.domain.helpers import EXPORT_FIXTURES, PROFILE_FIXTURES, load_export

ALL_PROFILE_FIXTURES = sorted(p.name for p in PROFILE_FIXTURES.glob("*.json"))


def _profile(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "label": "Test",
        "type": "pro",
        "description": "",
        "temperature": 93,
        "phases": [
            {
                "name": "Pump",
                "phase": "brew",
                "valve": 1,
                "duration": 28,
                "temperature": 0,
                "transition": {"type": "instant", "duration": 0, "adaptive": True},
                "pump": {"target": "pressure", "pressure": 9, "flow": 0},
                "targets": [{"type": "volumetric", "operator": "gte", "value": 36}],
            }
        ],
    }
    base.update(over)
    return base


# ── profiles that must validate ──────────────────────────────────────


@pytest.mark.parametrize("name", ALL_PROFILE_FIXTURES)
def test_every_shipped_profile_validates(name: str) -> None:
    """The firmware's own samples and the docs-site downloads, unedited."""
    raw = json.loads((PROFILE_FIXTURES / name).read_text())
    profile = Profile.model_validate(raw)
    assert profile.phases


def test_the_maintainers_own_profile_validates() -> None:
    raw = json.loads((EXPORT_FIXTURES / "profile-dCs4AOOcBn.json").read_text())
    profile = Profile.model_validate(raw)
    assert profile.label == "Cremina v2"
    assert profile.type == "pro"
    assert len(profile.phases) == 5
    assert profile.phases[3].transition is not None
    assert profile.phases[3].transition.type == "ease-in-out"


def test_firmware_write_profile_output_validates() -> None:
    """`writeProfile` emits `id` and an undocumented `transition.target`.

    The schema forbids both — it has `additionalProperties: false` and never
    mentions `target` — so a validator built from the schema alone rejects the
    device's own output. The parser wins; we accept it.
    """
    body = _profile(id="aB3xYz90Pq", favorite=True, selected=False, utility=False)
    body["phases"][0]["transition"] = {
        "type": "linear",
        "target": "time",
        "duration": 2,
        "adaptive": False,
    }
    profile = Profile.model_validate(body)
    assert profile.id == "aB3xYz90Pq"
    assert profile.phases[0].transition is not None
    assert profile.phases[0].transition.target == "time"


def test_underscore_annotation_keys_are_kept_not_rejected() -> None:
    """`^_` keys are the schema's reserved user-annotation space."""
    profile = Profile.model_validate(_profile(_source="roaster notes", _bean="Gratus"))
    assert profile.annotations == {"_source": "roaster notes", "_bean": "Gratus"}
    assert profile.to_device()["_bean"] == "Gratus"


def test_simple_integer_pump_validates() -> None:
    body = _profile()
    body["phases"][0]["pump"] = 100
    assert Phase.model_validate(body["phases"][0]).pump == 100


def test_utility_profile_with_integer_pump_validates() -> None:
    raw = json.loads((PROFILE_FIXTURES / "firmware-flush.json").read_text())
    profile = Profile.model_validate(raw)
    assert profile.utility is True
    assert all(isinstance(p.pump, int) for p in profile.phases)


def test_hold_measured_sentinel_is_accepted() -> None:
    """`-1` means 'hold whatever was measured at phase entry'."""
    pump = Pump.model_validate({"target": "flow", "pressure": -1, "flow": -1})
    assert pump.pressure == -1
    assert pump.flow == -1


# ── profiles that must be rejected ───────────────────────────────────


def test_zero_phases_is_rejected() -> None:
    """`BrewProcess` calls `phases.at(0)` — an empty profile crashes brew start."""
    with pytest.raises(ValidationError):
        Profile.model_validate(_profile(phases=[]))


def test_float_pump_percent_is_rejected() -> None:
    """`100.0` is the trap: the firmware branches on `is<int>()`.

    A float falls through to the advanced-object branch, every field reads back
    as zero, and the pump never runs — with no error anywhere.
    """
    body = _profile()
    body["phases"][0]["pump"] = 100.0
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


def test_unknown_target_type_is_rejected() -> None:
    """The firmware drops an unrecognised target type silently (`continue;`)."""
    body = _profile()
    body["phases"][0]["targets"] = [{"type": "weight", "operator": "gte", "value": 36}]
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


def test_operator_gt_is_rejected() -> None:
    """Any unrecognised operator spelling parses as `lte` on the device.

    `"gt"` therefore inverts the stop condition rather than failing, so the
    phase ends at the wrong moment and nothing reports why.
    """
    body = _profile()
    body["phases"][0]["targets"] = [{"type": "pressure", "operator": "gt", "value": 3}]
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


def test_temperature_200_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Profile.model_validate(_profile(temperature=200))


def test_phase_temperature_200_is_rejected() -> None:
    body = _profile()
    body["phases"][0]["temperature"] = 200
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


@pytest.mark.parametrize("bad_id", ["has space", "slash/es", "a" * 32, ""])
def test_ids_that_would_not_fit_the_slog_header_are_rejected(bad_id: str) -> None:
    """`profileId[32]` in the shot header means 31 usable characters."""
    with pytest.raises(ValidationError):
        Profile.model_validate(_profile(id=bad_id))


def test_unknown_top_level_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Profile.model_validate(_profile(mystery=1))


def test_unknown_phase_key_is_rejected() -> None:
    body = _profile()
    body["phases"][0]["mystery"] = 1
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


def test_unknown_transition_type_is_rejected() -> None:
    body = _profile()
    body["phases"][0]["transition"] = {"type": "bounce", "duration": 1, "adaptive": False}
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


def test_negative_target_value_is_rejected() -> None:
    body = _profile()
    body["phases"][0]["targets"] = [{"type": "flow", "value": -1}]
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


def test_phase_duration_beyond_the_firmware_cap_is_rejected() -> None:
    body = _profile()
    body["phases"][0]["duration"] = 301
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


# ── canonical form and content hash ──────────────────────────────────


def test_canonical_json_ignores_device_managed_fields() -> None:
    """Re-importing a profile gives it a new id; that is not a new profile."""
    a = Profile.model_validate(_profile(id="aaa", favorite=True, selected=True))
    b = Profile.model_validate(_profile(id="bbb", favorite=False, selected=False))
    assert canonical_profile_json(a) == canonical_profile_json(b)
    assert profile_content_hash(a) == profile_content_hash(b)


def test_canonical_json_normalises_number_spelling() -> None:
    """The firmware round-trips numbers as floats; `28` and `28.0` are one shot."""
    a = Profile.model_validate(_profile())
    body = _profile()
    body["phases"][0]["duration"] = 28.0
    body["temperature"] = 93.0
    b = Profile.model_validate(body)
    assert canonical_profile_json(a) == canonical_profile_json(b)


def test_canonical_json_has_sorted_keys() -> None:
    text = canonical_profile_json(Profile.model_validate(_profile()))
    parsed = json.loads(text)
    assert list(parsed) == sorted(parsed)
    assert text == json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_content_hash_changes_when_the_brew_changes() -> None:
    a = Profile.model_validate(_profile())
    body = _profile()
    body["phases"][0]["pump"] = {"target": "pressure", "pressure": 6, "flow": 0}
    b = Profile.model_validate(body)
    assert profile_content_hash(a) != profile_content_hash(b)


def test_content_hash_ignores_user_annotations() -> None:
    a = Profile.model_validate(_profile())
    b = Profile.model_validate(_profile(_note="second attempt"))
    assert profile_content_hash(a) == profile_content_hash(b)


def test_content_hash_is_sha256_hex() -> None:
    digest = profile_content_hash(Profile.model_validate(_profile()))
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


# ── shot notes ───────────────────────────────────────────────────────


def test_notes_round_trip_is_byte_identical_to_the_device_form() -> None:
    """The real notes object from the maintainer's export, in and out unchanged."""
    device = load_export("shot-129.json")["notes"]
    notes = ShotNotes.model_validate(device)
    assert notes.to_device() == device


def test_notes_keep_numerics_as_strings() -> None:
    """`doseOut` is only honoured as an override when it arrives as a string.

    `notes["doseOut"].is<String>()` in ShotHistoryPlugin.cpp — send the number
    and the index volume is silently left alone.
    """
    notes = ShotNotes.model_validate({"id": "000129", "doseOut": "32.1"})
    assert notes.dose_out == "32.1"
    assert isinstance(notes.to_device()["doseOut"], str)


def test_parsed_view_gives_numbers_and_none() -> None:
    notes = ShotNotes.model_validate(
        {
            "id": "000129",
            "rating": 4,
            "doseIn": "18",
            "doseOut": "36.5",
            "ratio": "",
            "grindSetting": "",
            "balanceTaste": "sour",
            "notes": "a touch fast",
        }
    )
    parsed = notes.parsed
    assert parsed.dose_in == 18.0
    assert parsed.dose_out == 36.5
    assert parsed.ratio is None
    assert parsed.grind_setting is None
    assert parsed.balance_taste == "sour"
    assert parsed.rating == 4


def test_parsed_view_survives_junk_in_a_numeric_field() -> None:
    """The field is a free-text form input; it can contain anything."""
    notes = ShotNotes.model_validate({"id": "1", "doseIn": "about 18g"})
    assert notes.parsed.dose_in is None
    assert notes.to_device()["doseIn"] == "about 18g"


def test_numbers_are_normalised_into_the_device_string_form() -> None:
    """Another client may have written a number; never lose the note over it."""
    notes = ShotNotes.model_validate({"id": "1", "doseIn": 18, "doseOut": 36.5})
    assert notes.dose_in == "18"
    assert notes.dose_out == "36.5"


def test_notes_longer_than_200_characters_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ShotNotes.model_validate({"id": "1", "notes": "x" * 201})


def test_unknown_balance_taste_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ShotNotes.model_validate({"id": "1", "balanceTaste": "fruity"})


def test_rating_above_five_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ShotNotes.model_validate({"id": "1", "rating": 6})


def test_optional_fields_stay_absent_on_the_wire() -> None:
    """The firmware never sets `timestamp`; do not invent one for it."""
    notes = ShotNotes.model_validate({"id": "000001"})
    device = notes.to_device()
    assert "timestamp" not in device
    assert "beanType" not in device
    assert device["id"] == "000001"


def test_timestamp_round_trips_when_a_client_did_set_it() -> None:
    notes = ShotNotes.model_validate({"id": "1", "timestamp": 1_789_063_889})
    assert notes.to_device()["timestamp"] == 1_789_063_889


# ── live status and OTA ──────────────────────────────────────────────


def test_live_status_accepts_a_telemetry_frame() -> None:
    status = LiveStatus.model_validate(
        {
            "ct": 92.4,
            "tt": 93.0,
            "pr": 8.9,
            "fl": 2.1,
            "pw": 62.0,
            "hp": 18.5,
            "bw": 21.3,
            "process": {"a": 1, "s": "brew", "l": "Ramp", "e": 12400, "tt": "volumetric"},
            "pkr": 1.4,
        }
    )
    assert status.process is not None
    assert status.process.label == "Ramp"
    assert status.process.e == 12400


def test_live_status_accepts_a_state_frame() -> None:
    status = LiveStatus.model_validate(
        {
            "m": 1,
            "p": "Cremina v2",
            "puid": "dCs4AOOcBn",
            "cp": True,
            "cd": True,
            "sys": {"s": "ready", "m": "", "c": 0},
            "warn": [{"k": "waterLevel", "l": 1, "a": False}],
            "bc": True,
            "sbat": 84,
        }
    )
    assert status.sys is not None
    assert status.sys.s == "ready"
    assert status.warn is not None
    assert status.warn[0].level == 1
    assert status.has_pressure is True


def test_every_status_field_is_optional() -> None:
    """Both frames share `evt:status`; each carries only part of the picture."""
    assert LiveStatus().model_dump(exclude_none=True) == {}


def test_has_pressure_is_false_without_the_capability_flag() -> None:
    """A Standard board reports `cp: false` and a hard zero for `pr`."""
    assert LiveStatus.model_validate({"cp": False, "pr": 0.0}).has_pressure is False
    assert LiveStatus().has_pressure is False


def test_unknown_status_key_is_kept_not_rejected() -> None:
    """A firmware update must not be able to silence the live connection.

    `evt:status` arrives twice a second. If one new telemetry key made every
    frame fail validation, the device socket would go dark over a field we did not
    need — so unknown keys are carried rather than refused, and stay visible in
    a log instead of vanishing.
    """
    status = LiveStatus.model_validate({"ct": 92.0, "brandNew": 1})
    assert status.ct == 92.0
    assert status.model_dump(exclude_none=True)["brandNew"] == 1


def test_unknown_note_key_is_carried_through_verbatim() -> None:
    """`saveNotes` stores whatever object it is handed, extra keys included.

    Another client's field is already on the machine; dropping it on a
    read-modify-write would delete someone else's data, and rejecting it would
    quarantine the shot.
    """
    device = {"id": "000129", "doseOut": "32.1", "roaster": "Gratus"}
    notes = ShotNotes.model_validate(device)
    assert notes.to_device()["roaster"] == "Gratus"


def test_literal_annotations_key_is_rejected_on_a_profile() -> None:
    """`annotations` is where the `^_` keys land, not a key anyone may write.

    Accepting it would open a second annotation namespace past `extra="forbid"`
    when the schema allows exactly one.
    """
    with pytest.raises(ValidationError, match="leading underscore"):
        Profile.model_validate(_profile(annotations={"smuggled": 1}))


def test_ota_settings_parses_the_device_identity_frame() -> None:
    ota = OtaSettings.model_validate(
        {
            "latestVersion": "1.9.0",
            "displayUpdateAvailable": False,
            "controllerUpdateAvailable": False,
            "displayVersion": "v1.9.0-3-gabc123",
            "controllerVersion": "v1.9.0",
            "hardware": "GaggiMate Pro Rev 1.1",
            "channel": "latest",
            "updating": False,
        }
    )
    assert ota.hardware == "GaggiMate Pro Rev 1.1"
    assert ota.display_version == "v1.9.0-3-gabc123"
    assert ota.model_dump(by_alias=True, exclude_none=True)["latestVersion"] == "1.9.0"


@pytest.mark.parametrize(
    "pump",
    [
        {"target": "pressure", "pressure": 20, "flow": 0},
        {"target": "flow", "pressure": 0, "flow": 40},
        {"target": "pressure", "pressure": -2, "flow": 0},
    ],
)
def test_pump_values_outside_the_hardware_range_are_rejected(pump: dict[str, Any]) -> None:
    """Only `-1` is meaningful below zero, and no pump reaches 20 bar."""
    body = _profile()
    body["phases"][0]["pump"] = pump
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


def test_pump_percent_above_one_hundred_is_rejected() -> None:
    body = _profile()
    body["phases"][0]["pump"] = 150
    with pytest.raises(ValidationError):
        Profile.model_validate(body)


def test_to_device_puts_the_id_first_and_drops_nothing() -> None:
    profile = Profile.model_validate(_profile(id="9bar", _note="keep me"))
    device = profile.to_device()
    assert next(iter(device)) == "id"
    assert device["_note"] == "keep me"
    assert "annotations" not in device
    # What comes out validates again: a save-then-load round trip cannot drift.
    assert Profile.model_validate(device) == profile


# ── surviving the device's own round trip ────────────────────────────


def _as_write_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Reshape a profile the way the firmware's `writeProfile` hands it back.

    It always emits `id`, `favorite`, `selected`, `utility`, a per-phase
    `temperature` (0 where the author inherited), a full `transition` including
    the undocumented `target`, and numbers as floats. `targets` is emitted only
    when non-empty. See gaggimate-firmware.md §3.3.
    """

    def as_float(value: Any) -> Any:
        return (
            float(value)
            if isinstance(value, int | float) and not isinstance(value, bool)
            else value
        )

    phases: list[dict[str, Any]] = []
    for phase in raw["phases"]:
        out = dict(phase)
        out["duration"] = as_float(phase["duration"])
        out["temperature"] = as_float(phase.get("temperature", 0))
        transition = dict(phase.get("transition") or {})
        out["transition"] = {
            "type": transition.get("type", "instant"),
            "target": transition.get("target", "time"),
            "duration": as_float(transition.get("duration", 0)),
            "adaptive": transition.get("adaptive", False),
        }
        pump = phase["pump"]
        if isinstance(pump, dict):
            # Never float-ified: the simple form must stay an integer percent
            # or the firmware reads it as an advanced object full of zeros.
            out["pump"] = {
                "target": pump["target"],
                "pressure": as_float(pump["pressure"]),
                "flow": as_float(pump["flow"]),
            }
        targets = phase.get("targets") or []
        if targets:
            out["targets"] = [
                {
                    "type": t["type"],
                    "operator": t.get("operator", "gte"),
                    "value": as_float(t["value"]),
                }
                for t in targets
            ]
        else:
            out.pop("targets", None)
        phases.append(out)

    return {
        "id": "aB3xYz90Pq",
        "label": raw["label"],
        "type": raw["type"],
        "description": raw.get("description", ""),
        "temperature": as_float(raw.get("temperature", 0)),
        "favorite": True,
        "selected": True,
        "utility": raw.get("utility", False),
        "phases": phases,
    }


@pytest.mark.parametrize("name", ALL_PROFILE_FIXTURES)
def test_content_hash_survives_the_devices_own_round_trip(name: str) -> None:
    """Send a profile to the machine, read it back, and it must still be itself.

    This is the property the hash exists for. `writeProfile` returns something
    textually different every time — an id it assigned, a transition it filled
    in, floats where the author wrote ints — and if any of that reached the
    hash, every profile would look like it changed the first time the machine
    touched it.
    """
    raw = json.loads((PROFILE_FIXTURES / name).read_text())
    source = Profile.model_validate(raw)
    returned = Profile.model_validate(_as_write_profile(raw))

    assert returned.id == "aB3xYz90Pq"
    assert profile_content_hash(returned) == profile_content_hash(source)
    assert canonical_profile_json(returned) == canonical_profile_json(source)


def test_a_default_transition_hashes_the_same_as_no_transition() -> None:
    """`{instant, 0, false}` is exactly what the firmware writes for "none"."""
    body = _profile()
    body["phases"][0].pop("transition")
    absent = Profile.model_validate(body)

    body["phases"][0]["transition"] = {"type": "instant", "duration": 0, "adaptive": False}
    default = Profile.model_validate(body)

    assert profile_content_hash(absent) == profile_content_hash(default)


def test_a_real_transition_still_changes_the_hash() -> None:
    """Dropping the default must not blind the hash to a transition that matters."""
    plain = Profile.model_validate(_profile())
    body = _profile()
    body["phases"][0]["transition"] = {"type": "linear", "duration": 5, "adaptive": True}
    assert profile_content_hash(Profile.model_validate(body)) != profile_content_hash(plain)


def test_phase_temperature_zero_hashes_as_inheriting() -> None:
    """`0` is the sentinel for "use the profile temperature" — same as absent."""
    body = _profile()
    body["phases"][0].pop("temperature")
    absent = Profile.model_validate(body)
    explicit = Profile.model_validate(_profile())
    assert profile_content_hash(absent) == profile_content_hash(explicit)


def test_transition_target_time_hashes_as_absent() -> None:
    """`writeProfile` always adds `target`, defaulting to `"time"`."""
    body = _profile()
    body["phases"][0]["transition"] = {
        "type": "linear",
        "duration": 5,
        "adaptive": True,
        "target": "time",
    }
    with_target = Profile.model_validate(body)
    body["phases"][0]["transition"].pop("target")
    without = Profile.model_validate(body)
    assert profile_content_hash(with_target) == profile_content_hash(without)


def test_a_volumetric_transition_target_does_change_the_hash() -> None:
    """Only the `"time"` default is noise; the other two change what is brewed."""
    body = _profile()
    body["phases"][0]["transition"] = {
        "type": "linear",
        "duration": 5,
        "adaptive": True,
        "target": "volumetric",
    }
    volumetric = Profile.model_validate(body)
    body["phases"][0]["transition"]["target"] = "time"
    assert profile_content_hash(volumetric) != profile_content_hash(Profile.model_validate(body))


def test_unknown_key_inside_process_does_not_fail_the_frame() -> None:
    """`process` is the object the firmware actually grows between releases.

    It is nested inside a frame that arrives twice a second, so a closed model
    here would have cost the whole status frame — and with it the live view —
    over one key nobody was reading.
    """
    status = LiveStatus.model_validate(
        {"ct": 92.0, "process": {"a": 1, "s": "brew", "l": "Ramp", "phaseIndex": 2}}
    )
    assert status.process is not None
    assert status.process.label == "Ramp"
    assert status.process.model_dump(exclude_none=True)["phaseIndex"] == 2


def test_unknown_key_inside_a_warning_entry_does_not_fail_the_frame() -> None:
    status = LiveStatus.model_validate(
        {"warn": [{"k": "waterLevel", "l": 2, "a": True, "since": 1_789_063_889}]}
    )
    assert status.warn is not None
    assert status.warn[0].level == 2
    assert status.warn[0].model_dump(exclude_none=True)["since"] == 1_789_063_889


def test_unknown_key_inside_sys_does_not_fail_the_frame() -> None:
    status = LiveStatus.model_validate({"sys": {"s": "ready", "uptime": 4200}})
    assert status.sys is not None
    assert status.sys.s == "ready"
    assert status.sys.model_dump(exclude_none=True)["uptime"] == 4200

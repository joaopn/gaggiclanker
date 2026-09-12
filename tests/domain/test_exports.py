"""The web UI's JSON exports, read back.

The question every test here asks in one form or another: **is an imported shot
the same shot as a fetched one?** If the export → `Slog` conversion rounds one
sample differently from the binary parser, the same espresso scores differently
depending on which door it came in through, and two shots in the archive stop
being comparable. So the round trips are the subject, not a nicety.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from gaggiclanker.domain.diagnostics import transform_shot
from gaggiclanker.domain.exports import (
    ShotExport,
    export_device_id,
    looks_like_profile_export,
    looks_like_shot_export,
    profile_export_to_profiles,
    shot_export_to_slog,
    slog_to_raw,
)
from gaggiclanker.domain.models import Profile, profile_content_hash
from gaggiclanker.domain.scoring import execution_score
from gaggiclanker.domain.slog import FIELDS_MASK_ALL, FIELDS_MASK_V5, SlogError, parse_slog
from tests.domain.helpers import PROFILE_FIXTURES, load_export
from tests.imports.helpers import (
    PROFILE_ARRAY_EXPORT,
    V7_EXPORT,
    V7_SAMPLE_COUNT,
    export_from_slog,
    load_fixture,
    profile_array_export,
    synthetic_v7_export,
    synthetic_v7_slog,
)

# The maintainer's own export, pinned in tests/domain/test_slog.py too.
SHOT_129_SAMPLES = 213
SHOT_129_PHASES = 4
SHOT_129_DURATION_MS = 54617


def shot_129() -> ShotExport:
    return ShotExport.model_validate(load_export("shot-129.json"))


# ── the model ────────────────────────────────────────────────────────


def test_the_real_export_parses_with_its_ui_state_intact() -> None:
    export = shot_129()

    assert export.id == "129"
    assert export.profile == "Gratus 16:32 trad"
    assert export.profile_id == "rV4GhUcSZc"
    assert export.duration == SHOT_129_DURATION_MS
    assert export.sample_interval == 250
    assert export.fields_mask == FIELDS_MASK_V5
    assert export.samples_expected == SHOT_129_SAMPLES
    assert len(export.samples) == SHOT_129_SAMPLES
    assert len(export.phase_transitions) == SHOT_129_PHASES
    assert export.notes is not None
    assert export.notes.dose_out == "32.1"
    # `loaded` and `data` are UI state. Kept rather than rejected, and ignored.
    assert export.model_extra is not None
    assert export.model_extra["loaded"] is True


def test_system_info_reads_as_the_object_or_as_the_bare_integer() -> None:
    """Older files and hand-written ones write the bitfield; the UI writes the object."""
    decoded = ShotExport.model_validate(
        {"samples": [{"t": 0, "systemInfo": {"raw": 13, "bluetoothScaleConnected": True}}]}
    )
    plain = ShotExport.model_validate({"samples": [{"t": 0, "systemInfo": 13}]})

    assert decoded.samples[0].si == 13
    assert plain.samples[0].si == 13


def test_an_unknown_key_is_kept_and_a_bad_value_is_refused() -> None:
    """Lenient about keys, strict about values — the module's first rule."""
    lenient = ShotExport.model_validate({"samples": [], "somethingNewInV8": 42})
    assert lenient.model_extra == {"somethingNewInV8": 42}

    with pytest.raises(ValidationError):
        ShotExport.model_validate({"samples": [], "duration": "fifty seconds"})
    with pytest.raises(ValidationError):
        # 54617.5 ms is not a rounding of anything the UI writes; truncating it
        # silently would be reading a file we do not understand.
        ShotExport.model_validate({"samples": [], "duration": 54_617.5})


def test_a_numeric_id_is_the_same_shot_as_a_string_one() -> None:
    assert export_device_id(ShotExport.model_validate({"id": 129, "samples": []})) == "000129"
    assert export_device_id(ShotExport.model_validate({"id": "000129", "samples": []})) == "000129"
    # Not a decimal id: kept verbatim rather than rejected, because the row it
    # keys is better than no row at all.
    assert (
        export_device_id(ShotExport.model_validate({"id": "rescued", "samples": []})) == "rescued"
    )


def test_detection_reads_the_content_not_the_name() -> None:
    assert looks_like_shot_export(load_export("shot-129.json"))
    assert not looks_like_shot_export(load_export("profile-dCs4AOOcBn.json"))
    assert looks_like_profile_export(load_export("profile-dCs4AOOcBn.json"))
    assert looks_like_profile_export(load_fixture(PROFILE_ARRAY_EXPORT))
    assert not looks_like_profile_export([])
    assert not looks_like_shot_export("a string")
    # Recognisable as a shot even though the samples are nonsense, which is what
    # lets the importer quarantine it with its bytes rather than drop it.
    assert looks_like_shot_export({"id": "7", "samples": "not an array"})


# ── shots: the round trips ───────────────────────────────────────────


def test_the_rebuilt_slog_is_what_the_parser_would_have_produced() -> None:
    """export -> Slog -> bytes -> Slog, and the two Slogs agree field by field."""
    slog = shot_export_to_slog(shot_129())
    reparsed = parse_slog(slog_to_raw(slog), slog.shot_id)

    assert reparsed.header == slog.header
    assert reparsed.samples == slog.samples
    assert reparsed.incomplete == slog.incomplete
    assert len(reparsed.samples) == SHOT_129_SAMPLES


def test_diagnostics_of_an_imported_shot_equal_those_of_the_binary() -> None:
    """The acceptance that matters: one shot, one score, whichever door it came in."""
    slog = shot_export_to_slog(shot_129())
    reparsed = parse_slog(slog_to_raw(slog), slog.shot_id)

    assert transform_shot(slog, "per_phase") == transform_shot(reparsed, "per_phase")
    assert execution_score(transform_shot(slog, "per_phase")).score == pytest.approx(7.7)


def test_every_exported_sample_survives_the_rebuild() -> None:
    """Each of the 213 samples matches the device's own reading of it."""
    export = shot_129()
    slog = shot_export_to_slog(export)

    for i, (row, sample) in enumerate(zip(export.samples, slog.samples, strict=True)):
        for field in ("tt", "ct", "tp", "cp", "fl", "tf", "pf", "vf", "v", "ev", "pr"):
            assert getattr(sample, field) == pytest.approx(row.value(field)), f"sample {i} {field}"
        assert sample.t == row.t
        assert sample.si == row.si
        # The phase comes from the transition table, and agrees with the
        # per-sample number the UI wrote.
        assert sample.phase == row.phase_number


def test_the_header_carries_what_the_export_says() -> None:
    slog = shot_export_to_slog(shot_129())

    assert slog.version == 5
    assert slog.sample_interval == 250
    assert slog.header.fields_mask == FIELDS_MASK_V5
    assert slog.duration_ms == SHOT_129_DURATION_MS
    assert slog.volume_g == pytest.approx(32.1)
    assert slog.profile_name == "Gratus 16:32 trad"
    assert slog.incomplete is False
    assert [t.phase_name for t in slog.transitions] == ["fill", "soak", "ramp", "decline 9-4"]
    assert slog.shot_id == "000129"


def test_a_v7_export_keeps_the_four_fields_v7_added() -> None:
    export = ShotExport.model_validate(load_fixture(V7_EXPORT))
    slog = shot_export_to_slog(export)
    reparsed = parse_slog(slog_to_raw(slog), slog.shot_id)

    assert slog.version == 7
    assert slog.header.fields_mask == FIELDS_MASK_ALL
    assert slog.header.final_exit_reason == 1
    assert slog.header.final_exit_reason_label == "Volumetric target"
    assert slog.header.brew_delay_ms == 900
    assert [t.transition_reason for t in slog.transitions] == [0, 2, 5, 3]
    assert all(sample.wp is not None for sample in slog.samples)
    assert reparsed.samples == slog.samples
    assert reparsed.header == slog.header


def test_v6_and_later_keep_the_real_millisecond_timestamps() -> None:
    """Before v6 the file stores a sample index; from v6 it stores `millis()`."""
    export = ShotExport.model_validate(load_fixture(V7_EXPORT))
    slog = shot_export_to_slog(export)

    assert [s.t for s in slog.samples] == [row.t for row in export.samples]
    # ... and they are deliberately not multiples of the interval.
    assert any(s.t is not None and s.t % 250 for s in slog.samples)


def test_before_v6_a_time_that_is_not_a_whole_interval_rounds_down() -> None:
    """v5 stores an index, so the reader can only ever answer index * interval."""
    export = ShotExport.model_validate(
        {"version": 5, "sampleInterval": 250, "fieldsMask": 0b11, "samples": [{"t": 620, "tt": 93}]}
    )
    slog = shot_export_to_slog(export)

    assert slog.samples[0].t == 500


def test_a_field_in_the_mask_but_absent_from_the_export_reads_as_zero() -> None:
    """What the mask says exists, the parser reads — as 0 when nothing was written.

    A field whose mask bit is *clear* stays None: "never recorded" and "recorded
    zero" are different facts, and the pressure diagnostics depend on the
    difference.
    """
    export = ShotExport.model_validate(
        {"version": 5, "sampleInterval": 250, "fieldsMask": 0b11111, "samples": [{"t": 0}]}
    )
    sample = shot_export_to_slog(export).samples[0]

    assert sample.tt == 0
    assert sample.cp == 0
    assert sample.fl is None
    assert sample.wp is None


def test_values_are_clamped_the_way_the_firmware_clamps_them() -> None:
    """A value the 16-bit field cannot hold is pinned, not wrapped."""
    export = ShotExport.model_validate(
        {"version": 7, "fieldsMask": 0b11111, "samples": [{"t": 0, "tt": 99_999, "cp": -4}]}
    )
    sample = shot_export_to_slog(export).samples[0]

    assert sample.tt == pytest.approx(6553.5)
    assert sample.cp == 0


def test_an_export_claiming_more_samples_than_it_carries_is_incomplete() -> None:
    """A torn file re-encodes to a file the parser calls torn for the same reason."""
    export = ShotExport.model_validate(
        {
            "version": 5,
            "sampleInterval": 250,
            "fieldsMask": FIELDS_MASK_V5,
            "samplesExpected": 9,
            "samples": [{"t": 0}, {"t": 250}],
        }
    )
    slog = shot_export_to_slog(export)

    assert slog.incomplete is True
    assert parse_slog(slog_to_raw(slog)).incomplete is True


def test_a_mask_bit_this_build_cannot_write_is_an_error_not_a_guess() -> None:
    export = ShotExport.model_validate({"version": 7, "fieldsMask": 0xFFFF, "samples": [{"t": 0}]})

    with pytest.raises(SlogError):
        slog_to_raw(shot_export_to_slog(export))


def test_the_export_of_a_shot_round_trips_back_to_the_same_export() -> None:
    """The builder and the importer are inverses, so the fixture proves both."""
    slog = synthetic_v7_slog()
    rebuilt = shot_export_to_slog(ShotExport.model_validate(export_from_slog(slog, shot_id="412")))

    assert rebuilt.samples == slog.samples
    assert len(rebuilt.samples) == V7_SAMPLE_COUNT


# ── profiles ─────────────────────────────────────────────────────────


def test_the_ui_export_and_the_firmware_output_are_one_profile() -> None:
    """The UI strips id/selected/favorite; `writeProfile` adds them and `transition.target`."""
    ui_form = load_export("profile-dCs4AOOcBn.json")
    firmware_form = profile_array_export()[0]

    [from_ui] = profile_export_to_profiles(ui_form)
    [from_firmware, _] = profile_export_to_profiles(profile_array_export())

    assert from_firmware.id == "dCs4AOOcBn"
    assert from_firmware.favorite is True
    assert from_firmware.phases[0].transition is not None
    assert from_firmware.phases[0].transition.target == "time"
    assert firmware_form["phases"][0]["transition"]["target"] == "time"
    assert profile_content_hash(from_ui) == profile_content_hash(from_firmware)
    assert from_ui.label == "Cremina v2"


def test_a_json_array_is_a_multi_profile_export() -> None:
    profiles = profile_export_to_profiles(load_fixture(PROFILE_ARRAY_EXPORT))

    assert [p.label for p in profiles] == ["Cremina v2", "9 Bar Espresso"]


def test_every_shipped_profile_fixture_imports() -> None:
    """The firmware's own sample profiles are exports too, and all of them read."""
    for path in sorted(PROFILE_FIXTURES.glob("*.json")):
        profiles = profile_export_to_profiles(json.loads(path.read_text()))
        assert profiles, path.name
        assert all(isinstance(p, Profile) for p in profiles)


def test_a_profile_the_safety_model_refuses_is_an_error() -> None:
    """A float `pump` is parsed by the firmware as an object with zero targets."""
    document = load_export("profile-dCs4AOOcBn.json")
    document["phases"][0]["pump"] = 100.0

    with pytest.raises(ValidationError):
        profile_export_to_profiles(document)


# ── the committed fixtures ───────────────────────────────────────────


def test_the_synthetic_fixtures_still_match_their_builders() -> None:
    """The files on disk are generated; this is what keeps them honest."""
    assert load_fixture(V7_EXPORT) == synthetic_v7_export()
    assert load_fixture(PROFILE_ARRAY_EXPORT) == profile_array_export()

"""`.slog` codec tests: every version, both directions, and the real files.

The upstream v4/v5 fixtures and the maintainer's own v5 export are the ground
truth here — the synthetic files exist to cover versions no fixture has yet.
"""

from __future__ import annotations

import struct
from typing import Any

import pytest

from gaggiclanker.domain.models import PhaseTransition, Sample, SlogHeader
from gaggiclanker.domain.slog import (
    FIELDS_MASK_ALL,
    FIELDS_MASK_V5,
    MAGIC,
    Slog,
    SlogError,
    UnsupportedSlogVersion,
    encode_slog,
    header_size_for,
    parse_slog,
    sample_size_for,
)
from tests.domain.helpers import SLOG_FIXTURES, load_export, slog_from_export

# The chunk spec asks for these to be confirmed by running the firmware's own
# `parseBinaryShot.js` under Node. Node is not installable in this container
# (no runtime present, no root to add one), so they were confirmed two other
# ways instead, both independent of the code under test:
#
#   * `tests/fixtures/exports/shot-129.json` *is* the output of that JS parser
#     — the maintainer exported it from the machine's own web UI — so agreeing
#     with it is agreeing with the reference implementation, field by field.
#   * The UPSTREAM_FIXTURE_EXPECTATIONS below were cross-checked against
#     gaggimate-mcp's decoder, a separate Python implementation written from
#     the same `parseBinaryShot.js`, which reports the same counts and
#     durations for all three files.
#
# Pinned as constants so a parser change has to argue with them.
SHOT_129_SAMPLES = 213
SHOT_129_PHASES = 4
SHOT_129_DURATION_MS = 54617

# Likewise for the vendored upstream fixtures.
UPSTREAM_FIXTURE_EXPECTATIONS = {
    "shot_196_baseline_high.slog": (5, 118, 30846),
    "shot_204_ramping_flow.slog": (5, 188, 47809),
    "shot_222_hold_false_positive.slog": (5, 129, 32852),
}


def _synthetic(version: int, n: int = 8, *, fields_mask: int | None = None) -> Slog:
    """A small shot in the shape a given firmware version would have written."""
    if fields_mask is None:
        fields_mask = FIELDS_MASK_ALL if version >= 7 else FIELDS_MASK_V5
    interval = 250
    samples = [
        Sample(
            t=i * interval,
            tt=93.0,
            ct=92.0 + i * 0.1,
            tp=9.0,
            cp=2.0 + i * 0.9,
            fl=2.5 - i * 0.1,
            tf=2.0,
            pf=1.5 + i * 0.05,
            vf=-0.2 if i == 0 else 0.4,
            v=i * 4.1,
            ev=i * 4.3,
            pr=1.25 + i * 0.02,
            si=0x000D,
            wp=i * 3.7 if version >= 7 else None,
        )
        for i in range(n)
    ]
    transitions = (
        [
            PhaseTransition(
                sample_index=0, phase_number=0, transition_reason=0, phase_name="Preinfusion"
            ),
            PhaseTransition(
                sample_index=n // 2, phase_number=1, transition_reason=2, phase_name="Extraction"
            ),
        ]
        if version >= 5
        else []
    )
    header = SlogHeader(
        version=version,
        sample_size=sample_size_for(version, fields_mask),
        header_size=header_size_for(version),
        sample_interval=interval,
        fields_mask=fields_mask,
        sample_count=n,
        duration_ms=max(0, (n - 1) * interval),
        start_epoch=1_789_063_889,
        profile_id="dCs4AOOcBn",
        profile_name="Cremina v2",
        final_weight_g=32.1,
        transitions=transitions,
        final_exit_reason=1 if version >= 5 else 0,
        brew_delay_ms=800 if version >= 5 else 0,
    )
    return Slog(header=header, samples=samples, shot_id="000042")


# ── round trips ──────────────────────────────────────────────────────


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 7])
def test_encode_parse_round_trip(version: int) -> None:
    """Every version we claim to support survives encode -> parse unchanged."""
    original = _synthetic(version)
    parsed = parse_slog(encode_slog(original), "000042")

    assert parsed.version == version
    assert len(parsed.samples) == len(original.samples)
    assert parsed.incomplete is False
    assert parsed.profile_id == original.profile_id
    assert parsed.profile_name == original.profile_name
    assert parsed.timestamp == original.timestamp
    assert parsed.duration_ms == original.header.duration_ms
    assert parsed.volume_g == original.volume_g

    for before, after in zip(original.samples, parsed.samples, strict=True):
        for field in ("t", "tt", "ct", "tp", "cp", "fl", "tf", "pf", "vf", "v", "ev", "pr", "si"):
            assert getattr(after, field) == pytest.approx(getattr(before, field)), field
        # wp only exists from v7; earlier masks do not carry the bit at all.
        if version >= 7:
            assert after.wp == pytest.approx(before.wp)
        else:
            assert after.wp is None


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 7])
def test_encoded_size_matches_the_declared_layout(version: int) -> None:
    slog = _synthetic(version, n=5)
    raw = encode_slog(slog)
    expected_sample = sample_size_for(version, slog.header.fields_mask)
    assert len(raw) == header_size_for(version) + 5 * expected_sample
    # `reserved0` must advertise the size actually written, which is how the
    # firmware's own rebuild path decodes old files.
    assert raw[5] == expected_sample


def test_v6_widens_the_time_field_by_two_bytes() -> None:
    """The whole v5 -> v6 change is visible as +2 bytes per sample."""
    assert sample_size_for(5, FIELDS_MASK_V5) == 26
    assert sample_size_for(6, FIELDS_MASK_V5) == 28
    assert sample_size_for(7, FIELDS_MASK_ALL) == 30


def test_v5_time_survives_only_at_interval_resolution() -> None:
    """v5 stores a sample index, so `t` re-reads as a multiple of the interval.

    Worth pinning: it is the reason the importer cannot recover sub-interval timing from
    a v5 export, however precise the JSON looks.
    """
    slog = _synthetic(5, n=3)
    slog.samples[1].t = 260  # not a multiple of 250
    parsed = parse_slog(encode_slog(slog))
    assert parsed.samples[1].t == 250


def test_v6_keeps_millisecond_timestamps_exactly() -> None:
    slog = _synthetic(6, n=3)
    slog.samples[1].t = 263
    parsed = parse_slog(encode_slog(slog))
    assert parsed.samples[1].t == 263


def test_v7_carries_water_pumped_and_the_exit_reasons() -> None:
    parsed = parse_slog(encode_slog(_synthetic(7, n=6)))
    assert parsed.samples[-1].wp == pytest.approx(5 * 3.7, abs=0.05)
    assert parsed.header.final_exit_reason == 1
    assert parsed.header.final_exit_reason_label == "Volumetric target"
    assert parsed.header.brew_delay_ms == 800
    assert parsed.transitions[1].transition_reason_label == "Pressure target"


def test_pre_v5_has_no_transition_table() -> None:
    parsed = parse_slog(encode_slog(_synthetic(4, n=6)))
    assert parsed.transitions == []
    assert parsed.header.brew_delay_ms == 0


def test_system_info_bits_decode() -> None:
    parsed = parse_slog(encode_slog(_synthetic(7, n=5)))
    info = parsed.samples[0].system_info
    assert info is not None
    assert info.raw == 0x000D
    assert info.shot_started_volumetric is True
    assert info.currently_volumetric is False
    assert info.bluetooth_scale_connected is True
    assert info.volumetric_available is True
    assert info.extended_recording is False


def test_negative_flow_survives_the_signed_fields() -> None:
    """`fl`/`tf`/`pf`/`vf` are int16: a small negative reading is real data."""
    slog = _synthetic(7, n=5)
    slog.samples[0].fl = -0.35
    slog.samples[0].vf = -20.0
    parsed = parse_slog(encode_slog(slog))
    assert parsed.samples[0].fl == pytest.approx(-0.35)
    assert parsed.samples[0].vf == pytest.approx(-20.0)


def test_phase_is_stamped_onto_every_sample() -> None:
    parsed = parse_slog(encode_slog(_synthetic(7, n=8)))
    assert [s.phase for s in parsed.samples] == [0, 0, 0, 0, 1, 1, 1, 1]
    assert parsed.samples[0].phase_name == "Preinfusion"
    assert parsed.samples[-1].phase_name == "Extraction"


# ── real files ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(UPSTREAM_FIXTURE_EXPECTATIONS))
def test_real_fixture_parses_to_the_expected_shape(name: str) -> None:
    version, samples, duration = UPSTREAM_FIXTURE_EXPECTATIONS[name]
    slog = parse_slog((SLOG_FIXTURES / name).read_bytes(), name[:6])
    assert slog.version == version
    assert len(slog.samples) == samples
    assert slog.duration_ms == duration
    assert slog.incomplete is False
    assert slog.has_pressure is True


@pytest.mark.parametrize("name", sorted(UPSTREAM_FIXTURE_EXPECTATIONS))
def test_real_fixture_re_encodes_byte_for_byte(name: str) -> None:
    """The strongest statement the codec can make: nothing was lost or guessed."""
    raw = (SLOG_FIXTURES / name).read_bytes()
    assert encode_slog(parse_slog(raw)) == raw


def test_synthetic_v5_from_the_maintainers_own_export() -> None:
    """Re-encode the machine's own JSON export and read it back.

    The export came out of the firmware's JS parser, so agreeing with it is
    agreeing with the device. Real curves, four real phases, and the sample
    count and duration the machine itself reported.
    """
    raw = encode_slog(slog_from_export("shot-129.json"))
    slog = parse_slog(raw, "000129")

    assert slog.version == 5
    assert slog.sample_interval == 250
    assert slog.header.fields_mask == FIELDS_MASK_V5
    assert len(slog.samples) == SHOT_129_SAMPLES
    assert len(slog.transitions) == SHOT_129_PHASES
    assert slog.duration_ms == SHOT_129_DURATION_MS
    assert slog.incomplete is False
    assert [t.phase_name for t in slog.transitions] == ["fill", "soak", "ramp", "decline 9-4"]
    assert slog.volume_g == pytest.approx(32.1)
    assert slog.profile_name == "Gratus 16:32 trad"


def test_synthetic_v5_reproduces_every_exported_sample() -> None:
    """Every field of every sample matches the device's own reading."""
    export = load_export("shot-129.json")
    slog = parse_slog(encode_slog(slog_from_export("shot-129.json")), "000129")

    for i, (row, sample) in enumerate(zip(export["samples"], slog.samples, strict=True)):
        for field in ("tt", "ct", "tp", "cp", "fl", "tf", "pf", "vf", "v", "ev", "pr"):
            assert getattr(sample, field) == pytest.approx(row[field]), f"sample {i} {field}"
        assert sample.t == row["t"]
        assert sample.si == row["systemInfo"]["raw"]
        assert sample.phase == row["phaseNumber"]


# ── malformed input ──────────────────────────────────────────────────


def _minimal_header(version: int, **over: Any) -> bytes:
    fields_mask = over.get("fields_mask", 0b1111)
    size = header_size_for(version)
    out = bytearray(size)
    struct.pack_into(
        "<IBBHHHIIII32s48sH",
        out,
        0,
        over.get("magic", MAGIC),
        version,
        over.get("sample_size", sample_size_for(version, fields_mask)),
        over.get("header_size", size),
        over.get("sample_interval", 250),
        0,
        fields_mask,
        over.get("sample_count", 0),
        over.get("duration_ms", 0),
        over.get("start_epoch", 0),
        b"p\x00".ljust(32, b"\x00"),
        b"Profile\x00".ljust(48, b"\x00"),
        0,
    )
    return bytes(out)


def test_unknown_version_raises_rather_than_guessing() -> None:
    data = _minimal_header(4)
    data = data[:4] + bytes([9]) + data[5:]
    with pytest.raises(UnsupportedSlogVersion) as excinfo:
        parse_slog(data)
    assert excinfo.value.version == 9


def test_bad_magic_is_rejected() -> None:
    with pytest.raises(SlogError, match="invalid shot magic"):
        parse_slog(b"\xde\xad\xbe\xef" + b"\x00" * 124)


def test_html_response_is_named_for_what_it_is() -> None:
    """The device serves its own SPA for unknown paths; say so, don't say 'too small'."""
    with pytest.raises(SlogError, match="HTML"):
        parse_slog(b"<!DOCTYPE html><html><body>Not Found</body></html>")


def test_header_only_file_yields_no_samples_and_reads_incomplete() -> None:
    """A `.slog` is header-only while the shot is still being written."""
    slog = parse_slog(_minimal_header(7, fields_mask=FIELDS_MASK_ALL))
    assert slog.samples == []
    assert slog.incomplete is True


def test_zero_sample_count_means_never_finalised() -> None:
    """`sampleCount` is patched in at the end; 0 means the file was cut short."""
    slog = _synthetic(7, n=6)
    slog.header.sample_count = 0
    parsed = parse_slog(encode_slog(slog))
    assert parsed.incomplete is True
    # The samples are still there and still real — the archive keeps them.
    assert len(parsed.samples) == 6
    assert parsed.duration_ms == parsed.samples[-1].t


def test_trailing_bytes_mean_a_torn_write() -> None:
    raw = encode_slog(_synthetic(7, n=6)) + b"\x01\x02\x03"
    parsed = parse_slog(raw)
    assert parsed.trailing_bytes == 3
    assert parsed.incomplete is True
    assert len(parsed.samples) == 6


def test_claiming_more_samples_than_the_file_holds() -> None:
    slog = _synthetic(7, n=6)
    slog.header.sample_count = 20
    parsed = parse_slog(encode_slog(slog))
    assert parsed.incomplete is True
    assert len(parsed.samples) == 6
    assert parsed.header.sample_count == 20


def test_sample_size_contradicting_the_field_mask_is_an_error() -> None:
    """`reserved0` and `fieldsMask` must agree or we would misread every sample."""
    data = bytearray(_minimal_header(7, fields_mask=FIELDS_MASK_ALL))
    data[5] = 26  # a v5-sized sample claimed by a v7 file
    with pytest.raises(SlogError, match="bytes per sample"):
        parse_slog(bytes(data))


def test_empty_field_mask_is_an_error() -> None:
    with pytest.raises(SlogError, match="fieldsMask is 0"):
        parse_slog(_minimal_header(7, fields_mask=0))


def test_header_size_disagreeing_with_the_version_is_an_error() -> None:
    data = bytearray(_minimal_header(7, fields_mask=FIELDS_MASK_ALL))
    struct.pack_into("<H", data, 6, 128)
    with pytest.raises(SlogError, match="header size mismatch"):
        parse_slog(bytes(data))


def test_truncated_header_is_an_error() -> None:
    with pytest.raises(SlogError, match="too small"):
        parse_slog(_minimal_header(7)[:200])


def test_encode_refuses_a_version_it_cannot_read_back() -> None:
    slog = _synthetic(7, n=3)
    slog.header.version = 9
    with pytest.raises(UnsupportedSlogVersion):
        encode_slog(slog)


def test_profile_name_survives_a_multibyte_round_trip() -> None:
    """The name field is UTF-8; truncating mid-character would corrupt it."""
    slog = _synthetic(7, n=3)
    slog.header.profile_name = "Café crème — très fin"
    parsed = parse_slog(encode_slog(slog))
    assert parsed.profile_name == "Café crème — très fin"


def test_has_pressure_is_false_on_a_standard_board_trace() -> None:
    """Standard boards have no sensor and log a hard zero for `cp`."""
    slog = _synthetic(7, n=8)
    for sample in slog.samples:
        sample.cp = 0.0
        sample.tp = 0.0
    assert slog.has_pressure is False
    assert parse_slog(encode_slog(slog)).has_pressure is False


def test_a_mask_bit_from_a_future_firmware_is_skipped_not_misread() -> None:
    """An unknown field still occupies its two bytes in every sample.

    Walking every set bit in ascending order keeps the fields we *do* know
    aligned, so a firmware that adds bit 14 degrades to "we ignore that column"
    rather than "every reading after it is garbage".
    """
    future_mask = FIELDS_MASK_ALL | (1 << 14)
    known = encode_slog(_synthetic(7, n=4))
    record = sample_size_for(7, FIELDS_MASK_ALL)

    # Hand-built, because our own encoder refuses a mask it cannot write.
    raw = bytearray(known[:512])
    struct.pack_into("<I", raw, 12, future_mask)
    raw[5] = sample_size_for(7, future_mask)
    for i in range(4):
        start = 512 + i * record
        raw += known[start : start + record] + b"\xff\xff"

    parsed = parse_slog(bytes(raw))
    assert len(parsed.samples) == 4
    assert parsed.samples[2].ct == pytest.approx(92.2)
    assert parsed.samples[2].wp == pytest.approx(2 * 3.7, abs=0.05)


def test_encode_refuses_a_mask_it_cannot_write() -> None:
    """Better to refuse than to write a file whose header lies about its samples."""
    slog = _synthetic(7, n=2)
    slog.header.fields_mask = FIELDS_MASK_ALL | (1 << 14)
    with pytest.raises(SlogError, match="bits this build does not know"):
        encode_slog(slog)


def test_a_profile_name_too_long_for_the_header_is_truncated_not_corrupted() -> None:
    slog = _synthetic(7, n=2)
    slog.header.profile_name = "é" * 40  # 80 bytes, into a 48-byte field
    parsed = parse_slog(encode_slog(slog))
    assert parsed.profile_name == "é" * 23  # 46 bytes plus the NUL


def test_v5_samples_before_the_first_transition_default_to_phase_zero() -> None:
    """The firmware's own reader defaults to phase 0, so we do too.

    A transition table that starts late would otherwise leave the opening
    samples with no phase at all, and the diagnostics would slice the shot
    differently from the way the machine's UI draws it.
    """
    slog = _synthetic(5, n=6)
    slog.header.transitions = [
        PhaseTransition(sample_index=3, phase_number=1, phase_name="Extraction")
    ]
    parsed = parse_slog(encode_slog(slog))
    assert [s.phase for s in parsed.samples] == [0, 0, 0, 1, 1, 1]
    # The samples before the table starts fall back to the reader's own label.
    assert [s.phase_name for s in parsed.samples] == ["Phase 1"] * 3 + ["Extraction"] * 3


def test_v5_with_an_empty_transition_table_is_all_phase_zero() -> None:
    """Named "Phase 1" too, which is what the machine's own screen would show."""
    slog = _synthetic(5, n=4)
    slog.header.transitions = []
    parsed = parse_slog(encode_slog(slog))
    assert [s.phase for s in parsed.samples] == [0, 0, 0, 0]
    assert [s.phase_name for s in parsed.samples] == ["Phase 1"] * 4


def test_pre_v5_samples_have_no_phase_at_all() -> None:
    """There is no transition table before v5; None is the honest answer."""
    parsed = parse_slog(encode_slog(_synthetic(4, n=4)))
    assert [s.phase for s in parsed.samples] == [None] * 4


def test_non_finite_readings_encode_as_zero() -> None:
    """NaN and infinity reach us from JSON exports that did arithmetic on a gap.

    `ShotHistoryPlugin.cpp` writes 0 for both, so matching it keeps a
    re-encoded file identical to what the machine would have produced — and
    stops `struct.pack` raising on a value it cannot represent.
    """
    slog = _synthetic(7, n=4)
    slog.samples[1].cp = float("nan")
    slog.samples[1].fl = float("-inf")
    slog.samples[1].pr = float("inf")
    parsed = parse_slog(encode_slog(slog))
    assert parsed.samples[1].cp == 0.0
    assert parsed.samples[1].fl == 0.0
    assert parsed.samples[1].pr == 0.0

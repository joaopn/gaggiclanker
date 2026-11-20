"""Shot files from firmware 1.10: v8, the legacy hardware-scale v6, and the new `si` bits.

Both fixtures are real shots with the bytes firmware 1.10 changes edited, by
`tests/fixtures/slog_firmware_1_10/generate.py`: never a synthetic trace.
"""

from __future__ import annotations

import struct

import pytest

from gaggiclanker.domain import slog as slog_module
from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog
from gaggiclanker.domain.models import (
    PHASE_EXIT_REASONS,
    SI_ACTIVE_SCALE_CONNECTED,
    SI_BLUETOOTH_SCALE_CONNECTED,
    SystemInfo,
)
from gaggiclanker.domain.slog import (
    MAX_VERSION,
    SlogError,
    UnsupportedSlogVersion,
    encode_slog,
    has_elapsed_timestamp,
    parse_slog,
    sample_size_for,
)
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import EDGE_FIXTURES, FIXTURES, SLOG_FIXTURES, load_export

NEW = FIXTURES / "slog_firmware_1_10"
V8 = NEW / "shot_225_v8.slog"
V7_TWIN = EDGE_FIXTURES / "shot_225_fill_ended_at_start.slog"
LEGACY_V6 = NEW / "shot_196_legacy_v6.slog"
V5_SOURCE = SLOG_FIXTURES / "shot_196_baseline_high.slog"


# ── v8 ──────────────────────────────────────────────────────────────


def test_this_build_reads_up_to_v8_and_no_further() -> None:
    assert MAX_VERSION == 8
    bumped = bytearray(V8.read_bytes())
    bumped[4] = 9
    with pytest.raises(UnsupportedSlogVersion):
        parse_slog(bytes(bumped))


def test_a_v8_shot_parses_equal_to_its_v7_twin_but_for_the_new_scale_bit() -> None:
    eight = parse_slog(V8.read_bytes())
    seven = parse_slog(V7_TWIN.read_bytes())

    assert eight.version == 8 and seven.version == 7
    assert eight.header.model_copy(update={"version": 7}) == seven.header
    assert len(eight.samples) == len(seven.samples) > 100
    for new, old in zip(eight.samples, seven.samples, strict=True):
        assert old.si is not None and new.si is not None
        # Everything but `si` is the same sample, and `si` differs by that one bit only.
        assert new.model_copy(update={"si": old.si}) == old
        assert new.si & ~SI_ACTIVE_SCALE_CONNECTED == old.si


def test_the_fixture_does_carry_the_v8_bit_where_the_scale_bit_is_set() -> None:
    """The precondition of the test above: the new bit is really there to be ignored."""
    samples = parse_slog(V8.read_bytes()).samples
    with_scale = [s for s in samples if (s.si or 0) & SI_BLUETOOTH_SCALE_CONNECTED]
    assert with_scale
    assert all((s.si or 0) & SI_ACTIVE_SCALE_CONNECTED for s in with_scale)
    assert not any((s.si or 0) & SI_ACTIVE_SCALE_CONNECTED for s in samples if s not in with_scale)


def test_the_encoder_writes_v8_exactly_as_it_writes_v7() -> None:
    raw = V8.read_bytes()
    assert encode_slog(parse_slog(raw)) == raw
    assert sample_size_for(8, 0x3FFF) == sample_size_for(7, 0x3FFF) == 30


def test_v8_si_bits_are_information_and_none_of_them_is_unknown() -> None:
    every_bit = 0x01FF
    info = SystemInfo.from_raw(every_bit)
    assert info.process_is_brew and info.target_is_volumetric
    assert info.phase_has_volumetric and info.active_scale_connected
    # Bits that firmware 1.10 does not define stay unread rather than flagged.
    assert SystemInfo.from_raw(0x0200).raw == 0x0200
    # A shot from before them reads the same bits as not recorded.
    older = SystemInfo.from_raw(0x000D)
    assert not older.process_is_brew and not older.active_scale_connected


def test_a_ratio_stop_has_a_name() -> None:
    assert PHASE_EXIT_REASONS[9] == "Ratio target"


# ── legacy hardware-scale v6 ────────────────────────────────────────


def test_a_legacy_v6_shot_parses_and_its_time_is_the_v5_one() -> None:
    legacy = parse_slog(LEGACY_V6.read_bytes())
    source = parse_slog(V5_SOURCE.read_bytes())

    assert legacy.version == 6 and legacy.header.sample_size == 26
    assert legacy.trailing_bytes == 0
    assert legacy.header.model_copy(update={"version": 5}) == source.header
    assert [s.t for s in legacy.samples] == [s.t for s in source.samples]
    assert legacy.samples == source.samples
    assert legacy.samples[1].t == source.header.sample_interval


def test_the_legacy_rule_is_the_firmwares_pair_and_only_that_pair() -> None:
    assert not has_elapsed_timestamp(6, 26, 0x1FFF)
    assert has_elapsed_timestamp(6, 28, 0x1FFF)
    assert has_elapsed_timestamp(6, 0, 0x1FFF)  # a JSON export carries no reserved0
    assert has_elapsed_timestamp(6, 26, 0x3FFF)
    assert has_elapsed_timestamp(7, 26, 0x1FFF)
    assert has_elapsed_timestamp(8, 26, 0x1FFF)
    assert not has_elapsed_timestamp(5, 26, 0x1FFF)
    assert sample_size_for(6, 0x1FFF, device_sample_size=26) == 26
    assert sample_size_for(6, 0x1FFF) == 28


def test_a_legacy_v6_shot_was_refused_before_the_rule_and_is_read_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves: with the rule taken out the file is the quarantined one it used to be."""
    monkeypatch.setattr(
        slog_module, "has_elapsed_timestamp", lambda version, _size, _mask: version >= 6
    )
    with pytest.raises(SlogError, match="26 bytes per sample"):
        parse_slog(LEGACY_V6.read_bytes())
    monkeypatch.undo()
    assert parse_slog(LEGACY_V6.read_bytes()).samples


def test_an_ordinary_v6_shot_still_has_a_four_byte_time() -> None:
    """The same shot written as the upstream v6 (28-byte samples, `t` in milliseconds)."""
    legacy = LEGACY_V6.read_bytes()
    samples = parse_slog(legacy).samples
    ordinary = bytearray(legacy[:512])
    ordinary[5] = 28
    for index, sample in enumerate(samples):
        start = 512 + index * 26
        ordinary += struct.pack("<I", sample.t or 0) + legacy[start + 2 : start + 26]
    parsed = parse_slog(bytes(ordinary))
    assert parsed.header.sample_size == 28
    assert parsed.samples == samples


def test_the_encoder_does_not_write_the_legacy_layout() -> None:
    with pytest.raises(SlogError, match="legacy"):
        encode_slog(parse_slog(LEGACY_V6.read_bytes()))


def test_an_imported_v8_export_re_encodes_to_the_same_shot_as_its_v7_twin() -> None:
    """The importer re-creates raw bytes through the encoder, so v8 must come out as v7 does."""
    document = load_export("shot-v7-synthetic.json")
    as_v7 = encode_slog(shot_export_to_slog(ShotExport.model_validate(document)))
    as_v8 = encode_slog(shot_export_to_slog(ShotExport.model_validate({**document, "version": 8})))

    assert as_v8[4] == 8 and as_v7[4] == 7
    assert as_v8[:4] == as_v7[:4] and as_v8[5:] == as_v7[5:]
    assert parse_slog(as_v8).version == 8


def test_a_v8_shot_is_derived_and_stored_like_any_other() -> None:
    raw = V8.read_bytes()
    derived = derive_shot(parse_slog(raw), raw, device_id="000225")
    assert derived.diagnostics_error is None
    assert derived.shot.slog_version == 8
    assert derived.shot.scale_connected

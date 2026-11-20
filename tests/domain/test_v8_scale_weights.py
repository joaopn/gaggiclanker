"""What a firmware 1.10 shot's weight means: a scale's reading only where `si` says so.

Version 8 logs the *active* weight. With no scale that is the flow estimator's guess and
a scale lost mid-shot hands over to it, so a weight (and the weight flow) is the cup's only
on a sample with `si` bit 0x0100. The cases are the real v8 fixture with that bit edited
(by parse, edit, `encode_slog`: the fixture round-trips byte for byte), never a synthetic
trace. Each "reads as nothing" test is in two halves: the first half switches the rule off
and shows the weights would be read, the second shows they are not.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator, Callable, Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.db.schema import create_schema
from gaggiclanker.domain import metric_language, scale_weights
from gaggiclanker.domain.cup_flow import cup_first_drip_index, shot_has_scale
from gaggiclanker.domain.diagnostics import as_sample_dicts, transform_shot
from gaggiclanker.domain.models import (
    SI_ACTIVE_SCALE_CONNECTED,
    SI_BLUETOOTH_SCALE_CONNECTED,
    SI_EXTENDED_RECORDING,
    IndexEntry,
    Sample,
)
from gaggiclanker.domain.ratio import brew_ratio
from gaggiclanker.domain.slog import Slog, encode_slog, parse_slog
from gaggiclanker.domain.warnings import shot_warnings
from gaggiclanker.shotinfo.catalogue import ITEMS
from gaggiclanker.shotinfo.render import _curve_table, load_shots
from gaggiclanker.sync.derive import DerivedShot, derive_shot
from tests.domain.helpers import SCALE_LOST_AT, V7_TWIN, V8_SHOT, edited_v8

V8 = V8_SHOT
LOST_AT = SCALE_LOST_AT


def _edited(edit: Callable[[int, Sample], int]) -> bytes:
    """The real v8 shot with every sample's `si` set to ``edit(index, sample)``."""
    return edited_v8(lambda index, sample: {"si": edit(index, sample)})


def _without_active_bit(index: int, sample: Sample) -> int:
    return (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED


#: The v8 shot as a machine with no scale writes it: the weights are the estimator's, the
#: active-scale bit is on no sample.
NO_SCALE = _edited(_without_active_bit)
#: The same shot with the scale lost at LOST_AT: the estimator carries on from there.
SCALE_LOST = _edited(
    lambda index, sample: (
        (sample.si or 0) if index < LOST_AT else _without_active_bit(index, sample)
    )
)


@pytest.fixture
def rule_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Read version 8 as the older versions are: every weight is the scale's."""
    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    yield


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "gaggiclanker.db")
    await database.connect()
    await create_schema(database)
    try:
        yield database
    finally:
        await database.close()


def _derive(raw: bytes) -> DerivedShot:
    return derive_shot(parse_slog(raw), raw, device_id="000225")


def _diagnostics(derived: DerivedShot) -> dict[str, Any]:
    assert derived.shot.diagnostics_json is not None
    document: dict[str, Any] = json.loads(derived.shot.diagnostics_json)
    return document


async def _stored(db: Database, raw: bytes, device_id: str = "000225") -> int:
    derived = derive_shot(parse_slog(raw), raw, device_id=device_id)
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


# ── the fixtures are what they say ──────────────────────────────────


def test_the_edited_shots_are_the_real_shot_with_only_si_changed() -> None:
    original = parse_slog(V8.read_bytes())
    for raw in (NO_SCALE, SCALE_LOST):
        edited = parse_slog(raw)
        assert edited.header == original.header
        for new, old in zip(edited.samples, original.samples, strict=True):
            assert new.model_copy(update={"si": old.si}) == old
    # The weights are really there to be misread: the estimator's (here the real scale's)
    # values are on the wire, and the bit is what says whose they are.
    assert max(s.v or 0 for s in parse_slog(NO_SCALE).samples) > 30
    assert not any((s.si or 0) & SI_ACTIVE_SCALE_CONNECTED for s in parse_slog(NO_SCALE).samples)
    assert (parse_slog(NO_SCALE).samples[LOST_AT].si or 0) & SI_BLUETOOTH_SCALE_CONNECTED


# ── a shot with no scale ────────────────────────────────────────────


def test_without_the_bit_a_weight_would_be_read_and_with_the_rule_it_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slog = parse_slog(NO_SCALE)

    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    unruled = slog.recorded_samples()
    assert max(s.v or 0 for s in unruled) > 30
    assert max(s.vf or 0 for s in unruled) > 1
    assert slog.volume_g == pytest.approx(31.9)
    monkeypatch.undo()

    ruled = slog.recorded_samples()
    assert all(s.v is None and s.vf is None for s in ruled)
    assert slog.volume_g is None
    # The estimate is its own channel and is not touched; neither is anything else.
    assert [s.ev for s in ruled] == [s.ev for s in slog.samples]
    assert max(s.ev or 0 for s in ruled) > 30
    assert [s.model_copy(update={"v": None, "vf": None}) for s in slog.samples] == ruled
    # What the machine wrote is not edited: the parsed samples and the bytes keep the estimate.
    assert max(s.v or 0 for s in slog.samples) > 30
    assert encode_slog(slog) == NO_SCALE


def test_a_v8_shot_with_no_scale_is_stored_with_no_scale(rule_off: None) -> None:
    """The first half, on the stored result: the estimate would be stored as a weight."""
    derived = _derive(NO_SCALE)
    assert derived.shot.final_weight_g == pytest.approx(31.9)
    assert max(row.v or 0 for row in derived.samples) > 30
    diagnostics = _diagnostics(derived)
    assert diagnostics["diagnostics"]["weight"]["scale_connected"] is True
    assert diagnostics["summary"]["flow"]["cup_first_drip_s"] is not None
    assert diagnostics["diagnostics"]["extraction"]["cup_flow_avg_brew_g_s"] is not None


def test_a_v8_shot_with_no_scale_has_none_of_what_a_scale_gives() -> None:
    derived = _derive(NO_SCALE)
    assert derived.shot.final_weight_g is None
    assert not derived.shot.scale_connected
    assert all(row.v is None and row.vf is None for row in derived.samples)
    assert not shot_has_scale(row.v for row in derived.samples)
    diagnostics = _diagnostics(derived)
    assert diagnostics["diagnostics"]["weight"]["scale_connected"] is False
    assert diagnostics["summary"]["flow"]["cup_first_drip_s"] is None
    assert diagnostics["diagnostics"]["extraction"]["cup_flow_avg_brew_g_s"] is None
    # The estimated weight survives as its own channel.
    assert max(row.ev or 0 for row in derived.samples) > 30


async def _what_a_person_sees(
    db: Database, raw: bytes, device_id: str = "000225"
) -> dict[str, Any]:
    shot_id = await _stored(db, raw, device_id)
    row = await ShotsRepository(db).get(shot_id)
    assert row is not None
    (facts,) = await load_shots(db, [shot_id], samples=True)
    assert facts.samples is not None
    warnings = shot_warnings(
        final_weight_g=facts.shot.final_weight_g,
        scale_connected=facts.has_scale,
        final_exit_reason=facts.shot.final_exit_reason or 0,
        duration_s=facts.shot.duration_ms / 1000,
        # Half the cup: a shot that read a weight is far over this target.
        target_yield_g=16.0,
        phases=facts.phases,
        metrics=facts.metrics,
    )
    return {
        "list_has_scale": row.has_scale,
        "has_scale": facts.has_scale,
        "final_weight_g": row.final_weight_g,
        "curve_weight_max": max(sample.v or 0 for sample in facts.samples),
        "cup_first_drip_s": facts.summary["flow"]["cup_first_drip_s"],
        "cup_flow_avg": facts.diagnostics["extraction"]["cup_flow_avg_brew_g_s"],
        "target_warnings": sorted(
            w.fault for w in warnings if w.fault in ("over target", "under target")
        ),
    }


async def test_a_scaleless_v8_shot_reads_as_no_scale_everywhere_a_person_sees_it(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    unruled = await _what_a_person_sees(db, NO_SCALE, "000001")
    assert unruled["has_scale"] and unruled["list_has_scale"]
    assert unruled["final_weight_g"] == pytest.approx(31.9)
    assert unruled["curve_weight_max"] > 30
    assert unruled["cup_first_drip_s"] is not None and unruled["cup_flow_avg"] is not None
    assert unruled["target_warnings"] == ["over target"]
    monkeypatch.undo()

    assert await _what_a_person_sees(db, NO_SCALE, "000002") == {
        "list_has_scale": False,
        "has_scale": False,
        "final_weight_g": None,
        "curve_weight_max": 0,
        "cup_first_drip_s": None,
        "cup_flow_avg": None,
        "target_warnings": [],
    }


async def test_the_same_v8_file_with_the_bit_reads_as_its_scale_shot(db: Database) -> None:
    seen = await _what_a_person_sees(db, V8.read_bytes(), "000003")
    assert seen["has_scale"] and seen["list_has_scale"]
    assert seen["final_weight_g"] == pytest.approx(31.9)
    assert seen["curve_weight_max"] > 30
    assert seen["cup_first_drip_s"] is not None and seen["cup_flow_avg"] is not None
    assert seen["target_warnings"] == ["over target"]


def test_a_v8_shot_with_the_bit_is_what_its_v7_twin_is() -> None:
    """The same shot, written as version 7 and as version 8 with the bit: one stored result."""
    seven = _derive(V7_TWIN.read_bytes())
    eight = _derive(V8.read_bytes())
    assert seven.shot.slog_version == 7 and eight.shot.slog_version == 8
    for name in (
        "final_weight_g",
        "scale_connected",
        "phases_json",
        "diagnostics_json",
        "final_exit_reason",
        "sample_count",
    ):
        assert getattr(eight.shot, name) == getattr(seven.shot, name), name
    assert eight.shot.final_weight_g == pytest.approx(31.9)
    assert [row.model_copy(update={"si": None}) for row in eight.samples] == [
        row.model_copy(update={"si": None}) for row in seven.samples
    ]


# ── a scale lost part-way ───────────────────────────────────────────


def test_a_scale_lost_mid_shot_keeps_the_weights_before_and_drops_those_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slog = parse_slog(SCALE_LOST)
    original = parse_slog(V8.read_bytes())
    assert original.samples[LOST_AT].v and original.samples[-1].v

    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    unruled = slog.recorded_samples()
    assert [s.v for s in unruled] == [s.v for s in original.samples]
    monkeypatch.undo()

    ruled = slog.recorded_samples()
    assert [(s.v, s.vf) for s in ruled[:LOST_AT]] == [
        (s.v, s.vf) for s in original.samples[:LOST_AT]
    ]
    assert max(s.v or 0 for s in ruled[:LOST_AT]) > 5
    assert all(s.v is None and s.vf is None for s in ruled[LOST_AT:])
    assert [s.ev for s in ruled] == [s.ev for s in original.samples]

    derived = _derive(SCALE_LOST)
    assert derived.shot.scale_connected
    assert _diagnostics(derived)["diagnostics"]["weight"]["scale_connected"] is True
    stored = {row.t_ms: row.v for row in derived.samples}
    assert stored[original.samples[LOST_AT - 1].t or 0] == original.samples[LOST_AT - 1].v
    assert stored[original.samples[LOST_AT].t or 0] is None


# ── the connection flag the final-weight refill reads ───────────────


def test_scale_connected_follows_bit_0x0100_in_v8_and_not_0x0004(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bit 0x0004 is on every sample of NO_SCALE and 0x0100 on none.
    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    assert scale_weights.scale_connected_flag(8, parse_slog(NO_SCALE).samples)
    monkeypatch.undo()
    assert not scale_weights.scale_connected_flag(8, parse_slog(NO_SCALE).samples)
    assert not _derive(NO_SCALE).shot.scale_connected

    # And the other way: the active scale healthy while the Bluetooth bit is off (a hardware scale).
    hardware = _edited(
        lambda _i, sample: (
            ((sample.si or 0) & ~SI_BLUETOOTH_SCALE_CONNECTED) | SI_ACTIVE_SCALE_CONNECTED
        )
    )
    assert _derive(hardware).shot.scale_connected
    assert _derive(V8.read_bytes()).shot.scale_connected


def test_a_version_7_shot_keeps_the_rule_it_always_had() -> None:
    """The weights of a version 7 shot are read as they are whatever `si` says."""
    slog: Slog = parse_slog(V7_TWIN.read_bytes())
    assert slog.version == 7
    assert slog.recorded_samples() == slog.samples
    assert not any((s.si or 0) & SI_ACTIVE_SCALE_CONNECTED for s in slog.samples)
    assert scale_weights.scale_connected_flag(7, slog.samples)
    assert slog.volume_g == pytest.approx(31.9)


# ── a scale lost before the end ─────────────────────────────────────


def _bit_only_from(first: int) -> bytes:
    """The scale answers only from sample ``first`` on."""
    return _edited(
        lambda index, sample: (
            (sample.si or 0) | SI_ACTIVE_SCALE_CONNECTED
            if index >= first
            else _without_active_bit(index, sample)
        )
    )


#: The first sample of the extended recording after the stop (si bit 0x0010) in the real shot.
EXTENDED_FROM = next(
    index
    for index, sample in enumerate(parse_slog(V8.read_bytes()).samples)
    if (sample.si or 0) & SI_EXTENDED_RECORDING
)
#: The scale answers only in the last ten samples of the brew and in the recording after it (it
#: came up near the end of the brew).
NEAR_END = _bit_only_from(EXTENDED_FROM - 10)
#: The scale answers only in the extended recording after the stop: it never weighed the brew.
TAIL_ONLY = _bit_only_from(EXTENDED_FROM)
#: The scale comes up partway through the brew, at the sample where the coffee is well under way.
SCALE_UP_AT = 40
SCALE_UP = _bit_only_from(SCALE_UP_AT)
#: The scale is gone for the last four samples, which weigh nothing: the cup was lifted and the
#: scale switched off. Every weight before them is the scale's.
SILENT_TAIL = edited_v8(
    lambda index, sample: (
        {"si": _without_active_bit(index, sample), "v": 0.0, "vf": 0.0}
        if index >= len(parse_slog(V8.read_bytes()).samples) - 4
        else {}
    )
)


#: The scale is lost only in the extended recording after the stop (si bit 0x0010), whose weights
#: stay as the machine wrote them: the estimator barely moves there, and the header's weight is
#: the scale's.
TAIL_LOSS = _edited(
    lambda index, sample: (
        _without_active_bit(index, sample)
        if (sample.si or 0) & SI_EXTENDED_RECORDING
        else (sample.si or 0)
    )
)


def test_the_fixture_has_an_extended_recording_in_which_the_scale_still_answers() -> None:
    """The precondition of the tail cases: the real shot has after-stop samples to edit."""
    samples = parse_slog(V8.read_bytes()).samples
    tail = [s for s in samples if (s.si or 0) & SI_EXTENDED_RECORDING]
    assert len(tail) > 5 and samples[EXTENDED_FROM:] == tail
    assert all((s.si or 0) & SI_ACTIVE_SCALE_CONNECTED for s in tail)
    assert all((s.v or 0) > 30 for s in tail)
    # The edited shots really differ from it, in the tail and in `si` only.
    assert TAIL_LOSS != V8.read_bytes() and TAIL_ONLY != V8.read_bytes()
    assert not any(
        (s.si or 0) & SI_ACTIVE_SCALE_CONNECTED
        for s in parse_slog(TAIL_LOSS).samples[EXTENDED_FROM:]
    )
    brew = parse_slog(TAIL_ONLY).samples[:EXTENDED_FROM]
    assert not any((s.si or 0) & SI_ACTIVE_SCALE_CONNECTED for s in brew)


def _lost(raw: bytes) -> bool:
    return scale_weights.scale_lost_before_the_end(8, parse_slog(raw).samples)


def test_a_scale_is_lost_before_the_end_when_the_last_weighed_brew_sample_lacks_the_bit() -> None:
    assert not _lost(V8.read_bytes())  # the bit throughout
    assert not _lost(NO_SCALE)  # nothing ever answered: no scale, not a lost one
    assert _lost(SCALE_LOST)  # cleared from the middle to the end
    assert not _lost(NEAR_END)  # present only near the end
    assert not _lost(SILENT_TAIL)  # gone only where nothing is weighed
    assert not _lost(TAIL_LOSS)  # gone only in the extended recording, pump off
    assert not _lost(TAIL_ONLY)  # never answered during the brew: it had no scale
    # The shots the fixtures are made of weigh in their last samples, so the cases above differ
    # by the bit and not by the weights.
    assert (parse_slog(SCALE_LOST).samples[-1].v or 0) > 30
    assert (parse_slog(SILENT_TAIL).samples[-1].v or 0) == 0
    # Version 7 and below have no such thing.
    assert not scale_weights.scale_lost_before_the_end(7, parse_slog(SCALE_LOST).samples)


def test_a_scale_lost_before_the_end_has_no_final_weight_but_keeps_its_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slog = parse_slog(SCALE_LOST)

    # Without the rule the header's figure, the highest weight including the estimate, is read.
    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    assert slog.volume_g == pytest.approx(31.9)
    monkeypatch.undo()

    assert slog.volume_g is None
    # Nor is the last real reading offered as the yield: it exists, and understates the cup.
    last_real = max(s.v or 0 for s in slog.recorded_samples())
    assert 5 < last_real < 31.9

    derived = _derive(SCALE_LOST)
    assert derived.shot.final_weight_g is None
    assert derived.shot.scale_connected
    assert _diagnostics(derived)["diagnostics"]["weight"]["scale_connected"] is True
    assert max(row.v or 0 for row in derived.samples) == last_real


@pytest.mark.parametrize(
    "raw",
    [V8.read_bytes(), NEAR_END, SILENT_TAIL, TAIL_LOSS],
    ids=["throughout", "near the end", "silent tail", "lost in the tail"],
)
def test_the_header_counts_when_the_scale_answered_to_the_end(raw: bytes) -> None:
    assert parse_slog(raw).volume_g == pytest.approx(31.9)
    assert _derive(raw).shot.final_weight_g == pytest.approx(31.9)


# ── the machine's index volume is the estimate too ──────────────────


def _entry(volume_g: float | None) -> IndexEntry:
    header = parse_slog(V8.read_bytes()).header
    return IndexEntry(
        id=225,
        timestamp=header.start_epoch,
        duration_ms=header.duration_ms,
        volume_g=volume_g,
        flags=0x01,
        profile_id=header.profile_id,
        profile_name=header.profile_name,
    )


def _index_volume(raw: bytes, volume_g: float | None) -> float | None:
    slog = parse_slog(raw)
    return derive_shot(slog, raw, device_id="000225", entry=_entry(volume_g)).shot.index_volume_g


def test_the_index_volume_of_a_shot_with_no_scale_is_the_estimate_and_is_not_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header_g = parse_slog(NO_SCALE).header.final_weight_g
    assert header_g == pytest.approx(31.9)

    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    assert _index_volume(NO_SCALE, header_g) == pytest.approx(31.9)
    monkeypatch.undo()

    assert _index_volume(NO_SCALE, header_g) is None
    assert _index_volume(SCALE_LOST, header_g) is None


def test_a_dose_out_typed_on_the_machine_is_kept() -> None:
    """The firmware writes it into the index volume over the header's weight."""
    assert _index_volume(NO_SCALE, 36.5) == pytest.approx(36.5)
    assert _index_volume(SCALE_LOST, 36.5) == pytest.approx(36.5)
    # A figure a tenth of a gram from the header's is another figure.
    assert _index_volume(NO_SCALE, 31.8) == pytest.approx(31.8)


def test_the_index_volume_is_compared_as_the_firmwares_tenths_of_a_gram() -> None:
    assert _index_volume(NO_SCALE, 31.9 + 1e-9) is None
    assert _index_volume(NO_SCALE, 31.9 - 1e-9) is None


def test_the_index_volume_is_kept_when_the_header_weight_is_a_scales_and_for_older_shots() -> None:
    for raw in (V8.read_bytes(), NEAR_END, SILENT_TAIL, TAIL_LOSS, V7_TWIN.read_bytes()):
        assert _index_volume(raw, 31.9) == pytest.approx(31.9)
    # No index volume stays none, and a shot imported from a file has no index entry at all.
    assert _index_volume(NO_SCALE, None) is None
    imported = derive_shot(parse_slog(NO_SCALE), NO_SCALE, device_id="000225")
    assert imported.shot.index_volume_g is None


async def test_the_yield_and_the_ratio_of_a_scaleless_shot_do_not_show_the_estimate(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def seen(device_id: str) -> tuple[float | None, float | None]:
        derived = derive_shot(
            parse_slog(NO_SCALE),
            NO_SCALE,
            device_id=device_id,
            entry=_entry(31.9),
        )
        shot_id = await ShotsRepository(db).insert(derived.shot, derived.samples)
        row = await ShotsRepository(db).get(shot_id)
        assert row is not None
        ratio = brew_ratio(
            judged_dose_g=None, version_dose_g=18.0, judged_yield_g=None, scale_yield_g=row.volume_g
        )
        return row.volume_g, ratio

    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    assert await seen("000001") == (31.9, 1.77)
    monkeypatch.undo()
    assert await seen("000002") == (None, None)


def test_a_header_that_cannot_be_judged_leaves_no_index_volume() -> None:
    """No scale and no header weight (an incomplete copy, or a header of zero): nothing to compare.

    The machine's figure cannot be told from a typed one, so it is absent; a typed yield reaches
    the app through the notes' dose out and the judgement. With a header weight it is compared.
    """
    slog = parse_slog(NO_SCALE)
    assert slog.header.final_weight_g is not None
    blank = dataclasses.replace(
        slog, header=slog.header.model_copy(update={"final_weight_g": None})
    )
    assert not blank.header_weight.counts and blank.header_weight.final_weight_g is None
    assert blank.header_weight.index_volume(36.5) is None
    assert blank.header_weight.index_volume(None) is None
    assert slog.header_weight.index_volume(36.5) == pytest.approx(36.5)


# ── a weight that is not a scale's is absent, never zero ────────────


def _zeros(version: int, samples: list[Sample]) -> list[Sample]:
    """The earlier reading of the rule: the estimate replaced by a zero (a reading)."""
    return [
        sample
        if scale_weights.scale_reading(version, sample.si)
        else sample.model_copy(update={"v": 0.0, "vf": 0.0})
        for sample in samples
    ]


def _brew_cup_flow_mean(samples: list[Sample]) -> float:
    flows = [max(s.vf, 0.0) for s in samples if s.vf is not None]
    return round(sum(flows) / len(flows), 2)


def _decline(derived: DerivedShot) -> dict[str, Any]:
    assert derived.shot.phases_json is not None
    phases = json.loads(derived.shot.phases_json)
    metrics: dict[str, Any] = next(p for p in phases if p["phase_number"] == 2)["metrics"]
    return metrics


def test_a_scale_lost_mid_shot_leaves_no_zero_cup_after_the_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = parse_slog(V8.read_bytes()).samples
    last_real = original[LOST_AT - 1].v
    assert last_real and last_real > 10

    # The first half: read as zeros, the phase that holds the loss ends at a cup of 0 g.
    monkeypatch.setattr(
        "gaggiclanker.domain.slog.recorded_samples",
        lambda version, samples: _zeros(version, samples),
    )
    zeroed = _decline(_derive(SCALE_LOST))
    assert zeroed["cup_weight_end_g"] == 0.0 and zeroed["cup_weight_gained_g"] == 0.0
    monkeypatch.undo()

    metrics = _decline(_derive(SCALE_LOST))
    # The last weight the scale gave, not a vanished cup, and nothing gained is below zero.
    assert metrics["cup_weight_end_g"] == pytest.approx(last_real)
    assert metrics["cup_weight_gained_g"] == pytest.approx(last_real)


def test_the_cup_flow_of_a_scale_lost_mid_shot_is_over_the_samples_before_the_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = parse_slog(V8.read_bytes())
    before = original.samples[:LOST_AT]
    expected = _brew_cup_flow_mean(before)
    # Over every sample with zeros after the loss the mean is much lower: that is what the
    # estimate's absence must not be read as.
    zeroed = _brew_cup_flow_mean(_zeros(8, parse_slog(SCALE_LOST).samples))
    assert zeroed < expected - 0.2

    derived = _derive(SCALE_LOST)
    reading = _diagnostics(derived)["diagnostics"]["extraction"]["cup_flow_avg_brew_g_s"]
    # The brew is every sample here (no phase is a pre-infusion by name).
    assert reading == expected


def test_a_one_sample_blip_in_the_scale_is_a_gap_not_a_dip_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blip = 80
    raw = _edited(
        lambda index, sample: (
            _without_active_bit(index, sample) if index == blip else (sample.si or 0)
        )
    )
    original = parse_slog(V8.read_bytes())
    assert (original.samples[blip].v or 0) > 10

    def rate_mean(weights: list[float | None]) -> float:
        dt = original.sample_interval / 1000.0
        rates = [
            (cur - prev) / dt
            for prev, cur in pairwise(weights)
            if prev is not None and cur is not None and cur - prev >= 0
        ]
        return round(sum(rates) / len(rates), 2)

    weights = [s.v for s in original.samples]
    weights[blip] = None
    expected = rate_mean(weights)

    def measured() -> tuple[float | None, float | None]:
        derived = _derive(raw)
        row = next(r for r in derived.samples if r.t_ms == original.samples[blip].t)
        return row.v, _diagnostics(derived)["diagnostics"]["weight"]["rate_avg_g_s"]

    # The first half: a zero in the blip's place is a dip, and the climb back out of it is a
    # step of 60 g/s that lifts the average rate.
    monkeypatch.setattr(
        "gaggiclanker.domain.slog.recorded_samples",
        lambda version, samples: _zeros(version, samples),
    )
    dip_value, dip_rate = measured()
    assert dip_value == 0.0 and dip_rate is not None and dip_rate > expected + 0.2
    monkeypatch.undo()

    value, rate = measured()
    assert value is None
    assert rate == expected


def test_the_detailed_diagnostics_points_average_the_weights_recorded_around_a_blip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `per_phase_detailed` point is the mean of itself and its neighbours; a missing weight is
    not one of the three. Sample 76 is the middle point of the second phase's five. No production
    caller asks for this level (the chat's curve is `shotinfo/render.py`, tested below); the type
    is public, so it is kept right."""
    blip = 17 + 59
    raw = _edited(
        lambda index, sample: (
            _without_active_bit(index, sample) if index == blip else (sample.si or 0)
        )
    )
    slog = parse_slog(raw)
    original = parse_slog(V8.read_bytes()).samples
    neighbours = [original[blip - 1].v or 0.0, original[blip + 1].v or 0.0]
    expected = round(sum(neighbours) / 2, 1)

    def middle_point() -> float:
        shot = transform_shot(slog, "per_phase_detailed")
        points = shot["phases"][1]["samples"]
        assert points[2]["time_seconds"] == round((original[blip].t or 0) / 1000.0, 1)
        return float(points[2]["weight_g"])

    # The first half: with the weight read as zero the point dips to a third below its
    # neighbours.
    monkeypatch.setattr("gaggiclanker.domain.slog.recorded_samples", _zeros)
    assert middle_point() == round((sum(neighbours)) / 3, 1) < expected - 3
    monkeypatch.undo()

    assert middle_point() == expected


def test_the_detailed_diagnostics_points_of_a_scale_lost_mid_shot_have_no_weight_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`per_phase_detailed` points (about five per phase) carry a weight only where one was
    recorded."""
    slog = parse_slog(SCALE_LOST)

    def points() -> list[dict[str, Any]]:
        shot = transform_shot(slog, "per_phase_detailed")
        return [dict(point) for point in shot["phases"][1]["samples"]]

    # The first half: with the weights read as zeros the points after the loss say 0.0 g.
    monkeypatch.setattr("gaggiclanker.domain.slog.recorded_samples", _zeros)
    zeroed = points()
    assert [p["weight_g"] for p in zeroed][-3:] == [0.0, 0.0, 0.0]
    monkeypatch.undo()

    after = points()
    # The phase's points are at samples 17, 47, 76, 105 and 135; the scale is lost at 70.
    assert ["weight_g" in p for p in after] == [True, True, False, False, False]
    assert all(p["time_seconds"] > 0 for p in after)
    assert after[1]["weight_g"] > 5


# ── a scale that answers only after the stop ────────────────────────


def test_a_scale_that_comes_up_only_after_the_stop_did_not_weigh_the_brew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slog = parse_slog(TAIL_ONLY)
    tail_bits = [(s.si or 0) & SI_ACTIVE_SCALE_CONNECTED for s in slog.samples[EXTENDED_FROM:]]
    assert tail_bits and all(tail_bits)

    # The first half: judging every sample, the tail's bit makes it a scale shot, and the header
    # (the estimator's peak over the brew) a final weight.
    monkeypatch.setattr(scale_weights, "_brew_samples", lambda samples: list(samples))
    assert slog.volume_g == pytest.approx(31.9)
    derived = _derive(TAIL_ONLY)
    assert derived.shot.final_weight_g == pytest.approx(31.9)
    assert derived.shot.scale_connected
    assert _diagnostics(derived)["diagnostics"]["weight"]["scale_connected"] is True
    monkeypatch.undo()

    assert slog.volume_g is None
    derived = _derive(TAIL_ONLY)
    assert derived.shot.final_weight_g is None and not derived.shot.scale_connected
    assert _diagnostics(derived)["diagnostics"]["weight"]["scale_connected"] is False
    # Not even the tail's own readings are kept: the shot has no weights.
    assert all(row.v is None and row.vf is None for row in derived.samples)
    assert _index_volume(TAIL_ONLY, 31.9) is None
    assert _index_volume(TAIL_ONLY, 36.5) == pytest.approx(36.5)


# ── a scale that comes up partway through the brew ──────────────────


def _old_cup_first_drip_index(samples: Any) -> int | None:
    """The rule before a late scale had no baseline: the first recorded weight is the baseline."""
    first = None
    for index, sample in enumerate(samples):
        weight = sample.get("v")
        if weight is None:
            continue
        if first is None:
            first = float(weight)
        if weight >= first + 0.5:
            return index
    return None


def test_a_scale_that_comes_up_part_way_has_no_cup_first_drip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def drip(raw: bytes) -> float | None:
        derived = _derive(raw)
        flow = _diagnostics(derived)["summary"]["flow"]
        value: float | None = flow["cup_first_drip_s"]
        return value

    # A control: weighed from the start, the first drip is there and is read from the first
    # reading, which is the baseline.
    assert drip(V8.read_bytes()) is not None

    # The first half: a baseline taken from the first reading the scale gave, at sample 40, is
    # taken for the cup's empty weight and gives a drip that is made up.
    monkeypatch.setattr(
        "gaggiclanker.domain.diagnostics.cup_first_drip_index", _old_cup_first_drip_index
    )
    monkeypatch.setattr(
        "gaggiclanker.domain.phase_metrics.cup_first_drip_index", _old_cup_first_drip_index
    )
    assert drip(SCALE_UP) is not None
    monkeypatch.undo()

    assert drip(SCALE_UP) is None
    metrics = _decline(_derive(SCALE_UP))
    assert "cup_first_drip_s" not in metrics


def test_a_phase_cup_gain_is_absent_when_its_start_has_no_recorded_weight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def old_gained(data: Any, channel: str, position: int) -> float:
        here = metric_language._at_end_of_phase(data, channel, position)
        if here is None:
            raise metric_language._Absent("empty_window", "no sample was recorded in the phase")
        before = 0.0
        for earlier in range(position - 1, -1, -1):
            found = metric_language._at_end_of_phase(data, channel, earlier)
            if found is not None:
                before = found
                break
        return here - before

    # A control: weighed from the start, the phase's gain is its end weight.
    control = _decline(_derive(V8.read_bytes()))
    assert control["cup_weight_gained_g"] == pytest.approx(31.9, abs=0.5)

    # The first half: counted from zero, the phase that the scale came up in gains what the cup
    # held when the scale came up as well.
    monkeypatch.setattr(metric_language, "_gained", old_gained)
    counted = _decline(_derive(SCALE_UP))
    assert counted["cup_weight_gained_g"] == pytest.approx(31.9, abs=0.5)
    monkeypatch.undo()

    metrics = _decline(_derive(SCALE_UP))
    assert "cup_weight_gained_g" not in metrics
    # What was recorded is still said: the cup at the end of the phase is a reading.
    assert metrics["cup_weight_end_g"] == pytest.approx(31.9, abs=0.5)


async def test_the_chat_curve_of_a_scale_lost_mid_shot_has_no_weight_after_the_loss(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the chat reads is `shotinfo/render.py`'s curve table, built from the stored rows."""
    original = parse_slog(V8.read_bytes()).samples
    lost_from = (original[LOST_AT].t or 0) / 1000

    async def weight_cells(device_id: str) -> list[tuple[float, str]]:
        shot_id = await _stored(db, SCALE_LOST, device_id)
        (facts,) = await load_shots(db, [shot_id], samples=True)
        table = _curve_table(facts, [ITEMS["curve_weight"]], 500)
        header, *rows = table[1:]
        assert header == "t (s),weight (g)"
        return [(float(row.split(",")[0]), row.split(",")[1]) for row in rows]

    # The first half: stored as zeros, the weight column says 0.0 g for every row after the loss.
    monkeypatch.setattr("gaggiclanker.domain.slog.recorded_samples", _zeros)
    zeroed = await weight_cells("000001")
    assert [cell for t, cell in zeroed if t >= lost_from] and all(
        cell == "0.0" for t, cell in zeroed if t >= lost_from
    )
    monkeypatch.undo()

    cells = await weight_cells("000002")
    assert all(cell == "" for t, cell in cells if t >= lost_from)
    assert any(float(cell) > 5 for t, cell in cells if t < lost_from and cell)


# ── the baseline of a cup that was still empty ──────────────────────

#: Only the very first sample lacks the bit (the scale answered a moment after the recording began).
FIRST_SAMPLE_ONLY = _edited(
    lambda index, sample: _without_active_bit(index, sample) if index == 0 else (sample.si or 0)
)


def _earlier_cup_first_drip_index(samples: Any) -> int | None:
    """The earlier rule: a first weight that is not the first sample's is no baseline."""
    first = None
    for index, sample in enumerate(samples):
        weight = sample.get("v")
        if weight is None:
            continue
        if first is None:
            if index > 0:
                return None
            first = float(weight)
        if weight >= first + 0.5:
            return index
    return None


def test_a_first_sample_alone_without_the_bit_keeps_the_drip_and_the_gain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _derive(V8.read_bytes())
    control_flow = _diagnostics(control)["summary"]["flow"]
    assert control_flow["cup_first_drip_s"] is not None

    def seen(raw: bytes) -> tuple[float | None, float | None]:
        derived = _derive(raw)
        drip = _diagnostics(derived)["summary"]["flow"]["cup_first_drip_s"]
        first_phase = json.loads(derived.shot.phases_json or "[]")[0]["metrics"]
        return drip, first_phase.get("cup_weight_gained_g")

    # The first half: a baseline that must be the first sample's loses the drip and the first
    # phase's gain.
    for module in ("diagnostics", "phase_metrics"):
        monkeypatch.setattr(
            f"gaggiclanker.domain.{module}.cup_first_drip_index", _earlier_cup_first_drip_index
        )
    monkeypatch.setattr(
        metric_language,
        "_cup_started_empty",
        lambda data: metric_language._value(data, "cup_weight", 0) is not None,
    )
    assert seen(FIRST_SAMPLE_ONLY) == (None, None)
    monkeypatch.undo()

    drip, gain = seen(FIRST_SAMPLE_ONLY)
    assert drip is not None and gain is not None
    assert (drip, gain) == seen(V8.read_bytes())


def test_the_first_reading_is_a_baseline_only_while_the_cup_is_under_half_a_gram() -> None:
    """The edge of the rule, on the real shot's samples with the first weights taken away."""
    base = as_sample_dicts(parse_slog(V8.read_bytes()))
    assert base[0]["v"] == 0.0

    def drip_with_first_reading(weight: float) -> int | None:
        samples = [dict(sample) for sample in base]
        del samples[0]["v"]
        samples[1]["v"] = weight
        return cup_first_drip_index(samples)

    assert drip_with_first_reading(0.4) is not None
    assert drip_with_first_reading(0.5) is None
    assert drip_with_first_reading(12.0) is None


def test_one_filter_decides_which_samples_a_scale_answered_in() -> None:
    """The only bit is on a mid-brew sample that shares its moment with the next one, and the
    last of two at one moment is the sample the shot keeps: no kept sample has the bit."""
    at = 60
    original = parse_slog(V8.read_bytes()).samples
    raw = edited_v8(
        lambda index, sample: (
            {"si": (sample.si or 0) | SI_ACTIVE_SCALE_CONNECTED}
            if index == at
            else {"si": _without_active_bit(index, sample), "t": original[at].t}
            if index == at + 1
            else {"si": _without_active_bit(index, sample)}
        )
    )
    slog = parse_slog(raw)
    assert (slog.samples[at].si or 0) & SI_ACTIVE_SCALE_CONNECTED
    assert slog.samples[at].t == slog.samples[at + 1].t

    kept = scale_weights.kept_samples(slog.samples)
    assert len(kept) == len(slog.samples) - 1
    assert not any((s.si or 0) & SI_ACTIVE_SCALE_CONNECTED for s in kept)
    # Not answered, so not "lost" either, and no sample of the shot carries a weight.
    assert not scale_weights.scale_answered(8, kept)
    assert not scale_weights.scale_lost_before_the_end(8, slog.samples)
    assert all(s.v is None and s.vf is None for s in slog.recorded_samples())
    assert slog.volume_g is None

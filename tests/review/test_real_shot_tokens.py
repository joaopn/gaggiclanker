"""A review of a synced or imported shot selects its rules by the shot's own warnings and readings.

A shot is read for what is plainly wrong with it whatever its profile is for: the
warnings (over target, under target, skipped, fast flow), written as ``fault:``
tokens, and a handful of plain readings of its numbers (``first_drip:fast``,
``avg_flow:high``, ``temp:cold``, ``scale:absent``, ``yield:tiny``). There is no
band in any of them. These tests derive real fixture recordings, and the
constructed lever shot, the way ingest does and read the review's input.

The two yield warnings need the target of the version a shot is filed under: a shot that is
not filed has none of them among its tokens, and one that is filed under a Set version with a
target gets them, from the same function.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.sets import SetVersionRow
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.domain.warnings import FAULTS, fault_token
from gaggiclanker.knowledge.rules import load_seed_rules
from gaggiclanker.review.context import build_review_input, readable_summary, signal_tokens
from gaggiclanker.review.style import StyleVerdict, detect_style
from gaggiclanker.shotinfo import ShotFacts, load_shots
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import constructed_profile_for, standard_board
from tests.lever_shot import LEVER_PROFILE, lever_shot, without_scale

SLOGS = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "slog").glob("*.slog"))
UNKNOWN = StyleVerdict(style="unknown", tier="none", evidence=[])

#: Every shape of token a seed rule may key on, and what each one can be.
FAULT_TOKENS = {f"fault:{fault_token(fault)}" for fault in FAULTS}
KNOWN_PREFIXES = (
    "style:",
    "taste:",
    "aroma:",
    "balance:",
    "fault:",
    "first_drip:",
    "avg_flow:",
    "temp:",
    "scale:",
    "yield:",
)
KNOWN_TOKENS = {
    "temp:cold",
    "temp:hot",
    "scale:absent",
    "yield:tiny",
    "taste:sour_and_bitter",
}


def _rule_tokens() -> set[str]:
    """Every token some seed rule keys on."""
    return {token for rule in load_seed_rules() for token in rule.applies.get("signal") or []}


async def _ingest(
    db: Database, path: Path, *, variant: str | None = "pressure-first", **kwargs: object
) -> int:
    raw = path.read_bytes()
    derived = derive_shot(
        parse_slog(raw),
        raw,
        device_id=path.stem,
        profile=None if variant is None else constructed_profile_for(path, variant),
        **kwargs,  # type: ignore[arg-type]
    )
    assert derived.diagnostics_error is None
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


async def _ingest_lever(db: Database, slog: object = None) -> int:
    shot = slog if slog is not None else lever_shot()
    derived = derive_shot(
        shot,  # type: ignore[arg-type]
        slog_to_raw(shot),  # type: ignore[arg-type]
        device_id="000900",
        source="import",
        profile=LEVER_PROFILE,
    )
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


def test_every_token_a_seed_rule_keys_on_is_one_a_shot_can_produce() -> None:
    """No rule is left keyed on a token nothing makes (a band, an indicator)."""
    for token in sorted(_rule_tokens()):
        assert token.startswith(KNOWN_PREFIXES), token
        if token.startswith("fault:"):
            assert token in FAULT_TOKENS, token
        if token.startswith(("temp:", "scale:", "yield:")):
            assert token in KNOWN_TOKENS, token


def test_the_rules_keyed_on_a_fault_are_the_ones_listed() -> None:
    keyed = {
        (rule.category, rule.key): sorted(
            t for t in rule.applies.get("signal") or [] if t.startswith("fault:")
        )
        for rule in load_seed_rules()
    }
    assert {key: tokens for key, tokens in keyed.items() if tokens} == {
        ("profile_design_defaults", "preinfusion"): ["fault:fast_flow"],
    }


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
async def test_a_derived_real_shot_produces_readings_and_no_band(
    seeded: Database, path: Path
) -> None:
    shot_id = await _ingest(seeded, path)
    (facts,) = await load_shots(seeded, [shot_id])
    assert facts.full, "ingest stores the full diagnostics"

    listed = signal_tokens(facts, UNKNOWN)

    assert listed == sorted(set(listed))
    assert "style:unknown" in listed
    assert all(t.startswith(KNOWN_PREFIXES) for t in listed), listed
    assert not [t for t in listed if t.split(":")[0].isupper() or t.split(":")[1].isupper()]


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
async def test_a_real_shot_with_no_scale_flow_over_three_has_no_fault(
    seeded: Database, path: Path
) -> None:
    """These fixtures' scale flow never passes 3 g/s, and they are filed under no version."""
    shot_id = await _ingest(seeded, path)
    review = await build_review_input(seeded, shot_id)
    assert not [t for t in review.signals if t.startswith("fault:")]


async def test_the_lever_shot_s_review_reads_its_fast_flow_and_its_skipped_phase(
    seeded: Database,
) -> None:
    shot_id = await _ingest_lever(seeded)
    review = await build_review_input(seeded, shot_id)

    faults = [t for t in review.signals if t.startswith("fault:")]
    assert faults == ["fault:fast_flow", "fault:skipped"]
    assert review.signals == sorted(review.signals)


async def test_the_yield_faults_need_the_version_the_shot_is_filed_under(
    seeded: Database,
) -> None:
    shot_id = await _ingest_lever(seeded)
    (facts,) = await load_shots(seeded, [shot_id])
    assert "fault:over_target" not in signal_tokens(facts, UNKNOWN)
    unfiled = await build_review_input(seeded, shot_id)
    assert "fault:over_target" not in unfiled.signals

    # A shot filed under a Set version (a 36 g target) gets it, from the same function.
    version = SetVersionRow.model_construct(target_yield_g=36.0)
    filed = dataclasses.replace(facts, version=version)
    assert "fault:over_target" in signal_tokens(filed, UNKNOWN)
    assert "fault:under_target" not in signal_tokens(filed, UNKNOWN)


async def test_the_re_keyed_pre_infusion_rule_is_selected_by_fast_flow(seeded: Database) -> None:
    shot_id = await _ingest_lever(seeded)
    review = await build_review_input(seeded, shot_id)

    assert "preinfusion" in review.rule_keys
    rule = next(r for r in review.rules if r["key"] == "preinfusion")
    assert rule["category"] == "profile_design_defaults"


async def test_a_shot_that_never_goes_fast_does_not_select_it_by_fault(seeded: Database) -> None:
    shot_id = await _ingest(seeded, SLOGS[0])
    review = await build_review_input(seeded, shot_id)
    selecting = set(review.signals) & {"fault:fast_flow", "first_drip:fast"}
    assert ("preinfusion" in review.rule_keys) == bool(selecting)


async def test_a_shot_with_no_scale_reads_no_yield_or_fast_flow_and_says_so(
    seeded: Database,
) -> None:
    shot_id = await _ingest_lever(seeded, without_scale(lever_shot()))
    review = await build_review_input(seeded, shot_id)

    assert "scale:absent" in review.signals
    assert "fault:fast_flow" not in review.signals
    assert "fault:skipped" in review.signals


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
async def test_a_standard_board_shot_has_no_fault_and_no_pressure_reading(
    seeded: Database, path: Path
) -> None:
    slog = standard_board(parse_slog(path.read_bytes()))
    derived = derive_shot(slog, path.read_bytes(), device_id=path.stem)
    shot_id = await ShotsRepository(seeded).insert(derived.shot, derived.samples)
    (facts,) = await load_shots(seeded, [shot_id])

    tokens = signal_tokens(facts, UNKNOWN)

    assert not [t for t in tokens if t.startswith("fault:")]
    assert all(t.startswith(KNOWN_PREFIXES) for t in tokens)


#: A summary as a shot derived before the pressure gate (or written by hand) holds it: puck
#: flow and pressure numbers on a board that measured neither.
_FLOWING_SUMMARY = {
    "flow": {"avg_flow_ml_s": 4.2, "peak_flow_ml_s": 5.0, "time_to_first_drip_s": 1.2},
    "pressure": {"max_bar": 9.1, "min_bar": 0.0, "avg_bar": 6.0, "peak_time_s": 9.0},
    "temperature": {"avg_c": 93.0, "target_avg_c": 93.0},
}


async def _facts_with_summary(
    seeded: Database, *, has_pressure: bool, shot_id: int | None = None
) -> ShotFacts:
    shot_id = shot_id if shot_id is not None else await _ingest_lever(seeded)
    (facts,) = await load_shots(seeded, [shot_id])
    blob = {"summary": _FLOWING_SUMMARY, "has_pressure": has_pressure}
    return dataclasses.replace(
        facts,
        shot=facts.shot.model_copy(
            update={"diagnostics": blob, "profile_name_on_device": "", "phases": []}
        ),
    )


async def test_rule_tokens_do_not_read_puck_flow_without_a_pressure_sensor(
    seeded: Database,
) -> None:
    """Puck flow is a model of the pump on a board with a sensor and zeros on one without."""
    shot_id = await _ingest_lever(seeded)
    sensor = await _facts_with_summary(seeded, has_pressure=True, shot_id=shot_id)
    assert {"avg_flow:high", "first_drip:fast"} <= set(signal_tokens(sensor, UNKNOWN))

    blind = await _facts_with_summary(seeded, has_pressure=False, shot_id=shot_id)
    without = signal_tokens(blind, UNKNOWN)
    assert not [t for t in without if t.startswith(("avg_flow:", "first_drip:"))]


async def test_style_detection_does_not_call_a_shot_turbo_from_puck_flow_it_never_had(
    seeded: Database,
) -> None:
    shot_id = await _ingest_lever(seeded)
    sensor = await _facts_with_summary(seeded, has_pressure=True, shot_id=shot_id)
    flowing = detect_style(None, summary=readable_summary(sensor), duration_s=16.0)
    assert flowing.style == "turbo", "the telemetry tier calls a fast shot turbo"

    blind = await _facts_with_summary(seeded, has_pressure=False, shot_id=shot_id)
    verdict = detect_style(None, summary=readable_summary(blind), duration_s=16.0)
    assert verdict.style == "unknown"
    assert "flow" not in readable_summary(blind)
    assert "pressure" not in readable_summary(blind)

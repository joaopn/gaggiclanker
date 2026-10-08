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
import json
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.sets import SetVersionRow
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.domain.warnings import FAULTS, fault_token
from gaggiclanker.knowledge.rules import load_seed_rules
from gaggiclanker.knowledge.service import FAULT_QUERIES
from gaggiclanker.review.context import build_review_input, readable_summary, signal_tokens
from gaggiclanker.review.style import StyleVerdict, detect_style
from gaggiclanker.shotinfo import ShotFacts, load_shots
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import constructed_profile_for, fill_ended_shot, standard_board
from tests.lever_shot import LEVER_PROFILE, lever_shot, without_scale

SLOGS = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "slog").glob("*.slog"))
UNKNOWN = StyleVerdict(style="unknown", tier="none", evidence=[])

#: Every shape of token a seed rule may key on, and what each one can be.
FAULT_TOKENS = {f"fault:{fault_token(fault)}" for fault in FAULTS} | {"fault:skipped_at_start"}
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
        ("profile_design_defaults", "pressure_exit_on_a_fill"): ["fault:skipped_at_start"],
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
    # No scale, so the first drip read is the puck flow's (with one it is the cup's).
    blob = {
        "summary": _FLOWING_SUMMARY,
        "diagnostics": {"scale_connected": False},
        "has_pressure": has_pressure,
    }
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


async def _ingest_slog(db: Database, slog: object, device_id: str) -> int:
    derived = derive_shot(
        slog,  # type: ignore[arg-type]
        slog_to_raw(slog),  # type: ignore[arg-type]
        device_id=device_id,
    )
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


async def test_shot_129_is_a_slow_first_drip_by_the_cup_the_model_is_shown(
    seeded: Database,
) -> None:
    """Puck flow starts at 6.8 s (neither fast nor slow); the cup is first filled at 19.25 s."""
    from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog
    from tests.shotinfo.conftest import SHOT_129

    slog = shot_export_to_slog(ShotExport.model_validate(json.loads(SHOT_129.read_text())))
    shot_id = await _ingest_slog(seeded, slog, "000129")
    (facts,) = await load_shots(seeded, [shot_id])

    assert facts.summary_value("flow", "time_to_first_drip_s") == 6.8
    assert facts.summary_value("flow", "cup_first_drip_s") == 19.25
    tokens = signal_tokens(facts, UNKNOWN)
    assert "first_drip:slow" in tokens
    assert "first_drip:fast" not in tokens


async def test_shot_196_reads_its_first_drip_from_the_cup_with_a_scale_and_the_puck_without(
    seeded: Database,
) -> None:
    slog = parse_slog((SLOGS[0]).read_bytes())
    assert SLOGS[0].stem.startswith("shot_196")
    with_scale = await _ingest_slog(seeded, slog, "000196")
    # Weights and cup flow zeroed, the connection flag left set.
    bare = dataclasses.replace(
        slog, samples=[s.model_copy(update={"v": 0.0, "vf": 0.0}) for s in slog.samples]
    )
    without = await _ingest_slog(seeded, bare, "000197")
    (scale_facts, bare_facts) = await load_shots(seeded, [with_scale, without])

    assert "first_drip:slow" in signal_tokens(scale_facts, UNKNOWN)  # the cup's 12.0 s
    assert "scale:absent" not in signal_tokens(scale_facts, UNKNOWN)
    # Without a scale it is the puck estimate (16.25 s) and the shot is said to have no scale,
    # though the firmware's connection flag was set.
    assert bare_facts.shot.scale_connected is True
    tokens = signal_tokens(bare_facts, UNKNOWN)
    assert "scale:absent" in tokens
    assert "first_drip:slow" in tokens


async def test_a_scale_shot_whose_connection_flag_is_cleared_is_not_without_a_scale(
    seeded: Database,
) -> None:
    slog = parse_slog(SLOGS[0].read_bytes())
    cleared = dataclasses.replace(
        slog, samples=[s.model_copy(update={"si": (s.si or 0) & ~0x0004}) for s in slog.samples]
    )
    shot_id = await _ingest_slog(seeded, cleared, "000198")
    (facts,) = await load_shots(seeded, [shot_id])

    assert facts.shot.scale_connected is False
    assert "scale:absent" not in signal_tokens(facts, UNKNOWN)


async def _ingest_fill_ended(db: Database) -> int:
    slog, _, profile = fill_ended_shot()
    derived = derive_shot(slog, slog_to_raw(slog), device_id="000225", profile=profile)
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


async def test_a_fill_that_ended_before_its_first_sample_has_a_signal_of_its_own(
    seeded: Database,
) -> None:
    """On the real shot's derived facts: the stop-early token is not the one it carries."""
    review = await build_review_input(seeded, await _ingest_fill_ended(seeded))

    faults = [t for t in review.signals if t.startswith("fault:")]
    assert faults == ["fault:skipped_at_start"]
    # The rule written for it is selected, and says what to do about the exit.
    rule = next(r for r in review.rules if r["key"] == "pressure_exit_on_a_fill")
    assert rule["category"] == "profile_design_defaults"
    assert rule["confidence"] == "anecdotal"
    assert rule["source"] == "observed on a GaggiMate"
    assert "reached" in rule["text"] and "at or above the exit pressure" in rule["text"]


async def test_its_excerpt_search_is_its_own_and_finds_the_stop_conditions(
    seeded: Database,
) -> None:
    """Not the volumetric-stop query, which would hand the model a shot that ended on a target."""
    review = await build_review_input(seeded, await _ingest_fill_ended(seeded))

    assert review.excerpts[0]["heading_path"] == "STOP_CONDITIONS#multiple-stop-conditions"
    assert review.excerpts[0]["query"] == FAULT_QUERIES["skipped_at_start"]
    assert FAULT_QUERIES["skipped"] not in {e["query"] for e in review.excerpts}


async def test_the_stop_early_signal_and_rules_never_reach_a_fill_that_ended_at_the_start(
    seeded: Database,
) -> None:
    started = await build_review_input(seeded, await _ingest_fill_ended(seeded))
    stopped = await build_review_input(seeded, await _ingest_lever(seeded))

    assert "fault:skipped" not in started.signals
    assert "pressure_exit_on_a_fill" not in stopped.rule_keys
    assert "fault:skipped_at_start" not in stopped.signals

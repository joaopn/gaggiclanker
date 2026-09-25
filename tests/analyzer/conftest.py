"""A Set with six shots, a knowledge tier, and an analyzer wired to a fake provider.

Everything here is deterministic on purpose. The context builder's whole claim is
that two builds of the same shot produce byte-identical text — that is what the
golden test asserts and what makes "the same shot selects the same rules"
checkable — so nothing in this fixture reads the clock, and the shots carry
hand-written diagnostics rather than diagnostics derived from a `.slog`, whose
numbers would move the day the diagnostics engine is tuned.

The fixture Set is the one from the chunk's third acceptance criterion: five
earlier shots in the same Set, judged, with the advice that followed each of
them, plus the sixth shot that is the subject of the analysis.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.analyzer.service import AnalyzerService
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.analyses import (
    AnalysesRepository,
    SuggestionsRepository,
    SuggestionWrite,
)
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
)
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.machines import MachineRepository, MachineUpsert
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotInsert, ShotSampleRow, ShotsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.knowledge.rules import seed_rules
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings_service import SettingsService
from tests.llm.conftest import FakeProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: A complete, valid analysis. The default the fake provider answers with, so a
#: test that is about the *service* does not have to restate the output
#: contract; a test that is about the output overrides one field of it.
GOOD_OUTPUT: dict[str, Any] = {
    "shot_style": "bloom",
    "execution": {
        "summary": "Clean extraction with a short bloom and no channeling.",
        "issues": [
            {
                "signal": "flow_adherence",
                "severity": "minor",
                "evidence": "flow RMSE 0.41 ml/s against the commanded curve",
            }
        ],
    },
    "taste_prediction": {"balance": "sour", "body": "thin", "confidence": "medium"},
    "diagnosis": "The shot ran four seconds fast for a bloom profile and the puck never loaded.",
    "suggestions": [
        {
            "variable": "grind",
            "direction": "finer",
            "magnitude": 2,
            "unit": "grinder_steps",
            "reason": "28 s target, 24 s actual, and flow overshot the commanded curve.",
            "confidence": "high",
            "priority": 1,
        },
        {
            "variable": "yield",
            "direction": "increase",
            "magnitude": 5,
            "unit": "g",
            "reason": "If the grind is already at its limit, take the ratio out instead.",
            "confidence": "medium",
            "priority": 2,
        },
        {
            "variable": "pressure",
            "direction": "decrease",
            "magnitude": 1,
            "unit": "bar",
            "reason": "A natural at this roast wants a bar less than nine.",
            "confidence": "low",
            "priority": 3,
        },
    ],
    "profile_patch": [
        {
            "phase_index": 1,
            "field": "duration",
            "from": "7",
            "to": "10",
            "reason": "A longer bloom for a light natural.",
        }
    ],
    "questions_for_user": ["What did the last shot taste like at the same grind?"],
    "rules_used": ["hierarchy", "grind", "natural.light"],
}


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "analyzer.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def seeded(db: Database) -> Database:
    """A database with the shipped prompts and the whole knowledge base in it.

    All three tiers, because the golden prompt carries all three. Seeding the
    twenty-five documents is a couple of hundred milliseconds of chunking once
    per test that asks for it — worth it, because a golden that rendered "no
    reference excerpts were retrieved" would assert nothing about the half of
    this feature that does the retrieving.
    """
    await seed_prompts(PromptsRepository(db), DEFAULT_PROMPTS_DIR)
    await seed_rules(RulesRepository(db))
    await KnowledgeService(db).seed_docs()
    return db


@dataclass(slots=True)
class Fixture:
    """The ids the analyzer tests reach for."""

    db: Database
    bean_id: int
    grinder_id: int
    set_id: int
    version_id: int
    profile_version_id: int
    #: Oldest first. `shots[-1]` is the one under analysis; the five before it
    #: are the trajectory.
    shots: list[int]


def _diagnostics(
    *,
    channeling: str,
    resistance: float,
    flow_rmse: float,
    score: float,
    reason: str,
) -> str:
    """A summary-level diagnostics blob in the shape the shots UI stores.

    Hand-written rather than derived: these numbers appear verbatim in the
    golden prompt, and deriving them would make the golden file move whenever
    the diagnostics engine is re-calibrated — which is a change to a different
    subsystem and should not fail this test.
    """
    return json.dumps(
        {
            "summary": {
                "temperature": {"min_c": 92.1, "max_c": 94.4, "avg_c": 93.2, "target_avg_c": 93.0},
                "pressure": {"min_bar": 0.4, "max_bar": 9.2, "avg_bar": 7.8, "peak_time_s": 12.5},
                "flow": {
                    "total_volume_ml": 36.4,
                    "avg_flow_ml_s": 1.9,
                    "peak_flow_ml_s": 3.1,
                    "time_to_first_drip_s": 8.2,
                },
                "extraction": {
                    "preinfusion_time_s": 10.0,
                    "main_extraction_time_s": 18.0,
                    "total_time_s": 28.0,
                },
            },
            "diagnostics": {
                "has_pressure": True,
                "resistance_avg": resistance,
                "resistance_slope": -0.04,
                "channeling_risk": channeling,
                "temperature_stability_c": 0.42,
                "pressure_rmse_bar": 0.31,
                "max_overshoot_bar": 0.22,
                "flow_rmse_ml_s": flow_rmse,
                "max_flow_overshoot_ml_s": 0.55,
                "scale_connected": True,
                "annotations": {
                    "resistance_level": "MODERATE",
                    "resistance_erosion": "GRADUAL_DECLINE",
                    "channeling_risk": channeling,
                    "pressure_adherence": "EXCELLENT",
                    "pressure_overshoot": "WITHIN_TOLERANCE",
                    "temperature_stability": "STABLE",
                    "flow_adherence": "GOOD",
                    "flow_overshoot": "MINOR_DEVIATION",
                },
            },
            "detail_level": "per_phase",
            "has_pressure": True,
            "score": {
                "score": score,
                "confidence": "high",
                "reason": reason,
                "components": {"flow_adherence": -0.05},
            },
        },
        separators=(",", ":"),
    )


_PHASES = json.dumps(
    [
        {
            "name": "Pre-infusion",
            "phase_number": 0,
            "start_time_seconds": 0.0,
            "duration_seconds": 3.0,
            "sample_count": 12,
            "avg_temperature_c": 92.8,
            "avg_pressure_bar": 3.9,
            "total_flow_ml": 4.2,
            "diagnostics": {"phase_type": "preinfusion", "ramp_rate_bar_s": 1.3},
        },
        {
            "name": "Bloom",
            "phase_number": 1,
            "start_time_seconds": 3.0,
            "duration_seconds": 7.0,
            "sample_count": 28,
            "avg_temperature_c": 93.1,
            "avg_pressure_bar": 0.6,
            "total_flow_ml": 0.4,
            "diagnostics": {"phase_type": "preinfusion", "ramp_rate_bar_s": 0.0},
        },
        {
            "name": "Pressurise",
            "phase_number": 2,
            "start_time_seconds": 10.0,
            "duration_seconds": 18.0,
            "sample_count": 72,
            "avg_temperature_c": 93.4,
            "avg_pressure_bar": 8.8,
            "total_flow_ml": 31.8,
            "diagnostics": {
                "phase_type": "brew",
                "channeling_risk": "LOW",
                "resistance_avg": 2.4,
            },
        },
    ],
    separators=(",", ":"),
)


def _samples() -> list[ShotSampleRow]:
    """A 28 s shot at 4 Hz, walking a bloom profile. Deterministic arithmetic."""
    rows: list[ShotSampleRow] = []
    for index in range(112):
        t_ms = index * 250
        seconds = t_ms / 1000
        if seconds < 3:
            pressure, flow = 1.3 * seconds, 0.0
        elif seconds < 10:
            pressure, flow = 0.6, 0.05
        else:
            pressure = 9.0 - (seconds - 10) * 0.1
            flow = 1.6 + (seconds - 10) * 0.03
        rows.append(
            ShotSampleRow(
                t_ms=t_ms,
                ct=round(92.8 + (index % 7) * 0.1, 2),
                tt=93.0,
                cp=round(pressure, 2),
                tp=9.0,
                pf=round(flow, 2),
                tf=1.8,
                v=round(max(0.0, (seconds - 8) * 2.0), 2),
                phase_number=0 if seconds < 3 else (1 if seconds < 10 else 2),
            )
        )
    return rows


#: The five earlier shots, in order: what each one did, and what was said about
#: it afterwards. Written out rather than generated because the golden file has
#: to read as a trajectory somebody could follow.
_HISTORY: tuple[dict[str, Any], ...] = (
    {
        "device_id": "000101",
        "started_at": "2026-03-02T08:10:00.000Z",
        "duration_ms": 21_000,
        "final_weight_g": 38.0,
        "score": 7.4,
        "channeling": "MODERATE",
        "rating": 2,
        "balance": "sour",
        "taste": ["sour_fermented.sour"],
        "notes": "Gushed. Sour and thin.",
        "advice": ("grind", "finer", 2.0, "grinder_steps", "accepted"),
    },
    {
        "device_id": "000102",
        "started_at": "2026-03-02T08:20:00.000Z",
        "duration_ms": 23_000,
        "final_weight_g": 37.2,
        "score": 8.1,
        "channeling": "LOW",
        "rating": 3,
        "balance": "sour",
        "taste": ["sour_fermented.sour.citric_acid"],
        "notes": "Better, still sharp.",
        "advice": ("grind", "finer", 1.0, "grinder_steps", "accepted"),
    },
    {
        "device_id": "000103",
        "started_at": "2026-03-02T08:30:00.000Z",
        "duration_ms": 25_000,
        "final_weight_g": 36.4,
        "score": 8.6,
        "channeling": "LOW",
        "rating": 3,
        "balance": "sour",
        "taste": ["sour_fermented.sour", "fruity.citrus_fruit"],
        "notes": "Still on the sour side of balanced.",
        "advice": ("temperature", "increase", 1.0, "c", "rejected"),
    },
    {
        "device_id": "000104",
        "started_at": "2026-03-03T08:05:00.000Z",
        "duration_ms": 26_500,
        "final_weight_g": 36.0,
        "score": 8.8,
        "channeling": "LOW",
        "rating": 4,
        "balance": "balanced",
        "taste": ["sweet.brown_sugar.caramelized", "nutty_cocoa.cocoa"],
        "notes": "Best so far.",
        "advice": None,
    },
    {
        "device_id": "000105",
        "started_at": "2026-03-03T08:15:00.000Z",
        "duration_ms": 24_000,
        "final_weight_g": 36.8,
        "score": 8.2,
        "channeling": "LOW",
        "rating": 3,
        "balance": "sour",
        "taste": ["sour_fermented.sour"],
        "notes": "Went backwards.",
        "advice": ("yield", "increase", 5.0, "g", "open"),
    },
)


@pytest.fixture
async def fixture(seeded: Database) -> Fixture:
    """One Set, six shots, five judgements and the advice that followed them."""
    return await build_fixture(seeded)


async def build_fixture(db: Database) -> Fixture:
    """The fixture data, as a plain function.

    A function as well as a fixture because the route tests build it against the
    *app's* own database handle, and reaching into a pytest fixture's wrapped
    coroutine to do that is the kind of trick that breaks on a pytest upgrade.
    """
    await seed_prompts(PromptsRepository(db), DEFAULT_PROMPTS_DIR)
    await seed_rules(RulesRepository(db))
    await KnowledgeService(db).seed_docs()
    await MachineRepository(db).update_identity(
        MachineUpsert(host="kitchen.local", name="Kitchen", hardware_string="GaggiMate Pro")
    )
    bean = await BeansRepository(db).create(
        BeanWrite(
            name="Ethiopia Guji",
            roaster="Hasbean",
            origin="Ethiopia",
            process="natural",
            roast_level="light",
            # Intensity left unstated on purpose: the golden shows that an
            # unstated scale is absent, not "not stated".
            acidity=4,
            sweetness=3,
            description="peach, jasmine, lemon",
        )
    )
    grinder = await GrindersRepository(db).create(
        GrinderWrite(name="Niche Zero", model="NZ", burr_type="conical", step_unit="numbers")
    )
    document = json.loads((FIXTURES / "profiles" / "docs-medium-18g.json").read_text())
    profile_version, _ = await ProfilesRepository(db).ensure_version(
        Profile.model_validate(document)
    )
    stored_set = await SetsRepository(db).create(
        SetWrite(
            name="Guji natural on the Niche",
            bean_id=bean.id,
            grinder_id=grinder.id,
        ),
        SetVersionWrite(
            profile_version_id=profile_version.id,
            grind_setting="22 numbers",
            grind_value=22.0,
            dose_g=18.0,
            target_yield_g=36.0,
            intent="Baseline for this bag.",
        ),
    )
    version_id = stored_set.current_version_id
    assert version_id is not None

    shots_repo = ShotsRepository(db)
    judgements = JudgementsRepository(db)
    analyses = AnalysesRepository(db)
    suggestions = SuggestionsRepository(db)
    shot_ids: list[int] = []

    for entry in _HISTORY:
        shot_id = await shots_repo.insert(
            ShotInsert(
                device_id=str(entry["device_id"]),
                raw_slog=b"fixture",
                started_at=str(entry["started_at"]),
                duration_ms=int(entry["duration_ms"]),
                profile_version_id=profile_version.id,
                profile_name_on_device="Medium 18g 1:2",
                final_weight_g=float(entry["final_weight_g"]),
                final_exit_reason=1,
                scale_connected=True,
                sample_count=112,
                sample_interval_ms=250,
                phases_json=_PHASES,
                diagnostics_json=_diagnostics(
                    channeling=str(entry["channeling"]),
                    resistance=2.4,
                    flow_rmse=0.41,
                    score=float(entry["score"]),
                    reason="Execution capped by flow adherence (0.05 point penalty).",
                ),
                execution_score=float(entry["score"]),
                execution_reason="Execution capped by flow adherence (0.05 point penalty).",
            )
        )
        await SetsRepository(db).assign_shot(shot_id, version_id)
        await judgements.upsert(
            shot_id,
            JudgementWrite(
                rating=int(entry["rating"]),
                balance=str(entry["balance"]),  # type: ignore[arg-type]
                taste_notes=list(entry["taste"]),
                dose_in_g=18.0,
                dose_out_g=float(entry["final_weight_g"]),
                grind_setting="22 numbers",
                notes=str(entry["notes"]),
            ),
        )
        advice = entry["advice"]
        if advice is not None:
            variable, direction, magnitude, unit, status = advice
            analysis_id = await analyses.start(
                _start(shot_id, version_id),
            )
            await analyses.finish(analysis_id, status="ok", output={"diagnosis": "fixture"})
            suggestion_ids = await suggestions.insert_many(
                analysis_id,
                [
                    SuggestionWrite(
                        variable=variable,
                        direction=direction,
                        magnitude=magnitude,
                        unit=unit,
                        reason="from the fixture",
                        confidence="medium",
                        priority=1,
                    )
                ],
            )
            if status == "accepted":
                await suggestions.accept(suggestion_ids[0], None)
            elif status == "rejected":
                await suggestions.reject(suggestion_ids[0])
        shot_ids.append(shot_id)

    subject = await shots_repo.insert(
        ShotInsert(
            device_id="000106",
            raw_slog=b"fixture",
            started_at="2026-03-03T08:25:00.000Z",
            duration_ms=24_000,
            profile_version_id=profile_version.id,
            profile_name_on_device="Medium 18g 1:2",
            final_weight_g=37.5,
            final_exit_reason=1,
            brew_delay_ms=1200,
            scale_connected=True,
            sample_count=112,
            sample_interval_ms=250,
            phases_json=_PHASES,
            diagnostics_json=_diagnostics(
                channeling="LOW",
                resistance=2.1,
                flow_rmse=0.44,
                score=8.3,
                reason="Execution capped by flow adherence (0.07 point penalty).",
            ),
            execution_score=8.3,
            execution_reason="Execution capped by flow adherence (0.07 point penalty).",
        ),
        _samples(),
    )
    await SetsRepository(db).assign_shot(subject, version_id)
    await judgements.upsert(
        subject,
        JudgementWrite(
            rating=3,
            balance="sour",
            taste_notes=["sour_fermented.sour.citric_acid", "fruity.citrus_fruit.lemon"],
            aroma_notes=["floral.floral.jasmine"],
            dose_in_g=18.0,
            dose_out_g=37.5,
            grind_setting="22 numbers",
            notes="Sharp up front, nothing behind it.",
            decision="improve",
        ),
    )
    shot_ids.append(subject)

    # Tier 3, both halves: one confirmed insight that applies to this Set (so
    # the golden shows what "what you have learned" renders as), and one that is
    # confirmed but scoped to a different grinder (so the golden also proves
    # that scoping actually excludes something).
    insights = InsightsRepository(db)
    await insights.insert(
        InsightWrite(
            scope=InsightScope(grinder_id=grinder.id, process="natural"),
            text="Naturals on this grinder want two numbers finer than a washed bean of the "
            "same roast.",
            evidence_shot_ids=[shot_ids[0], shot_ids[1]],
            source="analysis",
            confirmed=True,
        )
    )
    await insights.insert(
        InsightWrite(
            scope=InsightScope(grinder_id=grinder.id + 99),
            text="The other grinder drifts coarser as it warms up.",
            source="user",
            confirmed=True,
        )
    )
    await insights.insert(
        InsightWrite(
            scope=InsightScope(process="natural"),
            text="An unconfirmed proposal, which must never reach a prompt.",
            source="analysis",
            confirmed=False,
        )
    )

    return Fixture(
        db=db,
        bean_id=bean.id,
        grinder_id=grinder.id,
        set_id=stored_set.id,
        version_id=version_id,
        profile_version_id=profile_version.id,
        shots=shot_ids,
    )


def _start(shot_id: int, version_id: int) -> Any:
    from gaggiclanker.db.repos.analyses import AnalysisStart

    return AnalysisStart(
        shot_id=shot_id,
        set_version_id=version_id,
        provider="fake",
        model="fixture-model",
        prompt_name="analysis",
        prompt_version="fixture",
    )


@pytest.fixture
def provider() -> FakeProvider:
    """A provider that answers with :data:`GOOD_OUTPUT` unless a test scripts it."""
    return FakeProvider(script=[json.dumps(GOOD_OUTPUT)])


@pytest.fixture
def budget() -> RateLimitBudget:
    """This test's own budget. The real one is process-wide on purpose."""
    return RateLimitBudget(retries=0)


@pytest.fixture
def llm(db: Database, provider: FakeProvider, budget: RateLimitBudget) -> LlmService:
    return LlmService(
        SettingsService(SettingsRepository(db)),
        budget=budget,
        mode_memory=ModeMemory(),
        provider_factory=lambda _config, _name: provider,
    )


@pytest.fixture
def analyzer(fixture: Fixture, llm: LlmService) -> AnalyzerService:
    service = AnalyzerService(fixture.db, llm, PromptService(PromptsRepository(fixture.db)))
    # The retry backoff is a real wait in production and dead time here: the
    # failure paths retry two or three times, and half a second each adds up to
    # most of this file's wall clock.
    service.retry_delay_s = 0.0
    return service

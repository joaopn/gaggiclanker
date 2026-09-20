"""A small archive with four dialled-in Sets in it, and a coffee nobody has brewed.

Everything here is deterministic: hand-written diagnostics rather than numbers
derived from a `.slog`, fixed timestamps, and an explicit `as_of` date rather
than today. The golden prompt is rendered from this fixture, so anything that
read the clock would move the file once a day for ever.

The Sets are chosen to make the scoring readable rather than to be realistic:

    Kenya AA      light        washed   Kenya   Niche   5 shots, mean 4.4 stars
    Guji          light        natural  Ethiopia Niche  3 shots, mean 3 stars
    Brazil        medium-dark  washed   Brazil  Niche   2 shots, mean 4 stars
    Sumatra       light        washed   Kenya   Mazzer  4 shots, mean 5 stars
    Colombia      light        washed   Kenya   Niche   no shots at all

The new coffee is a light washed Kenyan, so Kenya AA matches on all three
attributes; Guji matches the roast only; Brazil matches the process only, and
its roast is two steps away, which is *not* adjacent. Sumatra matches on all
three and has the best outcome in the archive — and is on the other grinder, so
it must never appear. Colombia matches on all three too and has never been
brewed, so it must never appear either.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.machines import MachineRepository, MachineUpsert
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.knowledge.rules import seed_rules
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.starting.service import StartingPointService
from tests.llm.conftest import FakeProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: The day every run in this suite is asked on. Fixed, so the date the context
#: states as "today" renders the same in the golden file next year.
AS_OF = "2026-03-10"

#: A complete, valid starting point. The default the fake provider answers
#: with, so a test about the *service* does not restate the output contract; a
#: test about the output overrides one field of it.
GOOD_OUTPUT: dict[str, Any] = {
    "summary": (
        "A light washed Kenyan. Expect blackcurrant and a sharp acidity; it will want "
        "a fine grind and a long ratio."
    ),
    "questions_for_user": ["What do you normally grind espresso at on the Niche?"],
    "options": [
        {
            "option": "conservative",
            "headline": "Safe: 1:2 at 93 °C on the profile you already use",
            "grind_setting": "22",
            "grind_is_absolute": True,
            "grind_note": "Your usual 22, unchanged, so the first shot is a measurement.",
            "dose_g": 18.0,
            "yield_g": 36.0,
            "ratio": 2.0,
            "temperature_c": 93.0,
            "profile_version_id": None,
            "profile_note": "Whatever is selected on the machine is fine to start.",
            "rationale": "Classic espresso ratio while the bag is still degassing.",
            "rules_used": ["classic"],
            "excerpts_used": [],
            "similar_set_version_ids": [],
        },
        {
            "option": "recommended",
            "headline": "Start here: 1:2.5 at 94 °C, two finer",
            "grind_setting": "20",
            "grind_is_absolute": True,
            "grind_note": "Two numbers finer than your usual 22; a light washed bean wants it.",
            "dose_g": 18.0,
            "yield_g": 45.0,
            "ratio": 2.5,
            "temperature_c": 94.0,
            "profile_version_id": None,
            "profile_note": "",
            "rationale": "Light roasts run 93-96 °C and 1:2 to 1:3.",
            "rules_used": ["light"],
            "excerpts_used": [],
            "similar_set_version_ids": [],
        },
        {
            "option": "adventurous",
            "headline": "Push it: 1:3 at 95 °C with a long bloom",
            "grind_setting": "19",
            "grind_is_absolute": True,
            "grind_note": "One finer again; back it off if the shot chokes.",
            "dose_g": 18.0,
            "yield_g": 54.0,
            "ratio": 3.0,
            "temperature_c": 95.0,
            "profile_version_id": None,
            "profile_note": "",
            "rationale": "A long ratio is where a Kenyan of this roast usually opens up.",
            "rules_used": [],
            "excerpts_used": [],
            "similar_set_version_ids": [],
        },
    ],
}

#: A valid profile document an option may carry. Terminates on a volumetric
#: stop at the yield, which is what the policy and the prompt both require.
GOOD_PROFILE: dict[str, Any] = {
    "label": "Kenya light 1:2.5",
    "type": "pro",
    "description": "Long pre-infusion, nine bar, volumetric stop at 45 g.",
    "temperature": 94.0,
    "phases": [
        {
            "name": "Pre-infusion",
            "phase": "preinfusion",
            "valve": 1,
            "duration": 8,
            "pump": {"target": "flow", "pressure": 3, "flow": 2},
            "targets": [{"type": "pressure", "operator": "gte", "value": 4}],
        },
        {
            "name": "Extraction",
            "phase": "brew",
            "valve": 1,
            "duration": 30,
            "pump": {"target": "pressure", "pressure": 9, "flow": 0},
            "transition": {"type": "ease-in", "duration": 3},
            "targets": [{"type": "volumetric", "operator": "gte", "value": 45}],
        },
    ],
}

#: A profile the safety policy refuses. Eleven phases, and the policy allows
#: ten — the one thing `clamp` deliberately does not fix, because removing a
#: phase would change what the profile brews. A perfectly good `Profile` that
#: is still not writable, which is what the output model's policy validator
#: exists to catch before the run is stored as `ok`.
REFUSED_PROFILE: dict[str, Any] = {
    "label": "Eleven phases",
    "type": "pro",
    "temperature": 94.0,
    "phases": [
        {
            "name": f"Phase {index}",
            "phase": "brew",
            "valve": 1,
            "duration": 5,
            "pump": {"target": "pressure", "pressure": 9, "flow": 0},
            "targets": (
                [{"type": "volumetric", "operator": "gte", "value": 45}] if index == 10 else []
            ),
        }
        for index in range(11)
    ],
}


#: A profile with no stop condition anywhere. Structurally valid, inside every
#: bound, and it runs until its 30 s expires — flooding the cup. The shared
#: policy calls that "stops on time", which is the right answer for a backflush
#: and the wrong one for a starting point, so the rule that rejects it lives on
#: `StartingPointOption` rather than in `profile_policy`.
UNTERMINATED_PROFILE: dict[str, Any] = {
    "label": "No stop anywhere",
    "type": "pro",
    "temperature": 94.0,
    "phases": [
        {
            "name": "Extraction",
            "phase": "brew",
            "valve": 1,
            "duration": 30,
            "pump": {"target": "pressure", "pressure": 9, "flow": 0},
            "targets": [],
        }
    ],
}

#: Three phases, which the default policy allows and a *configured* ceiling of
#: two does not. The only way left to reach the accept path's own 422 now that
#: the output model checks the default bounds at propose time — and it is the
#: real case: somebody narrowed the policy in Settings after the run.
THREE_PHASE_PROFILE: dict[str, Any] = {
    "label": "Three phases",
    "type": "pro",
    "temperature": 94.0,
    "phases": [
        {
            "name": f"Phase {index}",
            "phase": "brew",
            "valve": 1,
            "duration": 10,
            "pump": {"target": "pressure", "pressure": 9, "flow": 0},
            "targets": (
                [{"type": "volumetric", "operator": "gte", "value": 45}] if index == 2 else []
            ),
        }
        for index in range(3)
    ],
}


@dataclass(slots=True)
class Fixture:
    """The ids the starting-point tests reach for."""

    db: Database
    grinder_id: int
    other_grinder_id: int
    #: The bag nobody has opened: light, washed, Kenya.
    new_bean_id: int
    profile_version_id: int
    #: `{label: set_version_id}` for every Set built below.
    versions: dict[str, int] = field(default_factory=dict)
    sets: dict[str, int] = field(default_factory=dict)


#: What each seeded Set is made of. One table, because every one of these rows
#: exists to make one term of the score observable and the test reads better
#: next to the data than next to a page of inserts.
_ARCHIVE: tuple[dict[str, Any], ...] = (
    {
        "label": "kenya",
        "bean": {
            "name": "Kenya AA",
            "roast_level": "light",
            "process": "washed",
            "origin": "Kenya",
        },
        "grinder": "niche",
        "grind": "21",
        "dose": 18.0,
        "yield": 45.0,
        # Five shots is the confidence ceiling, so this version's outcome term
        # is at full weight — which is what makes it beat Guji on more than
        # attributes alone.
        "shots": [(4, 8.8), (5, 9.1), (4, 8.5), (5, 9.3), (4, 8.9)],
    },
    {
        "label": "guji",
        "bean": {
            "name": "Ethiopia Guji",
            "roast_level": "light",
            "process": "natural",
            "origin": "Ethiopia",
        },
        "grinder": "niche",
        "grind": "23",
        "dose": 18.0,
        "yield": 40.0,
        "shots": [(3, 8.0), (3, 7.8), (3, 8.2)],
    },
    {
        "label": "brazil",
        "bean": {
            "name": "Brazil Cerrado",
            "roast_level": "medium-dark",
            "process": "washed",
            "origin": "Brazil",
        },
        "grinder": "niche",
        "grind": "26",
        "dose": 18.0,
        "yield": 32.0,
        "shots": [(4, 8.6), (4, 8.4)],
    },
    {
        "label": "sumatra",
        "bean": {
            "name": "Sumatra Lintong",
            "roast_level": "light",
            "process": "washed",
            "origin": "Kenya",
        },
        # The same three attributes as the new bag, and a *different* grinder:
        # this row is the whole proof that the grinder is a filter rather than
        # a score term, because on attributes alone it would come first.
        "grinder": "mazzer",
        "grind": "7.5",
        "dose": 20.0,
        "yield": 40.0,
        "shots": [(5, 9.5), (5, 9.4), (5, 9.6), (5, 9.5)],
    },
    {
        "label": "untouched",
        "bean": {
            "name": "Colombia Huila",
            "roast_level": "light",
            "process": "washed",
            "origin": "Kenya",
        },
        "grinder": "niche",
        "grind": "22",
        "dose": 18.0,
        "yield": 36.0,
        # No shots at all. A recipe somebody wrote down and never brewed is an
        # intention, not a result, and must not be offered as evidence.
        "shots": [],
    },
)


def _diagnostics(score: float) -> str:
    """A minimal diagnostics blob. The starting point reads none of it.

    Written anyway because the shots have to look like shots to every other
    query that touches them, and an empty column is the shape a quarantined
    shot has.
    """
    return json.dumps(
        {
            "summary": {"flow": {"avg_flow_ml_s": 1.9}},
            "diagnostics": {"has_pressure": True, "channeling_risk": "LOW"},
            "score": {"score": score, "confidence": "high", "reason": "fixture"},
        },
        separators=(",", ":"),
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "starting.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def fixture(db: Database) -> Fixture:
    return await build_fixture(db)


async def build_fixture(db: Database, *, seed_knowledge: bool = True) -> Fixture:
    """The fixture data, as a plain function.

    A function as well as a fixture because the route tests build it against the
    *app's* own database handle, and reaching into a pytest fixture's wrapped
    coroutine to do that breaks on a pytest upgrade.

    ``seed_knowledge`` is opt-out because chunking the twenty-five shipped
    documents is a couple of hundred milliseconds, and most tests here are about
    SQL or about the service rather than about retrieval.
    """
    await seed_prompts(PromptsRepository(db), DEFAULT_PROMPTS_DIR)
    await seed_rules(RulesRepository(db))
    if seed_knowledge:
        await KnowledgeService(db).seed_docs()

    await MachineRepository(db).update_identity(
        MachineUpsert(
            host="kitchen.local",
            name="Kitchen",
            hardware_string="GaggiMate Pro",
            temperature_offset_c=-1.5,
        )
    )
    grinders = GrindersRepository(db)
    niche = await grinders.create(
        GrinderWrite(name="Niche Zero", model="NZ", burr_type="conical", step_unit="numbers")
    )
    mazzer = await grinders.create(
        GrinderWrite(name="Mazzer Philos", burr_type="flat", step_unit="numbers")
    )
    grinder_ids = {"niche": niche.id, "mazzer": mazzer.id}

    document = json.loads((FIXTURES / "profiles" / "docs-medium-18g.json").read_text())
    profile_version, _ = await ProfilesRepository(db).ensure_version(
        Profile.model_validate(document)
    )

    beans = BeansRepository(db)
    sets = SetsRepository(db)
    shots = ShotsRepository(db)
    judgements = JudgementsRepository(db)
    versions: dict[str, int] = {}
    set_ids: dict[str, int] = {}
    device_id = 100

    for entry in _ARCHIVE:
        bean = await beans.create(BeanWrite(**entry["bean"]))
        stored = await sets.create(
            SetWrite(
                name=f"{entry['bean']['name']} on the {entry['grinder']}",
                bean_id=bean.id,
                grinder_id=grinder_ids[str(entry["grinder"])],
            ),
            SetVersionWrite(
                profile_version_id=profile_version.id,
                grind_setting=str(entry["grind"]),
                grind_value=float(entry["grind"]),
                dose_g=float(entry["dose"]),
                target_yield_g=float(entry["yield"]),
                intent="Baseline for this bag.",
            ),
            # Only one Set may be active and the flag is not what
            # this suite is about; leaving them all inactive keeps the inserts
            # independent of their order.
            activate=False,
        )
        version_id = stored.current_version_id
        assert version_id is not None
        versions[str(entry["label"])] = version_id
        set_ids[str(entry["label"])] = stored.id

        for rating, score in entry["shots"]:
            device_id += 1
            shot_id = await shots.insert(
                ShotInsert(
                    device_id=f"{device_id:06d}",
                    raw_slog=b"fixture",
                    started_at=f"2026-02-{(device_id % 27) + 1:02d}T08:00:00.000Z",
                    duration_ms=28_000,
                    profile_version_id=profile_version.id,
                    profile_name_on_device="Medium 18g 1:2",
                    final_weight_g=float(entry["yield"]),
                    final_exit_reason=1,
                    scale_connected=True,
                    sample_count=112,
                    sample_interval_ms=250,
                    diagnostics_json=_diagnostics(float(score)),
                    execution_score=float(score),
                    execution_reason="fixture",
                )
            )
            await sets.assign_shot(shot_id, version_id)
            await judgements.upsert(
                shot_id,
                JudgementWrite(
                    rating=int(rating),
                    balance="balanced",
                    dose_in_g=float(entry["dose"]),
                    dose_out_g=float(entry["yield"]),
                ),
            )

    # The coffee the wizard is about: light, washed, Kenyan. Every attribute
    # matches the Kenya AA Set, which is what makes it the top anchor.
    new_bean = await beans.create(
        BeanWrite(
            name="Kenya Nyeri",
            roaster="Square Mile",
            origin="Kenya",
            process="washed",
            roast_level="light",
            description="blackcurrant, tomato, cane sugar",
        )
    )

    return Fixture(
        db=db,
        grinder_id=niche.id,
        other_grinder_id=mazzer.id,
        new_bean_id=new_bean.id,
        profile_version_id=profile_version.id,
        versions=versions,
        sets=set_ids,
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
def starting(fixture: Fixture, llm: LlmService) -> StartingPointService:
    from gaggiclanker.drafts.proposals import DraftProposals

    prompts = PromptService(PromptsRepository(fixture.db))
    drafts = DraftProposals(fixture.db, llm.settings)
    service = StartingPointService(fixture.db, llm, prompts, drafts=drafts)
    # The retry backoff is a real wait in production and dead time here.
    service.retry_delay_s = 0.0
    return service


def option_with(**changes: Any) -> dict[str, Any]:
    """:data:`GOOD_OUTPUT` with the *recommended* option's fields changed.

    The one option every accept test uses, so a test that is about a profile
    document does not have to restate the other two.
    """
    output: dict[str, Any] = json.loads(json.dumps(GOOD_OUTPUT))
    for item in output["options"]:
        if item["option"] == "recommended":
            item.update(changes)
    return output

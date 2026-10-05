"""The opening context of a Set conversation, and the golden it renders to.

The golden file is `golden/set-chat-context.txt`, and it is the real check on
this module: the other tests here each pin one rule, while the golden asserts
that the whole document — the version being argued, the ledger with its dead
ends and outcomes, the spread, the evidence table, this version's shots in
their base information and the Keep shots that are the target — reads as one
thing a person would recognise.

The archive behind it is built here rather than borrowed, because it has to
carry everything at once: a roll back, the two versions it stepped over, a
failed prediction, an inconclusive one, an open one, Keep shots and a Discard.

Regenerate with `uv run pytest tests/chat -k golden --update-golden` and read
the diff: that diff is what the model would have been told differently.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from gaggiclanker.chat.context import (
    INSIGHT_CHARS,
    INSIGHTS_SHOWN,
    LEDGER_VERSIONS,
    RECENT_SHOTS,
    opening_context,
)
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
)
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    RollbackWrite,
    SetsRepository,
    SetVersionPatch,
    SetVersionRow,
    SetVersionWrite,
    SetWrite,
    VersionOutcomeWrite,
)
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.domain.spread import CountedShot
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings import SETTINGS_REGISTRY
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.sets.conftest import make_profile_version

GOLDEN = Path(__file__).resolve().parent / "golden" / "set-chat-context.txt"


@dataclass(slots=True)
class Experiment:
    """A Set with a history: five versions, a roll back, and shots to grade.

    The attributes count the versions in the order they were recorded; their
    names are v1, v1.1, v2 (the dose change was marked major), v2.1 (the roll
    back, on the same profile) and v2.2.
    """

    db: Database
    set_id: int
    v1: int
    v2: int
    v3: int
    v4: int
    v5: int


def _diagnostics(*, first_drip_s: float, max_bar: float, brew_flow: float) -> str:
    """The three paths the measures are read from, as ingest writes them."""
    return json.dumps(
        {
            "summary": {
                "flow": {"time_to_first_drip_s": first_drip_s},
                "pressure": {"max_bar": max_bar},
            },
            "diagnostics": {"extraction": {"flow_avg_brew_ml_s": brew_flow}},
        }
    )


async def _shot(
    db: Database,
    device_id: str,
    *,
    started_at: str,
    duration_ms: int,
    weight_g: float,
    first_drip_s: float,
    max_bar: float,
    brew_flow: float,
) -> int:
    return await ShotsRepository(db).insert(
        ShotInsert(
            device_id=device_id,
            raw_slog=b"not-a-slog",
            started_at=started_at,
            duration_ms=duration_ms,
            final_weight_g=weight_g,
            diagnostics_json=_diagnostics(
                first_drip_s=first_drip_s, max_bar=max_bar, brew_flow=brew_flow
            ),
        )
    )


@pytest.fixture
async def experiment(tmp_path: Path) -> AsyncIterator[Experiment]:
    """One Set, dialled in badly, rolled back, and dialled in again.

    Every number is written down here rather than derived, so the golden moves
    only when this module's prose or arithmetic does.
    """
    db = Database(tmp_path / "context.db")
    await db.connect()
    await run_migrations(db)
    try:
        bean = await BeansRepository(db).create(
            BeanWrite(
                name="Ethiopia Guji",
                roaster="Hasbean",
                roast_level="light",
                process="natural",
                origin="Ethiopia",
                acidity=4,
            )
        )
        grinder = await GrindersRepository(db).create(
            GrinderWrite(name="Niche Zero", burr_type="conical", step_unit="numbers")
        )
        profile = await make_profile_version(db, "Classic 9 bar", temperature=93.0)
        sets = SetsRepository(db)
        judgements = JudgementsRepository(db)

        row = await sets.create(
            SetWrite(name="Guji natural on the Niche", bean_id=bean.id, grinder_id=grinder.id),
            SetVersionWrite(
                profile_version_id=profile,
                grind_setting="22",
                dose_g=18.0,
                target_yield_g=36.0,
                intent="Where the bag's card says to start.",
            ),
        )
        set_id = row.id
        assert row.current_version_id is not None
        v1 = row.current_version_id

        # v1: two shots somebody was happy with. They are the gold standard.
        for index, (device, duration, weight, drip, bar, flow) in enumerate(
            (
                ("000001", 33_000, 36.2, 7.1, 9.10, 1.90),
                ("000002", 34_000, 36.0, 7.4, 9.20, 1.85),
            )
        ):
            shot = await _shot(
                db,
                device,
                started_at=f"2026-04-01T08:0{index}:00.000Z",
                duration_ms=duration,
                weight_g=weight,
                first_drip_s=drip,
                max_bar=bar,
                brew_flow=flow,
            )
            assert await sets.assign_shot(shot, v1)
            await judgements.upsert(
                shot, JudgementWrite(rating=4, balance="balanced", decision="keep")
            )

        # v2: finer, and it did not work. Graded failed.
        version = await sets.add_version(
            set_id,
            SetVersionPatch(
                grind_setting="20",
                intent="Chase the sweetness by going finer.",
                prediction="Shot time up by about 4 s and less sour.",
            ),
        )
        assert version is not None
        v2 = version.id
        shot = await _shot(
            db,
            "000003",
            started_at="2026-04-02T08:00:00.000Z",
            duration_ms=45_000,
            weight_g=35.1,
            first_drip_s=11.0,
            max_bar=9.60,
            brew_flow=1.10,
        )
        assert await sets.assign_shot(shot, v2)
        await judgements.upsert(
            shot,
            JudgementWrite(
                rating=2,
                balance="bitter",
                decision="improve",
                notes="Choked and went harsh at the end.",
                taste_notes=["roasted.burnt"],
            ),
        )
        graded = await sets.set_outcome(
            set_id,
            v2,
            VersionOutcomeWrite(
                outcome="failed", note="It went slower and turned bitter rather than sweeter."
            ),
        )
        assert graded.version is not None

        # v3: half a gram more dose, one shot, nothing conclusive. Marked a
        # major version, so the record carries both kinds of name: v2.
        version = await sets.add_version(
            set_id,
            SetVersionPatch(
                dose_g=18.5,
                intent="See whether a bigger dose slows the gusher down.",
                prediction="Shot time up by 2 s against v1.1.",
            ),
            major=True,
        )
        assert version is not None
        v3 = version.id
        shot = await _shot(
            db,
            "000004",
            started_at="2026-04-03T08:00:00.000Z",
            duration_ms=44_000,
            weight_g=35.6,
            first_drip_s=10.4,
            max_bar=9.50,
            brew_flow=1.15,
        )
        assert await sets.assign_shot(shot, v3)
        await judgements.upsert(
            shot, JudgementWrite(rating=2, balance="bitter", decision="improve")
        )
        graded = await sets.set_outcome(
            set_id,
            v3,
            VersionOutcomeWrite(
                outcome="inconclusive", note="One shot, and the difference is inside the spread."
            ),
        )
        assert graded.version is not None

        # v4: go back to what worked. v2 and v3 are now dead ends. This is a row
        # as a roll back wrote it before going back became a move of the Set's
        # pointer (a new version copying v1's recipe and naming it as what it
        # restores): databases still hold such rows and the line walk must keep
        # reading them. The revert itself is exercised below.
        first = await sets.get_version(v1)
        assert first is not None
        rolled = await sets.add_version(
            set_id,
            SetVersionPatch(
                profile_version_id=first.profile_version_id,
                grind_setting=first.grind_setting,
                grind_value=first.grind_value,
                dose_g=first.dose_g,
                target_yield_g=first.target_yield_g,
                intent="Back to the recipe with the Keep shots.",
                prediction="Back within the usual spread of v1.",
            ),
        )
        assert rolled is not None
        await db.execute(
            "UPDATE set_versions SET restores_version_id = ? WHERE id = ?", (v1, rolled.id)
        )
        v4 = rolled.id
        shot = await _shot(
            db,
            "000005",
            started_at="2026-04-04T08:00:00.000Z",
            duration_ms=33_500,
            weight_g=36.1,
            first_drip_s=7.2,
            max_bar=9.15,
            brew_flow=1.88,
        )
        assert await sets.assign_shot(shot, v4)
        await judgements.upsert(shot, JudgementWrite(rating=4, balance="balanced", decision="keep"))
        # Two more on the same recipe, so the Set has repeats and its spread is
        # measured rather than sitting on the floors.
        for index, (device, duration, weight, drip, bar, flow, rating) in enumerate(
            (
                ("000008", 32_500, 35.8, 6.9, 9.05, 1.95, 4),
                ("000009", 34_500, 36.4, 7.6, 9.25, 1.80, 5),
            )
        ):
            more = await _shot(
                db,
                device,
                started_at=f"2026-04-04T09:0{index}:00.000Z",
                duration_ms=duration,
                weight_g=weight,
                first_drip_s=drip,
                max_bar=bar,
                brew_flow=flow,
            )
            assert await sets.assign_shot(more, v4)
            await judgements.upsert(
                more, JudgementWrite(rating=rating, balance="balanced", decision="keep")
            )
        graded = await sets.set_outcome(
            set_id, v4, VersionOutcomeWrite(outcome="held", note="Straight back where it was.")
        )
        assert graded.version is not None

        # v5: the version this conversation is about. One shot judged, one
        # discarded, and nobody has graded the prediction.
        version = await sets.add_version(
            set_id,
            SetVersionPatch(
                grind_setting="21",
                intent="One number finer than the Keep recipe, for a little more body.",
                prediction=(
                    "Shot time up by 2 to 3 s against v2.1, yield the same, and no more bitterness."
                ),
            ),
        )
        assert version is not None
        v5 = version.id
        shot = await _shot(
            db,
            "000006",
            started_at="2026-04-05T08:00:00.000Z",
            duration_ms=36_000,
            weight_g=36.3,
            first_drip_s=8.0,
            max_bar=9.30,
            brew_flow=1.70,
        )
        assert await sets.assign_shot(shot, v5)
        await judgements.upsert(
            shot,
            JudgementWrite(
                rating=3,
                balance="balanced",
                decision="improve",
                notes="Sweeter, but the finish is drying.",
                taste_notes=["sweet.brown_sugar"],
            ),
        )
        spilled = await _shot(
            db,
            "000007",
            started_at="2026-04-05T08:10:00.000Z",
            duration_ms=12_000,
            weight_g=9.0,
            first_drip_s=6.0,
            max_bar=9.00,
            brew_flow=2.40,
        )
        assert await sets.assign_shot(spilled, v5)
        await judgements.upsert(
            spilled, JudgementWrite(decision="discard", notes="Knocked the cup.")
        )

        await InsightsRepository(db).insert(
            InsightWrite(
                scope=InsightScope(bean_id=bean.id),
                text="This bag runs fast for the first week after roasting.",
                source="chat",
                confirmed=True,
            )
        )
        yield Experiment(db=db, set_id=set_id, v1=v1, v2=v2, v3=v3, v4=v4, v5=v5)
    finally:
        await db.close()


def _claimed(summary: str, ordinals: dict[str, int]) -> set[int]:
    """The versions a summary line says it stands for, as ordinals, spans expanded.

    A span runs from one name to another over every version recorded between
    them, so ``ordinals`` (name → ordinal) is what expands it.
    """
    listed = summary.split("Not written out here:", 1)[1].split("(")[0]
    numbers: set[int] = set()
    for part in listed.split(","):
        span = re.fullmatch(r"\s*(v[\d.]+) to (v[\d.]+)\s*", part)
        one = re.fullmatch(r"\s*(v[\d.]+)\s*", part)
        if span is not None:
            numbers |= set(range(ordinals[span.group(1)], ordinals[span.group(2)] + 1))
        elif one is not None:
            numbers.add(ordinals[one.group(1)])
    assert numbers, summary
    return numbers


async def test_the_opening_context_matches_the_golden_file(
    experiment: Experiment, update_golden: bool
) -> None:
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    if update_golden:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(rendered, encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert GOLDEN.exists(), "run with --update-golden to create it"
    assert rendered == GOLDEN.read_text(encoding="utf-8")


async def test_the_same_archive_renders_the_same_text(experiment: Experiment) -> None:
    """Determinism, asserted directly rather than inferred from the golden.

    Nothing here may read the clock or iterate a set: a grade that can be traced
    needs a context that can be reproduced word for word.
    """
    scope = ToolScope.for_thread(experiment.set_id, experiment.v5)

    first = await opening_context(experiment.db, scope)
    second = await opening_context(experiment.db, scope)

    assert first == second


async def test_a_bean_with_only_a_name_is_named_and_nothing_else(experiment: Experiment) -> None:
    """The heading names what the person filled in and leaves the rest out.

    The golden covers the filled side (one scale set, two unset and absent).
    """
    await BeansRepository(experiment.db).update(1, BeanWrite(name="Mystery"))

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "Bean: Mystery. Grinder:" in rendered
    assert "not stated" not in rendered.split("THIS VERSION IS")[0]


async def test_a_general_conversation_gets_no_block(experiment: Experiment) -> None:
    assert await opening_context(experiment.db, ToolScope()) == ""


async def test_it_is_about_the_thread_s_version_not_the_current_one(
    experiment: Experiment,
) -> None:
    """A conversation opened on v1.1 is still about v1.1 three versions later."""
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v2)
    )

    assert "THIS VERSION IS v1.1" in rendered
    assert "(a dead end" in rendered
    assert "THE EVIDENCE FOR v1.1 AGAINST v1" in rendered


async def test_a_revert_is_told_to_the_conversation_it_reopens(experiment: Experiment) -> None:
    """v2.2 back to v1.1: the agent must not carry on as if nothing happened in between."""
    sets = SetsRepository(experiment.db)
    quiet = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v2)
    )
    assert "The Set went back to" not in quiet

    result = await sets.rollback(
        experiment.set_id, RollbackWrite(to_version_id=experiment.v2, note="v2.2 was too slow")
    )
    assert result.version is not None
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v2)
    )

    line = next(line for line in rendered.splitlines() if line.startswith("The Set went back to"))
    assert line.startswith("The Set went back to v1.1 from v2.2 on 20")
    assert line.endswith("UTC. Why: v2.2 was too slow")
    assert "THIS VERSION IS v1.1" in rendered and "the current version of this Set" in rendered
    # Another version's conversation is not told about it.
    other = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v4)
    )
    assert "The Set went back to" not in other
    # And the same archive reads the same bytes.
    assert rendered == await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v2)
    )


async def test_two_reverts_into_one_version_are_told_oldest_first(
    experiment: Experiment,
) -> None:
    sets = SetsRepository(experiment.db)
    await sets.rollback(
        experiment.set_id, RollbackWrite(to_version_id=experiment.v2, note="first time")
    )
    await sets.rollback(experiment.set_id, RollbackWrite(to_version_id=experiment.v5))
    await sets.rollback(
        experiment.set_id, RollbackWrite(to_version_id=experiment.v2, note="second time")
    )
    # Forge the log's times: the second revert's row is the older one by id order only.
    rows = await experiment.db.fetch_all(
        "SELECT id FROM set_version_reverts WHERE to_version_id = ? ORDER BY id",
        (experiment.v2,),
    )
    await experiment.db.execute(
        "UPDATE set_version_reverts SET created_at = '2026-05-02T10:00:00.000Z' WHERE id = ?",
        (rows[0]["id"],),
    )
    await experiment.db.execute(
        "UPDATE set_version_reverts SET created_at = '2026-05-01T10:00:00.000Z' WHERE id = ?",
        (rows[1]["id"],),
    )

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v2)
    )

    lines = [line for line in rendered.splitlines() if line.startswith("The Set went back to")]
    assert len(lines) == 2
    assert "2026-05-01 10:00 UTC. Why: second time" in lines[0]
    assert "2026-05-02 10:00 UTC. Why: first time" in lines[1]


async def _graded(experiment: Experiment) -> SetProposalsRepository:
    """Grade v2.2, so a change can be proposed against it at all."""
    await SetsRepository(experiment.db).set_outcome(
        experiment.set_id,
        experiment.v5,
        VersionOutcomeWrite(outcome="partly_held", note="Slower, but the finish is drying."),
    )
    return SetProposalsRepository(experiment.db)


async def _propose(experiment: Experiment, proposals: SetProposalsRepository) -> int:
    result = await proposals.create(
        experiment.set_id,
        ProposalWrite(
            patch=SetVersionPatch(dose_g=18.5),
            reason="Half a gram more, to carry the finish.",
            prediction="Compared to v2.2: a touch more body and no slower.",
        ),
    )
    assert result.proposal is not None, result.refused
    return result.proposal.id


async def test_a_waiting_proposal_is_in_front_of_the_agent_before_it_speaks(
    experiment: Experiment,
) -> None:
    """So a new conversation neither repeats it nor talks past it."""
    proposals = await _graded(experiment)
    await _propose(experiment, proposals)

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "A PROPOSAL IS WAITING FOR THE PERSON" in rendered
    assert "Dose 18 g → 18.5 g" in rendered
    assert "Half a gram more, to carry the finish." in rendered
    assert "Prediction (compared to v2.2)" in rendered
    assert "It has changed nothing: the Set is still on v2.2" in rendered
    assert "do not propose another change while it waits" in rendered


async def test_a_declined_proposal_is_told_with_the_reason_it_was_declined(
    experiment: Experiment,
) -> None:
    proposals = await _graded(experiment)
    proposal_id = await _propose(experiment, proposals)
    await proposals.decline(experiment.set_id, proposal_id, "The dose is not the problem.")

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "THE LAST PROPOSAL" in rendered
    assert 'They said: "The dose is not the problem."' in rendered
    assert "not something to send again" in rendered


async def test_an_accepted_proposal_names_the_version_it_became(
    experiment: Experiment,
) -> None:
    proposals = await _graded(experiment)
    proposal_id = await _propose(experiment, proposals)
    accepted = await proposals.accept(experiment.set_id, proposal_id)
    assert accepted.version is not None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, accepted.version.id)
    )

    assert "THE LAST PROPOSAL" in rendered
    assert accepted.version.version_label == "v2.3"
    assert "They accepted it; it is v2.3." in rendered


async def test_a_proposal_the_set_overtook_says_so_and_frees_the_agent(
    experiment: Experiment,
) -> None:
    """Retired by the version that overtook it, and the context says which one."""
    proposals = await _graded(experiment)
    await _propose(experiment, proposals)
    await SetsRepository(experiment.db).add_version(
        experiment.set_id,
        SetVersionPatch(dose_g=19, intent="Changed it by hand instead."),
    )
    versions = await SetsRepository(experiment.db).versions(experiment.set_id)

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, versions[0].id)
    )

    assert "A PROPOSAL IS WAITING" not in rendered
    assert "THE LAST PROPOSAL" in rendered
    assert versions[0].version_label == "v2.3"
    assert "the Set moved on to v2.3 before they did" in rendered
    assert "Propose afresh" in rendered


async def test_a_waiting_proposal_says_the_agent_suggested_a_major_version(
    experiment: Experiment,
) -> None:
    """The next conversation knows what it suggested, and that the person decides."""
    proposals = await _graded(experiment)
    result = await proposals.create(
        experiment.set_id,
        ProposalWrite(
            patch=SetVersionPatch(dose_g=18.5),
            reason="Half a gram more, to carry the finish.",
            prediction="Compared to v2.2: a touch more body and no slower.",
            suggest_major=True,
            major_reason="A heavier dose changes what this recipe is for.",
        ),
    )
    assert result.proposal is not None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert (
        'You suggested recording it as a major version: "A heavier dose changes what this '
        'recipe is for." They decide on the card.'
    ) in rendered


async def test_a_reason_that_ends_in_a_full_stop_is_not_given_a_second_one(
    experiment: Experiment,
) -> None:
    proposals = await _graded(experiment)
    await _propose(experiment, proposals)

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert 'carry the finish."' in rendered
    assert '".' not in rendered


async def test_a_set_nobody_has_proposed_anything_for_says_so(
    experiment: Experiment,
) -> None:
    """The block is always rendered: a section that comes and goes reads as two documents."""
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )
    assert "PROPOSALS\nNothing has been proposed to the person on this Set yet." in rendered


async def test_the_ledger_carries_the_dead_ends_and_the_track_record(
    experiment: Experiment,
) -> None:
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "- v1.1 (dead end) ·" in rendered
    assert "- v2 (dead end) ·" in rendered
    assert "← this version" in rendered
    assert "← what v2.2 is compared against" in rendered
    assert "Track record: 1 of 3 graded predictions held" in rendered


async def test_the_evidence_and_the_spread_are_the_page_s_own(experiment: Experiment) -> None:
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "THE EVIDENCE FOR v2.2 AGAINST v2.1" in rendered
    assert "HOW MUCH THIS SET VARIES WHEN NOTHING CHANGED" in rendered
    # The yardstick is stated with what it was held against, in both forms.
    assert "held against" in rendered
    assert "not measured yet" in rendered or "repeat shots" in rendered


async def test_a_discarded_shot_is_listed_and_marked_as_not_counted(
    experiment: Experiment,
) -> None:
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "Counted: not counted: discarded" in rendered


def _shot_headers(rendered: str) -> list[int]:
    """The shots written out in the shots block, in order."""
    block = rendered.split("SHOTS OF v", 1)[1].split("THE GOLD STANDARD", 1)[0]
    return [int(match) for match in re.findall(r"^shot (\d+)$", block, flags=re.MULTILINE)]


async def test_the_shots_are_this_version_s_newest_in_base_and_no_other_version_s(
    experiment: Experiment,
) -> None:
    sets = SetsRepository(experiment.db)
    mine = [
        row.shot_id for row in await sets.set_shots(experiment.set_id, version_id=experiment.v5)
    ]
    compared = {
        row.shot_id for row in await sets.set_shots(experiment.set_id, version_id=experiment.v4)
    }

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "ALL 2 SHOTS OF v2.2 (newest first)" in rendered
    assert _shot_headers(rendered) == mine, "newest first, as the Set page lists them"
    assert not set(_shot_headers(rendered)) & compared
    # Base only: the extended lines are a tool call away.
    assert "Rating: 3/5" in rendered
    assert "Score confidence" not in rendered and "[Curve]" not in rendered


async def test_the_number_of_shots_follows_what_the_caller_asks_for(
    experiment: Experiment,
) -> None:
    sets = SetsRepository(experiment.db)
    newest = (await sets.set_shots(experiment.set_id, version_id=experiment.v4))[0].shot_id

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v4), recent_shots=1
    )

    assert "THE LAST 1 OF 3 SHOTS OF v2.1 (newest first)" in rendered
    assert "The other 2 of v2.1's shots, and every other version's, are a list_set_shots" in (
        rendered
    )
    assert _shot_headers(rendered) == [newest]
    assert f"Shots written out below: {newest}." in rendered


async def test_the_shots_block_names_the_ids_of_the_shots_it_contains(
    experiment: Experiment,
) -> None:
    """So the agent can tell "is this shot already here?" from one line."""
    scope = ToolScope.for_thread(experiment.set_id, experiment.v5)
    every = await opening_context(experiment.db, scope)
    ids = _shot_headers(every)
    assert f"Shots written out below: {', '.join(map(str, ids))}." in every

    newest = await opening_context(experiment.db, scope, recent_shots=1)
    assert f"Shots written out below: {ids[0]}." in newest
    line = newest.split("Shots written out below:", 1)[1].split("\n", 1)[0]
    assert str(ids[1]) not in line


async def test_a_version_with_no_shots_says_so(experiment: Experiment) -> None:
    version = await SetsRepository(experiment.db).add_version(
        experiment.set_id, SetVersionPatch(intent="Nothing pulled on this yet.")
    )
    assert version is not None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, version.id)
    )

    assert version.version_label == "v2.3"
    assert "THE SHOTS OF v2.3\n- none yet." in rendered


def test_the_default_number_of_shots_is_the_setting_s_default() -> None:
    assert SETTINGS_REGISTRY["chatRecentShots"].default == RECENT_SHOTS


async def test_a_keep_shot_on_a_dead_end_is_not_the_gold_standard(
    experiment: Experiment,
) -> None:
    """Good on a branch nobody is brewing is not what an Improve shot is held to."""
    from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite

    on_a_dead_end = await SetsRepository(experiment.db).set_shots(
        experiment.set_id, version_id=experiment.v2
    )
    assert on_a_dead_end
    await JudgementsRepository(experiment.db).upsert(
        on_a_dead_end[0].shot_id, JudgementWrite(decision="keep")
    )

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    gold = next(line for line in rendered.splitlines() if "Keep shot" in line)
    assert "v1.1" not in gold
    assert "on v1, v2.1" in gold


async def test_a_set_with_no_shots_says_the_spread_is_not_measured_and_names_the_floors(
    experiment: Experiment,
) -> None:
    """A heading with nothing under it reads as a bug rather than as "not yet"."""
    empty = await SetsRepository(experiment.db).create(
        SetWrite(name="Nothing pulled yet", bean_id=1),
        SetVersionWrite(grind_setting="20"),
        automatch=False,
    )

    rendered = await opening_context(experiment.db, ToolScope.for_thread(empty.id))

    assert "Nothing is measured yet: this Set has no shots that count." in rendered
    assert "Shot time 2.00 s" in rendered
    assert "Rating 0.50" in rendered


async def test_the_insights_block_is_bounded_and_says_how_many_more_there_are(
    experiment: Experiment,
) -> None:
    """The one section whose length follows the kitchen rather than the Set."""
    repo = InsightsRepository(experiment.db)
    for number in range(INSIGHTS_SHOWN + 5):
        await repo.insert(
            InsightWrite(
                scope=InsightScope(bean_id=1),
                text=f"Insight {number}. " + "long " * 200,
                source="chat",
                confirmed=True,
            )
        )

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    block = rendered.split("CONFIRMED INSIGHTS THAT APPLY HERE\n")[1]
    listed = [line for line in block.splitlines() if line.startswith("- ")]
    assert len(listed) == INSIGHTS_SHOWN + 1
    assert listed[-1].startswith("- and ")
    assert "get_insights lists them" in listed[-1]
    # Each one's text is cut (the id and facts in front of it are not), so one essay
    # cannot take the context with it.
    assert all(len(line) <= INSIGHT_CHARS + 30 and line.endswith("…") for line in listed[:-1])
    # Newest first: the ones that supersede the others.
    assert f"Insight {INSIGHTS_SHOWN + 4}." in listed[0]


async def test_the_gold_standard_is_the_keep_shots_still_on_the_line(
    experiment: Experiment,
) -> None:
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "5 Keep shots on the line being brewed, on v1, v2.1" in rendered


async def test_the_budget_summarises_the_oldest_and_never_drops_the_two_that_matter(
    experiment: Experiment,
) -> None:
    """The rule, on a Set long enough to need it.

    Every version after the fifth is an ordinary one, so the ledger runs past
    its budget — and what has to survive is the version being argued and the
    one its prediction is compared against, however old that is.
    """
    sets = SetsRepository(experiment.db)
    for number in range(LEDGER_VERSIONS + 2):
        version = await sets.add_version(
            experiment.set_id, SetVersionPatch(intent=f"Filler {number}.")
        )
        assert version is not None
    # The newest version, predicting against v2.2 — which is by now far enough
    # back that the budget would otherwise have summarised it away.
    argued = await sets.add_version(
        experiment.set_id,
        SetVersionPatch(
            grind_setting="20.5",
            intent="Split the difference.",
            prediction="Between v2.1 and v2.2 on shot time.",
            compares_to_version_id=experiment.v5,
        ),
    )
    assert argued is not None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, argued.id)
    )

    assert "Not written out here:" in rendered
    assert "- v1 ·" not in rendered, "the oldest versions are summarised, not written out"
    assert f"- {argued.version_label} ← this version ·" in rendered
    assert "- v2.2 ← what v" in rendered
    # The summary names what it summarised and nothing else: v2.2 is written
    # out three lines below it, so a span that swallowed it would be a lie.
    made_in_order = sorted(
        await sets.versions(experiment.set_id), key=lambda version: (version.created_at, version.id)
    )
    ordinals = {version.version_label: index + 1 for index, version in enumerate(made_in_order)}
    ledger = [line for line in rendered.splitlines() if line.startswith("- v")]
    summary = next(line for line in rendered.splitlines() if "Not written out here:" in line)
    written = {ordinals[line[2:].split(" ")[0]] for line in ledger}

    assert _claimed(summary, ordinals) & written == set(), summary
    assert 1 in _claimed(summary, ordinals), "the oldest versions are what it stands for"


async def test_the_version_being_argued_survives_the_budget_however_old_it_is(
    experiment: Experiment,
) -> None:
    """An old conversation, reopened: it is still about its own version."""
    sets = SetsRepository(experiment.db)
    for number in range(LEDGER_VERSIONS + 4):
        assert await sets.add_version(
            experiment.set_id, SetVersionPatch(intent=f"Filler {number}.")
        )

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v2)
    )

    assert "THIS VERSION IS v1.1" in rendered
    assert "- v1.1 (dead end) ← this version ·" in rendered
    # And the version it is compared against comes with it.
    assert "- v1 ← what v1.1 is compared against ·" in rendered


# ── the ledger's averages ────────────────────────────────────────────


def _ledger_line_of(rendered: str, label: str) -> str:
    """The ledger line that starts with this version's name, whatever its marks."""
    return next(
        line
        for line in rendered.splitlines()
        if line.startswith(f"- {label} ") or line.startswith(f"- {label}\u00a0")
    )


def _averages_of(line: str) -> dict[str, float]:
    """The numbers in a line's "averages over …" part, by label."""
    part = line.split("averages over ", 1)[1].split(" · ")[0].split(": ", 1)[1]
    found: dict[str, float] = {}
    for label in ("shot time", "yield", "rating", "first drip"):
        match = re.search(rf"{label} (-?\d+\.\d)", part)
        if match is not None:
            found[label] = float(match.group(1))
    ratio = re.search(r"ratio 1:(\d+\.\d\d)", part)
    if ratio is not None:
        found["ratio"] = float(ratio.group(1))
    return found


async def test_each_ledger_line_carries_the_means_get_set_serves(experiment: Experiment) -> None:
    """The ledger and the trajectory are one function's two readers: no number differs."""
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )
    trends = await SetsRepository(experiment.db).trends(experiment.set_id)

    assert len(trends.versions) == 5
    for version in trends.versions:
        line = _ledger_line_of(rendered, version.version_label)
        served = _averages_of(line)
        assert served == {
            "shot time": round(version.avg_duration_s or 0, 1),
            "yield": round(version.avg_yield_g or 0, 1),
            "ratio": round(version.avg_ratio or 0, 2),
            "rating": round(version.avg_rating or 0, 1),
            "first drip": round(version.avg_first_drip_s or 0, 1),
        }, line


async def test_the_ledger_averages_count_only_the_shots_that_count(experiment: Experiment) -> None:
    """v2.2 has a 36.0 s shot and a discarded 12.0 s one: its mean is the first alone."""
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )
    line = _ledger_line_of(rendered, "v2.2")

    assert "2 shots (0 Keep, 1 Improve, 1 Discard)" in line
    assert _averages_of(line)["shot time"] == 36.0
    assert _averages_of(line)["first drip"] == 8.0
    assert "averages over 1 counted shot:" in line


async def test_a_version_with_shots_but_none_that_count_says_so(experiment: Experiment) -> None:
    sets = SetsRepository(experiment.db)
    await JudgementsRepository(experiment.db).upsert(
        (await sets.set_shots(experiment.set_id, version_id=experiment.v2, limit=1))[0].shot_id,
        JudgementWrite(decision="discard"),
    )

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )
    line = _ledger_line_of(rendered, "v1.1")

    assert "1 shot (0 Keep, 0 Improve, 1 Discard) · no counted shots ·" in line
    assert "averages over" not in line


async def test_a_version_with_no_shots_has_no_averages_part(experiment: Experiment) -> None:
    sets = SetsRepository(experiment.db)
    empty = await sets.add_version(experiment.set_id, SetVersionPatch(intent="Not brewed yet."))
    assert empty is not None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, empty.id)
    )
    line = _ledger_line_of(rendered, empty.version_label)

    assert " · no shots · " in line
    assert "averages" not in line
    assert "no counted shots" not in line


async def test_a_mean_over_fewer_shots_than_the_version_has_says_how_many(
    experiment: Experiment,
) -> None:
    """Both rows: every mean on the same shots says so once; mixed counts say it per mean."""
    sets = SetsRepository(experiment.db)
    shots = await sets.set_shots(experiment.set_id, version_id=experiment.v4, limit=10)
    assert len(shots) == 3
    # One of v2.1's three shots had no rating; the rest of its numbers stay on all three.
    await experiment.db.execute(
        "UPDATE shot_judgements SET rating = NULL WHERE shot_id = ?", (shots[0].shot_id,)
    )

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )
    line = _ledger_line_of(rendered, "v2.1")

    assert "rating 4.0 (over 2)" in line
    assert "shot time 33.5 s," in line and "shot time 33.5 s (over" not in line
    assert "averages over counted shots:" in line


async def test_the_budget_still_trims_with_the_averages_in_the_lines(
    experiment: Experiment,
) -> None:
    """The old end is summarised as before; what is written out keeps its means."""
    sets = SetsRepository(experiment.db)
    for number in range(LEDGER_VERSIONS + 2):
        assert await sets.add_version(
            experiment.set_id, SetVersionPatch(intent=f"Filler {number}.")
        )
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v2)
    )

    ledger = [line for line in rendered.splitlines() if line.startswith("- v")]
    assert "Not written out here:" in rendered
    # The version being argued and its comparison survive whatever their age, with means...
    assert "averages over counted shots:" in _ledger_line_of(rendered, "v1.1")
    assert "averages over counted shots:" in _ledger_line_of(rendered, "v1")
    # ...and the budget still bounds the lines: the newest few plus the two kept ones.
    assert len(ledger) == LEDGER_VERSIONS + 2
    assert "- v2.1 " not in rendered
    assert "- v2.2 " not in rendered


async def test_a_mean_over_fewer_shots_than_the_version_has_but_not_all_alike_says_each_count(
    experiment: Experiment,
) -> None:
    """Ten shots, seven counted, five of those rated: "(over 7)" and "(over 5)" side by side.

    The threshold is the version's shot count: a mean over the seven counted ones is still
    a mean over fewer than the ten it has.
    """
    sets = SetsRepository(experiment.db)
    judgements = JudgementsRepository(experiment.db)
    version = await sets.add_version(experiment.set_id, SetVersionPatch(intent="Ten pulls."))
    assert version is not None
    for index in range(10):
        shot = await _shot(
            experiment.db,
            f"0001{index:02d}",
            started_at=f"2026-04-06T08:{index:02d}:00.000Z",
            duration_ms=30_000 + index * 500,
            weight_g=36.0,
            first_drip_s=7.0,
            max_bar=9.0,
            brew_flow=1.9,
        )
        assert await sets.assign_shot(shot, version.id)
        if index >= 7:
            await judgements.upsert(shot, JudgementWrite(decision="discard"))
        elif index < 5:
            await judgements.upsert(shot, JudgementWrite(rating=4))

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, version.id)
    )
    line = _ledger_line_of(rendered, version.version_label)

    assert "10 shots" in line
    assert "shot time 31.5 s (over 7)" in line
    assert "rating 4.0 (over 5)" in line
    assert "first drip 7.0 s (over 7)" in line
    assert "averages over counted shots:" in line


async def test_counted_shots_that_recorded_nothing_say_so(experiment: Experiment) -> None:
    sets = SetsRepository(experiment.db)
    version = await sets.add_version(experiment.set_id, SetVersionPatch(intent="Blank logs."))
    assert version is not None
    shot = await ShotsRepository(experiment.db).insert(
        ShotInsert(
            device_id="000990",
            raw_slog=b"not-a-slog",
            started_at="2026-04-07T08:00:00.000Z",
            duration_ms=0,
        )
    )
    assert await sets.assign_shot(shot, version.id)

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, version.id)
    )
    line = _ledger_line_of(rendered, version.version_label)

    assert "1 shot (0 Keep, 0 Improve, 0 Discard) · counted shots recorded no values ·" in line


async def test_the_context_reads_the_counted_shots_once(
    experiment: Experiment, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original = SetsRepository.counted_shots

    async def counting(self: SetsRepository, set_id: int) -> list[CountedShot]:
        nonlocal calls
        calls += 1
        return await original(self, set_id)

    monkeypatch.setattr(SetsRepository, "counted_shots", counting)
    await opening_context(experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5))

    assert calls == 1


# ── the profile ──────────────────────────────────────────────────────

PROFILE_HEADING = "THE PROFILE v2.2 BREWS"


def _document_line(rendered: str, after: str) -> str:
    """The line of compact JSON that follows the `as stored` line under a heading."""
    lines = rendered.splitlines()
    start = lines.index(after)
    marker = next(i for i in range(start, len(lines)) if "as stored (compact JSON" in lines[i])
    return lines[marker + 1]


async def _own_profile_version(experiment: Experiment) -> int:
    version = await SetsRepository(experiment.db).get_version(experiment.v5)
    assert version is not None and version.profile_version_id is not None
    return version.profile_version_id


async def test_the_profile_the_version_brews_is_in_the_context_as_stored(
    experiment: Experiment,
) -> None:
    """Byte-equal to the profile version's `json` column, and to what `get_profile` serves."""
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )
    profile_id = await _own_profile_version(experiment)
    column = await experiment.db.fetch_value(
        "SELECT json FROM profile_versions WHERE id = ?", (profile_id,)
    )
    ctx = ToolContext(
        db=experiment.db,
        settings=SettingsService(SettingsRepository(experiment.db)),
        knowledge=KnowledgeService(experiment.db),
        scope=ToolScope.for_thread(experiment.set_id, experiment.v5),
        caller="test",
        permissions=CHAT_PERMISSIONS,
    )
    served = await registry.dispatch(ctx, "get_profile", {"profile_version_id": profile_id})

    assert served.ok, served.data
    line = _document_line(rendered, PROFILE_HEADING)
    assert line == column
    assert json.loads(line) == served.data["document"]
    assert json.dumps(served.data["document"], separators=(",", ":"), ensure_ascii=False) == column
    assert f"Profile version {profile_id}, Classic 9 bar, as stored" in rendered


async def test_the_machine_s_own_fields_and_defaults_are_not_in_the_profile(
    experiment: Experiment,
) -> None:
    """Nothing invented: no favorite, no selected, no phase temperature the author never wrote."""
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )
    line = _document_line(rendered, PROFILE_HEADING)

    for invented in ('"selected"', '"favorite"', '"id"', '"temperature":0.0', '"temperature":0,'):
        assert invented not in line, invented
    assert '"duration":3,' in line, "an integral number is written as it is stored, not 3.0"


def _first_difference(before: str, after: str) -> int:
    """The index of the first byte at which two renderings differ."""
    for index, (left, right) in enumerate(zip(before, after, strict=False)):
        if left != right:
            return index
    return min(len(before), len(after))


async def test_nothing_that_changes_between_turns_comes_before_the_profile(
    experiment: Experiment,
) -> None:
    """The cache prefix: a shot, a grade, a new version, a proposal and a revert all land after it.

    On a version whose compared version brews another profile, so both blocks are in play.
    """
    db = experiment.db
    sets = SetsRepository(db)
    version = await _other_profile_version(experiment)
    scope = ToolScope.for_thread(experiment.set_id, version.id)

    async def render() -> str:
        return await opening_context(db, scope)

    first = await render()
    block_end = first.index("THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET")
    assert first.startswith(f"THE PROFILE {version.version_label} BREWS")
    assert first.count("as stored (compact JSON") == 2, "both profiles are in the protected block"

    states = []
    shot = await _shot(
        db,
        "000700",
        started_at="2026-04-08T08:00:00.000Z",
        duration_ms=31_000,
        weight_g=36.0,
        first_drip_s=7.0,
        max_bar=9.0,
        brew_flow=1.9,
    )
    assert await sets.assign_shot(shot, version.id)
    await JudgementsRepository(db).upsert(shot, JudgementWrite(rating=3, decision="improve"))
    states.append(("a shot", await render()))
    graded = await sets.set_outcome(
        experiment.set_id,
        version.id,
        VersionOutcomeWrite(outcome="partly_held", note="Slower, but the finish is drying."),
    )
    assert graded.version is not None, graded
    states.append(("a grade", await render()))
    newer = await sets.add_version(experiment.set_id, SetVersionPatch(intent="One finer."))
    assert newer is not None
    states.append(("a new version", await render()))
    await sets.set_outcome(
        experiment.set_id,
        newer.id,
        VersionOutcomeWrite(outcome="held", note="Straight where it should be."),
    )
    result = await SetProposalsRepository(db).create(
        experiment.set_id,
        ProposalWrite(
            patch=SetVersionPatch(dose_g=18.5),
            reason="Half a gram more, to carry the finish.",
            prediction="Compared to v3.1: a touch more body and no slower.",
        ),
    )
    assert result.proposal is not None, result.refused
    states.append(("a proposal", await render()))
    await sets.rollback(
        experiment.set_id, RollbackWrite(to_version_id=version.id, note="One finer was no better")
    )
    states.append(("a revert", await render()))

    previous = first
    for name, rendered in states:
        assert rendered != previous, name
        assert _first_difference(previous, rendered) > block_end, name
        assert rendered[:block_end] == first[:block_end], name
        previous = rendered


async def test_a_version_with_no_profile_says_so(experiment: Experiment) -> None:
    sets = SetsRepository(experiment.db)
    bare = await sets.add_version(
        experiment.set_id,
        SetVersionPatch(profile_version_id=None, intent="Any profile will do for this one."),
    )
    assert bare is not None and bare.profile_version_id is None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, bare.id)
    )

    assert f"THE PROFILE {bare.version_label} BREWS" in rendered
    assert "This version names no profile" in rendered
    assert "as stored (compact JSON" not in rendered


async def _other_profile_version(experiment: Experiment) -> SetVersionRow:
    other = await make_profile_version(experiment.db, "Lever ramp", temperature=92.0)
    version = await SetsRepository(experiment.db).add_version(
        experiment.set_id,
        SetVersionPatch(
            profile_version_id=other,
            intent="Try the other profile.",
            prediction="Shot time up by 3 s against v2.1.",
            compares_to_version_id=experiment.v4,
        ),
        major=True,
    )
    assert version is not None
    return version


async def test_a_compared_version_on_another_profile_adds_that_profile_once(
    experiment: Experiment,
) -> None:
    version = await _other_profile_version(experiment)

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, version.id)
    )

    assert rendered.count('"label":"Lever ramp"') == 1
    assert rendered.count('"label":"Classic 9 bar"') == 1
    assert rendered.count("as stored (compact JSON") == 2
    assert f"THE PROFILE {version.version_label} BREWS\n" in rendered
    assert (
        "THE PROFILE v2.1 BREWS (v3 is compared against it; a different profile version)"
        in rendered
    )
    # Its own profile first, the compared one after it.
    assert rendered.index('"label":"Lever ramp"') < rendered.index('"label":"Classic 9 bar"')


async def test_a_compared_version_on_the_same_profile_adds_nothing(experiment: Experiment) -> None:
    """v2.2 is compared against v2.1: the same profile version, so one document."""
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert rendered.count("THE PROFILE ") == 1
    assert rendered.count('"label":"Classic 9 bar"') == 1


async def test_a_version_with_no_profile_still_gets_the_compared_versions_profile(
    experiment: Experiment,
) -> None:
    bare = await SetsRepository(experiment.db).add_version(
        experiment.set_id,
        SetVersionPatch(
            profile_version_id=None,
            intent="Any profile.",
            prediction="Faster than v2.1.",
            compares_to_version_id=experiment.v4,
        ),
    )
    assert bare is not None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, bare.id)
    )

    assert "This version names no profile" in rendered
    assert rendered.count('"label":"Classic 9 bar"') == 1


async def test_a_version_with_a_profile_compared_against_one_that_names_none_adds_nothing(
    experiment: Experiment,
) -> None:
    sets = SetsRepository(experiment.db)
    bare = await sets.add_version(
        experiment.set_id,
        SetVersionPatch(profile_version_id=None, intent="Any profile."),
    )
    assert bare is not None and bare.profile_version_id is None
    profiled = await sets.add_version(
        experiment.set_id,
        SetVersionPatch(
            profile_version_id=await _own_profile_version(experiment),
            intent="Back on the Classic profile.",
            prediction="Faster than the version with no profile.",
            compares_to_version_id=bare.id,
        ),
    )
    assert profiled is not None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, profiled.id)
    )

    assert rendered.count("THE PROFILE ") == 1
    assert rendered.count("as stored (compact JSON") == 1
    assert "This version names no profile" not in rendered

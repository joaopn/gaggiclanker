"""The opening context of a Set conversation, and the golden it renders to.

The golden file is `golden/set-chat-context.txt`, and it is the real check on
this module: the other tests here each pin one rule, while the golden asserts
that the whole document — the version being argued, the ledger with its dead
ends and outcomes, the spread, the evidence table, both versions' shots and the
Keep shots that are the target — reads as one thing a person would recognise.

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
    SetVersionWrite,
    SetWrite,
    VersionOutcomeWrite,
)
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.tools.scope import ToolScope
from tests.sets.conftest import make_profile_version

GOLDEN = Path(__file__).resolve().parent / "golden" / "set-chat-context.txt"


@dataclass(slots=True)
class Experiment:
    """A Set with a history: five versions, a roll back, and shots to grade."""

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

        # v3: half a gram more dose, one shot, nothing conclusive.
        version = await sets.add_version(
            set_id,
            SetVersionPatch(
                dose_g=18.5,
                intent="See whether a bigger dose slows the gusher down.",
                prediction="Shot time up by 2 s against v2.",
            ),
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

        # v4: go back to what worked. v2 and v3 are now dead ends.
        rolled = await sets.rollback(
            set_id,
            RollbackWrite(
                to_version_id=v1,
                intent="Back to the recipe with the Keep shots.",
                prediction="Back within the usual spread of v1.",
            ),
        )
        assert rolled.version is not None
        v4 = rolled.version.id
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
                    "Shot time up by 2 to 3 s against v4, yield the same, and no more bitterness."
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


def _claimed(summary: str) -> set[int]:
    """The version numbers a summary line says it stands for, spans expanded."""
    listed = summary.split("Not written out here:", 1)[1].split("(")[0]
    numbers: set[int] = set()
    for part in listed.split(","):
        span = re.fullmatch(r"\s*v(\d+) to v(\d+)\s*", part)
        one = re.fullmatch(r"\s*v(\d+)\s*", part)
        if span is not None:
            numbers |= set(range(int(span.group(1)), int(span.group(2)) + 1))
        elif one is not None:
            numbers.add(int(one.group(1)))
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


async def test_a_general_conversation_gets_no_block(experiment: Experiment) -> None:
    assert await opening_context(experiment.db, ToolScope()) == ""


async def test_it_is_about_the_thread_s_version_not_the_current_one(
    experiment: Experiment,
) -> None:
    """A conversation opened on v2 is still about v2 three versions later."""
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v2)
    )

    assert "THIS VERSION IS v2" in rendered
    assert "(a dead end" in rendered
    assert "THE EVIDENCE FOR v2 AGAINST v1" in rendered


async def _graded(experiment: Experiment) -> SetProposalsRepository:
    """Grade v5, so a change can be proposed against it at all."""
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
            prediction="Compared to v5: a touch more body and no slower.",
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
    assert "Prediction (compared to v5)" in rendered
    assert "It has changed nothing: the Set is still on v5" in rendered
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
    assert f"They accepted it; it is v{accepted.version.version_no}." in rendered


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
    assert f"the Set moved on to v{versions[0].version_no} before they did" in rendered
    assert "Propose afresh" in rendered


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

    assert "- v2 (dead end) ·" in rendered
    assert "- v3 (dead end) ·" in rendered
    assert "← this version" in rendered
    assert "← what v5 is compared against" in rendered
    assert "Track record: 1 of 3 graded predictions held" in rendered


async def test_the_evidence_and_the_spread_are_the_page_s_own(experiment: Experiment) -> None:
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "THE EVIDENCE FOR v5 AGAINST v4" in rendered
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

    assert "NOT COUNTED (discarded" in rendered


async def test_a_keep_shot_on_a_dead_end_is_not_the_gold_standard(
    experiment: Experiment,
) -> None:
    """Good on a branch nobody is brewing is not what an Improve shot is held to."""
    from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite

    on_a_dead_end = await SetsRepository(experiment.db).set_shots(experiment.set_id, version_no=2)
    assert on_a_dead_end
    await JudgementsRepository(experiment.db).upsert(
        on_a_dead_end[0].shot_id, JudgementWrite(decision="keep")
    )

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    gold = next(line for line in rendered.splitlines() if "Keep shot" in line)
    assert "v2" not in gold
    assert "on v1, v4" in gold


async def test_a_set_with_no_shots_says_the_spread_is_not_measured_and_names_the_floors(
    experiment: Experiment,
) -> None:
    """A heading with nothing under it reads as a bug rather than as "not yet"."""
    empty = await SetsRepository(experiment.db).create(
        SetWrite(name="Nothing pulled yet", bean_id=1),
        SetVersionWrite(grind_setting="20"),
        activate=False,
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
    # Each one is cut, so one essay cannot take the context with it.
    assert all(len(line) <= INSIGHT_CHARS + 4 for line in listed[:-1])
    # Newest first: the ones that supersede the others.
    assert f"Insight {INSIGHTS_SHOWN + 4}." in listed[0]


async def test_the_gold_standard_is_the_keep_shots_still_on_the_line(
    experiment: Experiment,
) -> None:
    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, experiment.v5)
    )

    assert "5 Keep shots on the line being brewed, on v1, v4" in rendered


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
    # The newest version, predicting against v5 — which is by now far enough
    # back that the budget would otherwise have summarised it away.
    argued = await sets.add_version(
        experiment.set_id,
        SetVersionPatch(
            grind_setting="20.5",
            intent="Split the difference.",
            prediction="Between v4 and v5 on shot time.",
            compares_to_version_id=experiment.v5,
        ),
    )
    assert argued is not None

    rendered = await opening_context(
        experiment.db, ToolScope.for_thread(experiment.set_id, argued.id)
    )

    assert "Not written out here:" in rendered
    assert "- v1 ·" not in rendered, "the oldest versions are summarised, not written out"
    assert f"- v{argued.version_no} ← this version ·" in rendered
    assert "- v5 ← what v" in rendered
    # The summary names what it summarised and nothing else: v5 is written out
    # three lines below it, so a span that swallowed it would be a lie.
    ledger = [line for line in rendered.splitlines() if line.startswith("- v")]
    summary = next(line for line in rendered.splitlines() if "Not written out here:" in line)
    written = {int(line.split("v", 1)[1].split(" ")[0].rstrip(":")) for line in ledger}

    assert _claimed(summary) & written == set(), summary
    assert 1 in _claimed(summary), "the oldest versions are what it stands for"


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

    assert "THIS VERSION IS v2" in rendered
    assert "- v2 (dead end) ← this version ·" in rendered
    # And the version it is compared against comes with it.
    assert "- v1 ← what v2 is compared against ·" in rendered

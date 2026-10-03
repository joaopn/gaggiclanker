"""What a run is told: which insights, in what order, and the golden prompt it renders to.

The golden file is `golden/patterns-prompt.txt`: both prompts, rendered over the fixture
world. Regenerate with `uv run pytest tests/patterns -k golden --update-golden` and read the
diff before committing it.
"""

from __future__ import annotations

from pathlib import Path

from gaggiclanker.db.repos.knowledge_insights import InsightScope
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.repos.patterns import (
    PatternProposalsRepository,
    PatternProposalWrite,
    PatternRunOutcome,
    PatternRunsRepository,
    PatternRunStart,
    PatternSource,
)
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.patterns.context import build_patterns_input
from gaggiclanker.patterns.service import PATTERNS_PROMPT, PATTERNS_USER_PROMPT, PatternsService
from tests.patterns.world import Talking

GOLDEN = Path(__file__).resolve().parent / "golden" / "patterns-prompt.txt"


async def _with_extras(talking: Talking) -> None:
    """Two more general insights and two declined proposals, written so that id order and
    alphabetical order disagree, and an archived Set: what the golden must keep in id order."""
    world = talking.world
    await world.general("Zeroing the scale first avoids a yield error.", process="washed")
    await world.general("Always preheat the group for a minute.")
    runs = PatternRunsRepository(world.db)
    proposals = PatternProposalsRepository(world.db)
    run_id = await runs.start(PatternRunStart())
    sources = [
        PatternSource(
            insight_id=talking.a_grinder, set_id=world.a, set_name="Guji daily", text="A."
        ),
        PatternSource(
            insight_id=talking.b_grinder, set_id=world.b, set_name="Yirg daily", text="B."
        ),
    ]
    await runs.finish_done(
        run_id,
        PatternRunOutcome(status="done"),
        [
            PatternProposalWrite(text="Zeta: finer is always better.", sources=sources),
            PatternProposalWrite(
                text="Alpha: coarser below 9.",
                scope=InsightScope(roast_level="light"),
                sources=sources,
            ),
        ],
    )
    for proposal in await proposals.for_run(run_id):
        await proposals.dismiss(proposal.id)
    await world.sets.archive(world.c)


async def _rendered(service: PatternsService, talking: Talking) -> str:
    await _with_extras(talking)
    given = await build_patterns_input(talking.world.db)
    prompts = PromptService(PromptsRepository(talking.world.db))
    system = await prompts.load(PATTERNS_PROMPT)
    user = await prompts.load(PATTERNS_USER_PROMPT, given.render())
    return f"=== SYSTEM ===\n{system.system}\n=== USER ===\n{user.user}"


async def test_the_rendered_input_matches_the_golden_file(
    service: PatternsService, talking: Talking, update_golden: bool
) -> None:
    rendered = await _rendered(service, talking)
    if update_golden:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(rendered + "\n", encoding="utf-8")
    assert GOLDEN.exists(), "run with --update-golden to create it"
    assert rendered + "\n" == GOLDEN.read_text(encoding="utf-8")


async def test_the_same_rows_give_the_same_input(talking: Talking) -> None:
    first = await build_patterns_input(talking.world.db)
    second = await build_patterns_input(talking.world.db)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.render() == second.render()


async def test_the_input_is_ordered_by_set_id_then_insight_id(talking: Talking) -> None:
    given = await build_patterns_input(talking.world.db)

    assert [item.id for item in given.sets] == sorted(item.id for item in given.sets)
    for item in given.sets:
        assert [i.id for i in item.insights] == sorted(i.id for i in item.insights)


async def test_only_confirmed_insights_of_sets_that_are_not_being_designed_are_read(
    talking: Talking,
) -> None:
    given = await build_patterns_input(talking.world.db)

    read = {insight.id for item in given.sets for insight in item.insights}
    assert talking.waiting not in read
    assert talking.designing not in read
    assert {talking.a_grinder, talking.a_only, talking.b_grinder, talking.c_grinder} == read
    assert talking.world.designing not in {item.id for item in given.sets}
    assert [item.id for item in given.general] == [talking.general]


async def test_a_set_is_shown_with_the_attributes_a_scope_may_name(talking: Talking) -> None:
    given = await build_patterns_input(talking.world.db)
    world = talking.world

    by_id = {item.id: item for item in given.sets}
    assert by_id[world.a].attributes == {
        "bean_id": world.bean_a,
        "roast_level": "light",
        "process": "natural",
        "origin": "Ethiopia",
        "grinder_id": world.grinder_id,
    }
    assert "profile_style" not in by_id[world.a].attributes
    assert by_id[world.c].attributes["roast_level"] == "dark"


async def test_what_an_insight_rests_on_is_told_as_then_and_now(talking: Talking) -> None:
    world = talking.world
    version = await world.sets.get(world.a)
    assert version is not None
    await world.db.execute(
        "UPDATE set_versions SET outcome = 'failed' WHERE id = ?", (version.current_version_id,)
    )

    given = await build_patterns_input(world.db)

    line = next(
        insight
        for item in given.sets
        for insight in item.insights
        if insight.id == talking.a_grinder
    )
    assert line.rests_on == ["v1 held → now failed"]
    assert "rests on v1 held → now failed" in given.render()["set_insights"]


async def test_declined_proposals_are_told_with_their_sources(talking: Talking) -> None:
    world = talking.world
    runs = PatternRunsRepository(world.db)
    proposals = PatternProposalsRepository(world.db)
    run_id = await runs.start(PatternRunStart())
    await runs.finish_done(
        run_id,
        PatternRunOutcome(status="done"),
        [
            PatternProposalWrite(
                text="The Niche channels under 9.",
                sources=[
                    PatternSource(
                        insight_id=talking.a_grinder,
                        set_id=world.a,
                        set_name="Guji daily",
                        text="A.",
                    ),
                    PatternSource(
                        insight_id=talking.b_grinder,
                        set_id=world.b,
                        set_name="Yirg daily",
                        text="B.",
                    ),
                ],
            )
        ],
    )
    (waiting,) = await proposals.for_run(run_id)
    await proposals.dismiss(waiting.id)

    given = await build_patterns_input(world.db)

    assert [item.id for item in given.declined] == [waiting.id]
    block = given.render()["declined"]
    assert "The Niche channels under 9." in block
    assert f'Guji daily #{talking.a_grinder} "A."' in block


async def test_archived_sets_are_read_and_designing_ones_are_not(talking: Talking) -> None:
    world = talking.world
    await world.sets.archive(world.c)

    given = await build_patterns_input(world.db)

    assert [item.id for item in given.sets] == [world.a, world.b, world.c]
    assert next(item for item in given.sets if item.id == world.c).archived is True
    assert "(archived)" in given.render()["set_insights"]
    assert world.designing not in {item.id for item in given.sets}


async def test_general_and_declined_blocks_are_in_id_order_whatever_the_words(
    talking: Talking,
) -> None:
    await _with_extras(talking)

    given = await build_patterns_input(talking.world.db)

    assert [item.id for item in given.general] == sorted(item.id for item in given.general)
    assert [item.text for item in given.general][-1] == "Always preheat the group for a minute."
    assert [item.text for item in given.declined] == [
        "Zeta: finer is always better.",
        "Alpha: coarser below 9.",
    ]

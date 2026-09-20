"""Each tool, once, against a seeded archive.

Through the dispatcher rather than by calling the functions, because the
dispatcher is what the chat and MCP both use and a tool that works when called
directly but whose schema rejects its own arguments is a tool that does not
work.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
)
from gaggiclanker.tools.registry import READ_ONLY, ToolContext, registry
from gaggiclanker.tools.sql import ALLOWED_VIEWS
from tests.analyzer.conftest import Fixture


async def call(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert outcome.ok, outcome.data
    return outcome.data


async def refuse(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, name, arguments)
    assert not outcome.ok
    return outcome.data


# -- reading ---------------------------------------------------------------


async def test_describe_schema_lists_every_allowed_view_with_columns(ctx: ToolContext) -> None:
    data = await call(ctx, "describe_schema")

    assert {view["name"] for view in data["views"]} == set(ALLOWED_VIEWS)
    assert all(view["columns"] for view in data["views"])
    assert data["examples"], "a model writing SQL needs worked examples, not only columns"


async def test_the_schema_examples_all_run(ctx: ToolContext) -> None:
    """A wrong example is worse than none: the model copies it."""
    for example in (await call(ctx, "describe_schema"))["examples"]:
        outcome = await registry.dispatch(ctx, "query_shots", {"sql": example["sql"]})
        assert outcome.ok, (example["sql"], outcome.data)


async def test_get_shot_summary_carries_the_verdict_and_the_diagnostics(
    ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(ctx, "get_shot", shot_id=archive.shots[0])

    assert data["shot"]["shot_id"] == archive.shots[0]
    assert data["shot"]["set_name"]
    assert data["diagnostics"] is not None
    assert data["phases"] is None, "summary is summary"


async def test_get_shot_curve_is_downsampled_across_the_whole_shot(
    ctx: ToolContext, archive: Fixture
) -> None:
    # The last shot is the one the fixture gives samples to.
    data = await call(ctx, "get_shot", shot_id=archive.shots[-1], detail="curve")

    curve = data["curve"]
    assert curve
    # Not the first N samples: a LIMIT would return the pre-infusion and none
    # of the shot, which is the bug the modulo exists to avoid.
    assert curve[-1]["t_s"] > curve[0]["t_s"]


async def test_get_shot_says_so_when_there_is_no_such_shot(ctx: ToolContext) -> None:
    data = await refuse(ctx, "get_shot", shot_id=999_999)
    assert "No shot" in data["detail"]


async def test_compare_shots_reports_only_what_differs(ctx: ToolContext, archive: Fixture) -> None:
    data = await call(ctx, "compare_shots", shot_ids=archive.shots[:3])

    assert len(data["shots"]) == 3
    fields = {row["field"] for row in data["differences"]}
    assert fields, "three shots from different versions differ in something"
    assert "set_name" not in fields, "they are all in the same Set"


async def test_compare_shots_refuses_more_than_four(ctx: ToolContext, archive: Fixture) -> None:
    data = await refuse(ctx, "compare_shots", shot_ids=archive.shots[:5])
    assert data["error"] == "invalid_arguments"


async def test_get_set_returns_the_versions_and_the_trajectory(
    ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(ctx, "get_set", set_id=archive.set_id)

    assert data["set"]["id"] == archive.set_id
    assert len(data["versions"]) >= 1
    assert isinstance(data["trajectory"], list)


async def test_get_set_falls_back_to_the_conversation_scope(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """ "How is it going?" has an answer when the conversation is about a Set."""
    data = await call(set_ctx, "get_set")
    assert data["set"]["id"] == archive.set_id


async def test_get_set_says_what_to_do_when_there_is_no_scope(ctx: ToolContext) -> None:
    data = await refuse(ctx, "get_set")
    assert "list_sets" in data["detail"]


async def test_the_catalogue_tools_list_what_was_seeded(ctx: ToolContext) -> None:
    assert (await call(ctx, "list_sets"))["count"] == 1
    assert (await call(ctx, "list_beans"))["count"] == 1
    assert (await call(ctx, "list_grinders"))["count"] == 1
    assert (await call(ctx, "list_profiles"))["count"] >= 1


async def test_list_grinders_carries_the_step_unit(ctx: ToolContext) -> None:
    """Advice is given in the grinder's own units, so the unit has to be visible."""
    grinders = (await call(ctx, "list_grinders"))["items"]
    assert grinders[0]["step_unit"]


async def test_search_knowledge_returns_citable_hits(ctx: ToolContext) -> None:
    data = await call(ctx, "search_knowledge", query="channeling", k=3)

    assert data["hits"]
    assert all(hit["heading_path"] for hit in data["hits"])


async def test_get_knowledge_chunk_expands_a_citation(ctx: ToolContext) -> None:
    path = (await call(ctx, "search_knowledge", query="grind", k=1))["hits"][0]["heading_path"]

    data = await call(ctx, "get_knowledge_chunk", heading_path=path)

    assert data["heading_path"] == path
    assert data["body"]


async def test_an_unknown_citation_points_at_the_search(ctx: ToolContext) -> None:
    data = await refuse(ctx, "get_knowledge_chunk", heading_path="nope#nope")
    assert "search_knowledge" in data["detail"]


async def test_get_rules_filters_by_category_and_by_applies(ctx: ToolContext) -> None:
    everything = await call(ctx, "get_rules")
    assert everything["rules"]

    narrowed = await call(ctx, "get_rules", category="temperature_by_roast")
    assert narrowed["rules"]
    assert {rule["category"] for rule in narrowed["rules"]} == {"temperature_by_roast"}

    selected = await call(ctx, "get_rules", applies=["roast_level:light"])
    assert len(selected["rules"]) <= len(everything["rules"])


async def test_get_insights_returns_only_confirmed_ones_by_default(
    ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(ctx, "get_insights")
    assert all(insight["confirmed"] for insight in data["insights"])


# -- proposing -------------------------------------------------------------


#: A prediction long enough to be one. The tool refuses the absence of a
#: prediction, not a bad one, so every happy-path call here carries a real
#: sentence rather than twenty characters of filler.
PREDICTION = "Compared to v1: two to four seconds longer and less sour."


async def test_propose_set_version_creates_a_proposal_and_no_version(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    sets = SetsRepository(archive.db)
    before = await sets.current_version(archive.set_id)
    assert before is not None

    data = await call(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer, to chase the sour finish.",
        grind_setting="20",
        prediction=PREDICTION,
    )

    assert data["status"] == "proposed"
    assert data["changed"] == ["the grind"]
    assert data["change_summary"].startswith("Grind ")
    assert data["prediction"] == PREDICTION
    assert data["compares_to_version_no"] == before.version_no
    # The whole point: the Set is where it was, and the answer says so.
    assert "Nothing has changed yet" in data["note"]
    after = await sets.current_version(archive.set_id)
    assert after is not None and after.id == before.id

    waiting = await SetProposalsRepository(archive.db).waiting(archive.set_id)
    assert waiting is not None and waiting.id == data["proposal_id"]
    assert waiting.thread_id is None, "this context names no conversation"


async def test_a_proposal_records_the_conversation_it_came_from(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """So the experiment log can lead back to where the change was argued."""
    cursor = await archive.db.execute(
        "INSERT INTO chat_threads (title, set_id) VALUES (?, ?)", ("About v1", archive.set_id)
    )
    set_ctx.thread_id = int(cursor.lastrowid or 0)

    data = await call(
        set_ctx,
        "propose_set_version",
        reason="Half a gram more.",
        dose_g=18.5,
        prediction=PREDICTION,
    )

    waiting = await SetProposalsRepository(archive.db).waiting(archive.set_id)
    assert waiting is not None
    assert waiting.id == data["proposal_id"]
    assert waiting.thread_id == set_ctx.thread_id


async def test_propose_set_version_refuses_a_proposal_with_no_prediction(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await refuse(
        set_ctx, "propose_set_version", reason="Two clicks finer.", grind_setting="20"
    )
    assert "without a prediction" in data["detail"]
    assert await SetProposalsRepository(archive.db).waiting(archive.set_id) is None


async def test_a_prediction_too_short_to_be_one_is_the_same_refusal(
    set_ctx: ToolContext,
) -> None:
    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer.",
        grind_setting="20",
        prediction="   better   ",
    )
    assert "without a prediction" in data["detail"]


async def test_propose_set_version_refuses_a_proposal_that_changes_nothing(
    set_ctx: ToolContext,
) -> None:
    """Another shot on the same recipe is an answer in words, not a version."""
    data = await refuse(set_ctx, "propose_set_version", reason="because", prediction=PREDICTION)
    assert "has to change something" in data["detail"]
    assert "another shot on the same recipe" in data["detail"]


async def test_the_grind_text_and_its_number_are_one_change(set_ctx: ToolContext) -> None:
    data = await call(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer.",
        grind_setting="20",
        grind_value=20,
        prediction=PREDICTION,
    )
    assert data["changed"] == ["the grind"]


async def test_two_changes_are_refused_without_a_combined_reason(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Finer and a bit more coffee.",
        grind_setting="20",
        dose_g=18.5,
        prediction=PREDICTION,
    )
    assert "the dose and the grind at once" in data["detail"]
    assert "combined_reason" in data["detail"]
    assert await SetProposalsRepository(archive.db).waiting(archive.set_id) is None


async def test_two_changes_with_a_combined_reason_are_accepted_with_a_warning(
    set_ctx: ToolContext,
) -> None:
    data = await call(
        set_ctx,
        "propose_set_version",
        reason="Finer and a bit more coffee.",
        grind_setting="20",
        dose_g=18.5,
        prediction=PREDICTION,
        combined_reason="A finer grind on this basket chokes at the old dose, so both move.",
    )
    assert data["changed"] == ["the dose", "the grind"]
    assert "cannot separate them" in data["note"]


async def test_a_second_proposal_is_refused_and_told_about_the_first(
    set_ctx: ToolContext,
) -> None:
    await call(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer, to chase the sour finish.",
        grind_setting="20",
        prediction=PREDICTION,
    )
    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Or half a gram more.",
        dose_g=18.5,
        prediction=PREDICTION,
    )
    assert "already waiting" in data["detail"]
    assert "the grind" in data["detail"]
    assert "chase the sour finish" in data["detail"]


async def test_an_ungraded_prediction_blocks_the_next_proposal(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    sets = SetsRepository(archive.db)
    # A version somebody predicted something about and nobody has graded: the
    # state the whole rule is about.
    current = await sets.add_version(
        archive.set_id,
        SetVersionPatch.model_validate(
            {
                "intent": "one click finer",
                "grind_setting": "21",
                "prediction": "Expect two seconds longer and less sour.",
                "compares_to_version_id": None,
            }
        ),
    )
    assert current is not None and current.outcome_state == "open"

    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer.",
        grind_setting="20",
        prediction=PREDICTION,
    )
    assert f"v{current.version_no}'s prediction has not been graded" in data["detail"]
    assert "another shot on the same recipe" in data["detail"]


async def test_a_profile_nobody_has_is_refused_where_the_change_is_made(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """Refused at propose time, not discovered when the person presses Accept."""
    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Switch to the hotter profile.",
        profile_version_id=987_654,
        prediction=PREDICTION,
    )
    assert "not one this archive knows" in data["detail"]
    assert "list_profiles" in data["detail"]
    assert await SetProposalsRepository(archive.db).waiting(archive.set_id) is None


async def test_a_combined_reason_too_short_to_be_one_does_not_buy_two_changes(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """Twenty characters, the same floor a prediction has."""
    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Finer and a bit more coffee.",
        grind_setting="20",
        dose_g=18.5,
        prediction=PREDICTION,
        combined_reason="both",
    )
    assert "the dose and the grind at once" in data["detail"]
    assert "at least 20 characters" in data["detail"]
    assert await SetProposalsRepository(archive.db).waiting(archive.set_id) is None

    # Nineteen is still not enough, and twenty is.
    assert not (
        await registry.dispatch(
            set_ctx,
            "propose_set_version",
            {
                "reason": "Finer and a bit more coffee.",
                "grind_setting": "20",
                "dose_g": 18.5,
                "prediction": PREDICTION,
                "combined_reason": "x" * 19,
            },
        )
    ).ok
    assert (
        await registry.dispatch(
            set_ctx,
            "propose_set_version",
            {
                "reason": "Finer and a bit more coffee.",
                "grind_setting": "20",
                "dose_g": 18.5,
                "prediction": PREDICTION,
                "combined_reason": "y" * 20,
            },
        )
    ).ok


async def test_a_conversation_of_another_set_cannot_be_named_as_the_room(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """The thread comes from the runner, not the model, and is still checked."""
    other = await SetsRepository(archive.db).create(
        SetWrite(name="Another coffee", bean_id=archive.bean_id), SetVersionWrite(dose_g=18)
    )
    cursor = await archive.db.execute(
        "INSERT INTO chat_threads (title, set_id) VALUES (?, ?)", ("Elsewhere", other.id)
    )
    set_ctx.thread_id = int(cursor.lastrowid or 0)

    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer.",
        grind_setting="20",
        prediction=PREDICTION,
    )
    assert "not one of this Set's" in data["detail"]
    assert "Another coffee" not in data["detail"]
    assert await SetProposalsRepository(archive.db).waiting(archive.set_id) is None


async def test_a_comparison_outside_this_set_is_refused(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    other = await SetsRepository(archive.db).create(
        SetWrite(name="Another coffee", bean_id=archive.bean_id),
        SetVersionWrite(dose_g=18),
    )
    theirs = await SetsRepository(archive.db).current_version(other.id)
    assert theirs is not None

    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer.",
        grind_setting="20",
        prediction=PREDICTION,
        compares_to_version_id=theirs.id,
    )
    assert "not a version of this Set" in data["detail"]
    # It says nothing about whether that version exists anywhere else.
    assert "Another coffee" not in data["detail"]


async def test_propose_set_version_takes_no_temperature_and_says_where_it_went(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """A temperature argument is a refusal, not a silently dropped field.

    The machine brews at the profile's temperature, so there is nothing on a Set
    version for this to write. The schema refuses it (every tool model forbids
    extras) and the description sends the model to the tool that can actually
    change a temperature — a profile draft somebody approves.
    """
    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="a degree hotter",
        target_temperature_c=94,
    )
    assert "target_temperature_c" in str(data)

    spec = next(item for item in registry.specs() if item.name == "propose_set_version")
    assert "temperature" not in str(spec.input_schema()["properties"])
    assert "draft_profile" in spec.description

    # And the archive is untouched: a refused call writes nothing.
    current = await SetsRepository(archive.db).current_version(archive.set_id)
    assert current is not None and current.version_no == 1


async def test_record_insight_lands_unconfirmed_and_sourced_to_the_chat(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(
        set_ctx,
        "record_insight",
        text="This grinder wants two clicks finer for anything anaerobic.",
        evidence_shot_ids=archive.shots[:2],
        grinder_id=archive.grinder_id,
    )

    stored = await InsightsRepository(archive.db).get(data["insight_id"])
    assert stored is not None
    assert stored.source == "chat"
    assert stored.confirmed is False, "nothing unconfirmed reaches a prompt"
    assert stored.evidence_shot_ids == archive.shots[:2]


async def test_a_recorded_insight_does_not_reach_the_next_prompt(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """The loop tier 3 exists to break: propose, then be believed next turn."""
    await call(set_ctx, "record_insight", text="Always go finer.", grinder_id=archive.grinder_id)

    listed = await call(set_ctx, "get_insights")

    assert "Always go finer." not in [insight["text"] for insight in listed["insights"]]


async def test_the_propose_tools_are_refused_for_a_read_only_caller(
    set_ctx: ToolContext,
) -> None:
    """The permission class, not the scope: these three are in this scope."""
    read_only = ToolContext(
        db=set_ctx.db,
        settings=set_ctx.settings,
        scope=set_ctx.scope,
        caller="test",
        permissions=READ_ONLY,
    )

    for name in ("propose_set_version", "draft_profile", "record_insight"):
        outcome = await registry.dispatch(read_only, name, {})
        assert outcome.status == "refused", name
        assert "permission class" in outcome.error, name


# -- the tools that need the running application ---------------------------


async def test_run_analysis_is_propose_class_because_it_spends_money(
    ctx: ToolContext,
) -> None:
    """It creates nothing to confirm, and it is still not a read.

    The permission class is what a caller is handed, and handing a read-only
    agent a button that queues provider calls is not handing it a read.
    """
    spec = registry.get("run_analysis")
    assert spec is not None
    assert spec.permission == "propose"
    assert "run_analysis" not in {tool.name for tool in registry.specs(READ_ONLY)}


async def test_run_analysis_shares_the_analysis_rate_limit(
    ctx: ToolContext, archive: Fixture
) -> None:
    """Going through a tool must not be a way around the route's own limit.

    Called rather than dispatched, unlike everything else in this file: no
    conversation offers `run_analysis` any more — a Set's chat grades its own
    prediction and a general one is not about a shot — so the dispatcher would
    refuse it for being out of scope before the limiter it is about. That
    refusal is asserted in `tests/tools/test_scope.py`; this is the tool.
    """
    from gaggiclanker.infra.ratelimit import ANALYSIS_RATE_LIMIT, RateLimiter
    from gaggiclanker.tools.builtin import RunAnalysisInput, run_analysis

    ctx.rate_limits = RateLimiter()
    # No analyzer is wired, so every permitted call stops at "needs the running
    # application" — which is after the limiter, and is the point.
    for _ in range(ANALYSIS_RATE_LIMIT):
        with pytest.raises(ValueError, match="running gaggiclanker application"):
            await run_analysis(ctx, RunAnalysisInput(shot_id=archive.shots[0]))

    with pytest.raises(ValueError, match="Rate limit reached"):
        await run_analysis(ctx, RunAnalysisInput(shot_id=archive.shots[0]))


@pytest.mark.parametrize("name", ["starting_point", "draft_profile"])
async def test_a_tool_that_needs_a_service_says_so_rather_than_crashing(
    ctx: ToolContext, archive: Fixture, name: str
) -> None:
    """A context built without the service a tool needs has to say so readably."""
    arguments = (
        {"bean_id": archive.bean_id}
        if name == "starting_point"
        else {"base_version_id": archive.profile_version_id, "patch": {}, "reason": "x"}
    )

    data = await refuse(ctx, name, **arguments)

    assert "running gaggiclanker application" in data["detail"]

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
from gaggiclanker.db.repos.sets import SetsRepository
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
    ctx: ToolContext, archive: Fixture
) -> None:
    """ "How is it going?" has an answer when the thread is scoped."""
    data = await call(ctx, "get_set")
    assert data["set"]["id"] == archive.set_id


async def test_get_set_says_what_to_do_when_there_is_no_scope(ctx: ToolContext) -> None:
    ctx.set_id = None
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


async def test_propose_set_version_creates_a_chat_version(
    ctx: ToolContext, archive: Fixture
) -> None:
    before = await SetsRepository(archive.db).current_version(archive.set_id)
    assert before is not None

    data = await call(
        ctx,
        "propose_set_version",
        reason="Two clicks finer, to chase the sour finish.",
        grind_setting="20",
    )

    assert data["version"]["origin"] == "chat"
    assert data["version"]["version_no"] == before.version_no + 1
    assert data["changed"] == ["grind_setting"]
    # Everything not named is inherited, which is what makes a version a delta.
    assert data["version"]["dose_g"] == before.dose_g


async def test_propose_set_version_refuses_a_version_that_changes_nothing(
    ctx: ToolContext,
) -> None:
    data = await refuse(ctx, "propose_set_version", reason="because")
    assert "change something" in data["detail"]


async def test_record_insight_lands_unconfirmed_and_sourced_to_the_chat(
    ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(
        ctx,
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
    ctx: ToolContext, archive: Fixture
) -> None:
    """The loop tier 3 exists to break: propose, then be believed next turn."""
    await call(ctx, "record_insight", text="Always go finer.", grinder_id=archive.grinder_id)

    listed = await call(ctx, "get_insights")

    assert "Always go finer." not in [insight["text"] for insight in listed["insights"]]


async def test_the_propose_tools_are_refused_for_a_read_only_caller(
    ctx: ToolContext,
) -> None:
    read_only = ToolContext(db=ctx.db, settings=ctx.settings, caller="test", permissions=READ_ONLY)

    for name in ("propose_set_version", "draft_profile", "record_insight"):
        outcome = await registry.dispatch(read_only, name, {})
        assert outcome.status == "refused", name


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
    """Going through a tool must not be a way around the route's own limit."""
    from gaggiclanker.infra.ratelimit import ANALYSIS_RATE_LIMIT, RateLimiter

    ctx.rate_limits = RateLimiter()
    # No analyzer is wired, so every permitted call stops at "needs the running
    # application" — which is after the limiter, and is the point.
    for _ in range(ANALYSIS_RATE_LIMIT):
        data = await refuse(ctx, "run_analysis", shot_id=archive.shots[0])
        assert "running gaggiclanker application" in data["detail"]

    data = await refuse(ctx, "run_analysis", shot_id=archive.shots[0])

    assert "Rate limit reached" in data["detail"]


@pytest.mark.parametrize("name", ["run_analysis", "draft_profile"])
async def test_a_tool_that_needs_a_service_says_so_rather_than_crashing(
    ctx: ToolContext, archive: Fixture, name: str
) -> None:
    """A context built without the service a tool needs has to say so readably."""
    arguments = (
        {"shot_id": archive.shots[0]}
        if name == "run_analysis"
        else {"base_version_id": archive.profile_version_id, "patch": {}, "reason": "x"}
    )

    data = await refuse(ctx, name, **arguments)

    assert "running gaggiclanker application" in data["detail"]

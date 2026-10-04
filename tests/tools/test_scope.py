"""What each kind of conversation has, and what it is refused.

The exact lists are pinned here on purpose. They are a design decision — a Set's
chat sees one Set and a general one changes no Set — and a tool that quietly
joined or left a list would be that decision changed by accident. A new tool
fails this file until somebody says which conversations have it.

The refusals are the other half, and the one that matters most is the shot: a
shot of another Set and a shot that does not exist have to be refused in the
same words, or a conversation limited to one Set could walk the archive by
reading the differences.
"""

from __future__ import annotations

import pytest

from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import DESIGN_RULE, DESIGN_TOOLS, GENERAL_TOOLS, SET_TOOLS, ToolScope
from tests.review.conftest import Fixture


def test_a_set_conversation_has_exactly_these_tools() -> None:
    assert ToolScope.for_thread(3).tools == frozenset(
        {
            "compare_shots",
            "draft_profile",
            "get_insights",
            "get_knowledge_chunk",
            "get_profile",
            "get_rules",
            "get_set",
            "get_shot",
            "get_shot_extended",
            "get_shot_full",
            "list_profiles",
            "list_set_shots",
            "propose_insight_deletion",
            "propose_outcome",
            "propose_set_version",
            "record_insight",
            "search_knowledge",
        }
    )


def test_a_general_conversation_has_exactly_these_tools() -> None:
    assert ToolScope().tools == frozenset(
        {
            "compare_shots",
            "describe_schema",
            "draft_profile",
            "get_insights",
            "get_knowledge_chunk",
            "get_profile",
            "get_rules",
            "get_set",
            "get_shot",
            "get_shot_extended",
            "get_shot_full",
            "list_beans",
            "list_grinders",
            "list_profiles",
            "list_sets",
            "query_shots",
            "search_knowledge",
        }
    )


def test_a_conversation_designing_a_set_has_exactly_these_tools() -> None:
    assert ToolScope.for_thread(3, designing=True).tools == frozenset(
        {
            "get_insights",
            "get_knowledge_chunk",
            "get_profile",
            "get_rules",
            "get_set",
            "list_profiles",
            "propose_initial_recipe",
            "search_knowledge",
        }
    )


def test_no_scope_names_a_tool_that_does_not_exist() -> None:
    registered = set(registry.names())

    assert SET_TOOLS <= registered
    assert GENERAL_TOOLS <= registered
    assert DESIGN_TOOLS <= registered
    # Every registered tool is offered somewhere: a tool no conversation has
    # is dead code that the stdio server would still register.
    assert registered == SET_TOOLS | GENERAL_TOOLS | DESIGN_TOOLS


def test_the_design_flag_means_nothing_outside_a_set() -> None:
    assert ToolScope.for_thread(None, designing=True) == ToolScope()
    assert ToolScope(designing=True).tools != DESIGN_TOOLS


async def test_the_scope_is_resolved_from_the_set_s_own_flag(archive: Fixture) -> None:
    """The one way a turn's scope is built: the thread's columns and the Set's flag."""
    from gaggiclanker.db.repos.sets import DesignBrief, SetsRepository, SetWrite

    designed = await SetsRepository(archive.db).create_design(
        SetWrite(name="Designed", bean_id=archive.bean_id), DesignBrief()
    )

    assert (await ToolScope.resolve(archive.db, designed.id)).tools == DESIGN_TOOLS
    assert (await ToolScope.resolve(archive.db, archive.set_id)).tools == SET_TOOLS
    assert await ToolScope.resolve(archive.db, None) == ToolScope()


def test_a_set_conversation_cannot_reach_the_archive() -> None:
    """The four that would be a way around the scope in one call."""
    scope = ToolScope.for_thread(3)

    for name in ("query_shots", "describe_schema", "list_sets", "list_beans"):
        assert not scope.allows(name), name


def test_a_general_conversation_cannot_change_a_set() -> None:
    scope = ToolScope()

    for name in ("propose_set_version", "record_insight"):
        assert not scope.allows(name), name


@pytest.mark.parametrize("name", ["starting_point", "get_starting_point"])
def test_no_conversation_can_ask_for_a_starting_point(name: str) -> None:
    """A run started from a chat had no screen that could accept it.

    A bag nobody has brewed is designed in a Set of its own, and the New Set
    dialog's suggestions accept the run they start. So neither tool exists, in
    any scope or over MCP.
    """
    assert registry.get(name) is None
    for scope in (ToolScope(), ToolScope.for_thread(3), ToolScope.for_thread(3, designing=True)):
        assert name not in scope.tools


def test_the_kind_follows_the_thread_s_two_columns() -> None:
    assert ToolScope.for_thread(None, None).kind == "general"
    assert ToolScope.for_thread(3, 9) == ToolScope(kind="set", set_id=3, set_version_id=9)


def test_a_refusal_names_the_tools_that_are_here_and_no_other_set() -> None:
    message = ToolScope.for_thread(3).refusal("query_shots")

    assert "list_set_shots" in message
    assert "Set" in message
    # Nothing about which other Sets exist, because that is what it is refusing.
    assert "list_sets" not in message


# -- the dispatcher, the schemas and the refusals --------------------------


async def test_the_dispatcher_refuses_a_tool_outside_the_scope(set_ctx: ToolContext) -> None:
    outcome = await registry.dispatch(set_ctx, "query_shots", {"sql": "SELECT 1"})

    assert outcome.status == "refused"
    assert "one Set" in outcome.error


async def test_an_out_of_scope_tool_is_refused_before_its_arguments_are_read(
    set_ctx: ToolContext,
) -> None:
    """The refusal must not depend on the call being well formed."""
    outcome = await registry.dispatch(set_ctx, "list_sets", {"nonsense": True})

    assert outcome.status == "refused"


async def test_an_unknown_tool_is_offered_this_conversation_s_list(
    set_ctx: ToolContext,
) -> None:
    outcome = await registry.dispatch(set_ctx, "delete_everything", {})

    assert outcome.status == "refused"
    assert "list_set_shots" in outcome.error
    assert "query_shots" not in outcome.error


async def test_the_schemas_sent_to_a_provider_are_the_scope_s(set_ctx: ToolContext) -> None:
    names = {schema["function"]["name"] for schema in registry.openai_schemas(scope=set_ctx.scope)}
    anthropic = {schema["name"] for schema in registry.anthropic_schemas(scope=set_ctx.scope)}

    assert names == SET_TOOLS
    assert anthropic == SET_TOOLS


async def test_get_set_refuses_another_set_without_saying_whether_it_exists(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    outcome = await registry.dispatch(set_ctx, "get_set", {"set_id": archive.set_id + 1})

    assert not outcome.ok
    assert "can see no other" in outcome.data["detail"]
    assert "No Set" not in outcome.data["detail"]


async def test_propose_set_version_refuses_another_set(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    outcome = await registry.dispatch(
        set_ctx,
        "propose_set_version",
        {"set_id": archive.set_id + 1, "reason": "finer", "grind_setting": "18"},
    )

    assert not outcome.ok
    assert "can see no other" in outcome.data["detail"]


@pytest.mark.parametrize("name", ["get_shot", "get_shot_extended", "get_shot_full"])
async def test_a_shot_tool_refuses_a_shot_of_another_set_and_one_that_is_not_there(
    set_ctx: ToolContext, archive: Fixture, other_set_shot: int, name: str
) -> None:
    elsewhere = await registry.dispatch(set_ctx, name, {"shot_id": other_set_shot})
    missing = await registry.dispatch(set_ctx, name, {"shot_id": 999_999})

    assert not elsewhere.ok
    assert not missing.ok
    # Word for word the same but for the id: telling them apart would be an
    # existence oracle over the whole archive.
    assert elsewhere.data["detail"].replace(str(other_set_shot), "N") == missing.data[
        "detail"
    ].replace("999999", "N")


async def test_compare_shots_refuses_the_moment_one_is_not_this_set_s(
    set_ctx: ToolContext, archive: Fixture, other_set_shot: int
) -> None:
    elsewhere = await registry.dispatch(
        set_ctx, "compare_shots", {"shot_ids": [archive.shots[0], other_set_shot]}
    )
    missing = await registry.dispatch(
        set_ctx, "compare_shots", {"shot_ids": [archive.shots[0], 999_999]}
    )

    assert not elsewhere.ok
    assert "not a shot of this Set" in elsewhere.data["detail"]
    assert elsewhere.data["detail"].replace(str(other_set_shot), "N") == missing.data[
        "detail"
    ].replace("999999", "N")


@pytest.mark.parametrize("name", ["get_shot", "get_shot_extended", "get_shot_full"])
async def test_a_shot_tool_reads_this_set_s_shot(
    set_ctx: ToolContext, archive: Fixture, name: str
) -> None:
    outcome = await registry.dispatch(set_ctx, name, {"shot_id": archive.shots[0]})

    assert outcome.ok, outcome.data
    assert outcome.data["text"].startswith(f"shot {archive.shots[0]}\n")


async def test_a_general_conversation_still_reads_any_shot(
    ctx: ToolContext, other_set_shot: int
) -> None:
    outcome = await registry.dispatch(ctx, "get_shot", {"shot_id": other_set_shot})

    assert outcome.ok, outcome.data


async def test_a_general_conversation_says_a_missing_shot_is_missing(ctx: ToolContext) -> None:
    outcome = await registry.dispatch(ctx, "get_shot", {"shot_id": 999_999})

    assert not outcome.ok
    assert "No shot 999999 in the archive" in outcome.data["detail"]


# -- insights, in and out --------------------------------------------------


async def test_get_insights_in_a_set_chat_answers_only_this_set_s_confirmed_ones(
    set_ctx: ToolContext, archive: Fixture, other_set_insights: dict[str, int]
) -> None:
    data = (await registry.dispatch(set_ctx, "get_insights", {})).data

    texts = {insight["text"] for insight in data["insights"]}
    assert "This Set's bean likes it finer." in texts
    # Another bean's, another grinder's, and anything nobody has confirmed.
    assert "Another bean entirely." not in texts
    assert "Another grinder entirely." not in texts
    assert "Nobody has confirmed this." not in texts
    assert all(insight["confirmed"] for insight in data["insights"])


async def test_get_insights_refuses_to_list_the_unconfirmed_ones_in_a_set_chat(
    set_ctx: ToolContext, archive: Fixture, other_set_insights: dict[str, int]
) -> None:
    """The flag was a way to read every proposal about every other coffee."""
    outcome = await registry.dispatch(set_ctx, "get_insights", {"include_unconfirmed": True})

    assert not outcome.ok
    assert "nothing unconfirmed is evidence" in outcome.data["detail"]
    assert "Another bean entirely" not in str(outcome.data)


async def test_get_insights_hands_back_no_shot_id_of_another_set(
    set_ctx: ToolContext, archive: Fixture, other_set_insights: dict[str, int], other_set_shot: int
) -> None:
    """An insight scoped by bean may rest on another Set's shots; those ids stay there.

    Otherwise the ids themselves are the existence oracle the shot refusals
    close: `get_shot` would refuse them, and being handed them is already the
    answer.
    """
    data = (await registry.dispatch(set_ctx, "get_insights", {})).data

    every_id = {shot for insight in data["insights"] for shot in insight["evidence_shot_ids"]}
    assert other_set_shot not in every_id
    assert every_id <= set(archive.shots)
    assert every_id, "the evidence of this Set's own shots still comes through"


async def test_a_general_chat_still_sees_every_insight(
    ctx: ToolContext, other_set_insights: dict[str, int]
) -> None:
    data = (await registry.dispatch(ctx, "get_insights", {"include_unconfirmed": True})).data

    texts = {insight["text"] for insight in data["insights"]}
    assert {"Another bean entirely.", "Nobody has confirmed this."} <= texts


async def test_record_insight_refuses_another_set_s_shot_as_evidence(
    set_ctx: ToolContext, archive: Fixture, other_set_shot: int
) -> None:
    outcome = await registry.dispatch(
        set_ctx,
        "record_insight",
        {"text": "Learned something.", "evidence_shot_ids": [other_set_shot]},
    )

    assert not outcome.ok
    assert "not a shot of this Set" in outcome.data["detail"]


@pytest.mark.parametrize(
    "scope",
    [
        {"bean_id": 1},
        {"grinder_id": 1},
        {"roast_level": "light"},
        {"process": "natural"},
        {"origin": "Ethiopia"},
        {"profile_style": "bloom"},
    ],
)
async def test_record_insight_refuses_any_attribute_scope(
    set_ctx: ToolContext, archive: Fixture, scope: dict[str, object]
) -> None:
    """An insight written here belongs to this Set, so it has no scope to set.

    Not even this Set's own bean: the scope is what used to carry an insight to
    every other Set on the same bean and grinder.
    """
    outcome = await registry.dispatch(
        set_ctx, "record_insight", {"text": "Learned something.", **scope}
    )

    assert not outcome.ok
    assert "belongs to this Set" in outcome.data["detail"]
    assert "reaches this Set's later conversations only" in outcome.data["detail"]
    assert await InsightsRepository(archive.db).own(archive.set_id, include_dismissed=True) == []


async def test_record_insight_names_no_scope_in_the_schema_the_model_is_shown() -> None:
    spec = registry.get("record_insight")
    assert spec is not None
    assert set(spec.input_model.model_json_schema()["properties"]) == {
        "text",
        "evidence_shot_ids",
        "rests_on_versions",
        "replaces_insight_id",
    }


async def test_record_insight_still_refuses_an_unknown_argument(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    outcome = await registry.dispatch(
        set_ctx, "record_insight", {"text": "Learned something.", "colour": "red"}
    )
    assert not outcome.ok
    assert "Unknown argument: colour" in outcome.data["detail"]


async def test_record_insight_with_no_scope_is_accepted(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    assert (
        await registry.dispatch(
            set_ctx,
            "record_insight",
            {"text": "This bean likes it finer.", "evidence_shot_ids": archive.shots[:1]},
        )
    ).ok


# -- what a refusal says ---------------------------------------------------


async def test_the_refusal_names_what_the_tool_is_rather_than_the_wrong_rule(
    ctx: ToolContext, set_ctx: ToolContext
) -> None:
    """A tool absent for its own reason says that reason, not the kind's rule."""
    shots = await registry.dispatch(ctx, "list_set_shots", {})
    in_a_set = await registry.dispatch(set_ctx, "query_shots", {"sql": "SELECT 1"})

    assert "one Set a conversation is about" in shots.error
    assert "Changing a Set" not in shots.error
    assert "can see this Set only" in in_a_set.error


# -- a Set being designed --------------------------------------------------


@pytest.fixture
async def design_ctx(archive: Fixture, settings: object) -> ToolContext:
    """A conversation about a Set of the fixture's bean that is being designed."""
    from gaggiclanker.db.repos.sets import DesignBrief, SetsRepository, SetWrite
    from gaggiclanker.knowledge.service import KnowledgeService
    from gaggiclanker.tools.registry import CHAT_PERMISSIONS

    designed = await SetsRepository(archive.db).create_design(
        SetWrite(name="Designed", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        DesignBrief(),
    )
    return ToolContext(
        db=archive.db,
        settings=settings,  # type: ignore[arg-type]
        knowledge=KnowledgeService(archive.db),
        scope=await ToolScope.resolve(archive.db, designed.id),
        caller="test",
        permissions=CHAT_PERMISSIONS,
    )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("propose_set_version", {"reason": "Finer.", "grind_setting": "18"}),
        (
            "draft_profile",
            {"base_version_id": 1, "patch": {"temperature": 92}, "reason": "Cooler."},
        ),
        ("list_set_shots", {}),
        ("get_shot", {"shot_id": 1}),
        ("get_shot_extended", {"shot_id": 1}),
        ("get_shot_full", {"shot_id": 1}),
        ("compare_shots", {"shot_ids": [1, 2]}),
        ("record_insight", {"text": "Something."}),
        ("query_shots", {"sql": "SELECT 1"}),
    ],
)
async def test_a_design_conversation_refuses_what_needs_a_recipe_and_points_the_way(
    design_ctx: ToolContext, name: str, arguments: dict[str, object]
) -> None:
    outcome = await registry.dispatch(design_ctx, name, arguments)

    assert outcome.status == "refused"
    assert DESIGN_RULE in outcome.error
    assert "propose_initial_recipe" in outcome.error


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("propose_set_version", {"reason": "Finer.", "grind_setting": "18"}),
        (
            "draft_profile",
            {"base_version_id": 1, "patch": {"temperature": 92}, "reason": "Cooler."},
        ),
    ],
)
async def test_the_two_change_tools_refuse_a_design_even_when_called_directly(
    design_ctx: ToolContext, name: str, arguments: dict[str, object]
) -> None:
    """Past the dispatcher — a caller that did not ask it — the rule still holds."""
    spec = registry.get(name)
    assert spec is not None

    with pytest.raises(ValueError, match="propose_initial_recipe is how the recipe is proposed"):
        await spec.fn(design_ctx, spec.input_model.model_validate(arguments))


async def test_the_schemas_sent_while_designing_are_the_design_scope_s(
    design_ctx: ToolContext,
) -> None:
    names = {
        schema["function"]["name"] for schema in registry.openai_schemas(scope=design_ctx.scope)
    }

    assert names == DESIGN_TOOLS


async def test_list_profiles_in_a_set_chat_does_not_count_other_sets_shots(
    set_ctx: ToolContext, ctx: ToolContext
) -> None:
    """A profile's archive-wide shot count is how busy the other Sets have been."""
    scoped = (await registry.dispatch(set_ctx, "list_profiles", {})).data
    general = (await registry.dispatch(ctx, "list_profiles", {})).data

    assert scoped["items"], scoped
    assert all("shot_count" not in item for item in scoped["items"])
    assert any("shot_count" in item for item in general["items"])


@pytest.fixture
async def other_set_insights(archive: Fixture, other_set_shot: int) -> dict[str, int]:
    """Four insights: this Set's, two about other kit, and one nobody confirmed."""
    from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
    from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
    from gaggiclanker.db.repos.knowledge_insights import (
        InsightScope,
        InsightsRepository,
        InsightWrite,
    )
    from gaggiclanker.db.repos.sets import SetsRepository

    repo = InsightsRepository(archive.db)
    row = await SetsRepository(archive.db).get(archive.set_id)
    assert row is not None
    other_bean = await BeansRepository(archive.db).create(BeanWrite(name="Somebody else's bag"))
    other_grinder = await GrindersRepository(archive.db).create(GrinderWrite(name="A second one"))
    written = {
        "mine": InsightWrite(
            scope=InsightScope(bean_id=row.bean_id),
            text="This Set's bean likes it finer.",
            # One id from this Set and one from another: a bean-scoped insight
            # legitimately rests on both, and only one of them may come back.
            evidence_shot_ids=[archive.shots[0], other_set_shot],
            source="chat",
            confirmed=True,
        ),
        "other_bean": InsightWrite(
            scope=InsightScope(bean_id=other_bean.id),
            text="Another bean entirely.",
            evidence_shot_ids=[other_set_shot],
            source="chat",
            confirmed=True,
        ),
        "other_grinder": InsightWrite(
            scope=InsightScope(grinder_id=other_grinder.id),
            text="Another grinder entirely.",
            source="chat",
            confirmed=True,
        ),
        "unconfirmed": InsightWrite(
            scope=InsightScope(bean_id=row.bean_id),
            text="Nobody has confirmed this.",
            source="chat",
            confirmed=False,
        ),
    }
    return {name: await repo.insert(spec) for name, spec in written.items()}


@pytest.fixture
async def other_set_shot(archive: Fixture) -> int:
    """A shot filed under a different Set, which this conversation may not see."""
    from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite

    sets = SetsRepository(archive.db)
    other = await sets.create(
        SetWrite(name="Another bag", bean_id=archive.bean_id),
        SetVersionWrite(grind_setting="20"),
        automatch=False,
    )
    version = await sets.current_version(other.id)
    assert version is not None
    moved = archive.shots[-1]
    assert await sets.assign_shot(moved, version.id)
    return moved


def test_the_mcp_client_is_told_what_a_design_connection_has() -> None:
    """The first line a CLI reads must not send it to a tool this connection lacks."""
    from gaggiclanker.tools.mcp.server import instructions_for

    told = instructions_for(ToolScope.for_thread(3, designing=True))

    assert "propose_initial_recipe" in told
    assert "list_set_shots" not in told
    assert instructions_for(ToolScope.for_thread(3)) != told


def test_the_shot_reading_tools_say_the_opening_context_shots_are_already_there() -> None:
    """The claude_code provider sees only these descriptions, so they carry the guidance."""
    from gaggiclanker.tools.mcp.server import SET_INSTRUCTIONS

    for name in ("get_shot", "get_shot_full", "list_set_shots"):
        spec = registry.get(name)
        assert spec is not None
        assert "opening context" in spec.description, name
        assert "already there in base information" in spec.description, name
    assert "get_shot_extended" in SET_INSTRUCTIONS
    assert "already holds" in SET_INSTRUCTIONS

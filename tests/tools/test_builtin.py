"""Each tool, once, against a seeded archive.

Through the dispatcher rather than by calling the functions, because the
dispatcher is what the chat and MCP both use and a tool that works when called
directly but whose schema rejects its own arguments is a tool that does not
work.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    RollbackWrite,
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
)
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.domain.models import Profile
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.tools.registry import READ_ONLY, ToolContext, registry
from gaggiclanker.tools.sql import ALLOWED_VIEWS
from tests.review.conftest import Fixture


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


async def test_get_shot_is_the_base_rendering_of_the_shot(
    ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(ctx, "get_shot", shot_id=archive.shots[0])

    assert data["shot_id"] == archive.shots[0]
    assert data["tier"] == "base"
    assert data["text"].startswith(f"shot {archive.shots[0]}\n")
    assert "Rating: 2/5" in data["text"]
    assert "Channeling risk: MODERATE" in data["text"]
    # Nothing extended, and no curve: that is what the other two tools are for.
    assert "[Curve]" not in data["text"]
    assert "Score confidence" not in data["text"]
    assert set(data) == {"shot_id", "tier", "text"}, "the old analysis field is gone"


async def test_get_shot_extended_adds_only_what_base_leaves_out(
    ctx: ToolContext, archive: Fixture
) -> None:
    # The last shot is the one the fixture gives samples to.
    base = (await call(ctx, "get_shot", shot_id=archive.shots[-1]))["text"]
    extended = await call(ctx, "get_shot_extended", shot_id=archive.shots[-1])

    assert extended["tier"] == "extended"
    assert "Score confidence: high" in extended["text"]
    assert "[Curve]\n" in extended["text"]
    heading = extended["text"].split("[Curve]\n", 1)[1].splitlines()[0]
    assert re.fullmatch(r"\d+ of 112 samples, shape-preserving; always kept: .+", heading)
    assert "Rating:" not in extended["text"]
    assert "Rating: 3/5" in base


async def test_the_next_shot_tool_call_follows_the_curve_points_setting(
    ctx: ToolContext, archive: Fixture
) -> None:
    """Read per call, like the tiers: no restart, no new conversation."""
    shot = archive.shots[-1]

    def rows(text: str) -> int:
        lines = text.split("[Curve]\n", 1)[1].splitlines()
        end = next((i for i, line in enumerate(lines) if line.startswith("[")), len(lines))
        return end - 2

    before = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]
    await ctx.settings.store("chatCurvePoints", 10)
    fewer = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]
    compared = (await call(ctx, "compare_shots", shot_ids=[shot, archive.shots[0]]))["shots"]
    await ctx.settings.store("chatCurvePoints", 500)
    whole = (await call(ctx, "get_shot_full", shot_id=shot))["text"]

    assert rows(fewer) < rows(before) <= 60
    assert rows(compared[0]["text"]) == rows(fewer)
    assert "[Curve]\nall 112 samples\n" in whole
    assert rows(whole) == 112


async def test_get_shot_full_is_both(ctx: ToolContext, archive: Fixture) -> None:
    full = await call(ctx, "get_shot_full", shot_id=archive.shots[-1])

    assert full["tier"] == "full"
    assert "Rating: 3/5" in full["text"]
    assert "Score confidence: high" in full["text"]
    assert "[Curve]" in full["text"]


async def test_the_next_shot_tool_call_follows_a_tier_moved_since_the_last(
    ctx: ToolContext, archive: Fixture
) -> None:
    """Read per call, the way the stdio child reads it too."""
    shot = archive.shots[-1]
    before = (await call(ctx, "get_shot", shot_id=shot))["text"]
    repo = ShotInfoTiersRepository(archive.db)
    await repo.set_tier(ShotInfoTierWrite(item_key="score_confidence", tier="base"))
    await repo.set_tier(ShotInfoTierWrite(item_key="rating", tier="excluded"))

    after = (await call(ctx, "get_shot", shot_id=shot))["text"]
    extended = (await call(ctx, "get_shot_extended", shot_id=shot))["text"]

    assert "Score confidence: high" not in before
    assert "Score confidence: high" in after
    assert "Score confidence" not in extended, "an item sits in one tier at a time"
    assert "Rating: 3/5" in before
    assert "Rating:" not in after
    assert "Rating:" not in (await call(ctx, "get_shot_full", shot_id=shot))["text"]


@pytest.mark.parametrize("name", ["get_shot", "get_shot_extended", "get_shot_full"])
async def test_a_shot_tool_says_so_when_there_is_no_such_shot(ctx: ToolContext, name: str) -> None:
    data = await refuse(ctx, name, shot_id=999_999)
    assert "No shot" in data["detail"]


async def test_the_shot_tools_take_the_shot_id_and_nothing_else(ctx: ToolContext) -> None:
    """The old `detail` argument is gone: the tier is the tool."""
    data = await refuse(ctx, "get_shot", shot_id=1, detail="curve")
    assert data["error"] == "invalid_arguments"


async def test_compare_shots_renders_each_in_full_in_the_order_given(
    ctx: ToolContext, archive: Fixture
) -> None:
    wanted = [archive.shots[2], archive.shots[0], archive.shots[-1]]

    data = await call(ctx, "compare_shots", shot_ids=wanted)

    assert [shot["shot_id"] for shot in data["shots"]] == wanted
    assert all(shot["tier"] == "full" for shot in data["shots"])
    assert all(shot["text"].startswith(f"shot {shot['shot_id']}\n") for shot in data["shots"])
    assert "Score confidence" in data["shots"][0]["text"]
    assert "Rating:" in data["shots"][0]["text"]


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


async def test_list_beans_leaves_out_what_nobody_filled_in(ctx: ToolContext) -> None:
    """A null or empty field would read to the model as a fact about the coffee."""
    beans = BeansRepository(ctx.db)
    bare = await beans.create(BeanWrite(name="Mystery"))
    scaled = await beans.create(BeanWrite(name="Scaled", acidity=4, sweetness=2))

    items = {item["id"]: item for item in (await call(ctx, "list_beans"))["items"]}

    assert items[bare.id] == {
        "id": bare.id,
        "name": "Mystery",
        "decaf": False,
        "archived": False,
        "created_at": bare.created_at,
        "set_count": 0,
    }
    assert items[scaled.id]["acidity"] == 4
    assert items[scaled.id]["sweetness"] == 2
    assert "intensity" not in items[scaled.id]


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
    assert data["compares_to_version"] == before.version_label
    # Named, never counted: there is no ordinal to reach the model.
    assert "compares_to_version_no" not in data
    # The whole point: the Set is where it was, and the answer says so.
    assert "Nothing has changed yet" in data["note"]
    # The person names the version on the card; the answer says both names.
    assert (data["would_be_minor"], data["would_be_major"]) == ("v1.1", "v2")
    assert "a minor version (v1.1) or a major one (v2); by default it is minor" in data["note"]
    assert data["suggest_major"] is False
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
    assert f"{current.version_label}'s prediction has not been graded" in data["detail"]
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


async def _second_profile(archive: Fixture) -> int:
    """A profile version the archive knows besides the Set's own, on no machine yet."""
    profiles = ProfilesRepository(archive.db)
    own = await profiles.get_version(archive.profile_version_id)
    assert own is not None and isinstance(own.profile, dict)
    version, _ = await profiles.ensure_version(
        Profile.model_validate({**own.profile, "label": "Hotter"})
    )
    return version.id


async def test_a_profile_not_on_the_machine_is_refused_and_draft_profile_named(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """Nothing in the app can put an existing version on the machine; only drafts are pushed.

    A Set version naming such a profile is a recipe nobody can brew, and the
    person finds that out at the machine after pressing Accept.
    """
    hotter = await _second_profile(archive)

    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Switch to the hotter profile.",
        profile_version_id=hotter,
        prediction=PREDICTION,
    )

    assert f"Profile version {hotter} is not on the machine" in data["detail"]
    assert "draft_profile" in data["detail"]
    assert "on_machine" in data["detail"]
    assert await SetProposalsRepository(archive.db).waiting(archive.set_id) is None


async def test_a_profile_on_the_machine_can_be_proposed(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """The refusal is about the machine, not about changing the profile."""
    hotter = await _second_profile(archive)
    profiles = ProfilesRepository(archive.db)
    await profiles.upsert_device_profile(device_id="hot", version_id=hotter)

    data = await call(
        set_ctx,
        "propose_set_version",
        reason="Switch to the hotter profile.",
        profile_version_id=hotter,
        prediction=PREDICTION,
    )
    assert data["changed"] == ["the profile"]

    # A profile deleted from the machine since is refused again.
    await SetProposalsRepository(archive.db).decline(archive.set_id, data["proposal_id"])
    await profiles.mark_one_deleted("hot")
    again = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Switch to the hotter profile.",
        profile_version_id=hotter,
        prediction=PREDICTION,
    )
    assert "is not on the machine" in again["detail"]


async def test_list_profiles_says_which_are_on_the_machine(
    ctx: ToolContext, archive: Fixture
) -> None:
    hotter = await _second_profile(archive)
    await ProfilesRepository(archive.db).upsert_device_profile(
        device_id="own", version_id=archive.profile_version_id
    )

    items = {
        item["profile_version_id"]: item for item in (await call(ctx, "list_profiles"))["items"]
    }

    assert items[archive.profile_version_id]["on_machine"] == 1
    assert items[hotter]["on_machine"] == 0


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
    assert current is not None and current.version_label == "v1"


def _with_drafts(ctx: ToolContext) -> ToolContext:
    """The same context, able to store a draft. No machine anywhere in it.

    ``DraftProposals`` is the half of the draft feature built without the
    connection — the archive and the safety bounds — which is exactly what the
    application hands a tool.
    """
    ctx.drafts = DraftProposals(ctx.db, ctx.settings)
    return ctx


async def test_a_profile_draft_in_a_set_conversation_needs_a_prediction(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """A profile change is a change to the experiment, so it owes a guess too."""
    data = await refuse(
        _with_drafts(set_ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
    )
    assert "cannot be proposed without a prediction" in data["detail"]


async def test_a_profile_draft_outside_a_set_needs_none(ctx: ToolContext, archive: Fixture) -> None:
    """A draft that belongs to no experiment has nothing to be graded against."""
    data = await call(
        _with_drafts(ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
    )
    assert data["prediction"] == ""

    stored = await ProfileDraftsRepository(archive.db).get(data["draft_id"])
    assert stored is not None
    assert stored.set_id is None


async def test_a_set_conversation_s_draft_carries_its_set_and_its_prediction(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    current = await SetsRepository(archive.db).current_version(archive.set_id)
    assert current is not None

    data = await call(
        _with_drafts(set_ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        prediction="Compared to v1: less of the dry finish, and no slower.",
    )

    assert data["prediction"].startswith("Compared to v1")
    assert data["compares_to_version"] == current.version_label
    assert "the Set is where it was" in data["note"]
    assert "a minor version (v1.1, the default for a draft) or a major one (v2)" in data["note"]

    stored = await ProfileDraftsRepository(archive.db).get(data["draft_id"])
    assert stored is not None
    assert stored.set_id == archive.set_id
    assert stored.compares_to_version_id == current.id
    assert stored.status == "draft", "nothing is on the machine"


async def test_a_profile_draft_is_blocked_while_a_prediction_is_ungraded(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """The same rule as a proposed grind change, in the same words."""
    current = await SetsRepository(archive.db).add_version(
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
    assert current is not None

    data = await refuse(
        _with_drafts(set_ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        prediction="Compared to v2: less of the dry finish.",
    )
    assert f"{current.version_label}'s prediction has not been graded" in data["detail"]


async def test_a_profile_draft_is_blocked_while_a_proposal_is_waiting(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    await call(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer, to chase the sour finish.",
        grind_setting="20",
        prediction=PREDICTION,
    )

    data = await refuse(
        _with_drafts(set_ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        prediction="Compared to v1: less of the dry finish.",
    )
    assert "already waiting" in data["detail"]
    assert "the grind" in data["detail"]


async def test_record_insight_lands_unconfirmed_and_sourced_to_the_chat(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(
        set_ctx,
        "record_insight",
        text="This grinder wants two clicks finer for anything anaerobic.",
        evidence_shot_ids=archive.shots[:2],
    )

    stored = await InsightsRepository(archive.db).get(data["insight_id"])
    assert stored is not None
    assert stored.source == "chat"
    assert stored.confirmed is False, "nothing unconfirmed reaches a prompt"
    assert stored.evidence_shot_ids == archive.shots[:2]
    # It belongs to the Set it was learned in, at the version the chat is about.
    assert stored.set_id == archive.set_id
    assert stored.set_version_id == archive.version_id
    assert stored.scope.stated() == {}
    assert data["scope"] == "this Set, learned at v1"
    assert stored.thread_id is None, "this context names no conversation"


async def test_a_recorded_insight_records_the_conversation_that_wrote_it(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    cursor = await archive.db.execute(
        "INSERT INTO chat_threads (title, set_id) VALUES ('v1', ?)", (archive.set_id,)
    )
    set_ctx.thread_id = int(cursor.lastrowid or 0)

    data = await call(
        set_ctx, "record_insight", text="Finer helps.", evidence_shot_ids=archive.shots[:1]
    )

    stored = await InsightsRepository(archive.db).get(data["insight_id"])
    assert stored is not None and stored.thread_id == set_ctx.thread_id


async def test_a_recorded_insight_does_not_reach_the_next_prompt(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    """The loop tier 3 exists to break: propose, then be believed next turn."""
    await call(
        set_ctx, "record_insight", text="Always go finer.", evidence_shot_ids=archive.shots[:1]
    )

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


async def test_a_tool_that_needs_a_service_says_so_rather_than_crashing(
    ctx: ToolContext, archive: Fixture
) -> None:
    """A context built without the service a tool needs has to say so readably."""
    data = await refuse(
        ctx, "draft_profile", base_version_id=archive.profile_version_id, patch={}, reason="x"
    )

    assert "running gaggiclanker application" in data["detail"]


# -- version names -------------------------------------------------------------

MAJOR_REASON = "Two clicks is a new direction for this Set, not a nudge on the old one."


async def test_a_proposal_stores_the_agent_s_major_suggestion_and_its_reason(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer, to chase the sour finish.",
        grind_setting="20",
        prediction=PREDICTION,
        suggest_major=True,
        major_reason=f"  {MAJOR_REASON}  ",
    )

    assert data["suggest_major"] is True
    assert "your suggestion of major is shown with your reason" in data["note"]
    # Only a suggestion: the default for a grind change is still minor.
    assert "by default it is minor" in data["note"]
    stored = await SetProposalsRepository(archive.db).get(archive.set_id, data["proposal_id"])
    assert stored is not None
    assert (stored.suggest_major, stored.major_reason) == (True, MAJOR_REASON)


@pytest.mark.parametrize("reason", ["", "   ", "It is bigger."])
async def test_a_major_suggestion_without_a_reason_is_refused_in_its_own_words(
    set_ctx: ToolContext, archive: Fixture, reason: str
) -> None:
    data = await refuse(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer, to chase the sour finish.",
        grind_setting="20",
        prediction=PREDICTION,
        suggest_major=True,
        major_reason=reason,
    )

    assert data["detail"].startswith("suggest_major needs major_reason")
    assert await SetProposalsRepository(archive.db).waiting(archive.set_id) is None


async def test_a_reason_without_a_suggestion_is_not_stored(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(
        set_ctx,
        "propose_set_version",
        reason="Two clicks finer, to chase the sour finish.",
        grind_setting="20",
        prediction=PREDICTION,
        major_reason=MAJOR_REASON,
    )

    stored = await SetProposalsRepository(archive.db).get(archive.set_id, data["proposal_id"])
    assert stored is not None
    assert (stored.suggest_major, stored.major_reason) == (False, "")


async def test_a_set_draft_stores_the_agent_s_major_suggestion(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(
        _with_drafts(set_ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        prediction=PREDICTION,
        suggest_major=True,
        major_reason=MAJOR_REASON,
    )

    assert data["suggest_major"] is True
    stored = await ProfileDraftsRepository(archive.db).get(data["draft_id"])
    assert stored is not None
    assert (stored.suggest_major, stored.major_reason) == (True, MAJOR_REASON)
    assert (stored.set_next_minor_label, stored.set_next_major_label) == ("v1.1", "v2")


async def test_a_set_draft_refuses_a_major_suggestion_without_a_reason(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await refuse(
        _with_drafts(set_ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        prediction=PREDICTION,
        suggest_major=True,
    )

    assert data["detail"].startswith("suggest_major needs major_reason")


async def test_a_draft_outside_a_set_cannot_suggest_a_major_version(
    ctx: ToolContext, archive: Fixture
) -> None:
    data = await refuse(
        _with_drafts(ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        suggest_major=True,
        major_reason=MAJOR_REASON,
    )

    assert "only means something in a conversation about one Set" in data["detail"]


async def test_get_set_names_the_version_the_set_went_back_to_as_current(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    sets = SetsRepository(archive.db)
    first = await sets.current_version(archive.set_id)
    assert first is not None
    await sets.add_version(archive.set_id, SetVersionPatch(grind_setting="21"))
    assert (await call(set_ctx, "get_set"))["set"]["current_version_label"] == "v1.1"

    await sets.rollback(archive.set_id, RollbackWrite(to_version_id=first.id))

    got = await call(set_ctx, "get_set")
    assert got["set"]["current_version_label"] == "v1"
    assert {v["version_label"]: v["is_current"] for v in got["versions"]} == {
        "v1": True,
        "v1.1": False,
    }
    assert (got["set"]["next_minor_label"], got["set"]["next_major_label"]) == ("v1.2", "v2")


async def test_the_set_tools_name_versions_and_never_count_them(
    set_ctx: ToolContext, ctx: ToolContext, archive: Fixture
) -> None:
    """There is no ordinal for a model to say "v3" from: the names are all it is given."""
    sets = SetsRepository(archive.db)
    await sets.add_version(archive.set_id, SetVersionPatch(grind_setting="21"))
    await sets.add_version(
        archive.set_id,
        SetVersionPatch.model_validate(
            {"grind_setting": "20", "prediction": PREDICTION, "compares_to_version_id": None}
        ),
    )

    got = await call(set_ctx, "get_set")
    listed = await call(ctx, "list_sets")

    assert got["set"]["current_version_label"] == "v1.2"
    assert [version["version_label"] for version in got["versions"]] == ["v1.2", "v1.1", "v1"]
    assert [version["version_label"] for version in got["trajectory"]] == ["v1", "v1.1", "v1.2"]
    rows = [got["set"], *got["versions"], *got["trajectory"], *listed["items"]]
    for row in rows:
        assert not {"version_no", "current_version_no", "compares_to_version_no"} & set(row)

"""The tools and the drink: what `propose_recipe_version` takes, what `get_recipe` serves.

The fixture Recipe is a cappuccino one with notes (the review tests' archive).
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.db.repos.recipe_proposals import RecipeProposalsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.review.conftest import Fixture
from tests.tools.test_builtin import PREDICTION, call, refuse

NOTES = "oat milk (Oatly Barista), ~150 ml"


def _keys(data: dict[str, Any]) -> set[str]:
    return {rule["key"] for rule in data["rules"]}


async def _waiting(archive: Fixture) -> object:
    waiting = await RecipeProposalsRepository(archive.db).waiting(archive.recipe_id)
    assert waiting is not None
    return waiting


async def test_a_drink_change_is_a_proposal_that_suggests_a_major(
    recipe_ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(
        recipe_ctx,
        "propose_recipe_version",
        reason="A latte, so the shot has to carry more milk.",
        drink="latte",
        prediction=PREDICTION,
    )

    assert data["changed"] == ["the drink"]
    assert data["change_summary"] == "Drink Cappuccino → Latte"
    assert data["suggest_major"] is True
    assert "the card suggests a major because the drink changes" in data["note"]
    assert "your suggestion of major" not in data["note"]
    waiting = await _waiting(archive)
    assert waiting.major_reason == "the drink changes"  # type: ignore[attr-defined]
    assert waiting.patch.model_fields_set == {"drink"}  # type: ignore[attr-defined]


async def test_a_drink_change_with_a_dial_is_one_change_and_needs_no_combined_reason(
    recipe_ctx: ToolContext,
) -> None:
    data = await call(
        recipe_ctx,
        "propose_recipe_version",
        reason="A shorter, heavier shot to stand up to a latte.",
        drink="latte",
        target_yield_g=32,
        prediction=PREDICTION,
    )

    assert data["changed"] == ["the target yield", "the drink"]
    assert (
        "Target yield" in data["change_summary"]
        and "Drink Cappuccino → Latte" in data["change_summary"]
    )
    assert data["suggest_major"] is True


async def test_notes_alone_are_a_minor_and_empty_notes_take_them_away(
    recipe_ctx: ToolContext,
) -> None:
    changed = await call(
        recipe_ctx,
        "propose_recipe_version",
        reason="Less milk in the cup.",
        drink_notes="oat milk, ~120 ml",
        prediction=PREDICTION,
    )
    assert changed["changed"] == ["the drink notes"]
    assert changed["suggest_major"] is False
    assert changed["change_summary"] == (f"Drink notes {NOTES} → oat milk, ~120 ml")


async def test_removing_the_notes_says_cleared(recipe_ctx: ToolContext) -> None:
    removed = await call(
        recipe_ctx,
        "propose_recipe_version",
        reason="No milk notes any more.",
        drink_notes="",
        prediction=PREDICTION,
    )

    assert removed["changed"] == ["the drink notes"]
    assert removed["change_summary"] == f"Drink notes {NOTES} → cleared"


async def test_the_same_drink_or_the_same_notes_is_not_a_change(recipe_ctx: ToolContext) -> None:
    data = await refuse(
        recipe_ctx,
        "propose_recipe_version",
        reason="Nothing really.",
        drink="cappuccino",
        drink_notes=f"  {NOTES} ",
        prediction=PREDICTION,
    )

    assert "has to change something" in data["detail"]
    assert "drink_notes" in data["detail"]


async def test_the_tool_says_a_drink_change_is_a_new_major_by_default() -> None:
    spec = next(item for item in registry.specs() if item.name == "propose_recipe_version")
    properties = spec.input_schema()["properties"]

    assert "a different drink is a new major by default" in spec.description
    assert "espresso" in str(properties["drink"]) and "latte" in str(properties["drink"])
    assert "drink_notes" in properties


async def test_the_initial_recipe_tool_takes_no_drink() -> None:
    spec = next(item for item in registry.specs() if item.name == "propose_initial_recipe")

    assert "drink" not in spec.input_schema()["properties"]
    assert "drink_notes" not in spec.input_schema()["properties"]


async def test_get_recipe_serves_the_drink_and_the_notes(recipe_ctx: ToolContext) -> None:
    data = await call(recipe_ctx, "get_recipe")

    assert (data["recipe"]["drink"], data["recipe"]["drink_notes"]) == ("cappuccino", NOTES)
    assert {(v["drink"], v["drink_notes"]) for v in data["versions"]} == {("cappuccino", NOTES)}


async def test_get_recipe_leaves_out_a_drink_nobody_stated(
    recipe_ctx: ToolContext, ctx: ToolContext, archive: Fixture
) -> None:
    await archive.db.execute("UPDATE recipe_versions SET drink = NULL, drink_notes = ''")

    data = await call(recipe_ctx, "get_recipe")

    assert "drink" not in data["recipe"] and "drink_notes" not in data["recipe"]
    assert all("drink" not in v and "drink_notes" not in v for v in data["versions"])
    listed = await call(ctx, "list_recipes")
    assert all("drink" not in item for item in listed["items"])


async def test_list_recipe_shots_serves_the_drink_when_the_person_shares_it(
    recipe_ctx: ToolContext, archive: Fixture
) -> None:
    from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite

    assert "Recipe drink" not in (await call(recipe_ctx, "list_recipe_shots"))["shots"][0]["text"]
    tiers = ShotInfoTiersRepository(archive.db)
    await tiers.set_tier(ShotInfoTierWrite(item_key="recipe_drink", tier="base"))
    await tiers.set_tier(ShotInfoTierWrite(item_key="recipe_drink_notes", tier="base"))

    text = (await call(recipe_ctx, "list_recipe_shots"))["shots"][0]["text"]

    assert "Recipe drink: Cappuccino" in text
    assert f"Recipe drink notes: {NOTES}" in text


async def test_get_rules_adds_the_recipes_drink_to_what_the_agent_passes(
    recipe_ctx: ToolContext, archive: Fixture
) -> None:
    milk = "ristretto_and_milk"

    cappuccino = await call(recipe_ctx, "get_rules", applies=["roast_level:light"])
    assert milk in _keys(cappuccino)
    await archive.db.execute("UPDATE recipe_versions SET drink = 'espresso'")
    espresso = await call(recipe_ctx, "get_rules", applies=["roast_level:light"])
    assert milk not in _keys(espresso)
    await archive.db.execute("UPDATE recipe_versions SET drink = NULL")
    none = await call(recipe_ctx, "get_rules", applies=["roast_level:light"])
    assert milk not in _keys(none)
    # Nothing is added to a call that passes nothing: it asks for every rule.
    await archive.db.execute("UPDATE recipe_versions SET drink = 'espresso'")
    everything = await call(recipe_ctx, "get_rules")
    assert milk in _keys(everything)


async def test_a_general_conversation_adds_no_drink(ctx: ToolContext) -> None:
    narrowed = await call(ctx, "get_rules", applies=["roast_level:light"])

    assert "ristretto_and_milk" not in {rule["key"] for rule in narrowed["rules"]}


async def test_in_a_design_conversation_get_rules_takes_the_drink_from_the_brief(
    archive: Fixture, settings: SettingsService
) -> None:
    from gaggiclanker.db.repos.recipes import DesignBrief, RecipesRepository, RecipeWrite

    recipes = RecipesRepository(archive.db)
    designed = await recipes.create_design(
        RecipeWrite(name="Designed", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        DesignBrief(drink="cappuccino", goal="Body."),
    )
    ctx = ToolContext(
        db=archive.db,
        settings=settings,
        knowledge=KnowledgeService(archive.db),
        scope=ToolScope.for_thread(designed.id, designed.current_version_id, designing=True),
        caller="test",
        permissions=CHAT_PERMISSIONS,
    )
    milk = "ristretto_and_milk"

    # The designing version 1 states no drink; the brief does.
    cappuccino = await call(ctx, "get_rules", applies=["roast_level:light"])
    assert milk in _keys(cappuccino)
    for drink in ("espresso", "other"):
        brief = designed.design_brief.model_copy(update={"drink": drink})
        await archive.db.execute(
            "UPDATE recipes SET design_brief = ? WHERE id = ?",
            (brief.model_dump_json(), designed.id),
        )
        assert milk not in _keys(await call(ctx, "get_rules", applies=["roast_level:light"]))


async def test_get_rules_follows_the_version_the_conversation_is_about_not_the_current_one(
    archive: Fixture, settings: SettingsService
) -> None:
    from gaggiclanker.db.repos.recipes import RecipesRepository, RecipeVersionPatch

    recipes = RecipesRepository(archive.db)
    older = archive.version_id  # a cappuccino version
    newer = await recipes.add_version(
        archive.recipe_id, RecipeVersionPatch(drink="espresso", intent="Straight now.")
    )
    assert newer is not None

    def context(version_id: int) -> ToolContext:
        return ToolContext(
            db=archive.db,
            settings=settings,
            knowledge=KnowledgeService(archive.db),
            scope=ToolScope.for_thread(archive.recipe_id, version_id),
            caller="test",
            permissions=CHAT_PERMISSIONS,
        )

    milk = "ristretto_and_milk"
    on_older = await call(context(older), "get_rules", applies=["roast_level:light"])
    on_newer = await call(context(newer.id), "get_rules", applies=["roast_level:light"])

    assert milk in _keys(on_older), "the thread is on the cappuccino version"
    assert milk not in _keys(on_newer), "the current version is espresso"

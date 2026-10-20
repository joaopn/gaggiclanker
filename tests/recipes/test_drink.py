"""A Recipe version's drink and drink notes: the vocabulary, the columns and the writes.

The drink is what is added to the shot after it leaves the portafilter, and the notes say how the
drink is made around the shot. Both belong to a version and are inherited by the next one; a
version made before drinks were recorded states no drink (``NULL``), which is not Espresso.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from gaggiclanker.db.repos.recipe_proposals import (
    DRINK_MAJOR_REASON,
    ProposalWrite,
    RecipeProposalsRepository,
    change_groups,
    changed_names,
)
from gaggiclanker.db.repos.recipes import (
    RECIPE_FIELDS,
    DesignBrief,
    RecipeVersionChange,
    RecipeVersionPatch,
    RecipeVersionWrite,
    RecipeWrite,
    RollbackWrite,
    version_changes,
)
from gaggiclanker.domain.drinks import DRINK_TERMS, DRINKS, drink_label, drink_line
from gaggiclanker.domain.vocab import vocabulary
from tests.profile_rows import with_a_profile
from tests.recipes.conftest import Fixtures, make_profile_version, make_shot


def data(response: httpx.Response) -> Any:
    body = response.json()
    assert body["ok"] is True, body
    return body["data"]


# ── the vocabulary ───────────────────────────────────────────────────────


def test_the_drinks_are_the_five_in_the_order_of_the_table() -> None:
    assert DRINKS == ("espresso", "americano", "cappuccino", "latte", "other")
    assert tuple(term.value for term in DRINK_TERMS) == DRINKS


def test_each_drink_has_its_label_and_the_covers_line_the_dialog_shows() -> None:
    assert {term.value: (term.label, term.covers) for term in DRINK_TERMS} == {
        "espresso": ("Espresso", "the shot as it comes out, any ratio"),
        "americano": ("Americano", "topped with hot water (long black too)"),
        "cappuccino": ("Cappuccino", "a little milk: macchiato, cortado, flat white, cappuccino"),
        "latte": ("Latte", "a lot of milk: latte, iced latte, mocha"),
        "other": ("Other", "anything else: tonic, affogato, shakerato"),
    }
    assert drink_label("latte") == "Latte" and drink_label(None) is None
    assert drink_line("cappuccino") == (
        "Cappuccino (a little milk: macchiato, cortado, flat white, cappuccino)"
    )
    assert drink_line(None) is None


async def test_the_served_vocabulary_is_the_module_and_the_database_agrees(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    served = data(await client.get("/api/vocab"))["drinks"]

    assert served == [
        {"value": term.value, "label": term.label, "covers": term.covers} for term in DRINK_TERMS
    ]
    assert [item.model_dump() for item in vocabulary().drinks] == served
    # The CHECK on the column lists exactly these values.
    schema = str(
        await app.state.db.fetch_value(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'recipe_versions'"
        )
    )
    check = re.search(r"drink\s+TEXT[^,]*?CHECK\s*\((.*?)\)\s*\)", schema, re.DOTALL)
    assert check is not None
    assert set(re.findall(r"'([^']+)'", check.group(1))) == set(DRINKS)


# ── the columns and the views ────────────────────────────────────────────


async def _recipe(wired: Fixtures, **version: Any) -> int:
    version.setdefault("drink", "cappuccino")
    row = await wired.recipes.create(
        RecipeWrite(name="Guji", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        RecipeVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22", **version),
    )
    return row.id


async def test_the_database_refuses_a_drink_outside_the_five(wired: Fixtures) -> None:
    recipe_id = await _recipe(wired)

    with pytest.raises(Exception, match="CHECK"):
        await wired.db.execute(
            "UPDATE recipe_versions SET drink = 'tea' WHERE recipe_id = ?", (recipe_id,)
        )


async def test_the_views_serve_the_drink_the_notes_and_the_shots_recipe_drink(
    wired: Fixtures,
) -> None:
    recipe_id = await _recipe(wired, drink_notes="oat milk, 150 ml")
    current = await wired.recipes.current_version(recipe_id)
    assert current is not None
    shot = await make_shot(wired.db, "000001")
    assert await wired.recipes.assign_shot(shot, current.id)

    version = await wired.db.fetch_one(
        "SELECT drink, drink_notes FROM v_recipe_versions WHERE recipe_version_id = ?",
        (current.id,),
    )
    served = await wired.db.fetch_one("SELECT recipe_drink FROM v_shots WHERE shot_id = ?", (shot,))

    assert version is not None and served is not None
    assert (version["drink"], version["drink_notes"]) == ("cappuccino", "oat milk, 150 ml")
    assert served["recipe_drink"] == "cappuccino"


# ── writing version 1 ────────────────────────────────────────────────────


def test_version_1_requires_a_drink() -> None:
    with pytest.raises(ValidationError, match="drink"):
        RecipeVersionWrite.model_validate({"dose_g": 18})
    with pytest.raises(ValidationError, match="drink"):
        RecipeVersionWrite.model_validate({"drink": "tea"})
    with pytest.raises(ValidationError, match="drink"):
        RecipeVersionWrite.model_validate({"drink": None})


def test_whitespace_only_notes_are_none_and_long_notes_are_refused() -> None:
    assert RecipeVersionWrite(drink="latte", drink_notes="   \n ").drink_notes == ""
    assert RecipeVersionWrite(drink="latte", drink_notes="  oat  ").drink_notes == "oat"
    with pytest.raises(ValidationError, match="drink_notes"):
        RecipeVersionWrite(drink="latte", drink_notes="x" * 501)


async def test_a_new_recipe_without_a_drink_is_refused(client: httpx.AsyncClient) -> None:
    bean = data(await client.post("/api/beans", json={"name": "Guji"}))
    body = await with_a_profile(client, {"name": "Guji", "bean_id": bean["id"]})
    del body["version"]["drink"]

    refused = await client.post("/api/recipes", json=body)

    assert refused.status_code == 400
    assert refused.json()["error"]["details"][0]["field"] == "body.version.drink"
    assert (await client.get("/api/recipes")).json()["data"]["items"] == []


async def test_a_new_recipe_stores_its_drink_and_notes_and_serves_them(
    client: httpx.AsyncClient,
) -> None:
    bean = data(await client.post("/api/beans", json={"name": "Guji"}))
    body = await with_a_profile(client, {"name": "Guji", "bean_id": bean["id"]})
    body["version"].update({"drink": "latte", "drink_notes": "  oat milk, ~150 ml "})

    created = data(await client.post("/api/recipes", json=body))

    assert (created["drink"], created["drink_notes"]) == ("latte", "oat milk, ~150 ml")
    detail = data(await client.get(f"/api/recipes/{created['id']}"))
    assert detail["recipe"]["drink"] == "latte"
    only = detail["versions"][0]["version"]
    assert (only["drink"], only["drink_notes"]) == ("latte", "oat milk, ~150 ml")
    listed = data(await client.get("/api/recipes"))["items"][0]
    assert (listed["drink"], listed["drink_notes"]) == ("latte", "oat milk, ~150 ml")


# ── changing a version: the drink and notes are inherited ────────────────


async def test_a_version_inherits_the_drink_and_notes_when_they_are_not_sent(
    wired: Fixtures,
) -> None:
    recipe_id = await _recipe(wired, drink_notes="oat milk")

    second = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(grind_setting="21"))

    assert second is not None
    assert (second.drink, second.drink_notes) == ("cappuccino", "oat milk")


async def test_a_sent_drink_and_notes_change_and_empty_notes_clear(wired: Fixtures) -> None:
    recipe_id = await _recipe(wired, drink_notes="oat milk")

    changed = await wired.recipes.add_version(
        recipe_id, RecipeVersionPatch(drink="latte", drink_notes="whole milk")
    )
    cleared = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(drink_notes=""))
    kept = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(dose_g=19))

    assert changed is not None and cleared is not None and kept is not None
    assert (changed.drink, changed.drink_notes) == ("latte", "whole milk")
    assert (cleared.drink, cleared.drink_notes) == ("latte", "")
    assert (kept.drink, kept.drink_notes) == ("latte", "")


def test_an_explicit_null_drink_is_refused_and_not_sending_it_is_not() -> None:
    with pytest.raises(ValidationError, match="drink"):
        RecipeVersionChange.model_validate({"drink": None})
    with pytest.raises(ValidationError, match="drink_notes"):
        RecipeVersionChange.model_validate({"drink_notes": None})
    unsent = RecipeVersionChange.model_validate({"dose_g": 18})
    assert "drink" not in unsent.model_fields_set and "drink_notes" not in unsent.model_fields_set


async def test_the_add_a_version_route_refuses_a_null_drink_and_takes_a_new_one(
    client: httpx.AsyncClient,
) -> None:
    bean = data(await client.post("/api/beans", json={"name": "Guji"}))
    body = await with_a_profile(client, {"name": "Guji", "bean_id": bean["id"]})
    recipe = data(await client.post("/api/recipes", json=body))

    refused = await client.post(
        f"/api/recipes/{recipe['id']}/versions", json={"drink": None, "intent": "x"}
    )
    changed = await client.post(
        f"/api/recipes/{recipe['id']}/versions",
        json={"drink": "americano", "drink_notes": "90 ml water", "intent": "a long black"},
    )

    assert refused.status_code == 400
    version = data(changed)
    assert (version["drink"], version["drink_notes"]) == ("americano", "90 ml water")
    assert len(data(await client.get(f"/api/recipes/{recipe['id']}"))["versions"]) == 2


def test_the_drink_and_its_notes_are_part_of_what_a_version_copies() -> None:
    assert {"drink", "drink_notes"} <= set(RECIPE_FIELDS)


async def test_rolling_back_writes_no_version_and_changes_neither_drink_nor_notes(
    wired: Fixtures,
) -> None:
    recipe_id = await _recipe(wired, drink="espresso", drink_notes="a")
    first = await wired.recipes.current_version(recipe_id)
    changed = await wired.recipes.add_version(
        recipe_id, RecipeVersionPatch(drink="latte", drink_notes="b", intent="milk")
    )
    assert first is not None and changed is not None

    result = await wired.recipes.rollback(recipe_id, RollbackWrite(to_version_id=first.id))

    assert result.refused is None
    versions = await wired.recipes.versions(recipe_id)
    assert len(versions) == 2
    assert {(v.id, v.drink, v.drink_notes) for v in versions} == {
        (first.id, "espresso", "a"),
        (changed.id, "latte", "b"),
    }
    now = await wired.recipes.get(recipe_id)
    assert now is not None and (now.drink, now.drink_notes) == ("espresso", "a")


# ── a version with no drink stated stays that way ────────────────────────


async def test_a_version_with_no_drink_stated_hands_that_on_and_reads_as_none(
    wired: Fixtures,
) -> None:
    recipe_id = await _recipe(wired)
    await wired.db.execute(
        "UPDATE recipe_versions SET drink = NULL WHERE recipe_id = ?", (recipe_id,)
    )

    second = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(dose_g=19))
    listed = await wired.recipes.get(recipe_id)

    assert second is not None and listed is not None
    assert (second.drink, second.drink_notes) == (None, "")
    assert (listed.drink, listed.drink_notes) == (None, "")


# ── what the log and the card say about it ───────────────────────────────


async def test_a_drink_change_and_a_notes_change_are_lines_of_the_diff(wired: Fixtures) -> None:
    recipe_id = await _recipe(wired, drink="espresso")
    added = await wired.recipes.add_version(
        recipe_id, RecipeVersionPatch(drink="cappuccino", drink_notes="whole milk")
    )
    changed = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(drink_notes="oat milk"))
    removed = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(drink_notes=""))
    versions = await wired.recipes.versions(recipe_id)
    by_id = {version.id: version for version in versions}
    assert added and changed and removed

    def lines(version: Any) -> list[tuple[str, str | None, str | None]]:
        parent = by_id[version.parent_version_id]
        return [(c.label, c.before, c.after) for c in version_changes(version, parent)]

    assert lines(added) == [
        ("Drink", "Espresso", "Cappuccino"),
        ("Drink notes", None, "whole milk"),
    ]
    assert lines(changed) == [("Drink notes", "whole milk", "oat milk")]
    assert lines(removed) == [("Drink notes", "oat milk", None)]


# ── the major suggestion ─────────────────────────────────────────────────


async def _propose(wired: Fixtures, recipe_id: int, **spec: Any) -> Any:
    proposals = RecipeProposalsRepository(wired.db)
    spec.setdefault("reason", "a drink for milk")
    spec.setdefault("prediction", "Compared to v1: a heavier shot that stands up to milk.")
    result = await proposals.create(recipe_id, ProposalWrite(**spec))
    assert result.refused is None, result.refused
    assert result.proposal is not None
    return result.proposal


async def test_a_different_drink_suggests_a_major_with_its_reason(wired: Fixtures) -> None:
    recipe_id = await _recipe(wired, drink="espresso")

    proposal = await _propose(wired, recipe_id, patch=RecipeVersionPatch(drink="cappuccino"))

    assert proposal.suggest_major is True
    assert proposal.major_reason == DRINK_MAJOR_REASON == "the drink changes"


async def test_the_agents_own_major_reason_is_kept_when_the_drink_also_changes(
    wired: Fixtures,
) -> None:
    recipe_id = await _recipe(wired, drink="espresso")

    proposal = await _propose(
        wired,
        recipe_id,
        patch=RecipeVersionPatch(drink="latte"),
        suggest_major=True,
        major_reason="a long, milky drink is a different shot to design",
    )

    assert proposal.suggest_major is True
    assert proposal.major_reason == "a long, milky drink is a different shot to design"


async def test_the_same_drink_or_a_change_of_notes_alone_suggests_nothing(
    wired: Fixtures,
) -> None:
    recipe_id = await _recipe(wired, drink="espresso")

    same = await _propose(
        wired, recipe_id, patch=RecipeVersionPatch(drink="espresso", drink_notes="a")
    )
    assert (same.suggest_major, same.major_reason) == (False, "")
    await RecipeProposalsRepository(wired.db).decline(recipe_id, same.id)

    notes = await _propose(wired, recipe_id, patch=RecipeVersionPatch(drink_notes="oat milk"))
    assert (notes.suggest_major, notes.major_reason) == (False, "")


async def test_a_suggestion_is_served_on_the_card_and_the_person_still_picks(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    bean = data(await client.post("/api/beans", json={"name": "Guji"}))
    recipe = data(
        await client.post(
            "/api/recipes", json=await with_a_profile(client, {"name": "G", "bean_id": bean["id"]})
        )
    )
    proposals = RecipeProposalsRepository(app.state.db)
    made = await proposals.create(
        recipe["id"],
        ProposalWrite(
            patch=RecipeVersionPatch(drink="cappuccino", drink_notes="whole milk, 120 ml"),
            reason="for milk",
            prediction="Compared to v1: heavier in the cup and still sweet.",
        ),
    )
    assert made.proposal is not None

    card = data(await client.get(f"/api/recipes/{recipe['id']}/proposals"))["items"][0]

    assert (card["suggest_major"], card["major_reason"]) == (True, "the drink changes")
    assert (card["next_minor_label"], card["next_major_label"]) == ("v1.1", "v2")
    assert [(c["label"], c["before"], c["after"]) for c in card["changes"]] == [
        ("Drink", "Espresso", "Cappuccino"),
        ("Drink notes", None, "whole milk, 120 ml"),
    ]
    assert card["changed"] == ["the drink", "the drink notes"]
    accepted = data(
        await client.post(f"/api/recipes/{recipe['id']}/proposals/{card['id']}/accept", json={})
    )
    # Nobody picked major: the suggestion only preselects the box.
    assert accepted["version"]["version_label"] == "v1.1"
    assert accepted["version"]["drink"] == "cappuccino"


def test_a_drink_change_is_named_but_is_not_one_of_the_changes_counted() -> None:
    patch = RecipeVersionPatch(drink="latte", drink_notes="oat", target_yield_g=40)

    assert change_groups(patch) == ["the target yield"]
    assert changed_names(patch) == ["the target yield", "the drink", "the drink notes"]


# ── a design takes its drink from the brief ──────────────────────────────


async def _design(wired: Fixtures, brief: DesignBrief) -> int:
    row = await wired.recipes.create_design(
        RecipeWrite(name="Designed", bean_id=wired.bean_id, grinder_id=wired.grinder_id), brief
    )
    return row.id


async def test_the_designs_version_1_takes_the_briefs_drink_whatever_the_card_says(
    wired: Fixtures,
) -> None:
    recipe_id = await _design(
        wired, DesignBrief(drink="latte", drink_notes="oat milk, 150 ml", goal="Body.")
    )
    profile = await make_profile_version(wired.db, "Designed")

    filled = await wired.recipes.add_version(
        recipe_id,
        RecipeVersionPatch(
            profile_version_id=profile, dose_g=18, drink="espresso", drink_notes="a card's notes"
        ),
    )

    assert filled is not None and filled.version_label == "v1"
    assert (filled.drink, filled.drink_notes) == ("latte", "oat milk, 150 ml")


async def test_an_old_brief_with_no_drink_fills_version_1_with_none(wired: Fixtures) -> None:
    recipe_id = await _design(wired, DesignBrief(goal="Body."))
    await wired.db.execute(
        "UPDATE recipes SET design_brief = ? WHERE id = ?",
        ('{"goal": "Body.", "usual_grind": ""}', recipe_id),
    )

    filled = await wired.recipes.add_version(recipe_id, RecipeVersionPatch(dose_g=18))

    assert filled is not None
    assert (filled.drink, filled.drink_notes) == (None, "")


async def test_the_design_route_requires_a_drink_and_stores_it_in_the_brief(
    client: httpx.AsyncClient,
) -> None:
    bean = data(await client.post("/api/beans", json={"name": "Kenya"}))
    grinder = data(await client.post("/api/grinders", json={"name": "Niche"}))
    kit = {"bean_id": bean["id"], "grinder_id": grinder["id"]}

    refused = await client.post("/api/recipes/design", json=kit)
    made = await client.post(
        "/api/recipes/design", json={**kit, "drink": "americano", "drink_notes": " 90 ml "}
    )

    assert refused.status_code == 400
    brief = data(made)["recipe"]["design_brief"]
    assert (brief["drink"], brief["drink_notes"]) == ("americano", "90 ml")


# ── the readers that serve a version ─────────────────────────────────────


async def test_the_shot_list_and_the_shot_detail_carry_the_filed_versions_drink(
    wired: Fixtures,
) -> None:
    recipe_id = await _recipe(wired, drink="americano")
    current = await wired.recipes.current_version(recipe_id)
    assert current is not None
    filed = await make_shot(wired.db, "000001")
    loose = await make_shot(wired.db, "000002")
    assert await wired.recipes.assign_shot(filed, current.id)

    page = await wired.shots.list_shots(limit=10)
    by_id = {row.id: row for row in page.items}
    detail = await wired.shots.get(filed)
    unfiled = await wired.shots.get(loose)

    assert by_id[filed].recipe_drink == "americano" and by_id[loose].recipe_drink is None
    assert detail is not None and detail.recipe_drink == "americano"
    assert unfiled is not None and unfiled.recipe_drink is None


async def test_a_shot_filed_under_a_version_with_no_drink_has_none(wired: Fixtures) -> None:
    recipe_id = await _recipe(wired)
    current = await wired.recipes.current_version(recipe_id)
    assert current is not None
    await wired.db.execute("UPDATE recipe_versions SET drink = NULL WHERE id = ?", (current.id,))
    shot = await make_shot(wired.db, "000001")
    assert await wired.recipes.assign_shot(shot, current.id)

    detail = await wired.shots.get(shot)

    assert detail is not None and detail.recipe_drink is None


async def test_the_catalogue_items_say_the_drink_by_its_label_and_the_notes(
    wired: Fixtures,
) -> None:
    from gaggiclanker.shotinfo.catalogue import ITEMS
    from gaggiclanker.shotinfo.render import load_shots

    recipe_id = await _recipe(wired, drink="cappuccino", drink_notes="oat milk, 150 ml")
    current = await wired.recipes.current_version(recipe_id)
    assert current is not None
    shot = await make_shot(wired.db, "000001")
    loose = await make_shot(wired.db, "000002")
    assert await wired.recipes.assign_shot(shot, current.id)

    filed, unfiled = await load_shots(wired.db, [shot, loose])

    for key, label in (
        ("recipe_drink", "Recipe drink"),
        ("recipe_drink_notes", "Recipe drink notes"),
    ):
        item = ITEMS[key]
        assert (item.group, item.label, item.default_tier) == (
            ITEMS["recipe_yield"].group,
            label,
            "excluded",
        )
    assert ITEMS["recipe_drink"].shot is not None and ITEMS["recipe_drink_notes"].shot is not None
    assert ITEMS["recipe_drink"].shot(filed) == "Cappuccino"
    assert ITEMS["recipe_drink_notes"].shot(filed) == "oat milk, 150 ml"
    assert ITEMS["recipe_drink"].shot(unfiled) is None
    assert ITEMS["recipe_drink_notes"].shot(unfiled) is None


async def test_the_query_sandbox_names_the_new_columns(client: httpx.AsyncClient) -> None:
    from gaggiclanker.tools.builtin import SCHEMA_NOTES

    assert "drink (v_recipe_versions)" in SCHEMA_NOTES and "recipe_drink (v_shots)" in SCHEMA_NOTES
    assert "drink_notes (v_recipe_versions)" in SCHEMA_NOTES

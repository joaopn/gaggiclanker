"""Duplicate a Recipe from one of its versions, and preview what the duplicate starts from.

A duplicate copies a recipe, not a past: a new profile made from the version's profile by the one
copy function, a new Recipe with its first version, an optional choice of the source Recipe's own
confirmed insights, and a note saying where it came from. These tests walk the two routes: the
shape of each answer, that every refusal writes nothing, that the whole thing is one transaction,
and that the copied insights are plain confirmed rows with no past.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.knowledge_insights import InsightsRepository, InsightWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.recipes import RecipesRepository, RecipeVersionWrite, RecipeWrite
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.signatures.service import SignatureService
from tests.drafts.conftest import BASE_LABEL, base_version_id, data, error
from tests.drafts.helpers import make_recipe_on, make_recipe_on_a_free_profile
from tests.drafts.test_board import adopted, get_board, pull, row_for
from tests.profile_rows import database_of

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _recipe_with_version(
    app: FastAPI, client: httpx.AsyncClient, name: str = "Guji"
) -> tuple[int, int]:
    """A Recipe and its v1: (recipe id, version id). The first is on the base profile; a profile has
    at most one Recipe, so any other is on the first profile that has none."""
    if name == "Guji":
        recipe_id = await make_recipe_on(client, name, await base_version_id(app, BASE_LABEL))
    else:
        recipe_id = await make_recipe_on_a_free_profile(app, client, name)
    detail = data(await client.get(f"/api/recipes/{recipe_id}"))
    return recipe_id, int(detail["versions"][0]["version"]["id"])


async def _insight(
    app: FastAPI,
    recipe_id: int | None,
    text: str,
    *,
    confirmed: bool = True,
    dismissed: bool = False,
    source: str = "chat",
) -> int:
    repo = InsightsRepository(app.state.db)
    insight_id = await repo.insert(
        InsightWrite(text=text, recipe_id=recipe_id, confirmed=confirmed, source=source)  # type: ignore[arg-type]
    )
    if dismissed:
        await app.state.db.execute(
            "UPDATE knowledge_insights SET dismissed = 1 WHERE id = ?", (insight_id,)
        )
    return insight_id


def _url(recipe_id: int, version_id: int, tail: str = "duplicate") -> str:
    return f"/api/recipes/{recipe_id}/versions/{version_id}/{tail}"


async def _preview(client: httpx.AsyncClient, recipe_id: int, version_id: int) -> httpx.Response:
    return await client.get(_url(recipe_id, version_id, "duplicate-preview"))


async def _duplicate(
    client: httpx.AsyncClient, recipe_id: int, version_id: int, **body: Any
) -> httpx.Response:
    payload: dict[str, Any] = {
        "recipe_name": "Copy",
        "profile_name": "Blooming milk",
        "insight_ids": [],
    }
    payload.update(body)
    if "bean_id" not in payload:
        bean = data(await client.get(f"/api/recipes/{recipe_id}"))["recipe"]["bean_id"]
        payload["bean_id"] = bean
    return await client.post(_url(recipe_id, version_id), json=payload)


async def _counts(app: FastAPI) -> tuple[int, ...]:
    db = app.state.db
    counts = []
    for table in (
        "recipes",
        "recipe_versions",
        "knowledge_insights",
        "profile_board",
        "profile_drafts",
        "profile_versions",
        "signature_expectations",
        "recipe_version_pins",
    ):
        counts.append(int(await db.fetch_value(f"SELECT COUNT(*) FROM {table}")))  # noqa: S608
    return tuple(counts)


async def _learned_at(app: FastAPI, insight_id: int, version_id: int | None) -> None:
    await app.state.db.execute(
        "UPDATE knowledge_insights SET recipe_version_id = ? WHERE id = ?", (version_id, insight_id)
    )


async def _no_profile_recipe(client: httpx.AsyncClient) -> tuple[int, int]:
    """A Recipe that never named a profile: one an older version made (the API no longer does)."""
    bean = data(await client.post("/api/beans", json={"name": "Bare", "roaster": "x"}))
    made = await RecipesRepository(database_of(client)).create(
        RecipeWrite(name="Bare", bean_id=bean["id"]),
        RecipeVersionWrite(dose_g=18, grind_setting="22"),
    )
    detail = data(await client.get(f"/api/recipes/{made.id}"))
    return int(made.id), int(detail["versions"][0]["version"]["id"])


# ── the preview ──────────────────────────────────────────────────────


async def test_the_preview_is_the_suggested_name_the_recipe_and_the_recipes_own_insights(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    other_recipe, _ = await _recipe_with_version(app, client, "Other")
    first = await _insight(app, recipe_id, "Grind finer than 2.3 runs sour", source="user")
    await _insight(app, recipe_id, "Waiting for the person", confirmed=False)
    await _insight(app, recipe_id, "Turned down", dismissed=True)
    await _insight(app, other_recipe, "Another Recipe's")
    await _insight(app, None, "A general insight about natural Ethiopians")
    second = await _insight(app, recipe_id, "Preinfusion under 6 s channels")
    before = await _counts(app)

    body = data(await _preview(client, recipe_id, version_id))

    assert set(body) == {
        "recipe_name",
        "bean_id",
        "grinder_id",
        "version_fields",
        "has_profile",
        "insights",
    }
    assert body["recipe_name"] == "Guji (copy)" and body["has_profile"] is True
    assert (
        body["bean_id"] == data(await client.get(f"/api/recipes/{recipe_id}"))["recipe"]["bean_id"]
    )
    assert body["grinder_id"] is None
    assert body["version_fields"] == {
        "profile_label": BASE_LABEL,
        "grind_setting": None,
        "grind_value": None,
        "dose_g": 18.0,
        "target_yield_g": 36.0,
        "intent": "",
    }
    # The Recipe's own confirmed, not dismissed ones, in the order the Recipe page lists them.
    assert body["insights"] == [
        {"id": first, "text": "Grind finer than 2.3 runs sour", "source": "user"},
        {"id": second, "text": "Preinfusion under 6 s channels", "source": "chat"},
    ]
    listed = data(await client.get("/api/knowledge/insights", params={"recipe_id": recipe_id}))[
        "items"
    ]
    own_listed = [
        item["id"] for item in listed if item["recipe_id"] == recipe_id and item["confirmed"]
    ]
    assert [item["id"] for item in body["insights"]] == own_listed
    assert await _counts(app) == before, "a preview writes nothing"


async def test_the_preview_lists_insights_by_version_newest_first_unversioned_then_copies(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, v1 = await _recipe_with_version(app, client)
    data(await client.post(f"/api/recipes/{recipe_id}/versions", json={"dose_g": 18.5}))
    versions = {
        v["version"]["version_label"]: v["version"]["id"]
        for v in data(await client.get(f"/api/recipes/{recipe_id}"))["versions"]
    }
    # Written oldest first: A at v1, a copy, a note with no version, B at v1.1, then another at v1.
    a = await _insight(app, recipe_id, "A learned at v1")
    copy = await _insight(app, recipe_id, "Copied with this Recipe")
    loose = await _insight(app, recipe_id, "Written with no version")
    b = await _insight(app, recipe_id, "B learned at v1.1")
    a2 = await _insight(app, recipe_id, "A2 learned at v1")
    await _learned_at(app, a, versions["v1"])
    await _learned_at(app, a2, versions["v1"])
    await _learned_at(app, b, versions["v1.1"])
    await _learned_at(app, loose, None)
    # A copy names no version in practice; the flag alone puts it last, whatever it names.
    await _learned_at(app, copy, versions["v1.1"])
    await app.state.db.execute(
        "UPDATE knowledge_insights SET copied_from_insight_id = 99 WHERE id = ?", (copy,)
    )

    body = data(await _preview(client, recipe_id, v1))

    # The newest version's group first, each group oldest first, no version, then the copies:
    # the Recipe page. The same fixture as the page's own test (`RecipeInsights.test.tsx`).
    assert [item["id"] for item in body["insights"]] == [b, a, a2, loose, copy]


async def test_a_version_with_no_profile_says_so_and_names_none(adopted: Adopted) -> None:
    _app, client, _fake = adopted
    recipe_id, version_id = await _no_profile_recipe(client)

    body = data(await _preview(client, recipe_id, version_id))

    assert body["has_profile"] is False and body["version_fields"]["profile_label"] is None
    assert body["version_fields"]["grind_setting"] == "22" and body["insights"] == []


async def test_the_preview_answers_404_and_409_like_the_duplicate(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    _, other_version = await _recipe_with_version(app, client, "Kenya")

    for url_recipe, url_version in (
        (9999, version_id),
        (recipe_id, 9999),
        (recipe_id, other_version),
    ):
        assert (await _preview(client, url_recipe, url_version)).status_code == 404
        assert (await _duplicate(client, url_recipe, url_version, bean_id=1)).status_code == 404

    await app.state.db.execute("UPDATE recipes SET designing = 1 WHERE id = ?", (recipe_id,))
    preview = await _preview(client, recipe_id, version_id)
    refused = await _duplicate(client, recipe_id, version_id)
    assert (preview.status_code, refused.status_code) == (409, 409)
    assert error(preview)["code"] == error(refused)["code"] == "DESIGNING"
    assert error(preview)["message"] == error(refused)["message"]


# ── the duplicate ────────────────────────────────────────────────────


async def test_a_duplicate_is_a_new_recipe_on_a_new_profile_with_a_note_and_no_history(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, _ = await _recipe_with_version(app, client)
    data(
        await client.post(
            f"/api/recipes/{recipe_id}/versions", json={"dose_g": 18.5, "intent": "more"}
        )
    )
    detail = data(await client.get(f"/api/recipes/{recipe_id}"))
    (v1,) = [
        v["version"]["id"] for v in detail["versions"] if v["version"]["version_label"] == "v1"
    ]
    base = await base_version_id(app, BASE_LABEL)

    response = await _duplicate(client, recipe_id, v1, recipe_name="  Guji milk  ")

    assert response.status_code == 201
    body = data(response)
    assert set(body) == {"recipe", "board_id"}
    made = body["recipe"]
    assert (made["name"], made["automatch"], made["archived"]) == ("Guji milk", True, False)
    assert made["id"] != recipe_id and made["version_count"] == 1 and made["shot_count"] == 0
    assert made["copied_from"] == {
        "recipe_id": recipe_id,
        "recipe_name": "Guji",
        "version_id": v1,
        "version_label": "v1",
    }
    new_detail = data(await client.get(f"/api/recipes/{made['id']}"))
    (only,) = new_detail["versions"]
    version = only["version"]
    assert (version["dose_g"], version["target_yield_g"]) == (18.0, 36.0)
    assert (version["prediction"], version["origin"], version["intent"]) == ("", "manual", "")
    assert version["parent_version_id"] is None and version["version_label"] == "v1"
    assert version["profile_label"] == "Blooming milk" and only["shots"] == []
    assert new_detail["recipe"]["copied_from"] == made["copied_from"]
    # The new profile is on the list, switched on and starred, waiting for nobody.
    board = await get_board(client)
    row = row_for(board, "Blooming milk")
    assert row["row"]["id"] == body["board_id"]
    assert (row["on_machine"], row["starred"], row["row"]["origin"]) == (True, True, "draft")
    assert row["row"]["current_version_id"] == version["profile_version_id"]
    assert board["proposals"] == []
    # The source Recipe is untouched, and still names its own profile.
    after = data(await client.get(f"/api/recipes/{recipe_id}"))
    assert len(after["versions"]) == 2 and after["recipe"]["copied_from"] is None
    assert after["versions"][0]["version"]["profile_version_id"] == base
    # The recipe is the version's, not the current one's.
    listed = data(await client.get("/api/recipes"))["items"]
    assert {item["name"]: item["copied_from"] is not None for item in listed} == {
        "Guji": False,
        "Guji milk": True,
    }


async def test_the_new_profile_is_the_versions_profile_under_the_new_name(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    base = await base_version_id(app, BASE_LABEL)

    made = data(await _duplicate(client, recipe_id, version_id))["recipe"]

    profiles = ProfilesRepository(app.state.db)
    original = await profiles.get_version(base)
    copy_id = data(await client.get(f"/api/recipes/{made['id']}"))["versions"][0]["version"][
        "profile_version_id"
    ]
    copy = await profiles.get_version(copy_id)
    assert original is not None and copy is not None and copy.id != original.id
    assert original.profile is not None and copy.profile is not None
    assert {**copy.profile, "label": ""} == {**original.profile, "label": ""}
    assert copy.profile["label"] == "Blooming milk"


async def test_the_signature_is_carried_to_the_new_profile(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    base = await base_version_id(app, BASE_LABEL)
    service = SignatureService(app.state.db)
    phase = (await service.phase_names(base) or [])[0]
    await service.propose(
        base, [ExpectationInput(tier="critical", kind="reached", phase=phase)], reason="r"
    )

    made = data(await _duplicate(client, recipe_id, version_id))["recipe"]

    copy_id = data(await client.get(f"/api/recipes/{made['id']}"))["versions"][0]["version"][
        "profile_version_id"
    ]
    assert len(await service.repo.confirmed_for_versions([copy_id])) == 1


async def test_duplicating_a_pinned_version_copies_the_profile_it_brews_not_the_pin(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    pin = data(await client.post(_url(recipe_id, version_id, "pin"), json={}))["pin"]
    base = await base_version_id(app, BASE_LABEL)
    assert pin["profile_version_id"] != base

    made = data(await _duplicate(client, recipe_id, version_id))["recipe"]

    profiles = ProfilesRepository(app.state.db)
    original = await profiles.get_version(base)
    copy_id = data(await client.get(f"/api/recipes/{made['id']}"))["versions"][0]["version"][
        "profile_version_id"
    ]
    copy = await profiles.get_version(copy_id)
    assert original is not None and copy is not None and copy.profile and original.profile
    assert {**copy.profile, "label": ""} == {**original.profile, "label": ""}
    assert copy_id not in (base, pin["profile_version_id"])
    # The new Recipe's version names the new profile, never the pin's frozen copy.
    assert (
        int(
            await app.state.db.fetch_value(
                "SELECT COUNT(*) FROM recipe_versions WHERE profile_version_id = ?",
                (pin["profile_version_id"],),
            )
        )
        == 0
    )


async def test_a_name_freed_by_an_unpin_is_still_the_pins_and_cannot_be_the_new_profile(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    pin = data(await client.post(_url(recipe_id, version_id, "pin"), json={}))["pin"]
    data(await client.post(f"/api/pins/{pin['id']}/unpin"))
    before = await _counts(app)

    refused = await _duplicate(client, recipe_id, version_id, profile_name=pin["name"])

    assert refused.status_code == 409 and error(refused)["code"] == "PROFILE_NAME_TAKEN"
    assert pin["name"] in error(refused)["message"] and "Guji v1" in error(refused)["message"]
    assert await _counts(app) == before


async def test_a_duplicate_of_an_any_profile_version_has_no_profile(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _no_profile_recipe(client)
    before_profiles = int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board"))

    response = await client.post(
        _url(recipe_id, version_id),
        json={
            "recipe_name": "Bare copy",
            "bean_id": data(await client.get(f"/api/recipes/{recipe_id}"))["recipe"]["bean_id"],
            "insight_ids": [],
        },
    )

    body = data(response)
    assert body["board_id"] is None
    version = data(await client.get(f"/api/recipes/{body['recipe']['id']}"))["versions"][0][
        "version"
    ]
    assert version["profile_version_id"] is None and version["grind_setting"] == "22"
    assert (
        int(await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board")) == before_profiles
    )


async def test_the_copy_suggestion_is_a_valid_recipe_name_at_the_limit(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    long_name = "L" * 199 + "z"
    await app.state.db.execute("UPDATE recipes SET name = ? WHERE id = ?", (long_name, recipe_id))

    suggested = data(await _preview(client, recipe_id, version_id))["recipe_name"]

    assert len(suggested) == 200 and suggested.endswith(" (copy)")
    made = data(await _duplicate(client, recipe_id, version_id, recipe_name=suggested))["recipe"]
    assert made["name"] == suggested
    short = data(await _preview(client, *(await _recipe_with_version(app, client, "Kenya"))))
    assert short["recipe_name"] == "Kenya (copy)"


async def test_the_sentences_a_person_reads_are_person_worded(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    cases: list[tuple[dict[str, Any], str]] = [
        ({"recipe_name": " "}, "A Recipe needs a name."),
        ({"recipe_name": "x" * 201}, "A Recipe name is at most 200 characters."),
        ({"profile_name": " "}, "A profile needs a name."),
        (
            {"insight_ids": [9999]},
            "One of the chosen insights is no longer one of this Recipe's confirmed insights.",
        ),
        ({"bean_id": 9999}, "That bean is not there any more. Pick another."),
        ({"grinder_id": 9999}, "That grinder is not there any more. Pick another."),
    ]
    for body, sentence in cases:
        refused = await _duplicate(client, recipe_id, version_id, **body)
        assert refused.status_code == 422 and error(refused)["message"] == sentence
    gone = await _preview(client, 9999, version_id)
    assert error(gone)["message"] == "That Recipe is not there any more."
    foreign = await _preview(client, recipe_id, 9999)
    assert error(foreign)["message"] == "That version is not one of this Recipe's."
    _, other = await _recipe_with_version(app, client, "Other")
    assert (
        error(await _preview(client, recipe_id, other))["message"]
        == foreign.json()["error"]["message"]
    )


async def test_an_archived_source_may_be_duplicated(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    data(await client.post(f"/api/recipes/{recipe_id}/archive"))

    made = data(await _duplicate(client, recipe_id, version_id))["recipe"]

    assert made["archived"] is False and made["automatch"] is True
    assert made["copied_from"]["recipe_id"] == recipe_id


async def test_the_new_profile_reaches_the_machine_through_the_ordinary_sync(
    adopted: Adopted,
) -> None:
    app, client, fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    data(await _duplicate(client, recipe_id, version_id))

    run = await pull(app)

    assert run.status == "ok", run.error
    assert "Blooming milk" in [p["label"] for p in fake.profiles]


async def test_a_new_bean_and_grinder_are_the_dialogs_not_the_sources(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    bean = data(await client.post("/api/beans", json={"name": "Kenya AA", "roaster": "x"}))
    grinder = data(
        await client.post("/api/grinders", json={"name": "Niche", "step_unit": "numbers"})
    )

    made = data(
        await _duplicate(
            client, recipe_id, version_id, bean_id=bean["id"], grinder_id=grinder["id"]
        )
    )["recipe"]

    assert (made["bean_id"], made["grinder_id"]) == (bean["id"], grinder["id"])


# ── refusals ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"recipe_name": "   "}, "recipe_name"),
        ({"profile_name": "   "}, "profile_name"),
        ({"profile_name": None}, "profile_name"),
        ({"insight_ids": [987654]}, "insight_ids"),
        ({"bean_id": 987654}, "bean_id"),
        ({"grinder_id": 987654}, "grinder_id"),
    ],
)
async def test_a_bad_field_is_unprocessable_and_writes_nothing(
    adopted: Adopted, body: dict[str, Any], field: str
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    before = await _counts(app)

    response = await _duplicate(client, recipe_id, version_id, **body)

    assert response.status_code == 422 and error(response)["details"]["field"] == field
    assert await _counts(app) == before


async def test_a_profile_name_for_a_version_with_no_profile_is_unprocessable(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _no_profile_recipe(client)
    before = await _counts(app)

    response = await _duplicate(client, recipe_id, version_id, profile_name="Anything")

    assert response.status_code == 422 and error(response)["details"]["field"] == "profile_name"
    assert await _counts(app) == before


async def test_an_insight_that_is_not_one_of_the_previews_is_unprocessable(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    other_recipe, _ = await _recipe_with_version(app, client, "Other")
    mine = await _insight(app, recipe_id, "Mine")
    foreign = [
        await _insight(app, other_recipe, "Another Recipe's"),
        await _insight(app, None, "General"),
        await _insight(app, recipe_id, "Unconfirmed", confirmed=False),
        await _insight(app, recipe_id, "Dismissed", dismissed=True),
    ]
    before = await _counts(app)

    for bad in foreign:
        refused = await _duplicate(client, recipe_id, version_id, insight_ids=[mine, bad])
        assert refused.status_code == 422 and error(refused)["details"]["field"] == "insight_ids"
    assert await _counts(app) == before


async def test_a_taken_profile_name_is_the_persons_sentence_and_writes_nothing(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    before = await _counts(app)

    for typed in (BASE_LABEL, BASE_LABEL.upper()):
        refused = await _duplicate(client, recipe_id, version_id, profile_name=typed)
        assert refused.status_code == 409 and error(refused)["code"] == "PROFILE_NAME_TAKEN"
        assert error(refused)["message"] == (
            f"There is already a profile called {BASE_LABEL}. Choose another name."
        )
        assert error(refused)["details"] == {"name": typed}
    assert await _counts(app) == before


async def test_a_profile_the_policy_would_change_is_refused_in_the_pins_words(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 7.5})
    before = await _counts(app)

    refused = await _duplicate(client, recipe_id, version_id)
    pin_refused = await client.post(_url(recipe_id, version_id, "pin"), json={})

    assert refused.status_code == 409 and error(refused)["code"] == "PROFILE_POLICY_REFUSED"
    assert "outside the current safety bounds" in error(refused)["message"]
    assert error(refused)["details"]["clamp_changes"]
    assert error(pin_refused)["code"] == "PIN_POLICY_REFUSED"
    assert error(refused)["message"] == error(pin_refused)["message"]
    assert error(refused)["details"] == error(pin_refused)["details"]
    assert await _counts(app) == before


# ── one transaction ──────────────────────────────────────────────────


async def test_the_last_write_failing_undoes_the_profile_the_recipe_and_the_insights(
    adopted: Adopted, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    ids = [await _insight(app, recipe_id, f"Insight {n}") for n in range(3)]
    before = await _counts(app)
    original = InsightsRepository.insert
    calls = 0

    async def fail_on_the_last(self: InsightsRepository, insight: InsightWrite) -> int:
        nonlocal calls
        calls += 1
        if calls == len(ids):
            raise RuntimeError("the last write fails")
        return await original(self, insight)

    monkeypatch.setattr(InsightsRepository, "insert", fail_on_the_last)

    failed = await _duplicate(client, recipe_id, version_id, insight_ids=ids)

    assert failed.status_code == 500

    assert calls == len(ids), "the failing write was the last one, after the profile and the Recipe"
    assert await _counts(app) == before
    board = await get_board(client)
    assert "Blooming milk" not in [r["row"]["label"] for r in board["rows"]]


# ── the copied insights ──────────────────────────────────────────────


async def test_copied_insights_are_plain_confirmed_rows_with_no_past(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    a = await _insight(app, recipe_id, "Grind finer than 2.3 runs sour", source="user")
    await _insight(app, recipe_id, "Not chosen")
    c = await _insight(app, recipe_id, "Preinfusion under 6 s channels", source="analysis")
    await app.state.db.execute(
        "UPDATE knowledge_insights SET recipe_version_id = ?, evidence_shot_ids_json = '[1, 2]', "
        "rests_on_json = ? WHERE id = ?",
        (version_id, f'[{{"recipe_version_id": {version_id}, "outcome": "held"}}]', a),
    )

    made = data(await _duplicate(client, recipe_id, version_id, insight_ids=[c, a]))["recipe"]

    rows = await app.state.db.fetch_all(
        "SELECT * FROM knowledge_insights WHERE recipe_id = ? ORDER BY id", (made["id"],)
    )
    # In the order the source Recipe lists them, however the request named them.
    assert [(r["text"], r["source"]) for r in rows] == [
        ("Grind finer than 2.3 runs sour", "user"),
        ("Preinfusion under 6 s channels", "analysis"),
    ]
    for row in rows:
        assert row["confirmed"] == 1 and row["confirmed_at"] is not None and row["dismissed"] == 0
        assert row["evidence_shot_ids_json"] == "[]" and row["rests_on_json"] == "[]"
        assert row["replaces_id"] is None and row["replaces_text"] == ""
        assert row["recipe_version_id"] is None and row["thread_id"] is None
        assert row["pattern_run_id"] is None and row["analysis_id"] is None
    # The originals are untouched.
    assert (
        int(
            await app.state.db.fetch_value(
                "SELECT COUNT(*) FROM knowledge_insights WHERE recipe_id = ?", (recipe_id,)
            )
        )
        == 3
    )
    insights = InsightsRepository(app.state.db)
    assert [i.text for i in await insights.for_recipe(made["id"]) if i.recipe_id == made["id"]] == [
        "Grind finer than 2.3 runs sour",
        "Preinfusion under 6 s channels",
    ]
    # Never a conversation's own proposal: no thread wrote them.
    thread_count = await app.state.db.fetch_value(
        "SELECT COUNT(*) FROM knowledge_insights WHERE recipe_id = ? AND thread_id IS NOT NULL",
        (made["id"],),
    )
    assert thread_count == 0


async def test_no_insights_chosen_copies_none_and_a_repeated_id_copies_one(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    only = await _insight(app, recipe_id, "Only one")

    none = data(await _duplicate(client, recipe_id, version_id, profile_name="P1"))["recipe"]
    twice = data(
        await _duplicate(
            client,
            recipe_id,
            version_id,
            profile_name="P2",
            recipe_name="Two",
            insight_ids=[only, only],
        )
    )["recipe"]

    count = "SELECT COUNT(*) FROM knowledge_insights WHERE recipe_id = ?"
    assert int(await app.state.db.fetch_value(count, (none["id"],))) == 0
    assert int(await app.state.db.fetch_value(count, (twice["id"],))) == 1


# ── the flag on a copy ───────────────────────────────────────────────


async def _copy_flags(app: FastAPI, recipe_id: int) -> list[Any]:
    rows = await app.state.db.fetch_all(
        "SELECT copied_from_insight_id FROM knowledge_insights WHERE recipe_id = ? ORDER BY id",
        (recipe_id,),
    )
    return [row["copied_from_insight_id"] for row in rows]


async def _home_flags(app: FastAPI, recipe_id: int) -> list[Any]:
    rows = await app.state.db.fetch_all(
        "SELECT copied_from_recipe_id FROM knowledge_insights WHERE recipe_id = ? ORDER BY id",
        (recipe_id,),
    )
    return [row["copied_from_recipe_id"] for row in rows]


async def test_a_copy_names_its_original_and_a_copy_of_a_copy_names_the_first(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    first = await _insight(app, recipe_id, "One")
    second = await _insight(app, recipe_id, "Two")
    assert await _copy_flags(app, recipe_id) == [None, None]

    b = data(
        await _duplicate(
            client, recipe_id, version_id, insight_ids=[first, second], profile_name="B"
        )
    )["recipe"]
    assert await _copy_flags(app, b["id"]) == [first, second]
    b_version = data(await client.get(f"/api/recipes/{b['id']}"))["versions"][0]["version"]["id"]
    c = data(
        await _duplicate(
            client,
            b["id"],
            b_version,
            recipe_name="C",
            profile_name="C profile",
            insight_ids=[
                item["id"] for item in data(await _preview(client, b["id"], b_version))["insights"]
            ],
        )
    )["recipe"]

    # The first original, not the copy it was copied from, and the Recipe it was learned in.
    assert await _copy_flags(app, c["id"]) == [first, second]
    assert await _home_flags(app, b["id"]) == [recipe_id, recipe_id]
    assert await _home_flags(app, c["id"]) == [recipe_id, recipe_id]
    assert await _home_flags(app, recipe_id) == [None, None]
    # The originals carry no flag, and the API serves it.
    assert await _copy_flags(app, recipe_id) == [None, None]
    served = data(await client.get("/api/knowledge/insights", params={"recipe_id": c["id"]}))[
        "items"
    ]
    assert [item["copied_from_insight_id"] for item in served] == [first, second]
    assert [item["copied_from_recipe_id"] for item in served] == [recipe_id, recipe_id]


async def test_the_flag_outlives_its_original(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    only = await _insight(app, recipe_id, "Only")
    b = data(await _duplicate(client, recipe_id, version_id, insight_ids=[only]))["recipe"]

    await app.state.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (only,))

    assert await _copy_flags(app, b["id"]) == [only]
    assert await _home_flags(app, b["id"]) == [recipe_id]


async def test_no_route_takes_the_flag_as_input(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    for field in ("copied_from_insight_id", "copied_from_recipe_id"):
        refused = await client.post("/api/knowledge/insights", json={"text": "x", field: 1})
        assert refused.status_code in (400, 422)
    body = await _duplicate(client, recipe_id, version_id, copied_from_insight_id=1)
    assert body.status_code in (400, 422)


async def test_an_edited_copy_keeps_its_flag(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    only = await _insight(app, recipe_id, "Grind finer than 2.3 runs sour")
    b = data(await _duplicate(client, recipe_id, version_id, insight_ids=[only]))["recipe"]
    (copy,) = [
        item["id"]
        for item in data(
            await client.get("/api/knowledge/insights", params={"recipe_id": b["id"]})
        )["items"]
    ]

    edited = await client.patch(
        f"/api/knowledge/insights/{copy}", json={"text": "Grind finer than 2.4 runs sour"}
    )

    # A reworded copy is still that lesson: erring towards one count is the safe side.
    assert data(edited)["text"] == "Grind finer than 2.4 runs sour"
    assert data(edited)["copied_from_insight_id"] == only
    assert await _copy_flags(app, b["id"]) == [only]


# ── Duplicate Profile ────────────────────────────────────────────────


def _profile_url(version_id: int) -> str:
    return f"/api/profile-versions/{version_id}/duplicate"


async def test_duplicate_profile_puts_a_new_profile_on_the_list_with_the_edited_document(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    base = await base_version_id(app, BASE_LABEL)
    original = await ProfilesRepository(app.state.db).get_version(base)
    assert original is not None and original.profile is not None
    edited = {**original.profile, "label": "Ignored", "description": "Edited before saving"}

    answered = await client.post(
        _profile_url(base), json={"name": "  Blooming milk ", "profile": edited}
    )

    assert answered.status_code == 201
    row = data(answered)
    assert row["label"] == "Blooming milk" and row["on_machine"] is True
    copy = await ProfilesRepository(app.state.db).get_version(row["current_version_id"])
    assert copy is not None and copy.profile is not None and copy.id != base
    assert copy.profile["label"] == "Blooming milk", "the name, not the document's label"
    assert copy.profile["description"] == "Edited before saving"
    board = await get_board(client)
    assert "Blooming milk" in [r["row"]["label"] for r in board["rows"]]
    drafts = data(await client.get("/api/profile-drafts", params={"status": "draft"}))
    assert drafts["items"] == [], "never a proposal waiting on the Profiles page"
    choices = data(await client.get("/api/recipes/profile-choices"))
    assert {"profile_version_id": copy.id, "label": "Blooming milk"} in choices["items"]


async def test_duplicate_profile_without_a_document_copies_the_version(adopted: Adopted) -> None:
    app, client, _fake = adopted
    base = await base_version_id(app, BASE_LABEL)

    row = data(await client.post(_profile_url(base), json={"name": "Plain copy"}))

    profiles = ProfilesRepository(app.state.db)
    original = await profiles.get_version(base)
    copy = await profiles.get_version(row["current_version_id"])
    assert original is not None and copy is not None and copy.profile and original.profile
    assert {**copy.profile, "label": ""} == {**original.profile, "label": ""}


async def test_duplicate_profile_refuses_the_originals_name_and_any_taken_one(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    base = await base_version_id(app, BASE_LABEL)
    before = await _counts(app)

    for typed in (BASE_LABEL, f" {BASE_LABEL.lower()} "):
        refused = await client.post(_profile_url(base), json={"name": typed})
        assert refused.status_code == 409 and error(refused)["code"] == "PROFILE_NAME_TAKEN"
        assert error(refused)["message"] == (
            f"There is already a profile called {BASE_LABEL}. Choose another name."
        )
    assert await _counts(app) == before


async def test_duplicate_profile_refusals_write_nothing(adopted: Adopted) -> None:
    app, client, _fake = adopted
    base = await base_version_id(app, BASE_LABEL)
    before = await _counts(app)

    empty = await client.post(_profile_url(base), json={"name": "   "})
    broken = await client.post(_profile_url(base), json={"name": "X", "profile": {"label": "X"}})
    missing = await client.post(_profile_url(999_999), json={"name": "X"})

    assert empty.status_code == 422
    assert broken.status_code == 422 and error(broken)["details"]["schema_errors"]
    assert missing.status_code == 404
    await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 7.5})
    clamped = await client.post(_profile_url(base), json={"name": "Too hot"})
    assert clamped.status_code == 409 and error(clamped)["code"] == "PROFILE_POLICY_REFUSED"
    assert await _counts(app) == before


async def test_duplicate_profile_refuses_a_pins_copy(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    pin = data(await client.post(_url(recipe_id, version_id, "pin"), json={}))["pin"]
    before = await _counts(app)

    refused = await client.post(_profile_url(pin["profile_version_id"]), json={"name": "Again"})

    assert refused.status_code == 409 and error(refused)["code"] == "PROFILE_IS_A_PIN"
    assert await _counts(app) == before


async def test_profile_choices_list_the_taken_profiles_with_their_recipe(adopted: Adopted) -> None:
    app, client, _fake = adopted
    recipe_id, version_id = await _recipe_with_version(app, client)
    data(await client.post(_url(recipe_id, version_id, "pin"), json={}))
    base = await base_version_id(app, BASE_LABEL)

    choices = data(await client.get("/api/recipes/profile-choices"))

    assert all(c["profile_version_id"] != base for c in choices["items"])
    assert choices["taken"] == [
        {
            "profile_version_id": base,
            "label": BASE_LABEL,
            "recipe_id": recipe_id,
            "recipe_name": "Guji",
        }
    ], "the profile with its Recipe, and never the pin's copy"

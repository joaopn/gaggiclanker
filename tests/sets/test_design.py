"""A Set designed in its own conversation: created empty, filled in place, discardable.

The rules pinned here are the repository's, because that is where they live and
every path reaches them: a Set being designed has a version 1 that states
nothing, the first version written to it — by any path — fills that version 1
instead of appending a v2, and a design nobody brewed anything under can be
deleted, and nothing else can.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatRepository
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.sets import (
    DesignBrief,
    SetRow,
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
    VersionRefused,
)
from tests.sets.conftest import Fixtures, make_profile_version, make_shot


async def designed(wired: Fixtures, **brief: Any) -> SetRow:
    return await wired.sets.create_design(
        SetWrite(name="Guji, designed", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        DesignBrief.model_validate(brief),
    )


# -- creating ----------------------------------------------------------------


async def test_a_designed_set_has_the_flag_the_brief_and_an_empty_version_1(
    wired: Fixtures,
) -> None:
    fork = await make_profile_version(wired.db, "Fork me")
    row = await designed(wired, fork_profile_version_id=fork, usual_grind=" 22 ", goal="Body.")

    assert row.designing is True
    assert row.automatch is True
    assert row.design_brief == DesignBrief(
        fork_profile_version_id=fork, usual_grind="22", goal="Body."
    )
    versions = await wired.sets.versions(row.id)
    assert len(versions) == 1
    version = versions[0]
    assert version.version_no == 1
    assert (
        version.profile_version_id,
        version.grind_setting,
        version.grind_value,
        version.dose_g,
        version.target_yield_g,
    ) == (None, None, None, None, None)
    assert (version.intent, version.origin, version.prediction) == ("", "manual", "")


async def test_a_set_made_any_other_way_is_not_being_designed(wired: Fixtures) -> None:
    row = await wired.sets.create(
        SetWrite(name="By hand", bean_id=wired.bean_id), SetVersionWrite()
    )

    assert row.designing is False
    assert row.design_brief == DesignBrief()


def test_the_brief_refuses_what_it_does_not_know_and_what_is_too_long() -> None:
    with pytest.raises(ValidationError):
        DesignBrief.model_validate({"goal": "x", "dose": 18})
    with pytest.raises(ValidationError):
        DesignBrief(usual_grind="x" * 101)
    with pytest.raises(ValidationError):
        DesignBrief(goal="x" * 2001)


async def test_a_damaged_brief_reads_as_an_empty_one_rather_than_breaking_the_list(
    wired: Fixtures,
) -> None:
    row = await designed(wired, goal="Body.")
    await wired.db.execute("UPDATE sets SET design_brief = 'not json' WHERE id = ?", (row.id,))

    listed = await wired.sets.list_sets()

    assert [item.design_brief for item in listed if item.id == row.id] == [DesignBrief()]


# -- filling -----------------------------------------------------------------


async def test_the_first_version_written_fills_version_1_in_place(wired: Fixtures) -> None:
    row = await designed(wired)
    v1 = await wired.sets.current_version(row.id)
    assert v1 is not None
    profile = await make_profile_version(wired.db, "Designed")

    filled = await wired.sets.add_version(
        row.id,
        SetVersionPatch(
            profile_version_id=profile,
            grind_setting="20",
            grind_value=20,
            dose_g=18,
            target_yield_g=40,
            intent="The recipe we worked out.",
            prediction="Compared to nothing: this should be sweet and about 30 s long.",
            origin="chat",
        ),
    )

    assert filled is not None
    assert (filled.id, filled.version_no) == (v1.id, 1)
    assert (filled.profile_version_id, filled.grind_setting, filled.grind_value) == (
        profile,
        "20",
        20,
    )
    assert (filled.dose_g, filled.target_yield_g) == (18, 40)
    assert (filled.intent, filled.origin) == ("The recipe we worked out.", "chat")
    # A version 1 is a baseline, not a change: whatever prediction came with
    # the write is not recorded on it.
    assert (filled.prediction, filled.compares_to_version_id, filled.prediction_at) == (
        "",
        None,
        None,
    )
    after = await wired.sets.get(row.id)
    assert after is not None
    assert after.designing is False
    assert after.version_count == 1


async def test_once_filled_the_set_appends_like_any_other(wired: Fixtures) -> None:
    row = await designed(wired)
    await wired.sets.add_version(row.id, SetVersionPatch(dose_g=18))

    second = await wired.sets.add_version(row.id, SetVersionPatch(dose_g=19))

    assert second is not None
    assert second.version_no == 2
    # The recipe v1 was filled with is what v2 inherits.
    assert second.dose_g == 19


async def test_a_hand_made_set_that_names_no_profile_still_appends(wired: Fixtures) -> None:
    """The "any profile" Set looks like an empty design and must not be treated as one."""
    row = await wired.sets.create(
        SetWrite(name="Any profile", bean_id=wired.bean_id), SetVersionWrite()
    )

    second = await wired.sets.add_version(row.id, SetVersionPatch(dose_g=18))

    assert second is not None
    assert second.version_no == 2


async def test_a_design_with_a_shot_filed_by_hand_refuses_to_be_filled(
    wired: Fixtures,
) -> None:
    row = await designed(wired)
    v1 = await wired.sets.current_version(row.id)
    assert v1 is not None
    shot = await make_shot(wired.db, "000101")
    assert await wired.sets.assign_shot(shot, v1.id)

    with pytest.raises(VersionRefused) as refused:
        await wired.sets.add_version(row.id, SetVersionPatch(dose_g=18))

    assert refused.value.refused == "design_has_shots"
    assert refused.value.code == "DESIGN_HAS_SHOTS"
    # Nothing was written: the version is as empty as it was and still alone.
    after = await wired.sets.get(row.id)
    assert after is not None
    assert (after.designing, after.version_count) == (True, 1)
    unchanged = await wired.sets.current_version(row.id)
    assert unchanged is not None and unchanged.dose_g is None


async def test_a_design_with_more_than_one_version_refuses_to_be_filled(
    wired: Fixtures,
) -> None:
    """Not reachable through the application; a hand-edited archive can do it."""
    row = await designed(wired)
    await wired.db.execute("UPDATE sets SET designing = 0 WHERE id = ?", (row.id,))
    await wired.sets.add_version(row.id, SetVersionPatch(dose_g=18))
    await wired.sets.add_version(row.id, SetVersionPatch(dose_g=19))
    await wired.db.execute("UPDATE sets SET designing = 1 WHERE id = ?", (row.id,))

    assert await wired.sets.design_refusal(row.id) == "design_has_versions"
    with pytest.raises(VersionRefused):
        await wired.sets.add_version(row.id, SetVersionPatch(dose_g=20))


# -- discarding --------------------------------------------------------------


async def test_a_design_nothing_was_filed_under_is_deleted_with_its_conversations(
    wired: Fixtures,
) -> None:
    row = await designed(wired)
    chats = ChatRepository(wired.db)
    opened = await chats.open_thread(row.id)
    assert opened.thread is not None

    assert await wired.sets.discard_design(row.id) is None

    assert await wired.sets.get(row.id) is None
    assert await wired.sets.versions(row.id) == []
    assert await chats.get_thread(opened.thread.id) is None


async def test_discarding_refuses_a_set_that_is_not_being_designed(wired: Fixtures) -> None:
    row = await wired.sets.create(
        SetWrite(name="By hand", bean_id=wired.bean_id), SetVersionWrite()
    )

    assert await wired.sets.discard_design(row.id) == "not_designing"
    assert await wired.sets.discard_design(row.id + 100) == "no_set"
    assert await wired.sets.get(row.id) is not None


async def test_discarding_refuses_a_design_somebody_filed_a_shot_under(wired: Fixtures) -> None:
    row = await designed(wired)
    v1 = await wired.sets.current_version(row.id)
    assert v1 is not None
    shot = await make_shot(wired.db, "000102")
    assert await wired.sets.assign_shot(shot, v1.id)

    assert await wired.sets.discard_design(row.id) == "design_has_shots"
    assert await wired.sets.get(row.id) is not None


async def test_a_filled_design_is_no_longer_discardable(wired: Fixtures) -> None:
    row = await designed(wired)
    await wired.sets.add_version(row.id, SetVersionPatch(dose_g=18))

    assert await wired.sets.discard_design(row.id) == "not_designing"


# -- through the routes ------------------------------------------------------


async def _designed_through(app: FastAPI) -> SetRow:
    """A design Set made in the app's own archive, for the route tests."""
    db = app.state.db
    bean = await BeansRepository(db).create(BeanWrite(name="Kenya AA", roast_level="light"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche Zero"))
    return await SetsRepository(db).create_design(
        SetWrite(name="Kenya AA on the Niche Zero", bean_id=bean.id, grinder_id=grinder.id),
        DesignBrief(goal="Juicy."),
    )


async def test_the_add_a_version_form_fills_a_designed_set_s_version_1(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed_through(app)

    response = await client.post(
        f"/api/sets/{row.id}/versions",
        json={"grind_setting": "18", "dose_g": 18, "target_yield_g": 40, "intent": "By hand."},
    )

    assert response.status_code == 201, response.text
    version = response.json()["data"]
    assert (version["id"], version["version_no"]) == (row.current_version_id, 1)
    assert version["origin"] == "manual"
    detail = response.json()
    assert detail["ok"] is True
    listed = (await client.get(f"/api/sets/{row.id}")).json()["data"]
    assert listed["set"]["designing"] is False
    assert [item["version"]["version_no"] for item in listed["versions"]] == [1]


async def test_the_add_a_version_form_is_refused_once_a_shot_is_filed_on_the_design(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed_through(app)
    assert row.current_version_id is not None
    shot = await make_shot(app.state.db, "000201")
    await SetsRepository(app.state.db).assign_shot(shot, row.current_version_id)

    response = await client.post(f"/api/sets/{row.id}/versions", json={"dose_g": 18})

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "DESIGN_HAS_SHOTS"
    assert body["error"]["details"] == {
        "field": "set_id",
        "message": "version 1 of this Set can no longer be filled",
    }


async def test_the_set_rows_carry_the_flag_and_the_brief(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed_through(app)

    listed = (await client.get("/api/sets")).json()["data"]["items"]

    served = next(item for item in listed if item["id"] == row.id)
    assert served["designing"] is True
    assert served["design_brief"] == {
        "fork_profile_version_id": None,
        "usual_grind": "",
        "goal": "Juicy.",
    }

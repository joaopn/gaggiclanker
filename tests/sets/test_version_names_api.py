"""The three places a person names a version: the form, the card, the push for a Set.

Each route takes ``major`` (true, false, or left out for the rule), refuses
anything that is not a boolean with the envelope's 400, and answers with the
name it gave. The Set page and the proposal card are served both names the
next version could take, so the web never numbers anything itself. The push
for a Set is in `tests/drafts/test_api.py`, beside the rest of the push.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetVersionPatch
from tests.sets.conftest import make_profile_version
from tests.sets.test_api import data, error

PREDICTION = "Compared to v1: two to four seconds longer and less sour."


@pytest.fixture
async def set_id(client: httpx.AsyncClient) -> int:
    bean = data(await client.post("/api/beans", json={"name": "Ethiopia Guji"}))
    created = data(
        await client.post(
            "/api/sets",
            json={
                "name": "Guji on the Niche",
                "bean_id": bean["id"],
                "version": {"dose_g": 18.0, "grind_setting": "22"},
            },
        )
    )
    return int(created["id"])


async def _propose(app: FastAPI, set_id: int, **over: Any) -> int:
    spec: dict[str, Any] = {
        "patch": SetVersionPatch(grind_setting="21"),
        "reason": "one click finer, chasing the sourness out",
        "prediction": PREDICTION,
    }
    spec.update(over)
    result = await SetProposalsRepository(app.state.db).create(set_id, ProposalWrite(**spec))
    assert result.proposal is not None, result.refused
    return result.proposal.id


class TestTheForm:
    async def test_it_answers_with_the_name_it_gave(
        self, client: httpx.AsyncClient, set_id: int
    ) -> None:
        url = f"/api/sets/{set_id}/versions"
        minor = await client.post(url, json={"grind_setting": "21"})
        assert minor.status_code == 201
        assert data(minor)["version_label"] == "v1.1"
        marked = data(await client.post(url, json={"grind_setting": "20", "major": True}))
        assert (marked["version_label"], marked["version_no"]) == ("v2", 3)
        unmarked = data(await client.post(url, json={"dose_g": 19, "major": False}))
        assert unmarked["version_label"] == "v2.1"

    async def test_major_is_not_stored_as_part_of_the_recipe(
        self, client: httpx.AsyncClient, set_id: int
    ) -> None:
        """Sending only `major` changes nothing in the recipe: every field is inherited."""
        version = data(await client.post(f"/api/sets/{set_id}/versions", json={"major": True}))
        assert (version["grind_setting"], version["dose_g"]) == ("22", 18.0)
        assert "major" not in version

    async def test_a_different_profile_is_a_major_by_default(
        self, client: httpx.AsyncClient, app: FastAPI, set_id: int
    ) -> None:
        profile = await make_profile_version(app.state.db, "Turbo")
        version = data(
            await client.post(f"/api/sets/{set_id}/versions", json={"profile_version_id": profile})
        )
        assert version["version_label"] == "v2"

    @pytest.mark.parametrize("value", ["yes", 2, [True], {"major": True}])
    async def test_anything_but_a_boolean_is_refused(
        self, client: httpx.AsyncClient, set_id: int, value: object
    ) -> None:
        response = await client.post(
            f"/api/sets/{set_id}/versions", json={"grind_setting": "21", "major": value}
        )
        assert response.status_code == 400
        assert error(response)["code"] == "INVALID_REQUEST"
        detail = data(await client.get(f"/api/sets/{set_id}"))
        assert len(detail["versions"]) == 1


class TestTheSetPage:
    async def test_it_serves_both_names_the_next_version_could_take(
        self, client: httpx.AsyncClient, set_id: int
    ) -> None:
        detail = data(await client.get(f"/api/sets/{set_id}"))
        assert (detail["next_minor_label"], detail["next_major_label"]) == ("v1.1", "v2")
        assert detail["set"]["current_version_label"] == "v1"
        await client.post(f"/api/sets/{set_id}/versions", json={"grind_setting": "21"})
        detail = data(await client.get(f"/api/sets/{set_id}"))
        assert (detail["next_minor_label"], detail["next_major_label"]) == ("v1.2", "v2")
        assert detail["set"]["current_version_label"] == "v1.1"
        assert detail["versions"][0]["version"]["version_label"] == "v1.1"

    async def test_a_set_being_designed_names_both_v1(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        bean = data(await client.post("/api/beans", json={"name": "Kenya AA"}))
        grinder = data(
            await client.post(
                "/api/grinders",
                json={"name": "Niche", "burr_type": "conical", "step_unit": "numbers"},
            )
        )
        designed = data(
            await client.post(
                "/api/sets/design", json={"bean_id": bean["id"], "grinder_id": grinder["id"]}
            )
        )
        detail = data(await client.get(f"/api/sets/{designed['set']['id']}"))
        assert (detail["next_minor_label"], detail["next_major_label"]) == ("v1", "v1")


class TestTheCard:
    async def test_the_card_serves_the_suggestion_the_rule_and_both_names(
        self, client: httpx.AsyncClient, app: FastAPI, set_id: int
    ) -> None:
        await _propose(
            app,
            set_id,
            suggest_major=True,
            major_reason="A finer grind this far changes what the Set is aiming at.",
        )
        proposal = data(await client.get(f"/api/sets/{set_id}"))["proposal"]
        assert proposal["suggest_major"] is True
        assert proposal["major_reason"] == (
            "A finer grind this far changes what the Set is aiming at."
        )
        # A grind change is a minor by the rule, whatever the agent suggests.
        assert proposal["major_by_default"] is False
        assert (proposal["next_minor_label"], proposal["next_major_label"]) == ("v1.1", "v2")
        assert proposal["base_version_label"] == "v1"
        assert proposal["compares_to_version_label"] == "v1"
        listed = data(await client.get(f"/api/sets/{set_id}/proposals"))["items"][0]
        assert listed["next_major_label"] == "v2"

    async def test_a_profile_change_is_a_major_by_the_rule(
        self, client: httpx.AsyncClient, app: FastAPI, set_id: int
    ) -> None:
        profile = await make_profile_version(app.state.db, "Turbo")
        await _propose(app, set_id, patch=SetVersionPatch(profile_version_id=profile))
        proposal = data(await client.get(f"/api/sets/{set_id}"))["proposal"]
        assert proposal["major_by_default"] is True
        assert proposal["suggest_major"] is False

    @pytest.mark.parametrize(
        ("body", "label"),
        [
            ({}, "v1.1"),
            ({"major": None}, "v1.1"),
            ({"major": False}, "v1.1"),
            ({"major": True}, "v2"),
        ],
    )
    async def test_accept_takes_the_box_and_answers_with_the_name(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        set_id: int,
        body: dict[str, Any],
        label: str,
    ) -> None:
        proposal_id = await _propose(app, set_id)
        answer = data(
            await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept", json=body)
        )
        assert answer["version"]["version_label"] == label
        assert answer["proposal"]["resulting_version_label"] == label

    async def test_accept_without_a_body_follows_the_rule(
        self, client: httpx.AsyncClient, app: FastAPI, set_id: int
    ) -> None:
        proposal_id = await _propose(app, set_id)
        answer = data(await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept"))
        assert answer["version"]["version_label"] == "v1.1"

    @pytest.mark.parametrize("body", [{"major": "yes"}, {"major": 1.5}, {"other": True}])
    async def test_accept_refuses_a_body_it_does_not_understand(
        self, client: httpx.AsyncClient, app: FastAPI, set_id: int, body: dict[str, Any]
    ) -> None:
        proposal_id = await _propose(app, set_id)
        response = await client.post(
            f"/api/sets/{set_id}/proposals/{proposal_id}/accept", json=body
        )
        assert response.status_code == 400
        assert error(response)["code"] == "INVALID_REQUEST"
        # Nothing was accepted.
        assert data(await client.get(f"/api/sets/{set_id}"))["proposal"]["status"] == "proposed"

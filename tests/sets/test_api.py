"""The HTTP surface for beans, grinders, the machine, Sets, judgement and vocab.

Against the real app: its lifespan opens the database and migrates it, and no
machine is configured, which is the configuration most of these routes are used
in — a Set is bookkeeping and works with the espresso machine unplugged.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.machines import MachineRepository, MachineUpsert
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetVersionPatch
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.db.repos.starting import StartingPointRunsRepository, StartingPointStart


def data(response: httpx.Response) -> Any:
    """The envelope's payload, with the status asserted on the way through."""
    body = response.json()
    assert body["ok"] is True, body
    return body["data"]


def error(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert body["ok"] is False, body
    return dict(body["error"])


@pytest.fixture
async def machine(app: FastAPI) -> None:
    """The machine row, as a connect would leave it.

    There is no HTTP route that creates one — the row always exists and identity
    comes from the device — so this goes through the repository, which is what
    the sync engine does.
    """
    await MachineRepository(app.state.db).update_identity(MachineUpsert(host="kitchen.local"))


@pytest.fixture
async def bean_id(client: httpx.AsyncClient) -> int:
    response = await client.post(
        "/api/beans",
        json={"name": "Ethiopia Guji", "roast_level": "light", "process": "natural"},
    )
    assert response.status_code == 201
    return int(data(response)["id"])


async def _make_set(client: httpx.AsyncClient, bean_id: int, **over: Any) -> Any:
    body: dict[str, Any] = {
        "name": "Guji on the Niche",
        "bean_id": bean_id,
        "version": {"dose_g": 18.0, "target_yield_g": 36.0, "grind_setting": "22"},
    }
    body.update(over)
    response = await client.post("/api/sets", json=body)
    assert response.status_code == 201, response.text
    return data(response)


class TestVocab:
    async def test_every_vocabulary_is_served_with_its_meanings(
        self, client: httpx.AsyncClient
    ) -> None:
        body = data(await client.get("/api/vocab"))
        assert [term["value"] for term in body["roast_levels"]] == [
            "light",
            "medium-light",
            "medium",
            "medium-dark",
            "dark",
        ]
        assert {term["value"] for term in body["processes"]} == {
            "washed",
            "natural",
            "honey",
            "anaerobic",
            "other",
        }
        # The flavour wheel as a tree, centre first, every node labelled.
        wheel = body["flavor_wheel"]
        assert [node["label"] for node in wheel] == [
            "Floral",
            "Fruity",
            "Sour/Fermented",
            "Green/Vegetative",
            "Other",
            "Roasted",
            "Spices",
            "Nutty/Cocoa",
            "Sweet",
        ]
        berry = next(node for node in wheel[1]["children"] if node["value"] == "fruity.berry")
        assert berry["children"][0] == {
            "value": "fruity.berry.blackberry",
            "label": "Blackberry",
            "children": [],
        }
        assert "taste_groups" not in body
        assert [term["value"] for term in body["decisions"]] == ["keep", "improve", "discard"]
        assert [term["label"] for term in body["decisions"]] == ["Keep", "Improve", "Discard"]
        assert [term["label"] for term in body["balances"]] == ["Sour", "Balanced", "Bitter"]

    async def test_the_enums_reach_openapi(self, client: httpx.AsyncClient) -> None:
        """The front end generates its types from this document.

        A vocabulary that is only in the database is one the UI has to retype,
        and the retyped copy is what drifts.
        """
        schema = (await client.get("/api/openapi.json")).json()
        judgement = schema["components"]["schemas"]["JudgementWrite"]["properties"]
        balance = judgement["balance"]["anyOf"][0]
        assert set(schema["components"]["schemas"][balance["$ref"].rsplit("/", 1)[-1]]["enum"]) == {
            "sour",
            "balanced",
            "bitter",
        }


class TestBeans:
    async def test_crud_and_archive(self, client: httpx.AsyncClient) -> None:
        created = data(
            await client.post("/api/beans", json={"name": "Kenya Kiambu", "roaster": "SQM"})
        )
        assert data(await client.get("/api/beans"))["items"][0]["id"] == created["id"]

        updated = data(
            await client.put(
                f"/api/beans/{created['id']}",
                json={"name": "Kenya Kiambu AA", "roast_level": "medium"},
            )
        )
        assert updated["name"] == "Kenya Kiambu AA"
        assert updated["roast_level"] == "medium"
        # A whole-object PUT: the roaster was not sent, so it was cleared.
        assert updated["roaster"] is None

        archived = data(await client.post(f"/api/beans/{created['id']}/archive"))
        assert archived["archived"] is True
        assert data(await client.get("/api/beans"))["items"] == []
        assert len(data(await client.get("/api/beans?include_archived=true"))["items"]) == 1

        back = data(await client.post(f"/api/beans/{created['id']}/unarchive"))
        assert back["archived"] is False

    async def test_a_value_outside_the_vocabulary_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/api/beans", json={"name": "x", "process": "boiled"})
        # 400, not 422: this app maps every request-validation failure onto the
        # one envelope code (`infra/envelope.py`).
        assert response.status_code == 400
        assert [detail["field"] for detail in error(response)["details"]] == ["body.process"]

    async def test_missing_bean_is_a_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/api/beans/404")).status_code == 404

    async def test_the_description_is_free_text_up_to_2000_characters(
        self, client: httpx.AsyncClient
    ) -> None:
        text = "Sweet and juicy.\nThe roaster says: plum, cocoa.\n" + "x" * 1900
        created = data(await client.post("/api/beans", json={"name": "x", "description": text}))
        assert created["description"] == text
        assert "variety" not in created

        too_long = await client.post("/api/beans", json={"name": "x", "description": "x" * 2001})
        assert too_long.status_code == 400

    async def test_an_unused_bean_can_be_deleted(self, client: httpx.AsyncClient) -> None:
        created = data(await client.post("/api/beans", json={"name": "Typo"}))

        response = await client.delete(f"/api/beans/{created['id']}")

        assert data(response) == {"deleted": True}
        assert (await client.get(f"/api/beans/{created['id']}")).status_code == 404
        assert data(await client.get("/api/beans?include_archived=true"))["items"] == []

    async def test_deleting_a_missing_bean_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.delete("/api/beans/404")
        assert response.status_code == 404
        assert error(response)["code"] == "NOT_FOUND"

    async def test_a_bean_a_set_uses_cannot_be_deleted(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        await _make_set(client, bean_id)
        await _make_set(client, bean_id, name="Second")

        response = await client.delete(f"/api/beans/{bean_id}")

        assert response.status_code == 409
        body = error(response)
        assert body["code"] == "CONFLICT"
        assert body["message"] == "2 Sets use this bean. Archive it instead."
        # Still there, and still resolving for the Sets that point at it.
        assert data(await client.get(f"/api/beans/{bean_id}"))["set_count"] == 2

    async def test_deleting_a_bean_removes_its_starting_point_runs(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        created = data(await client.post("/api/beans", json={"name": "Asked about once"}))
        runs = StartingPointRunsRepository(app.state.db)
        run_id = await runs.start(StartingPointStart(bean_id=created["id"]))
        assert await runs.get(run_id) is not None

        assert data(await client.delete(f"/api/beans/{created['id']}")) == {"deleted": True}

        assert await runs.get(run_id) is None
        assert await app.state.db.fetch_all("PRAGMA foreign_key_check") == []


class TestGrinders:
    async def test_create_list_and_edit(self, client: httpx.AsyncClient) -> None:
        created = data(
            await client.post("/api/grinders", json={"name": "Niche Zero", "burr_type": "conical"})
        )
        assert created["step_unit"] == "clicks"
        edited = data(
            await client.put(
                f"/api/grinders/{created['id']}",
                json={"name": "Niche Zero", "burr_type": "conical", "step_unit": "numbers"},
            )
        )
        assert edited["step_unit"] == "numbers"
        assert data(await client.get("/api/grinders"))["items"][0]["id"] == created["id"]


class TestMachine:
    async def test_the_machine_is_a_singleton_with_its_shot_counts(
        self, client: httpx.AsyncClient, machine: None
    ) -> None:
        body = data(await client.get("/api/machine"))
        assert body["machine"]["host"] == "kitchen.local"
        assert body["counts"]["total"] == 0

    async def test_there_is_a_machine_before_anything_has_connected(
        self, client: httpx.AsyncClient
    ) -> None:
        """A fresh install has the row, with an empty host, so the page can render."""
        body = data(await client.get("/api/machine"))
        assert body["machine"]["host"] == ""

    async def test_a_missing_machine_row_is_a_404_envelope(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        """Unreachable through the app, and answered anyway.

        The migration guarantees the row and the schema's `CHECK (id = 1)`
        refuses a second, so nothing here can delete it. A database somebody has
        been editing by hand can, and "the machine row is missing" in the
        envelope is a better answer than a 500 from an attribute on ``None``.
        """
        await app.state.db.execute("DELETE FROM machines")

        for response in (
            await client.get("/api/machine"),
            await client.patch("/api/machine", json={"name": "x"}),
        ):
            assert response.status_code == 404, response.text
            assert error(response)["code"] == "NOT_FOUND"
            assert "machine row is missing" in error(response)["message"]

    async def test_name_and_notes_are_editable_and_nothing_else_is(
        self, client: httpx.AsyncClient, machine: None
    ) -> None:
        patched = data(await client.patch("/api/machine", json={"name": "the kitchen one"}))
        assert patched["name"] == "the kitchen one"
        # Not sent, so untouched — a rename must not have to resend the notes.
        assert patched["notes"] == ""

        refused = await client.patch("/api/machine", json={"hardware_string": "made up"})
        assert refused.status_code == 400


class TestSets:
    async def test_create_get_and_version(self, client: httpx.AsyncClient, bean_id: int) -> None:
        created = await _make_set(client, bean_id)
        assert created["current_version_no"] == 1
        assert created["active"] is True

        added = data(
            await client.post(
                f"/api/sets/{created['id']}/versions",
                json={"grind_setting": "21", "intent": "chasing the sourness out"},
            )
        )
        assert added["version_no"] == 2
        assert added["grind_setting"] == "21"
        assert added["dose_g"] == 18.0

        detail = data(await client.get(f"/api/sets/{created['id']}"))
        assert [entry["version"]["version_no"] for entry in detail["versions"]] == [2, 1]
        newest = detail["versions"][0]
        assert [change["field"] for change in newest["changes"]] == ["grind_setting"]
        assert newest["changes"][0]["before"] == "22"
        assert detail["versions"][1]["changes"] == []

    async def test_a_set_on_a_bean_that_is_not_there_names_the_field(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/api/sets", json={"name": "x", "bean_id": 909})
        assert response.status_code == 422
        assert error(response)["details"]["field"] == "bean_id"

    async def test_activation_switches_and_archives_nothing(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        first = await _make_set(client, bean_id)
        second = await _make_set(client, bean_id, name="A different bag")

        assert data(await client.get(f"/api/sets/{first['id']}"))["set"]["active"] is False
        back = data(await client.post(f"/api/sets/{first['id']}/activate"))
        assert back["active"] is True
        other = data(await client.get(f"/api/sets/{second['id']}"))["set"]
        assert (other["active"], other["status"]) == (False, "active")

    async def test_archive_hides_it_from_the_list(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        assert data(await client.post(f"/api/sets/{created['id']}/archive"))["status"] == "archived"
        assert data(await client.get("/api/sets"))["items"] == []
        assert len(data(await client.get("/api/sets?include_archived=true"))["items"]) == 1

    async def test_routes_on_a_set_that_is_not_there_are_404s(
        self, client: httpx.AsyncClient
    ) -> None:
        assert (await client.get("/api/sets/404")).status_code == 404
        assert (await client.post("/api/sets/404/versions", json={})).status_code == 404
        assert (await client.post("/api/sets/404/activate")).status_code == 404
        assert (await client.post("/api/sets/404/archive")).status_code == 404
        assert (await client.get("/api/sets/404/trends")).status_code == 404


class TestPredictionsAndOutcomes:
    """The experiment log's writes, over HTTP: envelope, codes, and no echo."""

    async def _version_ids(self, client: httpx.AsyncClient, set_id: int) -> list[int]:
        detail = data(await client.get(f"/api/sets/{set_id}"))
        return [entry["version"]["id"] for entry in reversed(detail["versions"])]

    async def _labelled_shot(
        self,
        client: httpx.AsyncClient,
        app: FastAPI,
        version_id: int,
        device_id: str,
        decision: str,
    ) -> int:
        shot_id = await ShotsRepository(app.state.db).insert(
            ShotInsert(device_id=device_id, raw_slog=b"not-a-slog")
        )
        await client.put(f"/api/shots/{shot_id}/set-version", json={"set_version_id": version_id})
        await client.put(f"/api/shots/{shot_id}/judgement", json={"decision": decision})
        return shot_id

    async def test_a_prediction_is_recorded_and_read_back_with_the_version(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        await client.post(f"/api/sets/{created['id']}/versions", json={"intent": "one finer"})
        first, second = await self._version_ids(client, created["id"])

        body = data(
            await client.patch(
                f"/api/sets/{created['id']}/versions/{second}/prediction",
                json={"prediction": "less bitter, a shorter shot"},
            )
        )

        assert body["prediction"] == "less bitter, a shorter shot"
        assert body["compares_to_version_id"] == first
        assert body["compares_to_version_no"] == 1
        assert body["outcome_state"] == "open"

    async def test_a_prediction_after_the_first_shot_is_its_own_conflict(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        (first,) = await self._version_ids(client, created["id"])
        await self._labelled_shot(client, app, first, "000801", "keep")

        response = await client.patch(
            f"/api/sets/{created['id']}/versions/{first}/prediction",
            json={"prediction": "a secret I am typing afterwards"},
        )

        assert response.status_code == 409
        body = error(response)
        assert body["code"] == "VERSION_HAS_SHOTS"
        assert body["details"]["field"] == "prediction"
        # `details` names the field and the problem, never what was sent.
        assert "secret" not in response.text

    async def test_a_comparison_outside_the_set_is_refused_by_field(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        other = await _make_set(client, bean_id, name="A different bag")
        (foreign,) = await self._version_ids(client, other["id"])
        (mine,) = await self._version_ids(client, created["id"])

        response = await client.patch(
            f"/api/sets/{created['id']}/versions/{mine}/prediction",
            json={"prediction": "x", "compares_to_version_id": foreign},
        )

        assert response.status_code == 422
        assert error(response)["details"]["field"] == "compares_to_version_id"

        # The same check on the route that creates a version with one.
        refused = await client.post(
            f"/api/sets/{created['id']}/versions",
            json={"intent": "one finer", "prediction": "x", "compares_to_version_id": foreign},
        )
        assert refused.status_code == 422
        assert error(refused)["details"]["field"] == "compares_to_version_id"

    async def test_a_body_with_no_prediction_field_is_a_bad_request(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        """Removing a prediction is `""`, and that is something you say.

        An empty body is a caller that forgot the field, not a caller asking
        for the prediction to go, and answering it as a removal would delete
        somebody's words on a typo.
        """
        created = await _make_set(client, bean_id)
        (first,) = await self._version_ids(client, created["id"])
        await client.patch(
            f"/api/sets/{created['id']}/versions/{first}/prediction",
            json={"prediction": "less bitter"},
        )

        response = await client.patch(
            f"/api/sets/{created['id']}/versions/{first}/prediction", json={}
        )

        assert response.status_code == 400
        assert error(response)["code"] == "INVALID_REQUEST"
        detail = data(await client.get(f"/api/sets/{created['id']}"))
        assert detail["versions"][0]["version"]["prediction"] == "less bitter"

    async def test_a_comparison_must_be_older_than_the_version_making_it(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        for step in ("21", "20"):
            await client.post(
                f"/api/sets/{created['id']}/versions", json={"grind_setting": step, "intent": step}
            )
        _first, second, third = await self._version_ids(client, created["id"])

        response = await client.patch(
            f"/api/sets/{created['id']}/versions/{second}/prediction",
            json={"prediction": "x", "compares_to_version_id": third},
        )

        assert response.status_code == 422
        assert error(response)["details"]["field"] == "compares_to_version_id"

    async def test_a_new_version_on_a_set_that_is_not_there_is_a_404(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        """Not "that is not a version of this Set" — the Set is what is missing."""
        created = await _make_set(client, bean_id)
        (mine,) = await self._version_ids(client, created["id"])

        response = await client.post(
            "/api/sets/909/versions",
            json={"intent": "x", "prediction": "y", "compares_to_version_id": mine},
        )

        assert response.status_code == 404
        assert error(response)["code"] == "NOT_FOUND"

    async def test_an_explicit_null_comparison_reaches_the_row(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        """Over the wire too: omitting the key and sending null differ."""
        created = await _make_set(client, bean_id)
        await client.post(f"/api/sets/{created['id']}/versions", json={"intent": "one finer"})
        first, second = await self._version_ids(client, created["id"])

        omitted = data(
            await client.patch(
                f"/api/sets/{created['id']}/versions/{second}/prediction",
                json={"prediction": "less bitter"},
            )
        )
        assert omitted["compares_to_version_id"] == first

        explicit = data(
            await client.patch(
                f"/api/sets/{created['id']}/versions/{second}/prediction",
                json={"prediction": "less bitter", "compares_to_version_id": None},
            )
        )
        assert explicit["compares_to_version_id"] is None

    async def test_a_prediction_is_refused_while_a_grade_stands(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        (first,) = await self._version_ids(client, created["id"])
        await client.patch(
            f"/api/sets/{created['id']}/versions/{first}/prediction",
            json={"prediction": "less bitter"},
        )
        shot_id = await self._labelled_shot(client, app, first, "000805", "keep")
        await client.put(
            f"/api/sets/{created['id']}/versions/{first}/outcome", json={"outcome": "held"}
        )
        # Unfiling the shot reopens the "no shots" window; the grade does not.
        await client.put(f"/api/shots/{shot_id}/set-version", json={"set_version_id": None})

        response = await client.patch(
            f"/api/sets/{created['id']}/versions/{first}/prediction",
            json={"prediction": "a different claim"},
        )

        assert response.status_code == 409
        body = error(response)
        assert body["code"] == "VERSION_HAS_OUTCOME"
        assert body["details"]["field"] == "prediction"
        assert "different claim" not in response.text

        # Clearing the grade opens it again.
        await client.delete(f"/api/sets/{created['id']}/versions/{first}/outcome")
        assert (
            await client.patch(
                f"/api/sets/{created['id']}/versions/{first}/prediction",
                json={"prediction": "a different claim"},
            )
        ).status_code == 200

    async def test_an_outcome_needs_a_prediction_and_something_to_grade(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        (first,) = await self._version_ids(client, created["id"])

        # No prediction at all: 422, because the request is impossible, not
        # merely premature.
        response = await client.put(
            f"/api/sets/{created['id']}/versions/{first}/outcome", json={"outcome": "held"}
        )
        assert response.status_code == 422
        assert error(response)["details"]["field"] == "outcome"

        await client.patch(
            f"/api/sets/{created['id']}/versions/{first}/prediction",
            json={"prediction": "a clean 1:2"},
        )
        response = await client.put(
            f"/api/sets/{created['id']}/versions/{first}/outcome", json={"outcome": "held"}
        )
        assert response.status_code == 409
        assert error(response)["code"] == "NOTHING_TO_GRADE"

        await self._labelled_shot(client, app, first, "000802", "improve")
        body = data(
            await client.put(
                f"/api/sets/{created['id']}/versions/{first}/outcome",
                json={"outcome": "partly_held", "note": "shorter, still sharp"},
            )
        )
        assert body["outcome"] == "partly_held"
        assert body["outcome_state"] == "partly_held"

        cleared = data(await client.delete(f"/api/sets/{created['id']}/versions/{first}/outcome"))
        assert cleared["outcome"] is None
        assert cleared["outcome_state"] == "open"

    async def test_an_unknown_grade_is_refused_by_the_closed_vocabulary(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        """A grade the CHECK constraint would reject never reaches it."""
        created = await _make_set(client, bean_id)
        (first,) = await self._version_ids(client, created["id"])

        response = await client.put(
            f"/api/sets/{created['id']}/versions/{first}/outcome", json={"outcome": "sort of"}
        )

        assert response.status_code == 400
        assert error(response)["code"] == "INVALID_REQUEST"

    async def test_a_roll_back_appends_a_version_and_says_what_it_restored(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        await client.post(
            f"/api/sets/{created['id']}/versions", json={"grind_setting": "19", "intent": "a turbo"}
        )
        first, second = await self._version_ids(client, created["id"])

        response = await client.post(
            f"/api/sets/{created['id']}/rollback",
            json={"to_version_id": first, "intent": "that was worse"},
        )

        assert response.status_code == 201
        body = data(response)
        assert body["version_no"] == 3
        assert body["grind_setting"] == "22"
        assert body["parent_version_id"] == second
        assert body["restores_version_id"] == first
        assert body["restores_version_no"] == 1

        # Rolling back to where you already are is a 422 naming the field.
        refused = await client.post(
            f"/api/sets/{created['id']}/rollback", json={"to_version_id": body["id"]}
        )
        assert refused.status_code == 422
        assert error(refused)["details"]["field"] == "to_version_id"

    async def test_the_set_page_carries_the_track_record_and_the_roll_back_target(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        await client.post(f"/api/sets/{created['id']}/versions", json={"intent": "one finer"})
        first, second = await self._version_ids(client, created["id"])
        await self._labelled_shot(client, app, first, "000803", "keep")
        await client.patch(
            f"/api/sets/{created['id']}/versions/{second}/prediction",
            json={"prediction": "less bitter"},
        )
        await self._labelled_shot(client, app, second, "000804", "improve")
        await client.put(
            f"/api/sets/{created['id']}/versions/{second}/outcome", json={"outcome": "failed"}
        )

        detail = data(await client.get(f"/api/sets/{created['id']}"))

        assert detail["track_record"]["failed"] == 1
        assert detail["track_record"]["graded"] == 1
        assert detail["track_record"]["no_prediction"] == 1
        assert detail["rollback_target_version_id"] == first
        newest = detail["versions"][0]
        assert newest["labels"] == {"keep": 0, "improve": 1, "discard": 0, "unlabelled": 0}
        assert newest["dead_end"] is False

    async def test_a_roll_back_marks_what_it_stepped_over_on_the_page(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        for step in ("21", "20"):
            await client.post(
                f"/api/sets/{created['id']}/versions", json={"grind_setting": step, "intent": step}
            )
        first, second, _third = await self._version_ids(client, created["id"])
        await client.post(f"/api/sets/{created['id']}/rollback", json={"to_version_id": first})

        detail = data(await client.get(f"/api/sets/{created['id']}"))

        muted = {
            entry["version"]["version_no"] for entry in detail["versions"] if entry["dead_end"]
        }
        assert muted == {2, 3}
        assert second

    async def test_the_writes_on_a_version_that_is_not_there_are_404s(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        base = f"/api/sets/{created['id']}/versions/909"

        assert (
            await client.patch(f"{base}/prediction", json={"prediction": "x"})
        ).status_code == 404
        assert (await client.put(f"{base}/outcome", json={"outcome": "held"})).status_code == 404
        assert (await client.delete(f"{base}/outcome")).status_code == 404
        assert (
            await client.post("/api/sets/404/rollback", json={"to_version_id": 1})
        ).status_code == 404


class TestJudgementAndAssignment:
    @pytest.fixture
    async def shot_id(self, app: FastAPI) -> int:
        return await ShotsRepository(app.state.db).insert(
            ShotInsert(
                device_id="000500",
                raw_slog=b"not-a-slog",
                started_at="2026-04-01T08:00:00.000Z",
                duration_ms=28_000,
                execution_score=7.5,
                final_weight_g=36.0,
            )
        )

    async def test_put_get_and_delete_a_judgement(
        self, client: httpx.AsyncClient, shot_id: int
    ) -> None:
        saved = data(
            await client.put(
                f"/api/shots/{shot_id}/judgement",
                json={
                    "rating": 4,
                    "balance": "sour",
                    "taste_notes": ["sour_fermented.sour"],
                    "aroma_notes": ["fruity.berry", "floral"],
                    "dose_in_g": 18,
                    "dose_out_g": 36,
                    "notes": "sharp",
                    "decision": "improve",
                },
            )
        )
        assert saved["ratio"] == 2.0
        assert saved["taste_notes"] == ["sour_fermented.sour"]
        assert saved["aroma_notes"] == ["fruity.berry", "floral"]
        assert saved["decision"] == "improve"

        detail = data(await client.get(f"/api/shots/{shot_id}"))
        assert detail["judgement"]["rating"] == 4
        assert detail["shot"]["has_judgement"] is True

        assert data(await client.delete(f"/api/shots/{shot_id}/judgement"))["deleted"] is True
        assert (await client.delete(f"/api/shots/{shot_id}/judgement")).status_code == 404
        assert data(await client.get(f"/api/shots/{shot_id}"))["judgement"] is None

    async def test_the_list_row_carries_the_verdict_s_notes(
        self, client: httpx.AsyncClient, shot_id: int
    ) -> None:
        """The list shows what the cup was like, and edits it in place.

        Without this column the row's editor would have to fetch the whole shot
        before it could show what is already written — one request per row
        opened, for a string the list query's own join already has.
        """
        listed = data(await client.get("/api/shots"))["items"]
        assert [row["judgement_notes"] for row in listed] == [None]
        assert [row["judgement_rating"] for row in listed] == [None]
        assert [row["judgement_decision"] for row in listed] == [None]

        await client.put(
            f"/api/shots/{shot_id}/judgement",
            json={"rating": 4, "notes": "sharp, and short by a gram", "decision": "improve"},
        )

        listed = data(await client.get("/api/shots"))["items"]
        assert listed[0]["judgement_notes"] == "sharp, and short by a gram"
        assert listed[0]["judgement_rating"] == 4
        assert listed[0]["judgement_decision"] == "improve"
        # …and the filter agrees with the column, which it would not if the
        # verdict's rating were invisible to the query.
        assert data(await client.get("/api/shots", params={"min_rating": 4}))["total"] == 1

        await client.delete(f"/api/shots/{shot_id}/judgement")
        listed = data(await client.get("/api/shots"))["items"]
        assert listed[0]["judgement_notes"] is None
        assert listed[0]["judgement_rating"] is None
        assert listed[0]["judgement_decision"] is None

    @pytest.mark.parametrize("field", ["taste_notes", "aroma_notes"])
    async def test_an_unknown_note_is_refused_by_name(
        self, client: httpx.AsyncClient, shot_id: int, field: str
    ) -> None:
        response = await client.put(
            f"/api/shots/{shot_id}/judgement", json={field: ["fruity", "delicious"]}
        )
        assert response.status_code == 400
        assert "delicious" in str(error(response)["details"])

    async def test_the_old_decision_word_is_refused(
        self, client: httpx.AsyncClient, shot_id: int
    ) -> None:
        response = await client.put(f"/api/shots/{shot_id}/judgement", json={"decision": "adjust"})
        assert response.status_code == 400

    async def test_judging_a_shot_that_is_not_there(self, client: httpx.AsyncClient) -> None:
        assert (await client.put("/api/shots/404/judgement", json={})).status_code == 404

    async def test_assign_reassign_and_unassign(
        self,
        client: httpx.AsyncClient,
        shot_id: int,
        bean_id: int,
    ) -> None:
        created = await _make_set(client, bean_id)
        detail = data(await client.get(f"/api/sets/{created['id']}"))
        version_id = detail["versions"][0]["version"]["id"]

        assigned = data(
            await client.put(
                f"/api/shots/{shot_id}/set-version", json={"set_version_id": version_id}
            )
        )
        assert assigned["set_version_id"] == version_id
        assert assigned["set_badge"]["set_name"] == "Guji on the Niche"
        assert assigned["set_badge"]["version_no"] == 1

        detached = data(
            await client.put(f"/api/shots/{shot_id}/set-version", json={"set_version_id": None})
        )
        assert detached["set_version_id"] is None
        assert detached["set_badge"] is None

    async def test_assigning_to_a_version_that_does_not_exist_names_the_field(
        self, client: httpx.AsyncClient, shot_id: int
    ) -> None:
        response = await client.put(
            f"/api/shots/{shot_id}/set-version", json={"set_version_id": 4040}
        )
        assert response.status_code == 422
        assert error(response)["details"]["field"] == "set_version_id"

    async def test_the_needs_set_filter_and_its_count(
        self,
        client: httpx.AsyncClient,
        shot_id: int,
        bean_id: int,
    ) -> None:
        assert data(await client.get("/api/shots?needs_set=true"))["total"] == 1
        assert data(await client.get("/api/sync/status"))["counts"]["needs_set"] == 1

        created = await _make_set(client, bean_id)
        version_id = data(await client.get(f"/api/sets/{created['id']}"))["versions"][0]["version"][
            "id"
        ]
        await client.put(f"/api/shots/{shot_id}/set-version", json={"set_version_id": version_id})

        assert data(await client.get("/api/shots?needs_set=true"))["total"] == 0
        assert data(await client.get(f"/api/shots?set_id={created['id']}"))["total"] == 1
        assert data(await client.get(f"/api/shots?set_version_id={version_id}"))["total"] == 1
        assert data(await client.get("/api/sync/status"))["counts"]["needs_set"] == 0


class TestSetReferencesAndRefusals:
    """A stale id in a dropdown is a 422 naming it, never a 500.

    Every one of these used to reach the foreign key and come back as an
    IntegrityError, which the envelope can only report as an internal error —
    with nothing in the body saying which of the three ids was wrong.
    """

    @pytest.mark.parametrize(
        ("field", "body"),
        [
            ("bean_id", {"bean_id": 909}),
            ("grinder_id", {"grinder_id": 909}),
            ("version.profile_version_id", {"version": {"profile_version_id": 909}}),
        ],
    )
    async def test_a_reference_that_does_not_resolve_names_itself(
        self,
        client: httpx.AsyncClient,
        bean_id: int,
        field: str,
        body: dict[str, Any],
    ) -> None:
        payload: dict[str, Any] = {
            "name": "Guji on the Niche",
            "bean_id": bean_id,
            **body,
        }
        response = await client.post("/api/sets", json=payload)

        assert response.status_code == 422, response.text
        assert error(response)["details"]["field"] == field

    async def test_a_null_grinder_and_a_null_profile_are_fine(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        """A Set can name neither, so only a *stated* id is checked."""
        response = await client.post(
            "/api/sets",
            json={
                "name": "Whatever is loaded",
                "bean_id": bean_id,
                "grinder_id": None,
                "version": {"profile_version_id": None},
            },
        )
        assert response.status_code == 201

    async def test_activating_an_archived_set_is_a_conflict(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        live = await _make_set(client, bean_id, name="The live one")
        old = await _make_set(client, bean_id, name="Last month's bag")
        await client.post(f"/api/sets/{old['id']}/archive")
        await client.post(f"/api/sets/{live['id']}/activate")

        response = await client.post(f"/api/sets/{old['id']}/activate")

        assert response.status_code == 409
        assert error(response)["code"] == "CONFLICT"
        # And the live Set kept the flag: the whole point is that the machine is
        # not left with no usable active Set.
        assert data(await client.get(f"/api/sets/{live['id']}"))["set"]["active"] is True

    async def test_filing_a_shot_under_an_archived_set_is_refused(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        version_id = data(await client.get(f"/api/sets/{created['id']}"))["versions"][0]["version"][
            "id"
        ]
        await client.post(f"/api/sets/{created['id']}/archive")
        shot_id = await ShotsRepository(app.state.db).insert(
            ShotInsert(device_id="000600", raw_slog=b"not-a-slog")
        )

        response = await client.put(
            f"/api/shots/{shot_id}/set-version", json={"set_version_id": version_id}
        )

        assert response.status_code == 422
        assert error(response)["details"]["field"] == "set_version_id"


class TestTrendsRoute:
    async def test_the_route_answers_the_shape_the_chart_draws(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        version_id = data(await client.get(f"/api/sets/{created['id']}"))["versions"][0]["version"][
            "id"
        ]
        shot_id = await ShotsRepository(app.state.db).insert(
            ShotInsert(
                device_id="000601",
                raw_slog=b"not-a-slog",
                started_at="2026-04-01T08:00:00.000Z",
                duration_ms=28_000,
                execution_score=8.2,
            )
        )
        await client.put(f"/api/shots/{shot_id}/set-version", json={"set_version_id": version_id})
        await client.put(
            f"/api/shots/{shot_id}/judgement",
            json={"rating": 4, "dose_in_g": 18, "dose_out_g": 36},
        )

        body = data(await client.get(f"/api/sets/{created['id']}/trends"))

        assert body["set_id"] == created["id"]
        assert [version["version_no"] for version in body["versions"]] == [1]
        assert body["versions"][0]["shots"] == 1
        point = body["shots"][0]
        assert point["shot_id"] == shot_id
        assert point["duration_s"] == 28.0
        assert point["ratio"] == 2.0
        assert point["rating"] == 4


class TestProposals:
    """A change an agent proposed, and the two buttons only a person reaches.

    There is no route that *creates* a proposal here on purpose: creating one is
    the chat tool's job and accepting one is the person's, and these tests are
    about the half that answers to a press. The rows are made through the
    repository, which is what the tool does with the same call.
    """

    @staticmethod
    async def _propose(app: FastAPI, set_id: int, **over: Any) -> Any:
        spec: dict[str, Any] = {
            "patch": SetVersionPatch(grind_setting="21"),
            "reason": "one click finer, chasing the sourness out",
            "prediction": "Compared to v1: two to four seconds longer and less sour.",
        }
        spec.update(over)
        result = await SetProposalsRepository(app.state.db).create(set_id, ProposalWrite(**spec))
        assert result.proposal is not None, result.refused
        return result.proposal

    async def test_the_set_page_carries_the_waiting_proposal(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        await self._propose(app, created["id"])

        body = data(await client.get(f"/api/sets/{created['id']}"))
        proposal = body["proposal"]
        assert proposal["status"] == "proposed"
        assert proposal["changed"] == ["the grind"]
        assert proposal["base_is_current"] is True
        assert proposal["compares_to_version_no"] == 1
        # Rendered the way the log renders a version's own diff.
        assert proposal["changes"] == [
            {
                "field": "grind_setting",
                "label": "Grind",
                "before": "22",
                "after": "21",
                "from_profile": False,
            }
        ]
        # Nothing has changed yet: the Set is still on v1.
        assert body["set"]["current_version_no"] == 1

    async def test_a_set_with_nothing_waiting_says_so(
        self, client: httpx.AsyncClient, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        assert data(await client.get(f"/api/sets/{created['id']}"))["proposal"] is None

    async def test_accept_records_the_version_and_links_the_log_to_the_chat(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        thread = data(
            await client.post(
                "/api/chat/threads",
                json={"title": "About v1", "set_id": created["id"]},
            )
        )
        proposal = await self._propose(app, created["id"], thread_id=thread["id"])

        body = data(
            await client.post(f"/api/sets/{created['id']}/proposals/{proposal.id}/accept", json={})
        )
        assert body["proposal"]["status"] == "accepted"
        assert body["version"]["version_no"] == 2
        assert body["version"]["origin"] == "chat"
        assert body["version"]["grind_setting"] == "21"
        assert body["version"]["intent"] == "one click finer, chasing the sourness out"
        assert body["version"]["prediction"].startswith("Compared to v1")

        detail = data(await client.get(f"/api/sets/{created['id']}"))
        assert detail["proposal"] is None
        assert detail["versions"][0]["chat_thread_id"] == thread["id"]

    async def test_a_version_recorded_by_hand_retires_the_waiting_proposal(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        """No stale question left on the page, and no button that can only refuse."""
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])
        await client.post(
            f"/api/sets/{created['id']}/versions",
            json={"dose_g": 19.0, "intent": "by hand"},
        )

        detail = data(await client.get(f"/api/sets/{created['id']}"))
        assert detail["proposal"] is None
        listed = data(await client.get(f"/api/sets/{created['id']}/proposals"))["items"]
        assert [item["status"] for item in listed] == ["stale"]

        response = await client.post(
            f"/api/sets/{created['id']}/proposals/{proposal.id}/accept", json={}
        )
        assert response.status_code == 409
        assert error(response)["code"] == "PROPOSAL_DECIDED"
        # Two versions, not three: the hand-made change is where the Set is.
        assert len(data(await client.get(f"/api/sets/{created['id']}"))["versions"]) == 2

    async def test_accept_answers_the_stale_code_when_it_is_the_one_to_catch_it(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        """The defence behind the retirement, put back by hand to reach it."""
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])
        await client.post(
            f"/api/sets/{created['id']}/versions", json={"dose_g": 19.0, "intent": "by hand"}
        )
        await app.state.db.execute(
            "UPDATE set_version_proposals SET status = 'proposed' WHERE id = ?", (proposal.id,)
        )

        response = await client.post(
            f"/api/sets/{created['id']}/proposals/{proposal.id}/accept", json={}
        )
        assert response.status_code == 409
        body = error(response)
        assert body["code"] == "PROPOSAL_STALE"
        assert body["details"]["field"] == "base_version_id"

    async def test_accept_refuses_while_the_current_prediction_is_open(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])
        version_id = data(await client.get(f"/api/sets/{created['id']}"))["versions"][0]["version"][
            "id"
        ]
        await client.patch(
            f"/api/sets/{created['id']}/versions/{version_id}/prediction",
            json={"prediction": "Expect about 30 s.", "compares_to_version_id": None},
        )

        response = await client.post(
            f"/api/sets/{created['id']}/proposals/{proposal.id}/accept", json={}
        )
        assert response.status_code == 409
        assert error(response)["code"] == "PROPOSAL_OUTCOME_OPEN"
        # Still waiting, so the person can grade and come back to it.
        assert data(await client.get(f"/api/sets/{created['id']}"))["proposal"] is not None

    async def test_accepting_twice_is_a_conflict(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])
        url = f"/api/sets/{created['id']}/proposals/{proposal.id}/accept"
        assert (await client.post(url, json={})).status_code == 200
        response = await client.post(url, json={})
        assert response.status_code == 409
        assert error(response)["code"] == "PROPOSAL_DECIDED"

    async def test_decline_records_the_note_and_creates_nothing(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])

        body = data(
            await client.post(
                f"/api/sets/{created['id']}/proposals/{proposal.id}/decline",
                json={"note": "tried that last week"},
            )
        )
        assert body["proposal"]["status"] == "declined"
        assert body["proposal"]["decline_note"] == "tried that last week"
        assert body["version"] is None
        assert len(data(await client.get(f"/api/sets/{created['id']}"))["versions"]) == 1

    async def test_the_list_holds_the_answered_ones_too(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        first = await self._propose(app, created["id"])
        await client.post(f"/api/sets/{created['id']}/proposals/{first.id}/decline", json={})
        await self._propose(app, created["id"], patch=SetVersionPatch(dose_g=18.5))

        items = data(await client.get(f"/api/sets/{created['id']}/proposals"))["items"]
        assert [item["status"] for item in items] == ["proposed", "declined"]
        assert [item["changed"] for item in items] == [["the dose"], ["the grind"]]

    async def test_a_proposal_of_another_set_is_not_found(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        mine = await _make_set(client, bean_id)
        theirs = await _make_set(client, bean_id, name="Another coffee")
        proposal = await self._propose(app, theirs["id"])

        response = await client.post(
            f"/api/sets/{mine['id']}/proposals/{proposal.id}/accept", json={}
        )
        assert response.status_code == 404

    async def test_a_proposal_nobody_can_read_does_not_take_the_page_down(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        """One damaged row is a card that says so, not a Set page that will not load."""
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])
        await app.state.db.execute(
            "UPDATE set_version_proposals SET patch_json = ? WHERE id = ?",
            ("not json at all", proposal.id),
        )

        served = data(await client.get(f"/api/sets/{created['id']}"))["proposal"]
        assert served["readable"] is False
        assert served["changes"] == []
        assert served["changed"] == []
        assert (
            data(await client.get(f"/api/sets/{created['id']}/proposals"))["items"][0]["readable"]
            is False
        )

        refused = await client.post(
            f"/api/sets/{created['id']}/proposals/{proposal.id}/accept", json={}
        )
        assert refused.status_code == 409
        assert error(refused)["code"] == "PROPOSAL_UNREADABLE"

        declined = data(
            await client.post(f"/api/sets/{created['id']}/proposals/{proposal.id}/decline", json={})
        )
        assert declined["proposal"]["status"] == "declined"

    async def test_a_decline_note_longer_than_a_note_is_refused(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])

        response = await client.post(
            f"/api/sets/{created['id']}/proposals/{proposal.id}/decline",
            json={"note": "x" * 501},
        )

        # 400 rather than 422: the envelope's own validation handler answers a
        # malformed body, which a note ten times its cap is.
        assert response.status_code == 400
        # And it is still waiting: a refused note declines nothing.
        assert data(await client.get(f"/api/sets/{created['id']}"))["proposal"] is not None

    async def test_a_decline_note_of_five_hundred_is_kept(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])

        body = data(
            await client.post(
                f"/api/sets/{created['id']}/proposals/{proposal.id}/decline",
                json={"note": "y" * 500},
            )
        )
        assert body["proposal"]["decline_note"] == "y" * 500

    async def test_details_never_echo_what_was_sent(
        self, client: httpx.AsyncClient, app: FastAPI, bean_id: int
    ) -> None:
        created = await _make_set(client, bean_id)
        proposal = await self._propose(app, created["id"])
        await client.post(
            f"/api/sets/{created['id']}/versions", json={"dose_g": 19.0, "intent": "by hand"}
        )
        body = error(
            await client.post(f"/api/sets/{created['id']}/proposals/{proposal.id}/accept", json={})
        )
        rendered = str(body)
        assert "chasing the sourness out" not in rendered
        assert "less sour" not in rendered

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

        await client.put(
            f"/api/shots/{shot_id}/judgement",
            json={"rating": 4, "notes": "sharp, and short by a gram"},
        )

        listed = data(await client.get("/api/shots"))["items"]
        assert listed[0]["judgement_notes"] == "sharp, and short by a gram"
        assert listed[0]["judgement_rating"] == 4
        # …and the filter agrees with the column, which it would not if the
        # verdict's rating were invisible to the query.
        assert data(await client.get("/api/shots", params={"min_rating": 4}))["total"] == 1

        await client.delete(f"/api/shots/{shot_id}/judgement")
        listed = data(await client.get("/api/shots"))["items"]
        assert listed[0]["judgement_notes"] is None
        assert listed[0]["judgement_rating"] is None

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

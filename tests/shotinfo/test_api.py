"""`/api/shot-information`: the settings page's one document, and moving an item.

Against the real app. The examples are checked against the rendering itself —
every value the page shows must be a line the agent would read — so a second
formatting path would fail here rather than show a person numbers the model
never sees.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.shotinfo.catalogue import CATALOGUE, GROUP_NOTES, GROUPS, ITEMS, default_tiers
from gaggiclanker.shotinfo.glossary import render_glossary
from gaggiclanker.shotinfo.render import load_shots, render_shot
from tests.sets.test_api import data, error
from tests.shotinfo.conftest import SLOG, _insert


async def _judged_shot(db: Database) -> int:
    """The derived fixture shot, judged, so the judgement rows have examples."""
    shot = await _insert(db, parse_slog(SLOG.read_bytes()), "000204")
    await JudgementsRepository(db).upsert(
        shot,
        JudgementWrite(rating=4, balance="balanced", dose_in_g=18.0, dose_out_g=36.5),
    )
    return shot


def tokens(text: str) -> int:
    return round(len(text) / 3.5)


def items_of(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for group in document["groups"] for item in group["items"]}


# -- reading ------------------------------------------------------------------


async def test_the_document_is_the_catalogue_in_order_with_its_tiers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/shot-information")
    document = data(response)

    assert response.json()["meta"]["request_id"] == response.headers["x-request-id"]
    assert [group["name"] for group in document["groups"]] == list(GROUPS)
    assert [group["note"] for group in document["groups"]] == [
        GROUP_NOTES.get(group) for group in GROUPS
    ]
    assert [item["key"] for item in items_of(document).values()] == [item.key for item in CATALOGUE]
    for item in CATALOGUE:
        served = items_of(document)[item.key]
        assert served == {
            "key": item.key,
            "name": item.name,
            "label": item.label,
            "meaning": item.meaning,
            "default_tier": item.default_tier,
            "tier": item.default_tier,
            "locked": item.locked,
            "example": None,
        }


async def test_an_empty_archive_has_no_example_and_only_the_glossary_estimate(
    client: httpx.AsyncClient,
) -> None:
    document = data(await client.get("/api/shot-information"))

    assert document["example_shot"] is None
    assert all(item["example"] is None for item in items_of(document).values())
    assert document["estimates"] == {
        "base_per_shot": None,
        "extended_per_shot": None,
        "full_per_shot": None,
        "glossary": tokens(render_glossary(default_tiers())),
        "autoload": None,
        "recent_shots": 20,
    }


async def test_the_example_shot_is_named_with_its_date_and_whether_it_was_judged(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    db: Database = app.state.db
    shot = await _judged_shot(db)
    # A newer shot nobody has tasted yet loses to the judged one.
    await _insert(db, parse_slog(SLOG.read_bytes()), "000205")

    example = data(await client.get("/api/shot-information"))["example_shot"]

    [facts] = await load_shots(db, [shot])
    assert example == {"shot_id": shot, "started_at": facts.shot.started_at, "judged": True}


async def test_every_example_is_what_the_rendering_says(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """Shot items as their line, phase items as each phase's, channels as their column."""
    db: Database = app.state.db
    shot = await _judged_shot(db)
    document = data(await client.get("/api/shot-information"))
    [facts] = await load_shots(db, [shot], samples=True)
    # Everything in one rendering, so every item has a line to be compared with.
    rendered = render_shot(facts, "base", dict.fromkeys(ITEMS, "base"))
    lines = rendered.splitlines()

    shown = {key: item["example"] for key, item in items_of(document).items()}
    assert sum(value is not None for value in shown.values()) > 60, "a real shot has most items"
    for item in CATALOGUE:
        example = shown[item.key]
        if example is None:
            continue
        if item.key == "shot_id":
            assert lines[0] == f"shot {example}"
        elif item.kind == "shot":
            assert f"{item.label}: {example}" in lines, item.key
        elif item.kind == "phase":
            # Each line is its phase's head and then this item's own part of
            # the rendered phase line, label included: `phase 1 · fill: ramp …`.
            for line in example.splitlines():
                head, _, value = line.partition(": ")
                phase_line = next(text for text in lines if text.startswith(f"{head}: "))
                parts = phase_line.removeprefix(f"{head}: ").split("; ")
                if item.key == "phase_name":
                    assert value == "", item.key
                else:
                    assert value in parts, item.key
                    assert value.startswith(f"{item.label} "), item.key
        else:
            _assert_channel(lines, item.label, example)


def _assert_channel(lines: list[str], header: str, example: str) -> None:
    table = lines[lines.index("[Curve]") + 1 :]
    count = int(table[0].split()[0])
    columns = table[1].split(",")
    cells = [row.split(",")[columns.index(header)] for row in table[2 : 2 + count]]
    values = [float(cell) for cell in cells if cell]
    match = re.fullmatch(r"(\d+) samples, (\S+) to (\S+)(?: .+)?", example)
    assert match is not None, example
    assert int(match[1]) == count
    assert float(match[2]) == min(values)
    assert float(match[3]) == max(values)
    # At the column's own precision: the same text as a cell of the table.
    assert match[2] in cells
    assert match[3] in cells


async def test_the_estimates_are_the_example_s_renderings_at_the_current_tiers(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    db: Database = app.state.db
    shot = await _judged_shot(db)
    [facts] = await load_shots(db, [shot], samples=True)

    before = data(await client.get("/api/shot-information"))["estimates"]
    moved = data(await client.put("/api/shot-information/curve_pressure", json={"tier": "base"}))[
        "estimates"
    ]

    tiers = default_tiers()
    assert before == {
        "base_per_shot": tokens(render_shot(facts, "base", tiers)),
        "extended_per_shot": tokens(render_shot(facts, "extended", tiers)),
        "full_per_shot": tokens(render_shot(facts, "full", tiers)),
        "glossary": tokens(render_glossary(tiers)),
        "autoload": tokens(render_shot(facts, "base", tiers)) * 20,
        "recent_shots": 20,
    }
    now = {**tiers, "curve_pressure": "base"}
    assert moved["base_per_shot"] == tokens(render_shot(facts, "base", now))
    assert moved["base_per_shot"] > before["base_per_shot"] + 200, "a whole column of samples"
    assert moved["extended_per_shot"] == tokens(render_shot(facts, "extended", now))
    assert moved["glossary"] == tokens(render_glossary(now))
    assert moved["autoload"] == moved["base_per_shot"] * 20


async def test_the_autoload_follows_the_recent_shots_setting(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await _judged_shot(app.state.db)
    assert data(await client.patch("/api/settings", json={"chatRecentShots": 5}))

    estimates = data(await client.get("/api/shot-information"))["estimates"]

    assert estimates["recent_shots"] == 5
    assert estimates["autoload"] == estimates["base_per_shot"] * 5


# -- moving an item -----------------------------------------------------------


async def test_moving_an_item_answers_the_document_with_it_moved(
    client: httpx.AsyncClient,
) -> None:
    moved = data(await client.put("/api/shot-information/rating", json={"tier": "excluded"}))

    assert items_of(moved)["rating"]["tier"] == "excluded"
    assert items_of(moved)["rating"]["default_tier"] == "base"
    assert items_of(data(await client.get("/api/shot-information")))["rating"]["tier"] == (
        "excluded"
    )

    back = data(await client.put("/api/shot-information/rating", json={"tier": "base"}))
    assert items_of(back)["rating"]["tier"] == "base"


async def test_an_unknown_item_is_a_404_that_does_not_echo_the_key(
    client: httpx.AsyncClient,
) -> None:
    response = await client.put("/api/shot-information/no_such_item_xyz", json={"tier": "base"})

    assert response.status_code == 404
    assert error(response)["code"] == "NOT_FOUND"
    assert "no_such_item_xyz" not in response.text


@pytest.mark.parametrize("key", ["shot_id", "set_version", "counted"])
async def test_a_locked_item_is_refused_with_a_code_of_its_own(
    client: httpx.AsyncClient, key: str
) -> None:
    before = data(await client.get("/api/shot-information"))

    response = await client.put(f"/api/shot-information/{key}", json={"tier": "extended"})

    assert response.status_code == 422
    failure = error(response)
    assert failure["code"] == "LOCKED_ITEM"
    assert failure["details"] == {"field": "key", "message": "a locked item stays in its tier"}
    assert data(await client.get("/api/shot-information")) == before


@pytest.mark.parametrize(
    "body", [{"tier": "full"}, {"tier": ""}, {}, {"tier": "base", "key": "rating"}]
)
async def test_a_body_that_is_not_a_tier_is_refused_by_the_model(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    """A malformed body is the house's 400, and nothing is stored."""
    response = await client.put("/api/shot-information/rating", json=body)

    assert response.status_code == 400
    assert error(response)["code"] == "INVALID_REQUEST"
    assert items_of(data(await client.get("/api/shot-information")))["rating"]["tier"] == "base"


async def test_reset_puts_every_item_back_and_answers_the_document(
    client: httpx.AsyncClient,
) -> None:
    await client.put("/api/shot-information/rating", json={"tier": "excluded"})
    await client.put("/api/shot-information/flow_jitter", json={"tier": "base"})

    reset = data(await client.post("/api/shot-information/reset"))

    assert {key: item["tier"] for key, item in items_of(reset).items()} == dict(default_tiers())
    assert reset == data(await client.get("/api/shot-information"))

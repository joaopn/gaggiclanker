"""The flavour-wheel notes the shot panel offers: `/api/flavor-picks`.

Against the real app on a fresh database, so the defaults the migration seeds
are the ones read back.
"""

from __future__ import annotations

import httpx

from gaggiclanker.domain.vocab import FLAVOR_NOTES, in_wheel_order
from tests.sets.test_api import data, error


async def test_a_fresh_archive_offers_a_useful_start(client: httpx.AsyncClient) -> None:
    """An empty panel under every shot would be the first thing somebody fixes."""
    picks = data(await client.get("/api/flavor-picks"))

    assert set(picks) == {"taste", "aroma"}
    for kind in ("taste", "aroma"):
        assert 8 <= len(picks[kind]) <= 14, kind
        assert all(note in FLAVOR_NOTES for note in picks[kind])
        assert picks[kind] == in_wheel_order(picks[kind]), "shown in wheel order"
    assert "other.chemical.bitter" in picks["taste"]
    assert "floral" in picks["aroma"]


async def test_put_replaces_both_lists_in_wheel_order(client: httpx.AsyncClient) -> None:
    body = {
        "taste": ["sweet", "fruity.berry.blackberry", "sweet"],
        "aroma": [],
    }
    saved = data(await client.put("/api/flavor-picks", json=body))

    # De-duplicated and in the order they sit on the wheel, whatever order the
    # page sent them in.
    assert saved == {"taste": ["fruity.berry.blackberry", "sweet"], "aroma": []}
    assert data(await client.get("/api/flavor-picks")) == saved


async def test_an_unknown_note_is_refused_by_name_and_nothing_changes(
    client: httpx.AsyncClient,
) -> None:
    before = data(await client.get("/api/flavor-picks"))

    response = await client.put(
        "/api/flavor-picks", json={"taste": ["fruity", "delicious"], "aroma": []}
    )

    assert response.status_code == 400
    assert "delicious" in str(error(response)["details"])
    assert data(await client.get("/api/flavor-picks")) == before


async def test_a_list_left_out_is_an_empty_list(client: httpx.AsyncClient) -> None:
    """The page always sends both; a body with one means the other is empty."""
    saved = data(await client.put("/api/flavor-picks", json={"aroma": ["roasted"]}))
    assert saved == {"taste": [], "aroma": ["roasted"]}


async def test_the_same_note_can_be_on_both_lists(client: httpx.AsyncClient) -> None:
    saved = data(
        await client.put(
            "/api/flavor-picks", json={"taste": ["nutty_cocoa"], "aroma": ["nutty_cocoa"]}
        )
    )
    assert saved == {"taste": ["nutty_cocoa"], "aroma": ["nutty_cocoa"]}

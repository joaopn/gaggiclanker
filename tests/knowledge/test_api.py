"""`/api/knowledge` for tiers 2 and 3, over the real app.

The app seeds the documents in its lifespan, so these run against the shipped
corpus rather than against a fixture — which is what makes the "does the boot
path actually work" question answered here rather than nowhere.
"""

from __future__ import annotations

import httpx


async def _data(response: httpx.Response) -> dict:  # type: ignore[type-arg]
    assert response.status_code < 400, response.text
    payload = response.json()
    assert payload["ok"] is True, payload
    return dict(payload["data"])


async def test_the_docs_list_carries_counts_but_not_the_markdown(client: httpx.AsyncClient) -> None:
    data = await _data(await client.get("/api/knowledge/docs"))
    assert len(data["items"]) == 25
    first = data["items"][0]
    assert first["chunk_count"] > 0
    assert first["tokens_estimate"] > 0
    assert first["edited"] is False
    # The list is a directory; the bodies are 200 KB between them.
    assert all(item["body"] == "" for item in data["items"])


async def test_one_doc_comes_back_with_its_chunks(client: httpx.AsyncClient) -> None:
    data = await _data(await client.get("/api/knowledge/docs/ESPRESSO_TASTING_GUIDE"))
    assert data["doc"]["title"]
    assert data["doc"]["body"].startswith("#")
    assert data["chunks"]
    assert [chunk["ordinal"] for chunk in data["chunks"]] == list(range(len(data["chunks"])))
    assert all(
        chunk["heading_path"].startswith("ESPRESSO_TASTING_GUIDE#") for chunk in data["chunks"]
    )


async def test_an_unknown_slug_is_a_404_everywhere(client: httpx.AsyncClient) -> None:
    for response in (
        await client.get("/api/knowledge/docs/NOPE"),
        await client.put("/api/knowledge/docs/NOPE", json={"markdown": "# Nope"}),
        await client.post("/api/knowledge/docs/NOPE/reset"),
    ):
        assert response.status_code == 404
        assert response.json()["ok"] is False


async def test_editing_a_doc_rechunks_it_and_resetting_puts_it_back(
    client: httpx.AsyncClient,
) -> None:
    before = await _data(await client.get("/api/knowledge/docs/BASKETS"))

    edited = await _data(
        await client.put(
            "/api/knowledge/docs/BASKETS",
            json={"markdown": "# Baskets\n\n## Mine\n\n" + ("word " * 300).strip() + "\n"},
        )
    )
    assert edited["doc"]["edited"] is True
    assert [chunk["heading_path"] for chunk in edited["chunks"]] == ["BASKETS#mine"]

    # And the search sees the new text rather than the old.
    found = await _data(await client.get("/api/knowledge/search", params={"q": "headroom"}))
    assert all(hit["chunk"]["doc_slug"] != "BASKETS" for hit in found["items"])

    restored = await _data(await client.post("/api/knowledge/docs/BASKETS/reset"))
    assert restored["doc"]["edited"] is False
    assert restored["doc"]["body"] == before["doc"]["body"]
    assert [chunk["heading_path"] for chunk in restored["chunks"]] == [
        chunk["heading_path"] for chunk in before["chunks"]
    ]


async def test_search_answers_with_ranked_hits_and_snippets(client: httpx.AsyncClient) -> None:
    data = await _data(
        await client.get("/api/knowledge/search", params={"q": "channeling puck prep", "k": 4})
    )
    assert data["query"] == "channeling puck prep"
    assert 0 < len(data["items"]) <= 4
    first = data["items"][0]
    assert first["snippet"]
    assert first["score"] > 0
    assert first["chunk"]["heading_path"]


async def test_search_validates_its_arguments(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/knowledge/search")).status_code == 400
    assert (await client.get("/api/knowledge/search", params={"q": ""})).status_code == 400
    assert (
        await client.get("/api/knowledge/search", params={"q": "x", "k": 500})
    ).status_code == 400


async def test_insights_crud_and_confirmation(client: httpx.AsyncClient) -> None:
    created = await _data(
        await client.post(
            "/api/knowledge/insights",
            json={
                "scope": {"grinder_id": 2, "process": "natural"},
                "text": "Two numbers finer for naturals on the Niche.",
                "evidence_shot_ids": [4, 2],
            },
        )
    )
    insight_id = created["id"]
    assert created["source"] == "user"
    # A hand-written insight is confirmed by default: the person writing it is
    # the person who would confirm it.
    assert created["confirmed"] is True
    assert created["evidence_shot_ids"] == [2, 4]

    listed = await _data(await client.get("/api/knowledge/insights"))
    assert [item["id"] for item in listed["items"]] == [insight_id]
    assert "grinder_id" in listed["scope_keys"]

    patched = await _data(
        await client.patch(
            f"/api/knowledge/insights/{insight_id}",
            json={"text": "Three numbers finer.", "confirmed": False},
        )
    )
    assert patched["text"] == "Three numbers finer."
    assert patched["confirmed"] is False
    assert patched["confirmed_at"] is None
    # The scope was not sent, so it did not move.
    assert patched["scope"]["grinder_id"] == 2

    assert not (
        await _data(await client.get("/api/knowledge/insights", params={"confirmed": True}))
    )["items"]

    deleted = await _data(await client.delete(f"/api/knowledge/insights/{insight_id}"))
    assert deleted == {"deleted": True}
    assert (await client.delete(f"/api/knowledge/insights/{insight_id}")).status_code == 404
    assert (
        await client.patch(f"/api/knowledge/insights/{insight_id}", json={"confirmed": True})
    ).status_code == 404


async def test_an_unknown_scope_key_is_refused_rather_than_stored(
    client: httpx.AsyncClient,
) -> None:
    """`extra="forbid"` on the scope, seen from the wire.

    A misspelled dimension has to be a 400 rather than a row that silently never
    matches anything.
    """
    response = await client.post(
        "/api/knowledge/insights",
        json={"scope": {"bean_variety": "heirloom"}, "text": "Nope."},
    )
    assert response.status_code == 400


async def test_an_empty_insight_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/knowledge/insights", json={"text": ""})).status_code == 400


async def test_the_rule_routes_still_work_beside_the_new_ones(
    client: httpx.AsyncClient,
) -> None:
    """Tier 1 is untouched by this feature, and the router still mounts it."""
    data = await _data(await client.get("/api/knowledge/rules"))
    assert data["items"]
    assert data["categories"]

"""The knowledge rule routes, over the real app.

Listing, filtering and narrowing the rule tier, and a person switching a rule
off or editing it: the Knowledge page's own requests.
"""

from __future__ import annotations

import httpx


async def test_the_knowledge_rules_are_seeded_and_listed(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/knowledge/rules")
    assert response.status_code == 200
    body = response.json()["data"]

    assert len(body["items"]) > 100
    assert body["categories"][0] == "dial_in_order", "the order the prompt lists them in"
    first = body["items"][0]
    assert first["category"] == "dial_in_order"
    assert first["enabled"] is True
    assert first["edited"] is False
    assert first["source"]


async def test_rules_can_be_filtered(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/knowledge/rules", params={"category": "pressure_matrix"})
    items = response.json()["data"]["items"]
    assert items
    assert {item["category"] for item in items} == {"pressure_matrix"}

    bad = await client.get("/api/knowledge/rules", params={"category": "nonsense"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_REQUEST"


async def test_rules_can_be_narrowed_to_what_a_situation_would_select(
    client: httpx.AsyncClient,
) -> None:
    """`?applies=` answers "what would this bean be told"."""

    response = await client.get(
        "/api/knowledge/rules",
        params={"applies": "roast_level:light,process:natural,style:bloom,signal:balance:sour"},
    )
    keys = {item["key"] for item in response.json()["data"]["items"]}

    assert "natural.light" in keys
    assert "washed.light" not in keys, "a washed cell is not what this bean would be told"
    assert "sour" in keys
    assert "bitter" not in keys
    # Style-keyed rules follow the style, and the procedure rules always apply.
    assert "hierarchy" in keys
    bloom_ratio = {
        item["key"]
        for item in response.json()["data"]["items"]
        if item["category"] == "ratio_by_style"
    }
    assert "bloom" in bloom_ratio
    assert "turbo" not in bloom_ratio


async def test_a_malformed_applies_token_is_a_400(
    client: httpx.AsyncClient,
) -> None:

    response = await client.get("/api/knowledge/rules", params={"applies": "light"})

    assert response.status_code == 400
    assert response.json()["error"]["details"]["field"] == "applies"


async def test_a_rule_can_be_disabled_and_edited(
    client: httpx.AsyncClient,
) -> None:
    rule = (await client.get("/api/knowledge/rules", params={"category": "increments"})).json()[
        "data"
    ]["items"][0]

    disabled = await client.patch(f"/api/knowledge/rules/{rule['id']}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False

    edited = await client.patch(
        f"/api/knowledge/rules/{rule['id']}", json={"value": {"text": "mine", "min": 3}}
    )
    assert edited.json()["data"]["value"] == {"text": "mine", "min": 3}
    assert edited.json()["data"]["edited"] is True

    # A re-seed leaves the edit alone.
    reload = await client.post("/api/knowledge/rules/reload")
    assert reload.status_code == 200
    after = await client.get(f"/api/knowledge/rules?category={rule['category']}")
    mine = next(item for item in after.json()["data"]["items"] if item["id"] == rule["id"])
    assert mine["value"]["text"] == "mine"
    assert mine["enabled"] is False


async def test_patching_a_rule_that_does_not_exist_is_a_404(
    client: httpx.AsyncClient,
) -> None:
    response = await client.patch("/api/knowledge/rules/999999", json={"enabled": False})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"

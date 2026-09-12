"""``/api/prompts`` — the editor's contract, including the acceptance criterion."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.llm.prompts import PromptService


async def data(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    assert payload["ok"] is True, payload
    payload_data: dict[str, Any] = payload["data"]
    return payload_data


async def test_the_shipped_prompts_are_seeded_at_boot(client: httpx.AsyncClient) -> None:
    listing = await data(await client.get("/api/prompts"))

    names = [prompt["name"] for prompt in listing["prompts"]]
    assert "ping" in names
    assert "fragments/style" in names
    assert all(prompt["edited"] is False for prompt in listing["prompts"])


async def test_a_fragment_is_reachable_by_its_path(client: httpx.AsyncClient) -> None:
    prompt = await data(await client.get("/api/prompts/fragments/style"))

    assert prompt["fragment"] is True
    assert "template:" in prompt["content"]


async def test_an_unknown_prompt_is_a_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/prompts/nope")).status_code == 404


async def test_editing_a_prompt_changes_the_next_render_without_a_restart(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """The chunk's acceptance criterion, end to end.

    The edit is made to the shipped text rather than to a fixture, so the test
    breaks if ping.yaml stops saying what it renders - which is the only way a
    render assertion can go quietly wrong.
    """
    service = PromptService(PromptsRepository(app.state.db))
    before = await service.load("ping", {"topic": "grind size"})
    original = (await data(await client.get("/api/prompts/ping")))["content"]
    assert "Name the model you are" in original

    response = await client.put(
        "/api/prompts/ping",
        json={"content": original.replace("Name the model you are", "State your model id")},
    )

    assert response.status_code == 200
    assert (await data(response))["edited"] is True
    after = await PromptService(PromptsRepository(app.state.db)).load(
        "ping", {"topic": "grind size"}
    )
    assert "Name the model you are" in before.user
    assert "State your model id" in after.user


async def test_a_broken_edit_is_rejected_before_it_is_stored(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/prompts/ping", json={"content": "name: ping\nuser: [oops"})

    assert response.status_code == 400
    assert "YAML" in response.json()["error"]["message"]
    stored = await data(await client.get("/api/prompts/ping"))
    assert stored["edited"] is False


async def test_an_edit_naming_a_missing_fragment_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.put(
        "/api/prompts/ping", json={"content": "name: ping\nuser: |\n  {{> nowhere}}\n"}
    )

    assert response.status_code == 400
    assert "nowhere" in response.json()["error"]["message"]


async def test_reset_restores_what_shipped(client: httpx.AsyncClient) -> None:
    original = (await data(await client.get("/api/prompts/ping")))["content"]
    await client.put("/api/prompts/ping", json={"content": original + "\n# scribbled on\n"})

    restored = await data(await client.post("/api/prompts/ping/reset"))

    assert restored["edited"] is False
    assert restored["content"] == original


async def test_reload_reapplies_the_seeding_rules(client: httpx.AsyncClient) -> None:
    """Idempotent by design: a steady-state re-seed touches nothing."""
    assert (await data(await client.post("/api/prompts/reload")))["changed"] == 0


async def test_every_shipped_prompt_round_trips_through_a_save(
    client: httpx.AsyncClient,
) -> None:
    """Its own content must pass the validator it will be saved through.

    The list comes from the API rather than from a parametrize literal, so
    shipping a prompt is one file and not two edits — and a prompt that only
    exists because somebody added it to `gaggiclanker/prompts/` is covered the
    moment it is seeded. A hard-coded pair is how `analysis` and
    `analysis-user` went uncovered.
    """
    listing = await data(await client.get("/api/prompts"))
    names = [prompt["name"] for prompt in listing["prompts"]]
    assert {"ping", "fragments/style", "analysis", "analysis-user"} <= set(names)

    for name in names:
        prompt = await data(await client.get(f"/api/prompts/{name}"))

        response = await client.put(f"/api/prompts/{name}", json={"content": prompt["content"]})

        assert response.status_code == 200, f"{name} does not survive its own validator"
        assert (await data(response))["edited"] is False

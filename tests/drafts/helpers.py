"""Shared helpers for the draft and board tests: drafts made from the mirror, Sets to put them on.

What used to live in the staged push's replace tests (``test_replace``), which went with the
staged box; the helpers are what the board's tests still build their scenarios from.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWriteRow, DeviceWritesRepository
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id, data
from tests.llm.conftest import FakeProvider

APP_LABEL = f"{BASE_LABEL} [AI]"

Live = tuple[FastAPI, httpx.AsyncClient]


async def draft_of(
    app: FastAPI, client: httpx.AsyncClient, provider: FakeProvider, label: str, bar: float
) -> dict[str, Any]:
    """A draft of the mirrored profile ``label`` with its first pump set to ``bar``."""
    profile = await base_profile(app, label)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    provider.script = [json.dumps({"profile": document, "change_summary": f"{bar} bar."})]
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": await base_version_id(app, label), "notes": "change it"},
    )
    return dict(data(response))


async def make_set(client: httpx.AsyncClient, name: str = "Replace test") -> int:
    """A Set with one version, so a push has somewhere to record itself."""
    bean = data(await client.post("/api/beans", json={"name": name, "roaster": "nobody"}))
    stored = data(
        await client.post(
            "/api/sets",
            json={
                "name": name,
                "bean_id": bean["id"],
                "version": {"dose_g": 18.0, "target_yield_g": 36.0},
            },
        )
    )
    return int(stored["id"])


async def manual_draft(
    app: FastAPI,
    client: httpx.AsyncClient,
    base_label: str,
    label: str,
    bar: float,
    *,
    same_document: bool = False,
) -> dict[str, Any]:
    """A draft that carries its whole document, as a fork or a stage-as-is does."""
    profile = await base_profile(app, base_label)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["label"] = label
    if not same_document:
        document["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    response = await client.post(
        "/api/profile-drafts",
        json={
            "base_version_id": await base_version_id(app, base_label),
            "profile": document,
            "change_summary": "manual",
        },
    )
    assert response.status_code == 201, response.text
    return dict(data(response))


async def set_device_ids(app: FastAPI, set_id: int) -> list[str | None]:
    """The device profile each version of a Set names, oldest first."""
    rows = await app.state.db.fetch_all(
        "SELECT pushed_device_profile_id AS d FROM set_versions "
        "WHERE set_id = ? ORDER BY version_no",
        (set_id,),
    )
    return [row["d"] for row in rows]


def document_of(fake: FakeDevice, device_id: str) -> dict[str, Any]:
    """A profile on the machine as a document a draft can carry."""
    found = next(p for p in fake.profiles if p["id"] == device_id)
    return {k: v for k, v in found.items() if k not in ("id", "favorite", "selected")}


async def manual_from(
    client: httpx.AsyncClient, base_version_id: int, document: dict[str, Any], bar: float
) -> dict[str, Any]:
    """A draft of an explicit document, made from a given stored version."""
    edited = copy.deepcopy(document)
    edited["phases"][0]["pump"] = {"target": "pressure", "pressure": bar, "flow": 0}
    response = await client.post(
        "/api/profile-drafts",
        json={"base_version_id": base_version_id, "profile": edited, "change_summary": f"{bar}"},
    )
    assert response.status_code == 201, response.text
    return dict(data(response))


async def make_set_on(client: httpx.AsyncClient, name: str, profile_version_id: int) -> int:
    """A Set whose first version names a stored profile, as one made from the library does."""
    bean = data(await client.post("/api/beans", json={"name": name, "roaster": "nobody"}))
    response = await client.post(
        "/api/sets",
        json={
            "name": name,
            "bean_id": bean["id"],
            "version": {
                "dose_g": 18.0,
                "target_yield_g": 36.0,
                "profile_version_id": profile_version_id,
            },
        },
    )
    assert response.status_code < 300, response.text
    return int(data(response)["id"])


async def grind_change(client: httpx.AsyncClient, set_id: int) -> None:
    """A version that changes only the grind: it pushes nothing and names no device id."""
    response = await client.post(f"/api/sets/{set_id}/versions", json={"grind_value": 21.0})
    assert response.status_code < 300, response.text


def on_machine(fake: FakeDevice) -> list[str]:
    return [str(p["id"]) for p in fake.profiles]


def ids_labelled(fake: FakeDevice, label: str) -> list[str]:
    return [str(p["id"]) for p in fake.profiles if p.get("label") == label]


async def audit(app: FastAPI) -> list[DeviceWriteRow]:
    """Every device write, oldest first."""
    rows = await DeviceWritesRepository(app.state.db).list_writes(limit=500)
    return list(reversed(rows))


def kinds(rows: list[DeviceWriteRow], *wanted: str) -> list[tuple[str, str | None, str]]:
    return [(r.kind, r.device_id, r.result) for r in rows if r.kind in wanted]

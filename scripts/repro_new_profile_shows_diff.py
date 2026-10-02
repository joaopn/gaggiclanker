#!/usr/bin/env python
"""Reproduce: a profile designed from scratch is shown as a diff against another profile.

    uv run python scripts/repro_new_profile_shows_diff.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A draft always has a base version, so a profile the agent designed with no fork
(the synthetic "Empty baseline") or that the starting point's new-profile option
authored (the library's most-used profile) was stored as an edit of that base.
The Profiles page then listed every field as a change from it and its header
said "from Empty baseline". The detail of a new profile must say it is new and
serve no base profile; a draft edited from a real profile must keep its diff.

Through the real app: the design route and the tool as the chat's runner
dispatches it, the starting point's draft step, and the draft detail route.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations
from gaggiclanker.db.repos.profiles import SYNTHETIC_BASE_LABEL, ProfilesRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings
from gaggiclanker.starting.models import StartingPointOption
from gaggiclanker.tools.registry import registry
from gaggiclanker.tools.scope import ToolScope

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "profiles"

#: The synthetic base's own description, as an earlier version of the app stored it.
OLD_BASE_DESCRIPTION = (
    "An empty baseline, created because the archive held no profile to diff a new draft against."
)

WRITTEN: dict[str, Any] = {
    "type": "pro",
    "temperature": 92,
    "phases": [
        {
            "name": "Extraction",
            "phase": "brew",
            "valve": 1,
            "duration": 40,
            "pump": {"target": "pressure", "pressure": 9, "flow": 0},
            "targets": [{"type": "volumetric", "operator": "gte", "value": 40}],
        }
    ],
}


def data(response: httpx.Response) -> Any:
    body = response.json()
    if not body.get("ok"):
        raise SystemExit(f"{response.request.url.path} answered {response.status_code}: {body}")
    return body["data"]


def verdict(name: str, detail: dict[str, Any], listed: dict[str, Any], *, new: bool) -> bool:
    """``detail`` is the draft's detail; ``listed`` is the row the card is drawn from."""
    draft = detail["draft"]
    problems: list[str] = []
    for where, row in (("detail", draft), ("list row", listed)):
        if bool(row.get("is_new")) != new:
            problems.append(f"the {where} says is_new={row.get('is_new')!r}, expected {new}")
        # A new profile changes no stop condition; an edit that moved one still says so.
        if bool(row.get("stop_condition_changes")) == new:
            problems.append(
                f"the {where} carries stop_condition_changes={row.get('stop_condition_changes')!r}"
            )
    if new and listed.get("base_label") is not None:
        problems.append(f"the list row says base_label={listed.get('base_label')!r}")
    if bool(detail.get("is_new")) != new:
        problems.append(f"is_new is {detail.get('is_new')!r}, expected {new}")
    if new:
        if detail.get("base_profile") is not None:
            problems.append("a base profile is served")
        if draft.get("base_label") is not None:
            problems.append(f"base_label is {draft.get('base_label')!r}")
        if SYNTHETIC_BASE_LABEL in json.dumps(detail):
            problems.append("the synthetic baseline is named in the detail")
    elif detail.get("base_profile") is None:
        problems.append("the diff's base profile is missing")
    print(f"{name}: {'OK' if not problems else 'BUG: ' + '; '.join(problems)}")
    return not problems


async def listed(client: httpx.AsyncClient, draft_id: int) -> dict[str, Any]:
    rows = data(await client.get("/api/profile-drafts?open=true"))["items"]
    return dict(next(row for row in rows if row["id"] == draft_id))


async def archive_from_before_the_upgrade(path: Path, scratch: Path) -> int:
    """A database as the app left it before drafts were marked new, holding one old draft.

    The draft is based on the synthetic baseline and carries the stop-condition list that was
    computed against it, which is what the old card warned about. Built from the migrations that
    exist below 0035, so on a tree without that migration it is simply the current schema.
    """
    directory = scratch / "migrations"
    directory.mkdir()
    for file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if file.name < "0035":
            shutil.copy(file, directory / file.name)
    db = Database(path)
    await db.connect()
    try:
        await run_migrations(db, directory)

        async def version(label: str, description: str, content_hash: str) -> int:
            cursor = await db.execute(
                "INSERT INTO profile_versions (content_hash, label, type, json, source)"
                " VALUES (?, ?, 'pro', ?, 'draft')",
                (content_hash, label, json.dumps({"label": label, "description": description})),
            )
            return int(cursor.lastrowid or 0)

        base = await version(SYNTHETIC_BASE_LABEL, OLD_BASE_DESCRIPTION, "old-base")
        drafted = await version("Old design [AI]", "", "old-draft")
        stops = json.dumps(
            [{"phase_index": 0, "kind": "changed", "before": {"value": 36}, "after": {"value": 40}}]
        )
        cursor = await db.execute(
            "INSERT INTO profile_drafts (base_version_id, draft_version_id, status, created_at,"
            " updated_at, stop_condition_changes_json) VALUES (?, ?, 'draft', 'x', 'x', ?)",
            (base, drafted, stops),
        )
        return int(cursor.lastrowid or 0)
    finally:
        await db.close()


async def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "data").mkdir()
        old_draft = await archive_from_before_the_upgrade(root / "data" / "gaggiclanker.db", root)
        env = EnvSettings(DATA_DIR=str(root / "data"), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
        app = create_app(env, web_dist=root / "no-dist")
        async with app.router.lifespan_context(app):
            db = app.state.db
            library = await ProfilesRepository(db).ensure_version(
                Profile.model_validate(
                    {**json.loads((FIXTURE / "docs-medium-18g.json").read_text()), "label": "Mine"}
                )
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                bean = data(await client.post("/api/beans", json={"name": "Kenya AA"}))
                grinder = data(
                    await client.post(
                        "/api/grinders", json={"name": "Niche Zero", "step_unit": "numbers"}
                    )
                )
                created = data(
                    await client.post(
                        "/api/sets/design",
                        json={"bean_id": bean["id"], "grinder_id": grinder["id"]},
                    )
                )
                ctx = app.state.chat.tool_context(
                    scope=await ToolScope.resolve(db, created["set"]["id"]), run_id=None
                )
                outcome = await registry.dispatch(
                    ctx,
                    "propose_initial_recipe",
                    {
                        "profile": {"label": "From zero", "patch": WRITTEN},
                        "grind_setting": "a little finer than usual",
                        "grind_is_absolute": False,
                        "dose_g": 18,
                        "target_yield_g": 40,
                        "reason": "A plain first shot to taste the bean.",
                    },
                )
                if not outcome.ok:
                    print(f"the tool refused a whole document: {outcome.data}")
                    return 1
                detail = data(await client.get(f"/api/profile-drafts/{outcome.data['draft_id']}"))
                ok &= verdict(
                    "(a) a Set designed with no fork",
                    detail,
                    await listed(client, outcome.data["draft_id"]),
                    new=True,
                )

                option = StartingPointOption.model_validate(
                    {
                        "option": "recommended",
                        "headline": "A new profile",
                        "grind_setting": "20",
                        "dose_g": 18,
                        "yield_g": 36,
                        "ratio": 2,
                        "temperature_c": 92,
                        "profile": {
                            "label": "Wizard new",
                            **{
                                **WRITTEN,
                                "phases": [
                                    {
                                        **WRITTEN["phases"][0],
                                        "targets": [
                                            {"type": "volumetric", "operator": "gte", "value": 44}
                                        ],
                                    }
                                ],
                            },
                        },
                        "rationale": "Nothing in the library fits.",
                    }
                )
                draft = await app.state.starting._draft_for(option)
                assert draft is not None
                detail = data(await client.get(f"/api/profile-drafts/{draft.id}"))
                ok &= verdict(
                    "(b) the starting point's new profile",
                    detail,
                    await listed(client, draft.id),
                    new=True,
                )

                moved = json.loads((FIXTURE / "docs-medium-18g.json").read_text())
                moved["phases"][-1]["targets"] = [
                    {"type": "volumetric", "operator": "gte", "value": 61}
                ]
                detail = data(await client.get(f"/api/profile-drafts/{old_draft}"))
                ok &= verdict(
                    "(d) a draft made before the upgrade",
                    detail,
                    await listed(client, old_draft),
                    new=True,
                )

                edited = data(
                    await client.post(
                        "/api/profile-drafts",
                        json={
                            "base_version_id": library[0].id,
                            "profile": {
                                **moved,
                                "label": "Mine",
                                "temperature": 91,
                            },
                        },
                    )
                )
                draft_id = edited["id"]
                detail = data(await client.get(f"/api/profile-drafts/{draft_id}"))
                ok &= verdict(
                    "(c) control: an edit of a real profile",
                    detail,
                    await listed(client, draft_id),
                    new=False,
                )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

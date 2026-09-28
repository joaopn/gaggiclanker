#!/usr/bin/env python
"""Reproduce: a curve channel moved into base never reaches the opening context or the search.

    uv run python scripts/repro_base_curve_never_loaded.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

Settings → Shot information lets a person move any curve channel (pressure,
puck flow, …) into base, which is what a Set conversation's opening context and
the shot search show for every shot. The shot tools load a shot's samples when
their tier carries a curve, but the opening context and `list_set_shots`
loaded their shots without samples, so the curve the person asked for came out
empty: the channel was silently left out. The settings page's base and
autoload estimates did count it, so they overstated what a conversation sent.

The fix: both load the samples, in one query for all their shots, exactly when
the base tier carries a curve channel.

Through the real app: a shot imported from the fixture export, filed in a Set,
pressure moved into base; then the opening context the runner builds and the
search tool as the chat dispatches it. No model is needed for either.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from gaggiclanker.chat.context import opening_context
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings
from gaggiclanker.shotinfo.catalogue import effective_tiers
from gaggiclanker.tools.registry import registry
from gaggiclanker.tools.scope import ToolScope

EXPORT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "exports" / "shot-129.json"


def data(response: httpx.Response) -> Any:
    body = response.json()
    if not body.get("ok"):
        raise SystemExit(f"{response.request.url.path} answered {response.status_code}: {body}")
    return body["data"]


def has_curve(text: str) -> bool:
    """A curve table with a pressure column under it."""
    if "[Curve]\n" not in text:
        return False
    return "pressure (bar)" in text.split("[Curve]\n", 1)[1].splitlines()[1]


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = EnvSettings(DATA_DIR=str(root / "data"), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
        app = create_app(env, web_dist=root / "no-dist")
        async with app.router.lifespan_context(app):
            db = app.state.db
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                imported = data(
                    await client.post(
                        "/api/import",
                        files={"files": ("shot-129.json", EXPORT.read_bytes(), "application/json")},
                    )
                )
                bean = data(await client.post("/api/beans", json={"name": "Kenya AA"}))
                grinder = data(
                    await client.post(
                        "/api/grinders", json={"name": "Niche Zero", "step_unit": "numbers"}
                    )
                )
                made = data(
                    await client.post(
                        "/api/sets",
                        json={
                            "name": "Kenya on the Niche",
                            "bean_id": bean["id"],
                            "grinder_id": grinder["id"],
                            "version": {"grind_setting": "14", "dose_g": 18},
                        },
                    )
                )
            set_id = made["id"]
            shot_id = next(
                row["shot_id"] for row in imported["items"] if row.get("shot_id") is not None
            )
            version_id = made["current_version_id"]
            assert await SetsRepository(db).assign_shot(shot_id, version_id)
            await ShotInfoTiersRepository(db).set_tier(
                ShotInfoTierWrite(item_key="curve_pressure", tier="base")
            )

            scope = await ToolScope.resolve(db, set_id)
            context = await opening_context(db, scope, tiers=await effective_tiers(db))
            ctx = app.state.chat.tool_context(scope=scope, run_id=None)
            outcome = await registry.dispatch(ctx, "list_set_shots", {})
            if not outcome.ok:
                print(f"the search refused: {outcome.data}")
                return 1
            [hit] = outcome.data["shots"]
            shown = await registry.dispatch(ctx, "get_shot", {"shot_id": shot_id})
            assert shown.ok, shown.data

    found = {
        "get_shot": has_curve(shown.data["text"]),
        "opening context": has_curve(context),
        "list_set_shots": has_curve(hit["text"]),
    }
    for where, curve in found.items():
        print(f"{where:16} pressure curve in base: {'yes' if curve else 'NO'}")
    if not all(found.values()):
        print("BUG: a curve channel moved into base is left out where shots are shown unasked.")
        return 1
    print("OK: every base rendering carries the curve the person moved into base.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

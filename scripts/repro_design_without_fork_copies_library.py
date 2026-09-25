#!/usr/bin/env python
"""Reproduce: a Set designed with no profile to fork is built on the library's most-used profile.

    uv run python scripts/repro_design_without_fork_copies_library.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

New Set → Design it with the agent takes an optional profile to fork. Left
empty, the design was meant to start from nothing, and the dialog said instead
"the agent starts from one in the library": `propose_initial_recipe` fell back
to the library's most-used brew profile and merged the agent's document into
it key by key. Whatever of that profile the agent did not write — its
description here — came along into a profile that was meant to be new, and the
draft on the Profiles page read as an edit of it.

The fix: with no fork, the document the agent writes is the whole profile, and
the draft is diffed against the synthetic empty baseline.

Through the real app: the design route, then the tool as the chat's runner
dispatches it. No model is needed to call a tool.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import SYNTHETIC_BASE_LABEL, ProfilesRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings
from gaggiclanker.tools.registry import registry
from gaggiclanker.tools.scope import ToolScope

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "profiles"

#: What the agent writes: a whole profile, with no description of its own.
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


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = EnvSettings(DATA_DIR=str(root / "data"), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
        app = create_app(env, web_dist=root / "no-dist")
        async with app.router.lifespan_context(app):
            db = app.state.db
            # The library: one brew profile, with words of its own to leak.
            document = json.loads((FIXTURE / "docs-medium-18g.json").read_text())
            await ProfilesRepository(db).ensure_version(
                Profile.model_validate(
                    {**document, "label": "What I brew", "description": "Library words"}
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
            set_id = created["set"]["id"]

            ctx = app.state.chat.tool_context(
                scope=await ToolScope.resolve(db, set_id), run_id=None
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
            draft = await ProfileDraftsRepository(db).get(outcome.data["draft_id"])
            assert draft is not None
            profiles = ProfilesRepository(db)
            base = await profiles.get_version(draft.base_version_id)
            made = await profiles.get_version(outcome.data["recipe"]["profile_version_id"])
            assert base is not None and made is not None and made.profile is not None

    description = made.profile.get("description", "")
    print(f"draft base:               {base.label!r}")
    print(f"new profile's description: {description!r}")
    if base.label != SYNTHETIC_BASE_LABEL or description:
        print("BUG: the design was built on a library profile the person did not pick.")
        return 1
    print("OK: with no fork the profile is the agent's own document, diffed against nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

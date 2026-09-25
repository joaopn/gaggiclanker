#!/usr/bin/env python
"""Reproduce: an accepted first-recipe card no longer carries the recipe it set.

    uv run python scripts/repro_accepted_design_card_changes.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A proposal's ``changes`` are computed, not stored: the proposal's patch is
applied to its base version and the two are diffed, so a card and the log entry
it becomes are drawn by the same function. For a change that is right before
and after the person answers, because accepting appends a *new* version and
leaves the base as it was.

A Set being designed breaks that. Its first-recipe card is based on version 1,
which is empty, so while the card waits the diff is the whole recipe. Accepting
it fills version 1 *in place* with that same recipe, and from then on the base
and the preview are equal: ``changes`` comes back empty. The card in the chat
and on the Set page can only say "version 1 is set", and scrolling back through
the design conversation no longer shows what was agreed.

The fix is to diff a first-recipe card against an empty recipe whatever its
status, since that is what it was a change to.

Through the real app and its routes, with the card stored through the
repository the chat's tool writes it with: no model is needed to make a card.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetVersionPatch
from gaggiclanker.domain.models import Profile
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "profiles"
RECIPE_FIELDS = {
    "profile_version_id",
    "profile_temperature_c",
    "grind_setting",
    "dose_g",
    "target_yield_g",
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

                # The card, as `propose_initial_recipe` stores it: a draft of a
                # new profile, and the recipe naming that draft's version.
                db = app.state.db
                profiles = ProfilesRepository(db)
                document = json.loads((FIXTURE / "docs-medium-18g.json").read_text())
                base, _ = await profiles.ensure_version(
                    Profile.model_validate({**document, "label": "Library profile"})
                )
                drafted, _ = await profiles.ensure_version(
                    Profile.model_validate({**document, "label": "Designed for Kenya"})
                )
                draft = await ProfileDraftsRepository(db).create(
                    ProfileDraftWrite(
                        base_version_id=base.id,
                        draft_version_id=drafted.id,
                        change_summary="Designed in chat.",
                    )
                )
                stored = await SetProposalsRepository(db).create(
                    set_id,
                    ProposalWrite(
                        kind="design",
                        draft_id=draft.id,
                        thread_id=created["thread_id"],
                        reason="A longer, gentler shot for more body.",
                        patch=SetVersionPatch(
                            profile_version_id=drafted.id,
                            grind_setting="20",
                            grind_value=20,
                            dose_g=18,
                            target_yield_g=40,
                        ),
                    ),
                )
                assert stored.proposal is not None, stored.refused
                proposal_id = stored.proposal.id

                async def fields() -> set[str]:
                    listed = data(await client.get(f"/api/sets/{set_id}/proposals"))
                    card = next(item for item in listed["items"] if item["id"] == proposal_id)
                    return {change["field"] for change in card["changes"]}

                waiting = await fields()
                accepted = data(
                    await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept")
                )
                answered = {change["field"] for change in accepted["proposal"]["changes"]}
                listed = await fields()

    print(f"waiting card's changes:           {sorted(waiting)}")
    print(f"accept answer's changes:          {sorted(answered)}")
    print(f"accepted card's changes (listed): {sorted(listed)}")
    missing = [
        where
        for where, seen in (("waiting", waiting), ("accept answer", answered), ("listed", listed))
        if not RECIPE_FIELDS <= seen
    ]
    if missing:
        print(f"BUG: the recipe is missing from the card ({', '.join(missing)})")
        return 1
    print("OK: the card carries its recipe before and after it is accepted")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

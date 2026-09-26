#!/usr/bin/env python
"""Reproduce: accepting a card says nothing to the conversation it came from.

    uv run python scripts/repro_accept_not_told_to_agent.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

One conversation is one version: a Set's conversation belongs to the version it
was opened on, and a version the person accepts is a new experiment with a
conversation of its own. Nothing held the agent or the app to that.

* Accept on a card in the chat called the accept route and nothing else, so the
  agent was never told, went on as if the card were waiting, and never said
  that the new version is argued in a new conversation. That half is in the
  web and is pinned by its tests (the proposal card and the Chat page); what
  the agent is told to do with the message is checked here, in the prompts.
* The design prompt said the conversation a first recipe was designed in
  "becomes the Set's ordinary one" once accepted, and the Set prompt had no
  word about an accepted card.
* Discuss in chat on version 1 of a designed Set opened the newest conversation
  about version 1, which after the accept is the design itself: the button
  took the person back into the room the agent should be sending them out of.

Through the real app and its routes, with the first-recipe card stored through
the repository the chat's tool writes it with: no model is needed.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
import yaml

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetVersionPatch
from gaggiclanker.domain.models import Profile
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "profiles"
PROMPTS = ROOT / "gaggiclanker" / "prompts"


def data(response: httpx.Response) -> Any:
    body = response.json()
    if not body.get("ok"):
        raise SystemExit(f"{response.request.url.path} answered {response.status_code}: {body}")
    return body["data"]


def prose(name: str) -> str:
    """A prompt's system text as prose: where the lines wrap is not the point."""
    loaded = yaml.safe_load((PROMPTS / f"{name}.yaml").read_text())
    return " ".join(str(loaded["system"]).split())


async def main() -> int:
    bugs: list[str] = []
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
                design_room = created["thread_id"]
                v1 = created["version"]["id"]

                async def discuss() -> int:
                    opened = await client.post(
                        "/api/chat/threads/open", json={"set_id": set_id, "set_version_id": v1}
                    )
                    return int(data(opened)["id"])

                before = await discuss()
                data(await client.post(f"/api/sets/{set_id}/proposals/{proposal_id}/accept"))
                after = await discuss()

    print(f"design conversation: {design_room}")
    print(f"Discuss on v1 while designing: {before}; after the accept: {after}")
    if before != design_room:
        bugs.append("Continue designing no longer opens the design conversation")
    if after == design_room:
        bugs.append("Discuss on version 1 reopens the design conversation after the accept")

    design, change = prose("chat-design"), prose("chat-set")
    if "becomes the Set's ordinary one" in design:
        bugs.append("the design prompt says the design conversation becomes the Set's own")
    if '"Accepted:"' not in change or "they must start a new conversation" not in change:
        bugs.append("the Set prompt says nothing about an accepted card or a new conversation")

    for bug in bugs:
        print(f"BUG: {bug}")
    if bugs:
        return 1
    print("OK: an accepted card ends the conversation's version, and Discuss agrees")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

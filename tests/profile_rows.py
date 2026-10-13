"""A profile no Set has, for a test that needs a Set (a Set brews a profile, and a profile has at
most one Set)."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import httpx

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.domain.models import Profile

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "profiles" / "docs-medium-18g.json"
_counter = itertools.count(1)


async def free_profile_version(db: Database, label: str | None = None) -> int:
    """A new profile on the list, switched off, with no Set: the version it has active.

    Each call makes another profile (its own label), so a test that makes several Sets gives
    each its own.
    """
    document = json.loads(_FIXTURE.read_text())
    document["label"] = label or f"Profile {next(_counter)}"
    version, _ = await ProfilesRepository(db).ensure_version(
        Profile.model_validate(document), owner=None
    )
    row = await ProfileBoardRepository(db).insert(
        BoardRowWrite(
            label=document["label"],
            current_version_id=version.id,
            origin="adopted",
            on_machine=False,
        )
    )
    return row.current_version_id


def database_of(client: httpx.AsyncClient) -> Database:
    """The database behind a test client wired to the app in-process."""
    app: Any = client._transport.app  # type: ignore[attr-defined]
    db: Database = app.state.db
    return db


async def with_a_profile(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    """``POST /api/sets``'s body with a profile no Set has, when it names none."""
    version = dict(body.get("version") or {})
    if version.get("profile_version_id") is None and "profile_version_id" not in version:
        version["profile_version_id"] = await free_profile_version(database_of(client))
    return {**body, "version": version}


async def own_version(db: Database, version_id: int) -> int:
    """Put the profile version on the list as a profile of its own (switched off), so that a Set
    can start on it; a version a profile already owns is left as it is. Returns the version."""
    version = await ProfilesRepository(db).get_version(version_id)
    assert version is not None
    if version.board_id is None:
        await ProfileBoardRepository(db).insert(
            BoardRowWrite(
                label=version.label,
                current_version_id=version_id,
                origin="adopted",
                on_machine=False,
            )
        )
    return version_id


async def another_version(db: Database, version_id: int, **changes: Any) -> int:
    """Another version of the same profile: the document of ``version_id`` with these top-level
    fields changed, owned and listed by the profile (not made active)."""
    profiles = ProfilesRepository(db)
    version = await profiles.get_version(version_id)
    assert version is not None and version.board_id is not None and version.profile is not None
    made, _ = await profiles.ensure_version(
        Profile.model_validate({**version.profile, **changes}), owner=version.board_id
    )
    await ProfileBoardRepository(db).add_version(version.board_id, made.id, "edit")
    return made.id

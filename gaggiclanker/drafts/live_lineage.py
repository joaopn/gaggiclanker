"""The live profile list answering :func:`~gaggiclanker.db.repos.lineage.place_draft`.

One implementation, used by the draft constructor, the put and the landing the page shows, so
the three cannot disagree about where a draft goes or whether its name is taken.
"""

from __future__ import annotations

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profile_board import BoardRow, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository

__all__ = ["LiveLineage"]


class LiveLineage:
    def __init__(self, db: Database) -> None:
        self.board = ProfileBoardRepository(db)
        self.profiles = ProfilesRepository(db)
        self.sets = SetsRepository(db)

    def label_of(self, profile: BoardRow) -> str:
        return profile.label

    async def by_label(self, label: str) -> BoardRow | None:
        return await self.board.find_live_by_label(label)

    async def by_name_key(self, label: str) -> BoardRow | None:
        return await self.board.find_live_by_name_key(label)

    async def by_set(self, set_id: int) -> BoardRow | None:
        current = await self.sets.current_version(set_id)
        if current is None or current.profile_version_id is None:
            return None
        found = await self.sets.current_device_profile(set_id)
        row = None
        if found is not None:
            row = await self.board.find_live_by_device(found[0])
        if row is None:
            row = await self.board.find_live_by_listed_version(current.profile_version_id)
        return row

    async def base_profile(self, version_id: int) -> BoardRow | None:
        row = await self.board.find_live_by_listed_version(version_id)
        if row is None:
            device = await self.profiles.find_device_id_for_version(version_id)
            row = None if device is None else await self.board.find_live_by_device(device)
        return row

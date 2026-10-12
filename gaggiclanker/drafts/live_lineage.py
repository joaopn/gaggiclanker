"""The live profile list answering :func:`~gaggiclanker.db.repos.lineage.place_draft` and
:func:`~gaggiclanker.db.repos.lineage.change_destination`.

One implementation, used by the draft constructor, the put and the landing the page shows, so
the three cannot disagree about where a draft goes or whether its name is taken.
"""

from __future__ import annotations

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.lineage import pinned_sentence
from gaggiclanker.db.repos.pins import PinsRepository
from gaggiclanker.db.repos.profile_board import BoardRow, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository

__all__ = ["LiveLineage"]


class LiveLineage:
    def __init__(self, db: Database) -> None:
        self.board = ProfileBoardRepository(db)
        self.pins = PinsRepository(db)
        self.profiles = ProfilesRepository(db)
        self.sets = SetsRepository(db)

    def label_of(self, profile: BoardRow) -> str:
        return profile.label

    async def by_id(self, board_id: int) -> BoardRow | None:
        """The profile with this id when it is live: a draft's stored destination."""
        row = await self.board.get(board_id)
        return None if row is None or row.deleted_at is not None else row

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
        """The live profile this version belongs to: its owner, when it has one and that profile
        is live. A version that belongs to no profile (a mirrored file nothing stands on, a
        draft) belongs to the live profile standing on a live file that holds it, if any."""
        owner = await self.profiles.owner_of(version_id)
        if owner is not None:
            return await self.by_id(owner)
        device = await self.profiles.find_device_id_for_version(version_id)
        return None if device is None else await self.board.find_live_by_device(device)

    async def frozen_sentence(self, profile: BoardRow) -> str | None:
        pin = await self.pins.live_for_board(profile.id)
        if pin is None:
            return None
        return pinned_sentence(profile.label, pin.set_name, pin.version_label)

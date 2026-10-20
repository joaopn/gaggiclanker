"""`set_version_pins`: a Set version kept on the machine as a profile of its own.

A pin is a **record**, not a state of the profile-list row. It names the Set version, the row that
carries the copy and the copy itself: the pinned version's profile under the pin's name, a
profile version of its own because a version belongs to one profile. The copy belongs to one
Set version for ever (``profile_version_id`` is unique), so a shot brewed on it files under that
version even after the pin is gone, and no other version can claim it.

This module also owns :func:`judged_profile_version`: the one answer to "whose signature judges a
shot brewed on this profile version". A pin's copy is judged by the profile the pinned Set version
names; anything else by itself. Every signature lookup for a shot goes through it, so the pinned
version's override applies to the copy's shots and a later change to the original's signature
reaches them.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import version_name_sql
from gaggiclanker.db.repos.version_names import label_sql
from gaggiclanker.db.repository import Repository

__all__ = [
    "PinDetail",
    "PinRow",
    "PinWrite",
    "PinsRepository",
    "judged_profile_version",
    "judged_profile_versions",
]


class PinRow(BaseModel):
    """One row of `set_version_pins`."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_version_id: int
    board_id: int
    profile_version_id: int
    pinned_at: str
    unpinned_at: str | None = None

    @property
    def live(self) -> bool:
        return self.unpinned_at is None


class PinWrite(BaseModel):
    """What :meth:`PinsRepository.insert` stores."""

    model_config = ConfigDict(extra="forbid")

    set_version_id: int
    board_id: int
    profile_version_id: int


class PinDetail(PinRow):
    """A pin with the names a page says it in, and the shots brewed on its copy."""

    set_id: int
    set_name: str
    set_archived: bool = False
    #: The pinned version's name, "v1.2".
    version_label: str
    #: The profile the pinned version brews (the original, the one that judges the pin's shots).
    original_version_id: int | None = None
    #: The original profile's name (its owner's).
    original_label: str | None = None
    #: The name the copy has on the machine (the label of the profile-list row that carries it).
    name: str
    #: Shots filed under the pinned version whose profile is the copy.
    shot_count: int = 0


_DETAIL_SELECT = f"""
    SELECT p.*, v.recipe_id AS set_id, s.name AS set_name, s.archived AS set_archived,
           {label_sql("v")} AS version_label,
           v.profile_version_id AS original_version_id,
           {version_name_sql("opv")} AS original_label,
           cb.label AS name,
           (SELECT COUNT(*) FROM shots sh
             WHERE sh.recipe_version_id = p.recipe_version_id
               AND sh.profile_version_id = p.profile_version_id) AS shot_count
    FROM recipe_version_pins p
    JOIN recipe_versions v ON v.id = p.recipe_version_id
    JOIN recipes s ON s.id = v.recipe_id
    JOIN profile_board cb ON cb.id = p.board_id
    LEFT JOIN profile_versions opv ON opv.id = v.profile_version_id
"""  # noqa: S608 - the only interpolations are constant SQL expressions


class PinsRepository(Repository):
    """Reads and writes the pin records."""

    async def get(self, pin_id: int) -> PinRow | None:
        row = await self.db.fetch_one("SELECT * FROM recipe_version_pins WHERE id = ?", (pin_id,))
        return self.to_model(PinRow, row)

    async def detail(self, pin_id: int) -> PinDetail | None:
        row = await self.db.fetch_one(f"{_DETAIL_SELECT} WHERE p.id = ?", (pin_id,))
        return self.to_model(PinDetail, row)

    async def live_for_version(self, set_version_id: int) -> PinRow | None:
        """The version's live pin, if it has one (the unique index allows at most one)."""
        row = await self.db.fetch_one(
            "SELECT * FROM recipe_version_pins WHERE recipe_version_id = ? AND unpinned_at IS NULL",
            (set_version_id,),
        )
        return self.to_model(PinRow, row)

    async def by_copy(self, profile_version_id: int) -> PinRow | None:
        """The pin record, live or not, whose copy is this profile version."""
        row = await self.db.fetch_one(
            "SELECT * FROM recipe_version_pins WHERE profile_version_id = ?", (profile_version_id,)
        )
        return self.to_model(PinRow, row)

    async def by_copy_content(self, content_hash: str, name: str) -> PinRow | None:
        """The pin record, live or not, whose copy is this brew under this name (the first): the
        copy's version brews it and the row that carries the copy is called ``name``."""
        row = await self.db.fetch_one(
            "SELECT p.* FROM recipe_version_pins p "
            "JOIN profile_versions pv ON pv.id = p.profile_version_id "
            "JOIN profile_board b ON b.id = p.board_id "
            "WHERE pv.content_hash = ? AND b.label = ? ORDER BY p.id LIMIT 1",
            (content_hash, name),
        )
        return self.to_model(PinRow, row)

    async def live_for_board(self, board_id: int) -> PinDetail | None:
        """The live pin whose copy the profile-list row carries, if the row is a pinned one."""
        row = await self.db.fetch_one(
            f"{_DETAIL_SELECT} WHERE p.board_id = ? AND p.unpinned_at IS NULL", (board_id,)
        )
        return self.to_model(PinDetail, row)

    async def live_for_boards(self, board_ids: Iterable[int]) -> dict[int, PinDetail]:
        """The live pins of these rows, by row id."""
        ids = sorted(set(board_ids))
        if not ids:
            return {}
        rows = await self.db.fetch_all(
            f"{_DETAIL_SELECT} WHERE p.unpinned_at IS NULL "
            f"AND p.board_id IN ({', '.join('?' * len(ids))})",
            ids,
        )
        return {pin.board_id: pin for pin in self.to_models(PinDetail, rows)}

    async def live_for_set(self, set_id: int) -> list[PinDetail]:
        """A Set's live pins, newest first."""
        rows = await self.db.fetch_all(
            f"{_DETAIL_SELECT} WHERE v.recipe_id = ? AND p.unpinned_at IS NULL "
            "ORDER BY p.pinned_at DESC, p.id DESC",
            (set_id,),
        )
        return self.to_models(PinDetail, rows)

    async def insert(self, write: PinWrite) -> PinRow:
        cursor = await self.db.execute(
            "INSERT INTO recipe_version_pins "
            "(recipe_version_id, board_id, profile_version_id, pinned_at) VALUES (?, ?, ?, ?)",
            (write.set_version_id, write.board_id, write.profile_version_id, utc_now()),
        )
        pin = await self.get(int(cursor.lastrowid or 0))
        if pin is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("the pin vanished between write and read")
        return pin

    async def revive(self, pin_id: int) -> PinRow:
        """Pin again: the record is live from now on."""
        await self.db.execute(
            "UPDATE recipe_version_pins SET unpinned_at = NULL, pinned_at = ? WHERE id = ?",
            (utc_now(), pin_id),
        )
        pin = await self.get(pin_id)
        if pin is None:  # pragma: no cover - the caller read it in this transaction
            raise RuntimeError("the pin vanished between write and read")
        return pin

    async def unpin(self, pin_id: int) -> bool:
        """Unpin: stamp the record and tombstone the copy's row, in the caller's transaction.

        The next sync removes the file through the deleted-row plan (the fresh-load content
        check, the selected profile moving first). A copy that never reached the machine has
        nothing to remove. The row's waiting draft, if the sync has not pushed it yet, is
        discarded with the reason recorded, so the copy leaves no proposal on the Profiles page.
        Returns ``False`` when the pin was not live.
        """
        pin = await self.get(pin_id)
        if pin is None or pin.unpinned_at is not None:
            return False
        await self.mark_unpinned(pin_id)
        board = ProfileBoardRepository(self.db)
        row = await board.get(pin.board_id)
        if row is not None and row.deleted_at is None:
            if row.pending_draft_id is not None:
                await ProfileDraftsRepository(self.db).discard_unsent(
                    [row.pending_draft_id], why="unpinned"
                )
            await board.update(
                row.id,
                BoardRowPatch(deleted_at=utc_now(), pending_draft_id=None),
            )
        return True

    async def unpin_set(self, set_id: int) -> list[int]:
        """Unpin every live pin of a Set (an archived Set receives no shots). The ids."""
        unpinned = []
        for pin in await self.live_for_set(set_id):
            if await self.unpin(pin.id):
                unpinned.append(pin.id)
        return unpinned

    async def mark_unpinned(self, pin_id: int) -> None:
        await self.db.execute(
            "UPDATE recipe_version_pins SET unpinned_at = ? WHERE id = ? AND unpinned_at IS NULL",
            (utc_now(), pin_id),
        )

    async def move_to_set_version(
        self, pin_id: int, set_version_id: int | None, profile_version_id: int
    ) -> None:
        """The machine's side of the pin's file was kept: the pin's record names the machine's
        version (the copy row's active version now) and, when the Set followed, the Set version
        that was made for it. The row and its name are as they were."""
        await self.db.execute(
            "UPDATE recipe_version_pins SET profile_version_id = ?, "
            "recipe_version_id = COALESCE(?, recipe_version_id) WHERE id = ?",
            (profile_version_id, set_version_id, pin_id),
        )

    async def is_copy(self, profile_version_id: int) -> bool:
        return await self.by_copy(profile_version_id) is not None

    async def set_version_naming_content(
        self, content_hash: str, name: str
    ) -> tuple[str, str] | None:
        """The Set and version name of a Set version that brews this brew on a profile called
        ``name``, if any (the first). A profile a Set version brews can never become a pin's
        copy."""
        row = await self.db.fetch_one(
            f"SELECT s.name AS set_name, {label_sql('v')} AS version_label "  # noqa: S608
            "FROM recipe_versions v JOIN recipes s ON s.id = v.recipe_id "
            "JOIN profile_versions pv ON pv.id = v.profile_version_id "
            f"WHERE pv.content_hash = ? AND {version_name_sql('pv')} = ? "
            "ORDER BY v.id LIMIT 1",
            (content_hash, name),
        )
        return None if row is None else (str(row["set_name"]), str(row["version_label"]))


async def judged_profile_versions(
    db: Database, profile_version_ids: Iterable[int]
) -> dict[int, int]:
    """Each profile version mapped to the one whose signature judges the shots brewed on it.

    A pin's copy maps to the profile version its Set version names; every other version maps
    to itself. A copy of a Set version that names no profile (never made: a pin needs one)
    would map to itself.
    """
    ids = sorted(set(profile_version_ids))
    if not ids:
        return {}
    rows = await db.fetch_all(
        "SELECT p.profile_version_id AS copy, v.profile_version_id AS original "  # noqa: S608
        "FROM recipe_version_pins p JOIN recipe_versions v ON v.id = p.recipe_version_id "
        f"WHERE p.profile_version_id IN ({', '.join('?' * len(ids))})",
        ids,
    )
    judged = {i: i for i in ids}
    for row in rows:
        if row["original"] is not None:
            judged[int(row["copy"])] = int(row["original"])
    return judged


async def judged_profile_version(db: Database, profile_version_id: int) -> int:
    """The profile version whose signature judges a shot brewed on ``profile_version_id``.

    The one function every signature lookup for a shot asks (the checks and the shots table's
    Curve check, the shot page, the review's input, the signature lines, the chat): a pin's copy
    answers with the original it was copied from, anything else with itself.
    """
    return (await judged_profile_versions(db, [profile_version_id]))[profile_version_id]

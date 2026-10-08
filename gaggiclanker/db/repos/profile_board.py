"""`profile_board` — the profiles the app means the machine to hold.

The mirror (`device_profiles`) records what the machine *is*; this table records what the
app *wants*. One row per profile rather than per version, so a new version of a profile
replaces the row's current version in place and the row keeps its home-screen choice and its
identity. See `db/schema.sql` for what each column means.

Every write goes through a model here: :class:`BoardRowWrite` to insert,
:class:`BoardRowPatch` to change. A patch carries only the fields it names, so ``None`` can
mean "set to NULL" for a column that allows it without also meaning "leave alone".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.lineage import name_key
from gaggiclanker.db.repository import Repository

__all__ = [
    "VERSION_SOURCES",
    "BoardAdoption",
    "BoardRow",
    "BoardRowPatch",
    "BoardRowWrite",
    "BoardVersion",
    "ProfileBoardRepository",
]

#: Where a whole profile came from. Information only: it no longer decides what a sync may
#: push or remove (every profile the app has synced is the app's to manage).
type BoardOrigin = Literal["adopted", "draft"]

#: Where one version of a profile came from (the CHECK on `profile_board_versions.source`).
type VersionSource = Literal["agent", "edit", "machine", "edited_on_machine", "import"]
VERSION_SOURCES: tuple[str, ...] = ("agent", "edit", "machine", "edited_on_machine", "import")


class BoardRow(BaseModel):
    """One profile on the board."""

    model_config = ConfigDict(extra="forbid")

    id: int
    label: str
    current_version_id: int
    device_profile_id: str | None = None
    device_version_id: int | None = None
    #: Whether a sync should put the profile on the machine (or take it off). Independent of
    #: ``on_home_screen``, which is shown as "Starred" and only applies while it is on.
    on_machine: bool = True
    on_home_screen: bool = True
    origin: BoardOrigin
    failed_version_id: int | None = None
    pending_draft_id: int | None = None
    pending_set_id: int | None = None
    pending_major: bool | None = None
    #: The version this profile was before its newest one, which going back returns to.
    previous_version_id: int | None = None
    #: The content hash of a machine file a person chose to overrule in a conflict ("keep the
    #: app's version"): that same content is not flagged again, a further edit is a new conflict.
    conflict_overruled_hash: str | None = None
    #: Set by going back: the version that was left (the file holding it goes as a going
    #: back, not as a delayed replacement)...
    back_from_version_id: int | None = None
    #: ...and the Set version that was its Set's current one at the click and recorded it. That
    #: Set is not a reason to keep the file, but only while this exact Set version is still the
    #: Set's current one. Both are cleared once the sync has dealt with the file.
    back_from_set_version_id: int | None = None
    deleted_at: str | None = None
    created_at: str
    updated_at: str


class BoardRowWrite(BaseModel):
    """What :meth:`ProfileBoardRepository.insert` stores."""

    model_config = ConfigDict(extra="forbid")

    label: str
    current_version_id: int
    device_profile_id: str | None = None
    device_version_id: int | None = None
    on_machine: bool = True
    on_home_screen: bool = True
    origin: BoardOrigin
    pending_draft_id: int | None = None
    pending_set_id: int | None = None
    pending_major: bool | None = None
    #: Where the row's first version came from; it is recorded in the profile's version list.
    version_source: VersionSource = "machine"


class BoardRowPatch(BaseModel):
    """The fields of a row a caller changes; only the ones it names are written."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    current_version_id: int | None = None
    device_profile_id: str | None = None
    device_version_id: int | None = None
    on_machine: bool | None = None
    on_home_screen: bool | None = None
    origin: BoardOrigin | None = None
    failed_version_id: int | None = None
    pending_draft_id: int | None = None
    pending_set_id: int | None = None
    pending_major: bool | None = None
    previous_version_id: int | None = None
    conflict_overruled_hash: str | None = None
    back_from_version_id: int | None = None
    back_from_set_version_id: int | None = None
    deleted_at: str | None = None


#: Columns a patch may name that hold a boolean, stored as 0/1.
_BOOLEAN_COLUMNS = frozenset({"on_machine", "on_home_screen", "pending_major"})


class BoardVersion(BaseModel):
    """One version a profile has been, with where it came from."""

    model_config = ConfigDict(extra="forbid")

    board_id: int
    version_id: int
    added_at: str
    source: VersionSource


class BoardAdoption(BaseModel):
    """When the machine's own profiles were taken onto the board, and from which machine."""

    model_config = ConfigDict(extra="forbid")

    id: int
    adopted_at: str
    host: str = ""
    #: Set when a pull found the machine looking reset; a pull writes nothing until a
    #: person resumes it.
    paused_at: str | None = None
    paused_reason: str | None = None
    #: A person resumed after a pause and the next write phase has not run yet: it does not
    #: judge the machine reset again, since the person has answered that.
    resume_pending: bool = False


class ProfileBoardRepository(Repository):
    """Reads and writes the board."""

    async def list_rows(self, *, include_deleted: bool = False) -> list[BoardRow]:
        """The board in a fixed order (oldest row first), so a plan built from it is stable."""
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        rows = await self.db.fetch_all(f"SELECT * FROM profile_board {where} ORDER BY id")  # noqa: S608 - literal clause
        return self.to_models(BoardRow, rows)

    async def get(self, row_id: int) -> BoardRow | None:
        row = await self.db.fetch_one("SELECT * FROM profile_board WHERE id = ?", (row_id,))
        return self.to_model(BoardRow, row)

    async def find_deleted_with_file(self, version_id: int) -> BoardRow | None:
        """A deleted app row for this version whose machine file the pull has not dealt with.

        Putting the same profile back on the board while its old file is still waiting
        (a Set may be brewing it) revives that row instead of pushing a second identical copy.
        """
        row = await self.db.fetch_one(
            "SELECT * FROM profile_board WHERE current_version_id = ? AND deleted_at IS NOT NULL "
            "AND device_profile_id IS NOT NULL AND origin = 'draft' ORDER BY id LIMIT 1",
            (version_id,),
        )
        return self.to_model(BoardRow, row)

    async def clear_pending_if_unchanged(
        self, row_id: int, *, version_id: int, draft_id: int | None
    ) -> bool:
        """Clear what a pull has just recorded, unless the row moved on while it worked.

        A person may put a newer draft on the profile between the plan and this write; its
        pending draft and Set must survive, so the clear is conditional on the version and the
        draft the pull acted on.
        """
        cursor = await self.db.execute(
            "UPDATE profile_board SET pending_draft_id = NULL, pending_set_id = NULL, "
            "pending_major = NULL, updated_at = ? "
            "WHERE id = ? AND current_version_id = ? AND pending_draft_id IS ?",
            (utc_now(), row_id, version_id, draft_id),
        )
        return cursor.rowcount > 0

    async def find_live_by_version(self, version_id: int) -> BoardRow | None:
        """The live row whose current version is this one, if any."""
        row = await self.db.fetch_one(
            "SELECT * FROM profile_board WHERE current_version_id = ? AND deleted_at IS NULL "
            "ORDER BY id LIMIT 1",
            (version_id,),
        )
        return self.to_model(BoardRow, row)

    async def find_live_app_by_version(self, version_id: int) -> BoardRow | None:
        """The live row the app itself pushed whose current version is this one, if any."""
        row = await self.db.fetch_one(
            "SELECT * FROM profile_board WHERE current_version_id = ? AND deleted_at IS NULL "
            "AND origin = 'draft' ORDER BY id LIMIT 1",
            (version_id,),
        )
        return self.to_model(BoardRow, row)

    async def find_live_by_label(
        self, label: str, *, excluding: int | None = None
    ) -> BoardRow | None:
        """A live row with this label, if any (the first, for a stable answer).

        ``excluding`` leaves one row out: the row a new version continues is not a duplicate
        of itself.
        """
        row = await self.db.fetch_one(
            "SELECT * FROM profile_board WHERE label = ? AND deleted_at IS NULL "
            "AND id IS NOT ? ORDER BY id LIMIT 1",
            (label, excluding),
        )
        return self.to_model(BoardRow, row)

    async def find_live_by_name_key(
        self, label: str, *, excluding: int | None = None
    ) -> BoardRow | None:
        """A live row whose name is this one but for case and surrounding whitespace."""
        wanted = name_key(label)
        for row in await self.list_rows():
            if row.id != excluding and name_key(row.label) == wanted:
                return row
        return None

    async def find_live_by_device(self, device_id: str) -> BoardRow | None:
        """The live row that holds this machine file, if any."""
        row = await self.db.fetch_one(
            "SELECT * FROM profile_board WHERE device_profile_id = ? AND deleted_at IS NULL "
            "ORDER BY id LIMIT 1",
            (device_id,),
        )
        return self.to_model(BoardRow, row)

    async def other_live_on_device(self, device_id: str, *, excluding: int) -> BoardRow | None:
        """A live row other than ``excluding`` that stands on this machine file, if any."""
        row = await self.db.fetch_one(
            "SELECT * FROM profile_board WHERE device_profile_id = ? AND deleted_at IS NULL "
            "AND id != ? ORDER BY id LIMIT 1",
            (device_id, excluding),
        )
        return self.to_model(BoardRow, row)

    async def insert(self, write: BoardRowWrite) -> BoardRow:
        now = utc_now()
        cursor = await self.db.execute(
            """
            INSERT INTO profile_board
                (label, current_version_id, device_profile_id, device_version_id,
                 on_machine, on_home_screen, origin, pending_draft_id, pending_set_id,
                 pending_major, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                write.label,
                write.current_version_id,
                write.device_profile_id,
                write.device_version_id,
                int(write.on_machine),
                int(write.on_home_screen),
                write.origin,
                write.pending_draft_id,
                write.pending_set_id,
                None if write.pending_major is None else int(write.pending_major),
                now,
                now,
            ),
        )
        row = await self.get(int(cursor.lastrowid or 0))
        if row is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("board row vanished between write and read")
        # A profile's active version is always one of its versions.
        await self.add_version(row.id, write.current_version_id, write.version_source)
        return row

    async def update(self, row_id: int, patch: BoardRowPatch) -> BoardRow | None:
        """Write the fields ``patch`` names (including ones set to ``None``)."""
        values = {name: getattr(patch, name) for name in patch.model_fields_set}
        assignments = ["updated_at = ?"]
        params: list[object] = [utc_now()]
        for name, value in values.items():
            assignments.append(f"{name} = ?")
            params.append(int(value) if name in _BOOLEAN_COLUMNS and value is not None else value)
        await self.db.execute(
            f"UPDATE profile_board SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608 - column names come from the model's fields, values are bound
            [*params, row_id],
        )
        return await self.get(row_id)

    # ── a profile's versions ─────────────────────────────────────────

    async def add_version(
        self,
        board_id: int,
        version_id: int,
        source: VersionSource,
        *,
        added_at: str | None = None,
    ) -> bool:
        """Record that this profile has had this version. ``False`` when it already had it."""
        cursor = await self.db.execute(
            "INSERT OR IGNORE INTO profile_board_versions (board_id, version_id, added_at, source) "
            "VALUES (?, ?, ?, ?)",
            (board_id, version_id, added_at or utc_now(), source),
        )
        return cursor.rowcount > 0

    async def list_versions(self, board_id: int) -> list[BoardVersion]:
        """The profile's versions, newest first (the order the list shows them)."""
        rows = await self.db.fetch_all(
            "SELECT * FROM profile_board_versions WHERE board_id = ? "
            "ORDER BY added_at DESC, version_id DESC",
            (board_id,),
        )
        return self.to_models(BoardVersion, rows)

    async def get_version_entry(self, board_id: int, version_id: int) -> BoardVersion | None:
        row = await self.db.fetch_one(
            "SELECT * FROM profile_board_versions WHERE board_id = ? AND version_id = ?",
            (board_id, version_id),
        )
        return self.to_model(BoardVersion, row)

    async def find_live_by_listed_version(self, version_id: int) -> BoardRow | None:
        """The first live profile that has had this version, if any."""
        row = await self.db.fetch_one(
            "SELECT b.* FROM profile_board b JOIN profile_board_versions v ON v.board_id = b.id "
            "WHERE v.version_id = ? AND b.deleted_at IS NULL ORDER BY b.id LIMIT 1",
            (version_id,),
        )
        return self.to_model(BoardRow, row)

    async def shot_counts(self, version_ids: list[int]) -> dict[int, int]:
        """How many shots resolve to each of these versions (versions with none are absent)."""
        if not version_ids:
            return {}
        marks = ", ".join("?" * len(version_ids))
        rows = await self.db.fetch_all(
            "SELECT profile_version_id AS v, COUNT(*) AS n FROM shots "  # noqa: S608 - placeholders only
            f"WHERE profile_version_id IN ({marks}) GROUP BY profile_version_id",
            version_ids,
        )
        return {int(r["v"]): int(r["n"]) for r in rows}

    async def listed_hashes(self, board_id: int) -> set[str]:
        """The content hashes of the versions this profile has had, but not the ones only found on
        the machine's file (``edited_on_machine``): those are what a conflict is about, and
        recording one must not make the conflict go away."""
        rows = await self.db.fetch_all(
            "SELECT pv.content_hash AS h FROM profile_board_versions v "
            "JOIN profile_versions pv ON pv.id = v.version_id "
            "WHERE v.board_id = ? AND v.source != 'edited_on_machine'",
            (board_id,),
        )
        return {str(r["h"]) for r in rows}

    async def live_version_hashes(self) -> dict[str, list[int]]:
        """Content hash -> the live profiles (by id) that have had a version with it."""
        rows = await self.db.fetch_all(
            "SELECT pv.content_hash AS h, v.board_id AS b FROM profile_board_versions v "
            "JOIN profile_board b ON b.id = v.board_id AND b.deleted_at IS NULL "
            "JOIN profile_versions pv ON pv.id = v.version_id ORDER BY v.board_id"
        )
        found: dict[str, list[int]] = {}
        for row in rows:
            found.setdefault(str(row["h"]), []).append(int(row["b"]))
        return found

    # ── adoption ─────────────────────────────────────────────────────

    async def adoption(self) -> BoardAdoption | None:
        row = await self.db.fetch_one("SELECT * FROM profile_board_adoption WHERE id = 1")
        return self.to_model(BoardAdoption, row)

    async def pause(self, reason: str) -> None:
        await self.db.execute(
            "UPDATE profile_board_adoption SET paused_at = COALESCE(paused_at, ?), "
            "paused_reason = COALESCE(paused_reason, ?) WHERE id = 1",
            (utc_now(), reason),
        )

    async def resume(self) -> None:
        await self.db.execute(
            "UPDATE profile_board_adoption SET paused_at = NULL, paused_reason = NULL, "
            "resume_pending = 1 WHERE id = 1 AND paused_at IS NOT NULL"
        )

    async def settle_resume(self) -> None:
        """The write phase has run since the person resumed: judge resets again from now on."""
        await self.db.execute("UPDATE profile_board_adoption SET resume_pending = 0 WHERE id = 1")

    async def mark_adopted(self, host: str) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO profile_board_adoption (id, adopted_at, host) VALUES (1, ?, ?)",
            (utc_now(), host),
        )

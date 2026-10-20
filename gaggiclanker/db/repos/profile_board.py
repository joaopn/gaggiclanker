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

from pydantic import BaseModel, ConfigDict, model_validator

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
    #: The name of the document recorded for the file: what the file was called when the app
    #: recorded what it holds (a push: the profile's name; a recording of the file as it is: the
    #: file's own). With the recorded version's brew it is "what the app last saw this file
    #: hold", which is what a file read now is compared with. ``None`` exactly when
    #: ``device_version_id`` is.
    device_label: str | None = None
    #: Whether a sync should put the profile on the machine (or take it off). Independent of
    #: ``on_home_screen``, which is shown as "Starred" and only applies while it is on.
    on_machine: bool = True
    on_home_screen: bool = True
    origin: BoardOrigin
    failed_version_id: int | None = None
    pending_draft_id: int | None = None
    #: The version this profile was before its newest one, which going back returns to.
    previous_version_id: int | None = None
    #: The content hash of a machine file a person chose to overrule in a conflict ("keep the
    #: app's version"): that same content is not flagged again, a further edit is a new conflict.
    conflict_overruled_hash: str | None = None
    #: Set by going back: the version that was left (the file holding it goes as a going
    #: back, not as a delayed replacement)...
    back_from_version_id: int | None = None
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
    #: Required with ``device_version_id`` and absent without it.
    device_label: str | None = None
    on_machine: bool = True
    on_home_screen: bool = True
    origin: BoardOrigin
    pending_draft_id: int | None = None
    #: Where the row's first version came from; it is recorded in the profile's version list.
    version_source: VersionSource = "machine"

    @model_validator(mode="after")
    def _recorded_name_goes_with_the_recorded_version(self) -> BoardRowWrite:
        if (self.device_version_id is None) != (self.device_label is None):
            raise ValueError("device_label is written with device_version_id, and only with it")
        return self


class BoardRowPatch(BaseModel):
    """The fields of a row a caller changes; only the ones it names are written."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    current_version_id: int | None = None
    device_profile_id: str | None = None
    device_version_id: int | None = None
    #: The name of the document recorded, written with ``device_version_id`` (see the write
    #: model); cleared with it.
    device_label: str | None = None
    on_machine: bool | None = None
    on_home_screen: bool | None = None
    origin: BoardOrigin | None = None
    failed_version_id: int | None = None
    pending_draft_id: int | None = None
    previous_version_id: int | None = None
    conflict_overruled_hash: str | None = None
    back_from_version_id: int | None = None
    deleted_at: str | None = None

    @model_validator(mode="after")
    def _recorded_name_goes_with_the_recorded_version(self) -> BoardRowPatch:
        """What is recorded for a file is recorded with the name of the document, by the writer
        that knows it; clearing the version clears the name."""
        if "device_version_id" in self.model_fields_set:
            if self.device_version_id is None:
                if self.device_label is not None:
                    raise ValueError(
                        "device_label cannot be set while device_version_id is cleared"
                    )
                object.__setattr__(self, "device_label", None)
                self.model_fields_set.add("device_label")
            elif self.device_label is None:
                raise ValueError("device_version_id is written with the name of the document")
        elif "device_label" in self.model_fields_set:
            raise ValueError("device_label is written with device_version_id, and only with it")
        return self


#: The columns of a profile-list row that name one of its versions. A row only names versions it
#: owns.
_VERSION_COLUMNS = (
    "current_version_id",
    "device_version_id",
    "failed_version_id",
    "previous_version_id",
    "back_from_version_id",
)

#: Columns a patch may name that hold a boolean, stored as 0/1.
_BOOLEAN_COLUMNS = frozenset({"on_machine", "on_home_screen"})


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

    async def find_deleted_with_file(self, content_hash: str, name: str) -> BoardRow | None:
        """A deleted app row called ``name`` whose active version has this brew and whose
        machine file the pull has not dealt with.

        Putting the same profile back on the board while its old file is still waiting
        (a Recipe may be brewing it) revives that row instead of pushing a second identical copy.
        It is the same profile only under the same name: a new profile that brews what a
        deleted one brewed is a profile of its own.
        """
        row = await self.db.fetch_one(
            "SELECT b.* FROM profile_board b "
            "JOIN profile_versions cv ON cv.id = b.current_version_id "
            "WHERE cv.content_hash = ? AND b.label = ? AND b.deleted_at IS NOT NULL "
            "AND b.device_profile_id IS NOT NULL AND b.origin = 'draft' ORDER BY b.id LIMIT 1",
            (content_hash, name),
        )
        return self.to_model(BoardRow, row)

    async def clear_pending_if_unchanged(
        self, row_id: int, *, version_id: int, draft_id: int | None
    ) -> bool:
        """Clear what a pull has just marked pushed, unless the row moved on while it worked.

        A person may put a newer draft on the profile between the plan and this write; its
        pending draft must survive, so the clear is conditional on the version and the draft
        the pull acted on.
        """
        cursor = await self.db.execute(
            "UPDATE profile_board SET pending_draft_id = NULL, updated_at = ? "
            "WHERE id = ? AND current_version_id = ? AND pending_draft_id IS ?",
            (utc_now(), row_id, version_id, draft_id),
        )
        return cursor.rowcount > 0

    async def find_live_by_version(
        self, version_id: int, *, named: str | None = None
    ) -> BoardRow | None:
        """The live row whose current version is this one, if any.

        ``named`` keeps only a row with exactly that name: a version is what a profile brews, so
        a draft's version can be another profile's (two new profiles with one brew share the
        version until each lands), and "the draft is on the board" means the profile it is called.
        """
        row = await self.db.fetch_one(
            "SELECT * FROM profile_board WHERE current_version_id = ? AND deleted_at IS NULL "
            "AND (? IS NULL OR label = ?) ORDER BY id LIMIT 1",
            (version_id, named, named),
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
                (label, current_version_id, device_profile_id, device_version_id, device_label,
                 on_machine, on_home_screen, origin, pending_draft_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                write.label,
                write.current_version_id,
                write.device_profile_id,
                write.device_version_id,
                write.device_label,
                int(write.on_machine),
                int(write.on_home_screen),
                write.origin,
                write.pending_draft_id,
                now,
                now,
            ),
        )
        row_id = int(cursor.lastrowid or 0)
        # A profile's active version is always one of its versions, and a version it lists is its
        # own: if the one given belongs to another profile the row gets a copy and names that.
        await self.add_version(row_id, write.current_version_id, write.version_source)
        row = await self.get(row_id)
        if row is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("board row vanished between write and read")
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

    async def version_for(self, row_id: int, version_id: int) -> int:
        """The version of this profile that stands for ``version_id``: the one place a version is
        attached to a profile.

        * on no profile yet: the profile takes it over (unless it already has a version of
          that brew, which is then the answer and the ownerless one is left alone), and with it
          the one Recipe with no profile that brews it, when the profile has no Recipe; if it has
          one, the version stays the Recipe's and the profile gets a copy;
        * already this profile's: itself;
        * another profile's: a profile never takes another's version, so it gets its own copy,
          carrying the signature the way a new version of a profile does. A profile that already
          has a version of that brew uses it.

        Whatever the answer is, the profile's own columns that named ``version_id`` name it.
        Every path that lists a version on a profile goes through here (:meth:`add_version`).
        """
        found = await self.db.fetch_one(
            "SELECT id, board_id, content_hash FROM profile_versions WHERE id = ?", (version_id,)
        )
        if found is None:
            raise RuntimeError(f"profile version {version_id} is missing")
        if found["board_id"] == row_id:
            return version_id
        mine = await self.db.fetch_value(
            "SELECT id FROM profile_versions WHERE board_id = ? AND content_hash = ?",
            (row_id, found["content_hash"]),
        )
        if found["board_id"] is None and mine is None:
            takes, taker = await self._taken_over_with(row_id, version_id)
            if takes:
                await self.db.execute(
                    "UPDATE profile_versions SET board_id = ? WHERE id = ?", (row_id, version_id)
                )
                if taker is not None:
                    # The Recipe that brewed this version while no profile had it is this profile's.
                    await self.db.execute(
                        "UPDATE recipes SET board_id = ? WHERE id = ?", (row_id, taker)
                    )
                    await self.settle_a_recipes_versions(row_id, taker)
                return version_id
            # Otherwise the version stays with the Recipe that names it, and the profile gets its
            # own.
        carries = False
        if mine is not None:
            effective = int(mine)
            # What a draft stored with its still ownerless version (the agent's expectations)
            # follows it onto the profile's own version of that brew. Another profile's version
            # has its own signature, which this profile's version does not take.
            carries = found["board_id"] is None
        else:
            cursor = await self.db.execute(
                "INSERT INTO profile_versions "
                "(content_hash, board_id, label, type, utility, json, device_json, source, "
                " created_at) "
                "SELECT content_hash, ?, label, type, utility, json, device_json, source, "
                " created_at FROM profile_versions WHERE id = ?",
                (row_id, version_id),
            )
            effective = int(cursor.lastrowid or 0)
            carries = True
        if carries:
            # Imported here: the signature service reads this module's neighbours.
            from gaggiclanker.signatures.service import SignatureService

            await SignatureService(self.db).carry_quietly(version_id, effective)
        for column in _VERSION_COLUMNS:
            await self.db.execute(
                f"UPDATE profile_board SET {column} = ? WHERE id = ? AND {column} = ?",  # noqa: S608 - constant column names
                (effective, row_id, version_id),
            )
        return effective

    async def settle_a_recipes_versions(
        self, row_id: int, set_id: int, *, follow: bool = True
    ) -> None:
        """A Recipe that has just become this profile's: every other version no profile owns that
        its versions name goes through :meth:`version_for` like any other (taken over, or the
        profile gets a copy of its own that the Recipe's versions then name), so all of the Recipe's
        versions are the profile's."""
        loose = await self.db.fetch_all(
            "SELECT DISTINCT v.profile_version_id AS id FROM recipe_versions v "
            "JOIN profile_versions pv ON pv.id = v.profile_version_id "
            "WHERE v.recipe_id = ? AND pv.board_id IS NULL ORDER BY v.profile_version_id",
            (set_id,),
        )
        for entry in loose:
            old = int(entry["id"])
            mine = await self.version_for(row_id, old)
            if mine != old:
                await self.db.execute(
                    "UPDATE recipe_versions SET profile_version_id = ? "
                    "WHERE recipe_id = ? AND profile_version_id = ?",
                    (mine, set_id, old),
                )
            await self.add_version(row_id, mine, "edit")
        if not follow:
            return
        # The Recipe brews what its profile has active: a version taken over while the profile had
        # another active (one it listed without making active) is followed from.
        active = await self.db.fetch_value(
            "SELECT current_version_id FROM profile_board WHERE id = ?", (row_id,)
        )
        if active is not None:
            # Imported here: the Recipes repository reads this one's neighbours.
            from gaggiclanker.db.repos.recipes import RecipesRepository

            await RecipesRepository(self.db).follow_profile(row_id, int(active))

    async def _taken_over_with(self, row_id: int, version_id: int) -> tuple[bool, int | None]:
        """Whether this profile may take over the ownerless version, and the Recipe that goes with
        it. A version no Recipe names is simply taken. One a Recipe with no profile names (the first
        recipe a design was filled with, an authored starting point, a file a Recipe started on
        while the list had none) is taken together with that Recipe, when the profile has no other
        Recipe and the Recipe brews no version another profile owns, and only through the Recipe's
        current version. Anything else leaves the version with the Recipe and the profile gets a
        copy of its own."""
        named = await self.db.fetch_all(
            "SELECT DISTINCT recipe_id FROM recipe_versions WHERE profile_version_id = ?",
            (version_id,),
        )
        if not named:
            return True, None
        if len(named) > 1:
            return False, None
        set_id = int(named[0]["set_id"])
        owned = await self.db.fetch_value("SELECT board_id FROM recipes WHERE id = ?", (set_id,))
        busy = await self.db.fetch_value(
            "SELECT 1 FROM recipes WHERE board_id = ? AND id != ?", (row_id, set_id)
        )
        elsewhere = await self.db.fetch_value(
            "SELECT 1 FROM recipe_versions v JOIN profile_versions pv ON pv.id = "
            "v.profile_version_id "
            "WHERE v.recipe_id = ? AND pv.board_id IS NOT NULL AND pv.board_id != ? LIMIT 1",
            (set_id, row_id),
        )
        if busy or elsewhere or (owned is not None and owned != row_id):
            return False, None
        if owned is not None:
            return True, None
        # A Recipe with no profile is taken only through its current version, as its profile is
        # decided everywhere else: a profile that takes over a version the Recipe named earlier gets
        # a copy of its own, and the version stays the Recipe's.
        current = await self.db.fetch_value(
            "SELECT v.profile_version_id FROM recipes s JOIN recipe_versions v "
            "ON v.id = s.current_version_id WHERE s.id = ?",
            (set_id,),
        )
        if current != version_id:
            return False, None
        return True, set_id

    async def add_version(
        self,
        board_id: int,
        version_id: int,
        source: VersionSource,
        *,
        added_at: str | None = None,
    ) -> bool:
        """Record that this profile has had this version (its own version of it, see
        :meth:`version_for`; callers that go on to use the version ask for it first). ``False``
        when it already had it."""
        effective = await self.version_for(board_id, version_id)
        cursor = await self.db.execute(
            "INSERT OR IGNORE INTO profile_board_versions (board_id, version_id, added_at, source) "
            "VALUES (?, ?, ?, ?)",
            (board_id, effective, added_at or utc_now(), source),
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

    async def find_live_by_listed_version(
        self, version_id: int, *, named: str | None = None
    ) -> BoardRow | None:
        """The first live profile that has had this version, if any (``named``: and is called
        exactly that, see :meth:`find_live_by_version`)."""
        row = await self.db.fetch_one(
            "SELECT b.* FROM profile_board b JOIN profile_board_versions v ON v.board_id = b.id "
            "WHERE v.version_id = ? AND b.deleted_at IS NULL AND (? IS NULL OR b.label = ?) "
            "ORDER BY b.id LIMIT 1",
            (version_id, named, named),
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

    async def listed_brews(self, board_id: int) -> set[str]:
        """The brew hashes of the versions this profile has had, but not the ones only found on
        the machine's file (``edited_on_machine``): those are what a conflict is about, and
        recording one must not make the conflict go away."""
        rows = await self.db.fetch_all(
            "SELECT pv.content_hash AS h FROM profile_board_versions v "
            "JOIN profile_versions pv ON pv.id = v.version_id "
            "WHERE v.board_id = ? AND v.source != 'edited_on_machine'",
            (board_id,),
        )
        return {str(r["h"]) for r in rows}

    async def live_version_brews(self) -> dict[str, list[int]]:
        """Brew hash -> the live profiles (by id) that have had a version with it."""
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

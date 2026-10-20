"""`profile_versions` and `device_profiles` — the mirror of `/p/` on the machine.

Two tables because a profile has two independent identities. What it *brews* is
the canonical JSON without its name and that JSON's sha256, and that is immutable: edit a
phase and you have a new version, not a changed one. Where it *lives* is the device id, and
that is mutable — it gains and loses the favourite star, moves position, is
selected, is deleted. Putting the second lot in the hash would make every
profile appear to change the moment somebody starred it. The name is neither: it belongs to
the profile (``profile_board.label``), and a document that carries it is built by
:func:`document_for`.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonObject, utc_now
from gaggiclanker.db.repository import Repository, row_to_dict
from gaggiclanker.domain.models import Profile, brew_hash, canonical_document_json
from gaggiclanker.domain.profile_recipe import profile_recipe

__all__ = [
    "SYNTHETIC_BASE_DESCRIPTION",
    "SYNTHETIC_BASE_LABEL",
    "DeviceProfileRow",
    "DeviceProfileSummary",
    "ProfileVersionPage",
    "ProfileVersionRow",
    "ProfileVersionSummary",
    "ProfilesRepository",
    "VersionPin",
    "document_for",
    "not_synthetic_sql",
    "stored_document",
    "stored_document_json",
    "version_name_sql",
]


#: The label of the synthetic base a draft written from zero is diffed against:
#: a Recipe designed with no profile to fork, or any authored draft when the
#: archive holds no profile at all. See :meth:`ProfilesRepository.empty_base`.
SYNTHETIC_BASE_LABEL = "Empty baseline"

#: Its description, word for word: the document is content-hashed, so these words are what
#: makes the stored row *the* synthetic base rather than a person's profile with the same label.
SYNTHETIC_BASE_DESCRIPTION = (
    "An empty baseline, created because the archive held no profile to diff a new draft against."
)


def not_synthetic_sql(alias: str) -> str:
    """A WHERE term that leaves out the synthetic base (by its label and its own description)."""
    return (
        f"NOT ({alias}.label = '{SYNTHETIC_BASE_LABEL}' "
        f"AND json_extract({alias}.json, '$.description') = '{SYNTHETIC_BASE_DESCRIPTION}')"
    )


def version_name_sql(alias: str) -> str:
    """The name to show for the profile version ``alias`` is: a SQL expression.

    A version belongs to a profile and the profile's name is ``profile_board.label``; a version
    that belongs to none yet is called what its draft calls it, else what the machine file that
    holds it is called, else the name its document had when it was first stored. Every reader
    that shows a version's name asks this (or :meth:`ProfilesRepository.name_of`, which is the
    same rule), so the stored label is never read for a name unless nothing else names it.
    """
    return (
        f"COALESCE((SELECT b.label FROM profile_board b WHERE b.id = {alias}.board_id), "  # noqa: S608 - the alias is a constant chosen by the caller
        f"(SELECT d.label FROM profile_drafts d "
        f"WHERE d.draft_version_id = {alias}.id AND d.label <> '' ORDER BY d.id DESC LIMIT 1), "
        # A live file's name first (no ORDER BY: it would put a sort into every list query).
        f"(SELECT f.label FROM device_profiles f "
        f"WHERE f.current_version_id = {alias}.id AND f.label <> '' AND f.deleted_at IS NULL "
        f"LIMIT 1), "
        f"(SELECT f.label FROM device_profiles f "
        f"WHERE f.current_version_id = {alias}.id AND f.label <> '' LIMIT 1), "
        f"{alias}.label)"
    )


def document_for(version: ProfileVersionRow, name: str) -> Profile:
    """The profile document of ``version`` carrying ``name``: the one place a document is built
    from a stored version.

    A version is what its profile brews; the name is the profile's. Every document bound for
    the machine, the editor, an export or the agent is this, with the owner's current name (a
    new profile's draft: the draft's), because the label inside the stored document is only the
    name it had when it was first stored.
    """
    if not version.profile:  # pragma: no cover - the column is NOT NULL
        raise ValueError(f"profile version {version.id} has no document")
    return Profile.model_validate({**version.profile, "label": name})


def stored_document(version: ProfileVersionRow, name: str) -> dict[str, Any]:
    """The version's stored canonical document under ``name``: the `json` column, decoded, with
    the label the caller names and nothing else added.

    What the chat is shown and what `draft_profile` merges its patch into, so a document the
    agent copies from what it read is the document a draft starts from. It is the canonical
    form (keys sorted, device-owned fields and firmware defaults absent), not a re-rendering
    in the machine's shape: no `favorite`, no `selected`, no `temperature: 0` the author never
    wrote. Built from :func:`document_for`'s inputs: same version, same name.
    """
    return {**(version.profile or {}), "label": name}


def stored_document_json(version: ProfileVersionRow, name: str) -> str:
    """:func:`stored_document` as compact JSON."""
    return json.dumps(stored_document(version, name), separators=(",", ":"), ensure_ascii=False)


class ProfileVersionRow(BaseModel):
    """One immutable profile version, identified by its content hash."""

    model_config = ConfigDict(extra="forbid")

    id: int
    content_hash: str
    #: The profile this version belongs to, or ``None`` while it is on none.
    board_id: int | None = None
    #: The name the document had when this version was first stored. The profile's name is
    #: ``profile_board.label`` of ``board_id`` (a version is what the profile brews).
    label: str
    type: str
    utility: bool = False
    #: The canonical profile document, decoded. Named `profile` on the model and
    #: `json` in the column: `json` is already a (deprecated) method on every
    #: pydantic model and a field that shadows it is a mypy error waiting to
    #: happen.
    profile: JsonObject = Field(validation_alias="json")
    #: The last raw document the device served for this version, decoded. Kept
    #: for reference: it carries the fields the canonical form drops.
    device_profile: JsonObject = Field(default=None, validation_alias="device_json")
    source: str = "device"
    created_at: str


class VersionPin(BaseModel):
    """The pin a profile version is the copy of."""

    model_config = ConfigDict(extra="forbid")

    pin_id: int
    set_id: int
    set_version_id: int


class VersionSet(BaseModel):
    """The Set a profile has (a profile has at most one, archived or not)."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str


class ProfileVersionSummary(BaseModel):
    """A version as a table row: what it is, where it came from, who uses it.

    Deliberately without the document. `profile` and `device_profile` are a few
    kilobytes each and a page of fifty of them is a payload nobody reads — the
    list says which versions exist, `/api/profile-versions/{id}` says what one
    contains.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    content_hash: str
    #: The name this version's document had when it was first stored: history, not the
    #: profile's name (that is ``profile_name``).
    label: str
    #: What the profile this version belongs to is called now (``profile_board.label``); for a
    #: version that belongs to none, what its draft or its machine file calls it, else
    #: ``label``. What a picker shows.
    profile_name: str
    type: str
    utility: bool = False
    #: `device` (mirrored off the machine), `import` (a file someone loaded), or
    #: `draft` (this box authored it). A drafted version becomes a
    #: mirrored one the moment the board's write phase puts it on the machine —
    #: the hash is the same document, so it keeps the source it was created with.
    source: str = "device"
    created_at: str
    #: Whether some device profile currently points at this version. An imported
    #: version is normally False; a mirrored one that was later edited on the
    #: machine becomes False too, which is the honest answer — it is history.
    mirrored: bool = False
    #: How many shots resolve to this exact version.
    shot_count: int = 0
    #: The brew temperature and the weight in the cup the document states, or
    #: ``None`` where it does not (see :mod:`gaggiclanker.domain.profile_recipe`).
    #: Carried on the row because the New Recipe form fills its recipe from the
    #: version a person picks, and fetching every document to read two numbers
    #: out of it is the payload this summary exists to avoid.
    temperature_c: float | None = None
    target_yield_g: float | None = None
    #: The profile the version belongs to (``None``: an imported file or a draft no profile has
    #: taken yet), whether it is that profile's active version, and the profile's Recipe. A new
    #: Recipe takes a profile no Recipe has, by the version that profile has active.
    board_id: int | None = None
    is_active: bool = False
    set: VersionSet | None = None
    #: Set when this version belongs to a pin's copy (the copy, live or since unpinned, or what
    #: the machine's file came to hold): the pin, its Recipe and the Recipe version it belongs to. A
    #: recipe picker leaves such a version out, because no Recipe brews a pin's profile; ``None``
    #: for every other version.
    pin: VersionPin | None = None


class ProfileVersionPage(BaseModel):
    """One page of versions, with the size of the unfiltered-by-page total."""

    model_config = ConfigDict(extra="forbid")

    items: list[ProfileVersionSummary]
    total: int


class DeviceProfileRow(BaseModel):
    """Which version a device id currently holds, and its NVS-side state."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    current_version_id: int
    favorite: bool = False
    selected: bool = False
    position: int | None = None
    first_seen_at: str
    last_seen_at: str
    deleted_at: str | None = None
    #: The name the file had when the sync last read it.
    label: str = ""


class DeviceProfileSummary(BaseModel):
    """A device profile joined to its current version — what `GET /api/profiles` returns. Its
    ``label`` is the name the machine's file has (the mirror stores it with the file)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    current_version_id: int
    favorite: bool = False
    selected: bool = False
    position: int | None = None
    first_seen_at: str
    last_seen_at: str
    deleted_at: str | None = None
    label: str
    type: str
    utility: bool = False
    content_hash: str
    shot_count: int = 0


class ProfilesRepository(Repository):
    """Reads and writes the profile mirror."""

    # ── versions ─────────────────────────────────────────────────────

    async def ensure_version(
        self,
        profile: Profile,
        *,
        owner: int | None,
        source: str = "device",
        device_json: str | None = None,
    ) -> tuple[ProfileVersionRow, bool]:
        """Return the version row for this profile under ``owner``, inserting it if it is new.

        A version belongs to one profile (``owner``, a profile-list row id) and is found or made
        by that profile and its content; ``owner=None`` is a version that is on no profile yet (a
        draft for a new profile, an imported file, a machine file that has not joined the list,
        the empty baseline), found or made among the versions that have none. Every caller says
        which: there is no default, because a version stored under the wrong owner is a version
        another profile can end up listing. The profile that lists an ownerless version takes it
        over (:meth:`ProfileBoardRepository.version_for`); a profile never takes another
        profile's version, it gets its own copy.

        The second element says whether it was inserted, which is what the sync
        engine reports as "a profile changed" — a profile the machine serves
        every fifteen minutes must not look like news every fifteen minutes.

        A version of a profile is what it brews, found by the brew hash, which leaves the name
        out: the same brew under another name is the same version. The name is the profile's
        (``profile_board.label``); the label stored with a version is only the name its document
        had when it was first stored, and nothing may read it as the profile's name
        (:func:`document_for` builds the document with the profile's). A version on **no**
        profile has no profile name, so it stays the document it came as: found by its brew and
        the label it is stored under.
        """
        content_hash = brew_hash(profile)
        existing = await self.find_version(
            content_hash, owner=owner, label=profile.label if owner is None else None
        )
        if existing is not None:
            if device_json is not None and json.loads(device_json) != existing.device_profile:
                # Same profile, textually different device document (a firmware
                # that started emitting a new key). Keep the newest for
                # reference; the version itself has not changed.
                await self.db.execute(
                    "UPDATE profile_versions SET device_json = ? WHERE id = ?",
                    (device_json, existing.id),
                )
            return existing, False

        cursor = await self.db.execute(
            """
            INSERT INTO profile_versions
                (content_hash, board_id, label, type, utility, json, device_json, source,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_hash,
                owner,
                profile.label,
                profile.type,
                int(profile.utility),
                canonical_document_json(profile),
                device_json,
                source,
                utc_now(),
            ),
        )
        version = await self.get_version(int(cursor.lastrowid or 0))
        if version is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("profile version vanished between write and read")
        return version, True

    async def version_for_file(
        self, profile: Profile, device_id: str, *, device_json: str | None = None
    ) -> tuple[ProfileVersionRow, bool]:
        """The version a machine file holds, as the mirror stores it: ``(version, inserted)``.

        A file a profile stands on holds that profile's version of its content. A file no
        profile stands on (one put back after a reset while the list was paused, one made on the
        display, a second file with a profile's content) holds the stored version with that content
        when exactly one profile owns one **and is called what the file is called**, so nothing is
        inserted and nothing is news; otherwise it holds the version on no profile that is its
        brew under its own name (a copy made on the display under another name is not that
        profile's version: it is the document it came as).
        """
        owner = await self.standing_profile(device_id)
        if owner is None:
            content_hash = brew_hash(profile)
            owned = await self.db.fetch_all(
                "SELECT pv.* FROM profile_versions pv JOIN profile_board b ON b.id = pv.board_id "
                "WHERE pv.content_hash = ? AND b.label = ?",
                (content_hash, profile.label),
            )
            if len(owned) == 1:
                return ProfileVersionRow.model_validate(dict(owned[0])), False
        return await self.ensure_version(profile, owner=owner, device_json=device_json)

    async def find_version(
        self, content_hash: str, *, owner: int | None, label: str | None = None
    ) -> ProfileVersionRow | None:
        """The version with this brew that belongs to ``owner``; for ``owner=None`` (a version on
        no profile) the one stored under ``label`` as well, since those are unique by both."""
        row = await self.db.fetch_one(
            "SELECT * FROM profile_versions WHERE content_hash = ? AND board_id IS ? "
            "AND (? IS NULL OR label = ?)",
            (content_hash, owner, label, label),
        )
        return self.to_model(ProfileVersionRow, row)

    async def get_version(self, version_id: int) -> ProfileVersionRow | None:
        row = await self.db.fetch_one("SELECT * FROM profile_versions WHERE id = ?", (version_id,))
        return self.to_model(ProfileVersionRow, row)

    async def default_draft_base(self) -> int:
        """The version a profile that was authored rather than edited is diffed against.

        A draft is always *derived from* a version, because the diff view is how
        somebody reads it before approving. A starting-point option a model wrote
        from scratch is derived from nothing, so the base is the closest thing
        available: the most-used brew profile in the library, which is what
        "what you brew now" means. Utility profiles never: a backflush is
        nobody's baseline. When the library is empty it is :meth:`empty_base`.
        """
        best = await self.db.fetch_value(
            f"""
            SELECT pv.id FROM profile_versions pv
             WHERE pv.utility = 0 AND {not_synthetic_sql("pv")}
             ORDER BY (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = pv.id) DESC,
                      pv.id DESC
             LIMIT 1
            """  # noqa: S608 - the one interpolation is a constant fragment
        )
        if best is not None:
            return int(best)
        return await self.empty_base()

    async def empty_base(self) -> int:
        """The synthetic minimal profile a draft written from zero is diffed against.

        Stored as a version like any other: a real row rather than a special
        case in the diff view, and it costs one profile nobody selects. It is
        only ever the *diff's* other side — no draft document is built from it,
        so what it contains never reaches a profile somebody brews. Its
        description predates designing from zero and is kept word for word: the
        document is content-hashed, and new words would store a second baseline
        beside the one an archive may already have.
        """
        version, _ = await self.ensure_version(
            Profile.model_validate(
                {
                    "label": SYNTHETIC_BASE_LABEL,
                    "type": "pro",
                    "description": SYNTHETIC_BASE_DESCRIPTION,
                    "temperature": 93.0,
                    "phases": [
                        {
                            "name": "Extraction",
                            "phase": "brew",
                            "valve": 1,
                            "duration": 30,
                            "pump": {"target": "pressure", "pressure": 9, "flow": 0},
                            "targets": [{"type": "volumetric", "operator": "gte", "value": 36}],
                        }
                    ],
                }
            ),
            owner=None,
            source="draft",
        )
        return version.id

    async def find_version_of_profile_named(
        self, content_hash: str, name: str
    ) -> ProfileVersionRow | None:
        """A version with this brew that the live profile called exactly ``name`` has, if there is
        one: whether a file with that name and brew is already a profile's (the importer's skip).
        The same brew under another name is not: it is a profile of its own."""
        row = await self.db.fetch_one(
            "SELECT pv.* FROM profile_board b "
            "JOIN profile_versions pv ON pv.board_id = b.id "
            "WHERE b.label = ? AND b.deleted_at IS NULL AND pv.content_hash = ? "
            "ORDER BY pv.id LIMIT 1",
            (name, content_hash),
        )
        return self.to_model(ProfileVersionRow, row)

    async def served(self, version: ProfileVersionRow) -> ProfileVersionRow:
        """The version as people and clients are shown it: its ``label`` and its ``profile``
        under the name of the profile it belongs to (see :meth:`name_of`), not the name the
        document happened to be first stored with."""
        name = await self.name_of(version)
        return version.model_copy(update={"label": name, "profile": stored_document(version, name)})

    async def name_of(self, version: ProfileVersionRow) -> str:
        """The name to show for this version (see :func:`version_name_sql`)."""
        found = await self.db.fetch_value(
            f"SELECT {version_name_sql('pv')} FROM profile_versions pv WHERE pv.id = ?",  # noqa: S608 - constant fragment
            (version.id,),
        )
        return str(found) if found is not None else version.label

    async def find_version_by_label(self, label: str) -> ProfileVersionRow | None:
        """The version a name stands for.

        The importer's fallback link: a shot export names its profile but the `profileId` it
        carries is usually long gone from the machine, so the name is the only handle left. The
        profile list's own name comes first (that profile's active version; a live profile before
        a deleted one, the newest of those of one name); with no such profile (the list has not
        adopted the machine, as it does not with Writes off) the newest stored version whose
        label is the name, which until a version stops carrying its name is what it was called.
        An exact, case-sensitive match on purpose: "9 Bar" and "9 bar" are two profiles as far
        as the machine is concerned.
        """
        if not label:
            return None
        row = await self.db.fetch_one(
            "SELECT pv.* FROM profile_board b JOIN profile_versions pv "
            "ON pv.id = b.current_version_id WHERE b.label = ? "
            "ORDER BY (b.deleted_at IS NOT NULL), b.id DESC LIMIT 1",
            (label,),
        )
        if row is None:
            row = await self.db.fetch_one(
                "SELECT * FROM profile_versions WHERE label = ? ORDER BY id DESC LIMIT 1", (label,)
            )
        return self.to_model(ProfileVersionRow, row)

    async def list_versions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        source: str | None = None,
    ) -> ProfileVersionPage:
        """Every version, newest first, whether or not the machine still has it.

        This is the other half of `/api/profiles`: that one lists what is *on*
        the machine, this one lists everything the archive can resolve a shot
        to — including versions imported from a file, which no device profile
        points at and which would otherwise be invisible.
        """
        # The synthetic base is a diff's other side, never a profile anybody has.
        where = [not_synthetic_sql("v")]
        params: list[object] = []
        if source is not None:
            where.append("v.source = ?")
            params.append(source)
        clause = " AND ".join(where)
        total_row = await self.db.fetch_one(
            f"SELECT COUNT(*) AS n FROM profile_versions v WHERE {clause}",  # noqa: S608
            params,
        )
        rows = await self.db.fetch_all(
            f"""
            SELECT v.id, v.content_hash, v.label, {version_name_sql("v")} AS profile_name,
                   v.type, v.utility, v.source, v.created_at,
                   v.json AS document,
                   EXISTS (SELECT 1 FROM device_profiles d
                            WHERE d.current_version_id = v.id
                              AND d.deleted_at IS NULL) AS mirrored,
                   (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = v.id) AS shot_count,
                   v.board_id,
                   EXISTS (SELECT 1 FROM profile_board b
                            WHERE b.id = v.board_id AND b.current_version_id = v.id
                              AND b.deleted_at IS NULL) AS is_active,
                   ps.id AS set_id, ps.name AS set_name,
                   p.id AS pin_id, pv.recipe_id AS pin_recipe_id,
                   p.recipe_version_id AS pin_recipe_version_id
            FROM profile_versions v
            LEFT JOIN recipes ps ON ps.board_id = v.board_id
            LEFT JOIN recipe_version_pins p ON p.profile_version_id = v.id
            LEFT JOIN recipe_versions pv ON pv.id = p.recipe_version_id
            WHERE {clause}
            ORDER BY v.id DESC
            LIMIT ? OFFSET ?
            """,  # noqa: S608 - the WHERE clauses above are literals, values are bound
            [*params, limit, offset],
        )
        return ProfileVersionPage(
            items=[_summary(row_to_dict(row)) for row in rows],
            total=int(total_row["n"]) if total_row is not None else 0,
        )

    async def pin_copy_ids(self) -> list[int]:
        """Every profile version that is the copy of a pin, live or since unpinned: none of them
        may be named by a Recipe version, so none is offered to choose from."""
        rows = await self.db.fetch_all(
            "SELECT profile_version_id FROM recipe_version_pins ORDER BY profile_version_id"
        )
        return [int(row["profile_version_id"]) for row in rows]

    async def find_version_for_device_profile(self, device_id: str) -> ProfileVersionRow | None:
        """The version a device id currently holds, if we have mirrored it.

        How a shot gets its `profile_version_id`: the `.slog` header records the
        profile *id*, and this is the only thing that maps that back to content.
        A shot brewed before the mirror caught up keeps a NULL and is re-linked
        by the next profiles run.
        """
        row = await self.db.fetch_one(
            """
            SELECT v.* FROM profile_versions v
            JOIN device_profiles d ON d.current_version_id = v.id
            WHERE d.device_id = ?
            """,
            (device_id,),
        )
        return self.to_model(ProfileVersionRow, row)

    # ── the device mirror ────────────────────────────────────────────

    async def upsert_device_profile(
        self,
        *,
        device_id: str,
        version_id: int,
        favorite: bool = False,
        selected: bool = False,
        position: int | None = None,
        label: str | None = None,
    ) -> DeviceProfileRow:
        """Point a device id at a version and refresh its NVS-side state, and the name the file
        has right now (``label``, from the document the machine served; left out, the version's).

        ``deleted_at = NULL`` on every upsert: a profile that reappears (the
        user restored it, or a previous list arrived during an OTA and was
        short) is alive again, and leaving the tombstone would hide it from the
        UI for ever.
        """
        now = utc_now()
        if label is None:
            label = str(
                await self.db.fetch_value(
                    "SELECT label FROM profile_versions WHERE id = ?", (version_id,)
                )
                or ""
            )
        await self.db.execute(
            """
            INSERT INTO device_profiles
                (device_id, current_version_id, favorite, selected, position, label,
                 first_seen_at, last_seen_at, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(device_id) DO UPDATE SET
                current_version_id = excluded.current_version_id,
                favorite = excluded.favorite,
                selected = excluded.selected,
                position = excluded.position,
                label = excluded.label,
                last_seen_at = excluded.last_seen_at,
                deleted_at = NULL
            """,
            (
                device_id,
                version_id,
                int(favorite),
                int(selected),
                position,
                label,
                now,
                now,
            ),
        )
        row = await self.get_device_profile(device_id)
        if row is None:  # pragma: no cover - the upsert above guarantees it
            raise RuntimeError(f"device profile {device_id!r} vanished between write and read")
        return row

    async def get_device_profile(self, device_id: str) -> DeviceProfileRow | None:
        row = await self.db.fetch_one(
            "SELECT * FROM device_profiles WHERE device_id = ?", (device_id,)
        )
        return self.to_model(DeviceProfileRow, row)

    async def mark_missing_deleted(self, seen: list[str]) -> int:
        """Tombstone every device profile the machine no longer lists.

        A *tombstone*, never a delete: shots reference the version, the user's
        Recipes will reference it, and "the profile I used in March" has to keep
        resolving after the profile is gone from the display.

        An empty ``seen`` is treated as "we learned nothing" rather than "the
        machine has no profiles": the firmware creates a Default profile on an
        empty filesystem, so a genuinely empty list is a failed read, and acting
        on it would tombstone the entire mirror.
        """
        if not seen:
            return 0
        placeholders = ", ".join("?" for _ in seen)
        cursor = await self.db.execute(
            f"""
            UPDATE device_profiles SET deleted_at = ?
            WHERE deleted_at IS NULL AND device_id NOT IN ({placeholders})
            """,  # noqa: S608 - placeholders are bound parameters, one per id
            (utc_now(), *seen),
        )
        return cursor.rowcount

    async def find_device_id_for_version(self, version_id: int) -> str | None:
        """Which live device profile currently holds this version, if any.

        Recorded on a draft at creation time so staleness can be checked later.
        ``None`` is a real answer, not a failure: an imported
        version, or a version this box drafted, was never on the machine and has
        nothing to drift from. When more than one file holds it the file the version's own
        profile stands on is the answer.
        """
        row = await self.db.fetch_one(
            """
            SELECT d.device_id FROM device_profiles d
            LEFT JOIN profile_versions pv ON pv.id = d.current_version_id
            WHERE d.current_version_id = ? AND d.deleted_at IS NULL
            ORDER BY d.device_id IS NOT (SELECT b.device_profile_id FROM profile_board b
                                          WHERE b.id = pv.board_id),
                     d.device_id
            LIMIT 1
            """,
            (version_id,),
        )
        return str(row["device_id"]) if row is not None else None

    async def owner_of(self, version_id: int) -> int | None:
        """The profile a version belongs to, or ``None``."""
        found = await self.db.fetch_value(
            "SELECT board_id FROM profile_versions WHERE id = ?", (version_id,)
        )
        return None if found is None else int(found)

    async def standing_profile(self, device_id: str) -> int | None:
        """The profile-list row that stands on this machine file: a live one, else the newest
        tombstoned one, else ``None``. The owner of a version the mirror reads off that file."""
        found = await self.db.fetch_value(
            "SELECT id FROM profile_board WHERE device_profile_id = ? "
            "ORDER BY (deleted_at IS NOT NULL), id DESC LIMIT 1",
            (device_id,),
        )
        return None if found is None else int(found)

    async def point_file_at(self, device_id: str, version_id: int) -> None:
        """Make a mirrored file hold another version (of the same document): a file a profile has
        just been attached to, or made into, holds that profile's own version of it.

        The shots already linked to the file's old version follow, and only those of this file:
        a shot is linked to what the file held when it was brewed, and the profile that now stands
        on the file is the one its shots are filed under. Without it a shot read between the
        mirror and the attach would stay on a version that belongs to no profile, and the Recipe
        that brews the profile would not file it.
        """
        old = await self.db.fetch_value(
            "SELECT current_version_id FROM device_profiles WHERE device_id = ?", (device_id,)
        )
        if old is None or int(old) == version_id:
            return
        await self.db.execute(
            "UPDATE device_profiles SET current_version_id = ? WHERE device_id = ?",
            (version_id, device_id),
        )
        await self.db.execute(
            "UPDATE shots SET profile_version_id = ?, updated_at = ? "
            "WHERE profile_id_on_device = ? AND profile_version_id = ?",
            (version_id, utc_now(), device_id, int(old)),
        )

    async def mark_one_deleted(self, device_id: str) -> int:
        """Tombstone one device profile, by id, without a list to diff against.

        What the board's write phase does to the mirror when it removes a file.
        `mark_missing_deleted` infers deletions from a fresh listing, which is right
        for sync and wrong here: we know exactly which profile we just removed, and
        waiting for the next sync would leave the Profiles page showing a file that is
        gone.

        A tombstone rather than a DELETE, as everywhere in this table: shots and
        Recipe versions reference the version, and "the profile I used in March"
        has to keep resolving.
        """
        cursor = await self.db.execute(
            "UPDATE device_profiles SET deleted_at = ? WHERE device_id = ? AND deleted_at IS NULL",
            (utc_now(), device_id),
        )
        return cursor.rowcount

    async def list_device_profiles(
        self, *, include_deleted: bool = False
    ) -> list[DeviceProfileSummary]:
        """The mirror, joined to the current version and counting shots."""
        where = ["1 = 1"]
        params: list[object] = []
        if not include_deleted:
            where.append("d.deleted_at IS NULL")
        rows = await self.db.fetch_all(
            f"""
            SELECT d.device_id, d.current_version_id, d.favorite, d.selected, d.position,
                   d.first_seen_at, d.last_seen_at, d.deleted_at,
                   d.label, v.type, v.utility, v.content_hash,
                   (SELECT COUNT(*) FROM shots s
                     WHERE s.profile_id_on_device = d.device_id) AS shot_count
            FROM device_profiles d
            JOIN profile_versions v ON v.id = d.current_version_id
            WHERE {" AND ".join(where)}
            ORDER BY d.deleted_at IS NOT NULL,
                     COALESCE(d.position, 9999), d.label
            """,  # noqa: S608 - the WHERE clauses above are literals, values are bound
            params,
        )
        return self.to_models(DeviceProfileSummary, rows)

    async def get_device_profile_summary(self, device_id: str) -> DeviceProfileSummary | None:
        summaries = await self.list_device_profiles(include_deleted=True)
        return next((s for s in summaries if s.device_id == device_id), None)


def _summary(row: dict[str, object]) -> ProfileVersionSummary:
    """A list row, with the recipe numbers read out of its document.

    Derived on read rather than stored: the rule is a few lines of Python over a
    document the row already holds, and a stored copy would have to be
    rewritten every time the rule learned something. The document itself stays off the
    row (see :class:`ProfileVersionSummary`).
    """
    raw = row.pop("document", None)
    document = json.loads(raw) if isinstance(raw, str) else None
    recipe = profile_recipe(document if isinstance(document, dict) else None)
    pin_id = row.pop("pin_id", None)
    pin_recipe_id = row.pop("pin_recipe_id", None)
    pin_recipe_version_id = row.pop("pin_recipe_version_id", None)
    pin = (
        None
        if pin_id is None
        else {"pin_id": pin_id, "set_id": pin_recipe_id, "set_version_id": pin_recipe_version_id}
    )
    set_id = row.pop("set_id", None)
    set_name = row.pop("set_name", None)
    return ProfileVersionSummary.model_validate(
        {
            **row,
            "set": None if set_id is None else {"id": set_id, "name": set_name},
            "temperature_c": recipe.temperature_c,
            "target_yield_g": recipe.target_yield_g,
            "pin": pin,
        }
    )

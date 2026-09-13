"""`profile_versions` and `device_profiles` — the mirror of `/p/` on the machine.

Two tables because a profile has two independent identities. What it *brews* is
the canonical JSON and its sha256, and that is immutable: edit a phase and you
have a new version, not a changed one. Where it *lives* is the device id, and
that is mutable — it gains and loses the favourite star, moves position, is
selected, is deleted. Putting the second lot in the hash would make every
profile appear to change the moment somebody starred it.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonObject, utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.models import Profile, canonical_profile_json, profile_content_hash

__all__ = [
    "DeviceProfileRow",
    "DeviceProfileSummary",
    "ProfileVersionPage",
    "ProfileVersionRow",
    "ProfileVersionSummary",
    "ProfilesRepository",
]


class ProfileVersionRow(BaseModel):
    """One immutable profile version, identified by its content hash."""

    model_config = ConfigDict(extra="forbid")

    id: int
    content_hash: str
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
    label: str
    type: str
    utility: bool = False
    #: `device` (mirrored off the machine), `import` (a file someone loaded), or
    #: `draft` (this box authored it). A drafted version becomes a
    #: mirrored one the moment it is pushed and the next profiles run sees it —
    #: the hash is the same document, so it keeps the source it was created with.
    source: str = "device"
    created_at: str
    #: Whether some device profile currently points at this version. An imported
    #: version is normally False; a mirrored one that was later edited on the
    #: machine becomes False too, which is the honest answer — it is history.
    mirrored: bool = False
    #: How many shots resolve to this exact version.
    shot_count: int = 0


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


class DeviceProfileSummary(BaseModel):
    """A device profile joined to its current version — what `GET /api/profiles` returns."""

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
        self, profile: Profile, *, source: str = "device", device_json: str | None = None
    ) -> tuple[ProfileVersionRow, bool]:
        """Return the version row for this profile, inserting it if it is new.

        The second element says whether it was inserted, which is what the sync
        engine reports as "a profile changed" — a profile the machine serves
        every fifteen minutes must not look like news every fifteen minutes.

        The label is *not* part of the hash's input beyond being a field of the
        canonical JSON, so renaming a profile does create a new version. That is
        deliberate: the name is what the shot header records and what the user
        recognises, so a rename is a change worth keeping.
        """
        content_hash = profile_content_hash(profile)
        existing = await self.get_version_by_hash(content_hash)
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
                (content_hash, label, type, utility, json, device_json, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_hash,
                profile.label,
                profile.type,
                int(profile.utility),
                canonical_profile_json(profile),
                device_json,
                source,
                utc_now(),
            ),
        )
        version = await self.get_version(int(cursor.lastrowid or 0))
        if version is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("profile version vanished between write and read")
        return version, True

    async def get_version(self, version_id: int) -> ProfileVersionRow | None:
        row = await self.db.fetch_one("SELECT * FROM profile_versions WHERE id = ?", (version_id,))
        return self.to_model(ProfileVersionRow, row)

    async def get_version_by_hash(self, content_hash: str) -> ProfileVersionRow | None:
        row = await self.db.fetch_one(
            "SELECT * FROM profile_versions WHERE content_hash = ?", (content_hash,)
        )
        return self.to_model(ProfileVersionRow, row)

    async def find_version_by_label(self, label: str) -> ProfileVersionRow | None:
        """The newest stored version carrying this exact label.

        The importer's fallback link: a shot export names its profile but
        the `profileId` it carries is usually long gone from the machine, so the
        label is the only handle left. Newest wins because a relabelled profile
        makes a new version and the most recent one is the current meaning of
        that name. An exact, case-sensitive match on purpose — "9 Bar" and
        "9 bar" are two profiles as far as the machine is concerned.
        """
        if not label:
            return None
        row = await self.db.fetch_one(
            "SELECT * FROM profile_versions WHERE label = ? ORDER BY id DESC LIMIT 1",
            (label,),
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
        where = ["1 = 1"]
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
            SELECT v.id, v.content_hash, v.label, v.type, v.utility, v.source, v.created_at,
                   EXISTS (SELECT 1 FROM device_profiles d
                            WHERE d.current_version_id = v.id
                              AND d.deleted_at IS NULL) AS mirrored,
                   (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = v.id) AS shot_count
            FROM profile_versions v
            WHERE {clause}
            ORDER BY v.id DESC
            LIMIT ? OFFSET ?
            """,  # noqa: S608 - the WHERE clauses above are literals, values are bound
            [*params, limit, offset],
        )
        return ProfileVersionPage(
            items=self.to_models(ProfileVersionSummary, rows),
            total=int(total_row["n"]) if total_row is not None else 0,
        )

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
    ) -> DeviceProfileRow:
        """Point a device id at a version and refresh its NVS-side state.

        ``deleted_at = NULL`` on every upsert: a profile that reappears (the
        user restored it, or a previous list arrived during an OTA and was
        short) is alive again, and leaving the tombstone would hide it from the
        UI for ever.
        """
        now = utc_now()
        await self.db.execute(
            """
            INSERT INTO device_profiles
                (device_id, current_version_id, favorite, selected, position,
                 first_seen_at, last_seen_at, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(device_id) DO UPDATE SET
                current_version_id = excluded.current_version_id,
                favorite = excluded.favorite,
                selected = excluded.selected,
                position = excluded.position,
                last_seen_at = excluded.last_seen_at,
                deleted_at = NULL
            """,
            (
                device_id,
                version_id,
                int(favorite),
                int(selected),
                position,
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
        Sets will reference it, and "the profile I used in March" has to keep
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
        nothing to drift from.
        """
        row = await self.db.fetch_one(
            "SELECT device_id FROM device_profiles "
            "WHERE current_version_id = ? AND deleted_at IS NULL LIMIT 1",
            (version_id,),
        )
        return str(row["device_id"]) if row is not None else None

    async def mark_one_deleted(self, device_id: str) -> int:
        """Tombstone one device profile, by id, without a list to diff against.

        The rollback path's only write to the mirror. `mark_missing_deleted`
        infers deletions from a fresh listing, which is right for sync and wrong
        here: we know exactly which profile we just removed, and waiting for the
        next sweep would leave the Profiles page showing a file that is gone.

        A tombstone rather than a DELETE, as everywhere in this table: shots and
        Set versions reference the version, and "the profile I used in March"
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
            SELECT d.*, v.label, v.type, v.utility, v.content_hash,
                   (SELECT COUNT(*) FROM shots s
                     WHERE s.profile_id_on_device = d.device_id) AS shot_count
            FROM device_profiles d
            JOIN profile_versions v ON v.id = d.current_version_id
            WHERE {" AND ".join(where)}
            ORDER BY d.deleted_at IS NOT NULL,
                     COALESCE(d.position, 9999), v.label
            """,  # noqa: S608 - the WHERE clauses above are literals, values are bound
            params,
        )
        return self.to_models(DeviceProfileSummary, rows)

    async def get_device_profile_summary(self, device_id: str) -> DeviceProfileSummary | None:
        summaries = await self.list_device_profiles(include_deleted=True)
        return next((s for s in summaries if s.device_id == device_id), None)

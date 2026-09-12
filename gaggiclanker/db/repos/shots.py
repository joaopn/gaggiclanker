"""`shots` and `shot_samples` — the archive proper.

The one invariant worth stating twice: **`raw_slog` is the source of truth and
everything else in these two tables is derived from it.** A shot is inserted
with its samples, its phases and its diagnostics in a single transaction, so a
crash half-way leaves no shot rather than a shot with half a curve; and a shot
whose bytes did not parse is inserted anyway, quarantined, with no samples at
all. The machine deletes its copy under storage pressure and will not wait for
us to fix a parser.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonList, JsonObject, JsonText, utc_now
from gaggiclanker.db.repository import Repository

__all__ = [
    "SAMPLE_FIELDS",
    "ShotCounts",
    "ShotDetailRow",
    "ShotInsert",
    "ShotListRow",
    "ShotPage",
    "ShotSampleRow",
    "ShotState",
    "ShotsRepository",
]

#: The 14 `.slog` sample fields, in the firmware's own order, plus the phase
#: number derived from the header's transition table. `t` is stored as `t_ms`
#: because it is the primary key half and "t" alone reads like a type.
SAMPLE_FIELDS = (
    "t_ms",
    "tt",
    "ct",
    "tp",
    "cp",
    "fl",
    "tf",
    "pf",
    "vf",
    "v",
    "ev",
    "pr",
    "si",
    "wp",
    "phase_number",
)


class ShotSampleRow(BaseModel):
    """One row of `shot_samples`, in real units.

    Every field but ``t_ms`` is optional: ``None`` means the file's `fieldsMask`
    bit was clear, i.e. "the firmware never recorded this", which is a different
    fact from "it recorded zero" and one the diagnostics depend on.
    """

    model_config = ConfigDict(extra="forbid")

    t_ms: int
    tt: float | None = None
    ct: float | None = None
    tp: float | None = None
    cp: float | None = None
    fl: float | None = None
    tf: float | None = None
    pf: float | None = None
    vf: float | None = None
    v: float | None = None
    ev: float | None = None
    pr: float | None = None
    si: int | None = None
    wp: float | None = None
    phase_number: int | None = None


class ShotInsert(BaseModel):
    """Everything needed to store one shot. The only way a row reaches `shots`."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    machine_id: int
    raw_slog: bytes
    #: Where these bytes came from: the sync engine (``device``) or a JSON
    #: export of a shot the machine has already deleted (``import``).
    source: str = "device"

    started_at: str | None = None
    start_epoch: int = 0
    duration_ms: int = 0
    profile_version_id: int | None = None
    profile_id_on_device: str = ""
    profile_name_on_device: str = ""
    final_weight_g: float | None = None
    final_exit_reason: int | None = None
    brew_delay_ms: int | None = None
    slog_version: int | None = None
    sample_interval_ms: int | None = None
    fields_mask: int | None = None
    sample_count: int = 0
    scale_connected: bool = False
    incomplete: bool = False

    deleted_on_device: bool = False
    quarantined: bool = False
    quarantine_reason: str | None = None

    phases_json: JsonText | None = None
    diagnostics_json: JsonText | None = None
    execution_score: float | None = None
    execution_reason: str | None = None

    index_rating: int | None = None
    index_volume_g: float | None = None
    index_avg_temp_c: float | None = None
    index_max_pressure_bar: float | None = None
    index_avg_flow_ml_s: float | None = None
    index_flags: int | None = None


class ShotListRow(BaseModel):
    """A row of `GET /api/shots`: enough to draw a line in a table, no curve."""

    model_config = ConfigDict(extra="forbid")

    id: int
    device_id: str
    machine_id: int
    source: str = "device"
    started_at: str | None = None
    start_epoch: int = 0
    duration_ms: int = 0
    profile_version_id: int | None = None
    profile_id_on_device: str = ""
    profile_name_on_device: str = ""
    profile_label: str | None = None
    final_weight_g: float | None = None
    volume_g: float | None = None
    index_rating: int | None = None
    index_avg_temp_c: float | None = None
    index_max_pressure_bar: float | None = None
    index_avg_flow_ml_s: float | None = None
    execution_score: float | None = None
    execution_reason: str | None = None
    sample_count: int = 0
    scale_connected: bool = False
    incomplete: bool = False
    quarantined: bool = False
    quarantine_reason: str | None = None
    deleted_on_device: bool = False
    #: The user's rating from the device's own notes, when there are any. The
    #: index also carries a rating; this one is the document's, which is the one
    #: the machine's UI edits.
    rating: int | None = None
    has_notes: bool = False
    synced_at: str


class ShotDetailRow(ShotListRow):
    """`GET /api/shots/{id}`: the list row plus the derived blobs.

    ``raw_slog`` is deliberately not here — it is a blob of a few kilobytes per
    shot and has its own endpoint. Reading a hundred detail rows should not
    read a megabyte of bytes nobody asked for. ``raw_bytes`` is its length, so a
    caller can tell "we hold the bytes" from "we hold a row".
    """

    model_config = ConfigDict(extra="forbid")

    set_version_id: int | None = None
    final_exit_reason: int | None = None
    brew_delay_ms: int | None = None
    slog_version: int | None = None
    sample_interval_ms: int | None = None
    fields_mask: int | None = None
    index_volume_g: float | None = None
    index_flags: int | None = None
    #: Per-phase statistics, decoded. Derived from `raw_slog` at ingest and
    #: rebuildable from it, which is why a diagnostics bug is not a lost shot.
    phases: JsonList = Field(default=None, validation_alias="phases_json")
    #: `{summary, diagnostics, detail_level, has_pressure}`, decoded.
    diagnostics: JsonObject = Field(default=None, validation_alias="diagnostics_json")
    raw_bytes: int = 0
    updated_at: str


@dataclass(frozen=True, slots=True)
class ShotState:
    """What the archive already knows about a device shot, for the index diff.

    Deliberately not a pydantic model: this is loaded for every shot on the
    machine on every pass, and it never leaves the process.
    """

    id: int
    device_id: str
    quarantined: bool
    deleted_on_device: bool
    index_rating: int | None
    index_volume_g: float | None
    index_flags: int | None
    has_notes: bool


class ShotCounts(BaseModel):
    """The headline numbers `GET /api/sync/status` reports."""

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    quarantined: int = 0
    deleted_on_device: int = 0
    incomplete: int = 0
    samples: int = 0


class ShotPage(BaseModel):
    """One page of shots, with the cursor that continues it."""

    model_config = ConfigDict(extra="forbid")

    items: list[ShotListRow]
    total: int
    next_cursor: str | None = None


# The list projection. Written once because the list route, the detail route and
# the tests must all agree on what "volume" means: the scale's final weight when
# there was a scale, and the device index's figure otherwise.
_LIST_COLUMNS = """
    s.id, s.device_id, s.machine_id, s.source, s.started_at, s.start_epoch, s.duration_ms,
    s.profile_version_id, s.profile_id_on_device, s.profile_name_on_device,
    v.label AS profile_label,
    s.final_weight_g,
    COALESCE(s.final_weight_g, s.index_volume_g) AS volume_g,
    s.index_rating, s.index_avg_temp_c, s.index_max_pressure_bar, s.index_avg_flow_ml_s,
    s.execution_score, s.execution_reason,
    s.sample_count, s.scale_connected, s.incomplete,
    s.quarantined, s.quarantine_reason, s.deleted_on_device,
    n.rating AS rating,
    n.shot_id IS NOT NULL AS has_notes,
    s.synced_at
"""

_LIST_FROM = """
    FROM shots s
    LEFT JOIN profile_versions v ON v.id = s.profile_version_id
    LEFT JOIN device_shot_notes n ON n.shot_id = s.id
"""

#: The default sort key. `COALESCE(started_at, '')` rather than `started_at` so
#: a shot from a machine whose clock never synced (`startEpoch < 10000`, which
#: the firmware's own UI treats as "no timestamp") sorts last under DESC instead
#: of first — and so keyset pagination has a total order to walk.
#:
#: `idx_shots_list` is an index on exactly this expression. Change one and the
#: other stops being used, silently: the planner cannot see through a COALESCE,
#: and the symptom is a TEMP B-TREE on every page rather than an error.
_ORDER_KEY = "COALESCE(s.started_at, '')"

#: What `GET /api/shots?sort=` accepts, and the column each name means. Only
#: these: a sort taken from the query string and pasted into SQL is an injection,
#: so the parameter selects a key from here and never becomes one.
SORT_KEYS: dict[str, str] = {
    "started_at": _ORDER_KEY,
    "execution_score": "s.execution_score",
    "duration": "s.duration_ms",
    "rating": "COALESCE(n.rating, s.index_rating)",
}


class ShotsRepository(Repository):
    """Reads and writes shots and their samples."""

    # ── writing ──────────────────────────────────────────────────────

    async def insert(self, shot: ShotInsert, samples: Sequence[ShotSampleRow] = ()) -> int:
        """Store one shot and its samples atomically. Returns the new row id.

        One transaction, deliberately: a shot whose samples half-landed would
        render as a curve that stops in the middle of the extraction, and
        nothing downstream could tell that apart from a machine that lost power.
        """
        if shot.quarantined and samples:
            # The archive's central rule, enforced where it is cheap:
            # bytes we could not parse cannot have produced trustworthy samples.
            raise ValueError("a quarantined shot must not carry sample rows")

        payload = shot.model_dump()
        for flag in ("scale_connected", "incomplete", "deleted_on_device", "quarantined"):
            payload[flag] = int(payload[flag])
        payload["synced_at"] = payload["updated_at"] = utc_now()
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{name}" for name in payload)

        async with self.db.transaction():
            cursor = await self.db.execute(
                f"INSERT INTO shots ({columns}) VALUES ({placeholders})",  # noqa: S608 - keys are model fields
                payload,
            )
            shot_id = int(cursor.lastrowid or 0)
            if samples:
                await self._insert_samples(shot_id, samples)
        return shot_id

    async def _insert_samples(self, shot_id: int, samples: Sequence[ShotSampleRow]) -> None:
        columns = ", ".join(("shot_id", *SAMPLE_FIELDS))
        placeholders = ", ".join("?" for _ in range(len(SAMPLE_FIELDS) + 1))
        await self.db.execute_many(
            f"INSERT INTO shot_samples ({columns}) VALUES ({placeholders})",  # noqa: S608 - SAMPLE_FIELDS is a module constant
            [(shot_id, *(getattr(sample, field) for field in SAMPLE_FIELDS)) for sample in samples],
        )

    async def update_index_fields(
        self,
        shot_id: int,
        *,
        rating: int | None,
        volume_g: float | None,
        avg_temp_c: float | None,
        max_pressure_bar: float | None,
        avg_flow_ml_s: float | None,
        flags: int | None,
        deleted_on_device: bool,
    ) -> None:
        """Reconcile a shot with a changed index entry.

        The device rewrites index entries **in place**: a rating typed on the
        machine, a dose entered in its notes card (which overrides `volume`), or
        the deleted flag set by `cleanupHistory()`. None of that changes the
        `.slog`, so re-fetching the file would learn nothing; this is the only
        path by which those facts reach the archive.
        """
        await self.db.execute(
            """
            UPDATE shots SET
                index_rating = ?, index_volume_g = ?, index_avg_temp_c = ?,
                index_max_pressure_bar = ?, index_avg_flow_ml_s = ?, index_flags = ?,
                deleted_on_device = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                rating,
                volume_g,
                avg_temp_c,
                max_pressure_bar,
                avg_flow_ml_s,
                flags,
                int(deleted_on_device),
                utc_now(),
                shot_id,
            ),
        )

    async def mark_deleted_on_device(self, shot_id: int) -> None:
        """Record that the machine no longer has this shot.

        Our copy stays — that is what the archive is for. Only the marker moves.
        """
        await self.db.execute(
            "UPDATE shots SET deleted_on_device = 1, updated_at = ? WHERE id = ?",
            (utc_now(), shot_id),
        )

    async def link_profile_version(self, shot_id: int, version_id: int) -> None:
        """Attach a shot to the profile version it was brewed with."""
        await self.db.execute(
            "UPDATE shots SET profile_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, utc_now(), shot_id),
        )

    async def link_unlinked_by_device_profile(
        self, machine_id: int, device_profile_id: str, version_id: int
    ) -> int:
        """Link every still-unlinked shot brewed with this device profile.

        Shots arrive before the profile mirror on a first boot (the index diff
        starts the moment the socket is up), so a shot's `profile_version_id` is
        filled in by whichever runs second. Only NULLs are touched: a shot
        already linked to the version that was on the device *at the time* keeps
        it when the user later edits that profile.
        """
        cursor = await self.db.execute(
            """
            UPDATE shots SET profile_version_id = ?, updated_at = ?
            WHERE machine_id = ? AND profile_id_on_device = ? AND profile_version_id IS NULL
            """,
            (version_id, utc_now(), machine_id, device_profile_id),
        )
        return cursor.rowcount

    # ── reading ──────────────────────────────────────────────────────

    async def known_states(self, machine_id: int) -> dict[str, ShotState]:
        """Every shot we already have for this machine, keyed by padded device id.

        One query per pass rather than one per index entry: a full index is a
        few hundred rows and the diff is a set difference, not a lookup loop.
        """
        rows = await self.db.fetch_all(
            """
            SELECT s.id, s.device_id, s.quarantined, s.deleted_on_device,
                   s.index_rating, s.index_volume_g, s.index_flags,
                   n.shot_id IS NOT NULL AS has_notes
            FROM shots s
            LEFT JOIN device_shot_notes n ON n.shot_id = s.id
            WHERE s.machine_id = ?
            """,
            (machine_id,),
        )
        return {
            str(row["device_id"]): ShotState(
                id=int(row["id"]),
                device_id=str(row["device_id"]),
                quarantined=bool(row["quarantined"]),
                deleted_on_device=bool(row["deleted_on_device"]),
                index_rating=row["index_rating"],
                index_volume_g=row["index_volume_g"],
                index_flags=row["index_flags"],
                has_notes=bool(row["has_notes"]),
            )
            for row in rows
        }

    async def get(self, shot_id: int) -> ShotDetailRow | None:
        row = await self.db.fetch_one(
            f"""
            SELECT {_LIST_COLUMNS},
                   s.set_version_id, s.final_exit_reason, s.brew_delay_ms, s.slog_version,
                   s.sample_interval_ms, s.fields_mask, s.index_volume_g, s.index_flags,
                   s.phases_json, s.diagnostics_json,
                   LENGTH(s.raw_slog) AS raw_bytes, s.updated_at
            {_LIST_FROM}
            WHERE s.id = ?
            """,
            (shot_id,),
        )
        return self.to_model(ShotDetailRow, row)

    async def get_by_device_id(self, machine_id: int, device_id: str) -> ShotDetailRow | None:
        row = await self.db.fetch_value(
            "SELECT id FROM shots WHERE machine_id = ? AND device_id = ?",
            (machine_id, device_id),
        )
        return None if row is None else await self.get(int(row))

    async def raw_slog(self, shot_id: int) -> bytes | None:
        """The stored `.slog` bytes. The archive's product; everything else is derived."""
        value = await self.db.fetch_value("SELECT raw_slog FROM shots WHERE id = ?", (shot_id,))
        return None if value is None else bytes(value)

    async def samples(self, shot_id: int) -> list[ShotSampleRow]:
        """Every sample of one shot, in `t_ms` order.

        The order is the table's own: `shot_samples` is WITHOUT ROWID on
        `(shot_id, t_ms)`, so this is a range scan over adjacent pages, and the
        ORDER BY costs nothing but says what the caller is promised.
        """
        rows = await self.db.fetch_all(
            f"SELECT {', '.join(SAMPLE_FIELDS)} FROM shot_samples "  # noqa: S608 - module constant
            "WHERE shot_id = ? ORDER BY t_ms",
            (shot_id,),
        )
        return self.to_models(ShotSampleRow, rows)

    async def counts(self, machine_id: int | None = None) -> ShotCounts:
        clause = "" if machine_id is None else " WHERE machine_id = ?"
        params: tuple[Any, ...] = () if machine_id is None else (machine_id,)
        row = await self.db.fetch_one(
            f"""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(quarantined), 0) AS quarantined,
                   COALESCE(SUM(deleted_on_device), 0) AS deleted_on_device,
                   COALESCE(SUM(incomplete), 0) AS incomplete
            FROM shots{clause}
            """,  # noqa: S608 - the clause is one of two literals
            params,
        )
        samples = await self.db.fetch_value(
            "SELECT COUNT(*) FROM shot_samples"
            if machine_id is None
            else "SELECT COUNT(*) FROM shot_samples WHERE shot_id IN "
            "(SELECT id FROM shots WHERE machine_id = ?)",
            params,
        )
        counts = self.to_model(ShotCounts, row)
        if counts is None:  # pragma: no cover - COUNT(*) always returns a row
            return ShotCounts()
        counts.samples = int(samples or 0)
        return counts

    async def list_shots(
        self,
        *,
        limit: int = 50,
        offset: int | None = None,
        cursor: str | None = None,
        start_from: str | None = None,
        start_to: str | None = None,
        profile_version_id: int | None = None,
        machine_id: int | None = None,
        quarantined: bool | None = None,
        include_deleted_on_device: bool = True,
        source: str | None = None,
        min_score: float | None = None,
        max_score: float | None = None,
        min_rating: int | None = None,
        sort: str = "started_at",
        descending: bool = True,
    ) -> ShotPage:
        """One page of shots, newest first by default.

        Two paginations on purpose. ``offset`` is what a table with page numbers
        wants; ``cursor`` is keyset pagination over ``(started_at, id)`` and is
        the one that stays correct while the sync engine inserts rows underneath
        the reader, which on this appliance it is doing all the time.

        ``cursor`` only works on the default sort: the cursor encodes that key,
        and a keyset over a nullable score would need a different one. Sorting
        by anything else is an offset page, which is what a "worst shots first"
        view wants anyway.
        """
        order_key = SORT_KEYS.get(sort)
        if order_key is None:
            raise ValueError(f"unknown sort {sort!r}; try one of {', '.join(sorted(SORT_KEYS))}")
        if cursor is not None and order_key != _ORDER_KEY:
            raise ValueError("cursor paging is only available on the default sort")

        where = ["1 = 1"]
        params: list[Any] = []
        if machine_id is not None:
            where.append("s.machine_id = ?")
            params.append(machine_id)
        if source is not None:
            where.append("s.source = ?")
            params.append(source)
        if min_score is not None:
            where.append("s.execution_score >= ?")
            params.append(min_score)
        if max_score is not None:
            where.append("s.execution_score <= ?")
            params.append(max_score)
        if min_rating is not None:
            where.append("COALESCE(n.rating, s.index_rating) >= ?")
            params.append(min_rating)
        if start_from is not None:
            where.append("s.started_at >= ?")
            params.append(start_from)
        if start_to is not None:
            where.append("s.started_at <= ?")
            params.append(start_to)
        if profile_version_id is not None:
            where.append("s.profile_version_id = ?")
            params.append(profile_version_id)
        if quarantined is not None:
            where.append("s.quarantined = ?")
            params.append(int(quarantined))
        if not include_deleted_on_device:
            where.append("s.deleted_on_device = 0")

        filters = " AND ".join(where)
        total = await self.db.fetch_value(
            f"SELECT COUNT(*) {_LIST_FROM} WHERE {filters}",
            params,
        )

        page_params = list(params)
        pagination = ""
        if cursor is not None:
            key, last_id = _decode_cursor(cursor)
            pagination = f" AND ({_ORDER_KEY}, s.id) < (?, ?)"
            page_params.extend((key, last_id))
        direction = "DESC" if descending else "ASC"
        rows = await self.db.fetch_all(
            f"""
            SELECT {_LIST_COLUMNS}
            {_LIST_FROM}
            WHERE {filters}{pagination}
            ORDER BY {order_key} {direction}, s.id {direction}
            LIMIT ? OFFSET ?
            """,
            [*page_params, limit, offset or 0],
        )
        items = self.to_models(ShotListRow, rows)
        # A cursor is only meaningful for the default sort walked forwards:
        # handing one back to a caller paging by `offset`, or sorting by score,
        # would let it mix two orderings and skip rows.
        next_cursor = (
            _encode_cursor(items[-1])
            if offset is None and order_key == _ORDER_KEY and descending and len(items) == limit
            else None
        )
        return ShotPage(items=items, total=int(total or 0), next_cursor=next_cursor)


def _encode_cursor(row: ShotListRow) -> str:
    """The keyset cursor: the sort key of the last row on the page."""
    return base64.urlsafe_b64encode(f"{row.started_at or ''}|{row.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, int]:
    """Read a cursor back, or say plainly that it is not one.

    A cursor is opaque to the client, which means a client *will* send us
    something that is not one — a truncated URL, a stale bookmark. Raising
    ``ValueError`` here lets the route turn it into a 400 that names the
    parameter rather than a 500 from a base64 decoder.
    """
    try:
        key, _, raw_id = base64.urlsafe_b64decode(cursor.encode()).decode().rpartition("|")
        return key, int(raw_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("cursor is not a cursor this server issued") from exc

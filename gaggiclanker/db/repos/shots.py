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
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gaggiclanker.db.repos.base import JsonList, JsonObject, JsonText, utc_now
from gaggiclanker.db.repos.reviews import ShotReviewsRepository
from gaggiclanker.db.repos.version_names import label_sql
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.signature import ShotChecks, check_key
from gaggiclanker.domain.vocab import Decision
from gaggiclanker.domain.warnings import shot_warnings
from gaggiclanker.review.reading import ChecksBlock, ReviewBlock, Served, review_key, serve_review
from gaggiclanker.signatures.checks import CheckSubject, checks_for_shots

__all__ = [
    "CHECK_SORT",
    "REVIEW_SORT",
    "SAMPLE_FIELDS",
    "ExampleShotRow",
    "ShotCounts",
    "ShotDerivationSource",
    "ShotDerivationUpdate",
    "ShotDetailRow",
    "ShotInsert",
    "ShotListItem",
    "ShotListRow",
    "ShotPage",
    "ShotSampleRow",
    "ShotSetBadge",
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
    #: The derivation version that wrote the two columns above (see
    #: `sync/derive.py`). A shot stored without diagnostics keeps 0.
    derivation_version: int = 0

    index_rating: int | None = None
    index_volume_g: float | None = None
    index_avg_temp_c: float | None = None
    index_max_pressure_bar: float | None = None
    index_avg_flow_ml_s: float | None = None
    index_flags: int | None = None


class ShotBytes(BaseModel):
    """A shot's id and its stored bytes: what re-deriving a column needs."""

    id: int
    device_id: str
    raw_slog: bytes


class ShotDerivationSource(BaseModel):
    """What re-deriving one shot reads: its bytes, and the gate it was derived with."""

    id: int
    device_id: str
    raw_slog: bytes
    #: The `has_pressure` its stored diagnostics were derived with (the resolved
    #: answer, never "unknown"), or ``None`` when it has no stored diagnostics.
    has_pressure: bool | None = None
    #: The stored document of the profile version the shot is linked to, decoded,
    #: or ``None`` when it is not linked (or the document does not decode).
    profile: JsonObject = Field(default=None, validation_alias="profile_json")
    #: The link that profile was read through, so the write can refuse to land
    #: on a shot that was linked to another version since.
    profile_version_id: int | None = None


class ShotDerivationUpdate(BaseModel):
    """The only columns a re-derive may write, and the version that wrote them."""

    model_config = ConfigDict(extra="forbid")

    phases_json: JsonText | None
    diagnostics_json: JsonText | None
    derivation_version: int


class ShotSetBadge(BaseModel):
    """The Set a shot belongs to, in the three fields a badge renders.

    Nested on the list row rather than three flat columns because it is one
    fact — "this shot is Ethiopia natural v1.1" — and a row with
    `set_name: null, version_label: "v2"` would be a shape nothing can render.
    """

    model_config = ConfigDict(extra="forbid")

    set_id: int
    set_name: str
    #: The version's name ("v1.1"), which is what a badge shows.
    version_label: str


class ShotListRow(BaseModel):
    """A row of `GET /api/shots`: enough to draw a line in a table, no curve."""

    model_config = ConfigDict(extra="forbid")

    id: int
    device_id: str
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
    #: The rating from this box's own verdict, which is a different fact from
    #: the one above: the machine's notes card is what was typed at the
    #: machine, this is what was decided here. The list shows this first and
    #: falls back to the device's, and it is what the stars in a row write.
    judgement_rating: int | None = None
    has_notes: bool = False
    #: Whether the user has recorded a verdict on the cup. The list shows it as
    #: a dot; `needs_set` has an equivalent on the Set side.
    has_judgement: bool = False
    #: The verdict's own notes, so the list can show what the cup was like and
    #: its row editor can open with what is already written rather than empty.
    #: The join that carries `rating` and `has_judgement` is already here, so
    #: this costs a column and no query.
    judgement_notes: str | None = None
    #: The verdict's decision — keep, improve, discard — which the list shows
    #: and sets from the row. The same join, one more column.
    judgement_decision: Decision | None = None
    #: What the user says they were brewing. NULL is the `needs_set`
    #: state: the shot arrived while no Set matched it, and it is waiting for
    #: somebody to say which one it belongs to.
    set_version_id: int | None = None
    set_badge: ShotSetBadge | None = None
    synced_at: str

    @model_validator(mode="before")
    @classmethod
    def _fold_set_badge(cls, data: Any) -> Any:
        """Fold the three joined badge columns into one nested object.

        The SQL has to select them flat — there is no other way to get three
        columns out of two joins — and the model has `extra="forbid"`, so
        without this every list query would fail validation. Done here rather
        than in the repository so that the projection and the model stay in one
        file and a new caller cannot forget it.
        """
        if not isinstance(data, dict) or "set_badge" in data:
            return data
        payload = dict(data)
        badge_set_id = payload.pop("badge_set_id", None)
        badge_set_name = payload.pop("badge_set_name", None)
        badge_version_label = payload.pop("badge_version_label", None)
        if badge_set_id is not None and badge_version_label is not None:
            payload["set_badge"] = {
                "set_id": badge_set_id,
                "set_name": badge_set_name or "",
                "version_label": badge_version_label or "",
            }
        return payload


class ShotListItem(ShotListRow):
    """A row of the shots list: the line, its Curve check and its review.

    Both depend on the version the shot is filed under (its target yield), on its profile
    version's **confirmed** signature and on the review in force, so they are worked out when the
    row is read and never stored: a shot refiled or discarded, a signature confirmed or a claim
    rejected needs no re-derivation. The Curve check is the deterministic checks and warnings only
    and never changes with a review; the review is what the model wrote.
    """

    model_config = ConfigDict(extra="forbid")

    checks: ChecksBlock
    review: ReviewBlock


class ShotDetailRow(ShotListRow):
    """`GET /api/shots/{id}`: the list row plus the derived blobs.

    ``raw_slog`` is deliberately not here — it is a blob of a few kilobytes per
    shot and has its own endpoint. Reading a hundred detail rows should not
    read a megabyte of bytes nobody asked for. ``raw_bytes`` is its length, so a
    caller can tell "we hold the bytes" from "we hold a row".
    """

    model_config = ConfigDict(extra="forbid")

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


class ExampleShotRow(BaseModel):
    """The shot a page takes its examples from, and whether it was judged."""

    model_config = ConfigDict(extra="forbid")

    id: int
    started_at: str | None = None
    judged: bool


@dataclass(frozen=True, slots=True)
class ShotState:
    """What the archive already knows about a device shot, for the index diff.

    Deliberately not a pydantic model: this is loaded for every shot on the
    machine on every pass, and it never leaves the process.
    """

    id: int
    device_id: str
    #: The other half of a shot's identity: the machine's number is reused after
    #: its settings are erased, the start time is not. 0 is "not known" (a shot
    #: stored unreadable before any index row listed it).
    start_epoch: int
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
    #: Shots with no Set version, quarantined ones excluded. The Shots page
    #: header shows it as a call to action, because an unassigned shot is
    #: invisible to every Set trend, the spread and the Set's conversations.
    needs_set: int = 0


class ShotPage(BaseModel):
    """One page of shots, with the cursor that continues it."""

    model_config = ConfigDict(extra="forbid")

    items: list[ShotListItem]
    total: int
    next_cursor: str | None = None


# The list projection. Written once because the list route, the detail route and
# the tests must all agree on what "volume" means: the scale's final weight when
# there was a scale, and the device index's figure otherwise.
_LIST_COLUMNS = f"""
    s.id, s.device_id, s.source, s.started_at, s.start_epoch, s.duration_ms,
    s.profile_version_id, s.profile_id_on_device, s.profile_name_on_device,
    v.label AS profile_label,
    s.final_weight_g,
    COALESCE(s.final_weight_g, s.index_volume_g) AS volume_g,
    s.index_rating, s.index_avg_temp_c, s.index_max_pressure_bar, s.index_avg_flow_ml_s,
    s.sample_count, s.scale_connected, s.incomplete,
    s.quarantined, s.quarantine_reason, s.deleted_on_device,
    n.rating AS rating,
    n.shot_id IS NOT NULL AS has_notes,
    j.shot_id IS NOT NULL AS has_judgement,
    j.rating AS judgement_rating,
    j.notes AS judgement_notes,
    j.decision AS judgement_decision,
    s.set_version_id,
    sv.set_id AS badge_set_id,
    st.name AS badge_set_name,
    {label_sql("sv")} AS badge_version_label,
    s.synced_at
"""

#: The "needs a Set" inbox, over `shots s`: the shots the archive is still
#: waiting for an answer on. A quarantined shot is not one: its bytes never
#: parsed, so there is no profile to match and nothing to judge, and leaving it
#: in would mean the count never reached zero. Nor is a shot labelled Discard:
#: the person has already said it went wrong, it counts towards no Set's spread,
#: and asking them where it belongs is asking about a shot they threw away. The
#: header's count, the list's filter and the button that files the inbox all
#: read this one condition, so the three can never disagree on what is waiting.
NEEDS_SET_SQL = """(
    s.set_version_id IS NULL AND s.quarantined = 0
    AND NOT EXISTS (
        SELECT 1 FROM shot_judgements dj WHERE dj.shot_id = s.id AND dj.decision = 'discard'
    )
)"""

# The Set joins are LEFT for the obvious reason and one less obvious one:
# `shots.set_version_id` carries no foreign key (migration 0005 explains why),
# so a row pointing at a version that no longer exists must list as unassigned
# rather than disappear from the archive.
_LIST_FROM = """
    FROM shots s
    LEFT JOIN profile_versions v ON v.id = s.profile_version_id
    LEFT JOIN device_shot_notes n ON n.shot_id = s.id
    LEFT JOIN shot_judgements j ON j.shot_id = s.id
    LEFT JOIN set_versions sv ON sv.id = s.set_version_id
    LEFT JOIN sets st ON st.id = sv.set_id
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
#: What "the rating" means when something has to pick one number.
_RATING_KEY = "COALESCE(j.rating, n.rating, s.index_rating)"

#: The Curve check and Review columns' sorts. Not columns: the order is the badge's, which is
#: worked out from the stored derivation, the filed version's target and the review in force
#: when the list is read (`_badge_sorted_ids`), so their entries below name no SQL.
CHECK_SORT = "check"
REVIEW_SORT = "review"

#: What a shot's checks are worked out from, per shot: the numbers `shot_warnings` reads, the
#: target yield and dose of the version the shot is filed under, and which profile version and
#: Set version decide its signature and its override. The diagnostics blob is large, so only
#: its `metrics` block and the pressure flag are pulled out of it.
_REVIEW_COLUMNS = """
    s.id, s.final_weight_g, s.scale_connected, s.final_exit_reason, s.duration_ms,
    s.phases_json, s.profile_version_id, s.set_version_id, s.quarantined, s.updated_at,
    CASE WHEN s.diagnostics_json IS NOT NULL AND json_valid(s.diagnostics_json)
         THEN json_extract(s.diagnostics_json, '$.metrics') END AS metrics_json,
    CASE WHEN s.diagnostics_json IS NOT NULL AND json_valid(s.diagnostics_json)
         THEN CASE json_type(s.diagnostics_json, '$.has_pressure')
                  WHEN 'false' THEN 0 ELSE 1 END
         ELSE 1 END AS has_pressure,
    sv.target_yield_g AS target_yield_g, sv.dose_g AS dose_g,
    j.decision AS judgement_decision
"""

SORT_KEYS: dict[str, str] = {
    CHECK_SORT: "",
    REVIEW_SORT: "",
    "started_at": _ORDER_KEY,
    "duration": "s.duration_ms",
    # The same expression the list's Rating column renders and the `min_rating`
    # filter applies, in that order of authority: this box's verdict, then the
    # machine's notes card, then the index's figure. Three places reading the
    # rating three different ways is how "sort by rating" and "rating" stop
    # agreeing on a page where both are visible.
    "rating": _RATING_KEY,
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

    async def replace_derived(
        self, shot_id: int, shot: ShotInsert, samples: Sequence[ShotSampleRow] = ()
    ) -> None:
        """Re-state one shot from a fresh set of bytes, keeping what is ours.

        The `--replace` path of the JSON importer: a better copy of a shot
        we already hold has arrived — an export with samples where we quarantined
        the bytes, or a file rescued from a backup. What the bytes say is
        overwritten; what a *person* or the *machine* said about the shot is not.

        Kept deliberately: the row id (so a Set or a judgement that references
        this shot still does), `set_version_id`, the `index_*` columns and
        `deleted_on_device` — the device's own view of a shot, which no export
        carries and which re-deriving cannot invent — and the `device_shot_notes`
        row, which lives in its own table and is written by its own repository.

        `profile_version_id` is COALESCEd rather than assigned: an import that
        cannot resolve the profile must not unlink a shot that was already
        linked to the version it was brewed with.
        """
        payload = {
            "id": shot_id,
            "raw_slog": shot.raw_slog,
            "source": shot.source,
            "started_at": shot.started_at,
            "start_epoch": shot.start_epoch,
            "duration_ms": shot.duration_ms,
            "profile_version_id": shot.profile_version_id,
            "profile_id_on_device": shot.profile_id_on_device,
            "profile_name_on_device": shot.profile_name_on_device,
            "final_weight_g": shot.final_weight_g,
            "final_exit_reason": shot.final_exit_reason,
            "brew_delay_ms": shot.brew_delay_ms,
            "slog_version": shot.slog_version,
            "sample_interval_ms": shot.sample_interval_ms,
            "fields_mask": shot.fields_mask,
            "sample_count": shot.sample_count,
            "scale_connected": int(shot.scale_connected),
            "incomplete": int(shot.incomplete),
            "quarantined": int(shot.quarantined),
            "quarantine_reason": shot.quarantine_reason,
            "phases_json": shot.phases_json,
            "diagnostics_json": shot.diagnostics_json,
            "derivation_version": shot.derivation_version,
            "updated_at": utc_now(),
        }
        if shot.quarantined and samples:
            raise ValueError("a quarantined shot must not carry sample rows")
        assignments = ", ".join(
            f"{name} = COALESCE(:{name}, profile_version_id)"
            if name == "profile_version_id"
            else f"{name} = :{name}"
            for name in payload
            if name != "id"
        )
        async with self.db.transaction():
            await self.db.execute(
                f"UPDATE shots SET {assignments} WHERE id = :id",  # noqa: S608 - keys are model fields
                payload,
            )
            # One transaction with the delete, so a replace that fails half-way
            # cannot leave a shot holding another shot's curve.
            await self.db.execute("DELETE FROM shot_samples WHERE shot_id = ?", (shot_id,))
            if samples:
                await self._insert_samples(shot_id, samples)

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

    async def missing_final_weight(self) -> list[ShotBytes]:
        """Shots a scale was connected to that were stored with no final weight.

        The candidates for re-reading the yield out of the bytes when the rule
        that derives it changes: a shot with no scale has no weight to find, and
        a quarantined one has no samples to read it from.
        """
        rows = await self.db.fetch_all(
            """
            SELECT id, device_id, raw_slog FROM shots
            WHERE final_weight_g IS NULL AND scale_connected = 1 AND quarantined = 0
            ORDER BY id
            """
        )
        return self.to_models(ShotBytes, rows)

    async def pending_derivations(self, version: int) -> list[int]:
        """Ids of the shots whose derived columns an older derivation wrote.

        ``ABS`` because a failed re-derive is marked with the negative of the
        version it failed at: it is skipped until the version moves on. A
        quarantined shot has nothing to derive from.
        """
        rows = await self.db.fetch_all(
            """
            SELECT id FROM shots
            WHERE quarantined = 0 AND ABS(derivation_version) < ?
            ORDER BY id
            """,
            (version,),
        )
        return [int(row["id"]) for row in rows]

    async def derivation_source(self, shot_id: int) -> ShotDerivationSource | None:
        """One shot's bytes, its profile, and the pressure gate its stored diagnostics used."""
        row = await self.db.fetch_one(
            """
            SELECT s.id, s.device_id, s.raw_slog,
                   CASE WHEN s.diagnostics_json IS NOT NULL AND json_valid(s.diagnostics_json)
                        THEN CASE json_type(s.diagnostics_json, '$.has_pressure')
                                 WHEN 'true' THEN 1 WHEN 'false' THEN 0 END
                   END AS has_pressure,
                   s.profile_version_id,
                   CASE WHEN json_valid(v.json) THEN v.json END AS profile_json
            FROM shots s LEFT JOIN profile_versions v ON v.id = s.profile_version_id
            WHERE s.id = ?
            """,
            (shot_id,),
        )
        return None if row is None else ShotDerivationSource.model_validate(dict(row))

    async def rewrite_derived(
        self, shot_id: int, update: ShotDerivationUpdate, *, profile_version_id: int | None
    ) -> bool:
        """Replace a shot's phases, diagnostics and version, and nothing else.

        One statement, so a crash leaves the shot as it was or as it is meant to
        be. Samples, notes, judgements, Set membership and `updated_at` are not
        this write's to touch: the shot itself did not change, only how it is
        read.

        ``profile_version_id`` is the link the update was derived with. The write
        lands only while the shot still has it: a link made since (the profile
        mirror, an import's label match) put the shot back to derivation version
        0 so it is derived again, and this write would put the old profile's
        result back at the current version. Returns whether it landed; ``False``
        leaves the shot for the next pass.
        """
        cursor = await self.db.execute(
            """
            UPDATE shots SET phases_json = :phases_json, diagnostics_json = :diagnostics_json,
                             derivation_version = :derivation_version
            WHERE id = :id AND profile_version_id IS :link
            """,
            {**update.model_dump(), "id": shot_id, "link": profile_version_id},
        )
        return cursor.rowcount > 0

    async def mark_derivation_failed(self, shot_id: int, version: int) -> None:
        """Record that this shot could not be re-derived at ``version``, so boots stop trying."""
        await self.db.execute(
            "UPDATE shots SET derivation_version = ? WHERE id = ?", (-version, shot_id)
        )

    async def set_final_weight(self, shot_id: int, final_weight_g: float) -> None:
        """Fill a final weight that a better reading of the same bytes found."""
        await self.db.execute(
            "UPDATE shots SET final_weight_g = ?, updated_at = ? WHERE id = ?",
            (final_weight_g, utc_now(), shot_id),
        )

    async def link_profile_version(self, shot_id: int, version_id: int) -> None:
        """Attach a shot to the profile version it was brewed with.

        The profile is an input of the shot's derived columns (which target each
        phase steered by), so the shot goes back to derivation version 0 in the
        same write: whatever it was derived with before, it is derived again.
        """
        await self.db.execute(
            """
            UPDATE shots SET profile_version_id = ?, derivation_version = 0, updated_at = ?
            WHERE id = ?
            """,
            (version_id, utc_now(), shot_id),
        )

    async def link_unlinked_by_device_profile(self, device_profile_id: str, version_id: int) -> int:
        """Link every still-unlinked shot brewed with this device profile.

        Shots arrive before the profile mirror on a first boot (the index diff
        starts the moment the socket is up), so a shot's `profile_version_id` is
        filled in by whichever runs second. Only NULLs are touched: a shot
        already linked to the version that was on the device *at the time* keeps
        it when the user later edits that profile. Each shot it links goes back to
        derivation version 0 in the same statement (see :meth:`link_profile_version`).
        """
        cursor = await self.db.execute(
            """
            UPDATE shots SET profile_version_id = ?, derivation_version = 0, updated_at = ?
            WHERE profile_id_on_device = ? AND profile_version_id IS NULL
            """,
            (version_id, utc_now(), device_profile_id),
        )
        return cursor.rowcount

    # ── reading ──────────────────────────────────────────────────────

    async def known_states(self) -> list[ShotState]:
        """Every shot we already have, in id order.

        A list and not a map by device id: after the machine's counter restarts
        several archived shots share a number, and a map would silently keep one
        of them. The identity of a shot is its number and its start epoch
        together; the index diff pairs on both.

        One query per pass rather than one per index entry: a full index is a
        few hundred rows and the diff is a set difference, not a lookup loop.
        """
        rows = await self.db.fetch_all(
            """
            SELECT s.id, s.device_id, s.start_epoch, s.quarantined, s.deleted_on_device,
                   s.index_rating, s.index_volume_g, s.index_flags,
                   n.shot_id IS NOT NULL AS has_notes
            FROM shots s
            LEFT JOIN device_shot_notes n ON n.shot_id = s.id
            ORDER BY s.id
            """
        )
        return [
            ShotState(
                id=int(row["id"]),
                device_id=str(row["device_id"]),
                start_epoch=int(row["start_epoch"]),
                quarantined=bool(row["quarantined"]),
                deleted_on_device=bool(row["deleted_on_device"]),
                index_rating=row["index_rating"],
                index_volume_g=row["index_volume_g"],
                index_flags=row["index_flags"],
                has_notes=bool(row["has_notes"]),
            )
            for row in rows
        ]

    async def get(self, shot_id: int) -> ShotDetailRow | None:
        row = await self.db.fetch_one(
            f"""
            SELECT {_LIST_COLUMNS},
                   s.final_exit_reason, s.brew_delay_ms, s.slog_version,
                   s.sample_interval_ms, s.fields_mask, s.index_volume_g, s.index_flags,
                   s.phases_json, s.diagnostics_json,
                   LENGTH(s.raw_slog) AS raw_bytes, s.updated_at
            {_LIST_FROM}
            WHERE s.id = ?
            """,
            (shot_id,),
        )
        return self.to_model(ShotDetailRow, row)

    async def get_many(self, shot_ids: Sequence[int]) -> list[ShotDetailRow]:
        """Several shots' detail rows in one query, in the order the ids were given.

        For the readers that render a list of shots in full — a chat's opening
        context, the shot search — where one query per shot would be twenty
        round trips to say what one could. An id with no shot is left out.
        """
        wanted = list(dict.fromkeys(int(shot_id) for shot_id in shot_ids))
        if not wanted:
            return []
        placeholders = ", ".join("?" * len(wanted))
        rows = await self.db.fetch_all(
            f"""
            SELECT {_LIST_COLUMNS},
                   s.final_exit_reason, s.brew_delay_ms, s.slog_version,
                   s.sample_interval_ms, s.fields_mask, s.index_volume_g, s.index_flags,
                   s.phases_json, s.diagnostics_json,
                   LENGTH(s.raw_slog) AS raw_bytes, s.updated_at
            {_LIST_FROM}
            WHERE s.id IN ({placeholders})
            """,
            wanted,
        )
        found = {row.id: row for row in self.to_models(ShotDetailRow, rows)}
        return [found[shot_id] for shot_id in wanted if shot_id in found]

    async def get_by_identity(self, device_id: str, start_epoch: int) -> ShotDetailRow | None:
        """The shot with this machine number *and* this start epoch.

        The only lookup that names one shot from what the machine or an export
        says about it: the number alone is reused after the machine's counter
        restarts. Unique since 0029.
        """
        row = await self.id_by_identity(device_id, start_epoch)
        return None if row is None else await self.get(row)

    async def id_by_identity(self, device_id: str, start_epoch: int) -> int | None:
        """The row id of the shot with this number and start epoch, if it is archived."""
        row = await self.db.fetch_value(
            "SELECT id FROM shots WHERE device_id = ? AND start_epoch = ?",
            (device_id, start_epoch),
        )
        return None if row is None else int(row)

    async def ids_by_device_id(self, device_id: str) -> list[int]:
        """Every archived shot that carries this machine number, oldest start first.

        More than one after the machine's counter restarted. What a caller that
        holds only a number (an export with no start time) has to be able to see
        before it decides that a shot is "the same one".
        """
        rows = await self.db.fetch_all(
            "SELECT id FROM shots WHERE device_id = ? ORDER BY start_epoch, id", (device_id,)
        )
        return [int(row["id"]) for row in rows]

    async def get_by_device_id(self, device_id: str) -> ShotDetailRow | None:
        """The *latest* shot the machine numbered this way (newest start epoch).

        For a reader that has only the number a person sees on screen; a number
        can name several archived shots, and this picks the one that can still be
        the machine's current. Nothing that decides whether two shots are the
        same shot may use it: that is :meth:`get_by_identity`.
        """
        row = await self.db.fetch_value(
            "SELECT id FROM shots WHERE device_id = ? ORDER BY start_epoch DESC, id DESC LIMIT 1",
            (device_id,),
        )
        return None if row is None else await self.get(int(row))

    async def unknown_start_shot(self, device_id: str) -> tuple[int, bytes] | None:
        """(id, stored bytes) of the shot with this number whose start time is unknown.

        Only a shot the machine still holds is offered: once it is marked gone
        its row is history and keeps the identity it has.
        """
        row = await self.db.fetch_one(
            "SELECT id, raw_slog FROM shots "
            "WHERE device_id = ? AND start_epoch = 0 AND deleted_on_device = 0",
            (device_id,),
        )
        return None if row is None else (int(row["id"]), bytes(row["raw_slog"]))

    async def adopt_start_epoch(
        self, shot_id: int, start_epoch: int, started_at: str | None
    ) -> None:
        """Give a shot whose start time was unknown the one the machine's index states.

        A shot stored unreadable before any index row listed it has no header to
        read the time from (0). Its first index row settles it, once, so the
        shot is never mistaken for a different one that later reuses its number.
        Only a 0 on a shot the machine still holds is ever replaced.
        """
        await self.db.execute(
            "UPDATE shots SET start_epoch = ?, started_at = ?, updated_at = ? "
            "WHERE id = ? AND start_epoch = 0 AND deleted_on_device = 0",
            (start_epoch, started_at, utc_now(), shot_id),
        )

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

    async def samples_for(self, shot_ids: Sequence[int]) -> dict[int, list[ShotSampleRow]]:
        """Every sample of several shots in one query, keyed by shot id.

        The same range scan as :meth:`samples`, once for the whole batch: a
        list of shots rendered with their curves would otherwise be a query
        per shot. A shot with no samples maps to an empty list.
        """
        wanted = sorted({int(shot_id) for shot_id in shot_ids})
        out: dict[int, list[ShotSampleRow]] = {shot_id: [] for shot_id in wanted}
        if not wanted:
            return out
        placeholders = ", ".join("?" * len(wanted))
        rows = await self.db.fetch_all(
            f"SELECT shot_id, {', '.join(SAMPLE_FIELDS)} FROM shot_samples "  # noqa: S608 - module constant and generated placeholders
            f"WHERE shot_id IN ({placeholders}) ORDER BY shot_id, t_ms",
            wanted,
        )
        for row in rows:
            data = dict(zip(row.keys(), tuple(row), strict=True))
            out[int(data.pop("shot_id"))].append(ShotSampleRow.model_validate(data))
        return out

    async def counts(self) -> ShotCounts:
        row = await self.db.fetch_one(
            f"""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(s.quarantined), 0) AS quarantined,
                   COALESCE(SUM(s.deleted_on_device), 0) AS deleted_on_device,
                   COALESCE(SUM(s.incomplete), 0) AS incomplete,
                   COALESCE(SUM({NEEDS_SET_SQL}), 0) AS needs_set
            FROM shots s
            """  # noqa: S608 - module constant
        )
        samples = await self.db.fetch_value("SELECT COUNT(*) FROM shot_samples")
        counts = self.to_model(ShotCounts, row)
        if counts is None:  # pragma: no cover - COUNT(*) always returns a row
            return ShotCounts()
        counts.samples = int(samples or 0)
        return counts

    async def example_shot(self) -> ExampleShotRow | None:
        """The shot a page takes its examples from; ``None`` for an empty archive.

        The newest **judged** shot — one whose judgement carries a rating or a
        decision — because that is the shot with every item on it: the newest
        shot is often the one nobody has tasted yet. Failing that the newest
        with any judgement at all (the machine's notes card can seed one that
        holds only a dose), and failing that the newest shot. A quarantined
        shot is never chosen, since its numbers are whatever the header held.
        "Newest" is the shot list's own order. ``judged`` is the first case.
        """
        row = await self.db.fetch_one(
            """
            SELECT s.id, s.started_at,
                   (j.rating IS NOT NULL OR j.decision IS NOT NULL) AS judged
            FROM shots s
            LEFT JOIN shot_judgements j ON j.shot_id = s.id
            WHERE s.quarantined = 0
            ORDER BY judged DESC, j.shot_id IS NOT NULL DESC,
                     COALESCE(s.started_at, '') DESC, s.id DESC
            LIMIT 1
            """
        )
        return self.to_model(ExampleShotRow, row)

    async def list_shots(
        self,
        *,
        limit: int = 50,
        offset: int | None = None,
        cursor: str | None = None,
        start_from: str | None = None,
        start_to: str | None = None,
        profile_version_id: int | None = None,
        set_id: int | None = None,
        set_version_id: int | None = None,
        needs_set: bool | None = None,
        quarantined: bool | None = None,
        include_deleted_on_device: bool = True,
        source: str | None = None,
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
        and a keyset over a nullable column would need a different one. Sorting
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
        if source is not None:
            where.append("s.source = ?")
            params.append(source)
        if min_rating is not None:
            where.append(f"{_RATING_KEY} >= ?")
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
        if set_id is not None:
            where.append("sv.set_id = ?")
            params.append(set_id)
        if set_version_id is not None:
            where.append("s.set_version_id = ?")
            params.append(set_version_id)
        if needs_set is not None:
            # "Which shots is the archive still waiting for an answer on?" —
            # `NEEDS_SET_SQL` says which, and why quarantined and discarded
            # shots are not among them.
            where.append(NEEDS_SET_SQL if needs_set else "s.set_version_id IS NOT NULL")
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

        if sort in (CHECK_SORT, REVIEW_SORT):
            ordered = await self._badge_sorted_ids(filters, params, descending, by=sort)
            wanted = ordered[offset or 0 :][:limit]
            page_rows = await self._list_rows_by_id(wanted)
            return ShotPage(
                items=await self._with_checks(page_rows), total=int(total or 0), next_cursor=None
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
        items = await self._with_checks(self.to_models(ShotListRow, rows))
        # A cursor is only meaningful for the default sort walked forwards:
        # handing one back to a caller paging by `offset`, or sorting by another key,
        # would let it mix two orderings and skip rows.
        next_cursor = (
            _encode_cursor(items[-1])
            if offset is None and order_key == _ORDER_KEY and descending and len(items) == limit
            else None
        )
        return ShotPage(items=items, total=int(total or 0), next_cursor=next_cursor)

    async def _list_rows_by_id(self, shot_ids: Sequence[int]) -> list[ShotListRow]:
        """List rows for these ids, in the order given."""
        if not shot_ids:
            return []
        placeholders = ", ".join("?" * len(shot_ids))
        rows = await self.db.fetch_all(
            f"SELECT {_LIST_COLUMNS} {_LIST_FROM} WHERE s.id IN ({placeholders})",
            list(shot_ids),
        )
        found = {row.id: row for row in self.to_models(ShotListRow, rows)}
        return [found[shot_id] for shot_id in shot_ids if shot_id in found]

    async def served(self, shot_ids: Sequence[int]) -> dict[int, Served]:
        """These shots as the person reads them: the Curve check and the review."""
        if not shot_ids:
            return {}
        marks = ", ".join("?" * len(shot_ids))
        return await self._served_for(f"s.id IN ({marks})", list(shot_ids))

    async def _served_for(self, where_sql: str, params: Sequence[Any]) -> dict[int, Served]:
        """Every shot the condition selects, as the person reads it: Curve check and review.

        The Curve check is the universal warnings merged with the results of the **confirmed**
        signature of the profile version each shot brewed (and its Set version's confirmed
        override); nothing proposed or rejected in a signature is ever read here. The review is
        the one in force, beside it and never mixed into it.
        """
        checks, reviewable = await self._checks_for(where_sql, params)
        readings = await ShotReviewsRepository(self.db).readings_for_shots(list(checks))
        return {
            shot_id: serve_review(
                found, readings.get(shot_id), reviewable=reviewable.get(shot_id, True)
            )
            for shot_id, found in checks.items()
        }

    async def _checks_for(
        self, where_sql: str, params: Sequence[Any]
    ) -> tuple[dict[int, ShotChecks], dict[int, bool]]:
        """The signature checks of every shot the condition selects, and whether it may be reviewed.

        A shot nobody can review is one whose bytes never parsed or that was labelled Discard.
        """
        rows = await self.db.fetch_all(
            f"SELECT {_REVIEW_COLUMNS} {_LIST_FROM} WHERE {where_sql}", list(params)
        )
        subjects: list[CheckSubject] = []
        reviewable: dict[int, bool] = {}
        for row in rows:
            reviewable[int(row["id"])] = (
                not row["quarantined"] and row["judgement_decision"] != "discard"
            )
            target = row["target_yield_g"]
            target = target if target is not None and target > 0 else None
            phases = _decoded(row["phases_json"], list)
            duration_s = (row["duration_ms"] or 0) / 1000
            scale = bool(row["scale_connected"])
            metrics = _decoded(row["metrics_json"], dict)
            subjects.append(
                CheckSubject(
                    shot_id=int(row["id"]),
                    profile_version_id=row["profile_version_id"],
                    set_version_id=row["set_version_id"],
                    warnings=shot_warnings(
                        final_weight_g=row["final_weight_g"],
                        scale_connected=scale,
                        final_exit_reason=row["final_exit_reason"] or 0,
                        duration_s=duration_s,
                        target_yield_g=target,
                        phases=phases,
                        metrics=metrics,
                    ),
                    phases=phases,
                    duration_s=duration_s,
                    scale_connected=scale,
                    final_weight_g=row["final_weight_g"],
                    target_yield_g=target,
                    dose_g=row["dose_g"],
                    has_pressure=bool(row["has_pressure"]),
                    per_phase=metrics.get("per_phase") is not False,
                    quarantined=bool(row["quarantined"]),
                    metrics=metrics,
                    revision=str(row["updated_at"]),
                )
            )
        return await checks_for_shots(self.db, subjects), reviewable

    async def _with_checks(self, rows: Sequence[ShotListRow]) -> list[ShotListItem]:
        """The page's rows with their Curve check and review, which are read, not stored."""
        if not rows:
            return []
        placeholders = ", ".join("?" * len(rows))
        served = await self._served_for(f"s.id IN ({placeholders})", [row.id for row in rows])
        items: list[ShotListItem] = []
        for row in rows:
            found = served.get(row.id)
            if found is None:  # pragma: no cover - the rows were just listed
                continue
            items.append(
                ShotListItem.model_validate(
                    {
                        **row.model_dump(),
                        "checks": found.checks_block,
                        "review": found.review,
                    }
                )
            )
        return items

    async def _badge_sorted_ids(
        self, filters: str, params: Sequence[Any], descending: bool, *, by: str
    ) -> list[int]:
        """Every matching shot's id in the Curve check column's or the Review column's order.

        Descending is the interesting end, as for every other column. What a column shows is what
        it sorts by, so the two cannot disagree. **Curve check:** the shot's first badge entry, the
        one its badge names: a failed critical expectation first, then a failed important one, an
        unexpected warning, an expected one, then a phase's entry before a whole-shot one and the
        earlier in the shot; shots with none last. **Review:** a reviewed shot's first fault by the
        same rule, then, work to do above work done, ``Failed to run``, ``Reviewing…``, ``No
        faults``, shots not reviewed, ``As intended``, and last the shots nobody can review. In
        both, shots with the same key come newest first (by start time, then id), which makes the
        key total, and ascending is the exact reverse of all of it.
        """
        served = await self._served_for(filters, params)
        started = {
            int(row["id"]): str(row["started"])
            for row in await self.db.fetch_all(
                f"SELECT s.id, COALESCE(s.started_at, '') AS started {_LIST_FROM} WHERE {filters}",
                list(params),
            )
        }
        # Two stable passes: newest first, then by the key.
        newest_first = sorted(served, key=lambda sid: (started.get(sid, ""), sid), reverse=True)
        if by == CHECK_SORT:
            ordered = sorted(newest_first, key=lambda sid: check_key(served[sid].checks))
        else:
            ordered = sorted(newest_first, key=lambda sid: review_key(served[sid]))
        return ordered if descending else ordered[::-1]


def _decoded(raw: Any, kind: type[list[Any]] | type[dict[str, Any]]) -> Any:
    """A stored JSON column as the list or object it should be, or an empty one."""
    if not isinstance(raw, str):
        return kind()
    try:
        value = json.loads(raw)
    except ValueError:
        return kind()
    return value if isinstance(value, kind) else kind()


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

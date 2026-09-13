"""`sets` and `set_versions` — what you were trying, and what you changed.

A **Set** is a stable identity: this bean, on this machine, through this
grinder. A **set version** is one concrete recipe that was actually brewed with
— a profile version, a grind, a dose, a target yield, a temperature — plus the
one sentence saying why it differs from the version before it.

Three properties follow from that split, and every method here exists to keep
one of them true:

* **versions are immutable.** Nothing updates a `set_versions` row. Changing
  anything appends a new version whose `parent_version_id` is the one it came
  from, because the product's whole question is "what did changing this do" and
  an edited row answers it with today's value for every shot ever attached.
* **one Set per machine is active at a time**, enforced by a partial unique
  index rather than by whoever remembers to clear the old flag. It is what
  auto-assignment consults when a shot lands.
* **a shot's Set is never guessed twice.** Auto-assignment only ever writes over
  a NULL, so a correction made by hand survives every later sync pass.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import SetVersionOrigin

__all__ = [
    "VERSION_FIELDS",
    "FieldChange",
    "SetRow",
    "SetTrends",
    "SetVersionPatch",
    "SetVersionRow",
    "SetVersionWrite",
    "SetWrite",
    "SetsRepository",
    "version_changes",
]


class SetWrite(BaseModel):
    """The identity half of a Set: what it is made of, not what it is set to."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    bean_id: int
    machine_id: int
    #: Nullable: pre-ground coffee and a grinder nobody has got round to
    #: recording are both real, and refusing the Set over it helps nobody.
    grinder_id: int | None = None


class SetVersionWrite(BaseModel):
    """A complete recipe. What `POST /api/sets` stores as version 1."""

    model_config = ConfigDict(extra="forbid")

    #: Which profile this Set brews with. NULL means the Set names no profile,
    #: and then auto-assignment does not filter on one — see
    #: :meth:`SetsRepository.auto_assign`.
    profile_version_id: int | None = None
    #: The grinder's own reading, as text: a Niche says "22", a Mazzer says
    #: "between 3 and 4". `grind_value` is the same thing as a number when there
    #: is one, so a chart has something to plot.
    grind_setting: str | None = Field(default=None, max_length=100)
    grind_value: float | None = Field(default=None, ge=0, le=10000)
    dose_g: float | None = Field(default=None, gt=0, le=100)
    target_yield_g: float | None = Field(default=None, gt=0, le=500)
    target_temperature_c: float | None = Field(default=None, ge=25, le=150)
    intent: str = Field(default="", max_length=500)
    origin: SetVersionOrigin = "manual"
    #: The analysis whose accepted suggestion produced this version.
    origin_analysis_id: int | None = None


class SetVersionPatch(BaseModel):
    """The body of `POST /api/sets/{id}/versions`: only what changed.

    Every field is optional *and* "not sent" is distinguishable from "sent as
    null", which is the whole point — omitting `dose_g` inherits the parent's
    dose, sending `null` clears it. That is what ``exclude_unset`` in
    :meth:`SetsRepository.add_version` reads.
    """

    model_config = ConfigDict(extra="forbid")

    profile_version_id: int | None = None
    grind_setting: str | None = Field(default=None, max_length=100)
    grind_value: float | None = Field(default=None, ge=0, le=10000)
    dose_g: float | None = Field(default=None, gt=0, le=100)
    target_yield_g: float | None = Field(default=None, gt=0, le=500)
    target_temperature_c: float | None = Field(default=None, ge=25, le=150)
    #: Not inherited, and asked for on every new version: a change with no
    #: stated intent is indistinguishable from a typo three weeks later.
    intent: str = Field(default="", max_length=500)
    origin: SetVersionOrigin = "manual"
    origin_analysis_id: int | None = None
    #: The profile id the firmware assigned when this version's profile was
    #: pushed. Not inherited by the next version: it names a file on
    #: the display, and a version that changed the grind did not push anything.
    pushed_device_profile_id: str | None = Field(default=None, max_length=31)


class SetVersionRow(BaseModel):
    """One row of `set_versions`, with the labels a reader needs beside it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_id: int
    version_no: int
    parent_version_id: int | None = None
    profile_version_id: int | None = None
    #: The profile's label, joined in. A version that names a profile the mirror
    #: has since dropped still has its id; the label is then NULL.
    profile_label: str | None = None
    grind_setting: str | None = None
    grind_value: float | None = None
    dose_g: float | None = None
    target_yield_g: float | None = None
    target_temperature_c: float | None = None
    intent: str = ""
    origin: SetVersionOrigin = "manual"
    origin_analysis_id: int | None = None
    #: Where this version's profile lives on the machine, when a draft push put
    #: it there. NULL for every version whose profile was authored on the
    #: display, and NULL again the moment somebody deletes it from there — a
    #: device id is the machine's to own.
    pushed_device_profile_id: str | None = None
    created_at: str
    shot_count: int = 0


class SetRow(BaseModel):
    """One row of `sets`, with the current version and the joined names."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    bean_id: int
    bean_name: str | None = None
    machine_id: int
    machine_name: str | None = None
    grinder_id: int | None = None
    grinder_name: str | None = None
    status: str = "active"
    #: "This is what the machine is set up for right now". At most one Set per
    #: machine holds it; archiving clears it.
    active: bool = False
    created_at: str
    current_version_id: int | None = None
    current_version_no: int = 0
    version_count: int = 0
    shot_count: int = 0
    profile_version_id: int | None = None
    profile_label: str | None = None


class FieldChange(BaseModel):
    """One difference between a version and its parent."""

    model_config = ConfigDict(extra="forbid")

    field: str
    label: str
    before: str | None = None
    after: str | None = None


class SetTrendPoint(BaseModel):
    """One shot on the Set's trend chart."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    device_id: str
    set_version_id: int
    version_no: int
    started_at: str | None = None
    execution_score: float | None = None
    duration_s: float | None = None
    #: Yield over dose, from the **judgement's** figures. Not from the device
    #: index: the index's volume is the scale's, which is only half of a ratio,
    #: and the dose only ever exists because a person typed it.
    ratio: float | None = None
    rating: int | None = None


class SetTrendVersion(BaseModel):
    """One version's averages, for the bars behind the per-shot line."""

    model_config = ConfigDict(extra="forbid")

    set_version_id: int
    version_no: int
    intent: str = ""
    origin: str = "manual"
    created_at: str
    shots: int = 0
    avg_execution_score: float | None = None
    avg_duration_s: float | None = None
    avg_ratio: float | None = None
    avg_rating: float | None = None


class SetTrends(BaseModel):
    """`GET /api/sets/{id}/trends`: the per-version summary and every shot."""

    model_config = ConfigDict(extra="forbid")

    set_id: int
    versions: list[SetTrendVersion]
    shots: list[SetTrendPoint]


#: The recipe fields, with the label the timeline shows and how to render a
#: value. One table, because the diff, the inheritance in `add_version` and the
#: UI's own wording all have to agree on what "the recipe" is.
VERSION_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("profile_version_id", "Profile", "profile"),
    ("grind_setting", "Grind", "text"),
    ("grind_value", "Grind value", "number"),
    ("dose_g", "Dose", "g"),
    ("target_yield_g", "Target yield", "g"),
    ("target_temperature_c", "Temperature", "c"),
)


def _render(value: Any, kind: str, labels: dict[int, str] | None = None) -> str | None:
    if value is None:
        return None
    if kind == "profile":
        label = (labels or {}).get(int(value))
        return label or f"#{int(value)}"
    if kind == "g":
        return f"{float(value):g} g"
    if kind == "c":
        return f"{float(value):g} °C"
    if kind == "number":
        return f"{float(value):g}"
    return str(value)


def version_changes(
    version: SetVersionRow,
    parent: SetVersionRow | None,
    profile_labels: dict[int, str] | None = None,
) -> list[FieldChange]:
    """What this version changed, against the one it came from.

    Computed rather than stored. A stored diff is a third copy of two values
    that can disagree with either, and this one is cheap: six fields compared in
    memory on a list the page already holds.

    The first version of a Set has no parent and therefore no changes — it is
    the baseline, not a change to anything, and rendering it as "six fields set"
    would bury the one version the reader actually wants to compare against.
    """
    if parent is None:
        return []
    changes: list[FieldChange] = []
    for field, label, kind in VERSION_FIELDS:
        before = getattr(parent, field)
        after = getattr(version, field)
        if before == after:
            continue
        changes.append(
            FieldChange(
                field=field,
                label=label,
                before=_render(before, kind, profile_labels),
                after=_render(after, kind, profile_labels),
            )
        )
    return changes


#: Every column of `set_versions` that a new version copies from its parent.
_INHERITED = (
    "profile_version_id",
    "grind_setting",
    "grind_value",
    "dose_g",
    "target_yield_g",
    "target_temperature_c",
)

_SET_SELECT = """
    SELECT s.*,
           b.name AS bean_name,
           m.name AS machine_name,
           g.name AS grinder_name,
           cur.id AS current_version_id,
           COALESCE(cur.version_no, 0) AS current_version_no,
           cur.profile_version_id AS profile_version_id,
           pv.label AS profile_label,
           (SELECT COUNT(*) FROM set_versions v WHERE v.set_id = s.id) AS version_count,
           (SELECT COUNT(*) FROM shots sh
             JOIN set_versions v2 ON v2.id = sh.set_version_id
            WHERE v2.set_id = s.id) AS shot_count
    FROM sets s
    JOIN beans b ON b.id = s.bean_id
    LEFT JOIN machines m ON m.id = s.machine_id
    LEFT JOIN grinders g ON g.id = s.grinder_id
    -- The current version is the highest `version_no`, not the newest row id:
    -- version numbers are the thing the user sees and the UNIQUE(set_id,
    -- version_no) index is what makes "highest" unambiguous.
    LEFT JOIN set_versions cur
           ON cur.set_id = s.id
          AND cur.version_no = (SELECT MAX(v3.version_no)
                                  FROM set_versions v3 WHERE v3.set_id = s.id)
    LEFT JOIN profile_versions pv ON pv.id = cur.profile_version_id
"""

_VERSION_SELECT = """
    SELECT v.*, pv.label AS profile_label,
           (SELECT COUNT(*) FROM shots sh WHERE sh.set_version_id = v.id) AS shot_count
    FROM set_versions v
    LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
"""


class SetsRepository(Repository):
    """Reads and writes Sets, their versions, and which shot belongs to which."""

    # ── sets ─────────────────────────────────────────────────────────

    async def create(
        self, spec: SetWrite, version: SetVersionWrite, *, activate: bool = True
    ) -> SetRow:
        """Create a Set and its first version, atomically.

        One transaction because a Set with no versions is not a Set — every
        query here joins through `set_versions`, and a half-created row would be
        invisible in the list and impossible to add a version to without special
        cases.

        ``activate`` defaults to true: a Set is created by somebody who has just
        put that bag in the hopper, and a new Set that did not start collecting
        shots would look broken. It switches the flag on the machine's previous
        Set off (the partial unique index would otherwise refuse the insert) and
        archives nothing.
        """
        now = utc_now()
        async with self.db.transaction():
            if activate:
                await self._clear_active(spec.machine_id)
            cursor = await self.db.execute(
                """
                INSERT INTO sets (name, bean_id, machine_id, grinder_id, status, active, created_at)
                VALUES (:name, :bean_id, :machine_id, :grinder_id, 'active', :active, :created_at)
                """,
                {
                    "name": spec.name,
                    "bean_id": spec.bean_id,
                    "machine_id": spec.machine_id,
                    "grinder_id": spec.grinder_id,
                    "active": int(activate),
                    "created_at": now,
                },
            )
            set_id = int(cursor.lastrowid or 0)
            await self._insert_version(set_id, 1, None, version.model_dump(), now)
        stored = await self.get(set_id)
        if stored is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("the set vanished between write and read")
        return stored

    async def get(self, set_id: int) -> SetRow | None:
        row = await self.db.fetch_one(f"{_SET_SELECT} WHERE s.id = ?", (set_id,))
        return self.to_model(SetRow, row)

    async def list_sets(
        self, *, include_archived: bool = False, machine_id: int | None = None
    ) -> list[SetRow]:
        """Every Set, the active one first and the newest after it."""
        where: list[str] = []
        params: list[Any] = []
        if not include_archived:
            where.append("s.status = 'active'")
        if machine_id is not None:
            where.append("s.machine_id = ?")
            params.append(machine_id)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = await self.db.fetch_all(
            f"{_SET_SELECT}{clause} ORDER BY s.active DESC, s.created_at DESC, s.id DESC",
            params,
        )
        return self.to_models(SetRow, rows)

    async def activate(self, set_id: int) -> SetRow | None:
        """Make this the Set the machine is currently set up for.

        Switches; archives nothing. Swapping between two bags over a week is an
        ordinary morning, and an activate that retired the other Set would make
        going back a new Set with no history.

        An **archived** Set is refused, and the refusal matters more than it
        looks: `active_version_for_machine` requires `status = 'active'`, so
        flipping the flag onto an archived Set would clear it from the live one
        and leave the machine with no usable active Set at all — every shot from
        then on landing in the inbox for no visible reason. The route turns the
        ``False`` into a 409.
        """
        row = await self.get(set_id)
        if row is None or row.status != "active":
            return None
        async with self.db.transaction():
            await self._clear_active(row.machine_id)
            await self.db.execute(
                "UPDATE sets SET active = 1 WHERE id = ? AND status = 'active'", (set_id,)
            )
        return await self.get(set_id)

    async def archive(self, set_id: int) -> SetRow | None:
        """Retire a Set. Clears `active` too — an archived Set collects no shots."""
        cursor = await self.db.execute(
            "UPDATE sets SET status = 'archived', active = 0 WHERE id = ?", (set_id,)
        )
        return None if cursor.rowcount == 0 else await self.get(set_id)

    async def _clear_active(self, machine_id: int) -> None:
        await self.db.execute(
            "UPDATE sets SET active = 0 WHERE machine_id = ? AND active = 1", (machine_id,)
        )

    # ── versions ─────────────────────────────────────────────────────

    async def add_version(self, set_id: int, patch: SetVersionPatch) -> SetVersionRow | None:
        """Append a version: the current one, with the sent fields changed.

        The parent is whatever is current *now*, read inside the transaction
        that writes the child, so two versions added at once cannot both claim
        the same parent or the same `version_no`.
        """
        sent = patch.model_dump(exclude_unset=True)
        async with self.db.transaction():
            parent = await self._current_version_row(set_id)
            if parent is None:
                return None
            values = {field: getattr(parent, field) for field in _INHERITED}
            for field in _INHERITED:
                if field in sent:
                    values[field] = sent[field]
            values["intent"] = patch.intent
            values["origin"] = patch.origin
            values["origin_analysis_id"] = patch.origin_analysis_id
            values["pushed_device_profile_id"] = patch.pushed_device_profile_id
            version_id = await self._insert_version(
                set_id, parent.version_no + 1, parent.id, values, utc_now()
            )
        return await self.get_version(version_id)

    async def _insert_version(
        self,
        set_id: int,
        version_no: int,
        parent_version_id: int | None,
        values: dict[str, Any],
        now: str,
    ) -> int:
        payload: dict[str, Any] = {
            "set_id": set_id,
            "version_no": version_no,
            "parent_version_id": parent_version_id,
            "intent": values.get("intent", ""),
            "origin": values.get("origin", "manual"),
            "origin_analysis_id": values.get("origin_analysis_id"),
            "pushed_device_profile_id": values.get("pushed_device_profile_id"),
            "created_at": now,
        }
        for field in _INHERITED:
            payload[field] = values.get(field)
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{name}" for name in payload)
        cursor = await self.db.execute(
            f"INSERT INTO set_versions ({columns}) VALUES ({placeholders})",  # noqa: S608 - keys are the literal payload above
            payload,
        )
        return int(cursor.lastrowid or 0)

    async def clear_pushed_device_profile(self, device_id: str) -> int:
        """Forget a device id every Set version that names it. Returns the count.

        Called when a rollback deletes the machine's copy. The version keeps its
        `profile_version_id` — what it brewed is a historical fact and does not
        change — but it must stop naming a file that is not there, or a later
        "select the profile this Set wants" would select whatever inherits that
        id next.
        """
        if not device_id:
            return 0
        cursor = await self.db.execute(
            "UPDATE set_versions SET pushed_device_profile_id = NULL "
            "WHERE pushed_device_profile_id = ?",
            (device_id,),
        )
        return cursor.rowcount

    async def get_version(self, version_id: int) -> SetVersionRow | None:
        row = await self.db.fetch_one(f"{_VERSION_SELECT} WHERE v.id = ?", (version_id,))
        return self.to_model(SetVersionRow, row)

    async def versions(self, set_id: int) -> list[SetVersionRow]:
        """Every version of a Set, newest first — the order the timeline reads in."""
        rows = await self.db.fetch_all(
            f"{_VERSION_SELECT} WHERE v.set_id = ? ORDER BY v.version_no DESC", (set_id,)
        )
        return self.to_models(SetVersionRow, rows)

    async def _current_version_row(self, set_id: int) -> SetVersionRow | None:
        row = await self.db.fetch_one(
            f"{_VERSION_SELECT} WHERE v.set_id = ? ORDER BY v.version_no DESC LIMIT 1", (set_id,)
        )
        return self.to_model(SetVersionRow, row)

    async def current_version(self, set_id: int) -> SetVersionRow | None:
        """The version a shot pulled right now would be attached to."""
        return await self._current_version_row(set_id)

    async def active_version_for_machine(self, machine_id: int) -> SetVersionRow | None:
        """The current version of the machine's active Set, if it has one.

        The one query auto-assignment runs per ingested shot: an index seek on
        `idx_sets_one_active_per_machine` and one more on the version.
        """
        row = await self.db.fetch_one(
            f"""
            {_VERSION_SELECT}
            JOIN sets s ON s.id = v.set_id
            WHERE s.machine_id = ? AND s.active = 1 AND s.status = 'active'
            ORDER BY v.version_no DESC LIMIT 1
            """,
            (machine_id,),
        )
        return self.to_model(SetVersionRow, row)

    # ── assignment ───────────────────────────────────────────────────

    async def assign_shot(self, shot_id: int, version_id: int | None) -> bool:
        """Attach a shot to a Set version, or detach it. A person's decision.

        Unlike :meth:`auto_assign` this overwrites whatever was there: it is the
        correction path, and the whole point of it is to fix a wrong guess.
        Returns False when the shot or the version does not exist — `shots`
        carries no foreign key on this column (see migration 0005), so this
        method is where the reference is checked.
        """
        if version_id is not None:
            # Not merely "does the version exist": a version of an *archived*
            # Set is refused too. Archiving is how a Set stops collecting shots,
            # and a hand assignment that could still put one there would be the
            # one path around it.
            exists = await self.db.fetch_value(
                """
                SELECT 1 FROM set_versions v
                JOIN sets s ON s.id = v.set_id
                WHERE v.id = ? AND s.status = 'active'
                """,
                (version_id,),
            )
            if exists is None:
                return False
        cursor = await self.db.execute(
            "UPDATE shots SET set_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, utc_now(), shot_id),
        )
        return cursor.rowcount > 0

    async def auto_assign(
        self,
        shot_id: int,
        *,
        machine_id: int,
        profile_version_id: int | None,
        device_profile_id: str,
    ) -> int | None:
        """Attach a freshly stored shot to the machine's active Set, if it fits.

        Returns the version id it was attached to, or ``None`` for "needs a Set".

        The match is on the **profile**, because that is the only thing the
        machine records that the Set also states. Two ways it can succeed:

        * the shot resolved to a profile version and the Set version names the
          same one; or
        * the shot has no linked version yet — the profile mirror has not caught
          up, which is ordinary on a first boot — and the device profile id it
          was brewed with currently maps to the version the Set names. A
          tombstoned mapping (`deleted_at`) does not count: the id has been
          freed on the machine and may already point at a different profile.

        A Set version that names **no** profile does not filter on one: a Set
        created without picking a profile is a Set that does not care which was
        selected, and leaving every shot unassigned would make that Set look
        broken rather than permissive.

        Anything else is left NULL, which is the `needs_set` state. Guessing
        wrong here is worse than not guessing: a mis-assigned shot pollutes the
        trend chart of a Set it was never part of, and nobody goes looking for
        it, whereas an unassigned shot is on a list with a button next to it.

        Never touches a shot that already has a version — the `IS NULL` in the
        UPDATE — so a hand correction survives every later pass.
        """
        version = await self.active_version_for_machine(machine_id)
        if version is None:
            return None
        if version.profile_version_id is not None:
            if profile_version_id is not None:
                if profile_version_id != version.profile_version_id:
                    return None
            elif device_profile_id:
                mapped = await self.db.fetch_value(
                    """
                    SELECT current_version_id FROM device_profiles
                    WHERE machine_id = ? AND device_id = ? AND deleted_at IS NULL
                    """,
                    (machine_id, device_profile_id),
                )
                if mapped is None or int(mapped) != version.profile_version_id:
                    return None
            else:
                return None
        cursor = await self.db.execute(
            """
            UPDATE shots SET set_version_id = ?, updated_at = ?
            WHERE id = ? AND set_version_id IS NULL
            """,
            (version.id, utc_now(), shot_id),
        )
        return version.id if cursor.rowcount > 0 else None

    # ── trends ───────────────────────────────────────────────────────

    async def trends(self, set_id: int) -> SetTrends:
        """Every shot in the Set, in order, plus the per-version averages.

        One query for the shots and the averages folded in Python: the point
        series is what the chart draws, the bars are the same numbers grouped,
        and computing them twice in SQL would let a rounding difference put a
        bar off its own points.

        The ratio comes from the *judgement's* doses. The index's volume is only
        half of a ratio — the dose exists nowhere on the machine unless somebody
        typed it into its notes card — so a shot with no dose has no ratio, and
        saying so is more useful than inventing one from the nominal basket size.
        """
        rows = await self.db.fetch_all(
            """
            SELECT sh.id AS shot_id, sh.device_id, sh.set_version_id, v.version_no,
                   sh.started_at, sh.execution_score, sh.duration_ms,
                   j.dose_in_g, j.dose_out_g, j.rating,
                   COALESCE(sh.final_weight_g, sh.index_volume_g) AS volume_g
            FROM shots sh
            JOIN set_versions v ON v.id = sh.set_version_id
            LEFT JOIN shot_judgements j ON j.shot_id = sh.id
            WHERE v.set_id = ?
            ORDER BY v.version_no, COALESCE(sh.started_at, ''), sh.id
            """,
            (set_id,),
        )
        points: list[SetTrendPoint] = []
        for row in rows:
            dose_in = row["dose_in_g"]
            # The judgement's own yield when it has one, the scale's otherwise:
            # the dose is the half that only a person can supply, and once it is
            # there the machine's measured yield is the better numerator.
            dose_out = row["dose_out_g"] or row["volume_g"]
            points.append(
                SetTrendPoint(
                    shot_id=int(row["shot_id"]),
                    device_id=str(row["device_id"]),
                    set_version_id=int(row["set_version_id"]),
                    version_no=int(row["version_no"]),
                    started_at=row["started_at"],
                    execution_score=row["execution_score"],
                    duration_s=None if row["duration_ms"] is None else row["duration_ms"] / 1000,
                    ratio=(
                        round(float(dose_out) / float(dose_in), 2) if dose_in and dose_out else None
                    ),
                    rating=row["rating"],
                )
            )

        versions = await self.versions(set_id)
        by_version = {
            version.id: [p for p in points if p.set_version_id == version.id]
            for version in versions
        }
        summaries = [
            SetTrendVersion(
                set_version_id=version.id,
                version_no=version.version_no,
                intent=version.intent,
                origin=version.origin,
                created_at=version.created_at,
                shots=len(by_version[version.id]),
                avg_execution_score=_mean([p.execution_score for p in by_version[version.id]]),
                avg_duration_s=_mean([p.duration_s for p in by_version[version.id]]),
                avg_ratio=_mean([p.ratio for p in by_version[version.id]]),
                avg_rating=_mean([p.rating for p in by_version[version.id]]),
            )
            # Oldest first: a trend reads left to right.
            for version in sorted(versions, key=lambda v: v.version_no)
        ]
        return SetTrends(set_id=set_id, versions=summaries, shots=points)


def _mean(values: list[float | None] | list[int | None]) -> float | None:
    """The mean of the values that exist, or ``None`` when none of them do.

    ``None`` rather than 0: a version whose shots all predate the scoring pass
    has no average score, and a zero would draw a bar at the bottom of the chart
    saying the recipe was terrible.
    """
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 3)

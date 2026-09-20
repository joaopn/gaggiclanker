"""`sets` and `set_versions` — what you were trying, and what you changed.

A **Set** is a stable identity: this bean, on the machine, through this
grinder. A **set version** is one concrete recipe that was actually brewed with
— a profile version, a grind, a dose, a target yield — plus the one sentence
saying why it differs from the version before it. The brew temperature is not
among them: the machine heats to what the profile says, so it is read from the
profile this version names and changing it means changing the profile.

Four properties follow from that split, and every method here exists to keep
one of them true:

* **the recipe is immutable.** Nothing updates the recipe half of a
  `set_versions` row. Changing anything appends a new version whose
  `parent_version_id` is the one it came from, because the product's whole
  question is "what did changing this do" and an edited row answers it with
  today's value for every shot ever attached.
* **a prediction is written before the shots or not at all.** What a version was
  expected to do differently can be typed and re-typed while the version has no
  shots, and is refused afterwards: a prediction written once the cup has been
  tasted is a memory, not a prediction. It is refused again once a grade has
  been recorded, which the grade has to be taken back first. The **outcome** —
  somebody's grade of that prediction — is the opposite: it is only recordable
  once there is something to grade, and it can be changed or taken back at any
  time.

  Both windows live here, in :meth:`SetsRepository.set_prediction` and
  :meth:`SetsRepository.set_outcome`, beside the other rules that depend on
  another table's rows. A trigger could enforce the first one, and that is
  exactly why it does not: the rule would then exist in two places with two
  wordings, the refusal would arrive as a constraint failure rather than as an
  error code a route can turn into a sentence, and a test of it would have to go
  through SQL rather than through the method everything else calls.

  The window is honest, not airtight: somebody who unfiles every shot from a
  version and clears its grade can write a fresh prediction on it. That is
  accepted. The rule guards against the habit of writing a prediction down after
  the fact, not against somebody setting out to deceive themselves.
* **one Set is active at a time**, enforced by a partial unique index rather
  than by whoever remembers to clear the old flag. It is what auto-assignment
  consults when a shot lands.
* **a shot's Set is never guessed twice.** Auto-assignment only ever writes over
  a NULL, so a correction made by hand survives every later sync pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.spread import CountedShot
from gaggiclanker.domain.vocab import (
    VERSION_OUTCOMES,
    OutcomeState,
    SetVersionOrigin,
    VersionOutcome,
)

__all__ = [
    "VERSION_FIELDS",
    "FieldChange",
    "RollbackWrite",
    "SetRow",
    "SetTrackRecord",
    "SetTrends",
    "SetVersionPatch",
    "SetVersionRow",
    "SetVersionWrite",
    "SetWrite",
    "SetsRepository",
    "VersionLabelCounts",
    "VersionLink",
    "VersionNode",
    "VersionOutcomeWrite",
    "VersionPredictionWrite",
    "VersionRefusal",
    "VersionWriteResult",
    "dead_end_ids",
    "track_record",
    "version_changes",
]

#: How long a prediction or an outcome note may be. Generous for a sentence or
#: three and short of an essay: this is read beside a shot, not filed.
TEXT_MAX = 1000

#: A prediction or an outcome note as it is accepted: stripped, then capped.
#: Stripped on the model rather than at each call site, so a field somebody left
#: as three spaces is "no prediction" everywhere at once — including in
#: `outcome_state`, which asks whether the text is empty.
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=TEXT_MAX)]


class SetWrite(BaseModel):
    """The identity half of a Set: what it is made of, not what it is set to."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    bean_id: int
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
    intent: str = Field(default="", max_length=500)
    #: What this version is expected to do differently. On a Set's first version
    #: there is nothing earlier to compare against, so there is no
    #: `compares_to_version_id` here: the prediction is graded against the
    #: numbers this version itself states.
    prediction: LongText = ""
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
    #: Not inherited, and asked for on every new version: a change with no
    #: stated intent is indistinguishable from a typo three weeks later.
    intent: str = Field(default="", max_length=500)
    #: Not inherited either, and for a stronger reason: a prediction belongs to
    #: one version's one change, and carrying the last one forward would put a
    #: guess nobody made on the record.
    prediction: LongText = ""
    #: Which version the prediction is measured against. **Omitted** it is the
    #: parent — the version this one was changed from, which is what "less
    #: bitter than before" means nine times in ten. Sent as **null** it is
    #: nothing: the prediction is graded on the numbers this version states.
    #: The two are told apart by ``exclude_unset``, like every other field here.
    compares_to_version_id: int | None = None
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
    #: The brew temperature, read from the profile this version names rather
    #: than typed on the Set: the machine heats to what the document says, so a
    #: number stored here could only ever disagree with the cup. NULL when the
    #: version names no profile, or when the profile states none (the firmware
    #: writes 0 for "not set"). Read-only — changing it means changing the
    #: profile.
    profile_temperature_c: float | None = None
    intent: str = ""
    origin: SetVersionOrigin = "manual"
    origin_analysis_id: int | None = None
    #: Where this version's profile lives on the machine, when a draft push put
    #: it there. NULL for every version whose profile was authored on the
    #: display, and NULL again the moment somebody deletes it from there — a
    #: device id is the machine's to own.
    pushed_device_profile_id: str | None = None
    #: What this version was expected to do differently, in the person's words.
    #: Empty is the common case and means exactly that: nobody committed to a
    #: guess, so there is nothing here to be right or wrong about.
    prediction: str = ""
    compares_to_version_id: int | None = None
    #: The compared-to version's number, joined in. A reader — and a model
    #: reading the curated view — thinks in "v3", never in a row id.
    compares_to_version_no: int | None = None
    #: The version whose recipe this one restores, when it came from a roll
    #: back, and its number beside it.
    restores_version_id: int | None = None
    restores_version_no: int | None = None
    prediction_at: str | None = None
    outcome: VersionOutcome | None = None
    outcome_note: str = ""
    outcome_at: str | None = None
    created_at: str
    shot_count: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def outcome_state(self) -> OutcomeState:
        """What the experiment log shows for this version.

        Derived rather than stored, because it is two columns read together and
        a stored copy would be a third value that can disagree with both. The
        distinction that matters is between "nobody predicted anything" and
        "somebody predicted something and has not said how it went": the first
        is not a gap in the record, the second is.
        """
        if not self.prediction:
            return "no_prediction"
        return self.outcome or "open"


class VersionPredictionWrite(BaseModel):
    """`PATCH .../versions/{id}/prediction`: the guess, and what it is against.

    ``prediction`` is **required**, with no default: an empty string is how one
    is taken back, and that has to be something a caller says rather than
    something a caller omits. A body with neither is a mistake, not a removal,
    and it is answered as one. The compared-to version and the timestamp go with
    the text when it goes, because a comparison with nothing to compare would be
    a dangling reference nobody can read.

    ``compares_to_version_id`` follows :class:`SetVersionPatch`: omitted is the
    parent, an explicit null is "nothing to compare against".
    """

    model_config = ConfigDict(extra="forbid")

    prediction: LongText
    compares_to_version_id: int | None = None


class VersionOutcomeWrite(BaseModel):
    """`PUT .../versions/{id}/outcome`: the grade, and why it was given."""

    model_config = ConfigDict(extra="forbid")

    outcome: VersionOutcome
    note: LongText = ""


class RollbackWrite(BaseModel):
    """`POST /api/sets/{id}/rollback`: go back to a recipe that worked.

    Nothing is written to the machine by this, ever. A roll back is a statement
    about the archive — "this is what I am brewing again" — and if the restored
    version names a different profile the log says so exactly as it does for any
    other version that changes one.
    """

    model_config = ConfigDict(extra="forbid")

    to_version_id: int
    intent: str = Field(default="", max_length=500)
    prediction: LongText = ""


class VersionLabelCounts(BaseModel):
    """How the shots on one version were labelled, for the log's one line."""

    model_config = ConfigDict(extra="forbid")

    keep: int = 0
    improve: int = 0
    discard: int = 0
    #: Shots on this version with no decision yet — judged or not.
    unlabelled: int = 0


class SetTrackRecord(BaseModel):
    """How often this Set's predictions turned out right.

    ``graded`` is the four recorded outcomes together, which is the denominator
    of the sentence the page leads with. The un-graded two are counted as well
    rather than folded away: "four of six held" reads very differently beside
    "and nine versions predicted nothing".
    """

    model_config = ConfigDict(extra="forbid")

    no_prediction: int = 0
    open: int = 0
    held: int = 0
    partly_held: int = 0
    failed: int = 0
    inconclusive: int = 0
    graded: int = 0


#: Why a guarded write to a version was refused. A slug rather than an HTTP
#: status: the repository has no opinion about statuses, and the route that
#: does is the one place the mapping is written down.
type VersionRefusal = Literal[
    "no_version",
    "has_shots",
    "has_outcome",
    "bad_compare",
    "no_prediction",
    "nothing_to_grade",
    "no_target",
    "current_version",
]


@dataclass(frozen=True, slots=True)
class VersionWriteResult:
    """A guarded write: the row it produced, or the reason there is none.

    Deliberately not an exception. Every refusal here is an ordinary answer to
    an ordinary request — the version already has shots, the prediction it would
    grade does not exist — and a route turns each one into its own status and
    message. Exactly one of the two fields is set.
    """

    version: SetVersionRow | None = None
    refused: VersionRefusal | None = None


class SetRow(BaseModel):
    """One row of `sets`, with the current version and the joined names."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    bean_id: int
    bean_name: str | None = None
    grinder_id: int | None = None
    grinder_name: str | None = None
    status: str = "active"
    #: "This is what the machine is set up for right now". At most one Set holds
    #: it; archiving clears it.
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
    #: True for a change nobody typed on the Set: it came with the profile this
    #: version switched to. The log says so, because "Temperature 93 → 94 °C"
    #: with no such field on the form reads as a bug otherwise.
    from_profile: bool = False


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
    that can disagree with either, and this one is cheap: five fields compared
    in memory on a list the page already holds.

    The first version of a Set has no parent and therefore no changes — it is
    the baseline, not a change to anything, and rendering it as "five fields
    set" would bury the one version the reader actually wants to compare
    against.

    The temperature is the sixth line the log can show and the one nobody typed:
    it rides along with the profile. A version that switched profiles changed
    the brew temperature too whenever the two documents state different ones,
    and that is the change the reader is looking for — "one degree hotter" is
    the profile's doing, so it is only ever reported beside the profile change
    that caused it.
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
    if (
        version.profile_version_id != parent.profile_version_id
        and version.profile_temperature_c != parent.profile_temperature_c
    ):
        changes.append(
            FieldChange(
                field="profile_temperature_c",
                label="Temperature",
                before=_render(parent.profile_temperature_c, "c"),
                after=_render(version.profile_temperature_c, "c"),
                from_profile=True,
            )
        )
    return changes


def _prediction_values(prediction: str, compares_to: int | None, *, now: str) -> dict[str, Any]:
    """The three prediction columns, which are written and cleared together.

    An empty prediction takes its comparison and its timestamp with it: a
    `compares_to_version_id` with no prediction beside it is a reference to a
    version nothing says anything about, and the log would have to render it as
    something.

    ``compares_to`` is already resolved by the caller, because only the caller
    can tell "the field was not sent" (inherit the parent) from "the field was
    sent as null" (compare against nothing, and grade the version on the numbers
    it states). Defaulting here would make the two indistinguishable, which is
    the bug this signature exists to prevent.
    """
    if not prediction:
        return {"prediction": "", "compares_to_version_id": None, "prediction_at": None}
    return {
        "prediction": prediction,
        "compares_to_version_id": compares_to,
        "prediction_at": now,
    }


class VersionNode(Protocol):
    """What walking a Set's line needs off a version, and nothing else.

    A structural type rather than :class:`SetVersionRow` because two callers
    want two different rows: the Set page holds the full version and the Chat
    page's folders want four columns for several Sets at once. One walk, either
    row — the alternative is a second copy of the rule, and a dead end the log
    shows and the folder does not is exactly the disagreement that would follow.
    """

    id: int
    version_no: int
    parent_version_id: int | None
    restores_version_id: int | None


class VersionLink(BaseModel):
    """One version as the line walk reads it, for many Sets in one query."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_id: int
    version_no: int
    parent_version_id: int | None = None
    restores_version_id: int | None = None


def live_line(versions: Sequence[VersionNode]) -> list[int]:
    """The versions still on the line being brewed, newest first.

    Walked back from the current version rather than filtered: a version is on
    the line if you can reach it by stepping backwards from where the Set is
    now. From a version that **restores** an earlier one, the step goes to what
    it restored — everything in between was stepped over. From any other, it
    goes to its parent.

    Reaching an id twice would be a cycle in data that should have none, and the
    walk stops rather than spinning: a malformed row must not hang the Set page.
    """
    by_id = {version.id: version for version in versions}
    node = max(versions, key=lambda version: version.version_no, default=None)
    line: list[int] = []
    seen: set[int] = set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        line.append(node.id)
        node = by_id.get(node.restores_version_id or node.parent_version_id or 0)
    return line


def dead_end_ids(versions: Sequence[VersionNode]) -> set[int]:
    """The versions that are not on the live line.

    A roll back from v5 to v3 appends v6 whose recipe is v3's and says so in
    `restores_version_id`. What that makes v4 and v5 is not "wrong" — they were
    real attempts — but a branch nobody is on any more, and the log mutes them
    so the line a reader follows is the one still being brewed.

    "Off the line" rather than "between the roll back and its target", because
    the two stop agreeing as soon as roll backs overlap: with v5 restoring v2,
    v6 restoring v4 and v7 restoring v3, the live line is v7, v3, v2, v1 and v6
    is a dead end even though nothing later spans it. Walking the line is the
    definition; a span test is an approximation of it that happens to be right
    for one roll back.

    Pure, over the list the page already holds.
    """
    live = set(live_line(versions))
    return {version.id for version in versions if version.id not in live}


def track_record(versions: Sequence[SetVersionRow]) -> SetTrackRecord:
    """How this Set's predictions have gone, counted by state.

    ``graded`` is every state that is a grade, derived from the vocabulary
    rather than from a list written out here: an outcome added to
    `VersionOutcome` and forgotten in this sum would quietly shrink the
    denominator of the only number the Set page leads with.
    """
    counts: dict[str, int] = dict.fromkeys(SetTrackRecord.model_fields, 0)
    for version in versions:
        counts[version.outcome_state] += 1
    counts["graded"] = sum(counts[outcome] for outcome in VERSION_OUTCOMES)
    return SetTrackRecord(**counts)


#: Every column of `set_versions` that a new version copies from its parent.
_INHERITED = (
    "profile_version_id",
    "grind_setting",
    "grind_value",
    "dose_g",
    "target_yield_g",
)

_SET_SELECT = """
    SELECT s.*,
           b.name AS bean_name,
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

#: The two self-joins resolve a row id into the version *number* a reader sees.
#: Joined rather than looked up by the caller, because the shot page is handed
#: one version and has no list to resolve it against.
_VERSION_SELECT = """
    SELECT v.*, pv.label AS profile_label,
           -- The brew temperature, out of the profile's own document: the
           -- firmware's 0 means "not set", which is the same rule
           -- `profile_recipe` applies to the same field. Read here rather than
           -- by loading every profile document into the process, because a Set
           -- page shows a dozen versions and only ever wants this one number.
           CASE WHEN json_type(pv.json, '$.temperature') IN ('integer', 'real')
                 AND json_extract(pv.json, '$.temperature') > 0
                THEN json_extract(pv.json, '$.temperature') END AS profile_temperature_c,
           cmp.version_no AS compares_to_version_no,
           res.version_no AS restores_version_no,
           (SELECT COUNT(*) FROM shots sh WHERE sh.set_version_id = v.id) AS shot_count
    FROM set_versions v
    LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
    LEFT JOIN set_versions cmp ON cmp.id = v.compares_to_version_id
    LEFT JOIN set_versions res ON res.id = v.restores_version_id
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
        shots would look broken. It switches the flag on the previous active Set
        off (the partial unique index would otherwise refuse the insert) and
        archives nothing.
        """
        now = utc_now()
        async with self.db.transaction():
            if activate:
                await self._clear_active()
            cursor = await self.db.execute(
                """
                INSERT INTO sets (name, bean_id, grinder_id, status, active, created_at)
                VALUES (:name, :bean_id, :grinder_id, 'active', :active, :created_at)
                """,
                {
                    "name": spec.name,
                    "bean_id": spec.bean_id,
                    "grinder_id": spec.grinder_id,
                    "active": int(activate),
                    "created_at": now,
                },
            )
            set_id = int(cursor.lastrowid or 0)
            values = version.model_dump()
            # Version 1 has no parent, so a prediction on it compares against
            # nothing: it is graded against the numbers the version states.
            values.update(_prediction_values(version.prediction, None, now=now))
            await self._insert_version(set_id, 1, None, values, now)
        stored = await self.get(set_id)
        if stored is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("the set vanished between write and read")
        return stored

    async def get(self, set_id: int) -> SetRow | None:
        row = await self.db.fetch_one(f"{_SET_SELECT} WHERE s.id = ?", (set_id,))
        return self.to_model(SetRow, row)

    async def list_sets(self, *, include_archived: bool = False) -> list[SetRow]:
        """Every Set, the active one first and the newest after it."""
        where: list[str] = []
        params: list[Any] = []
        if not include_archived:
            where.append("s.status = 'active'")
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
        looks: `active_version` requires `status = 'active'`, so
        flipping the flag onto an archived Set would clear it from the live one
        and leave the machine with no usable active Set at all — every shot from
        then on landing in the inbox for no visible reason. The route turns the
        ``False`` into a 409.
        """
        row = await self.get(set_id)
        if row is None or row.status != "active":
            return None
        async with self.db.transaction():
            await self._clear_active()
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

    async def _clear_active(self) -> None:
        await self.db.execute("UPDATE sets SET active = 0 WHERE active = 1")

    # ── versions ─────────────────────────────────────────────────────

    async def add_version(self, set_id: int, patch: SetVersionPatch) -> SetVersionRow | None:
        """Append a version: the current one, with the sent fields changed.

        The parent is whatever is current *now*, read inside the transaction
        that writes the child, so two versions added at once cannot both claim
        the same parent or the same `version_no`.
        """
        sent = patch.model_dump(exclude_unset=True)
        now = utc_now()
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
            # Omitted is "against the version I changed from"; an explicit null
            # is "against nothing". `sent` is what tells them apart.
            compares_to = (
                sent["compares_to_version_id"] if "compares_to_version_id" in sent else parent.id
            )
            values.update(_prediction_values(patch.prediction, compares_to, now=now))
            version_id = await self._insert_version(
                set_id, parent.version_no + 1, parent.id, values, now
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
            "prediction": values.get("prediction", ""),
            "compares_to_version_id": values.get("compares_to_version_id"),
            "restores_version_id": values.get("restores_version_id"),
            "prediction_at": values.get("prediction_at"),
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

    # ── the prediction, the outcome and the roll back ─────────────────
    #
    # Three guarded writes. Each reads the version inside the transaction it
    # writes in, because every guard here is about state that another request
    # can change: a shot lands, a judgement is typed, a version is added.

    async def set_prediction(
        self, set_id: int, version_id: int, spec: VersionPredictionWrite
    ) -> VersionWriteResult:
        """Write what this version is expected to do differently.

        Refused once the version has a shot, and again once its prediction has
        been graded. A prediction typed after the cup was tasted grades itself,
        and an archive that let one in would report a track record nobody could
        trust — which is the only number on the Set page worth reading.

        Not airtight, and deliberately so: unfiling every shot and clearing the
        grade opens the window again. The rule is against the habit, not against
        somebody determined to rewrite their own record.
        """
        now = utc_now()
        async with self.db.transaction():
            version = await self.version_of_set(set_id, version_id)
            if version is None:
                return VersionWriteResult(refused="no_version")
            if version.shot_count > 0:
                return VersionWriteResult(refused="has_shots")
            # A grade already given is a statement about the prediction as it
            # was written. Rewriting the prediction underneath it would leave
            # the grade attached to something nobody ever predicted, so the
            # grade comes off first, deliberately.
            if version.outcome is not None:
                return VersionWriteResult(refused="has_outcome")
            compares_to = (
                spec.compares_to_version_id
                if "compares_to_version_id" in spec.model_fields_set
                else version.parent_version_id
            )
            if spec.prediction and compares_to is not None:
                if await self.comparison_target(set_id, version.version_no, compares_to) is None:
                    return VersionWriteResult(refused="bad_compare")
            values = _prediction_values(spec.prediction, compares_to, now=now)
            await self.db.execute(
                """
                UPDATE set_versions
                   SET prediction = :prediction,
                       compares_to_version_id = :compares_to_version_id,
                       prediction_at = :prediction_at
                 WHERE id = :id
                """,
                {**values, "id": version_id},
            )
        return VersionWriteResult(version=await self.get_version(version_id))

    async def set_outcome(
        self, set_id: int, version_id: int, spec: VersionOutcomeWrite
    ) -> VersionWriteResult:
        """Grade this version's prediction. Changeable, and never automatic.

        Two things have to exist before there is a grade to give: a prediction,
        and a shot somebody has actually formed a view about. "Judged,
        non-discarded" means a decision of keep or improve — a discarded shot
        says the shot went wrong, not that the recipe did, and grading a
        prediction on one would be reading the wrong signal.
        """
        now = utc_now()
        async with self.db.transaction():
            version = await self.version_of_set(set_id, version_id)
            if version is None:
                return VersionWriteResult(refused="no_version")
            if not version.prediction:
                return VersionWriteResult(refused="no_prediction")
            if not await self._has_gradable_shot(version_id):
                return VersionWriteResult(refused="nothing_to_grade")
            await self.db.execute(
                """
                UPDATE set_versions
                   SET outcome = :outcome, outcome_note = :note, outcome_at = :now
                 WHERE id = :id
                """,
                {"outcome": spec.outcome, "note": spec.note, "now": now, "id": version_id},
            )
        return VersionWriteResult(version=await self.get_version(version_id))

    async def clear_outcome(self, set_id: int, version_id: int) -> VersionWriteResult:
        """Take a grade back. Always allowed: second thoughts are ordinary."""
        async with self.db.transaction():
            version = await self.version_of_set(set_id, version_id)
            if version is None:
                return VersionWriteResult(refused="no_version")
            await self.db.execute(
                "UPDATE set_versions SET outcome = NULL, outcome_note = '', outcome_at = NULL "
                "WHERE id = ?",
                (version_id,),
            )
        return VersionWriteResult(version=await self.get_version(version_id))

    async def rollback(self, set_id: int, spec: RollbackWrite) -> VersionWriteResult:
        """Append a version whose recipe is an earlier one's.

        A roll back is an ordinary new version with two extra facts on it:
        `restores_version_id` names the recipe it copied, and
        `parent_version_id` is still whatever was current — so the diff the log
        draws is the reversal, field by field, rather than an empty entry that
        only says "went back".

        `pushed_device_profile_id` is not copied, exactly as it is not on any
        other new version: it names a file on the display, and nothing here
        writes to the machine.
        """
        now = utc_now()
        async with self.db.transaction():
            current = await self._current_version_row(set_id)
            if current is None:
                return VersionWriteResult(refused="no_version")
            target = await self.version_of_set(set_id, spec.to_version_id)
            if target is None:
                return VersionWriteResult(refused="no_target")
            if target.id == current.id:
                return VersionWriteResult(refused="current_version")
            values: dict[str, Any] = {field: getattr(target, field) for field in _INHERITED}
            values["intent"] = spec.intent
            values["origin"] = "manual"
            values["restores_version_id"] = target.id
            values.update(_prediction_values(spec.prediction, current.id, now=now))
            version_id = await self._insert_version(
                set_id, current.version_no + 1, current.id, values, now
            )
        return VersionWriteResult(version=await self.get_version(version_id))

    async def comparison_target(
        self, set_id: int, version_no: int, compares_to: int
    ) -> SetVersionRow | None:
        """The version a prediction on `version_no` may name, or ``None``.

        Two conditions, in one place because both routes that accept a
        comparison have to agree on them:

        * **the same Set.** Two Sets are two coffees, and "less bitter than v2"
          across them compares nothing anybody brewed.
        * **older.** A prediction reads "compared to vN", and a vN that did not
          exist yet is not something this version could have been expected to
          improve on. This also rules out a version against itself, which is
          the degenerate case of the same mistake.

        `POST /versions` calls :meth:`version_of_set` instead, because there the
        version does not exist yet and every candidate is older by construction.
        """
        target = await self.version_of_set(set_id, compares_to)
        if target is None or target.version_no >= version_no:
            return None
        return target

    async def version_of_set(self, set_id: int, version_id: int) -> SetVersionRow | None:
        """A version, but only if it belongs to the Set the route named.

        Public because the route that accepts a compared-to version on a *new*
        version needs the same check, and two copies of "is this id one of this
        Set's" is how they come to disagree.
        """
        row = await self.db.fetch_one(
            f"{_VERSION_SELECT} WHERE v.id = ? AND v.set_id = ?", (version_id, set_id)
        )
        return self.to_model(SetVersionRow, row)

    async def _has_gradable_shot(self, version_id: int) -> bool:
        found = await self.db.fetch_value(
            """
            SELECT 1 FROM shots s
            JOIN shot_judgements j ON j.shot_id = s.id
            WHERE s.set_version_id = ? AND j.decision IN ('keep', 'improve')
            LIMIT 1
            """,
            (version_id,),
        )
        return found is not None

    async def label_counts(self, set_id: int) -> dict[int, VersionLabelCounts]:
        """How every version's shots were labelled, in one grouped pass."""
        rows = await self.db.fetch_all(
            """
            SELECT sh.set_version_id AS version_id,
                   SUM(j.decision = 'keep') AS keep,
                   SUM(j.decision = 'improve') AS improve,
                   SUM(j.decision = 'discard') AS discard,
                   SUM(j.decision IS NULL) AS unlabelled
            FROM shots sh
            JOIN set_versions v ON v.id = sh.set_version_id
            LEFT JOIN shot_judgements j ON j.shot_id = sh.id
            WHERE v.set_id = ?
            GROUP BY sh.set_version_id
            """,
            (set_id,),
        )
        return {
            int(row["version_id"]): VersionLabelCounts(
                keep=int(row["keep"] or 0),
                improve=int(row["improve"] or 0),
                discard=int(row["discard"] or 0),
                unlabelled=int(row["unlabelled"] or 0),
            )
            for row in rows
        }

    async def rollback_target(self, set_id: int) -> int | None:
        """The newest version worth going back to: the last one with a Keep.

        Never the current version — going back to where you already are is not
        a roll back — and never one whose only shots were discarded or never
        labelled, because "it worked" is a thing somebody said, not a thing a
        shot count implies.
        """
        value = await self.db.fetch_value(
            """
            SELECT v.id FROM set_versions v
            WHERE v.set_id = :set_id
              AND v.version_no < (SELECT MAX(v2.version_no) FROM set_versions v2
                                   WHERE v2.set_id = :set_id)
              AND EXISTS (SELECT 1 FROM shots sh
                            JOIN shot_judgements j ON j.shot_id = sh.id
                           WHERE sh.set_version_id = v.id AND j.decision = 'keep')
            ORDER BY v.version_no DESC LIMIT 1
            """,
            {"set_id": set_id},
        )
        return None if value is None else int(value)

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

    async def dead_end_versions(self, set_ids: Iterable[int]) -> set[int]:
        """Which of these Sets' versions are off the line still being brewed.

        One query for however many Sets were asked about, then the same walk the
        Set page uses, per Set. The Chat page's folders need this for a list of
        conversations spread over every Set there is, and a query per Set would
        make drawing the list cost as much as opening one.
        """
        wanted = sorted({int(set_id) for set_id in set_ids})
        if not wanted:
            return set()
        placeholders = ", ".join("?" * len(wanted))
        rows = await self.db.fetch_all(
            "SELECT id, set_id, version_no, parent_version_id, restores_version_id "  # noqa: S608 - placeholders are generated, the ids are bound
            f"FROM set_versions WHERE set_id IN ({placeholders})",
            wanted,
        )
        links = self.to_models(VersionLink, rows)
        dead: set[int] = set()
        for set_id in wanted:
            dead |= dead_end_ids([link for link in links if link.set_id == set_id])
        return dead

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

    async def active_version(self) -> SetVersionRow | None:
        """The current version of the active Set, if there is one.

        The one query auto-assignment runs per ingested shot: an index seek on
        `idx_sets_one_active` and one more on the version.
        """
        row = await self.db.fetch_one(
            f"""
            {_VERSION_SELECT}
            JOIN sets s ON s.id = v.set_id
            WHERE s.active = 1 AND s.status = 'active'
            ORDER BY v.version_no DESC LIMIT 1
            """
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
        profile_version_id: int | None,
        device_profile_id: str,
    ) -> int | None:
        """Attach a freshly stored shot to the active Set, if it fits.

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
        version = await self.active_version()
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
                    WHERE device_id = ? AND deleted_at IS NULL
                    """,
                    (device_profile_id,),
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

    # ── the spread ───────────────────────────────────────────────────

    async def counted_shots(self, set_id: int) -> list[CountedShot]:
        """Every shot of this Set that counts towards its spread, with its measures.

        "Counts" is three exclusions and no more. A **quarantined** shot never
        parsed, so its numbers are whatever the header happened to hold; an
        **incomplete** one stopped early, and its shot time measures the
        interruption rather than the recipe; a **Discard** says the shot went
        wrong, not that the recipe did, and grading a prediction on one reads
        the wrong signal. A shot nobody labelled counts: it is plain data, and
        excluding it would make the spread depend on how diligent somebody had
        been with the buttons.

        Each measure is read where it already lives and nothing is derived a
        second time. Three of them need a rule, and each rule is one this
        archive already applies somewhere else:

        * **"not recorded" is written as zero in several places**, so zero is
          read as absent where the engine cannot mean it literally. A
          ``duration_ms`` of 0 is a header that never got a time. The
          diagnostics engine writes ``0.0`` for an average over no samples
          (``_safe_mean([])``) and for ``max(pressures)`` over an empty list, so
          an average brew flow or a peak pressure of exactly zero is "nothing to
          average", not a shot that ran at no pressure. The **time to first
          drip** is the exception and is left alone: it is already nullable —
          the engine writes NULL when the flow never rose — so a 0.0 there is a
          real reading, the drip landing on the first sample.
        * **the JSON paths are guarded by type**, the same way a profile's
          temperature is read in `_VERSION_SELECT`: ``json_type(...) IN
          ('integer','real')``. A hand-edited row, an older document or a future
          shape could hold a string or an object at one of these paths, and a
          Set page that answered 500 because of one odd blob would be a bad
          trade for a number that is only ever an average.
        * **the yield follows the rule `starting/similar.py` already uses** —
          the judgement's typed dose out first, then the machine's final weight,
          then the device index's volume. A machine with no scale records
          nothing, so the only yield that exists is the one the person wrote
          down; and where both exist the typed one is the person correcting the
          scale, which is the number they would compare against.

        The version's recipe rides along on each shot, because grouping repeats
        is then a pass over one list rather than a second query.
        """
        rows = await self.db.fetch_all(
            """
            SELECT sh.id AS shot_id,
                   sh.set_version_id AS version_id,
                   v.version_no,
                   v.profile_version_id,
                   v.grind_setting,
                   v.grind_value,
                   v.dose_g,
                   v.target_yield_g,
                   CASE WHEN sh.duration_ms > 0 THEN sh.duration_ms / 1000.0 END AS shot_time_s,
                   CASE WHEN json_type(sh.diagnostics_json,
                                       '$.summary.flow.time_to_first_drip_s')
                             IN ('integer', 'real')
                        THEN json_extract(sh.diagnostics_json,
                                          '$.summary.flow.time_to_first_drip_s')
                        END AS first_drip_s,
                   COALESCE(j.dose_out_g, sh.final_weight_g, sh.index_volume_g) AS yield_g,
                   CASE WHEN json_type(sh.diagnostics_json, '$.summary.pressure.max_bar')
                             IN ('integer', 'real')
                         AND json_extract(sh.diagnostics_json, '$.summary.pressure.max_bar') > 0
                        THEN json_extract(sh.diagnostics_json, '$.summary.pressure.max_bar')
                        END AS peak_pressure_bar,
                   CASE WHEN json_type(sh.diagnostics_json,
                                       '$.diagnostics.extraction.flow_avg_brew_ml_s')
                             IN ('integer', 'real')
                         AND json_extract(sh.diagnostics_json,
                                          '$.diagnostics.extraction.flow_avg_brew_ml_s') > 0
                        THEN json_extract(sh.diagnostics_json,
                                          '$.diagnostics.extraction.flow_avg_brew_ml_s')
                        END AS brew_flow_ml_s,
                   j.rating,
                   j.balance,
                   j.decision
            FROM shots sh
            JOIN set_versions v ON v.id = sh.set_version_id
            LEFT JOIN shot_judgements j ON j.shot_id = sh.id
            WHERE v.set_id = ?
              AND sh.quarantined = 0
              AND sh.incomplete = 0
              AND (j.decision IS NULL OR j.decision != 'discard')
            ORDER BY sh.id
            """,
            (set_id,),
        )
        return self.to_models(CountedShot, rows)

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

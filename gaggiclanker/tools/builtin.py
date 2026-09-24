"""The tools themselves. One module, because the set is small and domain-bound.

Twenty-two tools in two permission classes, and the third is empty on purpose.
The read tools answer questions about the archive; the propose tools turn a
conclusion into a row somebody still has to confirm, or queue work that costs
money; there are no device-write tools here at all, and that is the feature —
pushing a profile and deleting a shot off the machine stay buttons in the UI.

Which of the twenty-two a conversation *has* is not decided here:
:mod:`gaggiclanker.tools.scope` decides it from the conversation's kind. What
is decided here is what a tool does when it is called inside a Set's
conversation — the Set is the conversation's and another one is refused, and a
shot filed elsewhere is refused in words that do not say whether it exists.

Two shapes recur and are worth stating once. **Every output is a pydantic model**,
so the JSON the model reads is the JSON the schema promised and `mypy --strict`
checks the middle. And **a missing service is an ordinary error**, not an
exception: the stdio MCP entry point opens a database and nothing else, so
`run_analysis` over stdio has to say "this needs the running application" rather
than raise `AttributeError` at the bottom of a stack the caller cannot see.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.analyses import AnalysesRepository
from gaggiclanker.db.repos.beans import BeansRepository
from gaggiclanker.db.repos.grinders import GrindersRepository
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
    set_attributes,
)
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.set_proposals import (
    ProposalWrite,
    ProposalWriteResult,
    SetProposalsRepository,
    change_groups,
)
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, version_changes
from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.profile_recipe import profile_recipe
from gaggiclanker.domain.sets import grind_value
from gaggiclanker.infra.errors import TooManyRequests
from gaggiclanker.infra.ratelimit import ANALYSIS_RATE_LIMIT, ANALYSIS_WINDOW_SECONDS
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.tools.registry import ToolContext, tool
from gaggiclanker.tools.scope import DESIGN_RULE
from gaggiclanker.tools.sql import (
    ALLOWED_VIEWS,
    DEFAULT_ROW_LIMIT,
    MAX_ROW_LIMIT,
    SqlRefused,
    run_query,
)

__all__ = ["EXAMPLE_QUERIES"]


class _Model(BaseModel):
    """Every tool model forbids extras: a typo'd argument is a refusal, not a silent no-op."""

    model_config = ConfigDict(extra="forbid")


# ── describe_schema ──────────────────────────────────────────────────

#: Shown with the schema. Examples are worth more than column lists to a model
#: that has to write correlated SQL, and these four are the shapes that actually
#: come up: one Set's shots, a per-version average, a join to the judgement, and
#: a curve slice.
EXAMPLE_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "The ten most recent shots in one Set, newest first",
        "SELECT shot_id, started_at, set_version_no, execution_score, rating, ratio\n"
        "  FROM v_shots WHERE set_id = 3 ORDER BY started_at DESC LIMIT 10",
    ),
    (
        "Average execution score and rating per version of a Set",
        "SELECT set_version_no, COUNT(*) AS shots,\n"
        "       ROUND(AVG(execution_score), 1) AS avg_score,\n"
        "       ROUND(AVG(rating), 2) AS avg_rating\n"
        "  FROM v_shots WHERE set_id = 3 GROUP BY set_version_no ORDER BY set_version_no",
    ),
    (
        "Shots the person disliked, with what the analyzer suggested",
        "SELECT s.shot_id, s.rating, s.balance, g.variable, g.direction, g.reason\n"
        "  FROM v_shots s JOIN v_suggestions g ON g.shot_id = s.shot_id\n"
        " WHERE s.rating <= 2 ORDER BY s.started_at DESC LIMIT 20",
    ),
    (
        "Flow against its target over the second half of one shot",
        "SELECT t_s, target_flow_ml_s, flow_ml_s, pressure_bar\n"
        "  FROM v_samples WHERE shot_id = 129 AND t_ms > 15000 ORDER BY t_ms",
    ),
)


class DescribeSchemaInput(_Model):
    """No arguments: the schema is small enough to return whole."""


class ViewSchema(_Model):
    name: str
    columns: list[str]


class SchemaOutput(_Model):
    views: list[ViewSchema]
    examples: list[dict[str, str]]
    notes: str


SCHEMA_NOTES = (
    "SQLite. Only these views exist for query_shots; the underlying tables are not readable. "
    "Timestamps are ISO-8601 UTC strings, so ORDER BY on them is chronological and "
    "date(started_at) works. Every row cap is enforced server-side — ask for an aggregate "
    "rather than a thousand rows. v_shots already joins the Set, the bean, the grinder and "
    "the judgement, so most questions need no join at all. v_judgements.taste_notes_json and "
    "aroma_notes_json are JSON arrays of SCA flavour-wheel notes, each the path from the "
    "centre joined by dots (fruity.berry.blackberry), so json_each with LIKE "
    "'sour_fermented.sour%' finds every sour note. profile_temperature_c is the brew "
    "temperature the profile states — a Set version records none of its own, so two "
    "versions differ in temperature only when they name different profiles."
)


@tool(
    "describe_schema",
    permission="read",
    description=(
        "The curated read-only views query_shots may use, their columns, and example "
        "queries. Call this before writing SQL for the first time in a conversation."
    ),
)
async def describe_schema(ctx: ToolContext, _: DescribeSchemaInput) -> SchemaOutput:
    views: list[ViewSchema] = []
    for name in sorted(ALLOWED_VIEWS):
        # `PRAGMA table_info` takes an identifier, not a bound parameter. The
        # name comes from the module-level frozenset, never from the caller.
        rows = await ctx.db.fetch_all(f"PRAGMA table_info({name})")
        views.append(ViewSchema(name=name, columns=[str(row["name"]) for row in rows]))
    return SchemaOutput(
        views=views,
        examples=[{"question": question, "sql": sql} for question, sql in EXAMPLE_QUERIES],
        notes=SCHEMA_NOTES,
    )


# ── query_shots ──────────────────────────────────────────────────────


class QueryInput(_Model):
    sql: str = Field(
        min_length=1,
        max_length=8000,
        description="One SELECT (a WITH ... SELECT is fine) over the v_* views.",
    )
    limit: int = Field(
        default=DEFAULT_ROW_LIMIT,
        ge=1,
        le=MAX_ROW_LIMIT,
        description=f"Rows to return, at most {MAX_ROW_LIMIT}.",
    )


class QueryOutput(_Model):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0


@tool(
    "query_shots",
    permission="read",
    description=(
        "Run one read-only SELECT over the curated views and get the rows back. "
        "This is the main analysis tool: aggregates, comparisons across Sets and "
        "versions, anything that is a question about many shots at once. "
        "Call describe_schema first if you do not know the columns."
    ),
)
async def query_shots(ctx: ToolContext, args: QueryInput) -> QueryOutput:
    try:
        result = await run_query(ctx.db.path, args.sql, limit=args.limit)
    except SqlRefused as exc:
        # Refusals are the model's to read and fix, so they come back as the
        # tool's error value with the reason spelled out.
        raise ValueError(str(exc)) from None
    return QueryOutput(
        columns=result.columns,
        rows=result.rows,
        row_count=len(result.rows),
        truncated=result.truncated,
        duration_ms=result.duration_ms,
    )


# ── shots ────────────────────────────────────────────────────────────


class GetShotInput(_Model):
    shot_id: int = Field(gt=0)
    detail: Literal["summary", "per_phase", "curve"] = Field(
        default="summary",
        description=(
            "summary: the row and its diagnostics. per_phase: adds the phase table. "
            "curve: adds a downsampled series — only ask for it when the shape matters."
        ),
    )


class ShotSummary(_Model):
    shot_id: int
    device_id: str = ""
    started_at: str | None = None
    duration_s: float = 0.0
    profile_label: str | None = None
    volume_g: float | None = None
    execution_score: float | None = None
    execution_reason: str | None = None
    set_id: int | None = None
    set_name: str | None = None
    set_version_no: int | None = None
    rating: int | None = None
    balance: str | None = None
    judgement_notes: str | None = None
    quarantined: bool = False


class ShotOutput(_Model):
    shot: ShotSummary
    diagnostics: dict[str, Any] | None = None
    phases: list[dict[str, Any]] | None = None
    curve: list[dict[str, Any]] | None = None
    analysis: dict[str, Any] | None = None


#: How many points a `curve` request comes back with. A shot is ~213 samples at
#: 250 ms; 60 is enough to see the shape and short enough to sit in a prompt
#: next to everything else the turn is carrying.
CURVE_POINTS = 60


async def _shot_summary(ctx: ToolContext, shot_id: int) -> tuple[ShotSummary, Any] | None:
    row = await ctx.db.fetch_one("SELECT * FROM v_shots WHERE shot_id = ?", (shot_id,))
    if row is None:
        return None
    data = dict(zip(row.keys(), tuple(row), strict=True))
    summary = ShotSummary(
        shot_id=int(data["shot_id"]),
        device_id=str(data.get("device_id") or ""),
        started_at=data.get("started_at"),
        duration_s=float(data.get("duration_s") or 0.0),
        profile_label=data.get("profile_label") or data.get("profile_name_on_device"),
        volume_g=data.get("volume_g"),
        execution_score=data.get("execution_score"),
        execution_reason=data.get("execution_reason"),
        set_id=data.get("set_id"),
        set_name=data.get("set_name"),
        set_version_no=data.get("set_version_no"),
        rating=data.get("rating"),
        balance=data.get("balance"),
        judgement_notes=data.get("judgement_notes"),
        quarantined=bool(data.get("quarantined")),
    )
    return summary, data


def _json(raw: Any) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:  # pragma: no cover - written by the parser, so never
        return None


@tool(
    "get_shot",
    permission="read",
    description=(
        "One shot in full: the row, its deterministic diagnostics, and optionally the "
        "phase table or a downsampled curve. Cite a shot by its id when you use it."
    ),
)
async def get_shot(ctx: ToolContext, args: GetShotInput) -> ShotOutput:
    found = await _shot_summary(ctx, args.shot_id)
    _shot_of_scope(ctx, args.shot_id, found[1] if found is not None else None)
    assert found is not None  # _shot_of_scope raises when there is nothing
    summary, data = found
    out = ShotOutput(shot=summary, diagnostics=_json(data.get("diagnostics_json")))

    latest = await AnalysesRepository(ctx.db).latest_for_shot(args.shot_id)
    if latest is not None:
        out.analysis = {
            "analysis_id": latest.id,
            "status": latest.status,
            "output": latest.output,
            "created_at": latest.created_at,
        }

    if args.detail in {"per_phase", "curve"}:
        phases = await ctx.db.fetch_value(
            "SELECT phases_json FROM shots WHERE id = ?", (args.shot_id,)
        )
        decoded = _json(phases)
        out.phases = decoded if isinstance(decoded, list) else []

    if args.detail == "curve":
        rows = await ctx.db.fetch_all(
            "SELECT COUNT(*) AS n FROM v_samples WHERE shot_id = ?", (args.shot_id,)
        )
        total = int(rows[0]["n"]) if rows else 0
        # Modulo on the row number rather than a LIMIT: a LIMIT would give the
        # first N samples, which is the pre-infusion and none of the shot.
        stride = max(1, total // CURVE_POINTS)
        points = await ctx.db.fetch_all(
            """
            SELECT t_s, temperature_c, pressure_bar, flow_ml_s, target_flow_ml_s,
                   weight_g, phase_number
              FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY t_ms) AS rn
                      FROM v_samples WHERE shot_id = ?)
             WHERE (rn - 1) % ? = 0
             ORDER BY t_s
            """,
            (args.shot_id, stride),
        )
        out.curve = [dict(zip(row.keys(), tuple(row), strict=True)) for row in points]
    return out


class CompareInput(_Model):
    shot_ids: list[int] = Field(min_length=2, max_length=4)


class CompareOutput(_Model):
    shots: list[ShotSummary]
    differences: list[dict[str, Any]] = Field(default_factory=list)


#: What a comparison actually turns on. Not every column: a table of forty
#: fields is a table nobody reads, and these are the ones a change is made in.
_COMPARE_FIELDS: tuple[str, ...] = (
    "profile_label",
    "grind_setting",
    "set_dose_g",
    "target_yield_g",
    "profile_temperature_c",
    "duration_s",
    "volume_g",
    "avg_temp_c",
    "max_pressure_bar",
    "avg_flow_ml_s",
    "execution_score",
    "rating",
    "balance",
    "ratio",
)


@tool(
    "compare_shots",
    permission="read",
    description=(
        "Two to four shots side by side, with the fields that differ called out. "
        "Use it for 'did the grind change actually help' questions."
    ),
)
async def compare_shots(ctx: ToolContext, args: CompareInput) -> CompareOutput:
    summaries: list[ShotSummary] = []
    raw: list[dict[str, Any]] = []
    for shot_id in args.shot_ids:
        found = await _shot_summary(ctx, shot_id)
        _shot_of_scope(ctx, shot_id, found[1] if found is not None else None)
        assert found is not None  # _shot_of_scope raises when there is nothing
        summaries.append(found[0])
        raw.append(found[1])

    differences: list[dict[str, Any]] = []
    for field_name in _COMPARE_FIELDS:
        values = [row.get(field_name) for row in raw]
        if len({json.dumps(value, default=str) for value in values}) > 1:
            differences.append(
                {"field": field_name, "values": dict(zip(args.shot_ids, values, strict=True))}
            )
    return CompareOutput(shots=summaries, differences=differences)


# ── sets and catalogue ───────────────────────────────────────────────


class GetSetInput(_Model):
    set_id: int | None = Field(
        default=None, description="Defaults to the Set this conversation is scoped to."
    )


class SetOutput(_Model):
    set: dict[str, Any]
    versions: list[dict[str, Any]]
    trajectory: list[dict[str, Any]]
    insights: list[str] = Field(default_factory=list)


def _resolve_set(ctx: ToolContext, given: int | None) -> int:
    """Which Set this call is about, and a refusal when it is not this one.

    In a Set conversation the answer is fixed: the Set the conversation is
    about, whatever was passed. A different id is refused in words that say so
    and **say nothing else** — not whether that Set exists, not how many there
    are. "There is no Set 7" and "Set 7 is somebody else's coffee" are two
    different things to learn, and a conversation limited to one Set gets to
    learn neither.
    """
    if ctx.scope.kind == "set" and ctx.scope.set_id is not None:
        if given is not None and given != ctx.scope.set_id:
            raise ValueError(
                f"This conversation is about Set {ctx.scope.set_id} and can see no other. "
                "Leave set_id out, or pass this one."
            )
        return ctx.scope.set_id
    if given is None:
        raise ValueError(
            "No set_id was given and this conversation is not scoped to a Set. "
            "Call list_sets and pass one."
        )
    return given


def _shot_of_scope(ctx: ToolContext, shot_id: int, data: dict[str, Any] | None) -> None:
    """Refuse a shot this conversation is not entitled to, without saying why.

    One message for both "there is no such shot" and "that shot belongs to
    another Set", because two messages are an existence oracle: a model told
    them apart could walk the archive from inside one Set's conversation, which
    is the whole thing the scope is for.
    """
    if ctx.scope.kind != "set":
        if data is None:
            raise ValueError(f"No shot {shot_id} in the archive.")
        return
    if data is None or data.get("set_id") != ctx.scope.set_id:
        raise ValueError(
            f"Shot {shot_id} is not a shot of this Set. This conversation can see this Set's "
            "shots only — list_set_shots is how to find them."
        )


@tool(
    "get_set",
    permission="read",
    description=(
        "One Set: what it is, every version with what changed and why, and the "
        "per-version averages that say whether each change helped."
    ),
)
async def get_set(ctx: ToolContext, args: GetSetInput) -> SetOutput:
    set_id = _resolve_set(ctx, args.set_id)
    sets = SetsRepository(ctx.db)
    row = await sets.get(set_id)
    if row is None:
        raise ValueError(f"No Set {set_id}.")
    versions = await sets.versions(set_id)
    trends = await sets.trends(set_id)
    insights = await _set_insights(ctx, row)
    return SetOutput(
        set=row.model_dump(mode="json"),
        versions=[version.model_dump(mode="json") for version in versions],
        trajectory=[version.model_dump(mode="json") for version in trends.versions],
        insights=[insight.render() for insight in insights],
    )


async def _set_insights(ctx: ToolContext, row: Any) -> list[Any]:
    """The confirmed insights that apply to a Set, through the shared matcher.

    The same ``select_insights`` an analysis uses, so the chat and the prompt
    cannot disagree about which insights apply — reimplementing the rule here
    is how the two drift.
    """
    bean = await BeansRepository(ctx.db).get(row.bean_id) if row.bean_id else None
    attributes = set_attributes(
        bean_id=row.bean_id,
        roast_level=getattr(bean, "roast_level", None),
        process=getattr(bean, "process", None),
        origin=getattr(bean, "origin", None),
        grinder_id=row.grinder_id,
    )
    return await InsightsRepository(ctx.db).select(attributes)


class ListSetShotsInput(_Model):
    version_no: int | None = Field(
        default=None,
        gt=0,
        description="Only the shots pulled under this version of the Set.",
    )
    label: Literal["keep", "improve", "discard"] | None = Field(
        default=None,
        description=(
            "Only the shots labelled this way. Keep is the gold standard, Improve is what "
            "you are working on, and Discard says the shot went wrong rather than the recipe."
        ),
    )
    limit: int = Field(default=50, ge=1, le=200)


class SetShotLine(_Model):
    """One shot, with the numbers a prediction is graded on beside the verdict."""

    shot_id: int
    version_no: int
    started_at: str | None = None
    shot_time_s: float | None = None
    first_drip_s: float | None = None
    yield_g: float | None = None
    peak_pressure_bar: float | None = None
    brew_flow_ml_s: float | None = None
    rating: int | None = None
    balance: str | None = None
    decision: str | None = None
    taste_notes: list[str] = Field(default_factory=list)
    aroma_notes: list[str] = Field(default_factory=list)
    notes: str = ""
    #: True when the shot's own numbers are not evidence: it never parsed, it
    #: stopped early, or it was labelled Discard. Said on the row rather than
    #: left out of the list, because "when did it go wrong" is a question these
    #: shots are part of the answer to.
    counts: bool = True


class ListSetShotsOutput(_Model):
    shots: list[SetShotLine] = Field(default_factory=list)
    count: int = 0
    #: True when the limit cut the list short, so a model can ask for more
    #: rather than conclude the Set has that many shots.
    truncated: bool = False


@tool(
    "list_set_shots",
    permission="read",
    description=(
        "This Set's shots, newest first: the six measures a prediction is graded on, the "
        "rating, the balance, the flavour notes, the label and the note. Filter by version "
        "number or by label — 'every Keep shot' and 'everything on v5' are the two questions "
        "it exists for. A shot marked counts=false is quarantined, incomplete or Discard: "
        "read it, but do not average it."
    ),
)
async def list_set_shots(ctx: ToolContext, args: ListSetShotsInput) -> ListSetShotsOutput:
    """No ``set_id``: the Set is the conversation's, which is the whole point.

    A Set conversation reads its own shots and cannot be pointed at anybody
    else's, so there is no argument here to point wrongly.
    """
    set_id = _resolve_set(ctx, None)
    # One more than asked for, and then trimmed: "did the limit cut this list
    # short" and "does the Set have exactly this many" are different answers,
    # and a count equal to the limit cannot tell them apart.
    found = await SetsRepository(ctx.db).set_shots(
        set_id,
        version_no=args.version_no,
        decision=args.label,
        limit=args.limit + 1,
    )
    rows = found[: args.limit]
    return ListSetShotsOutput(
        shots=[
            SetShotLine(
                shot_id=row.shot_id,
                version_no=row.version_no,
                started_at=row.started_at,
                shot_time_s=row.shot_time_s,
                first_drip_s=row.first_drip_s,
                yield_g=row.yield_g,
                peak_pressure_bar=row.peak_pressure_bar,
                brew_flow_ml_s=row.brew_flow_ml_s,
                rating=row.rating,
                balance=row.balance,
                decision=row.decision,
                taste_notes=row.taste_notes,
                aroma_notes=row.aroma_notes,
                notes=row.notes,
                counts=not (row.quarantined or row.incomplete or row.decision == "discard"),
            )
            for row in rows
        ],
        count=len(rows),
        truncated=len(found) > args.limit,
    )


class ListSetsInput(_Model):
    include_archived: bool = False


class ListOutput(_Model):
    items: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


@tool(
    "list_sets",
    permission="read",
    description="Every Set, the ones new shots are filed under first.",
)
async def list_sets(ctx: ToolContext, args: ListSetsInput) -> ListOutput:
    rows = await SetsRepository(ctx.db).list_sets(include_archived=args.include_archived)
    items = [row.model_dump(mode="json") for row in rows]
    return ListOutput(items=items, count=len(items))


class ListBeansInput(_Model):
    include_archived: bool = False


@tool("list_beans", permission="read", description="The bean catalogue.")
async def list_beans(ctx: ToolContext, args: ListBeansInput) -> ListOutput:
    rows = await BeansRepository(ctx.db).list_all(include_archived=args.include_archived)
    items = [row.model_dump(mode="json") for row in rows]
    return ListOutput(items=items, count=len(items))


class NoArgs(_Model):
    """No arguments."""


@tool(
    "list_grinders",
    permission="read",
    description=(
        "The grinders, with the unit each one is adjusted in. Give grind advice in "
        "the grinder's own units — 'two clicks finer', not 'finer by 15 microns'."
    ),
)
async def list_grinders(ctx: ToolContext, _: NoArgs) -> ListOutput:
    rows = await GrindersRepository(ctx.db).list_all()
    items = [row.model_dump(mode="json") for row in rows]
    return ListOutput(items=items, count=len(items))


class ListProfilesInput(_Model):
    limit: int = Field(default=50, ge=1, le=200)


@tool(
    "list_profiles",
    permission="read",
    description="Brew profile versions known to the archive, newest first.",
)
async def list_profiles(ctx: ToolContext, args: ListProfilesInput) -> ListOutput:
    rows = await ctx.db.fetch_all(
        "SELECT * FROM v_profiles ORDER BY created_at DESC, profile_version_id DESC LIMIT ?",
        (args.limit,),
    )
    items = [dict(zip(row.keys(), tuple(row), strict=True)) for row in rows]
    if ctx.scope.kind == "set":
        # `shot_count` counts the whole archive's shots on that profile, which
        # is how busy the *other* Sets have been — a fact this conversation is
        # not entitled to. The profiles themselves are archive-wide on purpose:
        # a draft is made from one, and they belong to no Set.
        for item in items:
            item.pop("shot_count", None)
    return ListOutput(items=items, count=len(items))


class GetProfileInput(_Model):
    profile_version_id: int = Field(gt=0, description="The profile version to read.")


class ProfileRecipeFacts(_Model):
    """What the document itself says about the recipe, read by the one rule."""

    #: The brew temperature the profile states; ``None`` when it states none
    #: (the firmware writes 0 for "not set").
    temperature_c: float | None = None
    #: The largest volumetric stop across the phases: the weight in the cup.
    target_yield_g: float | None = None


class GetProfileOutput(_Model):
    profile_version_id: int
    label: str
    type: str
    utility: bool = False
    #: `device`, `import` or `draft`: where this version came from.
    source: str = ""
    created_at: str
    #: The whole document, in the shape the machine stores it.
    document: dict[str, Any]
    recipe: ProfileRecipeFacts
    #: How many of the archive's shots were pulled with it. Only in a general
    #: conversation: see :func:`list_profiles` for why a Set's does not get it.
    shot_count: int | None = None


@tool(
    "get_profile",
    permission="read",
    description=(
        "One profile version in full: its label, type and whole document — every phase, pump "
        "target and stop condition — plus the temperature and yield the document states. Read "
        "the profile you are about to change or fork before you change it."
    ),
)
async def get_profile(ctx: ToolContext, args: GetProfileInput) -> GetProfileOutput:
    """The document, because nothing else hands a model one.

    `list_profiles` gives labels and metadata; an agent asked to fork or adjust
    a profile has to read what it is changing, or it writes a patch against a
    document it imagined. Profiles belong to no Set, so every conversation may
    read any of them; what a Set's conversation does not get is the
    archive-wide shot count, for the reason `list_profiles` gives.
    """
    version = await ProfilesRepository(ctx.db).get_version(args.profile_version_id)
    if version is None or not version.profile:
        raise ValueError(
            f"No profile version {args.profile_version_id}. list_profiles lists the ones "
            "this archive has."
        )
    document = Profile.model_validate(version.profile).to_device()
    facts = profile_recipe(document)
    shot_count: int | None = None
    if ctx.scope.kind != "set":
        shot_count = int(
            await ctx.db.fetch_value(
                "SELECT shot_count FROM v_profiles WHERE profile_version_id = ?",
                (version.id,),
            )
            or 0
        )
    return GetProfileOutput(
        profile_version_id=version.id,
        label=version.label,
        type=version.type,
        utility=version.utility,
        source=version.source,
        created_at=version.created_at,
        document=document,
        recipe=ProfileRecipeFacts(
            temperature_c=facts.temperature_c, target_yield_g=facts.target_yield_g
        ),
        shot_count=shot_count,
    )


# ── knowledge ────────────────────────────────────────────────────────


class SearchKnowledgeInput(_Model):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=5, ge=1, le=20)


class KnowledgeHit(_Model):
    heading_path: str
    doc_slug: str = ""
    doc_title: str = ""
    heading: str = ""
    snippet: str = ""
    body: str = ""
    score: float = 0.0


class SearchKnowledgeOutput(_Model):
    hits: list[KnowledgeHit] = Field(default_factory=list)


@tool(
    "search_knowledge",
    permission="read",
    description=(
        "Search the espresso knowledge base (prose documents, chunked by heading). "
        "Cite what you use by its heading_path, verbatim — that is a link the reader "
        "can follow."
    ),
)
async def search_knowledge(ctx: ToolContext, args: SearchKnowledgeInput) -> SearchKnowledgeOutput:
    knowledge = ctx.knowledge or KnowledgeService(ctx.db)
    hits = await knowledge.search_chunks(args.query, k=args.k)
    return SearchKnowledgeOutput(
        hits=[
            KnowledgeHit(
                heading_path=hit.chunk.heading_path,
                doc_slug=hit.chunk.doc_slug,
                doc_title=hit.chunk.doc_title,
                heading=hit.chunk.heading,
                snippet=hit.snippet,
                body=hit.chunk.body,
                score=hit.score,
            )
            for hit in hits
        ]
    )


class GetChunkInput(_Model):
    heading_path: str = Field(min_length=1, max_length=500)


class GetChunkOutput(_Model):
    heading_path: str
    doc_slug: str = ""
    doc_title: str = ""
    heading: str = ""
    body: str = ""


@tool(
    "get_knowledge_chunk",
    permission="read",
    description="One knowledge passage by its heading_path, in full.",
)
async def get_knowledge_chunk(ctx: ToolContext, args: GetChunkInput) -> GetChunkOutput:
    knowledge = ctx.knowledge or KnowledgeService(ctx.db)
    chunk = await knowledge.get_chunk(args.heading_path)
    if chunk is None:
        raise ValueError(
            f"No knowledge chunk at {args.heading_path!r}. Use search_knowledge to find one."
        )
    return GetChunkOutput(
        heading_path=chunk.heading_path,
        doc_slug=chunk.doc_slug,
        doc_title=chunk.doc_title,
        heading=chunk.heading,
        body=chunk.body,
    )


class GetRulesInput(_Model):
    category: str | None = None
    applies: list[str] | None = Field(
        default=None,
        max_length=20,
        description=(
            "Tokens of the form dimension:value (roast_level:light, style:bloom, "
            "signal:balance:sour). A rule is returned when every dimension it states is "
            "satisfied by one of them."
        ),
    )


class RuleOut(_Model):
    id: int
    category: str
    key: str
    text: str
    unit: str = ""
    confidence: str = ""
    source: str = ""


class GetRulesOutput(_Model):
    rules: list[RuleOut] = Field(default_factory=list)


@tool(
    "get_rules",
    permission="read",
    description=(
        "The authoritative heuristics tier: temperature by roast, pressure by roast and "
        "process, and so on. These outrank the prose documents where they disagree."
    ),
)
async def get_rules(ctx: ToolContext, args: GetRulesInput) -> GetRulesOutput:
    rows = await RulesRepository(ctx.db).list_rules(
        category=args.category, enabled=True, applies=args.applies
    )
    return GetRulesOutput(
        rules=[
            RuleOut(
                id=rule.id,
                category=rule.category,
                key=rule.key,
                text=rule.text,
                unit=rule.unit,
                confidence=rule.confidence,
                source=rule.source,
            )
            for rule in rows
        ]
    )


class GetInsightsInput(_Model):
    set_id: int | None = Field(
        default=None,
        description=(
            "Only the confirmed insights that apply to this Set. Defaults to the "
            "conversation's Set; omit both to list every confirmed insight."
        ),
    )
    include_unconfirmed: bool = Field(
        default=False,
        description=(
            "Unconfirmed insights are proposals waiting for the user. They are never "
            "evidence — do not reason from one without saying it is unconfirmed. "
            "Refused in a conversation about one Set: there, only what the person has "
            "confirmed is evidence."
        ),
    )


class InsightOut(_Model):
    id: int
    scope: str
    text: str
    evidence_shot_ids: list[int] = Field(default_factory=list)
    source: str = ""
    confirmed: bool = False


class GetInsightsOutput(_Model):
    insights: list[InsightOut] = Field(default_factory=list)


@tool(
    "get_insights",
    permission="read",
    description=(
        "What this archive has learned about this kitchen: confirmed insights, scoped to "
        "a bean, a grinder, a roast level or their combination. In a conversation about "
        "one Set it answers with the confirmed insights that apply to that Set — the same "
        "ones the opening context lists — and nothing else."
    ),
)
async def get_insights(ctx: ToolContext, args: GetInsightsInput) -> GetInsightsOutput:
    """Scoped in a Set conversation, and in three ways rather than one.

    The Set, obviously. But also **confirmed only**: an unconfirmed insight is a
    proposal waiting for the person, it is not evidence, and a list of every
    proposal in the archive would be a list of what has been noticed about every
    other coffee. And the **evidence shot ids are filtered to this Set's shots**:
    an insight scoped by bean legitimately rests on shots from several Sets, and
    handing those ids over would give back the existence oracle the shot
    refusals close — `get_shot` would refuse them, and their bare presence is
    already the answer.
    """
    repo = InsightsRepository(ctx.db)
    if ctx.scope.kind == "set":
        set_id = _resolve_set(ctx, args.set_id)
        if args.include_unconfirmed:
            raise ValueError(
                "Unconfirmed insights are proposals waiting for the person, and in a "
                "conversation about one Set nothing unconfirmed is evidence. Ask for the "
                "confirmed ones (leave include_unconfirmed out)."
            )
        row = await SetsRepository(ctx.db).get(set_id)
        if row is None:
            raise ValueError(f"No Set {set_id}.")
        mine = await SetsRepository(ctx.db).shot_ids(set_id)
        return GetInsightsOutput(
            insights=[_insight_out(insight, only=mine) for insight in await _set_insights(ctx, row)]
        )

    scope_id = args.set_id if args.set_id is not None else ctx.set_id
    if scope_id is not None and not args.include_unconfirmed:
        row = await SetsRepository(ctx.db).get(scope_id)
        if row is None:
            raise ValueError(f"No Set {scope_id}.")
        rows = await _set_insights(ctx, row)
    else:
        rows = await repo.list_insights(confirmed=None if args.include_unconfirmed else True)
    return GetInsightsOutput(insights=[_insight_out(insight) for insight in rows])


def _insight_out(insight: Any, only: set[int] | None = None) -> InsightOut:
    """One insight as the model is shown it, with its evidence narrowed or not."""
    evidence = list(insight.evidence_shot_ids or [])
    return InsightOut(
        id=insight.id,
        scope=insight.scope_label,
        text=insight.text,
        evidence_shot_ids=(
            evidence if only is None else [shot_id for shot_id in evidence if shot_id in only]
        ),
        source=insight.source,
        confirmed=insight.confirmed,
    )


# ── run_analysis ─────────────────────────────────────────────────────


class RunAnalysisInput(_Model):
    shot_id: int = Field(gt=0)


class RunAnalysisOutput(_Model):
    analysis_id: int
    shot_id: int
    status: str
    started: bool = False
    output: dict[str, Any] | None = None
    error: str | None = None


@tool(
    "run_analysis",
    permission="propose",
    description=(
        "Queue the deterministic per-shot analysis for a shot that has none, and return "
        "the row. It runs in the background; read it back with get_shot once it is done. "
        "Idempotent: a shot already being analysed returns the running row. It spends "
        "provider tokens, so it is rate limited."
    ),
)
async def run_analysis(ctx: ToolContext, args: RunAnalysisInput) -> RunAnalysisOutput:
    """Propose-class, and limited, because it is the one tool that spends money.

    ``read`` would have been the tidy answer — it creates nothing a person has
    to decide about — but the permission class is what a caller is handed, and
    handing a read-only agent a button that queues provider calls is not a read.
    The limiter is the analysis route's own bucket at the analysis route's own
    limit: a model that decides to analyse a year of shots is the loop that
    rate limit exists for, and going through a tool rather than the route must
    not be a way around it.
    """
    if ctx.rate_limits is not None:
        try:
            ctx.rate_limits.check(
                "analysis",
                f"tool:{ctx.user or ctx.caller}",
                limit=ANALYSIS_RATE_LIMIT,
                window=ANALYSIS_WINDOW_SECONDS,
            )
        except TooManyRequests as exc:
            raise ValueError(str(exc)) from None
    if ctx.analyzer is None or ctx.tasks is None:
        raise ValueError(
            "run_analysis needs the running gaggiclanker application; this connection has "
            "database access only."
        )
    try:
        row, started = await ctx.analyzer.start(args.shot_id, tasks=ctx.tasks)
    except LookupError:
        raise ValueError(f"No shot {args.shot_id} in the archive.") from None
    return RunAnalysisOutput(
        analysis_id=row.id,
        shot_id=row.shot_id,
        status=row.status,
        started=started,
        output=row.output if isinstance(row.output, dict) else None,
        error=row.error,
    )


# ── propose ──────────────────────────────────────────────────────────


#: The shortest thing this tool will accept as a prediction. Not a measure of
#: quality — twenty characters of nonsense is still nonsense — but it is the
#: difference between a sentence somebody wrote and a field somebody filled in
#: to get past a check. What a *good* prediction says (direction, rough size,
#: which recorded measure, which version) is the prompt's business; the tool
#: only refuses the absence.
PREDICTION_MIN_CHARS = 20


class ProposeVersionInput(_Model):
    set_id: int | None = None
    reason: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "One sentence: what this change is trying to find out. It becomes the "
            "version's 'What are you trying?' if the person accepts it."
        ),
    )
    prediction: str = Field(
        default="",
        max_length=1000,
        description=(
            "Required. What should differ if this change does what you think, by roughly "
            "how much, on which measure the archive records, compared with which version: "
            "'compared to v4, expect 3 to 5 s longer and less sour'. This is what the next "
            "conversation grades you on, so it has to be able to turn out wrong."
        ),
    )
    compares_to_version_id: int | None = Field(
        default=None,
        description=(
            "Which version of this Set the prediction is measured against. Defaults to the "
            "version this change is a change to, which is almost always right."
        ),
    )
    combined_reason: str = Field(
        default="",
        max_length=500,
        description=(
            "Only when two things really have to move together: why they cannot be "
            "separated. Without it, a proposal that moves two things is refused."
        ),
    )
    grind_setting: str | None = Field(default=None, max_length=100)
    grind_value: float | None = Field(default=None, ge=0, le=10000)
    dose_g: float | None = Field(default=None, gt=0, le=100)
    target_yield_g: float | None = Field(default=None, gt=0, le=500)
    profile_version_id: int | None = None


class ProposeVersionOutput(_Model):
    """What is now waiting, and the plain statement that nothing has changed."""

    proposal_id: int
    set_id: int
    status: str = "proposed"
    #: What it would move, named as a person names it: "the grind", "the dose".
    changed: list[str] = Field(default_factory=list)
    #: The same change with its numbers: "Grind 22 → 21".
    change_summary: str = ""
    prediction: str = ""
    compares_to_version_no: int | None = None
    #: What the model should tell the person, in the tool's own words, because
    #: "I have created v6" is exactly the sentence this whole change exists to
    #: stop being true.
    note: str = ""


@tool(
    "propose_set_version",
    permission="propose",
    description=(
        "Propose ONE change to this Set, with a prediction, for the person to accept or "
        "decline. It creates no version and changes nothing: until they press Accept, the "
        "next shot is still filed under the recipe they are brewing. A prediction is "
        "required — say what should differ, by roughly how much, on which recorded "
        "measure, compared with which version. One change at a time; two need "
        "combined_reason. It is refused while this version's own prediction has not been "
        "graded, and while another proposal is already waiting. There is no temperature "
        "here: the machine brews at the temperature the profile states, so a temperature "
        "change is a profile change — use draft_profile, and the person approves and "
        "pushes it."
    ),
)
async def propose_set_version(ctx: ToolContext, args: ProposeVersionInput) -> ProposeVersionOutput:
    """Write down a change and a prediction; the person decides.

    Every refusal below is an error *value* with a sentence of its own, because
    a model that is told "refused" and nothing else calls again with the same
    arguments. None of them says anything about another Set.
    """
    if ctx.scope.designing:
        # The dispatcher refuses this tool in a design conversation before it
        # gets here; this is the same sentence for a caller that did not ask it.
        raise ValueError(DESIGN_RULE)
    set_id = _resolve_set(ctx, args.set_id)
    sets = SetsRepository(ctx.db)
    if await sets.get(set_id) is None:
        raise ValueError(f"No Set {set_id}.")

    prediction = args.prediction.strip()
    if len(prediction) < PREDICTION_MIN_CHARS:
        raise ValueError(
            "A change to a Set cannot be proposed without a prediction, because a change "
            "nobody committed to a guess about cannot turn out to be wrong. Say what should "
            "differ, by roughly how much, on which measure the archive records, and compared "
            "with which version — then call this again."
        )

    fields = {
        name: getattr(args, name)
        for name in (
            "grind_setting",
            "grind_value",
            "dose_g",
            "target_yield_g",
            "profile_version_id",
        )
        if getattr(args, name) is not None
    }
    patch = SetVersionPatch.model_validate(fields)
    groups = change_groups(patch)
    if not groups:
        raise ValueError(
            "A proposal has to change something. If the right next step is another shot on "
            "the same recipe, say so to the person in words — that is a legitimate answer "
            "and it needs no version. Otherwise name one of: profile_version_id, "
            "grind_setting, grind_value, dose_g, target_yield_g."
        )
    combined = args.combined_reason.strip()
    if len(groups) > 1 and len(combined) < PREDICTION_MIN_CHARS:
        raise ValueError(
            f"This would change {_and(groups)} at once, and then no prediction can say which "
            "of them did anything. Propose one of them. If they truly have to move together, "
            "call this again with combined_reason saying why, in at least "
            f"{PREDICTION_MIN_CHARS} characters."
        )

    proposals = SetProposalsRepository(ctx.db)
    spec: dict[str, Any] = {
        "thread_id": ctx.thread_id,
        "patch": patch,
        "reason": args.reason,
        "prediction": prediction,
        "combined_reason": combined,
    }
    # Omitted means "against the version this is a change to", which is what the
    # repository defaults to; passing None through would mean "against nothing".
    if args.compares_to_version_id is not None:
        spec["compares_to_version_id"] = args.compares_to_version_id
    result = await proposals.create(set_id, ProposalWrite.model_validate(spec))
    if result.refused is not None:
        raise ValueError(await _proposal_refusal(sets, set_id, result))
    stored = result.proposal
    assert stored is not None  # a result with no refusal carries the row

    preview = await proposals.preview(stored)
    base = await sets.get_version(stored.base_version_id)
    summary = (
        "; ".join(
            f"{change.label} {change.before or 'not set'} → {change.after or 'cleared'}"
            for change in version_changes(preview, base)
        )
        if preview is not None and base is not None
        else ""
    )
    note = (
        "Nothing has changed yet. This is waiting for the person: the next shot is still "
        f"filed under v{stored.base_version_no}, and it becomes a version only if they "
        "accept it. Tell them what you are proposing and why, and say what you expect it to "
        "do. If they decline it, that is information about what they want — not a reason to "
        "propose it again."
    )
    if len(groups) > 1:
        note += (
            f" You have proposed {_and(groups)} together, so say plainly that the prediction "
            "cannot separate them: whatever happens, it will not say which one did it."
        )
    return ProposeVersionOutput(
        proposal_id=stored.id,
        set_id=set_id,
        status=stored.status,
        changed=stored.changed,
        change_summary=summary,
        prediction=stored.prediction,
        compares_to_version_no=stored.compares_to_version_no,
        note=note,
    )


def _and(parts: list[str]) -> str:
    """ "the dose and the grind", "the dose, the grind and the profile"."""
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


async def _proposal_refusal(sets: SetsRepository, set_id: int, result: ProposalWriteResult) -> str:
    """The sentence a refused proposal comes back as.

    Each refusal names the rule and the way out, and none of them reveals
    anything outside this Set — a proposal is refused for what this experiment
    is doing, never for what another one is.
    """
    if result.refused == "already_waiting" and result.waiting is not None:
        waiting = result.waiting
        return (
            f"A proposal is already waiting for the person on this Set: it changes "
            f"{_and(waiting.changed)} — “{waiting.reason}” — predicting "
            f"“{waiting.prediction}”. Talk about that one instead of stacking "
            "another beside it. They accept or decline it on the Set page."
        )
    if result.refused == "outcome_open":
        current = await sets.current_version(set_id)
        number = f"v{current.version_no}" if current is not None else "this version"
        return (
            f"{number}'s prediction has not been graded yet, so there is nothing settled to "
            "build the next change on. Grade it with the person on the Set page first, or "
            "ask for another shot on the same recipe and say what you expect from it."
        )
    if result.refused == "bad_compare":
        return (
            "compares_to_version_id is not a version of this Set. Name one of this Set's own "
            "versions, or leave it out to compare against the version this change is a "
            "change to."
        )
    if result.refused == "bad_profile":
        return (
            "That profile version is not one this archive knows, so there is nothing for the "
            "Set to be switched to. list_profiles lists the ones it has; to change how a "
            "profile brews rather than which one is used, draft_profile is the tool."
        )
    if result.refused == "designing":
        return DESIGN_RULE
    if result.refused == "not_designing":
        return (
            "This Set already has a recipe, so there is no initial recipe to propose. Changes "
            "to it are propose_set_version, one at a time and with a prediction."
        )
    if result.refused == "bad_draft":
        return (
            "The profile draft for this recipe could not be attached to it. Nothing was "
            "proposed; call propose_initial_recipe again."
        )
    if result.refused == "bad_thread":
        # Not something a model can cause by choosing arguments: the
        # conversation is the runner's to supply. Said plainly anyway, because
        # a refusal a model cannot act on must at least not read as its fault.
        return (
            "This conversation is not one of this Set's, so a change argued here cannot be "
            "recorded against it. Nothing was written; tell the person, and open the Set's own "
            "conversation from its page."
        )
    return f"Set {set_id} has no current version to build a change on."


class DraftProfileInput(_Model):
    base_version_id: int = Field(gt=0, description="The profile version to start from.")
    patch: dict[str, Any] = Field(
        description=(
            "A partial profile document merged into the base, key by key. Phases are "
            "replaced wholesale when 'phases' is present — send the full list."
        )
    )
    reason: str = Field(min_length=1, max_length=500)
    prediction: str = Field(
        default="",
        max_length=1000,
        description=(
            "Required in a conversation about one Set, where a profile change IS a change "
            "to the experiment: what should differ if this works, by roughly how much, on "
            "which recorded measure, compared with which version. It is recorded on the Set "
            "when the person pushes this draft for that Set. Not asked for elsewhere — a "
            "draft that belongs to no experiment has nothing to be graded against."
        ),
    )
    compares_to_version_id: int | None = Field(
        default=None,
        description=(
            "Which version of this Set the prediction is measured against. Defaults to the "
            "Set's current version, which is what this change would be a change to."
        ),
    )


class DraftProfileOutput(_Model):
    draft_id: int
    status: str
    change_summary: str = ""
    clamp_changes: list[dict[str, Any]] = Field(default_factory=list)
    stop_condition_changes: list[dict[str, Any]] = Field(default_factory=list)
    #: What this draft is expected to do, and against which version — empty
    #: outside a Set's conversation.
    prediction: str = ""
    compares_to_version_no: int | None = None
    #: What the model should tell the person. A draft is further from the
    #: machine than a proposal is from the Set: somebody has to approve it, push
    #: it, and say which Set it is for.
    note: str = ""


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """A shallow-by-key, deep-by-dict merge. Lists replace, they never append.

    Appending to ``phases`` would be the wrong default in the one place it
    matters: a model correcting phase 2 of four would otherwise get eight.
    """
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge(current, value)
        else:
            merged[key] = value
    return merged


@tool(
    "draft_profile",
    permission="propose",
    description=(
        "Create a profile draft from an existing version plus a patch. The draft goes "
        "through the same schema, safety-policy and clamp checks as one typed by hand, "
        "and it is NOT pushed to the machine — a person approves and pushes it. In a "
        "conversation about one Set a profile change IS a change to the experiment, so a "
        "prediction is required and the same rules apply as to any other change: not while "
        "this version's own prediction is ungraded, and not while a proposal is already "
        "waiting."
    ),
    timeout_s=30.0,
)
async def draft_profile(ctx: ToolContext, args: DraftProfileInput) -> DraftProfileOutput:
    """The safety layers are untouched; what a Set conversation adds is a guess.

    Nothing about how a draft is built, clamped, checked or pushed changes here.
    What changes is that a draft argued inside one Set's conversation is an
    experiment on that Set — the temperature and the pressure curve are recipe
    as much as the grind is — so it owes a prediction and waits its turn like
    any other change.
    """
    if ctx.scope.designing:
        # A profile of a Set being designed is part of its initial recipe, and
        # proposed whole with it: see `propose_initial_recipe`.
        raise ValueError(DESIGN_RULE)
    if ctx.drafts is None:
        raise ValueError(
            "draft_profile needs the running gaggiclanker application; this connection has "
            "database access only."
        )
    version = await ProfilesRepository(ctx.db).get_version(args.base_version_id)
    if version is None:
        raise ValueError(f"No profile version {args.base_version_id}.")

    set_id: int | None = None
    prediction = args.prediction.strip()
    compares_to: int | None = None
    note = (
        "Nothing has been sent to the machine. This is a draft on the Profiles page: the "
        "person reads the diff, approves it and pushes it, and only then does the machine "
        "hold it."
    )
    if ctx.scope.kind == "set":
        set_id = _resolve_set(ctx, None)
        compares_to = await _draft_experiment(ctx, set_id, args, prediction)
        note += (
            " It is also a change to this experiment, so your prediction is recorded on the "
            "Set as a new version when they push it for this Set — and not before. Say that: "
            "until they push it, the Set is where it was."
        )

    document = _merge(dict(version.profile or {}), args.patch)
    draft = await ctx.drafts.create_manual(
        base_version_id=args.base_version_id,
        document=document,
        change_summary=args.reason,
        notes="Proposed in chat.",
        set_id=set_id,
        prediction=prediction if set_id is not None else "",
        compares_to_version_id=compares_to,
    )
    return DraftProfileOutput(
        draft_id=draft.id,
        status=draft.status,
        change_summary=draft.change_summary,
        clamp_changes=[_as_dict(change) for change in draft.clamp_changes or []],
        stop_condition_changes=[_as_dict(change) for change in draft.stop_condition_changes or []],
        prediction=draft.prediction,
        compares_to_version_no=draft.compares_to_version_no,
        note=note,
    )


async def _draft_experiment(
    ctx: ToolContext, set_id: int, args: DraftProfileInput, prediction: str
) -> int | None:
    """The rules a profile change owes inside a Set's conversation.

    The same three the proposal tool applies, in the same words, because they
    are the same rule and a model that met one of them phrased differently
    would read them as two: a change needs a prediction, the current version's
    own prediction has to have been graded first, and one change waits for the
    person before the next is put in front of them.
    """
    if len(prediction) < PREDICTION_MIN_CHARS:
        raise ValueError(
            "In a conversation about one Set a profile change is a change to the experiment, "
            "and it cannot be proposed without a prediction. Say what should differ, by "
            "roughly how much, on which measure the archive records, and compared with which "
            "version — then call this again."
        )
    sets = SetsRepository(ctx.db)
    current = await sets.current_version(set_id)
    if current is not None and current.outcome_state == "open":
        raise ValueError(
            f"v{current.version_no}'s prediction has not been graded yet, so there is nothing "
            "settled to build the next change on. Grade it with the person on the Set page "
            "first, or ask for another shot on the same recipe and say what you expect from it."
        )
    waiting = await SetProposalsRepository(ctx.db).waiting(set_id)
    if waiting is not None:
        raise ValueError(
            f"A proposal is already waiting for the person on this Set: it changes "
            f"{_and(waiting.changed)} — “{waiting.reason}”. Talk about that one "
            "instead of putting a second change beside it. They accept or decline it on the "
            "Set page."
        )
    compares_to = args.compares_to_version_id
    if compares_to is None:
        # The Set's **current** version, which is the same default
        # `propose_set_version` takes and for the same reason: a change is a
        # change to what is being brewed now. Not the version this conversation
        # is about — a conversation opened on v4 and still going after v6 was
        # recorded would otherwise predict against a recipe two changes old.
        return current.id if current is not None else None
    if await sets.version_of_set(set_id, compares_to) is None:
        raise ValueError(
            "compares_to_version_id is not a version of this Set. Name one of this Set's own "
            "versions, or leave it out to compare against the version this conversation is "
            "about."
        )
    return compares_to


class InitialProfileInput(_Model):
    base_version_id: int | None = Field(
        default=None,
        gt=0,
        description=(
            "The profile version the new profile starts from. Leave it out to start from the "
            "profile the person asked to fork, or, when they named none, from the library's "
            "most-used profile."
        ),
    )
    patch: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "A partial profile document merged into the base, key by key. Phases are replaced "
            "wholesale when 'phases' is present — send the full list. Read the base with "
            "get_profile first."
        ),
    )
    label: str = Field(
        min_length=1,
        max_length=80,
        description=(
            "The new profile's own name. It is a new profile, not a version of the one it "
            "started from, so it gets a name of its own."
        ),
    )


class ProposeInitialRecipeInput(_Model):
    profile: InitialProfileInput
    grind_setting: str = Field(
        min_length=1,
        max_length=100,
        description=(
            "In this grinder's own units. A number only when the person's usual setting or a "
            "Set on this same grinder anchors it; otherwise words relative to their usual "
            "espresso setting."
        ),
    )
    grind_is_absolute: bool = Field(
        description=(
            "True only when grind_setting is a number on this grinder's dial that one of those "
            "anchors supports."
        ),
    )
    dose_g: float = Field(gt=0, le=100)
    target_yield_g: float = Field(gt=0, le=500)
    reason: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "What this recipe is for, in a sentence or two. It becomes version 1's "
            "'What are you trying?' if the person accepts it."
        ),
    )


class InitialRecipe(_Model):
    """The recipe as version 1 would state it."""

    profile_version_id: int
    profile_label: str | None = None
    grind_setting: str
    grind_value: float | None = None
    dose_g: float
    target_yield_g: float
    #: What the new profile brews at, read from its own document.
    profile_temperature_c: float | None = None


class ProposeInitialRecipeOutput(_Model):
    """What is now waiting, and the plain statement that nothing exists yet."""

    proposal_id: int
    set_id: int
    draft_id: int
    kind: Literal["design"] = "design"
    status: str = "proposed"
    recipe: InitialRecipe
    reason: str = ""
    #: Every number the safety policy moved on the way in.
    clamp_changes: list[dict[str, Any]] = Field(default_factory=list)
    stop_condition_changes: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


@tool(
    "propose_initial_recipe",
    permission="propose",
    description=(
        "Propose the whole first recipe of this Set, which is being designed: a new profile of "
        "its own (a base plus a patch, with its own label) and the grind, dose and target "
        "yield, as ONE card the person accepts or declines. It creates the profile as a draft "
        "that goes through the same schema, safety-policy and clamp checks as one typed by "
        "hand, and nothing else: until the person accepts, the Set has no recipe, and the "
        "machine never receives anything from here. A profile identical to one already in the "
        "library is refused — give it its own label or change something. A newer proposal "
        "replaces the one waiting. No prediction: a version 1 is a baseline."
    ),
    timeout_s=30.0,
)
async def propose_initial_recipe(
    ctx: ToolContext, args: ProposeInitialRecipeInput
) -> ProposeInitialRecipeOutput:
    """Draft the profile, then put the whole recipe on one card for the person.

    The draft is made **now**, not when the card is accepted: a document the
    safety policy refuses comes back to the model as this tool's error, which
    it can fix in the same conversation, instead of as a refusal on the
    person's Accept button. It belongs to no Set on the draft side — no
    `set_id`, no prediction — because a draft that names a Set is that Set's
    profile *change*, which a push for the Set records as a new version; this
    one is the profile version 1 already names once the card is accepted.

    Nothing is left behind by a refusal: the policy and the not-new check run
    before anything is stored, and a proposal the repository refuses has its
    draft discarded.
    """
    set_id = _resolve_set(ctx, None)
    sets = SetsRepository(ctx.db)
    row = await sets.get(set_id)
    if row is None:  # pragma: no cover - the scope's Set exists by construction
        raise ValueError(f"No Set {set_id}.")
    if not ctx.scope.designing or not row.designing:
        raise ValueError(
            "This Set already has a recipe, so there is no initial recipe to propose. Changes "
            "to it are propose_set_version, one at a time and with a prediction."
        )
    if ctx.drafts is None:
        raise ValueError(
            "propose_initial_recipe needs the running gaggiclanker application; this "
            "connection has database access only."
        )

    profiles = ProfilesRepository(ctx.db)
    base_id = (
        args.profile.base_version_id
        or row.design_brief.fork_profile_version_id
        or await profiles.default_draft_base()
    )
    base = await profiles.get_version(base_id)
    if base is None or not base.profile:
        raise ValueError(
            f"No profile version {base_id}. list_profiles lists the ones this archive has."
        )
    document = _merge(dict(base.profile), args.profile.patch)
    document["label"] = args.profile.label

    proposals = SetProposalsRepository(ctx.db)
    draft = await ctx.drafts.create_manual(
        base_version_id=base_id,
        document=document,
        change_summary=args.reason,
        notes=f"Designed in chat for Set “{row.name}”.",
        new_profile_only=True,
        reusable_version_ids=await proposals.design_profile_versions(set_id),
    )
    assert draft.draft_version_id is not None  # a stored draft names its version

    fields: dict[str, Any] = {
        "profile_version_id": draft.draft_version_id,
        "grind_setting": args.grind_setting,
        "dose_g": args.dose_g,
        "target_yield_g": args.target_yield_g,
    }
    value = grind_value(args.grind_setting, absolute=args.grind_is_absolute)
    if value is not None:
        fields["grind_value"] = value
    result = await proposals.create(
        set_id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            thread_id=ctx.thread_id,
            reason=args.reason,
            patch=SetVersionPatch.model_validate(fields),
        ),
    )
    if result.refused is not None or result.proposal is None:
        # The card was not stored, so the draft it would have carried is
        # nobody's: discarded rather than left open on the Profiles page.
        await ProfileDraftsRepository(ctx.db).discard_unsent([draft.id])
        raise ValueError(await _proposal_refusal(sets, set_id, result))
    stored = result.proposal

    preview = await proposals.preview(stored)
    assert preview is not None  # just stored, readable, on a version that exists
    return ProposeInitialRecipeOutput(
        proposal_id=stored.id,
        set_id=set_id,
        draft_id=draft.id,
        status=stored.status,
        recipe=InitialRecipe(
            profile_version_id=draft.draft_version_id,
            profile_label=preview.profile_label,
            grind_setting=args.grind_setting,
            grind_value=value,
            dose_g=args.dose_g,
            target_yield_g=args.target_yield_g,
            profile_temperature_c=preview.profile_temperature_c,
        ),
        reason=stored.reason,
        clamp_changes=[_as_dict(change) for change in draft.clamp_changes or []],
        stop_condition_changes=[_as_dict(change) for change in draft.stop_condition_changes or []],
        note=(
            "Nothing exists yet. This is a card waiting for the person: if they accept it, it "
            "becomes this Set's version 1, and the profile is then a draft on the Profiles page "
            "for them to approve and push — nothing brews it until they do. If they would "
            "rather change something, propose again: a newer card replaces this one."
        ),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value) if isinstance(value, dict) else {"value": str(value)}


class RecordInsightInput(_Model):
    text: str = Field(min_length=1, max_length=2000)
    evidence_shot_ids: list[int] = Field(default_factory=list, max_length=20)
    bean_id: int | None = None
    roast_level: str | None = None
    process: str | None = None
    origin: str | None = None
    grinder_id: int | None = None
    profile_style: str | None = None


class RecordInsightOutput(_Model):
    insight_id: int
    scope: str
    text: str
    confirmed: bool = False


#: What an insight may be scoped by in a Set's conversation, and where each
#: value has to come from. The Set's own attributes and no others: an insight
#: written here is about this coffee on this grinder, and one scoped by
#: somebody else's bean would be a statement about a Set this conversation
#: cannot see.
_SET_SCOPE_FIELDS = ("bean_id", "grinder_id", "roast_level", "process", "origin")


async def _check_set_scope(ctx: ToolContext, set_id: int, args: RecordInsightInput) -> None:
    """Refuse an insight scoped by anything that is not this Set's own.

    The evidence ids go through the same check the shot tools use, in the same
    words: naming another Set's shot as evidence would be a way of asking
    whether it exists.
    """
    sets = SetsRepository(ctx.db)
    row = await sets.get(set_id)
    if row is None:  # pragma: no cover - the scope's Set exists by construction
        raise ValueError(f"No Set {set_id}.")
    if args.evidence_shot_ids:
        mine = await sets.shot_ids(set_id)
        for shot_id in args.evidence_shot_ids:
            if shot_id not in mine:
                raise ValueError(
                    f"Shot {shot_id} is not a shot of this Set. This conversation can see "
                    "this Set's shots only — list_set_shots is how to find them."
                )
    bean = await BeansRepository(ctx.db).get(row.bean_id) if row.bean_id else None
    expected: dict[str, Any] = {
        "bean_id": row.bean_id,
        "grinder_id": row.grinder_id,
        "roast_level": getattr(bean, "roast_level", None),
        "process": getattr(bean, "process", None),
        "origin": getattr(bean, "origin", None),
    }
    for key in ("bean_id", "roast_level", "process", "origin", "grinder_id", "profile_style"):
        given = getattr(args, key)
        if given is None:
            continue
        if key not in _SET_SCOPE_FIELDS or given != expected[key]:
            raise ValueError(
                f"An insight from this conversation is about this Set: its bean, its "
                f"grinder, its roast level and its process. {key} is not one of them. "
                "Leave the scope out and it is recorded against this Set's own."
            )


@tool(
    "record_insight",
    permission="propose",
    description=(
        "Record something learned about THIS kitchen, scoped to whatever it is about. "
        "It is stored unconfirmed and reaches no future prompt until the user confirms "
        "it, so propose one only when a shot or two actually supports it. In a "
        "conversation about one Set it is about that Set: its own bean, grinder, roast "
        "level and process, and its own shots as evidence."
    ),
)
async def record_insight(ctx: ToolContext, args: RecordInsightInput) -> RecordInsightOutput:
    if ctx.scope.kind == "set":
        await _check_set_scope(ctx, _resolve_set(ctx, None), args)
    scope = InsightScope.model_validate(
        {
            key: getattr(args, key)
            for key in (
                "bean_id",
                "roast_level",
                "process",
                "origin",
                "grinder_id",
                "profile_style",
            )
            if getattr(args, key) is not None
        }
    )
    repo = InsightsRepository(ctx.db)
    insight_id = await repo.insert(
        InsightWrite(
            scope=scope,
            text=args.text,
            evidence_shot_ids=args.evidence_shot_ids,
            source="chat",
            confirmed=False,
        )
    )
    stored = await repo.get(insight_id)
    assert stored is not None  # just inserted
    return RecordInsightOutput(
        insight_id=stored.id,
        scope=stored.scope_label,
        text=stored.text,
        confirmed=stored.confirmed,
    )


# ── starting_point ───────────────────────────────────────────────────


class StartingPointInput(_Model):
    bean_id: int = Field(gt=0)
    grinder_id: int | None = Field(
        default=None, description="Omit for pre-ground coffee or a grinder nobody has recorded."
    )
    usual_grind: str = Field(
        default="",
        max_length=100,
        description=(
            "What they normally grind espresso at, in this grinder's own units. Pass it "
            "whenever they have said it: it is the only thing that lets the answer be a "
            "number on their dial rather than a direction."
        ),
    )
    dose_hint_g: float | None = Field(default=None, gt=0, le=100)


class StartingPointOutput(_Model):
    run_id: int
    status: str
    started: bool = False
    bean_id: int
    #: The three options, exactly as stored. Handed back whole rather than
    #: summarised: the model that asked for this is about to explain it, and a
    #: summary would make it paraphrase a paraphrase.
    output: dict[str, Any] | None = None
    error: str | None = None


@tool(
    "starting_point",
    permission="propose",
    description=(
        "Ask for three starting points — conservative, recommended, adventurous — for a bag "
        "nobody has brewed yet, anchored on similar past Sets and the rule tier. Runs in the "
        "background and returns the row; read it back with this tool's run_id once it is "
        "done. Nothing is created until a person accepts one in the UI. It spends provider "
        "tokens, so it is rate limited."
    ),
    timeout_s=30.0,
)
async def starting_point(ctx: ToolContext, args: StartingPointInput) -> StartingPointOutput:
    """Propose-class and limited, for exactly `run_analysis`'s reasons.

    It creates nothing a person has to decide about — the Set only exists once
    somebody accepts an option — but it queues a provider call, and handing a
    read-only agent a button that spends money is not a read. The limiter is the
    analysis bucket at the analysis limit: going through a tool must not be a
    way around what the route is already stopped from doing.
    """
    if ctx.rate_limits is not None:
        try:
            ctx.rate_limits.check(
                "analysis",
                f"tool:{ctx.user or ctx.caller}",
                limit=ANALYSIS_RATE_LIMIT,
                window=ANALYSIS_WINDOW_SECONDS,
            )
        except TooManyRequests as exc:
            raise ValueError(str(exc)) from None
    if ctx.starting is None or ctx.tasks is None:
        raise ValueError(
            "starting_point needs the running gaggiclanker application; this connection has "
            "database access only."
        )
    try:
        row, started = await ctx.starting.start(
            bean_id=args.bean_id,
            grinder_id=args.grinder_id,
            usual_grind=args.usual_grind,
            dose_hint_g=args.dose_hint_g,
            tasks=ctx.tasks,
        )
    except LookupError as exc:
        raise ValueError(str(exc)) from None
    return StartingPointOutput(
        run_id=row.id,
        status=row.status,
        started=started,
        bean_id=row.bean_id,
        output=row.output if isinstance(row.output, dict) else None,
        error=row.error,
    )


class ReadStartingPointInput(_Model):
    run_id: int = Field(gt=0)


@tool(
    "get_starting_point",
    permission="read",
    description=(
        "Read a starting-point run back: its status and, once it is done, its three options. "
        "Use it after starting_point to see what came out."
    ),
)
async def get_starting_point(ctx: ToolContext, args: ReadStartingPointInput) -> StartingPointOutput:
    """A read, unlike `starting_point` itself: it spends nothing and creates nothing."""
    from gaggiclanker.db.repos.starting import StartingPointRunsRepository

    row = await StartingPointRunsRepository(ctx.db).get(args.run_id)
    if row is None:
        raise ValueError(f"No starting point {args.run_id}.")
    return StartingPointOutput(
        run_id=row.id,
        status=row.status,
        bean_id=row.bean_id,
        output=row.output,
        error=row.error,
    )

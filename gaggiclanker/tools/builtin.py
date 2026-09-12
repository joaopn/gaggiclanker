"""The tools themselves. One module, because the set is small and domain-bound.

Seventeen tools in two permission classes, and the third is empty on purpose.
The read tools answer questions about the archive; the propose tools turn a
conclusion into a row somebody still has to confirm, or queue work that costs
money; there are no device-write tools here at all, and that is the feature —
pushing a profile and deleting a shot off the machine stay buttons in the UI.

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
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch
from gaggiclanker.infra.errors import TooManyRequests
from gaggiclanker.infra.ratelimit import ANALYSIS_RATE_LIMIT, ANALYSIS_WINDOW_SECONDS
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.tools.registry import ToolContext, tool
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
    "the judgement, so most questions need no join at all."
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
    if found is None:
        raise ValueError(f"No shot {args.shot_id} in the archive.")
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
    "target_temperature_c",
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
        if found is None:
            raise ValueError(f"No shot {shot_id} in the archive.")
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
    resolved = given if given is not None else ctx.set_id
    if resolved is None:
        raise ValueError(
            "No set_id was given and this conversation is not scoped to a Set. "
            "Call list_sets and pass one."
        )
    return resolved


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
        machine_id=row.machine_id,
    )
    return await InsightsRepository(ctx.db).select(attributes)


class ListSetsInput(_Model):
    include_archived: bool = False


class ListOutput(_Model):
    items: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


@tool("list_sets", permission="read", description="Every Set, the active one first.")
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
    return ListOutput(items=items, count=len(items))


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
            "signal:taste:sour). A rule is returned when every dimension it states is "
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
            "evidence — do not reason from one without saying it is unconfirmed."
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
        "a bean, a grinder, a roast level or their combination."
    ),
)
async def get_insights(ctx: ToolContext, args: GetInsightsInput) -> GetInsightsOutput:
    repo = InsightsRepository(ctx.db)
    scope_id = args.set_id if args.set_id is not None else ctx.set_id
    if scope_id is not None and not args.include_unconfirmed:
        row = await SetsRepository(ctx.db).get(scope_id)
        if row is None:
            raise ValueError(f"No Set {scope_id}.")
        rows = await _set_insights(ctx, row)
    else:
        rows = await repo.list_insights(confirmed=None if args.include_unconfirmed else True)
    return GetInsightsOutput(
        insights=[
            InsightOut(
                id=insight.id,
                scope=insight.scope_label,
                text=insight.text,
                evidence_shot_ids=list(insight.evidence_shot_ids or []),
                source=insight.source,
                confirmed=insight.confirmed,
            )
            for insight in rows
        ]
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


class ProposeVersionInput(_Model):
    set_id: int | None = None
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="One sentence: what this version is trying to find out.",
    )
    grind_setting: str | None = Field(default=None, max_length=100)
    grind_value: float | None = Field(default=None, ge=0, le=10000)
    dose_g: float | None = Field(default=None, gt=0, le=100)
    target_yield_g: float | None = Field(default=None, gt=0, le=500)
    target_temperature_c: float | None = Field(default=None, ge=25, le=150)
    profile_version_id: int | None = None


class ProposeVersionOutput(_Model):
    version: dict[str, Any]
    changed: list[str] = Field(default_factory=list)


@tool(
    "propose_set_version",
    permission="propose",
    description=(
        "Create the next version of a Set with the fields you name changed and the rest "
        "inherited. Change one variable at a time. The version is recorded with "
        "origin='chat' and is immediately live for new shots, so say what you created."
    ),
)
async def propose_set_version(ctx: ToolContext, args: ProposeVersionInput) -> ProposeVersionOutput:
    set_id = _resolve_set(ctx, args.set_id)
    sets = SetsRepository(ctx.db)
    if await sets.get(set_id) is None:
        raise ValueError(f"No Set {set_id}.")

    fields = {
        name: getattr(args, name)
        for name in (
            "grind_setting",
            "grind_value",
            "dose_g",
            "target_yield_g",
            "target_temperature_c",
            "profile_version_id",
        )
        if getattr(args, name) is not None
    }
    if not fields:
        raise ValueError(
            "A new version has to change something. Name at least one of: grind_setting, "
            "grind_value, dose_g, target_yield_g, target_temperature_c, profile_version_id."
        )
    patch = SetVersionPatch(intent=args.reason, origin="chat", **fields)
    version = await sets.add_version(set_id, patch)
    if version is None:  # pragma: no cover - get() above proved the Set exists
        raise ValueError(f"Set {set_id} has no current version to build on.")
    return ProposeVersionOutput(version=version.model_dump(mode="json"), changed=sorted(fields))


class DraftProfileInput(_Model):
    base_version_id: int = Field(gt=0, description="The profile version to start from.")
    patch: dict[str, Any] = Field(
        description=(
            "A partial profile document merged into the base, key by key. Phases are "
            "replaced wholesale when 'phases' is present — send the full list."
        )
    )
    reason: str = Field(min_length=1, max_length=500)


class DraftProfileOutput(_Model):
    draft_id: int
    status: str
    change_summary: str = ""
    clamp_changes: list[dict[str, Any]] = Field(default_factory=list)
    stop_condition_changes: list[dict[str, Any]] = Field(default_factory=list)


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
        "and it is NOT pushed to the machine — a person approves and pushes it."
    ),
    timeout_s=30.0,
)
async def draft_profile(ctx: ToolContext, args: DraftProfileInput) -> DraftProfileOutput:
    if ctx.drafts is None:
        raise ValueError(
            "draft_profile needs the running gaggiclanker application; this connection has "
            "database access only."
        )
    version = await ProfilesRepository(ctx.db).get_version(args.base_version_id)
    if version is None:
        raise ValueError(f"No profile version {args.base_version_id}.")
    document = _merge(dict(version.profile or {}), args.patch)
    draft = await ctx.drafts.create_manual(
        base_version_id=args.base_version_id,
        document=document,
        change_summary=args.reason,
        notes="Proposed in chat.",
    )
    return DraftProfileOutput(
        draft_id=draft.id,
        status=draft.status,
        change_summary=draft.change_summary,
        clamp_changes=[_as_dict(change) for change in draft.clamp_changes],
        stop_condition_changes=[_as_dict(change) for change in draft.stop_condition_changes],
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
    machine_id: int | None = None


class RecordInsightOutput(_Model):
    insight_id: int
    scope: str
    text: str
    confirmed: bool = False


@tool(
    "record_insight",
    permission="propose",
    description=(
        "Record something learned about THIS kitchen, scoped to whatever it is about. "
        "It is stored unconfirmed and reaches no future prompt until the user confirms "
        "it, so propose one only when a shot or two actually supports it."
    ),
)
async def record_insight(ctx: ToolContext, args: RecordInsightInput) -> RecordInsightOutput:
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
                "machine_id",
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

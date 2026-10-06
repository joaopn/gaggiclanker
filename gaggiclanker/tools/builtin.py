"""The tools themselves. One module, because the set is small and domain-bound.

Twenty-two tools in two permission classes, and the third is empty on purpose.
The read tools answer questions about the archive; the propose tools turn a
conclusion into a row somebody still has to confirm; there are no device-write
tools here at all, and that is the feature —
making a profile active (which the next sync puts on the machine) stays a button in the UI.

Which of the twenty-two a conversation *has* is not decided here:
:mod:`gaggiclanker.tools.scope` decides it from the conversation's kind. What
is decided here is what a tool does when it is called inside a Set's
conversation — the Set is the conversation's and another one is refused, and a
shot filed elsewhere is refused in words that do not say whether it exists.

Two shapes recur and are worth stating once. **Every output is a pydantic model**,
so the JSON the model reads is the JSON the schema promised and `mypy --strict`
checks the middle. And **a missing service is an ordinary error**, not an
exception: the stdio MCP entry point opens a database and nothing else, so a
tool that needs more has to say so rather than raise `AttributeError` at the
bottom of a stack the caller cannot see.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from gaggiclanker.db.repos.beans import BeansRepository
from gaggiclanker.db.repos.grinders import GrindersRepository
from gaggiclanker.db.repos.insight_deletions import (
    REASON_MAX,
    REASON_MIN,
    InsightDeletionRefusal,
    InsightDeletionsRepository,
    InsightDeletionWrite,
)
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.knowledge_insights import (
    InsightProposeRefusal,
    InsightsRepository,
    InsightWrite,
)
from gaggiclanker.db.repos.outcome_proposals import (
    OutcomeProposalsRepository,
    OutcomeProposalWrite,
)
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, stored_document
from gaggiclanker.db.repos.set_proposals import (
    ProposalWrite,
    ProposalWriteResult,
    SetProposalsRepository,
    change_groups,
)
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, version_changes
from gaggiclanker.db.repos.version_names import named_dump, next_names
from gaggiclanker.domain.phase_names import raw_phase_names
from gaggiclanker.domain.profile_recipe import profile_recipe
from gaggiclanker.domain.sets import grind_value, parse_version_label
from gaggiclanker.domain.signature import ExpectationInput, SignatureRefused
from gaggiclanker.domain.vocab import VERSION_OUTCOMES, Balance
from gaggiclanker.infra.errors import Unprocessable
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.shotinfo.catalogue import ITEMS, ShotTier, Tier, effective_tiers
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.glossary import extended_meanings
from gaggiclanker.shotinfo.render import load_shots, needs_samples, render_shot, with_samples
from gaggiclanker.shotinfo.search import SEARCH_LIMIT, ShotQuery, search_shots
from gaggiclanker.signatures.lines import signature_lines
from gaggiclanker.signatures.service import DraftSignature, SignatureService, validate_all
from gaggiclanker.tools.registry import ToolContext, tool
from gaggiclanker.tools.scope import DESIGN_RULE
from gaggiclanker.tools.sql import (
    ALLOWED_VIEWS,
    DEFAULT_ROW_LIMIT,
    MAX_ROW_LIMIT,
    SqlRefused,
    run_query,
)

log = structlog.get_logger(__name__)

__all__ = ["EXAMPLE_QUERIES"]


class _Model(BaseModel):
    """Every tool model forbids extras: a typo'd argument is a refusal, not a silent no-op."""

    model_config = ConfigDict(extra="forbid")


# ── describe_schema ──────────────────────────────────────────────────

#: Shown with the schema. Examples are worth more than column lists to a model
#: that has to write correlated SQL, and these four are the shapes that actually
#: come up: one Set's shots, a per-version average, a join to a shot's confirmed claims,
#: and a curve slice.
EXAMPLE_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "The ten most recent shots in one Set, newest first",
        "SELECT shot_id, started_at, set_version_label, volume_g, ratio, rating\n"
        "  FROM v_shots WHERE set_id = 3 ORDER BY started_at DESC LIMIT 10",
    ),
    (
        "Average yield, ratio and rating per version of a Set, the version with the earliest shot "
        "first",
        "SELECT set_version_label, COUNT(*) AS shots,\n"
        "       ROUND(AVG(volume_g), 1) AS avg_yield_g,\n"
        "       ROUND(AVG(ratio), 2) AS avg_ratio,\n"
        "       ROUND(AVG(rating), 2) AS avg_rating\n"
        "  FROM v_shots WHERE set_id = 3\n"
        " GROUP BY set_version_id, set_version_label ORDER BY MIN(started_at)",
    ),
    (
        "The claims a person confirmed about a Set's shots, with the fault word and the phase "
        "they are about",
        "SELECT s.shot_id, c.fault, c.window_text, c.text\n"
        "  FROM v_shots s JOIN v_review_claims c ON c.shot_id = s.shot_id AND c.kind = 'claim'\n"
        " WHERE s.set_id = 3 AND c.fault IS NOT NULL\n"
        " ORDER BY s.started_at DESC LIMIT 20",
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
    "versions differ in temperature only when they name different profiles. A Set version "
    "is named v<major>.<minor>: version_label (set_version_label in v_shots) is the name to "
    "say, 'v1.1'; version_major and version_minor are its parts. A name is an identifier, "
    "not a position: v1.2 may have been made after v2, so sort versions by created_at and "
    "never infer order from the name. is_current (v_set_versions) and current_version_label "
    "(v_sets) say which version a Set is on, and parent_version_label which one a version "
    "was made from. v_review_claims holds only the claims a person confirmed in a shot's "
    "newest finished reading (the fault word, the window it is about, its text and the "
    "numbers behind it); v_reviews says how many claims a reading has confirmed and how many "
    "are still unverified."
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
#
# Three tools and one renderer. A shot is shown to a model in two tiers from
# the shot information catalogue — base, which the opening context and the
# search show for every shot, and extended, which is asked for — and these
# tools are the only way to get either for one shot. Which item sits in which
# tier is read per call through `effective_tiers`, so the stdio server and the
# chat's own dispatcher answer with the same lines.


class ShotIdInput(_Model):
    shot_id: int = Field(gt=0, description="The shot's id, as `shot <id>` heads its rendering.")


class _WithMeanings(_Model):
    """An output that may open with the extended fields' meanings.

    On one extended read of the conversation only (see ``_extended_meanings``).
    A field of the parent so it is declared first and read before the lines it
    explains, and left out of the output altogether when there is none.
    """

    field_meanings: str | None = None

    @model_serializer(mode="wrap")
    def _without_empty_meanings(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("field_meanings") is None:
            data.pop("field_meanings", None)
        return data


class ShotTextOutput(_WithMeanings):
    """One shot, rendered at one tier, as plain text."""

    shot_id: int
    tier: ShotTier
    text: str


def _extended_meanings(ctx: ToolContext, tiers: Mapping[str, Tier]) -> str | None:
    """The extended half of the glossary, for this run's first read when the history has none.

    Called only after the shot has been rendered, so a read that failed (an
    unknown shot, one filed elsewhere) never reaches it and leaves the
    attachment for the next. There is no await between the check and the claim,
    so two reads of one round cannot both carry it. The state is the run's
    (:class:`~gaggiclanker.tools.registry.RunNotes`), the same on the chat's
    dispatcher and on the stdio MCP server.
    """
    if ctx.notes.extended_meanings_sent:
        return None
    meanings = extended_meanings(tiers)
    if meanings is None:
        return None
    ctx.notes.extended_meanings_sent = True
    log.info("extended_meanings_attached", run_id=ctx.run_id, caller=ctx.caller)
    return meanings


async def _shots_in_scope(
    ctx: ToolContext, shot_ids: list[int], tier: ShotTier
) -> tuple[list[ShotFacts], Mapping[str, Tier]]:
    """The shots, in the order asked, or the scope's refusal for the first one it refuses.

    Checked before the samples are read, so a refused shot costs nothing but
    its row, and in the same words whether the shot exists or not.
    """
    tiers = await effective_tiers(ctx.db)
    loaded = {facts.shot_id: facts for facts in await load_shots(ctx.db, shot_ids)}
    for shot_id in shot_ids:
        found = loaded.get(shot_id)
        _shot_of_scope(ctx, shot_id, {"set_id": found.set_id} if found is not None else None)
    shots = [loaded[shot_id] for shot_id in shot_ids]
    if needs_samples(tier, tiers):
        shots = await with_samples(ctx.db, shots)
    return shots, tiers


async def _curve_points(ctx: ToolContext) -> int:
    """The `chatCurvePoints` setting, read per call like the tiers."""
    return int(await ctx.settings.get("chatCurvePoints"))


async def _one_shot(ctx: ToolContext, shot_id: int, tier: ShotTier) -> ShotTextOutput:
    [facts], tiers = await _shots_in_scope(ctx, [shot_id], tier)
    text = render_shot(facts, tier, tiers, curve_points=await _curve_points(ctx))
    meanings = _extended_meanings(ctx, tiers) if tier != "base" else None
    return ShotTextOutput(field_meanings=meanings, shot_id=shot_id, tier=tier, text=text)


@tool(
    "get_shot",
    permission="read",
    description=(
        "One shot's base information: what it is and where it is filed, its outcome, the "
        "headline diagnostics and the person's judgement — the lines a Set conversation's "
        "opening context shows for every shot. Cite a shot by its id. get_shot_extended adds "
        "the rest; get_shot_full is both. In a Set conversation the shots in the opening "
        "context are already there in base information, so ask for one of those with "
        "get_shot_extended, not this."
    ),
)
async def get_shot(ctx: ToolContext, args: ShotIdInput) -> ShotTextOutput:
    return await _one_shot(ctx, args.shot_id, "base")


@tool(
    "get_shot_extended",
    permission="read",
    description=(
        "One shot's extended information, without its base lines: temperature, pressure and "
        "flow statistics, profile compliance, the checks that held (and the context and "
        "free-text expectations) of a confirmed signature, one line per phase (the pressure, "
        "the flows, the water, the temperature, the adherence and the resistance; each "
        "phase's name, "
        "duration, how it ended and its cup at the end are in base) and the "
        "curve as one table (its shape and every moment the diagnostics are about, not every "
        "sample). Ask for it when the base lines raise a question the shape of the shot "
        "would answer."
    ),
)
async def get_shot_extended(ctx: ToolContext, args: ShotIdInput) -> ShotTextOutput:
    return await _one_shot(ctx, args.shot_id, "extended")


@tool(
    "get_shot_full",
    permission="read",
    description=(
        "One shot's base and extended information together. The largest answer a shot gives "
        "(it carries the curve), so keep it for the shot a question turns on. In a Set "
        "conversation the shots in the opening context are already there in base information; "
        "for one of those, get_shot_extended adds only what is missing."
    ),
)
async def get_shot_full(ctx: ToolContext, args: ShotIdInput) -> ShotTextOutput:
    return await _one_shot(ctx, args.shot_id, "full")


class CompareInput(_Model):
    shot_ids: list[int] = Field(min_length=2, max_length=4)


class CompareOutput(_WithMeanings):
    shots: list[ShotTextOutput]


@tool(
    "compare_shots",
    permission="read",
    description=(
        "Two to four shots, each in full (base and extended, curve table included), in the "
        "order given, to read side by side. Use it for 'did the grind change actually help' "
        "questions. Large: prefer get_shot for a shot you only need the headline of."
    ),
)
async def compare_shots(ctx: ToolContext, args: CompareInput) -> CompareOutput:
    shots, tiers = await _shots_in_scope(ctx, args.shot_ids, "full")
    points = await _curve_points(ctx)
    return CompareOutput(
        field_meanings=_extended_meanings(ctx, tiers),
        shots=[
            ShotTextOutput(
                shot_id=facts.shot_id,
                tier="full",
                text=render_shot(facts, "full", tiers, curve_points=points),
            )
            for facts in shots
        ],
    )


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
        set=named_dump(row),
        versions=[named_dump(version) for version in versions],
        trajectory=[named_dump(version) for version in trends.versions],
        insights=[insight.render() for insight in insights],
    )


async def _set_insights(ctx: ToolContext, row: Any) -> list[Any]:
    """The confirmed insights that apply to a Set, through the one selection.

    The same ``InsightsRepository.for_set`` the opening context and the Set page
    use, so the chat and the page cannot disagree about which insights apply —
    reimplementing the rule here is how the two drift. The Set's own, then the
    general ones whose scope matches it; never another Set's.
    """
    return await InsightsRepository(ctx.db).for_set(row.id)


class Range(_Model):
    """Both ends inclusive; leave one out for an open range."""

    min: float | None = None
    max: float | None = None


#: What `order_by` may name, and the catalogue item each one is.
_ORDER_KEYS: dict[str, str] = {
    "date": "date",
    "rating": "rating",
    "shot_time": "shot_time",
    "yield_g": "yield",
    "first_drip": "first_drip",
    "peak_pressure": "peak_pressure",
    "brew_flow": "brew_flow",
    "dose_in": "dose_in",
    "dose_out": "dose_out",
    "ratio": "ratio",
    "resistance_level": "resistance_level",
    "pressure_adherence": "pressure_adherence",
    "flow_adherence": "flow_adherence",
}


class SearchShotsInput(_Model):
    version: str | int | float | None = Field(
        default=None,
        description=(
            "Only the shots pulled under this version of the Set, by its name as a string: "
            "'v1.1', '1.1' or '2' (which is v2, not the second version). A number loses "
            "trailing zeros, so 1.10 would arrive as v1.1."
        ),
    )
    label: Literal["keep", "improve", "discard"] | None = Field(
        default=None,
        description=(
            "Only the shots labelled this way. Keep is the gold standard, Improve is what you "
            "are working on, and Discard says the shot went wrong rather than the recipe."
        ),
    )
    balance: Balance | None = Field(default=None, description="Only this taste balance.")
    since: date | None = Field(default=None, description="From this day (UTC), inclusive.")
    until: date | None = Field(default=None, description="Up to this day (UTC), inclusive.")
    rating: Range | None = Field(default=None, description="1 to 5 stars.")
    shot_time: Range | None = Field(default=None, description="Seconds.")
    yield_g: Range | None = Field(default=None, description="The scale's final weight, grams.")
    first_drip: Range | None = Field(default=None, description="Seconds.")
    peak_pressure: Range | None = Field(default=None, description="Bar.")
    brew_flow: Range | None = Field(default=None, description="Average brew flow, ml/s.")
    dose_in: Range | None = Field(default=None, description="Grams.")
    dose_out: Range | None = Field(default=None, description="Grams.")
    ratio: Range | None = Field(
        default=None,
        description="Dose out / dose in, e.g. 2.0 (the version's dose when none was typed).",
    )
    resistance_level: Range | None = Field(
        default=None, description="The puck's average resistance, a unitless number."
    )
    pressure_adherence: Range | None = Field(
        default=None, description="RMSE of the pressure against its target, bar."
    )
    flow_adherence: Range | None = Field(
        default=None, description="RMSE of the pump flow against its target, ml/s."
    )
    order_by: Literal[
        "date",
        "rating",
        "shot_time",
        "yield_g",
        "first_drip",
        "peak_pressure",
        "brew_flow",
        "dose_in",
        "dose_out",
        "ratio",
        "resistance_level",
        "pressure_adherence",
        "flow_adherence",
    ] = Field(
        default="date",
        description=(
            "What the results are sorted by. Left out, the date, or the shot id when the date "
            "is not shared with you."
        ),
    )
    descending: bool = Field(default=True, description="Largest (or newest) first.")
    limit: int = Field(default=SEARCH_LIMIT, ge=1, le=SEARCH_LIMIT)


class ShotHit(_Model):
    shot_id: int
    #: The shot's base rendering.
    text: str


class SearchShotsOutput(_Model):
    shots: list[ShotHit] = Field(default_factory=list)
    count: int = 0
    #: True when more shots matched than came back, so a model can narrow the
    #: search rather than conclude the Set has only these.
    truncated: bool = False


#: Which catalogue item each search argument reads, beyond the ranges whose
#: argument names already are (or map through `_ORDER_KEYS` to) the key.
_ARGUMENT_ITEMS: dict[str, str] = {
    "version": "set_version",
    "label": "label",
    "balance": "balance",
    "since": "started_at",
    "until": "started_at",
    **{name: key for name, key in _ORDER_KEYS.items() if name != "date"},
}


def _order(args: SearchShotsInput, tiers: Mapping[str, Tier]) -> str:
    """What the results are sorted by: the one asked for, else the date.

    With the date excluded and no sort asked for, the shot id instead, which is
    locked in base: a search the agent made without naming the date must not be
    refused for sorting on it. A sort on the date it did name still is.
    """
    if "order_by" in args.model_fields_set or tiers.get("started_at") != "excluded":
        return args.order_by
    return "shot_id"


def _refuse_excluded(args: SearchShotsInput, tiers: Mapping[str, Tier], order: str) -> None:
    """Refuse a filter or a sort on an item the agent is not shown.

    Searching on an excluded item would hand back what excluding it withheld:
    which shots are above a rating is the rating, a bisection away. Which items
    are excluded is no secret — the glossary leaves them out — so the refusal
    names the item. An extended item stays searchable, since the agent may read
    it with `get_shot_extended` anyway.
    """
    used = [name for name in _ARGUMENT_ITEMS if getattr(args, name) is not None]
    sorted_on = {"date": "started_at", "shot_id": "shot_id"}.get(order) or _ORDER_KEYS[order]
    keys = [_ARGUMENT_ITEMS[name] for name in used] + [sorted_on]
    for key in dict.fromkeys(keys):
        if tiers.get(key, "excluded") == "excluded":
            raise ValueError(
                f"{ITEMS[key].name} is not shared with the agent, so the search cannot filter "
                "or sort on it; leave that argument out."
            )


@tool(
    "list_set_shots",
    permission="read",
    description=(
        "Search this Set's shots on their base information, and get each match's base "
        "rendering. Filter by version, label, balance and dates, or by a range on shot time, "
        "yield, first drip, peak pressure, average brew flow, rating, dose in, dose out, ratio, "
        "resistance level, pressure adherence or flow adherence; sort by date or any of those "
        "numbers. At most 10 shots come back; "
        "truncated says more matched. A shot with no value for a filter never matches it. A "
        "shot that is not counted is read, never averaged. The shots in the opening context are "
        "already there in base information: use this for shots outside it (other versions, "
        "older shots, a range)."
    ),
)
async def list_set_shots(ctx: ToolContext, args: SearchShotsInput) -> SearchShotsOutput:
    """No ``set_id``: the Set is the conversation's, which is the whole point.

    A Set conversation searches its own shots and cannot be pointed at anybody
    else's, so there is no argument here to point wrongly.
    """
    set_id = _resolve_set(ctx, None)
    tiers = await effective_tiers(ctx.db)
    order = _order(args, tiers)
    _refuse_excluded(args, tiers, order)
    version_id = None if args.version is None else await _named_version(ctx, set_id, args.version)
    ranges = {
        _ORDER_KEYS[name]: (bounds.min, bounds.max)
        for name in (
            "rating",
            "shot_time",
            "yield_g",
            "first_drip",
            "peak_pressure",
            "brew_flow",
            "dose_in",
            "dose_out",
            "ratio",
            "resistance_level",
            "pressure_adherence",
            "flow_adherence",
        )
        if (bounds := getattr(args, name)) is not None
    }
    found = await search_shots(
        ctx.db,
        set_id,
        ShotQuery(
            version_id=version_id,
            label=args.label,
            balance=args.balance,
            since=args.since.isoformat() if args.since is not None else None,
            until=args.until.isoformat() if args.until is not None else None,
            ranges=ranges,
            order_by=_ORDER_KEYS.get(order, order),
            descending=args.descending,
            limit=args.limit,
        ),
    )
    points = await _curve_points(ctx)
    # The samples of the results only, in one query, and only when a person
    # moved a curve channel into base: the search itself reads none.
    shots = await with_samples(ctx.db, found.shots) if needs_samples("base", tiers) else found.shots
    return SearchShotsOutput(
        shots=[
            ShotHit(
                shot_id=facts.shot_id,
                text=render_shot(facts, "base", tiers, curve_points=points),
            )
            for facts in shots
        ],
        count=len(found.shots),
        truncated=found.truncated,
    )


async def _named_version(ctx: ToolContext, set_id: int, name: str | float) -> int:
    """The id of this Set's version called ``name``, or a refusal saying which names exist.

    The name is what the agent reads everywhere ("v1.1"); an ordinal is never
    accepted as one — "2" is v2. The list in the refusal is this Set's own
    versions only, which the conversation can see anyway.
    """
    parsed = parse_version_label(str(name))
    sets = SetsRepository(ctx.db)
    found = None if parsed is None else await sets.version_named(set_id, *parsed)
    if found is not None:
        return found.id
    names = [version.version_label for version in reversed(await sets.versions(set_id))]
    raise ValueError(
        f"This Set has no version called {str(name)!r}. Its versions are "
        f"{', '.join(names)}; name one of them as 'v1.1', '1.1' or '2', or leave version out."
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
    items = [named_dump(row) for row in rows]
    return ListOutput(items=items, count=len(items))


class ListBeansInput(_Model):
    include_archived: bool = False


@tool(
    "list_beans",
    permission="read",
    description=(
        "The bean catalogue. Each coffee carries only the fields the person filled in; "
        "acidity, intensity and sweetness are their reading of the coffee, 1 low to 5 high."
    ),
)
async def list_beans(ctx: ToolContext, args: ListBeansInput) -> ListOutput:
    rows = await BeansRepository(ctx.db).list_all(include_archived=args.include_archived)
    # A field nobody filled in is left out rather than sent as null or "": the
    # model reads `"roast_level": null` as something known about the coffee.
    items = [
        {
            key: value
            for key, value in row.model_dump(mode="json").items()
            if value not in (None, "")
        }
        for row in rows
    ]
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
    description=(
        "Brew profile versions known to the archive, newest first. on_machine says whether "
        "the machine has that version now; only those can be brewed as they are."
    ),
)
async def list_profiles(ctx: ToolContext, args: ListProfilesInput) -> ListOutput:
    # `on_machine` is what a proposal naming a profile needs to know: a version
    # the machine does not have can only get there as a pushed draft.
    rows = await ctx.db.fetch_all(
        """
        SELECT p.*,
               EXISTS (SELECT 1 FROM device_profiles d
                        WHERE d.current_version_id = p.profile_version_id
                          AND d.deleted_at IS NULL) AS on_machine
          FROM v_profiles p
         ORDER BY p.created_at DESC, p.profile_version_id DESC
         LIMIT ?
        """,
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
    #: The version's stored canonical document, whole.
    document: dict[str, Any]
    recipe: ProfileRecipeFacts
    #: What the profile is for: the lines of its **confirmed** signature, tier first, each with
    #: the expectation's id. Empty when it has none; a proposed or rejected expectation is
    #: never here.
    signature: list[str] = Field(default_factory=list)
    #: How many of the archive's shots were pulled with it. Only in a general
    #: conversation: see :func:`list_profiles` for why a Set's does not get it.
    shot_count: int | None = None


@tool(
    "get_profile",
    permission="read",
    description=(
        "One profile version in full: its label, type and whole document — every phase, pump "
        "target and stop condition — plus the temperature and yield the document states. Read "
        "the profile you are about to change or fork before you change it. When the opening "
        "context shows the profile a version brews (a Set conversation's does, for its own "
        "version and the one it is compared against), it is already there, whole: this is for "
        "other profiles and versions."
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
    document = stored_document(version)
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
        signature=await signature_lines(ctx.db, version.id),
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
            "Only the confirmed insights that apply to this Set: its own, and the general "
            "ones whose scope matches it. Defaults to the conversation's Set; omit both to "
            "list every confirmed general insight."
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
    #: The line the opening context carries for this insight: its id, where it was
    #: learned, the versions it rests on (their outcome as it stands now, marked when
    #: it changed) and its shots. Built by ``InsightRow.render``, the one place.
    line: str = ""
    evidence_shot_ids: list[int] = Field(default_factory=list)
    source: str = ""
    confirmed: bool = False


class GetInsightsOutput(_Model):
    insights: list[InsightOut] = Field(default_factory=list)


@tool(
    "get_insights",
    permission="read",
    description=(
        "What this archive has learned about this kitchen: confirmed insights. An insight "
        "is either about one Set (learned in its conversations) or general, scoped to a "
        "bean, a grinder, a roast level or their combination. In a conversation about "
        "one Set it answers with the confirmed insights that apply to that Set — its own "
        "and the general ones that match it, the same ones the opening context lists — "
        "and nothing else."
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

    # A general conversation reads general insights, and the selection of one
    # Set when it names it (it is read-only across the archive). Never the
    # other Sets' own insights: those reach their Set's conversations only.
    if args.set_id is not None and not args.include_unconfirmed:
        row = await SetsRepository(ctx.db).get(args.set_id)
        if row is None:
            raise ValueError(f"No Set {args.set_id}.")
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
        line=insight.render(),
        evidence_shot_ids=(
            evidence if only is None else [shot_id for shot_id in evidence if shot_id in only]
        ),
        source=insight.source,
        confirmed=insight.confirmed,
    )


# ── propose ──────────────────────────────────────────────────────────


#: The shortest thing this tool will accept as a prediction. Not a measure of
#: quality — twenty characters of nonsense is still nonsense — but it is the
#: difference between a sentence somebody wrote and a field somebody filled in
#: to get past a check. What a *good* prediction says (direction, rough size,
#: which recorded measure, which version) is the prompt's business; the tool
#: only refuses the absence.
PREDICTION_MIN_CHARS = 20

#: How many expectations one proposal may carry: a signature is what a profile is for, in a
#: handful of lines, not an inventory.
SIGNATURE_MAX_EXPECTATIONS = 12

#: What a major version is, in the maintainer's words, for both tools that may
#: suggest one. The person decides on the card; the agent only suggests.
MAJOR_MEANING = (
    "A Set version is named v<major>.<minor>. A major version (v1.2 → v2) is a functional "
    "change to what the profile wants to do, and the person triggers it; dial-in technical "
    "changes stay minor (v1.2 → v1.3): grind, dose, yield, or a draft that only tunes a "
    "parameter such as a degree of temperature. By default a switch to a different profile "
    "(another entry of the profile list, not a newer version of the same profile) is major "
    "and everything else is minor. You may suggest major with suggest_major and "
    "major_reason; the card shows your reason and the person decides."
)

MAJOR_REASON_REFUSAL = (
    "suggest_major needs major_reason: say in at least {chars} characters why this change is "
    "a functional change to what the profile does rather than dialling in. The person reads "
    "it beside the Major change box and decides; leave suggest_major out to let the default "
    "stand."
)


def _major_suggestion(suggest_major: bool, major_reason: str) -> tuple[bool, str]:
    """The agent's major suggestion as stored, or its refusal.

    A suggestion without a reason is refused, like a change without a
    prediction: the person decides on the card, and a bare "major" gives them
    nothing to decide with. A reason with no suggestion is dropped — it would
    be a reason for something nobody suggested.
    """
    reason = major_reason.strip()
    if not suggest_major:
        return False, ""
    if len(reason) < PREDICTION_MIN_CHARS:
        raise ValueError(MAJOR_REASON_REFUSAL.format(chars=PREDICTION_MIN_CHARS))
    return True, reason


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
    suggest_major: bool = Field(
        default=False,
        description=(
            "Suggest that the person record this as a major version (the next vN) rather "
            "than a minor one (vN.M+1). Only for a functional change to what the profile "
            "does; requires major_reason. The person decides."
        ),
    )
    major_reason: str = Field(
        default="",
        max_length=500,
        description=f"With suggest_major: why, in at least {PREDICTION_MIN_CHARS} characters.",
    )


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
    #: The compared-to version's name, "v1.1".
    compares_to_version: str | None = None
    #: Whether the agent suggested a major version; the person decides.
    suggest_major: bool = False
    #: What the version would be called if they accept it as a minor and as a
    #: major, so the model can say both without numbering anything itself.
    would_be_minor: str = ""
    would_be_major: str = ""
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
        "change is a profile change — use draft_profile, and the person makes it active "
        "(with writes on, the next sync puts it on the machine). " + MAJOR_MEANING
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
    suggest_major, major_reason = _major_suggestion(args.suggest_major, args.major_reason)

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

    if patch.profile_version_id is not None:
        profiles = ProfilesRepository(ctx.db)
        if (
            await profiles.get_version(patch.profile_version_id) is not None
            and await profiles.find_device_id_for_version(patch.profile_version_id) is None
        ):
            # Accepting records the version and sends nothing, and nothing in
            # this app can put an existing profile version on the machine: only
            # a draft is ever pushed. A version naming a profile the machine
            # does not have is a recipe nobody can brew, and the person would
            # find that out at the machine, after the press. (A profile the
            # archive does not know at all is the repository's refusal below.)
            raise ValueError(
                f"Profile version {patch.profile_version_id} is not on the machine, and "
                "nothing in this app can put an existing profile version there, so a Set "
                "version naming it could not be brewed. list_profiles says which profiles are "
                "on the machine (on_machine). To brew this one, use draft_profile with it as "
                "the base: the person makes the proposal active for this Set, and the Set "
                "records the version when the next sync puts it on the machine."
            )

    proposals = SetProposalsRepository(ctx.db)
    spec: dict[str, Any] = {
        "thread_id": ctx.thread_id,
        "patch": patch,
        "reason": args.reason,
        "prediction": prediction,
        "combined_reason": combined,
        "suggest_major": suggest_major,
        "major_reason": major_reason,
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
    names = await next_names(ctx.db, set_id)
    default = "major" if await proposals.default_major(stored) else "minor"
    note = (
        "Nothing has changed yet. This is waiting for the person: the next shot is still "
        f"filed under {stored.base_version_label}, and it becomes a version only if they "
        "accept it. Tell them what you are proposing and why, and say what you expect it to "
        "do. If they decline it, that is information about what they want — not a reason to "
        "propose it again. On the card they choose whether it is a minor version "
        f"({names.minor}) or a major one ({names.major}); by default it is {default}"
        + (", and your suggestion of major is shown with your reason" if suggest_major else "")
        + ". Do not name the new version as if it were decided."
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
        compares_to_version=stored.compares_to_version_label,
        suggest_major=stored.suggest_major,
        would_be_minor=names.minor,
        would_be_major=names.major,
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
        number = current.version_label if current is not None else "this version"
        return (
            f"{number}'s prediction has not been graded yet, so there is nothing settled to "
            "build the next change on. Propose its outcome first (propose_outcome) and then "
            "propose the change in the same answer, or ask for another shot on the same recipe "
            "and say what you expect from it."
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


class ProposeOutcomeInput(_Model):
    outcome: str = Field(
        description=(
            "The whole prediction's outcome: held, partly_held, failed or inconclusive. "
            "Say inconclusive rather than stretch one shot into a result."
        )
    )
    note: str = Field(
        default="",
        max_length=1000,
        description=(
            "The per-claim lines the person reads before they accept: each claim of the "
            "prediction, what the shots showed against it, and the evidence. At least "
            f"{PREDICTION_MIN_CHARS} characters."
        ),
    )


class ProposeOutcomeOutput(_Model):
    """What is now waiting, and the plain statement that nothing is recorded."""

    proposal_id: int
    set_id: int
    version: str
    outcome: str
    counted_shots: int
    status: str = "proposed"
    #: Whether this replaced a grade of the same version that was still waiting.
    replaced_waiting_grade: bool = False
    note: str = ""


@tool(
    "propose_outcome",
    permission="propose",
    description=(
        "Propose how THIS conversation's version turned out — held, partly_held, failed or "
        "inconclusive — graded against all of its counted shots (Keep or Improve), never one "
        "shot. It records nothing: the person sees a card and accepts it, records another "
        "outcome, or dismisses it, and until they accept it the version's outcome is what it "
        "was. Refused when the version has no prediction (there is nothing to grade) or no "
        "shot the person has judged Keep or Improve. Call it at the end of your grade, "
        "once; a second call replaces the first while the person has not answered. Once it "
        "is waiting you may propose the next version in the same answer, and accepting that "
        "version records the grade."
    ),
)
async def propose_outcome(ctx: ToolContext, args: ProposeOutcomeInput) -> ProposeOutcomeOutput:
    """The agent's grade as a card. Never the grade itself.

    Every refusal is an error value with a sentence of its own, for the reason
    the other propose tools give. The version is the **conversation's own** and
    no argument can name another: a Set conversation is about one version, and a
    grade of some other version would be an argument made in the wrong room.
    """
    if ctx.scope.designing:
        raise ValueError(DESIGN_RULE)
    set_id = _resolve_set(ctx, None)
    sets = SetsRepository(ctx.db)
    version = (
        await sets.version_of_set(set_id, ctx.scope.set_version_id)
        if ctx.scope.set_version_id is not None
        else await sets.current_version(set_id)
    )
    if version is None:
        raise ValueError(
            "This conversation's version no longer exists, so there is nothing to grade."
        )
    outcome = args.outcome.strip().lower()
    if outcome not in VERSION_OUTCOMES:
        raise ValueError(
            f"outcome must be one of {', '.join(VERSION_OUTCOMES)}. It is the whole "
            "prediction's outcome, one word, with the per-claim lines in the note."
        )
    note = args.note.strip()
    if len(note) < PREDICTION_MIN_CHARS:
        raise ValueError(
            "A grade needs its reasons: the note is the per-claim lines the person reads "
            f"before they accept, at least {PREDICTION_MIN_CHARS} characters. Say what each "
            "claim of the prediction did against the shots, then call this again."
        )
    if not version.prediction:
        raise ValueError(
            f"{version.version_label} has no prediction, so there is nothing to grade. A "
            "version made by hand or a first recipe states none; say how its "
            "shots look in words, and let the person decide what to try next."
        )
    result = await OutcomeProposalsRepository(ctx.db).create(
        set_id,
        version.id,
        OutcomeProposalWrite.model_validate(
            {"outcome": outcome, "note": note, "thread_id": ctx.thread_id}
        ),
    )
    if result.refused == "nothing_to_grade":
        raise ValueError(
            f"{version.version_label} has no shot the person has judged Keep or Improve yet, so "
            "there is nothing to grade it on. Ask them to label a shot, or say what you would "
            "look for in the next one."
        )
    if result.refused == "bad_thread":
        raise ValueError(
            "This conversation is not one of this Set's, so a grade argued here cannot be "
            "recorded against it. Nothing was written; tell the person, and open the Set's own "
            "conversation from its page."
        )
    if result.refused is not None or result.proposal is None:
        raise ValueError(f"{version.version_label} cannot be graded right now.")
    stored = result.proposal
    return ProposeOutcomeOutput(
        proposal_id=stored.id,
        set_id=set_id,
        version=version.version_label,
        outcome=stored.outcome,
        counted_shots=stored.counted_shots,
        status=stored.status,
        replaced_waiting_grade=result.replaced is not None,
        note=(
            f"Nothing is recorded until the person accepts it: {version.version_label}'s outcome "
            "is still "
            + ("open" if version.outcome is None else f"{version.outcome} as they recorded it")
            + f". They see a card with your grade ({stored.outcome.replace('_', ' ')}, on "
            f"{stored.counted_shots} counted shot{'' if stored.counted_shots == 1 else 's'}) and "
            "can accept it, record another outcome, or dismiss it. Say the grade in one "
            "sentence with the per-claim lines above it. If you also propose the next version "
            "now, accepting that version records this grade; do not name the next version "
            "as decided."
        ),
    )


# ── propose_signature ────────────────────────────────────────────────


class ProposeSignatureInput(_Model):
    profile_version_id: int = Field(
        gt=0,
        description=(
            "The profile version these expectations are for: in a Set conversation, a profile "
            "this Set's versions brew (the one this version brews is at the top of your "
            "context); while a Set is being designed, the profile it forks or the one on the "
            "card."
        ),
    )
    expectations: list[ExpectationInput] = Field(
        min_length=1,
        max_length=SIGNATURE_MAX_EXPECTATIONS,
        description=(
            "What the profile is for, one expectation per line: a tier (critical, important or "
            "context), a phase the profile names, and one of four kinds. measure: an expression "
            "{channel, op, window, relative_to, compare} that every shot is checked against, "
            "for example the cup at the end of the ramp, as a share of the target yield, at "
            'most 0.15: {"channel": "cup_weight", "op": "at_end", "window": {"phase": "ramp"}, '
            '"relative_to": "target_yield", "compare": {"op": "<=", "value": 0.15}}. reached: '
            "the phase must begin. expects_warning: a universal warning (fast flow, skipped, "
            "over target, under target) is part of the design. free_text: what no expression "
            "says, with the fault word it fails with. Prefer values relative to the target "
            "yield or the dose, so one signature carries across beans and doses."
        ),
    )
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Why these expectations: what this profile is built to do.",
    )


class ProposedExpectation(_Model):
    id: int
    tier: str
    kind: str
    phase: str | None
    #: The fault word it fails with; empty for a measure bounded on both sides, which fails
    #: with one word per side.
    fault: str
    sentence: str


class ProposeSignatureOutput(_Model):
    profile_version_id: int
    profile_label: str
    proposed: list[ProposedExpectation]
    #: How many confirmed expectations this profile version already has.
    already_confirmed: int
    status: str = "proposed"
    note: str = ""


async def _signature_profile_versions(ctx: ToolContext, set_id: int) -> set[int]:
    """The profile versions a conversation may propose a signature for.

    A Set conversation: the profile versions its own versions brew. A Set being designed:
    the profile it forks and the ones its cards carried. Anything else is refused in one
    sentence whether or not it exists.
    """
    if ctx.scope.designing:
        row = await SetsRepository(ctx.db).get(set_id)
        found = set(await SetProposalsRepository(ctx.db).design_profile_versions(set_id))
        if row is not None and row.design_brief.fork_profile_version_id is not None:
            found.add(row.design_brief.fork_profile_version_id)
        return found
    return {
        version.profile_version_id
        for version in await SetsRepository(ctx.db).versions(set_id)
        if version.profile_version_id is not None
    }


@tool(
    "propose_signature",
    permission="propose",
    description=(
        "Propose what a profile version is FOR, as expectations a person confirms once: each "
        "has a tier (critical, important, context), a phase the profile names and a kind "
        "(measure, reached, expects_warning, free_text). Once confirmed, every shot on that "
        "profile is checked against them at no cost and the Review column shows 'ramp: early "
        "yield' in red when one fails. It writes only PROPOSED rows: nothing is checked, "
        "shown as a result or told to any agent until the person confirms it on the Profiles "
        "page. A measure must parse, carry a compare and name phases the profile has; a "
        "refusal names every problem so you can call again. Use it when the conversation turns "
        "to how the shots behave and the profile has no confirmed signature, not at every "
        "message; write what the profile is built to do, not what one shot did."
    ),
)
async def propose_signature(
    ctx: ToolContext, args: ProposeSignatureInput
) -> ProposeSignatureOutput:
    """The agent's signature as rows a person answers. Never a check."""
    set_id = _resolve_set(ctx, None)
    allowed = await _signature_profile_versions(ctx, set_id)
    if args.profile_version_id not in allowed:
        raise ValueError(
            "That profile version is not one this conversation is about. "
            + (
                "While a Set is being designed, propose for the profile it forks or the one "
                "on the card."
                if ctx.scope.designing
                else "Propose for a profile one of this Set's versions brews: the one this "
                "version brews is at the top of your context."
            )
        )
    service = SignatureService(ctx.db)
    try:
        rows = await service.propose(
            args.profile_version_id,
            args.expectations,
            reason=args.reason.strip(),
            thread_id=ctx.thread_id,
        )
    except SignatureRefused as refused:
        raise ValueError(str(refused)) from None
    version = await ProfilesRepository(ctx.db).get_version(args.profile_version_id)
    confirmed = (await service.repo.confirmed_for_versions([args.profile_version_id])).get(
        args.profile_version_id, []
    )
    return ProposeSignatureOutput(
        profile_version_id=args.profile_version_id,
        profile_label=version.label if version is not None else "",
        proposed=[
            ProposedExpectation(
                id=row.id,
                tier=row.tier,
                kind=row.kind,
                phase=row.phase,
                fault=row.fault or "",
                sentence=row.sentence,
            )
            for row in rows
        ],
        already_confirmed=len(confirmed),
        note=(
            f"{len(rows)} expectation{'' if len(rows) == 1 else 's'} stored as proposed. "
            "Nothing is checked against them until the person confirms each one (or all of "
            "them) on the Profiles page, under this profile version's Signature card: say "
            "that, and do not describe a shot as passing or failing one. If they reject one "
            "they may say why, and you are told at your next message."
        ),
    )


class ProposeOverrideInput(_Model):
    expectation_id: int = Field(
        gt=0,
        description="The confirmed measure to loosen or tighten, by its #id in your context.",
    )
    compare: dict[str, Any] = Field(
        description=(
            "The new limit, with the same comparison as the profile's: for 'at most 0.15' "
            'send {"op": "<=", "value": 0.2}. Only the numbers change.'
        )
    )
    reason: str = Field(min_length=1, max_length=500)


class ProposeOverrideOutput(_Model):
    override_id: int
    version: str
    expectation_id: int
    status: str = "proposed"
    note: str = ""


@tool(
    "propose_signature_override",
    permission="propose",
    description=(
        "Propose a different limit for ONE confirmed measure of the profile's signature, for "
        "THIS conversation's version only (a coarser bean, say: 'at most 0.20 here'). Only "
        "the numbers of the compare change: never the expression, the tier or the phase. It "
        "writes a proposed row; the person confirms it on the Set page, and it applies to "
        "this version's shots alone. One live override per version; a newer proposal "
        "replaces a waiting one."
    ),
)
async def propose_signature_override(
    ctx: ToolContext, args: ProposeOverrideInput
) -> ProposeOverrideOutput:
    """An override as a row a person answers. The version is the conversation's own."""
    if ctx.scope.designing:
        raise ValueError(DESIGN_RULE)
    set_id = _resolve_set(ctx, None)
    sets = SetsRepository(ctx.db)
    version = (
        await sets.version_of_set(set_id, ctx.scope.set_version_id)
        if ctx.scope.set_version_id is not None
        else await sets.current_version(set_id)
    )
    if version is None:
        raise ValueError("This conversation's version no longer exists.")
    try:
        row = await SignatureService(ctx.db).propose_override(
            set_version_id=version.id,
            profile_version_id=version.profile_version_id,
            expectation_id=args.expectation_id,
            compare=args.compare,
            reason=args.reason.strip(),
            thread_id=ctx.thread_id,
        )
    except SignatureRefused as refused:
        raise ValueError(str(refused)) from None
    return ProposeOverrideOutput(
        override_id=row.id,
        version=version.version_label,
        expectation_id=row.expectation_id,
        note=(
            f"Stored as proposed for {version.version_label}. It changes nothing until the "
            "person confirms it on the Set page, and then only this version's shots read the "
            "new limit."
        ),
    )


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
            "when this draft reaches the machine for that Set. Not asked for elsewhere — a "
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
    suggest_major: bool = Field(
        default=False,
        description=(
            "In a conversation about one Set only: suggest that putting this draft on the "
            "machine for the Set records a major version rather than a minor one. A draft that "
            "tunes a parameter is dialling in and stays minor; suggest major only for a "
            "functional change to what the profile does, with major_reason. The person decides."
        ),
    )
    major_reason: str = Field(
        default="",
        max_length=500,
        description=f"With suggest_major: why, in at least {PREDICTION_MIN_CHARS} characters.",
    )
    signature: list[ExpectationInput] = Field(
        default_factory=list,
        max_length=SIGNATURE_MAX_EXPECTATIONS,
        description=(
            "Optional: what this profile is for, written down with the change, as expectations "
            "(see propose_signature for their shape). They are validated against the phases of "
            "the profile as it will be after your patch, stored as PROPOSED on the draft's "
            "version, and used by nothing until the person confirms them on the Profiles page."
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
    #: The compared-to version's name, "v1.1".
    compares_to_version: str | None = None
    #: Whether the agent suggested a major version when it is made active for the Set; the
    #: person decides.
    suggest_major: bool = False
    #: How many expectations were stored, as proposed, with the draft.
    signature_proposed: int = 0
    #: What the model should tell the person. A draft is further from the
    #: machine than a proposal is from the Set: somebody has to make it active
    #: (saying which Set it is for), and a sync with writes on then sends it.
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
        "and it is NOT on the machine — a person makes it active, and with writes on the "
        "next sync puts it on the machine. In a "
        "conversation about one Set a profile change IS a change to the experiment, so a "
        "prediction is required and the same rules apply as to any other change: not while "
        "this version's own prediction is ungraded, and not while a proposal is already "
        "waiting. " + MAJOR_MEANING
    ),
    timeout_s=30.0,
)
async def draft_profile(ctx: ToolContext, args: DraftProfileInput) -> DraftProfileOutput:
    """The safety layers are untouched; what a Set conversation adds is a guess.

    Nothing about how a draft is built, clamped, checked or sent changes here.
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
    if args.suggest_major and ctx.scope.kind != "set":
        raise ValueError(
            "suggest_major only means something in a conversation about one Set: it is about "
            "which version it records there. Leave it out here."
        )
    suggest_major, major_reason = _major_suggestion(args.suggest_major, args.major_reason)
    note = (
        "Nothing has been sent to the machine. This is a proposal on the Profiles page, "
        "inside its profile: the person reads the diff and makes it active, and only after a "
        "sync with writes on does the machine hold it."
    )
    if ctx.scope.kind == "set":
        set_id = _resolve_set(ctx, None)
        compares_to = await _draft_experiment(ctx, set_id, args, prediction)
        names = await next_names(ctx.db, set_id)
        note += (
            " It is also a change to this experiment, so your prediction is recorded on the "
            "Set as a new version when it reaches the machine for this Set — and not before. Say "
            "that: until then the Set is where it was. When they put it there they choose "
            f"whether it is a minor version ({names.minor}, the default for a draft) or a "
            f"major one ({names.major})"
            + (", and your suggestion of major is shown with your reason" if suggest_major else "")
            + "."
        )

    document = _merge(dict(version.profile or {}), args.patch)
    carried: DraftSignature | None = None
    if args.signature:
        # Validated before anything is stored, against the phases the draft will have, so a
        # refusal leaves no draft behind and the model can fix the expectation and call again.
        try:
            valid = validate_all(args.signature, raw_phase_names(document) or [])
        except SignatureRefused as refused:
            raise ValueError(str(refused)) from None
        carried = DraftSignature(
            expectations=tuple(valid), reason=args.reason, thread_id=ctx.thread_id
        )
        note += (
            f" {len(valid)} expectation{'' if len(valid) == 1 else 's'} of what this profile is "
            "for were stored as proposed with the draft: nothing is checked against them until "
            "the person confirms them on the Profiles page."
        )
    draft = await ctx.drafts.create_manual(
        base_version_id=args.base_version_id,
        document=document,
        change_summary=args.reason,
        notes="Proposed in chat.",
        set_id=set_id,
        prediction=prediction if set_id is not None else "",
        compares_to_version_id=compares_to,
        suggest_major=suggest_major,
        major_reason=major_reason,
        signature=carried,
    )
    return DraftProfileOutput(
        draft_id=draft.id,
        status=draft.status,
        change_summary=draft.change_summary,
        clamp_changes=[_as_dict(change) for change in draft.clamp_changes or []],
        stop_condition_changes=[_as_dict(change) for change in draft.stop_condition_changes or []],
        prediction=draft.prediction,
        compares_to_version=draft.compares_to_version_label,
        suggest_major=draft.suggest_major,
        signature_proposed=(
            len(carried.expectations) - len(carried.skipped) if carried is not None else 0
        ),
        note=note
        + (
            f" {len(carried.skipped)} of the expectations were not stored again: the draft's "
            "version already has them (carried over from its base, or sent twice)."
            if carried is not None and carried.skipped
            else ""
        ),
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
    if current is not None and await SetProposalsRepository(ctx.db).outcome_blocks(current):
        raise ValueError(
            f"{current.version_label}'s prediction has not been graded yet, so there is nothing "
            "settled to build the next change on. Propose its outcome first (propose_outcome) "
            "and then draft the change in the same answer, or ask for another shot on the "
            "same recipe and say what you expect from it."
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
    """The new profile: the person's fork source plus a patch, or a whole document.

    There is deliberately no base to name. The only profile a design starts
    from is the one the person picked to fork in the New Set dialog; with
    none, the agent writes the profile from zero. Letting the model pick a
    base put the library's most-used profile under every unforked design, so
    whatever that profile carried and the patch left alone — its description,
    its temperature, its phases — came along into a profile that was meant to
    be new.
    """

    patch: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "With a profile to fork: a partial profile document merged into it, key by key "
            "(read it in the context above). With none: the whole profile document, written "
            "from zero — type, description, temperature and every phase. Either way phases "
            "are replaced wholesale when 'phases' is present — send the full list."
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
        "Propose the whole first recipe of this Set, which is being designed: a new profile "
        "of its own (the profile the person asked to fork plus a patch, or, when they named "
        "none, a whole document written from zero; with its own label either way) and the "
        "grind, dose and target yield, as ONE card the person accepts or declines. It "
        "creates the profile as a draft that goes through the same schema, safety-policy and "
        "clamp checks as one typed by hand, and nothing else: until the person accepts, the "
        "Set has no recipe, and the machine never receives anything from here. A profile "
        "identical to one already in the library is refused — give it its own label or "
        "change something. A newer proposal replaces the one waiting. No prediction: a "
        "version 1 is a baseline."
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
    profile *change*, which making it active for the Set records as a new version; this
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
    fork_id = row.design_brief.fork_profile_version_id
    if fork_id is not None:
        fork = await profiles.get_version(fork_id)
        if fork is None or not fork.profile:  # pragma: no cover - checked by the design route
            raise ValueError(f"The profile to fork, version {fork_id}, is gone.")
        base_id = fork_id
        document = _merge(dict(fork.profile), args.profile.patch)
    else:
        # Written from zero: the document is the patch and nothing else. The
        # empty baseline is only the base a draft must have; the draft is marked new, so the
        # Profiles page shows the profile and no diff.
        base_id = await profiles.empty_base()
        document = dict(args.profile.patch)
    document["label"] = args.profile.label

    proposals = SetProposalsRepository(ctx.db)
    try:
        draft = await ctx.drafts.create_manual(
            base_version_id=base_id,
            document=document,
            change_summary=args.reason,
            notes=f"Designed in chat for Set “{row.name}”.",
            # With a fork the draft is an edit of it, and its diff is the point.
            is_new=fork_id is None,
            new_profile_only=True,
            reusable_version_ids=await proposals.design_profile_versions(set_id),
        )
    except Unprocessable as exc:
        # The schema's per-field lines live in the error's details, which the
        # dispatcher does not show the model; a profile written from zero is
        # where they matter, since there is no fork to have filled the gaps.
        problems = (exc.details or {}).get("schema_errors") or []
        if not problems:
            raise
        raise ValueError(f"{exc}: " + "; ".join(str(problem) for problem in problems)) from None
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
            "becomes this Set's version 1, and the profile is then a proposal on the Profiles page "
            "for them to make active (the next sync with writes on puts it on the machine) "
            "— nothing brews it until it is there. "
            "If they would rather change something, propose again: a newer card replaces this one."
        ),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value) if isinstance(value, dict) else {"value": str(value)}


async def _resting_version(ctx: ToolContext, set_id: int, name: str) -> int:
    """The id of the version an insight is said to rest on, or a sentence naming this Set's own.

    Not :func:`_named_version`: its refusal tells the model to "leave version out", an
    argument this tool does not have.
    """
    parsed = parse_version_label(name)
    sets = SetsRepository(ctx.db)
    found = None if parsed is None else await sets.version_named(set_id, *parsed)
    if found is not None:
        return found.id
    names = [version.version_label for version in reversed(await sets.versions(set_id))]
    raise ValueError(
        f"This Set has no version called {name!r}. Its versions are {', '.join(names)}; "
        "name the ones the insight rests on as 'v1.2', or leave rests_on_versions out and "
        "name shots instead."
    )


class RecordInsightInput(_Model):
    """What an insight says and what it rests on. Nothing scopes it: it is about this Set.

    Extras are allowed on this one model only so that an attribute scope (a bean,
    a grinder, a roast level) a model still reaches for gets a sentence saying
    why it is not accepted, instead of a validation error about an unknown
    field. The schema the model is shown names ``text``, what it rests on and the
    insight it replaces, and nothing else.
    """

    model_config = ConfigDict(extra="allow")

    text: str = Field(min_length=1, max_length=2000)
    evidence_shot_ids: list[int] = Field(
        default_factory=list,
        max_length=20,
        description="Shots of this Set the insight comes from.",
    )
    rests_on_versions: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Versions of this Set whose recorded outcome the insight depends on, named "
            "as the record names them (v1.2). Each must have an outcome the person has "
            "recorded. At least one shot or one version is required."
        ),
    )
    replaces_insight_id: int | None = Field(
        default=None,
        description=(
            "The number of an ADDED insight of this Set that this one replaces. When the "
            "person adds this one, that one is deleted, so this one must say everything "
            "that is still true. Leave out to propose an insight on its own."
        ),
    )


class RecordInsightOutput(_Model):
    insight_id: int
    scope: str
    text: str
    confirmed: bool = False
    rests_on: list[str] = Field(default_factory=list)
    replaces_insight_id: int | None = None
    #: What the model should tell the person.
    note: str = ""


#: What used to scope an insight, and is now refused with a sentence.
_RETIRED_SCOPE_FIELDS = (
    "bean_id",
    "roast_level",
    "process",
    "origin",
    "grinder_id",
    "profile_style",
)


async def _check_evidence(ctx: ToolContext, set_id: int, args: RecordInsightInput) -> None:
    """Refuse evidence that is not this Set's, in the words the shot tools use.

    Naming another Set's shot as evidence would be a way of asking whether it
    exists.
    """
    if not args.evidence_shot_ids:
        return
    mine = await SetsRepository(ctx.db).shot_ids(set_id)
    for shot_id in args.evidence_shot_ids:
        if shot_id not in mine:
            raise ValueError(
                f"Shot {shot_id} is not a shot of this Set. This conversation can see "
                "this Set's shots only — list_set_shots is how to find them."
            )


def _insight_refusal(refused: InsightProposeRefusal, subject: int | None, label: str) -> str:
    """The sentence a refused insight comes back as, saying what to do instead.

    ``label`` names the version a refusal is about. Nothing here says anything about
    another Set: an id that is not this Set's is refused as exactly that.
    """
    if refused == "nothing_to_rest_on":
        return (
            "An insight has to rest on something: name the shots of this Set it comes "
            "from (evidence_shot_ids), the versions whose recorded outcome it depends on "
            "(rests_on_versions), or both."
        )
    if refused == "no_outcome":
        return (
            f"{label} has no recorded outcome yet: name shots instead, or wait until the "
            "person records its grade."
        )
    if refused == "bad_version":
        return (
            f"{label} is not a version of this Set. Name this Set's own versions "
            "as the record names them (v1.2)."
        )
    if refused == "replaces_general":
        return (
            f"Insight #{subject} is general knowledge, not this Set's, and only the person "
            "edits or deletes those (on the Knowledge page). Leave replaces_insight_id out "
            "to propose a new insight of this Set on its own."
        )
    if refused == "replaces_not_added":
        return (
            f"Insight #{subject} is not an added insight: only one the person has added "
            "can be replaced (a waiting or dismissed one cannot). Leave "
            "replaces_insight_id out to propose a new one."
        )
    return (
        f"Insight #{subject} is not an insight of this Set. get_insights lists the added "
        "insights of this Set with their numbers; leave replaces_insight_id out to "
        "propose a new one on its own."
    )


@tool(
    "record_insight",
    permission="propose",
    description=(
        "Record something learned about THIS Set, for the person to add or dismiss. It is "
        "about this Set only: it is stored with this Set and the version this conversation "
        "is about, shown to the person as a card, and — only if they add it — given to this "
        "Set's later conversations and to no other Set's. It reaches no future prompt until "
        "they do. Propose one only when named shots of this Set or versions with a recorded "
        "outcome actually support it, and name them (evidence_shot_ids, rests_on_versions); "
        "offer few. To replace an added insight the shots now contradict, set "
        "replaces_insight_id to its number: adding the new one deletes the old one. There "
        "is no scope to set."
    ),
)
async def record_insight(ctx: ToolContext, args: RecordInsightInput) -> RecordInsightOutput:
    """An insight belongs to the Set it was learned in, and waits for the person.

    Written with the conversation's Set and version and **no attribute scope**:
    what is true of this coffee on this grinder is a statement about this Set,
    and one scoped by bean would reach every other Set that shares it. Stored
    unconfirmed; the person's Add is the only thing that makes it confirmed. What
    it rests on and what it replaces are read and written in one transaction by
    the repository, so this function only turns a refusal into a sentence.
    """
    if ctx.scope.designing:
        raise ValueError(DESIGN_RULE)
    set_id = _resolve_set(ctx, None)
    given = list(args.model_extra or {})
    if any(key in _RETIRED_SCOPE_FIELDS for key in given):
        raise ValueError(
            "An insight from this conversation belongs to this Set: it is stored with this "
            "Set and reaches this Set's later conversations only, so it has no bean, grinder, "
            "roast level, process or style scope to set. Leave those out and call this "
            "again."
        )
    if given:
        raise ValueError(
            f"Unknown argument{'s' if len(given) > 1 else ''}: {', '.join(sorted(given))}. "
            "Only text, evidence_shot_ids, rests_on_versions and replaces_insight_id."
        )
    await _check_evidence(ctx, set_id, args)
    sets = SetsRepository(ctx.db)
    # Named the way a version is named everywhere else ("v1.2"), through the helper
    # that refuses with the Set's own list of versions.
    version_ids = [await _resting_version(ctx, set_id, name) for name in args.rests_on_versions]
    labels = dict(zip(version_ids, args.rests_on_versions, strict=True))
    version = (
        await sets.version_of_set(set_id, ctx.scope.set_version_id)
        if ctx.scope.set_version_id is not None
        else await sets.current_version(set_id)
    )
    repo = InsightsRepository(ctx.db)
    result = await repo.propose(
        InsightWrite(
            text=args.text,
            evidence_shot_ids=args.evidence_shot_ids,
            source="chat",
            confirmed=False,
            set_id=set_id,
            set_version_id=None if version is None else version.id,
            thread_id=ctx.thread_id,
            replaces_id=args.replaces_insight_id,
        ),
        version_ids=version_ids,
    )
    if result.refused is not None:
        raise ValueError(
            _insight_refusal(
                result.refused, result.subject, labels.get(result.subject or 0, "That version")
            )
        )
    assert result.insight_id is not None  # not refused
    stored = await repo.get(result.insight_id)
    assert stored is not None  # just inserted
    replaces = (
        f" Adding it deletes insight #{stored.replaces_id} ({stored.replaces_text!r}); "
        "until the person adds it, that one stands."
        if stored.replaces_id is not None
        else ""
    )
    return RecordInsightOutput(
        insight_id=stored.id,
        scope=stored.scope_label,
        text=stored.text,
        confirmed=stored.confirmed,
        rests_on=[item.render() for item in stored.rests_on],
        replaces_insight_id=stored.replaces_id,
        note=(
            "Nothing is recorded as known yet: the person sees a card and adds or dismisses "
            "it. It is about this Set only, and if they add it, only this Set's later "
            "conversations are told it." + replaces
        ),
    )


class ProposeInsightDeletionInput(_Model):
    insight_id: int = Field(
        description=(
            "The number of an ADDED insight of this Set (get_insights lists them, and the "
            "opening context shows each with its number)."
        )
    )
    reason: str = Field(
        default="",
        description=(
            f"Why it no longer fits, in {REASON_MIN} to {REASON_MAX} characters: the person "
            "reads it on the card before pressing Delete or Keep."
        ),
    )


class ProposeInsightDeletionOutput(_Model):
    proposal_id: int
    set_id: int
    insight_id: int
    insight_text: str
    status: str = "proposed"
    #: Whether this replaced a deletion of the same insight that was still waiting.
    replaced_waiting_proposal: bool = False
    note: str = ""


def _deletion_refusal(refused: InsightDeletionRefusal, insight_id: int) -> str:
    if refused == "general":
        return (
            f"Insight #{insight_id} is general knowledge, not this Set's: only the person "
            "deletes those, on the Knowledge page. If it misleads this Set, say so in words."
        )
    if refused == "not_added":
        return (
            f"Insight #{insight_id} is not an added insight: only one the person has added "
            "can be proposed for deletion. A waiting one is theirs to add or dismiss, and a "
            "dismissed one is already out of every prompt."
        )
    if refused == "bad_thread":
        # Not something a model can cause by choosing arguments: the conversation is
        # the runner's to supply. Said plainly anyway.
        return (
            "This conversation is not one of this Set's, so a deletion argued here cannot "
            "be recorded against it. Nothing was written; tell the person."
        )
    return (
        f"Insight #{insight_id} is not an insight of this Set. get_insights lists this "
        "Set's added insights with their numbers."
    )


@tool(
    "propose_insight_deletion",
    permission="propose",
    description=(
        "Propose deleting one ADDED insight of THIS Set that the shots no longer support "
        "and nothing replaces (when something replaces it, propose that with record_insight "
        "and replaces_insight_id instead). It deletes nothing: the person sees a card with "
        "your reason and presses Delete or Keep, and until they press Delete the insight "
        "stays and is still told to every conversation of this Set. A second call for the "
        "same insight replaces the first while the person has not answered."
    ),
)
async def propose_insight_deletion(
    ctx: ToolContext, args: ProposeInsightDeletionInput
) -> ProposeInsightDeletionOutput:
    """The agent's reason for removing an insight, as a card. Never the removal.

    Every refusal is an error value with a sentence of its own. The conversation is
    the runner's, so a call with no thread (a bare stdio session) is refused: the
    card would have nowhere to be.
    """
    if ctx.scope.designing:
        raise ValueError(DESIGN_RULE)
    set_id = _resolve_set(ctx, None)
    reason = args.reason.strip()
    if len(reason) < REASON_MIN:
        raise ValueError(
            "A deletion needs its reason: the person reads it on the card before pressing "
            f"Delete or Keep, at least {REASON_MIN} characters. Say which shots contradict "
            "the insight or why it no longer applies, then call this again."
        )
    if len(reason) > REASON_MAX:
        raise ValueError(
            f"The reason is too long: at most {REASON_MAX} characters, so it fits on the card."
        )
    if ctx.thread_id is None:
        raise ValueError(
            "A deletion is proposed inside a conversation, where the person sees the card "
            "and answers it. This session has no conversation, so nothing was written."
        )
    result = await InsightDeletionsRepository(ctx.db).propose(
        set_id,
        InsightDeletionWrite(thread_id=ctx.thread_id, insight_id=args.insight_id, reason=reason),
    )
    if result.refused is not None or result.proposal is None:
        raise ValueError(_deletion_refusal(result.refused or "bad_insight", args.insight_id))
    stored = result.proposal
    return ProposeInsightDeletionOutput(
        proposal_id=stored.id,
        set_id=set_id,
        insight_id=args.insight_id,
        insight_text=stored.insight_text,
        status=stored.status,
        replaced_waiting_proposal=result.replaced is not None,
        note=(
            f"Nothing is deleted: insight #{args.insight_id} stays, and every conversation of "
            "this Set is still told it, until the person presses Delete on the card. They can "
            "also Keep it. Say in one sentence why you think it no longer fits."
        ),
    )

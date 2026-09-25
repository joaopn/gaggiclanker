"""Everything the model is told, assembled deterministically.

One function — :func:`build_context` — reads the archive and produces a document
that is both the prompt's variables and the analysis row's `input_json`. That it
is one function matters: the snapshot stored on the row has to be *exactly* what
was sent, so there is no second assembly path where the two could differ.

Five sections, in the order the prompt carries them:

1. **the shot** — header, exit reason, per-phase metrics, the diagnostics with
   their band labels, the execution score with its components, and a 40-point
   downsampled curve so the model can see the *shape* without forty kilobytes
   of samples;
2. **the Set** — the bean with its roast level and process, the grinder with
   its own step unit, the machine with its hardware and offsets, the profile
   JSON, and the grind/dose/yield/temperature targets. Or an explicit "no Set"
   block, which is a statement rather than an omission: a model given no Set
   section would assume one was forgotten;
3. **the trajectory** — the previous N shots in the same Set, each with its
   diagnostics summary, the user's verdict, and the suggestions that followed it
   and what became of them. crema's interleaving trick, Set-scoped, and it is
   what stops the model repeating advice that has already been tried and failed;
4. **your judgement** — flagged as ground truth for taste;
5. **the rules** — the selected knowledge tier, verbatim.

Determinism is the property everything here is arranged around. Nothing here
reads the clock at all; nothing iterates a set; every list is sorted. Two builds
of the same shot produce byte-identical text, which is what the golden test
asserts and what makes "the same shot selects the same rules" checkable at all.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.analyzer.style import StyleVerdict, detect_style
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.analyses import AnalysesRepository, SuggestionsRepository
from gaggiclanker.db.repos.beans import BeansRepository, taste_scales
from gaggiclanker.db.repos.grinders import GrindersRepository
from gaggiclanker.db.repos.judgements import JudgementsRepository
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.knowledge_insights import set_attributes as insight_set_attributes
from gaggiclanker.db.repos.machines import MachineRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotDetailRow, ShotsRepository
from gaggiclanker.domain.models import PHASE_EXIT_REASONS
from gaggiclanker.domain.vocab import flavor_ancestors, flavor_path
from gaggiclanker.knowledge.rules import SetContext, render_rules, select_rules
from gaggiclanker.knowledge.service import (
    DEFAULT_CHUNK_TOKEN_BUDGET,
    KnowledgeService,
    RetrievalContext,
    render_excerpts,
    render_insights,
)
from gaggiclanker.sync.engine import downsample

__all__ = [
    "CURVE_POINTS",
    "TRAJECTORY_SHOTS",
    "AnalysisContext",
    "build_context",
    "retrieval_context",
    "set_attributes",
    "signal_tokens",
]

#: How many points of the curve the model sees. Forty is enough to read a ramp,
#: a plateau and a decline; the full 213 samples of a 30 s shot are 40 kB of
#: prompt for detail no language model uses.
CURVE_POINTS = 40

#: How many earlier shots of the Set come along. crema's review prompt uses
#: five and says so; five is also about where the prompt
#: stops growing faster than it gets more useful.
TRAJECTORY_SHOTS = 5

#: The two sides of the cup on the flavour wheel, for the one rule that needs
#: both at once: a note at or under one of these nodes puts the cup on that
#: side. The balance says the same thing in one word and counts too. Salty is
#: not on the sour side here: it is under-extraction, but it has its own rule,
#: and "salty and bitter" is not the channeling pattern Rao describes.
_SOUR_NODES = ("sour_fermented.sour",)
_BITTER_NODES = ("other.chemical.bitter", "roasted.burnt")


def _under(note: str, nodes: tuple[str, ...]) -> bool:
    return any(note == node or note.startswith(f"{node}.") for node in nodes)


def _with_ancestors(notes: list[str]) -> list[str]:
    """Each note and every node inside it on the wheel, first seen first."""
    return list(dict.fromkeys(value for note in notes for value in (*flavor_ancestors(note), note)))


class ShotFacts(BaseModel):
    """What the machine did, as far as the archive decoded it."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    device_id: str
    started_at: str | None = None
    duration_s: float | None = None
    profile_name: str = ""
    final_weight_g: float | None = None
    exit_reason: str = "Unknown"
    scale_connected: bool = False
    has_pressure: bool = True
    brew_delay_s: float | None = None
    sample_count: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    phases: list[dict[str, Any]] = Field(default_factory=list)
    execution_score: float | None = None
    execution_reason: str = ""
    execution_confidence: str = ""
    execution_components: dict[str, float] = Field(default_factory=dict)
    #: `[{t, cp, pf, v, ct}]`, at most :data:`CURVE_POINTS` points.
    curve: list[dict[str, float | None]] = Field(default_factory=list)


class SetFacts(BaseModel):
    """What the person was trying: the bean, the kit and the recipe."""

    model_config = ConfigDict(extra="forbid")

    set_id: int
    set_name: str
    version_id: int
    version_no: int
    intent: str = ""
    #: The catalogue ids, carried so an insight's scope can be matched against
    #: them. Not rendered into the prompt — a model has no use for a row id —
    #: but part of the stored snapshot, because `{"grinder_id": 2}` on an
    #: insight is only checkable later if the number it was checked against is
    #: on the row too.
    bean_id: int | None = None
    grinder_id: int | None = None
    bean_name: str = ""
    roaster: str = ""
    origin: str = ""
    process: str | None = None
    roast_level: str | None = None
    decaf: bool = False
    acidity: int | None = None
    intensity: int | None = None
    sweetness: int | None = None
    description: str = ""
    grinder_name: str = ""
    grinder_model: str = ""
    burr_type: str = "unknown"
    #: What one step of this grinder is called. The analyzer is handed this
    #: precisely so it stops inventing a scale (migration 0005's own comment).
    step_unit: str = "clicks"
    machine_hardware: str = ""
    temperature_offset_c: float | None = None
    brew_delay_ms: int | None = None
    grind_setting: str | None = None
    grind_value: float | None = None
    dose_g: float | None = None
    target_yield_g: float | None = None
    #: The brew temperature the profile states, not a number typed on the Set:
    #: a Set version records none, because the machine heats to what the
    #: document says. Named for where it comes from, so the model reads it as a
    #: property of the profile below rather than as something to change here.
    profile_temperature_c: float | None = None
    profile_label: str = ""
    profile: dict[str, Any] | None = None


class TrajectoryEntry(BaseModel):
    """One earlier shot of the Set, and what was said about it."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    device_id: str
    started_at: str | None = None
    version_no: int = 0
    duration_s: float | None = None
    final_weight_g: float | None = None
    execution_score: float | None = None
    channeling_risk: str | None = None
    resistance_avg: float | None = None
    rating: int | None = None
    balance: str | None = None
    taste_notes: list[str] = Field(default_factory=list)
    aroma_notes: list[str] = Field(default_factory=list)
    notes: str = ""
    #: The suggestions made *after* this shot and what became of them. The point
    #: of the whole section: advice that was accepted and did not help is the
    #: thing the next analysis most needs to know.
    suggestions: list[str] = Field(default_factory=list)


class JudgementFacts(BaseModel):
    """The user's verdict. Ground truth for taste, and labelled as such."""

    model_config = ConfigDict(extra="forbid")

    rating: int | None = None
    balance: str | None = None
    #: Flavour-wheel slugs, as recorded; rendered with their path.
    taste_notes: list[str] = Field(default_factory=list)
    aroma_notes: list[str] = Field(default_factory=list)
    dose_in_g: float | None = None
    dose_out_g: float | None = None
    ratio: float | None = None
    grind_setting: str | None = None
    notes: str = ""
    decision: str | None = None


class PreviousAnalysis(BaseModel):
    """The last analysis of this shot, when this run is a re-run."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: int
    created_at: str
    model: str = ""
    diagnosis: str = ""
    suggestions: list[str] = Field(default_factory=list)


class AnalysisContext(BaseModel):
    """Everything the model is told, as one validated document.

    Stored verbatim as the analysis row's `input_json`, so an analysis stays
    explainable after the prompt, the rules and the Set version have all moved
    on.
    """

    model_config = ConfigDict(extra="forbid")

    shot: ShotFacts
    set: SetFacts | None = None
    #: Why there is no Set. Filled in only when `set` is None, because "this
    #: shot is not in a Set" and "we forgot the Set" have to read differently.
    no_set_reason: str = ""
    style: str = "unknown"
    style_tier: str = "none"
    style_evidence: list[str] = Field(default_factory=list)
    trajectory: list[TrajectoryEntry] = Field(default_factory=list)
    judgement: JudgementFacts | None = None
    #: The signal tokens rule selection was made against.
    signals: list[str] = Field(default_factory=list)
    #: `[{key, category, text, confidence, source}]` for every selected rule.
    rules: list[dict[str, str]] = Field(default_factory=list)
    #: `[{heading_path, doc_slug, doc_title, heading, body, tokens_estimate,
    #: query}]` for every retrieved chunk (tier 2). Supporting context, cited by
    #: heading path, and stored on the snapshot in full — a citation into a
    #: document the user has since edited has to still resolve to the text the
    #: model actually read.
    excerpts: list[dict[str, Any]] = Field(default_factory=list)
    #: `[{id, scope, text, evidence_shot_ids}]` for every confirmed insight that
    #: applies to this Set (tier 3). Unconfirmed ones are never here.
    insights: list[dict[str, Any]] = Field(default_factory=list)
    previous_analysis: PreviousAnalysis | None = None

    @property
    def rule_keys(self) -> frozenset[str]:
        """What `rules_used` is validated against.

        ``frozenset`` rather than ``set`` because this class has a field called
        ``set`` — the Set the shot belongs to — and inside a class body that
        name shadows the builtin, so ``set[str]`` here is not a type at all.
        """
        return frozenset(rule["key"] for rule in self.rules)

    @property
    def excerpt_paths(self) -> frozenset[str]:
        """What `excerpts_used` is validated against. See `rule_keys` for the type."""
        return frozenset(str(excerpt["heading_path"]) for excerpt in self.excerpts)

    def render(self) -> dict[str, str]:
        """The prompt's variables: one rendered block per section.

        Text rather than JSON because these sections are read by a language
        model and prose with numbers in it costs a third of the tokens that the
        same facts cost as nested JSON — except the profile and the curve, which
        *are* structured data and are rendered as compact JSON inside their
        block.
        """
        return {
            "shot_facts": _render_shot(self.shot),
            "shot_style": _render_style(self),
            "set_context": (
                _render_set(self.set)
                if self.set is not None
                else f"NO SET. {self.no_set_reason}".strip()
            ),
            "trajectory": _render_trajectory(self.trajectory),
            "judgement": _render_judgement(self.judgement),
            "knowledge_rules": render_rules(self.rules),
            "knowledge_excerpts": render_excerpts(self.excerpts),
            "learned_insights": render_insights(self.insights),
            "previous_analysis": _render_previous(self.previous_analysis),
        }


# ── building ──────────────────────────────────────────────────────────


async def build_context(
    db: Database,
    shot_id: int,
    *,
    trajectory: int = TRAJECTORY_SHOTS,
    rerun_of: int | None = None,
    chunk_token_budget: int = DEFAULT_CHUNK_TOKEN_BUDGET,
) -> AnalysisContext:
    """Assemble everything the model is told about one shot.

    ``chunk_token_budget`` is how much tier-2 prose may come along, in estimated
    tokens; it is a *parameter* rather than a settings read so that this function
    stays pure with respect to the database it was handed — the analyzer service
    resolves `analysisChunkTokenBudget` and passes it, and the golden test pins
    the default. Zero turns retrieval off.

    ``rerun_of`` names an earlier analysis of the same shot whose diagnosis and
    suggestions are carried into the context. A re-run with no reference to what
    was said last time reliably says it again, in slightly different words,
    which reads to a user as the analyzer not listening.

    Raises ``LookupError`` for a shot that does not exist. Everything else is
    optional: a shot with no Set, no judgement, no samples and no diagnostics is
    a real shot in this archive, and each of those is a stated absence rather
    than a missing section.
    """
    shots = ShotsRepository(db)
    shot = await shots.get(shot_id)
    if shot is None:
        raise LookupError(f"no shot {shot_id}")

    blob = shot.diagnostics or {}
    summary = dict(blob.get("summary") or {})
    diagnostics = dict(blob.get("diagnostics") or {})
    score = dict(blob.get("score") or {})

    samples = await shots.samples(shot_id)
    facts = ShotFacts(
        shot_id=shot.id,
        device_id=shot.device_id,
        started_at=shot.started_at,
        duration_s=None if shot.duration_ms is None else round(shot.duration_ms / 1000, 2),
        profile_name=shot.profile_label or shot.profile_name_on_device,
        final_weight_g=shot.volume_g,
        exit_reason=PHASE_EXIT_REASONS.get(shot.final_exit_reason or 0, "Unknown"),
        scale_connected=shot.scale_connected,
        has_pressure=bool(blob.get("has_pressure", True)),
        brew_delay_s=None if shot.brew_delay_ms is None else round(shot.brew_delay_ms / 1000, 2),
        sample_count=shot.sample_count,
        summary=summary,
        diagnostics=diagnostics,
        phases=_phase_metrics(shot.phases or []),
        execution_score=shot.execution_score,
        execution_reason=str(score.get("reason") or shot.execution_reason or ""),
        execution_confidence=str(score.get("confidence") or ""),
        execution_components={
            str(key): float(value) for key, value in sorted((score.get("components") or {}).items())
        },
        curve=_curve(samples),
    )

    set_facts, no_set_reason = await _set_facts(db, shot)
    verdict = detect_style(
        set_facts.profile if set_facts else None,
        dose_g=set_facts.dose_g if set_facts else None,
        summary=summary,
        duration_s=facts.duration_s,
        # The header's own name. Every shot has one, including the ones whose
        # profile the mirror never captured — which are exactly the shots the
        # telemetry fallback reads worst.
        profile_name=facts.profile_name,
    )

    judgement_row = await JudgementsRepository(db).get(shot_id)
    judgement = (
        None
        if judgement_row is None
        else JudgementFacts(
            rating=judgement_row.rating,
            balance=judgement_row.balance,
            taste_notes=list(judgement_row.taste_notes),
            aroma_notes=list(judgement_row.aroma_notes),
            dose_in_g=judgement_row.dose_in_g,
            dose_out_g=judgement_row.dose_out_g,
            ratio=judgement_row.ratio,
            grind_setting=judgement_row.grind_setting,
            notes=judgement_row.notes,
            decision=judgement_row.decision,
        )
    )

    signals = signal_tokens(facts, judgement, verdict)
    selection = await select_rules(
        RulesRepository(db),
        SetContext(
            roast_level=set_facts.roast_level if set_facts else None,
            process=set_facts.process if set_facts else None,
            burr_type=set_facts.burr_type if set_facts else None,
            decaf=bool(set_facts.decaf) if set_facts else False,
        ),
        verdict.style,
        signals,
    )

    knowledge = KnowledgeService(db)
    excerpts = await knowledge.select_chunks(
        retrieval_context(selection.signals, verdict.style, judgement, set_facts),
        token_budget=chunk_token_budget,
    )
    insights = await knowledge.select_insights(set_attributes(set_facts, verdict.style))

    return AnalysisContext(
        shot=facts,
        set=set_facts,
        no_set_reason=no_set_reason,
        style=verdict.style,
        style_tier=verdict.tier,
        style_evidence=list(verdict.evidence),
        trajectory=await _trajectory(db, shot, limit=trajectory),
        judgement=judgement,
        signals=selection.signals,
        rules=[
            {
                "key": rule.key,
                "category": rule.category,
                "text": rule.text,
                "confidence": rule.confidence,
                "source": rule.source,
            }
            for rule in selection.rules
        ],
        excerpts=[excerpt.as_dict() for excerpt in excerpts],
        insights=[
            {
                "id": insight.id,
                "scope": insight.scope.label(),
                "text": insight.text,
                "evidence_shot_ids": list(insight.evidence_shot_ids or []),
            }
            for insight in insights
        ],
        previous_analysis=await _previous(db, rerun_of),
    )


def set_attributes(facts: SetFacts | None, style: str) -> dict[str, Any]:
    """This shot's Set, in the flat shape an insight's scope is matched against.

    A thin call onto
    :func:`gaggiclanker.db.repos.knowledge_insights.set_attributes`, which owns
    the key list and the "empty means not stated" normalisation — this only
    knows where the values live on a context.

    The style is the *detected* one rather than a stored attribute, because that
    is what "this profile style" means to somebody confirming an insight: the
    kind of shot the machine actually pulled. It is also why a Set on its own
    cannot answer for it, and why the Set page's list is narrower than a shot's.
    """
    if facts is None:
        return insight_set_attributes(profile_style=style)
    return insight_set_attributes(
        bean_id=facts.bean_id,
        roast_level=facts.roast_level,
        process=facts.process,
        origin=facts.origin,
        grinder_id=facts.grinder_id,
        profile_style=style,
    )


def retrieval_context(
    signals: list[str],
    style: str,
    judgement: JudgementFacts | None,
    facts: SetFacts | None,
) -> RetrievalContext:
    """The analysis, in the shape tier-2 retrieval reads it.

    A translation and nothing else, but it is a named one: the chat
    builds the same record from a conversation, and having one place where "what
    an analysis knows" becomes "what to search for" is what keeps the two
    callers retrieving comparably.
    """
    return RetrievalContext(
        style=style,
        signals=tuple(signals),
        taste_notes=tuple(judgement.taste_notes) if judgement else (),
        balance=judgement.balance if judgement else None,
        roast_level=facts.roast_level if facts else None,
        process=facts.process if facts else None,
    )


def signal_tokens(
    shot: ShotFacts,
    judgement: JudgementFacts | None,
    style: StyleVerdict,
) -> list[str]:
    """The tokens rule selection matches `applies.signal` against.

    The grammar is documented in :mod:`gaggiclanker.knowledge.rules`. Sorted,
    because the list is stored on the analysis row and a set's iteration order
    would make two identical runs produce different snapshots.
    """
    tokens: set[str] = {f"style:{style.style}"}

    for metric, label in (shot.diagnostics.get("annotations") or {}).items():
        tokens.add(f"{metric}:{label}")
    # The full diagnostics block nests its annotations one level deeper, per
    # sub-diagnostic. Both shapes are read, because which one a shot carries
    # depends on the detail level it was derived at.
    for section in ("resistance", "channeling", "temperature", "extraction", "profile_compliance"):
        block = shot.diagnostics.get(section)
        if isinstance(block, dict):
            for metric, label in (block.get("annotations") or {}).items():
                if isinstance(label, str):
                    tokens.add(f"{metric}:{label}")
            primary = (block.get("annotations") or {}).get("primary_signal")
            if isinstance(primary, str) and primary != "none":
                tokens.update(f"primary:{name}" for name in primary.split(",") if name)

    if not shot.scale_connected:
        tokens.add("scale:absent")

    flow = shot.summary.get("flow") or {}
    first_drip = flow.get("time_to_first_drip_s")
    if isinstance(first_drip, int | float):
        if first_drip < 3:
            tokens.add("first_drip:fast")
        elif first_drip > 10:
            tokens.add("first_drip:slow")
    avg_flow = flow.get("avg_flow_ml_s")
    if isinstance(avg_flow, int | float) and avg_flow > 0:
        if avg_flow > 3:
            tokens.add("avg_flow:high")
        elif avg_flow < 1:
            tokens.add("avg_flow:low")

    temperature = shot.summary.get("temperature") or {}
    target = temperature.get("target_avg_c")
    actual = temperature.get("avg_c")
    if isinstance(target, int | float) and isinstance(actual, int | float) and target > 0:
        if actual - target < -3:
            tokens.add("temp:cold")
        elif actual - target > 2:
            tokens.add("temp:hot")

    if shot.final_weight_g is not None and shot.final_weight_g < 1:
        # Not a failed shot: the firmware records a fraction of a gram when the
        # cup came off the scale early, and reading it as a yield is how an
        # analysis concludes the shot did not run.
        tokens.add("yield:tiny")

    if judgement is not None:
        # A note fires every node inside it on the wheel as well, so a rule
        # keyed on `taste:sour_fermented.sour` hears "acetic acid" and a person
        # who was only sure enough to say "sour" still reaches the same rule.
        tokens.update(f"taste:{note}" for note in _with_ancestors(judgement.taste_notes))
        tokens.update(f"aroma:{note}" for note in _with_ancestors(judgement.aroma_notes))
        if judgement.balance:
            tokens.add(f"balance:{judgement.balance}")
        # Sour AND bitter in the same cup is channeling, not an extraction
        # level — a different diagnosis with different advice (Rao: do *not*
        # grind finer). It gets its own token because a rule that matched
        # "sour or bitter" would fire on every ordinary sour cup and contradict
        # the sour rule sitting beside it. Either side can come from the
        # balance or from a taste note; the balance is one word, so both sides
        # at once needs at least one note.
        sour = judgement.balance == "sour" or any(
            _under(note, _SOUR_NODES) for note in judgement.taste_notes
        )
        bitter = judgement.balance == "bitter" or any(
            _under(note, _BITTER_NODES) for note in judgement.taste_notes
        )
        if sour and bitter:
            tokens.add("taste:sour_and_bitter")

    return sorted(tokens)


async def _set_facts(db: Database, shot: ShotDetailRow) -> tuple[SetFacts | None, str]:
    """The Set half of the context, or the reason there is not one."""
    if shot.set_version_id is None:
        return None, (
            "This shot is not attached to a Set, so the bean, the grinder and the "
            "intended recipe are all unknown. Do not assume any of them."
        )
    sets = SetsRepository(db)
    version = await sets.get_version(shot.set_version_id)
    if version is None:
        return None, (
            "This shot names a Set version that no longer exists, so the recipe "
            "behind it cannot be read."
        )
    row = await sets.get(version.set_id)
    if row is None:  # pragma: no cover - a version cannot outlive its Set
        return None, "This shot's Set has been removed."

    bean = await BeansRepository(db).get(row.bean_id)
    grinder = None if row.grinder_id is None else await GrindersRepository(db).get(row.grinder_id)
    machine = await MachineRepository(db).get()
    profile = None
    if version.profile_version_id is not None:
        stored = await ProfilesRepository(db).get_version(version.profile_version_id)
        profile = None if stored is None else stored.profile

    return (
        SetFacts(
            set_id=row.id,
            set_name=row.name,
            version_id=version.id,
            version_no=version.version_no,
            intent=version.intent,
            bean_id=row.bean_id,
            grinder_id=row.grinder_id,
            bean_name=bean.name if bean else "",
            roaster=(bean.roaster or "") if bean else "",
            origin=(bean.origin or "") if bean else "",
            process=bean.process if bean else None,
            roast_level=bean.roast_level if bean else None,
            decaf=bool(bean.decaf) if bean else False,
            acidity=bean.acidity if bean else None,
            intensity=bean.intensity if bean else None,
            sweetness=bean.sweetness if bean else None,
            description=(bean.description or "") if bean else "",
            grinder_name=grinder.name if grinder else "",
            grinder_model=(grinder.model or "") if grinder else "",
            burr_type=grinder.burr_type if grinder else "unknown",
            step_unit=grinder.step_unit if grinder else "clicks",
            machine_hardware=(machine.hardware_string or "") if machine else "",
            temperature_offset_c=machine.temperature_offset_c if machine else None,
            brew_delay_ms=machine.brew_delay_ms if machine else None,
            grind_setting=version.grind_setting,
            grind_value=version.grind_value,
            dose_g=version.dose_g,
            target_yield_g=version.target_yield_g,
            profile_temperature_c=version.profile_temperature_c,
            profile_label=version.profile_label or "",
            profile=profile,
        ),
        "",
    )


def _phase_metrics(phases: list[Any]) -> list[dict[str, Any]]:
    """The per-phase block, without the per-phase samples.

    `per_phase_detailed` carries five averaged samples per phase; the curve
    below already gives the model the shape, and the duplicate would be pure
    prompt weight.
    """
    out: list[dict[str, Any]] = []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        trimmed = {key: value for key, value in phase.items() if key != "samples"}
        out.append(trimmed)
    return out


def _curve(samples: list[Any]) -> list[dict[str, float | None]]:
    """At most :data:`CURVE_POINTS` points of t / pressure / flow / weight / temp.

    Evenly spaced rather than averaged, for the reason `downsample` gives: the
    model is reading a shape, and an averaged curve smooths away exactly the
    spike it would be reading it for.
    """
    return [
        {
            "t": round(sample.t_ms / 1000, 2),
            "cp": sample.cp,
            "pf": sample.pf,
            "v": sample.v,
            "ct": sample.ct,
        }
        for sample in downsample(samples, CURVE_POINTS)
    ]


async def _trajectory(db: Database, shot: ShotDetailRow, *, limit: int) -> list[TrajectoryEntry]:
    """The previous ``limit`` shots of the same Set, oldest first.

    Oldest first because it is a trajectory: "we went finer, then finer again,
    and it is still sour" only reads that way in order. Shots *after* this one
    are excluded even when they exist — re-analysing an old shot must not be
    told the future, or its advice would be unaccountable.
    """
    if shot.set_version_id is None or limit <= 0:
        return []
    version = await SetsRepository(db).get_version(shot.set_version_id)
    if version is None:
        return []

    rows = await db.fetch_all(
        """
        SELECT sh.id, sh.device_id, sh.started_at, sh.duration_ms, sh.execution_score,
               COALESCE(sh.final_weight_g, sh.index_volume_g) AS volume_g,
               sh.diagnostics_json, v.version_no
          FROM shots sh
          JOIN set_versions v ON v.id = sh.set_version_id
         WHERE v.set_id = ?
           AND sh.id != ?
           AND sh.quarantined = 0
           AND (COALESCE(sh.started_at, ''), sh.id) < (COALESCE(?, ''), ?)
         ORDER BY COALESCE(sh.started_at, '') DESC, sh.id DESC
         LIMIT ?
        """,
        (version.set_id, shot.id, shot.started_at, shot.id, limit),
    )
    ordered = list(reversed(rows))
    shot_ids = [int(row["id"]) for row in ordered]
    verdicts = await JudgementsRepository(db).for_shots(shot_ids)
    advice = await SuggestionsRepository(db).for_shots(shot_ids)

    entries: list[TrajectoryEntry] = []
    for row in ordered:
        shot_id = int(row["id"])
        blob: dict[str, Any] = {}
        raw = row["diagnostics_json"]
        if isinstance(raw, str):
            try:
                blob = json.loads(raw)
            except ValueError:  # pragma: no cover - the column is validated on write
                blob = {}
        diagnostics = blob.get("diagnostics") or {}
        verdict = verdicts.get(shot_id)
        entries.append(
            TrajectoryEntry(
                shot_id=shot_id,
                device_id=str(row["device_id"]),
                started_at=row["started_at"],
                version_no=int(row["version_no"]),
                duration_s=(
                    None if row["duration_ms"] is None else round(row["duration_ms"] / 1000, 2)
                ),
                final_weight_g=row["volume_g"],
                execution_score=row["execution_score"],
                channeling_risk=diagnostics.get("channeling_risk"),
                resistance_avg=diagnostics.get("resistance_avg"),
                rating=verdict.rating if verdict else None,
                balance=verdict.balance if verdict else None,
                taste_notes=list(verdict.taste_notes) if verdict else [],
                aroma_notes=list(verdict.aroma_notes) if verdict else [],
                notes=verdict.notes if verdict else "",
                suggestions=[
                    f"{item.variable} {item.direction}"
                    f"{'' if item.magnitude is None else f' {item.magnitude:g} {item.unit}'}"
                    f" -> {item.status}"
                    for item in advice.get(shot_id, [])
                ],
            )
        )
    return entries


async def _previous(db: Database, analysis_id: int | None) -> PreviousAnalysis | None:
    if analysis_id is None:
        return None
    row = await AnalysesRepository(db).get(analysis_id)
    if row is None or row.status != "ok":
        return None
    output = row.output or {}
    return PreviousAnalysis(
        analysis_id=row.id,
        created_at=row.created_at,
        model=row.model,
        diagnosis=str(output.get("diagnosis") or ""),
        suggestions=[
            f"{item.variable} {item.direction}"
            f"{'' if item.magnitude is None else f' {item.magnitude:g} {item.unit}'}"
            f" -> {item.status}"
            for item in row.suggestions
        ],
    )


# ── rendering ─────────────────────────────────────────────────────────
#
# Plain text with numbers in it, not JSON. The same facts cost about a third as
# many tokens this way, and — the part that matters more — a model reads
# "channeling_risk: HIGH (4 indicators)" as a finding and `{"channeling_risk":
# "HIGH"}` as a field it has been asked to copy.


def _line(label: str, value: Any, unit: str = "") -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, float):
        return f"{label}: {value:g}{unit}"
    return f"{label}: {value}{unit}"


def _block(lines: list[str | None]) -> str:
    return "\n".join(line for line in lines if line)


def _render_shot(shot: ShotFacts) -> str:
    head = _block(
        [
            _line("shot", f"{shot.device_id} (#{shot.shot_id})"),
            _line("started", shot.started_at),
            _line("profile as brewed", shot.profile_name),
            _line("duration", shot.duration_s, " s"),
            _line("yield", shot.final_weight_g, " g"),
            _line("exit reason", shot.exit_reason),
            _line("scale", "connected" if shot.scale_connected else "NOT connected"),
            _line("brew delay", shot.brew_delay_s, " s"),
            None
            if shot.has_pressure
            else "pressure sensor: ABSENT — every pressure-derived diagnostic "
            "below is missing rather than zero",
        ]
    )

    summary = shot.summary
    measured = _block(
        [
            _line("temperature avg", (summary.get("temperature") or {}).get("avg_c"), " °C"),
            _line(
                "temperature target", (summary.get("temperature") or {}).get("target_avg_c"), " °C"
            ),
            _line("pressure peak", (summary.get("pressure") or {}).get("max_bar"), " bar"),
            _line("pressure avg", (summary.get("pressure") or {}).get("avg_bar"), " bar"),
            _line("flow avg", (summary.get("flow") or {}).get("avg_flow_ml_s"), " ml/s"),
            _line("flow peak", (summary.get("flow") or {}).get("peak_flow_ml_s"), " ml/s"),
            _line("first drip", (summary.get("flow") or {}).get("time_to_first_drip_s"), " s"),
            _line("total volume", (summary.get("flow") or {}).get("total_volume_ml"), " ml"),
            _line(
                "pre-infusion", (summary.get("extraction") or {}).get("preinfusion_time_s"), " s"
            ),
            _line(
                "main extraction",
                (summary.get("extraction") or {}).get("main_extraction_time_s"),
                " s",
            ),
        ]
    )

    diagnostics = _render_diagnostics(shot.diagnostics)

    phases = "\n".join(
        f"  {index}. {phase.get('name', '?')} — "
        f"{_num(phase.get('duration_seconds'))} s, "
        f"{_num(phase.get('avg_pressure_bar'))} bar, "
        f"{_num(phase.get('avg_temperature_c'))} °C, "
        f"{_num(phase.get('total_flow_ml'))} ml" + _phase_tail(phase)
        for index, phase in enumerate(shot.phases)
    )

    components = (
        "\n".join(f"  {key}: {value:g}" for key, value in shot.execution_components.items())
        or "  (no penalties)"
    )
    score = _block(
        [
            _line("execution score", shot.execution_score, " / 10"),
            _line("confidence", shot.execution_confidence),
            _line("reason", shot.execution_reason),
        ]
    )

    curve = json.dumps(
        [[point["t"], point["cp"], point["pf"], point["v"], point["ct"]] for point in shot.curve],
        separators=(",", ":"),
    )

    return (
        f"{head}\n\nMEASURED\n{measured or '  (no summary was derived)'}"
        f"\n\nDIAGNOSTICS\n{diagnostics}"
        f"\n\nPHASES\n{phases or '  (none)'}"
        f"\n\nEXECUTION SCORE (deterministic — this is authoritative, not your opinion)\n"
        f"{score}\n{components}"
        f"\n\nCURVE ({len(shot.curve)} points of [t s, pressure bar, puck flow ml/s, "
        f"weight g, temp °C])\n{curve}"
    )


def _phase_tail(phase: dict[str, Any]) -> str:
    diagnostics = phase.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return ""
    bits = [
        f"type={diagnostics['phase_type']}" if "phase_type" in diagnostics else "",
        f"channeling={diagnostics['channeling_risk']}" if "channeling_risk" in diagnostics else "",
        f"R={_num(diagnostics['resistance_avg'])}" if "resistance_avg" in diagnostics else "",
        f"ramp={_num(diagnostics['ramp_rate_bar_s'])} bar/s"
        if "ramp_rate_bar_s" in diagnostics
        else "",
        f"taper={_num(diagnostics['taper_rate_bar_s'])} bar/s"
        if "taper_rate_bar_s" in diagnostics
        else "",
    ]
    present = [bit for bit in bits if bit]
    return f" [{', '.join(present)}]" if present else ""


def _render_diagnostics(diagnostics: dict[str, Any]) -> str:
    """The band labels and the numbers behind them, flattened.

    Both detail levels are handled: the summary block carries its annotations at
    the top, the full block nests them per sub-diagnostic. Which one a shot has
    depends on when it was derived, and a context that only understood one would
    silently drop half the archive's diagnostics.
    """
    if not diagnostics:
        return "  (no diagnostics were derived for this shot)"

    lines: list[str] = []
    bands = diagnostics.get("annotations") or {}
    flat = {
        key: value
        for key, value in diagnostics.items()
        # A key that is also a band is skipped here and printed under `bands`
        # with its label: printing `channeling_risk: LOW` twice reads as two
        # findings. `has_pressure` is already stated in the header.
        if isinstance(value, int | float | str) and key != "has_pressure" and key not in bands
    }
    for key, value in sorted(flat.items()):
        lines.append(f"  {key}: {_render_value(value)}")

    if bands:
        lines.append("  bands: " + ", ".join(f"{k}={v}" for k, v in sorted(bands.items())))

    for section in sorted(diagnostics):
        block = diagnostics[section]
        if not isinstance(block, dict):
            continue
        numbers = ", ".join(
            f"{key}={_num(value)}"
            for key, value in sorted(block.items())
            if isinstance(value, int | float) and not isinstance(value, bool)
        )
        bands = ", ".join(
            f"{key}={value}" for key, value in sorted((block.get("annotations") or {}).items())
        )
        if numbers or bands:
            lines.append(f"  {section}: {numbers}{' | ' if numbers and bands else ''}{bands}")
    return "\n".join(lines)


def _render_style(context: AnalysisContext) -> str:
    evidence = "; ".join(context.style_evidence) or "no evidence"
    return f"{context.style} (detected from {context.style_tier}: {evidence})"


def _render_set(facts: SetFacts) -> str:
    # Only what the person filled in: a line saying "not stated" reads to a
    # model as a fact about the coffee, and an absent line says the same thing
    # without inviting it to reason from the placeholder.
    bean = _block(
        [
            _line("bean", facts.bean_name),
            _line("roaster", facts.roaster),
            _line("origin", facts.origin),
            _line("process", facts.process),
            _line("roast level", facts.roast_level),
            _line("decaf", "yes" if facts.decaf else None),
            _line("taste", taste_scales(facts)),
            _line("description", facts.description),
        ]
    )
    kit = _block(
        [
            _line("grinder", f"{facts.grinder_name} {facts.grinder_model}".strip()),
            _line("burrs", facts.burr_type),
            _line(
                "grind unit",
                f"{facts.step_unit} — give every grind change in these, never in microns",
            ),
            _line("machine", facts.machine_hardware),
            _line("temperature offset", facts.temperature_offset_c, " °C"),
            _line("brew delay", facts.brew_delay_ms, " ms"),
        ]
    )
    recipe = _block(
        [
            _line("grind setting", facts.grind_setting),
            _line("grind value", facts.grind_value),
            _line("dose", facts.dose_g, " g"),
            _line("target yield", facts.target_yield_g, " g"),
            _line("profile temperature", facts.profile_temperature_c, " °C"),
            _line(
                "ratio",
                None
                if not (facts.dose_g and facts.target_yield_g)
                else f"1:{facts.target_yield_g / facts.dose_g:.2f}",
            ),
        ]
    )
    profile = (
        json.dumps(facts.profile, separators=(",", ":"), sort_keys=True)
        if facts.profile
        else "(the Set names no profile)"
    )
    return (
        f"Set: {facts.set_name} — version {facts.version_no}\n"
        f"version intent: {facts.intent or '(none stated)'}\n\n"
        f"BEAN\n{bean}\n\nKIT\n{kit}\n\nRECIPE (what this version is set to)\n"
        f"{recipe or '  (nothing recorded)'}\n\n"
        f"PROFILE ({facts.profile_label or 'unnamed'})\n{profile}"
    )


def _render_trajectory(entries: list[TrajectoryEntry]) -> str:
    if not entries:
        return "No earlier shots in this Set. This is the first one to go on."
    blocks: list[str] = []
    for entry in entries:
        taste = _notes(entry.taste_notes) or "no notes"
        aroma = f", smells of {_notes(entry.aroma_notes)}" if entry.aroma_notes else ""
        verdict = (
            f"rating {entry.rating}/5, balance {entry.balance or 'not stated'}, "
            f"tastes of {taste}{aroma}"
            if entry.rating or entry.balance or entry.taste_notes or entry.aroma_notes
            else "NOT JUDGED"
        )
        advice = "; ".join(entry.suggestions) or "no suggestions were made"
        blocks.append(
            f"  shot {entry.device_id} (v{entry.version_no}, {entry.started_at or 'no clock'}): "
            f"{_num(entry.duration_s)} s, {_num(entry.final_weight_g)} g, "
            f"score {_num(entry.execution_score)}, channeling {entry.channeling_risk or 'n/a'}\n"
            f"    you said: {verdict}"
            + (f"\n    notes: {entry.notes}" if entry.notes else "")
            + f"\n    advice that followed: {advice}"
        )
    return "\n".join(blocks)


def _notes(notes: list[str]) -> str:
    """Flavour-wheel notes as a person reads them: each with its path from the centre."""
    return "; ".join(flavor_path(note) for note in notes)


def _render_judgement(judgement: JudgementFacts | None) -> str:
    if judgement is None:
        return (
            "The user has not judged this shot. You have no taste information: say so, "
            "and put the taste question in questions_for_user rather than assuming one."
        )
    return (
        _block(
            [
                _line("rating", None if judgement.rating is None else f"{judgement.rating}/5"),
                _line("balance", judgement.balance),
                _line("taste", _notes(judgement.taste_notes) or None),
                _line("aroma", _notes(judgement.aroma_notes) or None),
                _line("dose in", judgement.dose_in_g, " g"),
                _line("dose out", judgement.dose_out_g, " g"),
                _line("ratio", None if judgement.ratio is None else f"1:{judgement.ratio:g}"),
                _line("grind as set", judgement.grind_setting),
                _line("decision", judgement.decision),
                _line("notes", judgement.notes),
            ]
        )
        or "The user recorded a judgement with nothing in it."
    )


def _render_previous(previous: PreviousAnalysis | None) -> str:
    if previous is None:
        return "This is the first analysis of this shot."
    advice = "; ".join(previous.suggestions) or "no suggestions"
    return (
        f"You analysed this shot before (#{previous.analysis_id}, {previous.created_at}, "
        f"{previous.model or 'unknown model'}).\n"
        f"  you said: {previous.diagnosis}\n"
        f"  you suggested: {advice}\n"
        "Do not simply repeat it. Say what you would change about that reading and why."
    )


def _render_value(value: Any) -> str:
    """A diagnostics value as text, with booleans spelled out.

    `bool` is an `int` in Python, so a plain numeric branch renders
    `scale_connected` as `1` — a number the model would be entitled to reason
    about. It is a fact, and it reads as one.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float):
        return _num(value)
    return str(value)


def _num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, int | float):
        return f"{float(value):g}"
    return str(value)

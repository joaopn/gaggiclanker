"""Everything a review is told about one shot, assembled deterministically.

One function, :func:`build_review_input`, reads the archive and produces a
document that is both the prompt's variables and the review row's
`input_json`. That it is one function matters: the snapshot stored on the row
has to be *exactly* what was sent, so there is no second assembly path where
the two could differ.

What goes in, and nothing else:

1. **the shot's own information**, written by the one shot renderer every chat
   tool uses (:func:`gaggiclanker.shotinfo.render_shot`) with every catalogue
   item in it except the ones listed in :data:`REVIEW_EXCLUDED_KEYS` and the
   groups named by :data:`REVIEW_EXCLUDED_GROUP_MEMBERS`: the person's
   judgement, the note typed on the machine (which seeds the judgement), the
   shot's earlier reviews, and the shot's label and counted state. The checks
   come first (the renderer's order), the Set version's recipe is in, since the
   checks' shares of the target need it, and the tiers a person set on Settings
   → Shot information govern what a chat is handed, never what a review reads;
2. **the free-text expectations of the confirmed signature**, each with its id, tier, phase,
   sentence and fault word: the review must answer every one;
3. **the Set version's prediction** and which version it is measured against, or the plain
   statement that there is none (the shot is not filed, or its version has no prediction);
4. **the profile the shot brewed**, the whole document of the profile version
   the shot itself links to;
5. **the detected shot style**, with its evidence;
6. **the knowledge rules and reference excerpts** the deterministic signal
   selection picks from the shot's telemetry.

**Independent by construction.** Nothing here reads the person's judgement (rating, balance,
notes, decision), the note typed on the machine, the label, another shot, an earlier
review, an insight or a conversation: the loader's judgement, note and review are dropped
before rendering, and rule selection is given no Set attributes. A review of one shot
cannot be told what the person thought of it, or what another shot did.

Determinism is the property everything here is arranged around. Nothing reads
the clock, nothing iterates a set, every list is sorted: two builds of the same
shot and the same knowledge produce byte-identical input.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.metric_language import CHANNELS, OPS
from gaggiclanker.domain.sets import version_label
from gaggiclanker.domain.warnings import fault_token
from gaggiclanker.knowledge.rules import SetContext, render_rules, select_rules
from gaggiclanker.knowledge.service import (
    DEFAULT_CHUNK_TOKEN_BUDGET,
    KnowledgeService,
    RetrievalContext,
    render_excerpts,
)
from gaggiclanker.review.style import StyleVerdict, detect_style
from gaggiclanker.shotinfo import CATALOGUE, ITEMS, ShotFacts, Tier, load_shots, render_shot

__all__ = [
    "REVIEW_CURVE_POINTS",
    "REVIEW_EXCLUDED_GROUP_MEMBERS",
    "REVIEW_EXCLUDED_KEYS",
    "ReviewInput",
    "build_review_input",
    "readable_summary",
    "review_keys",
    "review_tiers",
    "signal_tokens",
]

#: How many rows of the curve a review is shown, cut the same shape-preserving
#: way as the chat's. Fixed rather than read from the chat's own setting: what a
#: review was given depends on the shot and the knowledge only, so a person
#: tuning the chat's budget never changes what the next review of the same shot
#: reads. Sixty is the chat's default.
REVIEW_CURVE_POINTS = 60

#: Catalogue groups a review never reads, each named by one of its items so a
#: renamed group heading cannot quietly let the group back in: the person's
#: judgement (`rating`), the note typed on the machine (`note_text`: its rating,
#: balance and notes are the judgement typed somewhere else) and the shot's own
#: review (`review_state`: a review is never shown an earlier one).
REVIEW_EXCLUDED_GROUP_MEMBERS: tuple[str, ...] = (
    "rating",
    "note_text",
    "review_state",
)

#: Single items a review never reads, from groups it otherwise does: the
#: person's label (their verdict) and whether the shot is counted (which says
#: "discarded" for a shot they labelled so).
REVIEW_EXCLUDED_KEYS: frozenset[str] = frozenset({"label", "counted"})


def review_keys() -> frozenset[str]:
    """Every catalogue item a review reads: all of them but the exclusions."""
    groups = {ITEMS[key].group for key in REVIEW_EXCLUDED_GROUP_MEMBERS}
    return frozenset(
        item.key
        for item in CATALOGUE
        if item.group not in groups and item.key not in REVIEW_EXCLUDED_KEYS
    )


def review_tiers() -> Mapping[str, Tier]:
    """The tier layout a review renders the shot with, whatever the person set.

    Every item it reads is put in one tier and everything else is excluded,
    so a single rendering carries the lot.
    """
    wanted = review_keys()
    return {item.key: ("base" if item.key in wanted else "excluded") for item in CATALOGUE}


class ExpectationAsked(BaseModel):
    """One free-text expectation of the confirmed signature, as the review is asked about it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    tier: str
    #: The phase it is about, or ``None`` for the whole shot.
    phase: str | None = None
    #: The fault word it fails with.
    fault: str
    sentence: str


class ReviewInput(BaseModel):
    """Everything the model is told, as one validated document.

    Stored verbatim as the review row's `input_json`, so a review stays
    explainable after the shot's information, the prompt and the knowledge
    base have all moved on.
    """

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    #: The shot's own information, rendered.
    shot: str
    #: The label of the profile version the shot links to, and its document.
    #: Both empty when the archive holds no copy of what the machine ran.
    profile_label: str = ""
    profile: dict[str, Any] | None = None
    #: The phases the shot logged, in order: the only names a window may use.
    phases: list[str] = Field(default_factory=list)
    #: How many expectations the confirmed signature of the profile version has (0: the shot is
    #: read without a signature).
    signature_confirmed: int = 0
    #: The confirmed signature's free-text expectations, in written order: each one is answered.
    expectations: list[ExpectationAsked] = Field(default_factory=list)
    #: The name of the Set version the shot is filed under ("" when it is not filed), the
    #: prediction that version was filed with (the text the review is shown, "" for none) and
    #: the name of the version it is measured against ("" for nothing).
    version: str = ""
    prediction: str = ""
    compares_to: str = ""
    style: str = "unknown"
    style_tier: str = "none"
    style_evidence: list[str] = Field(default_factory=list)
    #: The signal tokens rule selection was made against.
    signals: list[str] = Field(default_factory=list)
    #: `[{key, category, text, confidence, source}]` for every selected rule.
    rules: list[dict[str, str]] = Field(default_factory=list)
    #: `[{heading_path, doc_slug, doc_title, heading, body, tokens_estimate,
    #: query}]` for every retrieved chunk, stored in full so a citation into a
    #: document edited since still resolves to the text the model read.
    excerpts: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def rule_keys(self) -> frozenset[str]:
        """What `rules_used` is validated against."""
        return frozenset(rule["key"] for rule in self.rules)

    @property
    def excerpt_paths(self) -> frozenset[str]:
        """What `excerpts_used` is validated against."""
        return frozenset(str(excerpt["heading_path"]) for excerpt in self.excerpts)

    def render(self) -> dict[str, str]:
        """The prompt's variables: one rendered block per section."""
        return {
            "shot_information": self.shot,
            "signature": _render_expectations(self),
            "prediction": _render_prediction(self),
            "phases": _render_phases(self.phases),
            "metric_language": _render_language(),
            "profile": _render_profile(self.profile_label, self.profile),
            "shot_style": _render_style(self),
            "knowledge_rules": render_rules(self.rules),
            "knowledge_excerpts": render_excerpts(self.excerpts),
        }


async def build_review_input(
    db: Database,
    shot_id: int,
    *,
    chunk_token_budget: int = DEFAULT_CHUNK_TOKEN_BUDGET,
) -> ReviewInput:
    """Assemble everything a review is told about one shot.

    ``chunk_token_budget`` is how much reference prose may come along, in
    estimated tokens; a parameter rather than a settings read so this stays a
    pure function of the database it was handed (the service resolves
    `knowledgeChunkTokenBudget`). Zero turns retrieval off.

    Raises ``LookupError`` for a shot that does not exist.
    """
    loaded = await load_shots(db, [shot_id], samples=True)
    if not loaded:
        raise LookupError(f"no shot {shot_id}")
    # Independent by construction: whatever the loader read about the person's
    # verdict, the machine's notes card and an earlier review is dropped here,
    # before anything renders, and the exclusions above keep the lines that
    # would describe them out as well. The Set version stays: its recipe is what
    # the checks' shares of the target are measured against, and its prediction
    # is what the review is asked to compare the shot with.
    # The shot row carries a copy of some of the person's verdict too (the
    # judgement's rating, notes and label, the machine's own rating), which no
    # item a review reads renders; cleared all the same, so the rule holds by
    # what is in hand rather than by what happens to be printed.
    shot = loaded[0].shot.model_copy(
        update={
            "rating": None,
            "index_rating": None,
            "judgement_rating": None,
            "judgement_notes": None,
            "judgement_decision": None,
            "has_judgement": False,
            "has_notes": False,
        }
    )
    facts = dataclasses.replace(loaded[0], shot=shot, judgement=None, note=None, reading=None)

    profile_label = ""
    profile: dict[str, Any] | None = None
    if facts.shot.profile_version_id is not None:
        stored = await ProfilesRepository(db).get_version(facts.shot.profile_version_id)
        if stored is not None:
            profile_label = stored.label
            profile = stored.profile

    expectations: list[ExpectationAsked] = []
    confirmed = 0
    if facts.shot.profile_version_id is not None:
        found = await SignatureRepository(db).confirmed_for_versions(
            [facts.shot.profile_version_id]
        )
        rows = found.get(facts.shot.profile_version_id, [])
        confirmed = len(rows)
        expectations = [
            ExpectationAsked(
                id=row.id,
                tier=row.tier,
                phase=row.phase,
                fault=row.fault or "",
                sentence=row.sentence,
            )
            for row in rows
            if row.kind == "free_text"
        ]

    version_name = ""
    prediction = ""
    compares_to = ""
    if facts.version is not None:
        version_name = version_label(facts.version.version_major, facts.version.version_minor)
        prediction = facts.version.prediction.strip()
        if prediction and facts.version.compares_to_version_id is not None:
            compared = await SetsRepository(db).get_version(facts.version.compares_to_version_id)
            if compared is not None:
                compares_to = version_label(compared.version_major, compared.version_minor)

    duration = facts.shot.duration_ms / 1000 if facts.shot.duration_ms > 0 else None
    verdict = detect_style(
        profile,
        # No dose: it lives in the judgement and the Set version, and the style is the
        # profile's, so the allongé test (a ratio) is skipped.
        dose_g=None,
        summary=readable_summary(facts),
        duration_s=None if duration is None else round(duration, 2),
        profile_name=facts.shot.profile_label or facts.shot.profile_name_on_device,
    )

    signals = signal_tokens(facts, verdict)
    # No Set attributes: the bean and the grinder are the Set's, so a rule
    # keyed on them is not selected for a review.
    selection = await select_rules(RulesRepository(db), SetContext(), verdict.style, signals)
    excerpts = await KnowledgeService(db).select_chunks(
        RetrievalContext(style=verdict.style, signals=tuple(selection.signals)),
        token_budget=chunk_token_budget,
    )

    return ReviewInput(
        shot_id=facts.shot_id,
        shot=render_shot(facts, "base", review_tiers(), curve_points=REVIEW_CURVE_POINTS),
        profile_label=profile_label,
        profile=profile,
        phases=list(
            dict.fromkeys(
                name for phase in facts.phases if (name := str(phase.get("name") or "").strip())
            )
        ),
        signature_confirmed=confirmed,
        expectations=expectations,
        version=version_name,
        prediction=prediction,
        compares_to=compares_to,
        style=verdict.style,
        style_tier=verdict.tier,
        style_evidence=list(verdict.evidence),
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
    )


def signal_tokens(facts: ShotFacts, style: StyleVerdict) -> list[str]:
    """The tokens rule selection matches `applies.signal` against, from the telemetry.

    The grammar is documented in :mod:`gaggiclanker.knowledge.rules`. Only the
    telemetry shapes come from here: a review reads no taste, so the taste,
    aroma and balance tokens are never produced for one (the chat can still
    pass them to `get_rules`). The faults are the shot's own warnings (``fault:
    fast_flow``, ``fault:skipped``, and the two yield ones when the shot is filed
    under a Set version with a target, which a reading is given), and the rest
    are plain readings of the numbers with no grade in them. The puck-flow readings are left
    out for a shot flagged without a pressure sensor (:func:`readable_summary`). Sorted,
    because the list is stored on the review row and a set's iteration order would make two
    identical runs produce different snapshots.
    """
    tokens: set[str] = {f"style:{style.style}"}
    tokens.update(f"fault:{fault_token(warning.fault)}" for warning in facts.warnings)

    if not facts.has_scale:
        tokens.add("scale:absent")

    flow = readable_summary(facts).get("flow") or {}
    # The first drip the model is shown: the cup's when the shot had a scale, the puck flow's
    # estimate otherwise, so the rule picked and the number read are one figure.
    first_drip = (
        facts.summary_value("flow", "cup_first_drip_s")
        if facts.has_scale
        else flow.get("time_to_first_drip_s")
    )
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

    temperature = facts.summary.get("temperature") or {}
    target = temperature.get("target_avg_c")
    actual = temperature.get("avg_c")
    if isinstance(target, int | float) and isinstance(actual, int | float) and target > 0:
        if actual - target < -3:
            tokens.add("temp:cold")
        elif actual - target > 2:
            tokens.add("temp:hot")

    volume = facts.shot.volume_g
    if volume is not None and volume < 1:
        # Not a failed shot: the firmware records a fraction of a gram when the
        # cup came off the scale early, and reading it as a yield is how a
        # model concludes the shot did not run.
        tokens.add("yield:tiny")

    return sorted(tokens)


def readable_summary(facts: ShotFacts) -> dict[str, Any]:
    """The summary block with the readings this shot cannot have taken out of it.

    A board with no pressure sensor writes puck flow and pressure into every sample as zeros,
    and the mask says the column exists, not that anything was measured. A shot derived before
    the gate on those (or written by hand) can still carry numbers built on them in its stored
    summary, and style detection (turbo is a flow) and the rule tokens (a fast first drip, a
    high average flow) would read them as a puck that behaved so. The same rule the catalogue
    applies to those items (`ShotFacts.puck_flow_recorded`), applied here where the summary is
    read directly.
    """
    summary = dict(facts.summary)
    if not facts.puck_flow_recorded:
        summary.pop("flow", None)
    if not facts.has_pressure:
        summary.pop("pressure", None)
    return summary


def _render_profile(label: str, profile: dict[str, Any] | None) -> str:
    if not profile:
        return "The archive holds no copy of the profile this shot was brewed with."
    document = json.dumps(profile, separators=(",", ":"), sort_keys=True)
    return f"{label or 'unnamed'}\n{document}"


def _render_style(review: ReviewInput) -> str:
    evidence = "; ".join(review.style_evidence) or "no evidence"
    return f"{review.style} (detected from {review.style_tier}: {evidence})"


def _render_expectations(review: ReviewInput) -> str:
    """The free-text expectations to answer, one line each, or why there are none."""
    if review.signature_confirmed == 0:
        return (
            "This shot's profile version has no confirmed signature, so there is nothing to "
            "answer: `free_text_results` is an empty list."
        )
    if not review.expectations:
        return (
            f"The confirmed signature has {review.signature_confirmed} expectations and none is "
            "free text, so there is nothing to answer: `free_text_results` is an empty list."
        )
    lines = [
        f"id {item.id} · {item.tier} · {item.phase or 'whole shot'} · fails as {item.fault}: "
        f"{item.sentence}"
        for item in review.expectations
    ]
    return "\n".join(lines)


def _render_prediction(review: ReviewInput) -> str:
    if not review.version:
        return (
            "The shot is not filed under a Set version, so there is no prediction to compare it "
            "with: the output has no `prediction` key."
        )
    if not review.prediction:
        return (
            f"The Set version the shot is filed under ({review.version}) was filed with no "
            "prediction: the output has no `prediction` key."
        )
    against = (
        f"measured against {review.compares_to}"
        if review.compares_to
        else "measured against nothing earlier: on the numbers this version states itself"
    )
    return f"Version {review.version} predicted, {against}:\n{review.prediction}"


def _render_language() -> str:
    """How an expression is written, from the language's own lists so the prompt cannot drift."""
    return "\n".join(
        [
            "You attach expressions; the server works out the numbers on this shot. An expression "
            "is a JSON object with `channel`, `op` and `window`, and optionally `relative_to`, "
            "`compare`, and for the time operations `threshold` (and `direction` for `time_to`).",
            f"channels: {', '.join(CHANNELS)}. The cup weight and the cup flow (`scale_flow`: "
            "what reached the cup, measured by the scale) come from the scale alone; the puck "
            "flow, pump flow, water pumped and resistance are the machine's estimates. Puck flow "
            "is an estimate from the pump model that stays near the pump flow; it does not track "
            "when coffee reaches the cup and can be seconds later or, on a long pre-infusion, "
            "much earlier. The target channels are what the profile commanded. A channel the shot "
            "did not record is reported as not measured, never as zero.",
            f"operations: {', '.join(OPS)}. `gained` reads the cup weight or the water pumped "
            "over one phase. `slope` is per second. `time_to`, `time_above` and `time_below` "
            "need a `threshold`.",
            'window: `{}` for the whole shot, `{"phase": "<a phase this shot logged>"}`, or a '
            'span `{"from": <anchor>, "to": <anchor>}`. An anchor is "shot_start", "shot_end", '
            '"first_drip" (the first puck flow), "peak_pressure", `{"phase_start": "<phase>"}`, '
            '`{"phase_end": "<phase>"}` or `{"at_s": <seconds>}`, each optionally with '
            '"offset_s".',
            '`relative_to`: "target_yield", "dose" or "final_weight" turns the value into a '
            "share of it (0.15 is 15 %).",
            '`compare`: `{"op": "<" | "<=" | ">" | ">=", "value": n}` or `{"op": "between", '
            '"low": a, "high": b}`: the condition your claim asserts, so that it holds when the '
            "claim is true (a cup over its limit is `>` the limit). The server says whether it "
            "held; a claim whose comparison did not hold is marked as not borne out.",
        ]
    )


def _render_phases(phases: list[str]) -> str:
    if not phases:
        return "This shot logged no phase table: a window may be the whole shot or a time span."
    return "; ".join(phases)

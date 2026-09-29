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
   Set version's recipe, the shot's earlier reviews, and the shot's Set, label
   and counted state. The tiers
   a person set on Settings → Shot information govern what a chat is handed,
   never what a review reads;
2. **the profile the shot brewed**, the whole document of the profile version
   the shot itself links to;
3. **the detected shot style**, with its evidence;
4. **the knowledge rules and reference excerpts** the deterministic signal
   selection picks from the shot's telemetry.

**Blind and independent by construction.** Nothing here reads the judgement,
the Set, its versions, another shot, an insight or an earlier review: the
loader's judgement, version, note and review are dropped before rendering,
rule selection is given no Set attributes, and retrieval no taste. So the
taste prediction needs no withholding trick, and a review of one shot cannot
be told what the Set is trying or what another shot did.

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
#: balance and notes are the judgement typed somewhere else), the Set version's
#: recipe (`recipe_grind`) and the shot's own review (`review_summary`: a
#: review is never shown an earlier one).
REVIEW_EXCLUDED_GROUP_MEMBERS: tuple[str, ...] = (
    "rating",
    "note_text",
    "recipe_grind",
    "review_summary",
)

#: Single items a review never reads, from groups it otherwise does: the
#: person's label (their verdict), whether the shot is counted (which says
#: "discarded" for a shot they labelled so) and the Set version it is filed
#: under (the Set is not the review's business, and a blind rendering would
#: otherwise say "not filed in a Set" about a shot that is).
REVIEW_EXCLUDED_KEYS: frozenset[str] = frozenset({"label", "counted", "set_version"})


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
    # Blind by construction: whatever the loader read about the person's
    # verdict, the Set version, the machine's notes card and an earlier review
    # is dropped here, before anything renders, and the exclusions below keep
    # the lines that would describe them out as well.
    # The shot row carries a copy of some of it too (the Set badge, the
    # judgement's rating, notes and label, the machine's own rating), which no
    # item a review reads renders; cleared all the same, so the rule holds by
    # what is in hand rather than by what happens to be printed.
    shot = loaded[0].shot.model_copy(
        update={
            "set_version_id": None,
            "set_badge": None,
            "rating": None,
            "index_rating": None,
            "judgement_rating": None,
            "judgement_notes": None,
            "judgement_decision": None,
            "has_judgement": False,
            "has_notes": False,
        }
    )
    facts = dataclasses.replace(
        loaded[0], shot=shot, judgement=None, version=None, note=None, review=None
    )

    profile_label = ""
    profile: dict[str, Any] | None = None
    if facts.shot.profile_version_id is not None:
        stored = await ProfilesRepository(db).get_version(facts.shot.profile_version_id)
        if stored is not None:
            profile_label = stored.label
            profile = stored.profile

    duration = facts.shot.duration_ms / 1000 if facts.shot.duration_ms > 0 else None
    verdict = detect_style(
        profile,
        # No dose: it lives in the judgement and the Set version, neither of
        # which a review reads, so the allongé test (a ratio) is skipped.
        dose_g=None,
        summary=dict(facts.summary),
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


#: Annotation keys whose value is a sentence written for a reader (the engine's
#: "No pressure sensor on this machine" and "Trimmed 26 ramp-up samples"
#: notes, the channeling guidance), not a band. No rule keys on one, and as a
#: token each would sit in the stored signal list and the retrieval queries.
_PROSE_ANNOTATIONS = frozenset({"note", "guidance"})


def signal_tokens(facts: ShotFacts, style: StyleVerdict) -> list[str]:
    """The tokens rule selection matches `applies.signal` against, from the telemetry.

    The grammar is documented in :mod:`gaggiclanker.knowledge.rules`. Only the
    telemetry shapes come from here: a review reads no taste, so the taste,
    aroma and balance tokens are never produced for one (the chat can still
    pass them to `get_rules`). Sorted, because the list is stored on the review
    row and a set's iteration order would make two identical runs produce
    different snapshots.
    """
    tokens: set[str] = {f"style:{style.style}"}
    diagnostics = facts.diagnostics

    for metric, label in (diagnostics.get("annotations") or {}).items():
        if metric not in _PROSE_ANNOTATIONS:
            tokens.add(f"{metric}:{label}")
    # The full diagnostics block, which is what ingest stores for every synced
    # and imported shot, nests its annotations one level deeper, per section,
    # under short keys (`resistance.annotations.level`). The rules key on the
    # summary block's section-qualified names (`resistance_level`), and
    # `stability` alone would be ambiguous between resistance and temperature,
    # so the sections that use short keys are qualified here. Both shapes are
    # read because which one a shot carries depends on the detail level it was
    # derived at.
    for section in ("resistance", "temperature"):
        block = diagnostics.get(section)
        if isinstance(block, dict):
            for metric, label in (block.get("annotations") or {}).items():
                if isinstance(label, str) and metric not in _PROSE_ANNOTATIONS:
                    tokens.add(f"{section}_{metric}:{label}")
    # Extraction and profile compliance already name their metrics in full
    # (`pressure_adherence`, `flow_trend`), so those are read as they are.
    for section in ("extraction", "profile_compliance"):
        block = diagnostics.get(section)
        if isinstance(block, dict):
            for metric, label in (block.get("annotations") or {}).items():
                if isinstance(label, str) and metric not in _PROSE_ANNOTATIONS:
                    tokens.add(f"{metric}:{label}")
    channeling = diagnostics.get("channeling")
    if isinstance(channeling, dict):
        # The risk is a field of the block, not an annotation. Its annotations
        # are read for the indicators that fired only: the per-indicator bands
        # (`flow_jitter`, `pressure_drop`, …) match no rule, and `guidance` and
        # `note` are prose, which is not a token.
        risk = channeling.get("channeling_risk")
        if isinstance(risk, str):
            tokens.add(f"channeling_risk:{risk}")
        primary = (channeling.get("annotations") or {}).get("primary_signal")
        if isinstance(primary, str) and primary != "none":
            tokens.update(f"primary:{name}" for name in primary.split(",") if name)

    if not facts.shot.scale_connected:
        tokens.add("scale:absent")

    flow = facts.summary.get("flow") or {}
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


def _render_profile(label: str, profile: dict[str, Any] | None) -> str:
    if not profile:
        return "The archive holds no copy of the profile this shot was brewed with."
    document = json.dumps(profile, separators=(",", ":"), sort_keys=True)
    return f"{label or 'unnamed'}\n{document}"


def _render_style(review: ReviewInput) -> str:
    evidence = "; ".join(review.style_evidence) or "no evidence"
    return f"{review.style} (detected from {review.style_tier}: {evidence})"

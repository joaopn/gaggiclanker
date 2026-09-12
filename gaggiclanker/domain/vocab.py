"""The closed vocabularies, in one module.

Every one of these appears in three places — a SQL ``CHECK`` constraint, a
pydantic ``Literal`` that becomes an OpenAPI enum, and a control in the front
end — and the only way they stay equal is for two of the three to be generated
from the third. This module is the third.

``GET /api/vocab`` serves the whole of it, so the UI renders a taste chip, a
roast-level select or a decision button from data rather than from a list typed
into a component; the migration's CHECK constraints are written against the same
tuples and there is a test that walks the database's own schema to prove they
still agree.

The taste vocabulary is crema's, kept
verbatim including its grouping into sour side / dialled in / bitter side /
strength. The grouping is the useful part: a tag on its own is a word, but
"three of the five tags you picked are on the sour side" is a diagnosis, and it
is what the analyzer is handed. Each tag carries a one-line meaning because the
words are jargon — "astringent" and "bitter" are the same thing to most people
and opposite things to a barista — and a chip whose definition appears on hover
teaches the vocabulary rather than assuming it.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

__all__ = [
    "BALANCES",
    "BURR_TYPES",
    "DECISIONS",
    "PROCESSES",
    "ROAST_LEVELS",
    "SET_VERSION_ORIGINS",
    "STEP_UNITS",
    "TASTE_GROUPS",
    "TASTE_TAGS",
    "Balance",
    "BurrType",
    "Decision",
    "Process",
    "RoastLevel",
    "SetVersionOrigin",
    "StepUnit",
    "TasteGroup",
    "TasteTag",
    "Vocabulary",
    "vocabulary",
]

# ── beans ────────────────────────────────────────────────────────────

#: How the cherry was processed. Closed because the knowledge tier's pressure
#: matrix is indexed by (roast level x process); free text would make every
#: lookup a fuzzy match. 'other' is the escape hatch, and NULL is the honest
#: answer for a bag that does not say.
type Process = Literal["washed", "natural", "honey", "anaerobic", "other"]

#: Five steps, the ones roasters actually print. Ordered light → dark, which is
#: the order the UI renders and the order a rule's range is expressed in.
type RoastLevel = Literal["light", "medium-light", "medium", "medium-dark", "dark"]

# ── hardware ─────────────────────────────────────────────────────────

#: Conical and flat burrs want different profiles; 'unknown' is the default
#: because most people do not know and guessing would feed the analyzer a fact
#: it would then reason from.
type BurrType = Literal["conical", "flat", "unknown"]

#: What one step of this grinder's adjustment is called. Advice is given in the
#: grinder's own units — "two clicks finer" is actionable, "15 microns finer" is
#: not — so this is the field that stops the analyzer inventing a scale.
type StepUnit = Literal["clicks", "numbers", "microns", "free"]

# ── judgement ────────────────────────────────────────────────────────

#: Where the cup sits on the one axis that maps cleanly onto extraction.
#: Deliberately the same three values the firmware's own notes card uses
#: (`domain/models.py::BalanceTaste`), so seeding a judgement from a device note
#: is a copy rather than a translation.
type Balance = Literal["sour", "balanced", "bitter"]

#: What to do next. NULL is "not decided yet", which is most shots.
type Decision = Literal["keep", "adjust", "discard"]

# ── sets ─────────────────────────────────────────────────────────────

#: Who proposed a Set version. Recorded rather than inferred so that "did
#: following the model's advice actually help" is a GROUP BY a year later.
type SetVersionOrigin = Literal["manual", "analysis", "chat"]


PROCESSES: tuple[str, ...] = get_args(Process.__value__)
ROAST_LEVELS: tuple[str, ...] = get_args(RoastLevel.__value__)
BURR_TYPES: tuple[str, ...] = get_args(BurrType.__value__)
STEP_UNITS: tuple[str, ...] = get_args(StepUnit.__value__)
BALANCES: tuple[str, ...] = get_args(Balance.__value__)
DECISIONS: tuple[str, ...] = get_args(Decision.__value__)
SET_VERSION_ORIGINS: tuple[str, ...] = get_args(SetVersionOrigin.__value__)


class TasteTag(BaseModel):
    """One taste chip: the stored slug, the word a person reads, what it means."""

    model_config = ConfigDict(extra="forbid")

    #: What goes in `shot_judgements.taste_tags_json`. Slugs rather than labels
    #: because "weak/watery" in a JSON array is a string with a slash in it that
    #: somebody will eventually try to put in a URL.
    value: str
    label: str
    meaning: str


class TasteGroup(BaseModel):
    """A named group of chips, with the direction it points."""

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    #: What picking tags from this group tells the analyzer, in one line.
    meaning: str
    tags: list[TasteTag]


#: crema's vocabulary, verbatim.
#:
#: The four groups are not decoration: "sour side" and "bitter side" are the two
#: directions an extraction can be wrong in, "dialled in" is the target, and
#: "strength" is the axis that is *independent* of extraction — a cup can be
#: perfectly balanced and simply too weak, which is a dose-and-yield problem and
#: not a grind problem. Folding strength into the other three is the classic
#: dial-in mistake, and keeping it as its own group is how the UI stops making
#: it.
TASTE_GROUPS: tuple[TasteGroup, ...] = (
    TasteGroup(
        value="sour",
        label="Sour side",
        meaning="Under-extracted: the water left before it had taken the sugars.",
        tags=[
            TasteTag(
                value="sour",
                label="sour",
                meaning="Puckering, lemon-juice acidity with nothing behind it.",
            ),
            TasteTag(
                value="sharp",
                label="sharp",
                meaning="Acidity with an edge on it — biting rather than bright.",
            ),
            TasteTag(
                value="thin",
                label="thin",
                meaning="Watery body: the cup feels dilute even at the right yield.",
            ),
            TasteTag(
                value="salty",
                label="salty",
                meaning="A faint saline note — the classic under-extraction tell.",
            ),
            TasteTag(
                value="quick_finish",
                label="quick finish",
                meaning="The flavour drops away a second after the sip.",
            ),
        ],
    ),
    TasteGroup(
        value="dialled_in",
        label="Dialled in",
        meaning="Where you are aiming: sweetness carrying the cup, nothing sticking out.",
        tags=[
            TasteTag(
                value="sweet",
                label="sweet",
                meaning="Sugar-browning sweetness carries the cup.",
            ),
            TasteTag(
                value="balanced",
                label="balanced",
                meaning="Acidity, sweetness and bitterness in proportion.",
            ),
            TasteTag(
                value="syrupy",
                label="syrupy",
                meaning="Heavy, coating body that clings to the tongue.",
            ),
            TasteTag(
                value="long_finish",
                label="long finish",
                meaning="The flavour is still there half a minute after the sip.",
            ),
        ],
    ),
    TasteGroup(
        value="bitter",
        label="Bitter side",
        meaning="Over-extracted: the water kept going and took the bitter compounds too.",
        tags=[
            TasteTag(
                value="bitter",
                label="bitter",
                meaning="Dark, burnt-cocoa bitterness dominating the cup.",
            ),
            TasteTag(
                value="harsh",
                label="harsh",
                meaning="Rough and aggressive rather than merely bitter.",
            ),
            TasteTag(
                value="astringent",
                label="astringent",
                meaning="Mouth-drying grip, like over-steeped black tea.",
            ),
            TasteTag(
                value="drying",
                label="drying",
                meaning="Leaves the tongue papery after the swallow.",
            ),
            TasteTag(
                value="hollow",
                label="hollow",
                meaning="Bitter edges with nothing in the middle.",
            ),
        ],
    ),
    TasteGroup(
        value="strength",
        label="Strength",
        meaning=(
            "Independent of extraction: a cup can be balanced and still be the wrong "
            "concentration, which is a dose-and-yield fix rather than a grind one."
        ),
        tags=[
            TasteTag(
                value="weak_watery",
                label="weak / watery",
                meaning="Right balance, too little of it — the cup is under strength.",
            ),
            TasteTag(
                value="too_intense",
                label="too intense / muddy",
                meaning="Overwhelming and smeared; flavours run into each other.",
            ),
        ],
    ),
)

#: Every tag slug, flattened. The validator for `taste_tags_json`.
TASTE_TAGS: tuple[str, ...] = tuple(tag.value for group in TASTE_GROUPS for tag in group.tags)


class Term(BaseModel):
    """One member of a simple vocabulary: the stored value and its label."""

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str


class Vocabulary(BaseModel):
    """Everything `GET /api/vocab` answers with.

    One request on page load, and the front end has every enum it needs. A UI
    that hard-codes these drifts from the database the first time one changes,
    and the symptom is a 422 on a value the user picked from a dropdown.
    """

    model_config = ConfigDict(extra="forbid")

    roast_levels: list[Term]
    processes: list[Term]
    burr_types: list[Term]
    step_units: list[Term]
    balances: list[Term]
    decisions: list[Term]
    origins: list[Term]
    taste_groups: list[TasteGroup]


def _terms(values: tuple[str, ...], labels: dict[str, str] | None = None) -> list[Term]:
    overrides = labels or {}
    return [
        Term(value=value, label=overrides.get(value, value.replace("-", " ").replace("_", " ")))
        for value in values
    ]


#: Labels that are not just the slug with its punctuation softened.
_BALANCE_LABELS = {
    "sour": "Sour — under-extracted",
    "balanced": "Balanced",
    "bitter": "Bitter — over-extracted",
}
_DECISION_LABELS = {
    "keep": "Keep this recipe",
    "adjust": "Adjust and pull again",
    "discard": "Discard — something went wrong",
}
_ORIGIN_LABELS = {
    "manual": "You changed it",
    "analysis": "From an analysis",
    "chat": "From the chat",
}


def vocabulary() -> Vocabulary:
    """The whole vocabulary, built fresh.

    Not a module-level constant: it is pydantic models all the way down and a
    shared instance is a mutable object every request would hand to a serialiser
    that could, in principle, be handed it twice at once. Building it costs
    microseconds and the endpoint is called once per page load.
    """
    return Vocabulary(
        roast_levels=_terms(ROAST_LEVELS),
        processes=_terms(PROCESSES),
        burr_types=_terms(BURR_TYPES),
        step_units=_terms(STEP_UNITS),
        balances=_terms(BALANCES, _BALANCE_LABELS),
        decisions=_terms(DECISIONS, _DECISION_LABELS),
        origins=_terms(SET_VERSION_ORIGINS, _ORIGIN_LABELS),
        taste_groups=list(TASTE_GROUPS),
    )

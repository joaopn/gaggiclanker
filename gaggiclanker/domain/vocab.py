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
    "ACTIONABLE_VARIABLES",
    "ANALYSIS_STATUSES",
    "BALANCES",
    "BURR_TYPES",
    "DECISIONS",
    "PROCESSES",
    "ROAST_LEVELS",
    "RULE_CATEGORIES",
    "RULE_CONFIDENCES",
    "SET_VERSION_ORIGINS",
    "SHOT_STYLES",
    "STEP_UNITS",
    "SUGGESTION_DIRECTIONS",
    "SUGGESTION_STATUSES",
    "SUGGESTION_UNITS",
    "SUGGESTION_VARIABLES",
    "TASTE_GROUPS",
    "TASTE_TAGS",
    "AnalysisStatus",
    "Balance",
    "BurrType",
    "Decision",
    "Process",
    "RoastLevel",
    "RuleCategory",
    "RuleConfidence",
    "SetVersionOrigin",
    "ShotStyle",
    "StepUnit",
    "SuggestionDirection",
    "SuggestionStatus",
    "SuggestionUnit",
    "SuggestionVariable",
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
#: `starting_point` is the starting-point wizard — the only origin that can appear on a
#: *first* version, which is what makes "how good is the cold start" answerable.
type SetVersionOrigin = Literal["manual", "analysis", "chat", "starting_point"]

# ── analysis ─────────────────────────────────────────────────────────

#: The shot styles the analyzer detects from the profile (and, failing that,
#: from the telemetry). Six of them are gaggimate-mcp's own three-tier
#: detection; `utility` and
#: `unknown` are ours. `utility` is a backflush or a flush — a profile that
#: brews nothing, so every dial-in rule about it would be nonsense — and
#: `unknown` is the honest answer when neither the profile nor the curve says,
#: which is better than filing an odd profile under `classic` and then advising
#: from classic's expectations.
type ShotStyle = Literal[
    "classic", "turbo", "bloom", "lever", "allonge", "dark", "utility", "unknown"
]

#: What a suggestion is *about*. The first four are actionable in the prototype
#: — accepting one writes a new Set version — and the rest are recorded and
#: shown but have nowhere to be applied yet: pressure, flow and preinfusion are
#: profile edits, and gaggiclanker writes nothing to the device.
type SuggestionVariable = Literal[
    "grind",
    "dose",
    "yield",
    "temperature",
    "pressure",
    "flow",
    "preinfusion",
    "puck_prep",
    "profile",
]

#: The variables `POST /api/suggestions/{id}/accept` can actually apply.
ACTIONABLE_VARIABLES: tuple[str, ...] = ("grind", "dose", "yield", "temperature")

#: Which way to move. `finer`/`coarser` are the grinder's words and
#: `increase`/`decrease` everything else's; keeping both rather than one signed
#: magnitude is what lets the UI render "two steps finer" instead of "grind
#: minus two", which is not how anybody says it. `hold` is a real answer — "this one
#: is right, change something else" — and is accepted as a no-op.
type SuggestionDirection = Literal["finer", "coarser", "increase", "decrease", "hold"]

#: The units a magnitude may carry, restricted so the number means something.
#: `grinder_steps` is the grinder's own unit (clicks, numbers, microns — which
#: one is on the grinder row), because "15 microns finer" is not actionable on
#: a Niche and "two steps" is.
type SuggestionUnit = Literal["grinder_steps", "g", "c", "bar", "ml_s", "seconds", "none"]

#: Where a suggestion has got to. `superseded` is what happens to the open
#: siblings for the same variable when one of them is accepted: they were not
#: rejected, they were overtaken.
type SuggestionStatus = Literal["open", "accepted", "rejected", "superseded"]

#: An analysis row's lifecycle. `interrupted` is what a `running` row becomes at
#: the next boot — the process died mid-call — and it is distinct from `failed`
#: because nothing was learned about the provider.
type AnalysisStatus = Literal["running", "ok", "failed", "interrupted"]

#: How much a knowledge rule is worth. `expert` is a published heuristic,
#: `calibrated` is a threshold measured against real shots, `anecdotal` is one
#: person's report, and `learned` is one this archive derived from its own Sets
#: (nothing writes those yet).
type RuleConfidence = Literal["expert", "calibrated", "anecdotal", "learned"]

#: The rule categories, in the order the analyzer's prompt lists them. The
#: order is load-bearing: rule selection is deterministic (the acceptance
#: criterion "two analyses of the same shot select the same rules"), and the
#: sort key is this tuple's index followed by the rule key.
type RuleCategory = Literal[
    "dial_in_order",
    "increments",
    "temperature_by_roast",
    "pressure_matrix",
    "ratio_by_style",
    "time_by_style",
    "rest_times",
    "band_meanings",
    "taste_to_suspect",
    "telemetry_to_cause",
    "profile_design_defaults",
    "equipment_hints",
    "safety_bounds",
]


PROCESSES: tuple[str, ...] = get_args(Process.__value__)
ROAST_LEVELS: tuple[str, ...] = get_args(RoastLevel.__value__)
BURR_TYPES: tuple[str, ...] = get_args(BurrType.__value__)
STEP_UNITS: tuple[str, ...] = get_args(StepUnit.__value__)
BALANCES: tuple[str, ...] = get_args(Balance.__value__)
DECISIONS: tuple[str, ...] = get_args(Decision.__value__)
SET_VERSION_ORIGINS: tuple[str, ...] = get_args(SetVersionOrigin.__value__)
SHOT_STYLES: tuple[str, ...] = get_args(ShotStyle.__value__)
SUGGESTION_VARIABLES: tuple[str, ...] = get_args(SuggestionVariable.__value__)
SUGGESTION_DIRECTIONS: tuple[str, ...] = get_args(SuggestionDirection.__value__)
SUGGESTION_UNITS: tuple[str, ...] = get_args(SuggestionUnit.__value__)
SUGGESTION_STATUSES: tuple[str, ...] = get_args(SuggestionStatus.__value__)
ANALYSIS_STATUSES: tuple[str, ...] = get_args(AnalysisStatus.__value__)
RULE_CONFIDENCES: tuple[str, ...] = get_args(RuleConfidence.__value__)
RULE_CATEGORIES: tuple[str, ...] = get_args(RuleCategory.__value__)


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
    #: The analyzer's own closed sets, served for the same reason as the
    #: rest: the Knowledge page and the suggestion cards render these words, and
    #: a component that typed them would drift from the CHECK constraint behind
    #: them.
    shot_styles: list[Term]
    suggestion_variables: list[Term]
    suggestion_directions: list[Term]
    suggestion_units: list[Term]
    suggestion_statuses: list[Term]
    rule_categories: list[Term]
    rule_confidences: list[Term]


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
_STYLE_LABELS = {
    "classic": "Classic — 9 bar, 1:2, 25-35 s",
    "turbo": "Turbo — 5-6 bar, fast and long-ratio",
    "bloom": "Bloom — pump off for a soak",
    "lever": "Lever — a long declining pressure",
    "allonge": "Allongé — low pressure, long ratio",
    "dark": "Dark — under 9 bar, short",
    "utility": "Utility — backflush or flush, not a shot",
    "unknown": "Unknown — the profile did not say",
}
_VARIABLE_LABELS = {
    "grind": "Grind",
    "dose": "Dose in",
    "yield": "Yield out",
    "temperature": "Temperature",
    "pressure": "Pressure",
    "flow": "Flow",
    "preinfusion": "Pre-infusion",
    "puck_prep": "Puck prep",
    "profile": "Profile",
}
_UNIT_LABELS = {
    "grinder_steps": "grinder steps",
    "g": "g",
    "c": "°C",
    "bar": "bar",
    "ml_s": "ml/s",
    "seconds": "s",
    "none": "—",
}
_CONFIDENCE_LABELS = {
    "expert": "Expert heuristic",
    "calibrated": "Calibrated on real shots",
    "anecdotal": "Anecdotal",
    "learned": "Learned here",
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
        shot_styles=_terms(SHOT_STYLES, _STYLE_LABELS),
        suggestion_variables=_terms(SUGGESTION_VARIABLES, _VARIABLE_LABELS),
        suggestion_directions=_terms(SUGGESTION_DIRECTIONS),
        suggestion_units=_terms(SUGGESTION_UNITS, _UNIT_LABELS),
        suggestion_statuses=_terms(SUGGESTION_STATUSES),
        rule_categories=_terms(RULE_CATEGORIES),
        rule_confidences=_terms(RULE_CONFIDENCES, _CONFIDENCE_LABELS),
    )

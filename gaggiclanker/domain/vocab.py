"""The closed vocabularies, in one module.

Every one of these appears in three places — a SQL ``CHECK`` constraint, a
pydantic ``Literal`` that becomes an OpenAPI enum, and a control in the front
end — and the only way they stay equal is for two of the three to be generated
from the third. This module is the third.

``GET /api/vocab`` serves the whole of it, so the UI renders a flavour note, a
roast-level select or a decision button from data rather than from a list typed
into a component; the schema's CHECK constraints are written against the same
tuples and there is a test that walks the database's own schema to prove they
still agree.

What a cup tasted and smelt like is recorded against the SCA/WCR Coffee
Taster's Flavor Wheel — the standard a coffee person already reads — as two
lists of notes, one for taste and one for aroma. The extraction direction is
not on the wheel and does not need to be: it is the balance (sour, balanced,
bitter), the GaggiMate's own three-way verdict, kept as a control of its own.
"""

from __future__ import annotations

import re
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BALANCES",
    "BURR_TYPES",
    "DECISIONS",
    "FLAVOR_LABELS",
    "FLAVOR_NOTES",
    "FLAVOR_PICK_KINDS",
    "FLAVOR_WHEEL",
    "MEASURE_DECIMALS",
    "MEASURE_DIFFERENCE_DECIMALS",
    "OUTCOME_STATES",
    "PREDICTION_STANCES",
    "PROCESSES",
    "REVIEW_CLAIM_KINDS",
    "REVIEW_CLAIM_STATUSES",
    "REVIEW_STATUSES",
    "ROAST_LEVELS",
    "RULE_CATEGORIES",
    "RULE_CONFIDENCES",
    "SET_VERSION_ORIGINS",
    "SHOT_STYLES",
    "SPREAD_MEASURES",
    "STEP_UNITS",
    "VERSION_OUTCOMES",
    "Balance",
    "BurrType",
    "Decision",
    "FlavorNode",
    "FlavorPickKind",
    "MeasureTerm",
    "OutcomeState",
    "PredictionStance",
    "Process",
    "ReviewClaimKind",
    "ReviewClaimStatus",
    "ReviewStatus",
    "RoastLevel",
    "RuleCategory",
    "RuleConfidence",
    "SetVersionOrigin",
    "ShotStyle",
    "SpreadMeasure",
    "StepUnit",
    "VersionOutcome",
    "Vocabulary",
    "flavor_ancestors",
    "flavor_path",
    "in_wheel_order",
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
#: because most people do not know and guessing would feed a model a fact it
#: would then reason from.
type BurrType = Literal["conical", "flat", "unknown"]

#: What one step of this grinder's adjustment is called. Advice is given in the
#: grinder's own units — "two clicks finer" is actionable, "15 microns finer" is
#: not — so this is the field that stops a model inventing a scale.
type StepUnit = Literal["clicks", "numbers", "microns", "free"]

# ── judgement ────────────────────────────────────────────────────────

#: Where the cup sits on the one axis that maps cleanly onto extraction.
#: Deliberately the same three values the firmware's own notes card uses
#: (`domain/models.py::BalanceTaste`), so seeding a judgement from a device note
#: is a copy rather than a translation.
type Balance = Literal["sour", "balanced", "bitter"]

#: What to do next with this recipe: keep it, improve on it, or throw the shot
#: away. NULL is "not decided yet", which is most shots.
type Decision = Literal["keep", "improve", "discard"]

#: The two lists of wheel notes a person keeps for the shot panel: the notes
#: offered on its Taste row and the ones offered on its Aroma row.
type FlavorPickKind = Literal["taste", "aroma"]

# ── sets ─────────────────────────────────────────────────────────────

#: Who proposed a Set version. Recorded rather than inferred so that "did
#: following the model's advice actually help" is a GROUP BY a year later.
#: `starting_point` is the starting-point wizard — the only origin that can appear on a
#: *first* version, which is what makes "how good is the cold start" answerable.
#: `analysis` is history: an accepted suggestion of the retired per-shot
#: analysis wrote it, and the versions it made keep it; nothing writes it now.
type SetVersionOrigin = Literal["manual", "analysis", "chat", "starting_point"]

#: A person's grade of a version's prediction, recorded after the shots are in.
#: Four values rather than a yes/no because the honest answer to "did what you
#: expected happen" is usually neither: a change that fixed the sourness and
#: lengthened the shot partly held, and one whose two shots were both channelled
#: is inconclusive — filing either as a failure would teach the wrong lesson.
type VersionOutcome = Literal["held", "partly_held", "failed", "inconclusive"]

#: What the experiment log shows for a version, which is the outcome plus the
#: two states that are not an outcome at all. Derived from the row, never
#: stored: `no_prediction` is an empty `prediction`, `open` is a prediction
#: nobody has graded yet, and the other four are the recorded grade.
type OutcomeState = Literal[
    "no_prediction", "open", "held", "partly_held", "failed", "inconclusive"
]

#: The measures the spread is worked out over, and the measures a version's
#: evidence lays side by side.
#:
#: Six because these are the numbers the archive already holds for an ordinary
#: shot — the duration and the final weight are columns, the first drip, the
#: peak pressure and the average brew flow are read out of the diagnostics that
#: were computed at ingest, and the rating is the person's. Nothing here is
#: derived a second time, and a measure whose value a shot does not carry is
#: simply not counted for that shot.
#:
#: The slugs are the field names `domain/spread.py` reads off a counted shot, so
#: a measure added here and forgotten there fails at the model rather than
#: quietly serving nothing.
type SpreadMeasure = Literal[
    "shot_time_s",
    "cup_first_drip_s",
    "first_drip_s",
    "yield_g",
    "peak_pressure_bar",
    "cup_flow_g_s",
    "brew_flow_ml_s",
    "rating",
]

# ── review ───────────────────────────────────────────────────────────

#: The shot styles Review detects from the profile (and, failing that,
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

#: A review row's lifecycle. `interrupted` is what a `running` row becomes at
#: the next boot — the process died mid-call — and it is distinct from `failed`
#: because nothing was learned about the provider.
type ReviewStatus = Literal["running", "ok", "failed", "interrupted"]

#: What one stored statement of a reading is. A `claim` is something the model says about a
#: window of the shot, a `free_text` one answers one of the confirmed signature's free-text
#: expectations, and a `prediction` says how the shot moved against the Set version's
#: prediction. All three are kept or rejected the same way, by a person, one at a time.
type ReviewClaimKind = Literal["claim", "free_text", "prediction"]

#: A claim is kept (`confirmed`) until a person rejects it, and they may restore it. A rejected
#: claim never teaches the chat anything.
type ReviewClaimStatus = Literal["confirmed", "rejected"]

#: How the shot moved against the prediction its Set version was filed with.
type PredictionStance = Literal["as_predicted", "partly", "against", "not_shown"]

#: How much a knowledge rule is worth. `expert` is a published heuristic,
#: `calibrated` is a threshold measured against real shots, `anecdotal` is one
#: person's report, and `learned` is one this archive derived from its own Sets
#: (nothing writes those yet).
type RuleConfidence = Literal["expert", "calibrated", "anecdotal", "learned"]

#: The rule categories, in the order a prompt lists them. The order is
#: load-bearing: rule selection is deterministic (two reviews of the same shot
#: select the same rules), and the
#: sort key is this tuple's index followed by the rule key.
type RuleCategory = Literal[
    "dial_in_order",
    "increments",
    "temperature_by_roast",
    "pressure_matrix",
    "ratio_by_style",
    "time_by_style",
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
FLAVOR_PICK_KINDS: tuple[str, ...] = get_args(FlavorPickKind.__value__)
SET_VERSION_ORIGINS: tuple[str, ...] = get_args(SetVersionOrigin.__value__)
VERSION_OUTCOMES: tuple[str, ...] = get_args(VersionOutcome.__value__)
OUTCOME_STATES: tuple[str, ...] = get_args(OutcomeState.__value__)
#: Typed as the literal rather than as plain strings, unlike its neighbours:
#: these slugs are also model fields and pydantic needs the enum, and one
#: tuple typed twice is how a cast creeps into every caller.
SPREAD_MEASURES: tuple[SpreadMeasure, ...] = get_args(SpreadMeasure.__value__)

#: How each measure is written down. One decimal reads as a shot time or a
#: weight does; bar and ml/s get two, because a tenth of a bar is a real
#: difference and 0.3 bar rounded to one decimal is the whole floor. Here
#: beside the labels and the units rather than in `domain/spread.py`, because
#: this is how a measure is *read* and the front end is served it.
MEASURE_DECIMALS: dict[SpreadMeasure, int] = {
    "shot_time_s": 1,
    "cup_first_drip_s": 1,
    "first_drip_s": 1,
    "yield_g": 1,
    "peak_pressure_bar": 2,
    "cup_flow_g_s": 2,
    "brew_flow_ml_s": 2,
    "rating": 1,
}

#: One decimal finer, and that is the whole reason it exists: a difference and
#: the yardstick it is held against decide a verdict, and at the means' own
#: precision "32.0 minus 30.0 is +2.0, beyond 2.0" reads as a contradiction of
#: itself. Written one place finer, the same row reads "+2.04 against 2.00" and
#: says what the arithmetic actually found. Derived rather than typed out, so a
#: measure cannot end up with a difference coarser than its own mean.
MEASURE_DIFFERENCE_DECIMALS: dict[SpreadMeasure, int] = {
    measure: decimals + 1 for measure, decimals in MEASURE_DECIMALS.items()
}
SHOT_STYLES: tuple[str, ...] = get_args(ShotStyle.__value__)
REVIEW_STATUSES: tuple[str, ...] = get_args(ReviewStatus.__value__)
REVIEW_CLAIM_KINDS: tuple[str, ...] = get_args(ReviewClaimKind.__value__)
REVIEW_CLAIM_STATUSES: tuple[str, ...] = get_args(ReviewClaimStatus.__value__)
PREDICTION_STANCES: tuple[str, ...] = get_args(PredictionStance.__value__)
RULE_CONFIDENCES: tuple[str, ...] = get_args(RuleConfidence.__value__)
RULE_CATEGORIES: tuple[str, ...] = get_args(RuleCategory.__value__)


class FlavorNode(BaseModel):
    """One segment of the flavour wheel: its stored slug, its label, what sits outside it."""

    model_config = ConfigDict(extra="forbid")

    #: The slug path, e.g. ``fruity.berry.blackberry``. What goes in
    #: `shot_judgements.taste_notes_json` and `aroma_notes_json`. The path rather
    #: than the leaf alone because the wheel repeats words across tiers
    #: ("Floral" the category and "Floral" the group; "Bitter" under Chemical is
    #: not the balance's bitter), and a path is what makes "any note under
    #: Sour" a prefix test.
    value: str
    label: str
    children: list[FlavorNode] = Field(default_factory=list)


#: The SCA/WCR Coffee Taster's Flavor Wheel (2016), all three tiers, in the
#: wheel's own clockwise order.
#:
#: Written as labels and turned into slugs by :func:`_slug`, so the list reads
#: like the printed wheel and a slug cannot disagree with its label. Any node at
#: any tier is a note somebody can record: the wheel is read from the centre
#: outwards — "fruity" when unsure, "blackberry" when sure — and a vocabulary
#: that accepted only leaves would make the unsure answer impossible to give.
_WHEEL: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    ("Floral", (("Black tea", ()), ("Floral", ("Chamomile", "Rose", "Jasmine")))),
    (
        "Fruity",
        (
            ("Berry", ("Blackberry", "Raspberry", "Blueberry", "Strawberry")),
            ("Dried fruit", ("Raisin", "Prune")),
            (
                "Other fruit",
                (
                    "Coconut",
                    "Cherry",
                    "Pomegranate",
                    "Pineapple",
                    "Grape",
                    "Apple",
                    "Peach",
                    "Pear",
                ),
            ),
            ("Citrus fruit", ("Grapefruit", "Orange", "Lemon", "Lime")),
        ),
    ),
    (
        "Sour/Fermented",
        (
            (
                "Sour",
                (
                    "Sour aromatics",
                    "Acetic acid",
                    "Butyric acid",
                    "Isovaleric acid",
                    "Citric acid",
                    "Malic acid",
                ),
            ),
            ("Alcohol/Fermented", ("Winey", "Whiskey", "Fermented", "Overripe")),
        ),
    ),
    (
        "Green/Vegetative",
        (
            ("Olive oil", ()),
            ("Raw", ()),
            (
                "Green/Vegetative",
                (
                    "Under-ripe",
                    "Peapod",
                    "Fresh",
                    "Dark green",
                    "Vegetative",
                    "Hay-like",
                    "Herb-like",
                ),
            ),
            ("Beany", ()),
        ),
    ),
    (
        "Other",
        (
            (
                "Papery/Musty",
                (
                    "Stale",
                    "Cardboard",
                    "Papery",
                    "Woody",
                    "Moldy/Damp",
                    "Musty/Dusty",
                    "Musty/Earthy",
                    "Animalic",
                    "Meaty brothy",
                    "Phenolic",
                ),
            ),
            ("Chemical", ("Bitter", "Salty", "Medicinal", "Petroleum", "Skunky", "Rubber")),
        ),
    ),
    (
        "Roasted",
        (
            ("Pipe tobacco", ()),
            ("Tobacco", ()),
            ("Burnt", ("Acrid", "Ashy", "Smoky", "Brown roast")),
            ("Cereal", ("Grain", "Malt")),
        ),
    ),
    (
        "Spices",
        (
            ("Pungent", ()),
            ("Pepper", ()),
            ("Brown spice", ("Anise", "Nutmeg", "Cinnamon", "Clove")),
        ),
    ),
    (
        "Nutty/Cocoa",
        (
            ("Nutty", ("Peanuts", "Hazelnut", "Almond")),
            ("Cocoa", ("Chocolate", "Dark chocolate")),
        ),
    ),
    (
        "Sweet",
        (
            ("Brown sugar", ("Molasses", "Maple syrup", "Caramelized", "Honey")),
            ("Vanilla", ()),
            ("Vanillin", ()),
            ("Overall sweet", ()),
            ("Sweet aromatics", ()),
        ),
    ),
)


def _slug(label: str) -> str:
    """``"Sour/Fermented"`` → ``"sour_fermented"``: lowercase, one ``_`` per run of the rest."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _build_wheel() -> tuple[FlavorNode, ...]:
    nodes: list[FlavorNode] = []
    for category, groups in _WHEEL:
        top = _slug(category)
        children: list[FlavorNode] = []
        for group, leaves in groups:
            middle = f"{top}.{_slug(group)}"
            children.append(
                FlavorNode(
                    value=middle,
                    label=group,
                    children=[
                        FlavorNode(value=f"{middle}.{_slug(leaf)}", label=leaf) for leaf in leaves
                    ],
                )
            )
        nodes.append(FlavorNode(value=top, label=category, children=children))
    return tuple(nodes)


FLAVOR_WHEEL: tuple[FlavorNode, ...] = _build_wheel()


def _walk(nodes: list[FlavorNode] | tuple[FlavorNode, ...]) -> list[FlavorNode]:
    out: list[FlavorNode] = []
    for node in nodes:
        out.append(node)
        out.extend(_walk(node.children))
    return out


#: Every node's slug, every tier, in wheel order (each node before what sits
#: outside it). The validator for both note columns, and the sort key that
#: puts a list of notes in the order a person reads them off the wheel.
FLAVOR_NOTES: tuple[str, ...] = tuple(node.value for node in _walk(FLAVOR_WHEEL))

#: Slug → its own label ("fruity.berry.blackberry" → "Blackberry").
FLAVOR_LABELS: dict[str, str] = {node.value: node.label for node in _walk(FLAVOR_WHEEL)}

_FLAVOR_ORDER: dict[str, int] = {value: index for index, value in enumerate(FLAVOR_NOTES)}


def flavor_ancestors(note: str) -> tuple[str, ...]:
    """The nodes inside ``note`` on the wheel, centre first; empty for a category.

    The slug is the path, so this is string work rather than a tree walk:
    ``"sour_fermented.sour.acetic_acid"`` → ``("sour_fermented",
    "sour_fermented.sour")``.
    """
    parts = note.split(".")
    return tuple(".".join(parts[:depth]) for depth in range(1, len(parts)))


def flavor_path(note: str) -> str:
    """How a person reads a note off the wheel: ``"Fruity › Berry › Blackberry"``."""
    return " › ".join(FLAVOR_LABELS[value] for value in (*flavor_ancestors(note), note))


def in_wheel_order(notes: list[str] | tuple[str, ...]) -> list[str]:
    """Known notes, de-duplicated, in wheel order. Unknown ones are the caller's to refuse."""
    last = len(_FLAVOR_ORDER)
    return sorted(dict.fromkeys(notes), key=lambda note: _FLAVOR_ORDER.get(note, last))


class Term(BaseModel):
    """One member of a simple vocabulary: the stored value and its label."""

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str


class MeasureTerm(BaseModel):
    """One measure the spread is worked out over: its slug, its words, its unit.

    A :class:`Term` with a unit, rather than a label that already contains one:
    the Set page writes "Shot time ±1.8 s" and the evidence table writes
    "held against 2.4 s", and a label of "Shot time (s)" would put the unit in
    the wrong half of both sentences.
    """

    model_config = ConfigDict(extra="forbid")

    value: SpreadMeasure
    label: str
    #: Empty for the rating: it is a number of stars, not a quantity.
    unit: str
    #: How many decimals a mean or a spread of this measure is written with.
    #: Served rather than decided in the front end: the server rounds what it
    #: serves, and a page formatting to its own precision would either invent
    #: digits or hide the one that decided a verdict.
    decimals: int
    #: How many a difference or a yardstick is written with — one finer, for
    #: the reason `MEASURE_DIFFERENCE_DECIMALS` gives. The floor on the spread
    #: line is a yardstick too, and is written the same way.
    difference_decimals: int


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
    #: The four grades a version prediction can be given, which is what the
    #: "record an outcome" control offers.
    version_outcomes: list[Term]
    #: The six states the experiment log renders, the four above plus the two
    #: that are not a grade: `no_prediction` and `open`.
    outcome_states: list[Term]
    #: The measures a Set's spread and a version's evidence are reported per,
    #: with the words and the unit each one is read in. Served for the same
    #: reason as the rest: the Set page renders these labels, and a component
    #: with its own copy would drift from what the server computes.
    spread_measures: list[MeasureTerm]
    #: The flavour wheel, as a tree: nine categories, their groups, their notes.
    flavor_wheel: list[FlavorNode]
    #: The shot styles style detection names, served for the same reason as
    #: the rest: the Knowledge page renders these words, and a component that
    #: typed them would drift from the list the detector returns.
    shot_styles: list[Term]
    rule_categories: list[Term]
    rule_confidences: list[Term]


def _terms(values: tuple[str, ...], labels: dict[str, str] | None = None) -> list[Term]:
    overrides = labels or {}
    return [
        Term(value=value, label=overrides.get(value, value.replace("-", " ").replace("_", " ")))
        for value in values
    ]


#: Labels that are not just the slug with its punctuation softened. Short on
#: purpose: they sit on a shot row and in a panel beside a curve, and the
#: balance's three are the GaggiMate's own words.
_BALANCE_LABELS = {
    "sour": "Sour",
    "balanced": "Balanced",
    "bitter": "Bitter",
}
_DECISION_LABELS = {
    "keep": "Keep",
    "improve": "Improve",
    "discard": "Discard",
}
_OUTCOME_LABELS = {
    "held": "Held",
    "partly_held": "Partly held",
    "failed": "Failed",
    "inconclusive": "Inconclusive",
    "open": "Open",
    "no_prediction": "No prediction",
}
#: How each spread measure reads, and what it is in. Short: they sit at the
#: start of a line the reader finishes with a number.
_MEASURE_LABELS = {
    "shot_time_s": "Shot time",
    "cup_first_drip_s": "First drip",
    "first_drip_s": "First puck flow",
    "yield_g": "Yield",
    "peak_pressure_bar": "Peak pressure",
    "cup_flow_g_s": "Cup flow",
    "brew_flow_ml_s": "Puck flow",
    "rating": "Rating",
}
_MEASURE_UNITS = {
    "shot_time_s": "s",
    "cup_first_drip_s": "s",
    "first_drip_s": "s",
    "yield_g": "g",
    "peak_pressure_bar": "bar",
    "cup_flow_g_s": "g/s",
    "brew_flow_ml_s": "ml/s",
    "rating": "",
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
        version_outcomes=_terms(VERSION_OUTCOMES, _OUTCOME_LABELS),
        outcome_states=_terms(OUTCOME_STATES, _OUTCOME_LABELS),
        spread_measures=[
            MeasureTerm(
                value=measure,
                label=_MEASURE_LABELS[measure],
                unit=_MEASURE_UNITS[measure],
                decimals=MEASURE_DECIMALS[measure],
                difference_decimals=MEASURE_DIFFERENCE_DECIMALS[measure],
            )
            for measure in SPREAD_MEASURES
        ],
        flavor_wheel=[node.model_copy(deep=True) for node in FLAVOR_WHEEL],
        shot_styles=_terms(SHOT_STYLES, _STYLE_LABELS),
        rule_categories=_terms(RULE_CATEGORIES),
        rule_confidences=_terms(RULE_CONFIDENCES, _CONFIDENCE_LABELS),
    )

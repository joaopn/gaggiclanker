"""The closed vocabularies, in one module.

Every one of these appears in three places — a SQL ``CHECK`` constraint, a
pydantic ``Literal`` that becomes an OpenAPI enum, and a control in the front
end — and the only way they stay equal is for two of the three to be generated
from the third. This module is the third.

``GET /api/vocab`` serves the whole of it, so the UI renders a flavour note, a
roast-level select or a decision button from data rather than from a list typed
into a component; the migration's CHECK constraints are written against the same
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
    "ACTIONABLE_VARIABLES",
    "ANALYSIS_STATUSES",
    "BALANCES",
    "BURR_TYPES",
    "DECISIONS",
    "FLAVOR_LABELS",
    "FLAVOR_NOTES",
    "FLAVOR_PICK_KINDS",
    "FLAVOR_WHEEL",
    "OUTCOME_STATES",
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
    "VERSION_OUTCOMES",
    "AnalysisStatus",
    "Balance",
    "BurrType",
    "Decision",
    "FlavorNode",
    "FlavorPickKind",
    "OutcomeState",
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

#: What a suggestion is *about*. The first three are actionable — accepting one
#: writes a new Set version — and the rest are recorded and shown but have
#: nowhere to be applied directly: temperature, pressure, flow and preinfusion
#: are all profile edits, which go through a draft somebody approves, and
#: gaggiclanker writes nothing to the device.
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

#: The variables `POST /api/suggestions/{id}/accept` can actually apply: the
#: three a Set version records. Temperature left this list when it left
#: `set_versions` — the machine brews at the profile's temperature, so applying
#: a temperature suggestion is drafting a profile, not writing a number.
ACTIONABLE_VARIABLES: tuple[str, ...] = ("grind", "dose", "yield")

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
FLAVOR_PICK_KINDS: tuple[str, ...] = get_args(FlavorPickKind.__value__)
SET_VERSION_ORIGINS: tuple[str, ...] = get_args(SetVersionOrigin.__value__)
VERSION_OUTCOMES: tuple[str, ...] = get_args(VersionOutcome.__value__)
OUTCOME_STATES: tuple[str, ...] = get_args(OutcomeState.__value__)
SHOT_STYLES: tuple[str, ...] = get_args(ShotStyle.__value__)
SUGGESTION_VARIABLES: tuple[str, ...] = get_args(SuggestionVariable.__value__)
SUGGESTION_DIRECTIONS: tuple[str, ...] = get_args(SuggestionDirection.__value__)
SUGGESTION_UNITS: tuple[str, ...] = get_args(SuggestionUnit.__value__)
SUGGESTION_STATUSES: tuple[str, ...] = get_args(SuggestionStatus.__value__)
ANALYSIS_STATUSES: tuple[str, ...] = get_args(AnalysisStatus.__value__)
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
    #: The flavour wheel, as a tree: nine categories, their groups, their notes.
    flavor_wheel: list[FlavorNode]
    #: The analyzer's own closed sets, served for the same reason as the
    #: rest: the Knowledge page and the suggestion cards render these words, and
    #: a component that typed them would drift from the CHECK constraint behind
    #: them.
    shot_styles: list[Term]
    suggestion_variables: list[Term]
    #: Which of those variables `POST /api/suggestions/{id}/accept` can apply.
    #: Served because the suggestion card decides whether to offer Accept at
    #: all, and a card that offered it for a variable the server refuses would
    #: turn good advice into a 409. Values rather than terms: the words come
    #: from `suggestion_variables` above, this is only the subset.
    actionable_variables: list[str]
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
        version_outcomes=_terms(VERSION_OUTCOMES, _OUTCOME_LABELS),
        outcome_states=_terms(OUTCOME_STATES, _OUTCOME_LABELS),
        flavor_wheel=[node.model_copy(deep=True) for node in FLAVOR_WHEEL],
        shot_styles=_terms(SHOT_STYLES, _STYLE_LABELS),
        suggestion_variables=_terms(SUGGESTION_VARIABLES, _VARIABLE_LABELS),
        actionable_variables=list(ACTIONABLE_VARIABLES),
        suggestion_directions=_terms(SUGGESTION_DIRECTIONS),
        suggestion_units=_terms(SUGGESTION_UNITS, _UNIT_LABELS),
        suggestion_statuses=_terms(SUGGESTION_STATUSES),
        rule_categories=_terms(RULE_CATEGORIES),
        rule_confidences=_terms(RULE_CONFIDENCES, _CONFIDENCE_LABELS),
    )

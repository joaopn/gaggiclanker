"""What a conversation designing a Recipe is told before it asks anything.

A Recipe being designed has a bean and a grinder and no recipe, so there is no
ledger, no spread and no shot to put in front of the model. What it needs
instead is what the person asked for and what this kitchen already knows about
brewing this coffee on this kit:

* **the Recipe and the brief** — the bean's facts, the grinder and its step unit,
  what the person wants from it and where they usually grind;
* **the profile they asked to fork**, whole, because "like this but with a
  bloom" needs the document it is a variation of — or, when they named none,
  the plain statement that the profile is written from zero;
* **the card on the table** — the initial recipe waiting for the person, or the
  last one they turned down and why;
* **this bean's other Recipes** — how the same coffee went on this and other
  grinders, version by version, with its shots, labels, ratings and outcomes;
* **similar Recipes on this grinder** — the starting point's own evidence, other
  beans only, since this bean's are in the block above;
* **the matching rules**, in the starting point's own selection.

**The profile library is not listed.** The only profile a design starts from
is the one the person picked to fork; listing the library invited the model to
pick one itself, and a design with no fork came out as a copy of whatever was
brewed most. `get_profile` still reads a profile the person asks about.

**The sibling Recipes are a deliberate exception** to "a Recipe's conversation sees
only its Recipe". It holds only while the Recipe is being designed, it is a read-only
snapshot assembled here on the server, and nothing in the design conversation's
tools browses other Recipes. The day version 1 is filled, the next turn is an
ordinary Recipe conversation and this block is gone.

**The same text for the same archive**, as every other context here: no clock,
no incidental query order, every list sorted by something the data states and
cut by a stated rule.
"""

from __future__ import annotations

import json
from typing import Any, get_args

from gaggiclanker.chat.context import (
    _OUTCOMES,
    _cut,
    _plural,
    _quote,
    _recipe,
    _taste,
)
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeanRow, BeansRepository
from gaggiclanker.db.repos.grinders import GrinderRow, GrindersRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow, document_for
from gaggiclanker.db.repos.recipe_proposals import RecipeProposalRow, RecipeProposalsRepository
from gaggiclanker.db.repos.recipes import (
    DesignBrief,
    RecipeRow,
    RecipesRepository,
    RecipeVersionRow,
    VersionLabelCounts,
)
from gaggiclanker.domain.models import (
    PhaseKind,
    ProfileType,
    PumpTarget,
    TargetOperator,
    TargetType,
    TransitionType,
)
from gaggiclanker.knowledge.drinks import drink_lines, drink_reference
from gaggiclanker.knowledge.rules import RecipeContext, render_rules
from gaggiclanker.review.style import detect_style
from gaggiclanker.signatures.lines import signature_answers_block, signature_block
from gaggiclanker.starting.context import (
    planned_style,
    render_similar,
    starting_rules,
)
from gaggiclanker.starting.similar import similar_recipes

__all__ = [
    "SIBLING_RECIPES",
    "SIBLING_VERSIONS",
    "SIMILAR_RECIPES",
    "design_context",
]

#: How many of this bean's other Recipes are written out, newest first. A bean
#: with more than this has a long history, and the newest are the ones that
#: say how it brews now.
SIBLING_RECIPES = 5

#: How many versions of each sibling Recipe, newest first. Enough to see where it
#: ended up and the last few moves that got it there.
SIBLING_VERSIONS = 6

#: How many similar Recipes on this grinder: the same three the starting point
#: shows. This bean's own Recipes are listed in a block of their own, so these are
#: other beans only.
SIMILAR_RECIPES = 3

#: How much of a person's goal and a decline note is quoted.
_GOAL_CHARS = 2000
_NOTE_CHARS = 300


async def design_context(db: Database, recipe_id: int, *, thread_id: int | None = None) -> str:
    """The design brief and the evidence, as markdown, or "" for a Recipe that is gone.

    ``thread_id`` is the conversation, which is told what **it** proposed as signature
    expectations and what the person did with each (and nothing any other conversation did).
    """
    recipes = RecipesRepository(db)
    row = await recipes.get(recipe_id)
    if row is None:
        return ""
    bean = await BeansRepository(db).get(row.bean_id)
    grinder = await GrindersRepository(db).get(row.grinder_id) if row.grinder_id else None

    fork = None
    if row.design_brief.fork_profile_version_id is not None:
        fork = await ProfilesRepository(db).get_version(row.design_brief.fork_profile_version_id)

    similar = await similar_recipes(
        db,
        roast_level=bean.roast_level if bean else None,
        process=bean.process if bean else None,
        origin=(bean.origin or None) if bean else None,
        decaf=bean.decaf if bean else False,
        grinder_id=row.grinder_id,
        exclude_bean_id=row.bean_id,
        limit=SIMILAR_RECIPES,
    )
    style, style_reason = await planned_style(db, similar)
    if fork is not None and fork.profile:
        # The profile they asked to fork is the strongest statement of the kind
        # of shot they are after, stronger than what another bean brewed.
        fork_name = await ProfilesRepository(db).name_of(fork)
        verdict = detect_style(fork.profile, label=fork_name)
        if verdict.style not in ("unknown", "utility"):
            style, style_reason = verdict.style, f"the profile to fork ({fork_name})"
    _signals, rules = await starting_rules(
        db,
        RecipeContext(
            roast_level=bean.roast_level if bean else None,
            process=bean.process if bean else None,
            burr_type=grinder.burr_type if grinder else None,
            decaf=bean.decaf if bean else False,
        ),
        style,
        row.design_brief.drink,
    )

    lines: list[str] = []
    lines += _heading(row, bean, grinder)
    lines += ["", *_brief(row, grinder)]
    block = await drink_reference(db, row.design_brief.drink)
    if block:
        lines += ["", *block.split("\n")]
    lines += [
        "",
        *_fork_block(fork, "" if fork is None else await ProfilesRepository(db).name_of(fork)),
    ]
    if fork is not None and fork.profile:
        # What the profile to fork is for, as it stands in force: the new profile is
        # derived from it, so its intent is the strongest statement of what to keep.
        lines += await signature_block(db, fork.id, None)
    lines += ["", *await _card_block(db, row)]
    signed = await signature_answers_block(db, thread_id)
    if signed:
        lines += ["", *signed]
    lines += ["", *await _siblings_block(db, row)]
    lines += ["", "SIMILAR RECIPES ON THIS GRINDER (other beans)", render_similar(similar)]
    lines += [
        "",
        f"THE RULES THAT MATCH (planned style: {style} — {style_reason})",
        render_rules(rules),
    ]
    lines += [
        "",
        "That is a snapshot, assembled when this turn began. The other Recipes above are "
        "evidence to design from; no tool here reads them further.",
    ]
    return "\n".join(lines)


# ── the Recipe and what was asked for ───────────────────────────────────


def _heading(row: RecipeRow, bean: BeanRow | None, grinder: GrinderRow | None) -> list[str]:
    bean_parts = [
        row.bean_name or "",
        f"roasted by {bean.roaster}" if bean is not None and bean.roaster else "",
        f"{bean.roast_level} roast" if bean is not None and bean.roast_level else "",
        f"{bean.process} process" if bean is not None and bean.process else "",
        f"from {bean.origin}" if bean is not None and bean.origin else "",
        "decaf" if bean is not None and bean.decaf else "",
    ]
    grinder_line = (
        f"{row.grinder_name}"
        + (f" ({grinder.burr_type} burrs)" if grinder.burr_type != "unknown" else "")
        + f", adjusted in {grinder.step_unit}"
        if grinder is not None
        else "not recorded"
    )
    lines = [
        "THIS CONVERSATION IS DESIGNING A NEW RECIPE",
        "",
        f"Recipe {row.id}: {row.name}. "
        f"Bean: {', '.join(part for part in bean_parts if part)}{_taste(bean)}. "
        f"Grinder: {grinder_line}.",
        "Nothing has been brewed on it. Its version 1 is empty: no profile, no grind, "
        "no dose, no yield. What you propose, once the person accepts it, becomes that "
        "version 1.",
    ]
    if bean is not None and bean.description:
        lines.append(f"About the bean: {_cut(bean.description, _NOTE_CHARS)}")
    return lines


def _brief(row: RecipeRow, grinder: GrinderRow | None) -> list[str]:
    brief = row.design_brief
    unit = grinder.step_unit if grinder is not None else "its units"
    return [
        "WHAT THEY ASKED FOR",
        *drink_lines(brief.drink, brief.drink_notes),
        f"Goal: {_quote(_cut(brief.goal, _GOAL_CHARS))}"
        if brief.goal
        else "Goal: not stated. Ask what they want from this Recipe before proposing anything.",
        f"Their usual espresso setting on this grinder: {brief.usual_grind} ({unit})."
        if brief.usual_grind
        else "Their usual espresso setting on this grinder: NOT GIVEN. Without it, or a Recipe "
        "below on this same grinder, you have no anchor on its scale.",
        _chosen(brief, unit),
    ]


def _chosen(brief: DesignBrief, unit: str) -> str:
    """The recipe numbers the person filled in themselves, which the design keeps.

    Said as an instruction and not only as a fact: a number the person typed is
    their decision, and a card that quietly replaced it would be declined for a
    reason the agent could have read here. Named when absent too, so "you
    decide" is as explicit as "keep this".
    """
    parts = [
        f"grind {brief.grind_setting} ({unit})" if brief.grind_setting else "",
        f"dose {brief.dose_g:g} g" if brief.dose_g is not None else "",
        f"target yield {brief.target_yield_g:g} g" if brief.target_yield_g is not None else "",
    ]
    chosen = ", ".join(part for part in parts if part)
    if not chosen:
        return "Numbers they chose: none. The grind, dose and yield are yours to propose."
    return (
        f"Numbers they chose: {chosen}. Propose exactly these in version 1 and design "
        "the profile around them (a yield they chose is where the last phase stops); work out "
        "only what they left open. Change one of them only if they ask you to in this "
        "conversation."
    )


def _fork_block(fork: ProfileVersionRow | None, fork_name: str) -> list[str]:
    """The profile to fork, whole, in the shape the machine stores it."""
    if fork is None or not fork.profile:
        return [
            "THE PROFILE TO FORK",
            "They named none, so the profile is written from zero: a whole document of your "
            "own, built from what they asked for, the bean and the Recipes below. Do not start "
            "from a profile in their library.",
            *_document_shape(),
        ]
    document = document_for(fork, fork_name).to_device()
    return [
        f"THE PROFILE TO FORK: [profile_version {fork.id}] {fork_name}",
        "The new profile is derived from this one and gets a name of its own; this one, and "
        "the Recipe that brews it, stays as it is.",
        "```json",
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False),
        "```",
    ]


def _document_shape() -> list[str]:
    """The keys of a profile document, for a model writing one with nothing to fork.

    Read off the schema's own types, so it cannot drift from what the draft
    validator accepts. Keys and allowed values only, never an example
    document: an example is a profile, and a profile to start from is exactly
    what the person did not name.
    """

    def one_of(kind: Any) -> str:
        return "|".join(get_args(kind))

    return [
        "The document's keys (the schema's, not values to copy):",
        f"- top level: label, type ({one_of(ProfileType)}), description, temperature (°C), "
        "phases (1-10, in order)",
        f"- a phase: name, phase ({one_of(PhaseKind)}), valve (0|1), duration (s), "
        "temperature (0 = the profile's), pump (an integer percent, or "
        f"{{target: {one_of(PumpTarget)}, pressure: bar, flow: ml/s}}), "
        f"targets ([{{type: {one_of(TargetType)}, operator: {one_of(TargetOperator)}, "
        "value}]: OR-combined stop conditions), optionally transition "
        f"({{type: {one_of(TransitionType)}, duration: s, adaptive: bool}})",
    ]


# ── the card ─────────────────────────────────────────────────────────


async def _card_block(db: Database, row: RecipeRow) -> list[str]:
    """The initial recipe waiting for the person, or the last one they answered."""
    proposals = RecipeProposalsRepository(db)
    waiting = await proposals.waiting(row.id)
    if waiting is not None:
        return [
            "A RECIPE IS WAITING FOR THE PERSON",
            f"{await _card_recipe(proposals, waiting)} Reason: {_quote(waiting.reason)}",
            "It has created nothing yet. Talk about this one; a newer propose_initial_recipe "
            "replaces it.",
        ]
    last = await proposals.last_decided(row.id)
    if last is None or last.status != "declined":
        return ["THE RECIPE", "Nothing has been proposed to the person yet."]
    note = (
        f' They said: "{_cut(last.decline_note.strip(), _NOTE_CHARS)}"'
        if last.decline_note.strip()
        else ""
    )
    return [
        "THE LAST RECIPE PROPOSED",
        f"{await _card_recipe(proposals, last)} Reason: {_quote(last.reason)}",
        f"They declined it.{note} Take that into the next proposal rather than sending it again.",
    ]


async def _card_recipe(proposals: RecipeProposalsRepository, card: RecipeProposalRow) -> str:
    preview = await proposals.preview(card)
    if preview is None:  # pragma: no cover - only a hand-damaged row has no preview
        return "Its version cannot be read."
    return f"Version 1: {_recipe(preview)} (profile draft #{card.draft_id})."


# ── this bean's other Recipes ───────────────────────────────────────────


async def _siblings_block(db: Database, row: RecipeRow) -> list[str]:
    """This bean's other Recipes that are still in use, newest first, capped."""
    recipes = RecipesRepository(db)
    siblings = sorted(
        (
            other
            for other in await recipes.list_recipes()
            if other.bean_id == row.bean_id and other.id != row.id
        ),
        key=lambda other: other.id,
        reverse=True,
    )
    lines = ["THIS BEAN'S OTHER RECIPES (newest first)"]
    if not siblings:
        lines.append("- none: this is the first Recipe of this bean.")
        return lines
    for other in siblings[:SIBLING_RECIPES]:
        lines += await _sibling(recipes, other)
    if len(siblings) > SIBLING_RECIPES:
        lines.append(f"- and {len(siblings) - SIBLING_RECIPES} older Recipes of this bean.")
    return lines


async def _sibling(recipes: RecipesRepository, other: RecipeRow) -> list[str]:
    grinder = other.grinder_name or "no grinder recorded"
    head = f"- Recipe {other.id}: {other.name} — on {grinder}, {_plural(other.shot_count, 'shot')}"
    if other.designing:
        return [f"{head}; being designed, no version yet."]
    versions = await recipes.versions(other.id)
    labels = await recipes.label_counts(other.id)
    ratings = {
        summary.recipe_version_id: summary.avg_rating
        for summary in (await recipes.trends(other.id)).versions
    }
    lines = [f"{head}."]
    for version in versions[:SIBLING_VERSIONS]:
        lines.append(f"  - {_sibling_version(version, labels.get(version.id), ratings)}")
    if len(versions) > SIBLING_VERSIONS:
        lines.append(f"  - and {len(versions) - SIBLING_VERSIONS} older versions.")
    return lines


def _sibling_version(
    version: RecipeVersionRow, counts: VersionLabelCounts | None, ratings: dict[int, float | None]
) -> str:
    shots = _plural(version.shot_count, "shot")
    if counts is not None and version.shot_count:
        shots += (
            f" ({counts.good} Good, {counts.neutral} Neutral, {counts.bad} Bad, "
            f"{counts.discarded} Discarded, {counts.unrated} unrated)"
        )
    rating = ratings.get(version.id)
    parts = [
        f"{version.version_label}: {_recipe(version)}",
        shots,
        f"mean rating {rating:.1f}" if rating is not None else "no rating",
        f"outcome {_OUTCOMES[version.outcome_state].split(' —')[0]}",
    ]
    if version.intent:
        parts.append(f"trying: {_cut(version.intent, _NOTE_CHARS)}")
    return " · ".join(parts)

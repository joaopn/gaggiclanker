"""What a conversation designing a Set is told before it asks anything.

A Set being designed has a bean and a grinder and no recipe, so there is no
ledger, no spread and no shot to put in front of the model. What it needs
instead is what the person asked for and what this kitchen already knows about
brewing this coffee on this kit:

* **the Set and the brief** — the bean's facts, the grinder and its step unit,
  what the person wants from it and where they usually grind;
* **the profile they asked to fork**, whole, because "like this but with a
  bloom" needs the document it is a variation of;
* **the card on the table** — the initial recipe waiting for the person, or the
  last one they turned down and why;
* **this bean's other Sets** — how the same coffee went on this and other
  grinders, version by version, with its shots, labels, ratings and outcomes;
* **similar Sets on this grinder** — the starting point's own evidence, other
  beans only, since this bean's are in the block above;
* **the profile library and the matching rules**, in the starting point's own
  shape and selection.

**The sibling Sets are a deliberate exception** to "a Set's conversation sees
only its Set". It holds only while the Set is being designed, it is a read-only
snapshot assembled here on the server, and nothing in the design conversation's
tools browses other Sets. The day version 1 is filled, the next turn is an
ordinary Set conversation and this block is gone.

**The same text for the same archive**, as every other context here: no clock,
no incidental query order, every list sorted by something the data states and
cut by a stated rule.
"""

from __future__ import annotations

import json

from gaggiclanker.analyzer.style import detect_style
from gaggiclanker.chat.context import _OUTCOMES, _cut, _plural, _quote, _recipe
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeanRow, BeansRepository
from gaggiclanker.db.repos.grinders import GrinderRow, GrindersRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow
from gaggiclanker.db.repos.set_proposals import SetProposalRow, SetProposalsRepository
from gaggiclanker.db.repos.sets import SetRow, SetsRepository, SetVersionRow, VersionLabelCounts
from gaggiclanker.domain.models import Profile
from gaggiclanker.knowledge.rules import SetContext, render_rules
from gaggiclanker.starting.context import (
    planned_style,
    profile_candidates,
    render_profiles,
    render_similar,
    starting_rules,
)
from gaggiclanker.starting.similar import similar_sets

__all__ = [
    "SIBLING_SETS",
    "SIBLING_VERSIONS",
    "SIMILAR_SETS",
    "design_context",
]

#: How many of this bean's other Sets are written out, newest first. A bean
#: with more than this has a long history, and the newest are the ones that
#: say how it brews now.
SIBLING_SETS = 5

#: How many versions of each sibling Set, newest first. Enough to see where it
#: ended up and the last few moves that got it there.
SIBLING_VERSIONS = 6

#: How many similar Sets on this grinder: the same three the starting point
#: shows. This bean's own Sets are listed in a block of their own, so these are
#: other beans only.
SIMILAR_SETS = 3

#: How much of a person's goal and a decline note is quoted.
_GOAL_CHARS = 2000
_NOTE_CHARS = 300


async def design_context(db: Database, set_id: int) -> str:
    """The design brief and the evidence, as markdown, or "" for a Set that is gone."""
    sets = SetsRepository(db)
    row = await sets.get(set_id)
    if row is None:
        return ""
    bean = await BeansRepository(db).get(row.bean_id)
    grinder = await GrindersRepository(db).get(row.grinder_id) if row.grinder_id else None

    fork = None
    if row.design_brief.fork_profile_version_id is not None:
        fork = await ProfilesRepository(db).get_version(row.design_brief.fork_profile_version_id)

    similar = await similar_sets(
        db,
        roast_level=bean.roast_level if bean else None,
        process=bean.process if bean else None,
        origin=(bean.origin or None) if bean else None,
        decaf=bean.decaf if bean else False,
        grinder_id=row.grinder_id,
        exclude_bean_id=row.bean_id,
        limit=SIMILAR_SETS,
    )
    style, style_reason = await planned_style(db, similar)
    if fork is not None and fork.profile:
        # The profile they asked to fork is the strongest statement of the kind
        # of shot they are after, stronger than what another bean brewed.
        verdict = detect_style(fork.profile)
        if verdict.style not in ("unknown", "utility"):
            style, style_reason = verdict.style, f"the profile to fork ({fork.label})"
    _signals, rules = await starting_rules(
        db,
        SetContext(
            roast_level=bean.roast_level if bean else None,
            process=bean.process if bean else None,
            burr_type=grinder.burr_type if grinder else None,
            decaf=bean.decaf if bean else False,
        ),
        style,
    )

    lines: list[str] = []
    lines += _heading(row, bean, grinder)
    lines += ["", *_brief(row, grinder)]
    lines += ["", *_fork_block(fork)]
    lines += ["", *await _card_block(db, row)]
    lines += ["", *await _siblings_block(db, row)]
    lines += ["", "SIMILAR SETS ON THIS GRINDER (other beans)", render_similar(similar)]
    lines += ["", "THE PROFILE LIBRARY", render_profiles(await profile_candidates(db))]
    lines += [
        "",
        f"THE RULES THAT MATCH (planned style: {style} — {style_reason})",
        render_rules(rules),
    ]
    lines += [
        "",
        "That is a snapshot, assembled when this turn began. The other Sets above are "
        "evidence to design from; no tool here reads them further.",
    ]
    return "\n".join(lines)


# ── the Set and what was asked for ───────────────────────────────────


def _heading(row: SetRow, bean: BeanRow | None, grinder: GrinderRow | None) -> list[str]:
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
        "THIS CONVERSATION IS DESIGNING A NEW SET",
        "",
        f"Set {row.id}: {row.name}. Bean: {', '.join(part for part in bean_parts if part)}. "
        f"Grinder: {grinder_line}.",
        "Nothing has been brewed on it. Its version 1 has no recipe yet: no profile, no grind, "
        "no dose, no yield. What you propose, once the person accepts it, becomes that "
        "version 1.",
    ]
    if bean is not None and bean.description:
        lines.append(f"About the bean: {_cut(bean.description, _NOTE_CHARS)}")
    return lines


def _brief(row: SetRow, grinder: GrinderRow | None) -> list[str]:
    brief = row.design_brief
    unit = grinder.step_unit if grinder is not None else "its units"
    return [
        "WHAT THEY ASKED FOR",
        f"Goal: {_quote(_cut(brief.goal, _GOAL_CHARS))}"
        if brief.goal
        else "Goal: not stated. Ask what they want from this Set before proposing anything.",
        f"Their usual espresso setting on this grinder: {brief.usual_grind} ({unit})."
        if brief.usual_grind
        else "Their usual espresso setting on this grinder: NOT GIVEN. Without it, or a Set "
        "below on this same grinder, you have no anchor on its scale.",
    ]


def _fork_block(fork: ProfileVersionRow | None) -> list[str]:
    """The profile to fork, whole, in the shape the machine stores it."""
    if fork is None or not fork.profile:
        return [
            "THE PROFILE TO FORK",
            "They named none. Start from a profile in the library below, or author one.",
        ]
    document = Profile.model_validate(fork.profile).to_device()
    return [
        f"THE PROFILE TO FORK: [profile_version {fork.id}] {fork.label}",
        "The new profile is derived from this one and gets a name of its own; this one, and "
        "every Set that brews it, stays as it is.",
        "```json",
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False),
        "```",
    ]


# ── the card ─────────────────────────────────────────────────────────


async def _card_block(db: Database, row: SetRow) -> list[str]:
    """The initial recipe waiting for the person, or the last one they answered."""
    proposals = SetProposalsRepository(db)
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


async def _card_recipe(proposals: SetProposalsRepository, card: SetProposalRow) -> str:
    preview = await proposals.preview(card)
    if preview is None:  # pragma: no cover - only a hand-damaged row has no preview
        return "Its recipe cannot be read."
    return f"Recipe: {_recipe(preview)} (profile draft #{card.draft_id})."


# ── this bean's other Sets ───────────────────────────────────────────


async def _siblings_block(db: Database, row: SetRow) -> list[str]:
    """This bean's other Sets that are still in use, newest first, capped."""
    sets = SetsRepository(db)
    siblings = sorted(
        (
            other
            for other in await sets.list_sets()
            if other.bean_id == row.bean_id and other.id != row.id
        ),
        key=lambda other: other.id,
        reverse=True,
    )
    lines = ["THIS BEAN'S OTHER SETS (newest first)"]
    if not siblings:
        lines.append("- none: this is the first Set of this bean.")
        return lines
    for other in siblings[:SIBLING_SETS]:
        lines += await _sibling(sets, other)
    if len(siblings) > SIBLING_SETS:
        lines.append(f"- and {len(siblings) - SIBLING_SETS} older Sets of this bean.")
    return lines


async def _sibling(sets: SetsRepository, other: SetRow) -> list[str]:
    grinder = other.grinder_name or "no grinder recorded"
    head = f"- Set {other.id}: {other.name} — on {grinder}, {_plural(other.shot_count, 'shot')}"
    if other.designing:
        return [f"{head}; being designed, no recipe yet."]
    versions = await sets.versions(other.id)
    labels = await sets.label_counts(other.id)
    ratings = {
        summary.set_version_id: summary.avg_rating
        for summary in (await sets.trends(other.id)).versions
    }
    lines = [f"{head}."]
    for version in versions[:SIBLING_VERSIONS]:
        lines.append(f"  - {_sibling_version(version, labels.get(version.id), ratings)}")
    if len(versions) > SIBLING_VERSIONS:
        lines.append(f"  - and {len(versions) - SIBLING_VERSIONS} older versions.")
    return lines


def _sibling_version(
    version: SetVersionRow, counts: VersionLabelCounts | None, ratings: dict[int, float | None]
) -> str:
    shots = _plural(version.shot_count, "shot")
    if counts is not None and version.shot_count:
        shots += (
            f" ({counts.keep} Keep, {counts.improve} Improve, {counts.discard} Discard, "
            f"{counts.unlabelled} unlabelled)"
        )
    rating = ratings.get(version.id)
    parts = [
        f"v{version.version_no}: {_recipe(version)}",
        shots,
        f"mean rating {rating:.1f}" if rating is not None else "no rating",
        f"outcome {_OUTCOMES[version.outcome_state].split(' —')[0]}",
    ]
    if version.intent:
        parts.append(f"trying: {_cut(version.intent, _NOTE_CHARS)}")
    return " · ".join(parts)

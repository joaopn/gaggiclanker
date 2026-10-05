"""What the model is told before it asks anything: the experiment so far.

A Set conversation is about **one version** of one Set — the change being
argued — and this module writes down everything the archive already knows about
that change before a word is typed: the Set and the recipe, the whole prediction
ledger with its outcomes, the evidence table this version's prediction is graded
on, the spread that decides what counts as a difference, this version's newest
shots in their base information, and what a good shot of this coffee has looked
like.

Three properties are load-bearing.

**It is the same text for the same archive.** Nothing here reads the clock,
iterates a set or depends on a query's incidental order: every list is sorted by
something the data states, every number is rounded at the precision the
vocabulary gives the measure, and the budget below drops versions by a rule
rather than by whatever fitted. A grade that can be traced needs a context that
can be reproduced.

**It is reused, not recomputed.** The ledger, the dead ends, the track record,
the spread and the evidence come from the same repository methods and the same
pure functions the Set page is served from, so the agent and the page can never
be shown two different accounts of the same experiment.

**Nothing unconfirmed goes in.** Tier 3's rule is that an insight reaches no
prompt until a person has confirmed it, and the chat is a prompt like any other
— a model that proposes an insight and is then handed it back next turn has
manufactured its own evidence.

A general conversation gets no block at all: it is about the archive, and there
is no one experiment to put in front of it. A conversation about a Set that is
still being designed gets the design brief instead (`chat/design_context.py`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeanRow, BeansRepository, taste_scales
from gaggiclanker.db.repos.grinders import GrindersRepository
from gaggiclanker.db.repos.insight_deletions import InsightDeletionsRepository
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.db.repos.outcome_proposals import OutcomeProposalRow, OutcomeProposalsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, stored_document_json
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    SetRow,
    SetsRepository,
    SetTrendVersion,
    SetVersionRow,
    dead_end_ids,
    track_record,
    version_changes,
)
from gaggiclanker.domain.spread import (
    MEASURE_FLOORS,
    MeasureEvidence,
    Spread,
    VersionEvidence,
    pooled_spreads,
    version_evidence,
)
from gaggiclanker.domain.vocab import SPREAD_MEASURES, MeasureTerm, SpreadMeasure, vocabulary
from gaggiclanker.shotinfo.catalogue import Tier, effective_tiers
from gaggiclanker.shotinfo.downsample import CURVE_POINTS
from gaggiclanker.shotinfo.render import load_shots, needs_samples, render_shot
from gaggiclanker.tools.scope import ToolScope

__all__ = [
    "INSIGHTS_SHOWN",
    "INSIGHT_CHARS",
    "LEDGER_VERSIONS",
    "NOTE_CHARS",
    "RECENT_SHOTS",
    "opening_context",
    "thread_title_from",
]

#: How many versions the ledger writes out in full. A Set that has run past this
#: is summarised from the oldest end — see :func:`_ledger` for the rule and for
#: the two versions it never drops.
LEDGER_VERSIONS = 12

#: How many of this version's newest shots are written out when the caller
#: does not say: the `chatRecentShots` setting's default, which is what the
#: runner passes. The rest are a search away.
RECENT_SHOTS = 20

#: How many confirmed insights are written out, and how long each may be. The
#: only section whose size follows the kitchen rather than the experiment, so it
#: is the only one that needs a cap of its own; the rest are a `get_insights`
#: call away.
INSIGHTS_SHOWN = 20
INSIGHT_CHARS = 300

#: How much of a written note is quoted, on a shot line and on a ledger line
#: alike. One sentence or two, which is what a note is.
NOTE_CHARS = 160

#: The words, units and precision each measure is read in, from the vocabulary
#: the Set page is served — so "±2.0 s" in the chat and "±2.0 s" on the page are
#: the same sentence. Built once: the vocabulary is pure and this is a lookup.
_MEASURES: dict[SpreadMeasure, MeasureTerm] = {
    term.value: term for term in vocabulary().spread_measures
}

#: How an outcome reads in a sentence. The vocabulary's own labels are title
#: case for a badge ("Partly held"); these are the same words mid-line.
_OUTCOMES: dict[str, str] = {
    "no_prediction": "no prediction",
    "open": "open — nobody has graded it yet",
    "held": "held",
    "partly_held": "partly held",
    "failed": "failed",
    "inconclusive": "inconclusive",
}


def _taste(bean: BeanRow | None) -> str:
    """The heading's taste clause ("; taste acidity 4 (1 low to 5 high)"), or ""."""
    phrase = taste_scales(bean)
    return f"; taste {phrase}" if phrase else ""


def _plural(count: int, noun: str) -> str:
    """ "1 shot", "2 shots". Written out because the model reads these lines."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


async def opening_context(
    db: Database,
    scope: ToolScope,
    *,
    recent_shots: int = RECENT_SHOTS,
    tiers: Mapping[str, Tier] | None = None,
    curve_points: int = CURVE_POINTS,
    thread_id: int | None = None,
) -> str:
    """The experiment so far, as markdown, or an empty string for a general chat.

    ``scope.set_version_id`` is the version being argued. A scope that names a
    Set and no version falls back to the current one rather than refusing: a
    thread always stores both, and a caller assembling a context by hand (a
    test, a script) should get the obvious answer.

    ``recent_shots`` is how many of the version's newest shots are written
    out, ``tiers`` which of their items are base and ``curve_points`` how far a
    curve moved into base is cut; the runner reads all three at the start of
    the turn, so the context and the tools of one turn agree.
    """
    if scope.kind != "set" or scope.set_id is None:
        return ""
    if scope.designing:
        # A Set being designed has no experiment yet: its conversation is told
        # the brief and the evidence to design from instead. Imported here
        # because that module renders with this one's helpers.
        from gaggiclanker.chat.design_context import design_context

        return await design_context(db, scope.set_id)
    sets = SetsRepository(db)
    row = await sets.get(scope.set_id)
    if row is None:
        return ""
    versions = await sets.versions(scope.set_id)
    version = _this_version(versions, scope.set_version_id, row.current_version_id)
    if version is None:
        return ""
    current = next((item for item in versions if item.is_current), version)

    by_id = {item.id: item for item in versions}
    dead_ends = dead_end_ids(versions, row.current_version_id)
    labels = await sets.label_counts(scope.set_id)
    # One pass over the counted shots feeds the spread and the evidence, exactly
    # as the Set page does it: two passes could answer with two different sets
    # of shots if one landed between them.
    counted = await sets.counted_shots(scope.set_id)
    spreads = pooled_spreads(counted)
    compared = by_id.get(version.compares_to_version_id or 0)
    evidence = (
        version_evidence(
            counted,
            spreads,
            version_id=version.id,
            version_label=version.version_label,
            compares_to_version_id=version.compares_to_version_id,
            compares_to_version_label=version.compares_to_version_label,
        )
        if version.prediction
        else None
    )

    profile_labels = _profile_labels(versions)
    # The page's own bars, from the one function `get_set`'s trajectory is served by.
    # Handed the counted shots read above, so the spread, the evidence and the means are
    # one pass over one list.
    averages = {
        item.set_version_id: item
        for item in (await sets.trends(scope.set_id, counted=counted)).versions
    }
    lines: list[str] = []
    # First, and nothing that moves between turns before it: see `_profile_block`.
    lines += await _profile_block(db, version, compared)
    lines += [""]
    lines += await _heading(db, row, version, versions, by_id, dead_ends)
    lines += await _reverts_block(sets, version)
    lines += ["", *await _proposal_block(db, scope.set_id, profile_labels, current)]
    grade = await _grade_block(db, scope.set_id, version)
    if grade:
        lines += ["", *grade]
    lines += ["", *_ledger(versions, dead_ends, labels, version, by_id, profile_labels, averages)]
    lines += ["", *_spread_block(spreads)]
    if evidence is not None:
        lines += ["", *_evidence_block(evidence, version, compared)]
    lines += [
        "",
        *await _shots_block(
            db,
            scope.set_id,
            version,
            recent=max(1, recent_shots),
            tiers=tiers if tiers is not None else await effective_tiers(db),
            curve_points=curve_points,
        ),
    ]
    lines += ["", *_gold_standard(counted, versions, dead_ends)]
    lines += ["", *await _insights_block(db, row)]
    proposed = await _proposed_insights_block(db, thread_id)
    if proposed:
        lines += ["", *proposed]
    deletions = await _proposed_deletions_block(db, thread_id)
    if deletions:
        lines += ["", *deletions]
    lines += [
        "",
        "Those facts are the record, not the whole archive: use the tools for anything else, "
        "and for anything you are about to quote a number from.",
    ]
    return "\n".join(lines)


def _this_version(
    versions: Sequence[SetVersionRow], version_id: int | None, current_id: int | None
) -> SetVersionRow | None:
    """The version the conversation is about; the Set's current one when unsaid."""
    wanted = version_id if version_id is not None else current_id
    return next((version for version in versions if version.id == wanted), None)


# ── the Set and this version ─────────────────────────────────────────


async def _heading(
    db: Database,
    row: SetRow,
    version: SetVersionRow,
    versions: Sequence[SetVersionRow],
    by_id: dict[int, SetVersionRow],
    dead_ends: set[int],
) -> list[str]:
    bean = await BeansRepository(db).get(row.bean_id) if row.bean_id else None
    grinder = await GrindersRepository(db).get(row.grinder_id) if row.grinder_id else None
    bean_line = ", ".join(
        part
        for part in (
            row.bean_name,
            f"{bean.roast_level} roast" if bean is not None and bean.roast_level else "",
            f"{bean.process} process" if bean is not None and bean.process else "",
        )
        if part
    ) + _taste(bean)
    grinder_line = (
        f"{row.grinder_name}, adjusted in {grinder.step_unit}"
        if grinder is not None and grinder.step_unit
        else (row.grinder_name or "")
    )
    parent = by_id.get(version.parent_version_id or 0)
    changes = version_changes(version, parent, _profile_labels(versions))

    lines = [
        "THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET",
        "",
        f"Set {row.id}: {row.name}."
        + (f" Bean: {bean_line}." if bean_line else "")
        + (f" Grinder: {grinder_line}." if grinder_line else ""),
        f"{_plural(row.version_count, 'version')}, {_plural(row.shot_count, 'shot')}. "
        + ("Archived." if row.archived else "In use.")
        + (" New shots on its profile are filed here." if row.automatch else ""),
        "",
        f"THIS VERSION IS {version.version_label}"
        + (" (a dead end: the Set went back past it)" if version.id in dead_ends else "")
        + (" — the current version of this Set" if version.is_current else ""),
        f"Recipe: {_recipe(version)}.",
        f"Changed against {parent.version_label}: {_changes(changes)}."
        if parent is not None
        else "It is the Set's first version: a baseline, not a change to anything.",
    ]
    if version.intent:
        lines.append(f"What you are trying: {version.intent}")
    lines.append(_prediction_line(version))
    return lines


async def _reverts_block(sets: SetsRepository, version: SetVersionRow) -> list[str]:
    """Every time the Set went back to this version, oldest first, and when.

    A revert reopens this version's conversation as the Set's live one, and the
    agent must not carry on as if nothing had happened in between: the Set was on
    other versions, and what was said above may predate them. Every revert into
    this version is told, not only the latest or the ones since the last message,
    so the lines depend on the archive alone and the context stays byte-stable.
    """
    reverts = await sets.reverts_into(version.id)
    if not reverts:
        return []
    lines = [""]
    for revert in reverts:
        when = revert.created_at[:16].replace("T", " ") + " UTC"
        note = f" Why: {revert.note}" if revert.note else ""
        lines.append(
            f"The Set went back to {revert.to_version_label} from "
            f"{revert.from_version_label} on {when}.{note}"
        )
    lines.append(
        "What was said in this conversation before then was said while the Set was on this "
        "version; the versions it went back past are dead ends now."
    )
    return lines


async def _profile_block(
    db: Database, version: SetVersionRow, compared: SetVersionRow | None
) -> list[str]:
    """The profile this version brews, in full, and the compared version's when it differs.

    The stored canonical document, compact JSON with no renderer: what a phase
    commands (its pump target, the transition, the duration, the stop conditions)
    is what the shot's per-phase lines are read against, and a summary of it would
    be one more thing to disagree with the machine. It is the document `get_profile`
    serves, so that tool is for the *other* profiles.

    It is the **very first thing** in the context, because the front of the prompt
    is what the provider's cache can reuse and everything after the first changed
    byte is paid for again. For one conversation's version this block never
    changes: the version's own profile and the version it is compared against are
    fixed when the version is made, and the label in the heading is its name. Every
    other part moves, the Set's shot count and version count, the outcome, a waiting
    proposal or grade, a revert, the ledger's means, the shots, so none of them may
    come before it.

    The compared version's profile follows only when it is a different profile
    version: on the same one the first block already is its document, and a second
    copy would be the same bytes twice. A version that names no profile says so
    instead of leaving the heading out.
    """
    own = await _profile_text(db, version.profile_version_id, version.profile_label)
    lines = [f"THE PROFILE {version.version_label} BREWS", *own]
    if (
        compared is not None
        and compared.profile_version_id is not None
        and compared.profile_version_id != version.profile_version_id
    ):
        lines += [
            "",
            f"THE PROFILE {compared.version_label} BREWS "
            f"({version.version_label} is compared against it; a different profile version)",
            *await _profile_text(db, compared.profile_version_id, compared.profile_label),
        ]
    return lines


async def _profile_text(
    db: Database, profile_version_id: int | None, label: str | None
) -> list[str]:
    if profile_version_id is None:
        return [
            "This version names no profile, so no shot is filed under it automatically: "
            "only a person files one here, whatever profile it was pulled on."
        ]
    stored = await ProfilesRepository(db).get_version(profile_version_id)
    if stored is None or not stored.profile:
        return [
            f"Profile version {profile_version_id} ({label or 'unlabelled'}) has no stored "
            "document in the archive."
        ]
    return [
        f"Profile version {stored.id}, {stored.label}, as stored (compact JSON, every field):",
        stored_document_json(stored),
    ]


def _profile_labels(versions: Sequence[SetVersionRow]) -> dict[int, str]:
    return {
        version.profile_version_id: version.profile_label
        for version in versions
        if version.profile_version_id is not None and version.profile_label
    }


def _changes(changes: Sequence[Any]) -> str:
    if not changes:
        return "nothing in the recipe — only the intent"
    return "; ".join(
        f"{change.label} {change.before or 'not set'} → {change.after or 'cleared'}"
        + (" (it came with the profile)" if change.from_profile else "")
        for change in changes
    )


def _prediction_line(version: SetVersionRow) -> str:
    if not version.prediction:
        return "Prediction: none was written for this version, so there is nothing to grade."
    against = (
        f" (compared to {version.compares_to_version_label})"
        if version.compares_to_version_label
        else " (compared to nothing: grade it on the numbers it states)"
    )
    note = f" Note: {version.outcome_note}" if version.outcome_note else ""
    return (
        f"Prediction{against}: {version.prediction}\n"
        f"Outcome: {_OUTCOMES[version.outcome_state]}.{note}"
    )


def _recipe(version: SetVersionRow) -> str:
    """One line of numbers, omitting what the version does not state.

    A blank where a dose should be is a fact — this Set has never recorded one —
    and writing "dose: none" invites the model to treat it as a measurement.
    """
    parts: list[str] = []
    if version.grind_setting:
        parts.append(f"grind {version.grind_setting}")
    if version.dose_g is not None:
        parts.append(f"{version.dose_g:g} g in")
    if version.target_yield_g is not None:
        parts.append(f"{version.target_yield_g:g} g out")
    # Attributed, because it is the one number here nobody typed on the Set: the
    # machine brews at the profile's temperature, and a model that reads it as a
    # Set field would propose changing a field that does not exist.
    if version.profile_temperature_c is not None:
        parts.append(f"{version.profile_temperature_c:g} °C from its profile")
    if version.profile_label:
        parts.append(f"profile {version.profile_label}")
    return ", ".join(parts) if parts else "(nothing recorded)"


# ── the proposal ─────────────────────────────────────────────────────


async def _proposal_block(
    db: Database, set_id: int, profile_labels: dict[int, str], current: SetVersionRow
) -> list[str]:
    """What has been proposed to the person, and what they did about it.

    Right after this version's own block, because it is the other half of "where
    does this experiment stand": a change waiting for an answer is the reason
    not to propose another one, and a change the person turned down last week is
    the reason not to send it again.

    Always rendered, even when there is nothing, for the reason every other
    block here is: a section that appears and disappears makes two archives that
    differ in one row read as two different documents.
    """
    proposals = SetProposalsRepository(db)
    waiting = await proposals.waiting(set_id)
    if waiting is not None:
        return [
            "A PROPOSAL IS WAITING FOR THE PERSON",
            f"It changes {await _proposal_change(proposals, waiting, profile_labels)}. "
            f"Reason: {_quote(waiting.reason)} {_proposal_prediction(waiting)}",
            "It has changed nothing: the Set is still on "
            f"{waiting.base_version_label} until they accept it. Talk about this one — do not "
            "propose another change while it waits."
            + (
                f" You suggested recording it as a major version: {_quote(waiting.major_reason)}"
                " They decide on the card."
                if waiting.suggest_major
                else ""
            ),
        ]
    last = await proposals.last_decided(set_id)
    if last is None:
        return [
            "PROPOSALS",
            "Nothing has been proposed to the person on this Set yet.",
        ]
    if last.kind == "design":
        return ["THE LAST PROPOSAL", await _design_line(proposals, last)]
    return [
        "THE LAST PROPOSAL",
        f"It proposed changing {await _proposal_change(proposals, last, profile_labels)}. "
        f"Reason: {_quote(last.reason)} {_proposal_prediction(last)}",
        _decided_line(last, current),
    ]


async def _design_line(proposals: SetProposalsRepository, row: Any) -> str:
    """The initial recipe this Set was designed with, once the design is over.

    Said as a recipe, not as a change: it was the whole first recipe, and a
    list of every field going from nothing to a value says less than the
    recipe does. It carried no prediction, because a version 1 is a baseline.
    """
    preview = await proposals.preview(row)
    recipe = _recipe(preview) if preview is not None else "(its recipe cannot be read)"
    if row.status == "accepted":
        return (
            f"This Set was designed in conversation, and its initial recipe was accepted as "
            f"{row.resulting_version_label}: {recipe}. Reason: {_quote(row.reason)} It is the "
            "baseline; every change since is in the ledger below."
        )
    return (
        f"The last initial recipe proposed for this Set was {row.status} before anything was "
        f"brewed: {recipe}. The Set's recipe was written another way."
    )


def _quote(text: str) -> str:
    """A person's or an agent's own sentence, quoted and punctuated once.

    These are written by somebody and usually end in a full stop already;
    ``"…finish.".`` is the kind of detail a reader stops on, so the closing
    punctuation goes inside the quotation and is not added twice.
    """
    written = text.strip()
    return f'"{written}"' if written.endswith((".", "!", "?")) else f'"{written}."'


async def _proposal_change(
    proposals: SetProposalsRepository, row: Any, profile_labels: dict[int, str]
) -> str:
    """The proposed change as the log renders a version's own, or the bare names.

    Through the same renderer the experiment log uses, so a change reads the
    same before and after the person answers. When the version it was made
    against has gone, the named groups are what is left to say.
    """
    base = await proposals.sets.get_version(row.base_version_id)
    preview = await proposals.preview(row)
    if base is None or preview is None:  # pragma: no cover - the base is a reference
        return ", ".join(row.changed) or "nothing"
    labels = {
        **profile_labels,
        **{
            version.profile_version_id: version.profile_label
            for version in (base, preview)
            if version.profile_version_id is not None and version.profile_label
        },
    }
    return _changes(version_changes(preview, base, labels))


def _proposal_prediction(row: Any) -> str:
    against = (
        f" (compared to {row.compares_to_version_label})"
        if row.compares_to_version_label
        else " (compared to nothing)"
    )
    combined = (
        f" Two things move together because: {_quote(row.combined_reason)} So whatever "
        "happens, the prediction cannot say which of them did it."
        if row.combined_reason
        else ""
    )
    return f"Prediction{against}: {_quote(row.prediction)}{combined}"


def _decided_line(row: Any, current: SetVersionRow) -> str:
    """How it was answered, in the words that are useful next time."""
    if row.status == "accepted":
        return (
            f"They accepted it; it is {row.resulting_version_label}. Its own prediction is in the "
            "ledger below."
        )
    if row.status == "declined":
        note = (
            f' They said: "{_cut(row.decline_note.strip(), NOTE_CHARS)}"'
            if row.decline_note
            else ""
        )
        return (
            f"They declined it.{note} A declined proposal is information about what they want, "
            "not something to send again."
        )
    return (
        f"Nobody answered it: the Set moved on to {current.version_label} before they did, so it "
        "was never applied and it is not waiting for anything. Propose afresh if the change "
        "still makes sense against what is being brewed now."
    )


# ── the grade ────────────────────────────────────────────────────────


async def _grade_block(db: Database, set_id: int, version: SetVersionRow) -> list[str]:
    """What this conversation proposed as the version's outcome, and what the person did.

    **This version's only.** A grade the agent proposed is words until the
    person accepts it, and nothing an agent graded reaches a later conversation
    unless they did: so a waiting or dismissed grade is rendered here, in the
    conversation of the version it grades, so the agent does not repeat itself,
    and appears nowhere else — not in the ledger, the track record, another
    version's context or any tool's output, all of which read recorded outcomes
    only. Nothing is rendered when this version has had no grade proposed, so an
    experiment nobody has graded reads as it always did.
    """
    outcomes = OutcomeProposalsRepository(db)
    waiting = await outcomes.waiting_for_version(version.id)
    if waiting is not None:
        lines = [
            "A GRADE YOU PROPOSED IS WAITING FOR THE PERSON",
            f"You proposed {_grade_words(waiting)}. They have not answered it yet. Nothing is "
            f"recorded: {version.version_label}'s outcome is still {_recorded_words(version)}.",
        ]
        if waiting.counted_shots_now > waiting.counted_shots:
            more = waiting.counted_shots_now - waiting.counted_shots
            lines.append(
                f"{_plural(more, 'shot')} counted since you wrote it; if they change the "
                "picture, propose the grade again and it replaces this one."
            )
        lines.append(
            "Talk about it — do not propose it again unless you have a reason. If you propose "
            "the next version while it waits, accepting that version records this grade."
        )
        return lines
    last = await outcomes.last_answered(version.id)
    if last is None:
        return []
    return [
        "THE LAST ANSWER TO YOUR GRADE",
        f"You proposed {_grade_words(last)}. {_grade_answer(last)}",
    ]


def _grade_words(row: OutcomeProposalRow) -> str:
    return (
        f"{_OUTCOMES[row.outcome]}, on {_plural(row.counted_shots, 'counted shot')}"
        f" ({_quote(_cut(row.note.strip(), NOTE_CHARS * 2))})"
    )


def _recorded_words(version: SetVersionRow) -> str:
    if version.outcome is None:
        return "open"
    return f"{_OUTCOMES[version.outcome]}, as the person recorded it"


def _grade_answer(row: OutcomeProposalRow) -> str:
    if row.status == "accepted":
        return f"They accepted it: the outcome is recorded as {_OUTCOMES[row.outcome]}."
    if row.status == "changed":
        instead = _OUTCOMES[row.recorded_outcome or row.outcome]
        return (
            f"They recorded {instead} instead: that is the outcome of this version, and where "
            "it differs from yours they weighed something you could not see."
        )
    note = (
        f' They said: "{_cut(row.decision_note.strip(), NOTE_CHARS)}"' if row.decision_note else ""
    )
    return (
        f"They dismissed it.{note} Nothing is recorded, and the outcome is still open until "
        "they grade it; a dismissed grade is information about what they want, not something "
        "to send again."
    )


# ── the ledger ───────────────────────────────────────────────────────


def _ledger(
    versions: Sequence[SetVersionRow],
    dead_ends: set[int],
    labels: dict[int, Any],
    this: SetVersionRow,
    by_id: dict[int, SetVersionRow],
    profile_labels: dict[int, str],
    averages: Mapping[int, SetTrendVersion],
) -> list[str]:
    """Every version, oldest first, one line each — budgeted from the old end.

    The rule, in full, because it is a rule and not a "whatever fits": the
    newest :data:`LEDGER_VERSIONS` are written out; **this version and the one
    its prediction is compared against are never dropped**, whatever their age,
    because the conversation is about exactly those two; everything dropped is
    summarised as counts. A Set that has not run past the budget reads as the
    whole ledger, which is the ordinary case.
    """
    # Oldest first by when they were made; every one is written out by its name.
    oldest_first = _oldest_first(versions)
    kept = {version.id for version in oldest_first[-LEDGER_VERSIONS:]}
    kept.add(this.id)
    if this.compares_to_version_id is not None:
        kept.add(this.compares_to_version_id)
    dropped = [version for version in oldest_first if version.id not in kept]

    lines = ["THE EXPERIMENT SO FAR (oldest first)"]
    if dropped:
        lines.append(_dropped_line(dropped, oldest_first))
    lines += [
        _ledger_line(version, dead_ends, labels, this, by_id, profile_labels, averages)
        for version in oldest_first
        if version.id in kept
    ]
    lines.append(_track_record_line(versions))
    return lines


def _oldest_first(versions: Iterable[SetVersionRow]) -> list[SetVersionRow]:
    """By `created_at`; the row id only breaks a tie and is never shown."""
    return sorted(versions, key=lambda version: (version.created_at, version.id))


def _dropped_line(dropped: Sequence[SetVersionRow], every: Sequence[SetVersionRow]) -> str:
    """The versions the budget left out, named exactly.

    Named rather than spanned: the two the budget never drops sit inside the
    old end of the Set, so "v1 to v13" would claim to have summarised versions
    that are written out three lines below it.
    """
    counted: dict[str, int] = {}
    for version in dropped:
        counted[version.outcome_state] = counted.get(version.outcome_state, 0) + 1
    states = ", ".join(
        f"{count} {_OUTCOMES[state].split(' —')[0]}" for state, count in sorted(counted.items())
    )
    return (
        f"- Not written out here: {_ranges(dropped, every)} "
        f"({len(dropped)} versions: {states}). get_set has them all."
    )


def _ranges(versions: Iterable[SetVersionRow], every: Sequence[SetVersionRow]) -> str:
    """ "v1, v1.3 to v2.1, v3" — versions made one after another collapsed, in order.

    "One after another" is by when they were made among **all** the Set's
    versions (``every``, oldest first), so a span reads from its first version's
    name to its last's and covers every version made between them, minor or
    major.
    """
    position = {version.id: index for index, version in enumerate(every)}
    ordered = sorted(
        {version.id: version for version in versions}.values(), key=lambda v: position[v.id]
    )
    spans: list[tuple[SetVersionRow, SetVersionRow]] = []
    for version in ordered:
        if spans and position[version.id] == position[spans[-1][1].id] + 1:
            spans[-1] = (spans[-1][0], version)
        else:
            spans.append((version, version))
    return ", ".join(
        start.version_label if start is end else f"{start.version_label} to {end.version_label}"
        for start, end in spans
    )


def _ledger_line(
    version: SetVersionRow,
    dead_ends: set[int],
    labels: dict[int, Any],
    this: SetVersionRow,
    by_id: dict[int, SetVersionRow],
    profile_labels: dict[int, str],
    averages: Mapping[int, SetTrendVersion],
) -> str:
    """One version: what it changed, what it produced, and how it was graded.

    The **change**, not the recipe: a ledger of full recipes is fifteen lines
    that differ in one number each, and what a reader — or a model looking for
    what has already been tried — is after is the difference. The first version
    has no parent to differ from, so it carries its recipe; this version's and
    the compared version's own recipes are written out in full above.

    What it **produced** is the version's means over its counted shots, the same
    numbers `get_set`'s trajectory serves (:func:`_averages`), so the first
    question about a change ("did it move anything?") needs no tool call.

    The outcome carries **its note**. The note is the only place the reason a
    past experiment failed is written down, and it is the thing that stops the
    same change being proposed again.
    """
    counts = labels.get(version.id)
    shots = (
        _plural(version.shot_count, "shot")
        + (
            f" ({counts.keep} Keep, {counts.improve} Improve, {counts.discard} Discard)"
            if counts is not None and version.shot_count
            else ""
        )
        if version.shot_count
        else "no shots"
    )
    note = str(version.outcome_note or "").strip()
    prediction = (
        f"predicted{_against(version)}: {version.prediction} → "
        f"{_OUTCOMES[version.outcome_state]}" + (f' — "{note[:NOTE_CHARS]}"' if note else "")
        if version.prediction
        else "no prediction"
    )
    marks = "".join(
        [
            " (dead end)" if version.id in dead_ends else "",
            " ← this version" if version.id == this.id else "",
            f" ← what {this.version_label} is compared against"
            if version.id == this.compares_to_version_id
            else "",
        ]
    )
    parent = by_id.get(version.parent_version_id or 0)
    changed = (
        _changes(version_changes(version, parent, profile_labels))
        if parent is not None
        else _recipe(version)
    )
    parts = [
        f"{version.version_label}{marks}",
        changed,
        *([f"restores {version.restores_version_label}"] if version.restores_version_label else []),
        shots,
        *_averages(version, averages.get(version.id)),
        prediction,
    ]
    # Separated by a middle dot rather than by full stops: an intent is the
    # person's own sentence and usually ends in one already, and "Filler 3.."
    # is the kind of detail a reader stops on.
    return f"- {' · '.join(parts)}"


def _averages(version: SetVersionRow, trend: SetTrendVersion | None) -> list[str]:
    """The ledger's "what did it do" part: means over counted shots, or why there are none.

    Means only, no spread per version (the spread block is the yardstick). A
    mean over fewer shots than the version has says how many it is over, so a
    rating from one shot of five is not read as five. A mean nothing recorded
    (no scale, nobody rated, no pressure sensor) is left out, not written as a zero.
    """
    if trend is None or not version.shot_count:
        return []
    if not trend.counted_shots:
        return ["no counted shots"]
    over = trend.averaged_over
    ratio = f"1:{trend.avg_ratio:.2f}" if trend.avg_ratio is not None else None
    # (value, how many shots it is over, written form), in the order the vocabulary lists them.
    means: list[tuple[str, int, str | None]] = [
        (
            "shot time",
            over.duration_s,
            None if trend.avg_duration_s is None else f"{trend.avg_duration_s:.1f} s",
        ),
        (
            "yield",
            over.yield_g,
            None if trend.avg_yield_g is None else f"{trend.avg_yield_g:.1f} g",
        ),
        ("ratio", over.ratio, ratio),
        (
            "rating",
            over.rating,
            None if trend.avg_rating is None else f"{trend.avg_rating:.1f}",
        ),
        (
            "first drip",
            over.first_drip_s,
            None if trend.avg_first_drip_s is None else f"{trend.avg_first_drip_s:.1f} s",
        ),
    ]
    shown = [(label, n, text) for label, n, text in means if text is not None]
    if not shown:
        return ["counted shots recorded no values"]
    counts = {n for _, n, _ in shown}
    if len(counts) == 1 and (only := counts.pop()) < version.shot_count:
        # Every mean rests on the same few shots: say so once, not five times.
        return [
            f"averages over {only} counted {'shot' if only == 1 else 'shots'}: "
            + ", ".join(f"{label} {text}" for label, _, text in shown)
        ]
    parts = [
        f"{label} {text}" + (f" (over {n})" if n < version.shot_count else "")
        for label, n, text in shown
    ]
    return ["averages over counted shots: " + ", ".join(parts)]


def _against(version: SetVersionRow) -> str:
    return (
        f" against {version.compares_to_version_label}" if version.compares_to_version_label else ""
    )


def _track_record_line(versions: Sequence[SetVersionRow]) -> str:
    record = track_record(versions)
    return (
        f"Track record: {record.held} of {record.graded} graded predictions held "
        f"({record.partly_held} partly, {record.failed} failed, "
        f"{record.inconclusive} inconclusive); {record.open} open, "
        f"{record.no_prediction} with no prediction."
    )


# ── the spread and the evidence ──────────────────────────────────────


def _spread_block(spreads: dict[SpreadMeasure, Spread]) -> list[str]:
    """How much this Set's shots vary when nothing in the recipe changed."""
    lines = [
        "HOW MUCH THIS SET VARIES WHEN NOTHING CHANGED",
        "This is the yardstick. A difference smaller than it is not a result.",
    ]
    if not any(spreads[measure].recorded for measure in SPREAD_MEASURES):
        # A Set with no counted shots records nothing, and a heading with
        # nothing under it reads as a bug rather than as "not yet". The floors
        # are what a difference would be held against the moment there is one.
        floors = ", ".join(
            f"{_MEASURES[measure].label} {_fine(measure, MEASURE_FLOORS[measure])}{_unit(measure)}"
            for measure in SPREAD_MEASURES
        )
        lines.append(
            "Nothing is measured yet: this Set has no shots that count. Until it has, a "
            f"difference is held against these floors — {floors}."
        )
        return lines
    for measure in SPREAD_MEASURES:
        spread = spreads[measure]
        term = _MEASURES[measure]
        if not spread.recorded:
            continue
        if spread.measured and spread.value is not None:
            recipes = spread.shots - spread.degrees_of_freedom
            lines.append(
                f"- {term.label}: ±{_number(measure, spread.value)}{_unit(measure)}, from "
                f"{_plural(spread.shots, 'repeat shot')} of {_plural(recipes, 'recipe')}."
            )
        else:
            lines.append(
                f"- {term.label}: not measured yet — a difference is held against "
                f"{_fine(measure, spread.floor)}{_unit(measure)} until it is."
            )
    return lines


def _evidence_block(
    evidence: VersionEvidence, version: SetVersionRow, compared: SetVersionRow | None
) -> list[str]:
    """This version's shots against the compared version's, measure by measure."""
    this_label = version.version_label
    other_label = compared.version_label if compared is not None else "nothing"
    lines = [
        f"THE EVIDENCE FOR {this_label} AGAINST {other_label}",
        "Every counted shot of both versions, never a chosen one.",
        "",
        f"| measure | {this_label} | {other_label} | difference | verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [_evidence_row(row) for row in evidence.measures]
    lines += ["", _counts_line(this_label, evidence.this)]
    if evidence.other is not None:
        lines.append(_counts_line(evidence.other.version_label, evidence.other))
    else:
        lines.append(
            "This version is compared against nothing: grade its prediction on the numbers "
            "it states."
        )
    return lines


def _evidence_row(row: MeasureEvidence) -> str:
    term = _MEASURES[row.measure]
    mine = _side(row.measure, row.this.mean, row.this.n)
    theirs = "—" if row.other is None else _side(row.measure, row.other.mean, row.other.n)
    if row.difference is None or row.yardstick is None:
        verdict = "nothing to compare"
        difference = "—"
    else:
        difference = f"{_signed(row.measure, row.difference)}{_unit(row.measure)}"
        held = f"{_fine(row.measure, row.yardstick)}{_unit(row.measure)}"
        verdict = (
            f"beyond the spread (held against {held})"
            if row.verdict == "beyond"
            else f"inside the spread (held against {held})"
        )
    return f"| {term.label} | {mine} | {theirs} | {difference} | {verdict} |"


def _side(measure: SpreadMeasure, mean: float | None, n: int) -> str:
    if mean is None or n == 0:
        return "nothing recorded"
    return f"{_number(measure, mean)}{_unit(measure)} over {_plural(n, 'shot')}"


def _counts_line(label: str, counts: Any) -> str:
    return (
        f"{label}: {_plural(counts.shots, 'counted shot')} — {counts.sour} sour, {counts.balanced} "
        f"balanced, {counts.bitter} bitter; {counts.keep} Keep, {counts.improve} Improve, "
        f"{counts.unlabelled} unlabelled."
    )


# ── the shots ────────────────────────────────────────────────────────


async def _shots_block(
    db: Database,
    set_id: int,
    version: SetVersionRow,
    *,
    recent: int,
    tiers: Mapping[str, Tier],
    curve_points: int,
) -> list[str]:
    """This version's newest shots, counted or not, each in its base information.

    Only this version's: the compared-to version is in the evidence table and
    the gold standard, which are aggregates over every counted shot, and any
    one of its shots is a search away. A shot that does not count is written
    out like the rest — "when did it go wrong" is a question it is part of the
    answer to — and says so on its own line.
    """
    rows = await SetsRepository(db).set_shots(set_id, version_id=version.id, limit=recent)
    # The samples only when a person moved a curve channel into base: at the
    # default tiers the opening context carries no curve and reads none.
    shots = await load_shots(
        db, [row.shot_id for row in rows], samples=needs_samples("base", tiers)
    )
    number = version.version_label
    if not shots:
        return [f"THE SHOTS OF {number}", "- none yet."]
    total = max(version.shot_count, len(shots))
    if total > len(shots):
        heading = f"THE LAST {len(shots)} OF {total} SHOTS OF {number} (newest first)"
        rest = (
            f"The other {total - len(shots)} of {number}'s shots, and every other version's, "
            "are a list_set_shots search away"
        )
    else:
        heading = (
            f"THE ONLY SHOT OF {number}"
            if total == 1
            else f"ALL {total} SHOTS OF {number} (newest first)"
        )
        rest = "Every other version's shots are a list_set_shots search away"
    lines = [
        heading,
        f"Each in its base information. {rest}; get_shot_extended adds any one shot's "
        "diagnostics, phases and curve.",
        # The ids, so "is this shot already here?" is a look at one line and not a scan of
        # the shots below: an agent that re-read shots it had been handed spent its tool
        # calls (and, through a CLI, every later request) on the same text twice.
        "Shots written out below: " + ", ".join(str(facts.shot_id) for facts in shots) + ".",
    ]
    for facts in shots:
        lines += ["", render_shot(facts, "base", tiers, curve_points=curve_points)]
    return lines


# ── the gold standard ────────────────────────────────────────────────


def _gold_standard(
    counted: Sequence[Any], versions: Sequence[SetVersionRow], dead_ends: set[int]
) -> list[str]:
    """What good has looked like in this Set: the Keep shots still on the line.

    On the live line only, because a Keep shot under a version a roll back
    stepped over is a cup somebody liked on a branch nobody is brewing any more
    — worth finding with `list_set_shots`, not worth holding an Improve shot up
    against.
    """
    live = {version.id for version in versions if version.id not in dead_ends}
    keeps = [shot for shot in counted if shot.decision == "keep" and shot.version_id in live]
    if not keeps:
        return [
            "THE GOLD STANDARD",
            "No shot on the line being brewed has been labelled Keep yet, so there is no "
            "target to compare an Improve shot against.",
        ]
    # In the order they were made, each by its name.
    by_id = {version.id: version for version in versions}
    ids = sorted({shot.version_id for shot in keeps}, key=lambda i: (by_id[i].created_at, i))
    names = [by_id[i].version_label for i in ids]
    averages = [
        f"{_MEASURES[measure].label.lower()} {_number(measure, _mean(keeps, measure))}"
        f"{_unit(measure)}"
        for measure in SPREAD_MEASURES
        if _mean(keeps, measure) is not None
    ]
    return [
        "THE GOLD STANDARD",
        f"{_plural(len(keeps), 'Keep shot')} on the line being brewed, on "
        f"{', '.join(names)}. "
        f"Their averages: {', '.join(averages)}.",
        "That is what good has tasted like here. An Improve shot is compared with it.",
    ]


def _mean(shots: Sequence[Any], measure: SpreadMeasure) -> float | None:
    values = [value for shot in shots if (value := shot.measure(measure)) is not None]
    return sum(values) / len(values) if values else None


# ── the insights ─────────────────────────────────────────────────────


async def _insights_block(db: Database, row: SetRow) -> list[str]:
    """The confirmed insights that apply, newest first and bounded.

    Bounded because this is the one section whose length is not a property of
    the Set: a kitchen that has been confirming insights for a year would push
    the experiment itself out of the model's attention with a wall of prose
    about beans. The newest are the ones that supersede the others, the rest
    are a sentence and a tool call away, and both the cut and the order are
    fixed so the same archive renders the same text.
    """
    insights = await _insights(db, row)
    if not insights:
        return ["CONFIRMED INSIGHTS THAT APPLY HERE", "- none yet."]
    newest = sorted(insights, key=lambda item: item.id, reverse=True)
    lines = [
        "CONFIRMED INSIGHTS THAT APPLY HERE",
        *(f"- {item.render(text_chars=INSIGHT_CHARS)}" for item in newest[:INSIGHTS_SHOWN]),
    ]
    if len(newest) > INSIGHTS_SHOWN:
        lines.append(
            f"- and {len(newest) - INSIGHTS_SHOWN} more that apply here; get_insights lists them."
        )
    return lines


def _cut(text: str, limit: int) -> str:
    """Text at its cap, with an ellipsis so a reader can see it was cut."""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


async def _insights(db: Database, row: SetRow) -> list[Any]:
    """Through the one selection, so the chat and the Set page cannot disagree."""
    return await InsightsRepository(db).for_set(row.id)


async def _proposed_insights_block(db: Database, thread_id: int | None) -> list[str]:
    """What this conversation proposed as insights, and what the person did with each.

    **This conversation's only** (keyed by the thread that wrote it): a waiting
    insight is not evidence, and a dismissed one reaches no prompt except the
    conversation that proposed it, which is told so that it does not offer it
    again. An added insight is also in the confirmed list above; here it is said
    to have been added.
    """
    if thread_id is None:
        return []
    proposed = await InsightsRepository(db).proposed_in(thread_id)
    if not proposed:
        return []
    lines = ["INSIGHTS YOU PROPOSED IN THIS CONVERSATION"]
    for item in proposed:
        if item.dismissed:
            state = "dismissed by the person — do not offer it again"
        elif item.confirmed:
            state = (
                "added by the person, and it replaced the old insight, which was deleted — it "
                "is in the confirmed list above"
                if item.replaced == "deleted"
                else "added by the person; the insight it was meant to replace had already "
                "changed, so nothing was deleted — it is in the confirmed list above"
                if item.replaced == "old_changed"
                else "added by the person — it is in the confirmed list above"
            )
        else:
            state = "waiting: the person has not answered, so it is not evidence yet"
            if item.replaces_id is not None:
                state += f"; adding it would delete #{item.replaces_id}"
            elif item.replaced == "old_changed":
                state += "; the insight it was meant to replace is already gone"
        lines.append(f"- {_cut(item.text, INSIGHT_CHARS)} ({state})")
    return lines


async def _proposed_deletions_block(db: Database, thread_id: int | None) -> list[str]:
    """What this conversation proposed deleting, and what the person did with each.

    **This conversation's only.** A kept one says only that it was kept (the person's
    answer, with nothing to argue with); one whose insight went another way first
    says it is already gone. An answered one stays here so the conversation does not
    propose it again, and the text is the one the card showed: nothing else of a
    deleted insight is read anywhere.
    """
    if thread_id is None:
        return []
    proposed = await InsightDeletionsRepository(db).proposed_in(thread_id)
    if not proposed:
        return []
    lines = ["INSIGHT DELETIONS YOU PROPOSED IN THIS CONVERSATION"]
    for item in proposed:
        state = {
            "deleted": "deleted by the person",
            "kept": "kept by the person",
            "stale": "already gone, nothing to do",
        }.get(item.status, "waiting: nothing is deleted until the person presses Delete")
        # The number is there while the insight is: once it is gone, only the text is.
        number = "" if item.insight_id is None else f"#{item.insight_id} "
        # A proposal whose insight went another way has lost its copy of the text.
        text = _cut(item.insight_text, INSIGHT_CHARS) or "an insight that has since been removed"
        lines.append(f"- {number}{text} ({state})")
    return lines


# ── formatting ───────────────────────────────────────────────────────


def _number(measure: SpreadMeasure, value: float | None) -> str:
    """A mean or a spread, at the precision the measure is read in."""
    return "—" if value is None else f"{value:.{_MEASURES[measure].decimals}f}"


def _signed(measure: SpreadMeasure, value: float) -> str:
    """A difference, with its sign and at the difference precision.

    The sign is half of what a prediction claimed, and the precision is the one
    the verdict was decided at — "+2.5 s" beside "held against 2.00 s" reads as
    two different measurements of the same thing.
    """
    return f"{value:+.{_MEASURES[measure].difference_decimals}f}"


def _fine(measure: SpreadMeasure, value: float) -> str:
    """A yardstick or a floor: one decimal finer, as everywhere else."""
    return f"{value:.{_MEASURES[measure].difference_decimals}f}"


def _unit(measure: SpreadMeasure) -> str:
    unit = _MEASURES[measure].unit
    return f" {unit}" if unit else ""


def thread_title_from(message: str) -> str:
    """A thread's name, taken from its first message.

    The first sentence, capped. Naming a thread is a chore nobody does, and an
    untitled list of twenty conversations is unusable; the user can rename it.
    """
    collapsed = " ".join(message.split())
    if not collapsed:
        return "New conversation"
    for stop in (". ", "? ", "! "):
        head, sep, _ = collapsed.partition(stop)
        if sep and len(head) >= 12:
            collapsed = head + sep.strip()
            break
    return collapsed[:80].rstrip() + ("…" if len(collapsed) > 80 else "")

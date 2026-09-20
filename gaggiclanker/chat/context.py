"""What the model is told before it asks anything: the experiment so far.

A Set conversation is about **one version** of one Set — the change being
argued — and this module writes down everything the archive already knows about
that change before a word is typed: the Set and the recipe, the whole prediction
ledger with its outcomes, the evidence table this version's prediction is graded
on, the spread that decides what counts as a difference, the shots on both sides
of the comparison, and what a good shot of this coffee has looked like.

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
is no one experiment to put in front of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository
from gaggiclanker.db.repos.grinders import GrindersRepository
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository, set_attributes
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    SetRow,
    SetShotRow,
    SetsRepository,
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
from gaggiclanker.tools.scope import ToolScope

__all__ = [
    "INSIGHTS_SHOWN",
    "INSIGHT_CHARS",
    "LEDGER_VERSIONS",
    "NOTE_CHARS",
    "VERSION_SHOTS",
    "opening_context",
    "thread_title_from",
]

#: How many versions the ledger writes out in full. A Set that has run past this
#: is summarised from the oldest end — see :func:`_ledger` for the rule and for
#: the two versions it never drops.
LEDGER_VERSIONS = 12

#: How many shots of each compared version are listed one line at a time. Enough
#: to show a run and short of a chapter; `list_set_shots` fetches the rest.
VERSION_SHOTS = 12

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

_DECISIONS: dict[str, str] = {"keep": "Keep", "improve": "Improve", "discard": "Discard"}


def _plural(count: int, noun: str) -> str:
    """ "1 shot", "2 shots". Written out because the model reads these lines."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


async def opening_context(db: Database, scope: ToolScope) -> str:
    """The experiment so far, as markdown, or an empty string for a general chat.

    ``scope.set_version_id`` is the version being argued. A scope that names a
    Set and no version falls back to the current one rather than refusing: a
    thread always stores both, and a caller assembling a context by hand (a
    test, a script) should get the obvious answer.
    """
    if scope.kind != "set" or scope.set_id is None:
        return ""
    sets = SetsRepository(db)
    row = await sets.get(scope.set_id)
    if row is None:
        return ""
    versions = await sets.versions(scope.set_id)
    version = _this_version(versions, scope.set_version_id)
    if version is None:
        return ""

    by_id = {item.id: item for item in versions}
    dead_ends = dead_end_ids(versions)
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
            version_no=version.version_no,
            compares_to_version_id=version.compares_to_version_id,
            compares_to_version_no=version.compares_to_version_no,
        )
        if version.prediction
        else None
    )

    profile_labels = _profile_labels(versions)
    lines: list[str] = []
    lines += await _heading(db, row, version, versions, by_id, dead_ends)
    lines += ["", *await _proposal_block(db, scope.set_id, profile_labels, versions[0])]
    lines += ["", *_ledger(versions, dead_ends, labels, version, by_id, profile_labels)]
    lines += ["", *_spread_block(spreads)]
    if evidence is not None:
        lines += ["", *_evidence_block(evidence, version, compared)]
    lines += ["", *await _shots_block(sets, scope.set_id, version, compared)]
    lines += ["", *_gold_standard(counted, versions, dead_ends)]
    lines += ["", *await _insights_block(db, row)]
    lines += [
        "",
        "Those facts are the record, not the whole archive: use the tools for anything else, "
        "and for anything you are about to quote a number from.",
    ]
    return "\n".join(lines)


def _this_version(
    versions: Sequence[SetVersionRow], version_id: int | None
) -> SetVersionRow | None:
    """The version the conversation is about; the current one when unsaid.

    ``versions`` is newest first, so the current version is the first of them.
    """
    if version_id is not None:
        return next((version for version in versions if version.id == version_id), None)
    return versions[0] if versions else None


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
    )
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
        f"Status: {row.status}{', currently on the machine' if row.active else ''}.",
        "",
        f"THIS VERSION IS v{version.version_no}"
        + (" (a dead end: a later roll back went back past it)" if version.id in dead_ends else "")
        + (" — the current version of this Set" if version.id == versions[0].id else ""),
        f"Recipe: {_recipe(version)}.",
        f"Changed against v{parent.version_no}: {_changes(changes)}."
        if parent is not None
        else "It is the Set's first version: a baseline, not a change to anything.",
    ]
    if version.intent:
        lines.append(f"What you are trying: {version.intent}")
    lines.append(_prediction_line(version))
    return lines


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
        f" (compared to v{version.compares_to_version_no})"
        if version.compares_to_version_no
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
            f"v{waiting.base_version_no} until they accept it. Talk about this one — do not "
            "propose another change while it waits.",
        ]
    last = await proposals.last_decided(set_id)
    if last is None:
        return [
            "PROPOSALS",
            "Nothing has been proposed to the person on this Set yet.",
        ]
    return [
        "THE LAST PROPOSAL",
        f"It proposed changing {await _proposal_change(proposals, last, profile_labels)}. "
        f"Reason: {_quote(last.reason)} {_proposal_prediction(last)}",
        _decided_line(last, current),
    ]


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
        f" (compared to v{row.compares_to_version_no})"
        if row.compares_to_version_no
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
            f"They accepted it; it is v{row.resulting_version_no}. Its own prediction is in the "
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
        f"Nobody answered it: the Set moved on to v{current.version_no} before they did, so it "
        "was never applied and it is not waiting for anything. Propose afresh if the change "
        "still makes sense against what is being brewed now."
    )


# ── the ledger ───────────────────────────────────────────────────────


def _ledger(
    versions: Sequence[SetVersionRow],
    dead_ends: set[int],
    labels: dict[int, Any],
    this: SetVersionRow,
    by_id: dict[int, SetVersionRow],
    profile_labels: dict[int, str],
) -> list[str]:
    """Every version, oldest first, one line each — budgeted from the old end.

    The rule, in full, because it is a rule and not a "whatever fits": the
    newest :data:`LEDGER_VERSIONS` are written out; **this version and the one
    its prediction is compared against are never dropped**, whatever their age,
    because the conversation is about exactly those two; everything dropped is
    summarised as counts. A Set that has not run past the budget reads as the
    whole ledger, which is the ordinary case.
    """
    oldest_first = sorted(versions, key=lambda version: version.version_no)
    kept = {version.id for version in oldest_first[-LEDGER_VERSIONS:]}
    kept.add(this.id)
    if this.compares_to_version_id is not None:
        kept.add(this.compares_to_version_id)
    dropped = [version for version in oldest_first if version.id not in kept]

    lines = ["THE EXPERIMENT SO FAR (oldest first)"]
    if dropped:
        lines.append(_dropped_line(dropped))
    lines += [
        _ledger_line(version, dead_ends, labels, this, by_id, profile_labels)
        for version in oldest_first
        if version.id in kept
    ]
    lines.append(_track_record_line(versions))
    return lines


def _dropped_line(dropped: Sequence[SetVersionRow]) -> str:
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
        f"- Not written out here: {_ranges(version.version_no for version in dropped)} "
        f"({len(dropped)} versions: {states}). get_set has them all."
    )


def _ranges(numbers: Iterable[int]) -> str:
    """ "v1, v4 to v7, v9" — consecutive version numbers collapsed, in order."""
    ordered = sorted(set(numbers))
    spans: list[tuple[int, int]] = []
    for number in ordered:
        if spans and number == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], number)
        else:
            spans.append((number, number))
    return ", ".join(f"v{start}" if start == end else f"v{start} to v{end}" for start, end in spans)


def _ledger_line(
    version: SetVersionRow,
    dead_ends: set[int],
    labels: dict[int, Any],
    this: SetVersionRow,
    by_id: dict[int, SetVersionRow],
    profile_labels: dict[int, str],
) -> str:
    """One version: what it changed, what it produced, and how it was graded.

    The **change**, not the recipe: a ledger of full recipes is fifteen lines
    that differ in one number each, and what a reader — or a model looking for
    what has already been tried — is after is the difference. The first version
    has no parent to differ from, so it carries its recipe; this version's and
    the compared version's own recipes are written out in full above.

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
            f" ← what v{this.version_no} is compared against"
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
        f"v{version.version_no}{marks}",
        changed,
        *([f"restores v{version.restores_version_no}"] if version.restores_version_no else []),
        shots,
        prediction,
    ]
    # Separated by a middle dot rather than by full stops: an intent is the
    # person's own sentence and usually ends in one already, and "Filler 3.."
    # is the kind of detail a reader stops on.
    return f"- {' · '.join(parts)}"


def _against(version: SetVersionRow) -> str:
    return f" against v{version.compares_to_version_no}" if version.compares_to_version_no else ""


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
    this_label = f"v{version.version_no}"
    other_label = f"v{compared.version_no}" if compared is not None else "nothing"
    lines = [
        f"THE EVIDENCE FOR v{version.version_no} AGAINST {other_label}",
        "Every counted shot of both versions, never a chosen one.",
        "",
        f"| measure | {this_label} | {other_label} | difference | verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [_evidence_row(row) for row in evidence.measures]
    lines += ["", _counts_line(this_label, evidence.this)]
    if evidence.other is not None:
        lines.append(_counts_line(f"v{evidence.other.version_no}", evidence.other))
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
    sets: SetsRepository,
    set_id: int,
    version: SetVersionRow,
    compared: SetVersionRow | None,
) -> list[str]:
    """This version's shots and the compared version's, one line each."""
    lines = [f"THE SHOTS OF v{version.version_no} (newest first)"]
    lines += _shot_lines(
        await sets.set_shots(set_id, version_no=version.version_no, limit=VERSION_SHOTS)
    )
    if compared is not None:
        lines += ["", f"THE SHOTS OF v{compared.version_no}, WHICH IT IS COMPARED AGAINST"]
        lines += _shot_lines(
            await sets.set_shots(set_id, version_no=compared.version_no, limit=VERSION_SHOTS)
        )
    return lines


def _shot_lines(shots: Sequence[SetShotRow]) -> list[str]:
    if not shots:
        return ["- none yet."]
    return [_shot_line(shot) for shot in shots]


def _shot_line(shot: SetShotRow) -> str:
    bits = [f"shot {shot.shot_id}"]
    if shot.started_at:
        bits.append(str(shot.started_at)[:16].replace("T", " "))
    for measure in (
        "shot_time_s",
        "first_drip_s",
        "yield_g",
        "peak_pressure_bar",
        "brew_flow_ml_s",
    ):
        value = getattr(shot, measure)
        if value is None:
            continue
        term = _MEASURES[measure]
        bits.append(f"{term.label.lower()} {_number(measure, value)}{_unit(measure)}")
    if shot.rating is not None:
        bits.append(f"rated {shot.rating}/5")
    if shot.balance:
        bits.append(shot.balance)
    bits.append(_DECISIONS.get(shot.decision or "", "not labelled"))
    notes = [*shot.taste_notes, *shot.aroma_notes]
    if notes:
        bits.append("flavours " + ", ".join(notes))
    line = f"- {' · '.join(bits)}"
    if not _counts(shot):
        line += " — NOT COUNTED (" + _why_not_counted(shot) + ")"
    written = str(shot.notes or "").strip()
    return f'{line} — "{_cut(written, NOTE_CHARS)}"' if written else line


def _counts(shot: SetShotRow) -> bool:
    return not (shot.quarantined or shot.incomplete or shot.decision == "discard")


def _why_not_counted(shot: SetShotRow) -> str:
    if shot.decision == "discard":
        return "discarded: the shot went wrong, not the recipe"
    return "quarantined" if shot.quarantined else "stopped early"


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
    numbers = sorted({shot.version_no for shot in keeps})
    averages = [
        f"{_MEASURES[measure].label.lower()} {_number(measure, _mean(keeps, measure))}"
        f"{_unit(measure)}"
        for measure in SPREAD_MEASURES
        if _mean(keeps, measure) is not None
    ]
    return [
        "THE GOLD STANDARD",
        f"{_plural(len(keeps), 'Keep shot')} on the line being brewed, on "
        f"{', '.join(f'v{number}' for number in numbers)}. "
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
        *(f"- {_cut(item.render(), INSIGHT_CHARS)}" for item in newest[:INSIGHTS_SHOWN]),
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
    """Through the shared matcher, so the chat and an analysis cannot disagree."""
    bean = await BeansRepository(db).get(row.bean_id) if row.bean_id else None
    attributes = set_attributes(
        bean_id=row.bean_id,
        roast_level=getattr(bean, "roast_level", None),
        process=getattr(bean, "process", None),
        origin=getattr(bean, "origin", None),
        grinder_id=row.grinder_id,
    )
    return await InsightsRepository(db).select(attributes)


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

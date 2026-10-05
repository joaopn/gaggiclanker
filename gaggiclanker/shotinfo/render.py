"""Loading shots and writing them down for a model: one loader, one renderer.

Every place a chat is told about a shot — a Set conversation's opening
context, the shot search, `get_shot`, `get_shot_extended`, `get_shot_full` and
`compare_shots` — reads it through :func:`load_shots` and writes it with
:func:`render_shot`. One path, so a shot reads the same wherever the model
meets it, and a number it quotes from the search is the number `get_shot`
would have given it.

The rendering is plain text, not JSON: a header line ``shot <id>``, then each
group that has something to say, in catalogue order, as a ``[Group]`` line and
``label: value`` lines. Phases are one line each; the curve is one compact
table, cut to about as many rows as the caller asks for
(:mod:`~gaggiclanker.shotinfo.downsample`). It is **byte-stable**: nothing
here reads the clock, iterates a set or depends on a query's incidental order,
so the same archive, tiers and curve budget give the same text, and a golden
file can hold it.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.judgements import JudgementsRepository
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.reviews import ShotReviewsRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotSampleRow, ShotsRepository
from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUPS,
    ITEMS,
    WARNINGS_GROUP,
    Channel,
    Item,
    ShotTier,
    Tier,
    keys_in,
)
from gaggiclanker.shotinfo.downsample import CurveEvents, find_events, select_rows
from gaggiclanker.shotinfo.facts import ShotFacts

__all__ = [
    "Line",
    "item_example",
    "load_shots",
    "needs_samples",
    "render_shot",
    "shot_lines",
    "with_samples",
]


def needs_samples(tier: ShotTier, tiers: Mapping[str, Tier]) -> bool:
    """Whether a rendering at ``tier`` carries a curve, and so needs the samples."""
    return any(ITEMS[key].kind == "curve" for key in keys_in(tier, tiers))


async def load_shots(
    db: Database, shot_ids: Sequence[int], *, samples: bool = False
) -> list[ShotFacts]:
    """Everything the catalogue reads about these shots, in a fixed number of queries.

    One query per table, whatever the number of shots — the shot rows, their
    judgements, the notes typed on the machine, the versions they are filed
    under, their newest finished reviews and, only when ``samples`` is asked
    for, every sample of all of them
    — because the opening context renders twenty shots and the search loads a
    whole Set. The result follows the order of ``shot_ids``; an id with no shot
    is left out.
    """
    rows = await ShotsRepository(db).get_many(shot_ids)
    if not rows:
        return []
    ids = [row.id for row in rows]
    judgements = await JudgementsRepository(db).for_shots(ids)
    notes = await NotesRepository(db).for_shots(ids)
    reviews = await ShotReviewsRepository(db).latest_finished_for_shots(ids)
    versions = await SetsRepository(db).versions_by_id(
        row.set_version_id for row in rows if row.set_version_id is not None
    )
    curves: dict[int, list[ShotSampleRow]] = (
        await ShotsRepository(db).samples_for(ids) if samples else {}
    )
    return [
        ShotFacts(
            shot=row,
            judgement=judgements.get(row.id),
            version=versions.get(row.set_version_id) if row.set_version_id is not None else None,
            note=notes.get(row.id),
            review=reviews.get(row.id),
            samples=tuple(curves.get(row.id, ())) if samples else None,
        )
        for row in rows
    ]


async def with_samples(db: Database, shots: Sequence[ShotFacts]) -> list[ShotFacts]:
    """The same shots with their samples, read in one query for all of them.

    For a caller that checks what it loaded before paying for the curve: a
    shot tool refuses a shot outside its Set before reading its samples.
    """
    curves = await ShotsRepository(db).samples_for([facts.shot_id for facts in shots])
    return [
        dataclasses.replace(facts, samples=tuple(curves.get(facts.shot_id, ()))) for facts in shots
    ]


@dataclass(frozen=True, slots=True)
class Line:
    """One rendered value: which item, and for a phase item, which phase."""

    key: str
    value: str
    #: The phase's position in the shot's phase list, for a phase item.
    phase: int | None = None


def shot_lines(facts: ShotFacts, keys: frozenset[str]) -> list[Line]:
    """Every non-empty shot and phase value among ``keys``, in catalogue order.

    The structured half of the renderer, and what the tests compare item by
    item. Curve channels are not lines; :func:`_curve_table` writes them.
    """
    lines: list[Line] = []
    for item in CATALOGUE:
        if item.key not in keys or item.shot is None:
            continue
        value = item.shot(facts)
        if value:
            lines.append(Line(item.key, value))
    for index, phase in enumerate(facts.phases):
        for item in CATALOGUE:
            if item.key not in keys or item.phase is None:
                continue
            value = (item.chat_phase or item.phase)(facts, phase)
            if value:
                lines.append(Line(item.key, value, index))
    return lines


def render_shot(
    facts: ShotFacts, tier: ShotTier, tiers: Mapping[str, Tier], *, curve_points: int
) -> str:
    """One shot, as the model reads it, at ``base``, ``extended`` or ``full``.

    The header ``shot <id>`` opens every rendering, whatever the tier: it is
    how the text is cited, so the shot id is written there and not again as a
    line. A group with nothing to show is left out, as is a line the shot has
    no value for — absent means not recorded, never zero. ``curve_points`` is
    the `chatCurvePoints` setting: about how many rows the curve is cut to.
    """
    keys = keys_in(tier, tiers)
    lines = shot_lines(facts, keys)
    out = [f"shot {facts.shot_id}"]
    for group in _render_order():
        body = _group_body(facts, group, keys, lines, curve_points)
        if body:
            out.append(f"[{group}]")
            out.extend(body)
    return "\n".join(out)


def _render_order() -> list[str]:
    """Warnings first, then the phases, every other group as catalogued, the curve last.

    What the agent should not miss comes before what it reads for detail, and
    the long table comes last so nothing sits below it. Warnings and the curve
    are named by the catalogue; the phase group is the one whose items are
    per-phase.
    """
    phases = [g for g in GROUPS if any(i.kind == "phase" for i in CATALOGUE if i.group == g)]
    curve = [g for g in GROUPS if any(i.kind == "curve" for i in CATALOGUE if i.group == g)]
    first = [WARNINGS_GROUP, *phases]
    rest = [g for g in GROUPS if g not in first and g not in curve]
    return [*first, *rest, *curve]


def item_example(facts: ShotFacts, key: str, *, curve_points: int) -> str | None:
    """One item's value on one shot, as a rendering writes it; ``None`` when absent.

    What Settings → Shot information shows beside each item, built from the
    renderer's own pieces so the page shows what the agent reads rather than
    a second formatting of it:

    * a shot item is its line's value (``54.6 s``);
    * a phase item is one line per phase that has it, headed as the phase
      lines are, and always by the phase's name, whatever tier the name sits
      in: a bare phase number on a settings page says nothing;
    * a curve channel, whose column is far too long to show, is the table's
      row count and the range the column spans over those rows (``54 of 213
      samples, 0.2 to 8.5 bar``, or ``all 24 samples, …`` for a curve that is
      not cut), written at the column's precision. A channel the table would
      leave out is absent here too.
    """
    item = ITEMS[key]
    if item.channel is not None:
        return _channel_summary(facts, item.channel, curve_points)
    if item.phase is None:
        return next((line.value for line in shot_lines(facts, frozenset({key}))), None)
    lines = shot_lines(facts, frozenset({key, "phase_name"}))
    out: list[str] = []
    for index, phase in enumerate(facts.phases):
        mine = {line.key: line for line in lines if line.phase == index}
        own = mine.get(key)
        if own is None:
            continue
        named = mine.get("phase_name")
        head = _phase_head(phase, named.value if named is not None else None)
        out.append(head if key == "phase_name" else f"{head}: {_phase_value(own)}")
    return "\n".join(out) or None


def _group_body(
    facts: ShotFacts, group: str, keys: frozenset[str], lines: list[Line], curve_points: int
) -> list[str]:
    members = [item for item in CATALOGUE if item.group == group]
    if group == WARNINGS_GROUP:
        # One line per warning, under the heading and without a label of its own.
        return [part for line in lines if line.key == "warnings" for part in line.value.split("\n")]
    if any(item.kind == "curve" for item in members):
        return _curve_table(facts, [item for item in members if item.key in keys], curve_points)
    if any(item.kind == "phase" for item in members):
        return _phase_lines(facts, lines)
    # The shot id is the header already; a line repeating it would be the one
    # line in every rendering that says nothing new.
    wanted = {item.key for item in members} - {"shot_id"}
    return [f"{ITEMS[line.key].label}: {line.value}" for line in lines if line.key in wanted]


def _phase_lines(facts: ShotFacts, lines: list[Line]) -> list[str]:
    """One line per phase that has anything to say, headed by the phase.

    The head is always the phase's number and name (``phase 3 · decline 9-4``), whether or
    not the phase-name item is in the tier: a line of per-phase numbers with no phase to
    hang them on is unreadable, and an extended read alone must say which phase is which.
    A log with no phase table has no named phase, and its line is headed by the number.
    """
    out: list[str] = []
    for index, phase in enumerate(facts.phases):
        mine = [line for line in lines if line.phase == index]
        named = next((line.value for line in mine if line.key == "phase_name"), None)
        values = [_phase_value(line) for line in mine if line.key != "phase_name"]
        if not values and named is None:
            continue
        if named is None:
            # A rendering without the phase name item still names the phases its lines are
            # about: an extended read alone must say which phase each line is.
            phase_name = ITEMS["phase_name"].phase
            named = phase_name(facts, phase) if phase_name is not None else None
        head = _phase_head(phase, named)
        out.append(f"{head}: {'; '.join(values)}" if values else head)
    return out


def _phase_head(phase: Mapping[str, Any], named: str | None) -> str:
    return f"phase {named}" if named is not None else f"phase {phase.get('phase_number')}"


def _phase_value(line: Line) -> str:
    return f"{ITEMS[line.key].label} {line.value}"


def _curve_table(facts: ShotFacts, items: list[Item], curve_points: int) -> list[str]:
    """The tier's channels as one table: what it holds, a header, then a row per sample kept.

    The rows are the shot's :func:`_selection` at ``curve_points``, the same
    timestamps for every channel, and the first line says how many of the
    shot's samples they are and which moments were kept whatever the budget.
    A channel the shot did not record — every value missing, or a scale or
    pressure channel on a machine without one, which the firmware writes as
    zeros — is left out of the table rather than shown as a column of zeros.
    An empty cell is one sample the firmware did not record.
    """
    if not items or not facts.samples:
        return []
    channels = [
        channel
        for item in items
        if (channel := item.channel) is not None and _recorded(facts, channel)
    ]
    if not channels:
        return []
    samples = facts.samples
    positions, events = _selection(facts, curve_points)
    header = ",".join(["t (s)", *(channel.header for channel in channels)])
    rows = [
        ",".join(
            [
                f"{samples[index].t_ms / 1000:.2f}",
                *(_cell(getattr(samples[index], channel.field), channel) for channel in channels),
            ]
        )
        for index in positions
    ]
    return [_curve_heading(len(positions), len(samples), events), header, *rows]


def _selection(facts: ShotFacts, curve_points: int) -> tuple[list[int], CurveEvents]:
    """Which samples the curve writes, and the moments among them that were guaranteed.

    One selection per shot, whatever channels the tiers show: it reads
    pressure and puck flow only as far as the shot recorded them, so moving a
    channel between tiers never moves a timestamp.
    """
    samples = facts.samples or ()
    pressure = _recorded(facts, _PRESSURE)
    puck_flow = _recorded(facts, _PUCK_FLOW)
    events = find_events(
        samples,
        facts.phases,
        pressure=pressure,
        puck_flow=puck_flow,
        sample_interval_ms=facts.shot.sample_interval_ms,
    )
    positions = select_rows(samples, events, curve_points, pressure=pressure, puck_flow=puck_flow)
    return positions, events


def _curve_heading(shown: int, total: int, events: CurveEvents) -> str:
    """``all 24 samples``, or how many of how many and what was kept whatever the budget."""
    if shown == total:
        return f"all {total} samples"
    kept = ["the first and last"]
    if events.phase_edges:
        kept.append("each phase's first and last")
    if events.peak_pressure is not None:
        kept.append("peak pressure")
    if events.first_drip is not None:
        kept.append("first drip")
    if events.pressure_drop is not None:
        kept.append("both ends of the largest pressure drop")
    return f"{shown} of {total} samples, shape-preserving; always kept: {', '.join(kept)}"


#: A column header's unit, the part in brackets: ``pressure (bar)``.
_UNIT = re.compile(r"\(([^)]+)\)$")


def _channel_summary(facts: ShotFacts, channel: Channel, curve_points: int) -> str | None:
    """The column's row count and range, over the rows the table writes."""
    if not facts.samples or not _recorded(facts, channel):
        return None
    positions, _ = _selection(facts, curve_points)
    values = [
        value
        for index in positions
        if (value := getattr(facts.samples[index], channel.field)) is not None
    ]
    if not values:
        return None
    total = len(facts.samples)
    count = f"all {total}" if len(positions) == total else f"{len(positions)} of {total}"
    unit = _UNIT.search(channel.header)
    span = f"{_cell(min(values), channel)} to {_cell(max(values), channel)}"
    return f"{count} samples, {span}" + (f" {unit.group(1)}" if unit else "")


def _channel_of(key: str) -> Channel:
    channel = ITEMS[key].channel
    if channel is None:  # pragma: no cover - the catalogue defines both as channels
        raise LookupError(key)
    return channel


#: The two channels the curve's rows are chosen on.
_PRESSURE = _channel_of("curve_pressure")
_PUCK_FLOW = _channel_of("curve_puck_flow")


def _recorded(facts: ShotFacts, channel: Channel) -> bool:
    samples = facts.samples or ()
    values = [getattr(sample, channel.field) for sample in samples]
    if all(value is None for value in values):
        return False
    if channel.needs == "pressure" and not facts.has_pressure:
        return False
    if channel.needs == "scale":
        # The shot's own flag, or a reading that proves a scale was there: an
        # older file may not carry the flag, and a weight never rises without one.
        return facts.shot.scale_connected or any((sample.v or 0) > 0 for sample in samples)
    return True


def _cell(value: float | int | None, channel: Channel) -> str:
    if value is None:
        return ""
    if channel.decimals == 0:
        return str(int(value))
    text = f"{float(value):.{channel.decimals}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text

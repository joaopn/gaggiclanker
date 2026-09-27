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
table. It is **byte-stable**: nothing here reads the clock, iterates a set or
depends on a query's incidental order, so the same archive and the same tiers
give the same text, and a golden file can hold it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.judgements import JudgementsRepository
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotSampleRow, ShotsRepository
from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUPS,
    ITEMS,
    Channel,
    Item,
    ShotTier,
    Tier,
    keys_in,
)
from gaggiclanker.shotinfo.facts import ShotFacts

__all__ = [
    "Line",
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
    under and, only when ``samples`` is asked for, every sample of all of them
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
            value = item.phase(facts, phase)
            if value:
                lines.append(Line(item.key, value, index))
    return lines


def render_shot(facts: ShotFacts, tier: ShotTier, tiers: Mapping[str, Tier]) -> str:
    """One shot, as the model reads it, at ``base``, ``extended`` or ``full``.

    The header ``shot <id>`` opens every rendering, whatever the tier: it is
    how the text is cited, so the shot id is written there and not again as a
    line. A group with nothing to show is left out, as is a line the shot has
    no value for — absent means not recorded, never zero.
    """
    keys = keys_in(tier, tiers)
    lines = shot_lines(facts, keys)
    out = [f"shot {facts.shot_id}"]
    for group in GROUPS:
        body = _group_body(facts, group, keys, lines)
        if body:
            out.append(f"[{group}]")
            out.extend(body)
    return "\n".join(out)


def _group_body(facts: ShotFacts, group: str, keys: frozenset[str], lines: list[Line]) -> list[str]:
    members = [item for item in CATALOGUE if item.group == group]
    if any(item.kind == "curve" for item in members):
        return _curve_table(facts, [item for item in members if item.key in keys])
    if any(item.kind == "phase" for item in members):
        return _phase_lines(facts, lines)
    # The shot id is the header already; a line repeating it would be the one
    # line in every rendering that says nothing new.
    wanted = {item.key for item in members} - {"shot_id"}
    return [f"{ITEMS[line.key].label}: {line.value}" for line in lines if line.key in wanted]


def _phase_lines(facts: ShotFacts, lines: list[Line]) -> list[str]:
    """One line per phase that has anything to say, headed by the phase.

    The head is the phase-name item's value when it is in the tier
    (``phase 3 · decline 9-4``) and the bare number otherwise, since a line of
    per-phase numbers with no phase to hang them on is unreadable.
    """
    out: list[str] = []
    for index, phase in enumerate(facts.phases):
        mine = [line for line in lines if line.phase == index]
        named = next((line.value for line in mine if line.key == "phase_name"), None)
        values = [
            f"{ITEMS[line.key].label} {line.value}" for line in mine if line.key != "phase_name"
        ]
        if not values and named is None:
            continue
        number = phase.get("phase_number")
        head = f"phase {named}" if named is not None else f"phase {number}"
        out.append(f"{head}: {'; '.join(values)}" if values else head)
    return out


def _curve_table(facts: ShotFacts, items: list[Item]) -> list[str]:
    """The tier's channels as one table: a header, then a row per sample.

    Full resolution, every stored sample. A channel the shot did not record —
    every value missing, or a scale or pressure channel on a machine without
    one, which the firmware writes as zeros — is left out of the table rather
    than shown as a column of zeros. An empty cell is one sample the firmware
    did not record.
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
    header = ",".join(["t (s)", *(channel.header for channel in channels)])
    rows = [
        ",".join(
            [
                f"{sample.t_ms / 1000:.2f}",
                *(_cell(getattr(sample, channel.field), channel) for channel in channels),
            ]
        )
        for sample in facts.samples
    ]
    return [f"{len(rows)} samples", header, *rows]


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

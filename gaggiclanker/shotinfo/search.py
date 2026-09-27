"""The shot search: one Set's shots, filtered and sorted on their base items.

A Set conversation opens with its version's latest shots; everything else it
finds through here. A filter is either a plain column — version, label,
balance, day, and the numbers :data:`~gaggiclanker.db.repos.sets.SEARCH_COLUMNS`
lists — applied in SQL, or a value only the loaded shot knows (a number inside
the diagnostics, a band, the ratio), applied here over the same catalogue
accessors the renderer uses. So a shot found by "first drip over 9 s" shows a
first drip over 9 s, and a shot the machine recorded no first drip for matches
no range on it: absent is not zero.

Deterministic like everything else a model reads: the order is the sort key,
then the shot id in the same direction, and a shot with no value for the key
sorts after every shot that has one, whichever the direction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.domain.vocab import Decision
from gaggiclanker.shotinfo import catalogue
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.render import load_shots

__all__ = [
    "SEARCH_BANDS",
    "SEARCH_LIMIT",
    "SEARCH_NUMBERS",
    "SearchResult",
    "ShotQuery",
    "search_shots",
]

#: The most shots one search returns. A search is how the model finds which
#: shots to read in full, not a way to read a Set in bulk.
SEARCH_LIMIT = 10

#: The numeric base items a search filters and sorts on, by catalogue key.
SEARCH_NUMBERS: Mapping[str, Callable[[ShotFacts], float | None]] = {
    "execution_score": catalogue.execution_score,
    "rating": catalogue.rating,
    "shot_time": catalogue.shot_time,
    "yield": catalogue.yield_g,
    "first_drip": catalogue.first_drip,
    "peak_pressure": catalogue.peak_pressure,
    "brew_flow": catalogue.brew_flow,
    "dose_in": catalogue.dose_in,
    "dose_out": catalogue.dose_out,
    "ratio": catalogue.ratio,
}

#: The banded base items a search matches exactly, by catalogue key.
SEARCH_BANDS: Mapping[str, Callable[[ShotFacts], str | None]] = {
    "channeling_risk": catalogue.channeling_risk,
    "resistance_level": catalogue.resistance_band,
    "pressure_adherence": catalogue.pressure_adherence_band,
    "flow_adherence": catalogue.flow_adherence_band,
}


@dataclass(frozen=True, slots=True)
class ShotQuery:
    """What to find. Every filter is optional; none at all is "the newest"."""

    version_no: int | None = None
    label: Decision | None = None
    balance: str | None = None
    #: ``YYYY-MM-DD``, inclusive, of the shot's UTC start.
    since: str | None = None
    until: str | None = None
    #: ``(min, max)`` per :data:`SEARCH_NUMBERS` key, either end optional.
    ranges: Mapping[str, tuple[float | None, float | None]] = field(default_factory=dict)
    #: One label per :data:`SEARCH_BANDS` key.
    bands: Mapping[str, str] = field(default_factory=dict)
    #: A :data:`SEARCH_NUMBERS` key, or ``date``.
    order_by: str = "date"
    descending: bool = True
    limit: int = SEARCH_LIMIT


@dataclass(frozen=True, slots=True)
class SearchResult:
    shots: list[ShotFacts]
    #: More shots matched than were returned.
    truncated: bool


async def search_shots(db: Database, set_id: int, query: ShotQuery) -> SearchResult:
    """This Set's shots that match, in order, at most ``query.limit`` of them."""
    ids = await SetsRepository(db).search_shot_ids(
        set_id,
        version_no=query.version_no,
        decision=query.label,
        balance=query.balance,
        since=query.since,
        until=query.until,
        ranges=query.ranges,
    )
    matched = [facts for facts in await load_shots(db, ids) if _matches(facts, query)]
    ordered = _ordered(matched, query.order_by, descending=query.descending)
    limit = max(1, min(query.limit, SEARCH_LIMIT))
    return SearchResult(shots=ordered[:limit], truncated=len(ordered) > limit)


def _matches(facts: ShotFacts, query: ShotQuery) -> bool:
    """The filters the SQL could not apply, and the ranges again.

    The ranges are checked here even where the SQL already applied them: it
    costs nothing, and the item's own accessor is then the last word on what
    "shot time over 30 s" means.
    """
    for key, (low, high) in query.ranges.items():
        value = SEARCH_NUMBERS[key](facts)
        if value is None:
            return False
        if low is not None and value < low:
            return False
        if high is not None and value > high:
            return False
    return all(SEARCH_BANDS[key](facts) == wanted for key, wanted in query.bands.items())


def _ordered(shots: list[ShotFacts], order_by: str, *, descending: bool) -> list[ShotFacts]:
    def value(facts: ShotFacts) -> float | str | None:
        if order_by == "date":
            return facts.shot.started_at or None
        return SEARCH_NUMBERS[order_by](facts)

    keyed = [(value(facts), facts) for facts in shots]
    present = [(key, facts) for key, facts in keyed if key is not None]
    absent = [facts for key, facts in keyed if key is None]
    present.sort(key=lambda pair: (pair[0], pair[1].shot_id), reverse=descending)
    absent.sort(key=lambda facts: facts.shot_id, reverse=descending)
    return [facts for _, facts in present] + absent

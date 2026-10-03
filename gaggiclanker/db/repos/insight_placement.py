"""Move agent-written insights onto the Set they fit, once.

An insight used to be filed by bean and grinder, so one learned in a single Set's
conversation reached every Set that shared them. From now on an insight written
in a Set's conversation belongs to that Set. The agent-written insights that
already exist are placed where they can be, and left alone where they cannot.

This is a step the application runs at boot, after the migrations, and not SQL in
the migration: "fits" is the **live matching rule** — the same
:func:`~gaggiclanker.db.repos.knowledge_insights.scope_matches` over the same
Set attributes the selection uses — and a copy of its logic in SQL would be a
second rule that can disagree with the first. It is idempotent by a marker row
(``insight_placement_build``, migration 0039): it does nothing once it has run, so
an insight a person has since moved, added or dismissed is never redone.

**Fits a Set**, stated as a test: the insight is *about the coffee* — its stored scope
names a bean (``bean_id``), or its scope is empty and it lists evidence shots (how a
Set's conversation stored an insight before insights had owners: no scope, its
evidence in that Set) — **and** the scope matches the Set's attributes by
:func:`scope_matches` **and**, when the insight lists evidence shots, at least one of
them is filed in that Set. An insight scoped only by grinder, roast level, process,
origin or style, and an empty-scope one with no evidence, is about equipment, a kind
of coffee or the whole kitchen: it stays general whatever its evidence, so every
future Set keeps getting it. Archived Sets are candidates
(an insight that fits a live and an archived Set is ambiguous, and stays
general); Sets still being designed are not (they have no shots and no insights).

**Exactly one fitting Set**: the insight moves to it and keeps its text,
confirmation, evidence, scope and dates; its version is the version of its newest
evidence shot filed in that Set, else the version that was the Set's current one
when the insight was written, else none ("learned before versions were
recorded"). **None or several**: it stays general, exactly as it was.

Only ``chat`` and ``analysis`` insights move. A hand-written one (``user``) is
general knowledge the person wrote down, and stays where they put it.
"""

from __future__ import annotations

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.knowledge_insights import (
    InsightRow,
    InsightsRepository,
    scope_matches,
)
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repository import Repository

log = structlog.get_logger(__name__)

__all__ = ["MOVABLE_SOURCES", "InsightPlacementBuilder"]

#: Who wrote an insight that may move. Never ``user``.
MOVABLE_SOURCES = ("chat", "analysis")


class InsightPlacementBuilder(Repository):
    """Places the existing agent-written insights, once."""

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.insights = InsightsRepository(db)
        self.sets = SetsRepository(db)

    async def built(self) -> bool:
        return (
            await self.db.fetch_one("SELECT 1 FROM insight_placement_build WHERE id = 1")
            is not None
        )

    async def build(self) -> dict[str, int] | None:
        """Place them. ``None`` when it was already done, else the counts."""
        async with self.db.transaction():
            if await self.built():
                return None
            counts = await self._build()
            await self.db.execute(
                "INSERT INTO insight_placement_build (id, built_at, moved) VALUES (1, ?, ?)",
                (utc_now(), counts["moved"]),
            )
        log.info("insights_placed", **counts)
        return counts

    async def fitting_sets(self, insight: InsightRow) -> list[int]:
        """The Sets this insight fits, by the definition above. Ids, oldest first.

        Public so a test can ask the same question of every row independently of
        what :meth:`build` decided.
        """
        found: list[int] = []
        if not _about_the_coffee(insight):
            return found
        evidence = {int(shot) for shot in insight.evidence_shot_ids or [] if _is_id(shot)}
        listed = bool(insight.evidence_shot_ids)
        for set_id in await self._candidate_sets():
            attributes = await self.insights.attributes_of(set_id)
            if attributes is None or not scope_matches(insight.scope.stated(), attributes):
                continue
            if listed and not (evidence & await self.sets.shot_ids(set_id)):
                continue
            found.append(set_id)
        return found

    async def _candidate_sets(self) -> list[int]:
        rows = await self.db.fetch_all("SELECT id FROM sets WHERE designing = 0 ORDER BY id")
        return [int(row["id"]) for row in rows]

    async def _build(self) -> dict[str, int]:
        rows = await self.db.fetch_all(
            "SELECT id FROM knowledge_insights "
            "WHERE set_id IS NULL AND source IN (?, ?) ORDER BY id",
            MOVABLE_SOURCES,
        )
        considered = 0
        moved = 0
        ambiguous = 0
        for row in rows:
            insight = await self.insights.get(int(row["id"]))
            if insight is None:  # pragma: no cover - read a moment ago in this transaction
                continue
            considered += 1
            fitting = await self.fitting_sets(insight)
            if len(fitting) > 1:
                ambiguous += 1
            if len(fitting) != 1:
                continue
            set_id = fitting[0]
            version_id = await self._version_learned_at(insight, set_id)
            # `updated_at` is left alone: the insight keeps its dates, and moving
            # it is not an edit anybody made.
            await self.db.execute(
                "UPDATE knowledge_insights SET set_id = ?, set_version_id = ? WHERE id = ?",
                (set_id, version_id, insight.id),
            )
            moved += 1
        return {
            "considered": considered,
            "moved": moved,
            "ambiguous": ambiguous,
            "left_general": considered - moved,
        }

    async def _version_learned_at(self, insight: InsightRow, set_id: int) -> int | None:
        """Which version of the Set the insight was learned at, or ``None``.

        The newest evidence shot filed in the Set decides; with no evidence, the
        version that was the Set's current one when the insight was written; and
        a Set that had no version yet then (the insight predates it) gives none.
        """
        evidence = [int(shot) for shot in insight.evidence_shot_ids or [] if _is_id(shot)]
        if evidence:
            marks = ", ".join("?" for _ in evidence)
            found = await self.db.fetch_value(
                f"""
                SELECT sh.set_version_id FROM shots sh
                  JOIN set_versions v ON v.id = sh.set_version_id
                 WHERE v.set_id = ? AND sh.id IN ({marks})
                 ORDER BY COALESCE(sh.started_at, '') DESC, sh.id DESC
                 LIMIT 1
                """,  # noqa: S608 - the placeholders are literal question marks
                (set_id, *evidence),
            )
            if found is not None:
                return int(found)
        found = await self.db.fetch_value(
            """
            SELECT id FROM set_versions
             WHERE set_id = ? AND created_at <= ?
             ORDER BY version_no DESC LIMIT 1
            """,
            (set_id, insight.created_at),
        )
        return None if found is None else int(found)


def _is_id(value: object) -> bool:
    """A stored evidence entry that is a shot id; anything else is damage and ignored."""
    return isinstance(value, int) and not isinstance(value, bool)


def _about_the_coffee(insight: InsightRow) -> bool:
    """Whether an insight is about one coffee, and so may move onto a Set.

    A bean in its scope, or no scope at all with evidence shots to say which Set
    it was learned in. Everything else is general knowledge and stays.
    """
    stated = insight.scope.stated()
    if "bean_id" in stated:
        return True
    return not stated and bool(insight.evidence_shot_ids)

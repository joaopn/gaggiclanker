"""What the model is told before it asks anything: the Set the thread is about.

A scoped thread starts with the facts the person would otherwise have to type
out — which bag, which grinder, what the current recipe is, what the last few
shots did, and what this archive has already learned that applies. Everything
here is reachable through tools as well, and that is the point of putting it in
the prompt: a first turn that has to spend three tool calls learning what Set 3
is answers slowly and sometimes answers about the wrong Set.

Nothing unconfirmed goes in. Tier 3's rule
is that an insight reaches no prompt until a person has confirmed it, and the
chat is a prompt like any other — a model that proposes an insight and is then
handed it back next turn has manufactured its own evidence.
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository, set_attributes
from gaggiclanker.db.repos.sets import SetsRepository

__all__ = ["RECENT_SHOTS", "scope_block", "thread_title_from"]

#: How many recent shots the scope block carries. Enough to see a trend, few
#: enough that the block stays a page rather than a chapter; anything more is a
#: `query_shots` call away.
RECENT_SHOTS = 8


async def scope_block(db: Database, set_id: int | None) -> str:
    """The Set context as markdown, or an empty string for an unscoped thread."""
    if set_id is None:
        return ""
    sets = SetsRepository(db)
    row = await sets.get(set_id)
    if row is None:
        return ""

    lines = [
        "THIS CONVERSATION IS ABOUT ONE SET",
        "",
        f"Set {row.id}: {row.name}"
        + (f" — {row.bean_name}" if row.bean_name else "")
        + (f" on the {row.grinder_name}" if row.grinder_name else ""),
        f"Status: {row.status}{', currently active on the machine' if row.active else ''}. "
        f"{row.version_count} version(s), {row.shot_count} shot(s).",
    ]

    current = await sets.current_version(set_id)
    if current is not None:
        lines += ["", f"Current recipe (v{current.version_no}):", _recipe(current)]
        if current.intent:
            lines.append(f"Intent: {current.intent}")

    versions = await sets.versions(set_id)
    if len(versions) > 1:
        lines += ["", "Version history (oldest first):"]
        lines += [
            f"- v{version.version_no}: {_recipe(version)}"
            + (f" — {version.intent}" if version.intent else "")
            for version in versions
        ]

    shots = await db.fetch_all(
        """
        SELECT shot_id, started_at, set_version_no, duration_s, volume_g,
               execution_score, rating, balance, judgement_notes
          FROM v_shots
         WHERE set_id = ?
         ORDER BY COALESCE(started_at, '') DESC, shot_id DESC
         LIMIT ?
        """,
        (set_id, RECENT_SHOTS),
    )
    if shots:
        lines += ["", f"Last {len(shots)} shots (newest first):"]
        lines += [_shot_line(dict(zip(row.keys(), tuple(row), strict=True))) for row in shots]

    insights = await _insights(db, row)
    if insights:
        lines += ["", "Confirmed insights that apply here:"]
        lines += [f"- {insight.render()}" for insight in insights]

    lines += [
        "",
        "Those facts are a starting point, not the whole archive. Use the tools for "
        "anything else, and for anything you are about to quote a number from.",
    ]
    return "\n".join(lines)


def _recipe(version: Any) -> str:
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
    if version.target_temperature_c is not None:
        parts.append(f"{version.target_temperature_c:g} °C")
    if version.profile_label:
        parts.append(f"profile {version.profile_label}")
    return ", ".join(parts) if parts else "(nothing recorded)"


def _shot_line(row: dict[str, Any]) -> str:
    bits = [f"- shot {row['shot_id']}"]
    if row.get("started_at"):
        bits.append(str(row["started_at"])[:16].replace("T", " "))
    if row.get("set_version_no") is not None:
        bits.append(f"v{row['set_version_no']}")
    if row.get("duration_s"):
        bits.append(f"{float(row['duration_s']):.1f} s")
    if row.get("volume_g") is not None:
        bits.append(f"{float(row['volume_g']):.1f} g")
    if row.get("execution_score") is not None:
        bits.append(f"score {float(row['execution_score']):.0f}")
    if row.get("rating") is not None:
        bits.append(f"rated {row['rating']}/5")
    if row.get("balance"):
        bits.append(str(row["balance"]))
    line = " · ".join(bits)
    notes = str(row.get("judgement_notes") or "").strip()
    return f"{line} — {notes[:120]}" if notes else line


async def _insights(db: Database, row: Any) -> list[Any]:
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

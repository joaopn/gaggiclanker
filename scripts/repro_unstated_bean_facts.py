#!/usr/bin/env python
"""Reproduce: a bean field nobody filled in reaches the model as "not stated".

    uv run python scripts/repro_unstated_bean_facts.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A bean needs nothing but a name, so most of its fields are often empty. The
prompts rendered some of those empty fields anyway: the analysis context's BEAN
block wrote ``process: not stated`` and ``roast level: not stated``, the
starting-point context wrote the same for origin, process and roast level (and
``roast not stated, process not stated, origin not stated`` on a similar Set,
whose "why it is similar" line also called every field neither bean recorded
"different"),
and the ``list_beans`` tool handed the chat and MCP clients ``null`` and ``""``
for every unset field. A model reads a line like that as something known about
the coffee. An absent line says the same without inviting it to reason from a
placeholder.

The fix renders a bean field only when it holds a value. Checked here for a
bean with nothing but a name, through each of the three renderers.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

from gaggiclanker.analyzer.context import SetFacts, _render_set
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.starting.context import build_context, render_similar
from gaggiclanker.starting.similar import SimilarSet
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, registry
from gaggiclanker.tools.scope import ToolScope


async def main() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "repro.db")
        await db.connect()
        try:
            await run_migrations(db)
            bean = await BeansRepository(db).create(BeanWrite(name="Mystery"))

            analysis = _render_set(
                SetFacts(
                    set_id=1, set_name="A Set", version_id=1, version_no=1, bean_name="Mystery"
                )
            )
            if "not stated" in analysis:
                problems.append("analysis BEAN block: " + _lines_with(analysis, "not stated"))

            starting = (await build_context(db, bean_id=bean.id, as_of="2026-01-01")).render()
            if "not stated" in starting["bean_facts"]:
                problems.append(
                    "starting-point bean: " + _lines_with(starting["bean_facts"], "not stated")
                )

            similar = render_similar(
                [
                    SimilarSet(
                        set_id=1,
                        set_name="Old",
                        set_version_id=1,
                        version_no=1,
                        created_at="2026-01-01T00:00:00Z",
                        score=0.5,
                        attribute_score=0.5,
                        outcome_score=0.5,
                        bean_name="Old bean",
                    )
                ]
            )
            for needle in ("not stated", "different"):
                if needle in similar:
                    problems.append("similar Set: " + _lines_with(similar, needle))

            ctx = ToolContext(
                db=db,
                settings=SettingsService(SettingsRepository(db)),
                knowledge=KnowledgeService(db),
                scope=ToolScope(),
                caller="repro",
                permissions=CHAT_PERMISSIONS,
            )
            outcome = await registry.dispatch(ctx, "list_beans", {})
            item: dict[str, Any] = outcome.data["items"][0]
            empty = sorted(key for key, value in item.items() if value in (None, ""))
            if empty:
                problems.append(f"list_beans sends unset fields: {', '.join(empty)}")
        finally:
            await db.close()

    for problem in problems:
        print(f"BUG: {problem}")
    if problems:
        return 1
    print("OK: a bean with only a name reaches the model with only its name")
    return 0


def _lines_with(text: str, needle: str) -> str:
    return " | ".join(line.strip() for line in text.splitlines() if needle in line)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

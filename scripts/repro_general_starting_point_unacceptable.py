#!/usr/bin/env python
"""Reproduce: a starting point the General chat asks for can never be accepted.

    uv run python scripts/repro_general_starting_point_unacceptable.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The General chat could call ``starting_point``, which spends a provider call on
three recipes for a bag nobody has brewed and stores them as a run, and its
description told the model a person "accepts one in the UI". Nothing in the web
could: the New Set dialog accepts only the run it started itself (the id lives
in the dialog's state), the chat has no card for the tool, and there is no
route that lists runs. The person was sent to accept something they could not
find.

The fix retires the tool (and its read-back twin) from every conversation: a new
bag is designed in a Set of its own, which ends in a first recipe to accept.

Through the real app: the tools a provider is sent for a General conversation
decide it. The dispatch of the tool is printed too; after the fix it is refused
as unknown, and before it an empty archive makes it an error for the bean, so it
is shown rather than scored.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, registry
from gaggiclanker.tools.scope import ToolScope

RETIRED = ("starting_point", "get_starting_point")


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = EnvSettings(DATA_DIR=str(root / "data"), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
        app = create_app(env, web_dist=root / "no-dist")
        async with app.router.lifespan_context(app):
            scope = await ToolScope.resolve(app.state.db, None)
            offered = {
                schema["name"] for schema in registry.anthropic_schemas(CHAT_PERMISSIONS, scope)
            }
            ctx = app.state.chat.tool_context(scope=scope, run_id=None)
            outcome = await registry.dispatch(ctx, "starting_point", {"bean_id": 1})

    still_offered = [name for name in RETIRED if name in offered]
    print(f"General chat is offered: {still_offered or 'neither retired tool'}")
    print(f"calling starting_point anyway: {outcome.status}")
    if still_offered:
        print("BUG: the General chat can start a starting point nothing in the web accepts")
        return 1
    print("OK: no conversation can start a starting point it cannot hand to a person")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

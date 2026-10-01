"""Nothing but a person's sync request starts the profile pass, which is where a write can start.

The profile board's write phase is a step of the profiles pass, so who can start that pass is
who can make the app write to the machine without being asked. The engine has no timer and
reacts to no device event by mirroring; this pins it from the source, so a new caller (a timer,
an event handler, a boot step) fails here and not in somebody's machine.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "gaggiclanker"

#: Where each way of starting the pass may be called from. ``sync_profiles`` is the pass itself;
#: the engine's own loop is the only code that runs it in the app, and the route is the only
#: code that asks the loop for one.
ALLOWED = {
    "request_profile_sync": {"api/sync.py"},
    "sync_profiles": {"sync/engine.py"},
}


def callers(name: str) -> set[str]:
    pattern = re.compile(rf"\.{name}\(")
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        if pattern.search(path.read_text()):
            found.add(str(path.relative_to(PACKAGE)))
    return found


def test_only_the_sync_route_asks_for_a_profile_pass() -> None:
    assert callers("request_profile_sync") == ALLOWED["request_profile_sync"]


def test_only_the_engine_runs_a_profile_pass() -> None:
    assert callers("sync_profiles") == ALLOWED["sync_profiles"]


def test_the_engine_runs_it_only_from_its_poked_loop() -> None:
    source = (PACKAGE / "sync" / "engine.py").read_text()

    assert len(re.findall(r"\.sync_profiles\(", source)) == 1, "one caller inside the engine"
    loop = source[source.index("async def _profiles_loop") :]
    assert "await self._profile_poke.wait()" in loop.split("async def ", 2)[1]
    assert "self.sync_profiles(trigger=trigger)" in loop.split("async def ", 2)[1]

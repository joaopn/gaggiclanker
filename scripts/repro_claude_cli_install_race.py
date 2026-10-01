#!/usr/bin/env python
"""Reproduce: the install route's 202 answer can already say the install is done.

    uv run python scripts/repro_claude_cli_install_race.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

``POST /api/llm/claude-cli/install`` starts the download as a background task
and then reads the job back to build its 202 answer. The read awaits (npm's
tags, the image binary's version), so the task can run to the end first and the
answer says ``done``. The settings page writes the 202 answer into its query
cache and toasts only on a running -> done change in what it sees, so an
install that finished before the answer was read never gets its toast. It also
made the route's own test fail on one run in four.

Here the scheduling is forced rather than waited for: the manager's status
read lets the install finish before it reads the job, which is the worst case
the real event loop reaches under load. The 202 answer must be the job as it
started: running, with the target asked for.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gaggiclanker.llm import claude_cli  # noqa: E402
from gaggiclanker.settings import EnvSettings  # noqa: E402
from tests.conftest import running_app  # noqa: E402
from tests.llm.test_claude_cli import PACKAGE, FakeNpm, manager_for  # noqa: E402


async def main() -> int:
    claude_cli.platform_package = lambda: PACKAGE  # type: ignore[assignment]
    shutil.which = lambda name, *a, **k: "/image/claude"  # type: ignore[assignment]
    npm = FakeNpm()
    npm.publish("2.1.281")
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        env = EnvSettings(DATA_DIR=str(data_dir), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
        async with running_app(env) as (app, client):
            manager = manager_for(data_dir, npm, bundled="2.1.267")
            app.state.claude_cli = manager
            read = manager.status

            async def status_after_the_install(**kwargs: Any) -> dict[str, Any]:
                for _ in range(10_000):
                    if not manager.running:
                        break
                    await asyncio.sleep(0)
                return await read(**kwargs)

            manager.status = status_after_the_install  # type: ignore[method-assign]
            response: httpx.Response = await client.post(
                "/api/llm/claude-cli/install", json={"version": "latest"}
            )
            job = response.json()["data"]["job"]
            if manager.running:
                print("setup failed: the install never finished", file=sys.stderr)
                return 2
            asked = (response.status_code, job["state"], job["target"])
            if asked != (202, "running", "latest"):
                print(f"BUG: the 202 answer's job is {job}", file=sys.stderr)
                return 1
    print("ok: the 202 answer is the job as it started")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

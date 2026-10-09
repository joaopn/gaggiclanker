#!/usr/bin/env python
"""Reproduce: clicking away from the Chat page and back loses the open conversation.

    uv run python scripts/repro_chat_position_forgotten.py

Exits non-zero while the bug exists, zero when it is fixed. Needs Node on
``PATH`` (or at ``/workspace/.tools/node/bin``) and ``web/node_modules``
installed (``cd web && npm ci``).

The bug
-------

The open conversation lived only in the URL (``?thread=``), and the sidebar's
Chat link is a bare ``/chat``. A person reading a conversation who went to the
Shots page and clicked Chat again landed on the newest Set's badge with nothing
open, and had to find the conversation again; a badge they had opened without
a conversation was forgotten the same way.

The check is the Chat page's "coming back to it" block (Vitest): it opens a
conversation, goes to another page through a link, comes back through the
sidebar's ``/chat`` and expects the same conversation (and a picked badge) on
screen, a deleted one forgotten, and a link to still win over what was
remembered. A run in which the block does not exist counts as the bug.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
WEB_TEST = "src/pages/ChatPage.test.tsx"
WEB_BLOCK = "coming back to it"
WANTED = 4
# Where this project's container keeps Node; nothing is installed in the image.
CONTAINER_NODE = Path("/workspace/.tools/node/bin")


def web_check(env: dict[str, str]) -> tuple[int, int] | None:
    npx = shutil.which("npx", path=env["PATH"])
    if npx is None or not (WEB / "node_modules").is_dir():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [
                npx,
                "vitest",
                "run",
                WEB_TEST,
                "-t",
                WEB_BLOCK,
                "--passWithNoTests",
                "--reporter=default",
                "--reporter=json",
                f"--outputFile.json={report}",
            ],
            cwd=WEB,
            env=env,
            check=False,
        )
        if not report.is_file():
            return None
        results = json.loads(report.read_text())
    return results.get("numPassedTests", 0), results.get("numFailedTests", 0)


def main() -> int:
    env = dict(os.environ)
    if CONTAINER_NODE.is_dir():
        env["PATH"] = f"{CONTAINER_NODE}{os.pathsep}{env.get('PATH', '')}"
    web = web_check(env)
    if web is None:
        print("ERROR: could not run vitest; put Node on PATH and run `cd web && npm ci`")
        return 2
    passed, failed = web
    if passed < WANTED or failed > 0:
        print(
            f"FAIL: the Chat page forgets where the person was ({passed} passed, {failed} failed)"
        )
        return 1
    print(f"PASS: the Chat page reopens where the person was ({passed} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

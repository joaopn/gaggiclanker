#!/usr/bin/env python
"""Reproduce: a profile draft made in a Set's chat can never be pushed for that Set.

    uv run python scripts/repro_no_push_for_a_set.py

Exits non-zero while the bug exists, zero when it is fixed. Needs Node on
``PATH`` (or at ``/workspace/.tools/node/bin``) and ``web/node_modules``
installed (``cd web && npm ci``).

The bug
-------

A draft the agent proposes in a Set's conversation carries that Set and a
prediction. The server records both as the Set's next version when the draft is
pushed with the Set's id (``POST /api/profile-drafts/{id}/push`` with
``set_id``), and the draft card promised exactly that: "It is recorded on the
Set when you push this draft for that Set". But the card's only button pushed
with no Set, so:

- the Set never got the version the conversation argued for, and the prediction
  was lost;
- shots brewed on the pushed profile were not filed under the Set, since the
  Set still named the old profile;
- once pushed, the card said "Recorded on the Set when this was pushed for it."
  whatever the push had done.

The check is on the request the card sends and on what the card says after the
push, in a focused block of the card's Vitest file: pushing a Set's draft sends
that Set's id by default, the plain push stays available, and the pushed card
claims the prediction was recorded only when the archive says a version of its
Set was recorded by that push. This script runs that block and exits with its
status; a run in which the block does not exist counts as the bug.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
TEST = "src/components/drafts/DraftCard.test.tsx"
BLOCK = "pushing a Set's draft"
# Where this project's container keeps Node; nothing is installed in the image.
CONTAINER_NODE = Path("/workspace/.tools/node/bin")


def main() -> int:
    env = dict(os.environ)
    if CONTAINER_NODE.is_dir():
        env["PATH"] = f"{CONTAINER_NODE}{os.pathsep}{env.get('PATH', '')}"
    npx = shutil.which("npx", path=env["PATH"])
    if npx is None:
        print("ERROR: npx not found; put Node on PATH")
        return 2
    if not (WEB / "node_modules").is_dir():
        print("ERROR: web/node_modules is missing; run `cd web && npm ci`")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [
                npx,
                "vitest",
                "run",
                TEST,
                "-t",
                BLOCK,
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
            print("ERROR: vitest wrote no report")
            return 2
        results = json.loads(report.read_text())

    passed = results.get("numPassedTests", 0)
    failed = results.get("numFailedTests", 0)
    if passed == 0 or failed > 0:
        print(f"FAIL: a Set's draft is not pushed for its Set ({passed} passed, {failed} failed)")
        return 1
    print(f"PASS: a Set's draft is pushed for its Set by default ({passed} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

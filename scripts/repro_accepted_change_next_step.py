#!/usr/bin/env python
"""Reproduce: an accepted change says nothing about the machine, and the agent makes up a push step.

    uv run python scripts/repro_accepted_change_next_step.py

Exits non-zero while the bug exists, zero when it is fixed. Needs Node on
``PATH`` (or at ``/workspace/.tools/node/bin``) and ``web/node_modules``
installed (``cd web && npm ci``).

The bug
-------

A person accepted a grind-only change in a Set's conversation. Accepting records
a version and sends nothing to the machine, and for a grind change nothing needs
sending: the profile on the machine is the one the previous version already
brewed with, and the next shots on it are filed under the new version by
themselves. Nothing said so. The accepted card read only "Accepted as v2", the
Set prompt said nothing about what an accepted grind, dose or yield change means
for the machine, and the "Accepted:" turn did not name the change. Asked what to
do next, the agent invented a profile push, a staging queue and a step to "log
the shot against the version".

The check has three parts, and all of them have to pass:

- the card's "the next step after an accept" block (Vitest): the card names what
  the person does by hand, says there is nothing to push when the profile is
  unchanged, and the "Accepted:" turn names the change;
- the Set prompt tells the agent that an accepted non-profile change puts
  nothing on the machine (pytest);
- ``propose_set_version`` refuses a profile version the machine does not have,
  since nothing in the app can put an existing version there (pytest).

A run in which a check does not exist counts as the bug.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
WEB_TEST = "src/components/sets/ProposalCard.test.tsx"
WEB_BLOCK = "the next step after an accept"
PY_TESTS = ["tests/chat/test_prompts.py", "tests/tools/test_builtin.py"]
PY_SELECT = "accepted_change_needs_nothing_on_the_machine or profile_not_on_the_machine_is_refused"
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


def python_check() -> tuple[int, int]:
    """Passed and failed counts; the select matching nothing is (0, 0)."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-n",
                "0",
                "-p",
                "no:cacheprovider",
                *PY_TESTS,
                "-k",
                PY_SELECT,
                f"--junitxml={report}",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        print(run.stdout[-2000:])
        if not report.is_file():
            return 0, 0
        # pytest's own report, written a line above: nothing untrusted.
        suite = ElementTree.parse(report).getroot()  # noqa: S314
        suite = suite if suite.tag == "testsuite" else suite[0]
    total = int(suite.get("tests", 0))
    failed = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    skipped = int(suite.get("skipped", 0))
    return total - failed - skipped, failed


def main() -> int:
    env = dict(os.environ)
    if CONTAINER_NODE.is_dir():
        env["PATH"] = f"{CONTAINER_NODE}{os.pathsep}{env.get('PATH', '')}"
    web = web_check(env)
    if web is None:
        print("ERROR: could not run vitest; put Node on PATH and run `cd web && npm ci`")
        return 2
    py = python_check()
    ok = True
    for name, (passed, failed), wanted in (("card", web, 1), ("prompt and tool", py, 2)):
        if passed < wanted or failed > 0:
            print(f"FAIL: {name}: {passed} passed, {failed} failed")
            ok = False
    if not ok:
        return 1
    print(f"PASS: the accepted change names its next step ({web[0]} card, {py[0]} back-end checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Reproduce: the image does not build because the web stage cannot type-check its tests.

    uv run python scripts/repro_image_web_fixtures.py

Exits non-zero while the bug exists, zero when it is fixed. Needs Docker, and
builds only the Dockerfile's front-end stage (``--target web-build``).

The bug
-------

The front-end stage copies ``web/`` and runs ``npx tsc --noEmit``, which checks
every file under ``web/src``, tests included. The profile summary tests import
the shared profile fixtures from ``tests/fixtures/profiles/`` so the web and the
Python suite read the same JSON; outside the image that path exists, inside the
stage it does not, so ``docker compose build`` failed with TS2307 "Cannot find
module '../../../tests/fixtures/profiles/…'" while every local gate was green.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG = "gaggiclanker:repro-web-fixtures"


def main() -> int:
    build = subprocess.run(
        ["docker", "build", "--target", "web-build", "-q", "-t", TAG, str(ROOT)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        print(build.stderr.strip()[-3000:])
        print("FAIL: the image's front-end stage does not build")
        return 1
    print("PASS: the image's front-end stage type-checks and builds")
    return 0


if __name__ == "__main__":
    sys.exit(main())

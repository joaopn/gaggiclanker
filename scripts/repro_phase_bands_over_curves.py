#!/usr/bin/env python
"""Reproduce: the shot chart's phase bands are opaque boxes drawn over the curves.

    uv run python scripts/repro_phase_bands_over_curves.py

Exits non-zero while the bug exists, zero when it is fixed. Needs Node on
``PATH`` (or at ``/workspace/.tools/node/bin``) and ``web/node_modules``
installed (``cd web && npm ci``).

The bug
-------

The shot detail chart shades alternate phases (fill, soak, ramp, decline...)
with chartjs-plugin-annotation ``box`` annotations, meant as faint background.
Two things made every second band a solid block that hid the pressure, flow,
weight and temperature lines under it:

1. The band colour was read from the stylesheet token ``--muted``, which is an
   opaque surface colour in both palettes. Its translucent fallback only applies
   when no stylesheet is attached, which is exactly the test environment, so
   the suite never saw an opaque band.
2. The boxes set no ``drawTime``, and the plugin draws annotations
   ``afterDatasetsDraw`` by default, so the band was painted on top of the
   lines rather than behind them.

The canvas is opaque to jsdom and there is no headless browser, so the check is
on the configuration handed to Chart.js: with the real stylesheet's palettes in
effect, every phase box must draw ``beforeDatasetsDraw`` and have a translucent
fill, in the light and the dark palette. That lives in a focused Vitest file;
this script runs it and exits with its status.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
TEST = "src/components/charts/ShotChart.test.tsx"
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

    result = subprocess.run([npx, "vitest", "run", TEST], cwd=WEB, env=env, check=False)
    if result.returncode != 0:
        print("FAIL: phase bands are drawn over the curves or in an opaque colour")
        return 1
    print("PASS: phase bands draw behind the curves in a translucent colour")
    return 0


if __name__ == "__main__":
    sys.exit(main())

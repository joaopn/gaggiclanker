#!/usr/bin/env python
"""Reproduce: the Docker image cannot run its own default LLM provider.

    uv run python scripts/repro_image_has_claude_cli.py [--no-build] [--tag TAG]

Exits non-zero while the bug exists, zero when it is fixed. Needs Docker, and
builds the image from this checkout first (a few minutes the first time).

The bug
-------

``llmProvider`` defaults to ``claude_code``, which runs the Claude Code CLI as
a subprocess. The runtime stage of the Dockerfile carried Python and the venv
and nothing else, so on a fresh install every Validate, analysis and chat
answered "the Claude Code CLI (claude) was not found on PATH" — the default
provider could not work in the image the README tells people to run, however
the token was set.

This script builds the image and asks it, as the unprivileged user the app
runs as and with the PATH the app has, whether ``claude`` resolves and runs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The same lookup the provider's spawn does, then the cheapest command the
#: CLI answers without a network or a credential.
PROBE = (
    "import shutil, subprocess, sys; "
    "path = shutil.which('claude'); "
    "print('claude ->', path); "
    "sys.exit(1) if path is None else None; "
    "out = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=60); "
    "print(out.stdout.strip() or out.stderr.strip()); "
    "sys.exit(out.returncode)"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="gaggiclanker:repro-claude-cli")
    parser.add_argument("--no-build", action="store_true", help="probe an image already built")
    args = parser.parse_args()

    if not args.no_build:
        build = subprocess.run(["docker", "build", "-q", "-t", args.tag, str(ROOT)])
        if build.returncode != 0:
            print("FAIL: the image did not build")
            return 1

    # --entrypoint python and --user app: the entrypoint's setpriv needs a
    # capability compose grants and `docker run` does not, and what matters is
    # what the app user's PATH resolves, which this reproduces directly.
    probe = subprocess.run(
        ["docker", "run", "--rm", "--user", "app", "--entrypoint", "python", args.tag, "-c", PROBE],
        capture_output=True,
        text=True,
    )
    print(probe.stdout.strip())
    if probe.returncode != 0:
        print(probe.stderr.strip()[-2000:])
        print("FAIL: the image cannot run the Claude Code CLI its default provider needs")
        return 1
    print("PASS: the image runs the Claude Code CLI as the app user")
    return 0


if __name__ == "__main__":
    sys.exit(main())

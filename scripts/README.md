# scripts/

Standalone programs run with `uv run python scripts/<name>.py`. Not part of the
package and not imported by it.

**`repro_<bug>.py` — one per open bug.** The project's rule is that a bug gets
a reproduction before it gets a fix: the script exits
non-zero while the bug exists and zero once it is fixed, and it is verified on
the base branch as well as the fix branch. It stays in the tree after the fix as
the cheapest possible regression test.

**`profile_gate.py` — the four validation layers, over a file you have.**
The CI gate for profile drafts, and the five minutes before somebody puts a hand-written
profile on their machine. Schema and policy offline; add `--host` for the
save-then-load round trip and `--brew` to run a shot with it. Everything it
creates it deletes, including when a layer fails — but point it at the
simulator (`scripts/sim.sh serve`) rather than at your machine unless you mean
it.

```bash
uv run python scripts/profile_gate.py --offline tests/fixtures/profiles/*.json
uv run python scripts/profile_gate.py --host 127.0.0.1:8080 --brew my-profile.json
```

It is the one place in this repository that writes to a machine without the
`deviceWritesEnabled` switch, because it has no database to read the switch
from; it says so in its own docstring, and it is a developer tool run
deliberately from a shell against a host named on the command line.

Anything that needs a device or the firmware simulator says so at the top and
fails with a clear message when it is not reachable, rather than hanging.

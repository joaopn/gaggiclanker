# scripts/

Standalone programs run with `uv run python scripts/<name>.py`, and two shell
scripts. Not part of the package and not imported by it.

**`gates.sh` — the checks a branch owes before it is pushed, and no others.**
It takes the files changed since the merge base with `origin/dev`, plus anything
uncommitted, and picks the gates from them:

| Changed | Gates |
|---|---|
| `gaggiclanker/`, `tests/`, `pyproject.toml`, `uv.lock` | `ruff check .`, `ruff format --check .`, `mypy`, `pytest`, then `npm run gen:api`, which must leave `web/src/api/schema.d.ts` unchanged |
| `web/` (Markdown excepted) | `npm run check`, `npm run build` |
| `gaggiclanker/{device,sync,drafts,notes,cleanup}/`, `scripts/sim*`, `tests/simulator/` | `scripts/sim.sh test`, reported as owed and run only with `--sim` |
| anything else with Python in it | `ruff format --check` on those files |

```bash
scripts/gates.sh --dry-run                # the changed files and the plan
scripts/gates.sh                          # run it; stops at the first failure
scripts/gates.sh --keep-going             # run everything, then report
scripts/gates.sh --base origin/<parent>   # a stacked branch, against its parent
scripts/gates.sh --sim                    # include the simulator suite (~3 min)
```

Each gate prints its duration and the run ends with a summary table; the exit
status is non-zero if any gate failed. The web gates and `gen:api` need `npm`
on `PATH` (Node 22) and `npm ci` already run in `web/`; the script finds neither
on its own and says which is missing. A checkout that carries a history check
tool for git-ignored working notes gets that run first; a clone without one
skips it.

The Python suite runs in parallel (`pytest-xdist`, one worker per core), which
is what brings it from minutes to well under one on a many-core machine.
Starting the workers takes a few seconds, so a run over one file is quicker
with `-n 0`.

**`sim.sh` — the GaggiMate firmware simulator.** Builds the real display
firmware natively and runs the `-m simulator` tests against it; the header of
the script has the prerequisites. Those tests run one at a time (`-n 0`),
because there is one simulator and it brews one shot at a time.

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

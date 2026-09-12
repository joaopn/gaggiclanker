# scripts/

Standalone programs run with `uv run python scripts/<name>.py`. Not part of the
package and not imported by it.

**`repro_<bug>.py` — one per open bug.** The project's rule is that a bug gets
a reproduction before it gets a fix: the script exits
non-zero while the bug exists and zero once it is fixed, and it is verified on
the base branch as well as the fix branch. It stays in the tree after the fix as
the cheapest possible regression test.

Anything that needs a device or the firmware simulator says so at the top and
fails with a clear message when it is not reachable, rather than hanging.

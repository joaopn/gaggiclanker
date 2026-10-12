"""What an upgrade step is, and the rule that the registry lists them in order.

This module and everything else under ``gaggiclanker/db/upgrades`` imports nothing from the
rest of the application (``tests/test_upgrade_registry.py`` walks the imports): a step
is frozen code, and a live function that changes after the step has shipped must not change
what the step does to a database made before.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = ["Step", "validate_steps"]


@dataclass(frozen=True, slots=True)
class Step:
    """One upgrade: what a database at ``level - 1`` becomes at ``level``."""

    #: The level this step produces. The database it runs on is at ``level - 1``.
    level: int
    #: The fingerprint (``schema.fingerprint_of(describe(...))``) of the database it upgrades
    #: from, which is the frozen schema of the level below. A database that does not match is
    #: not one this step knows how to carry forward, and the upgrade refuses it.
    expects: str
    #: Whether the step rebuilds a table other tables refer to. Foreign keys are then switched
    #: off for the whole upgrade (they cannot change inside a transaction) and checked after.
    foreign_keys_off: bool
    #: Runs on a raw connection inside the upgrade's transaction and returns counts for the
    #: log. It never begins, commits or rolls back, and never sets ``user_version``.
    run: Callable[[sqlite3.Connection], dict[str, int]]


def validate_steps(steps: Sequence[Step]) -> tuple[Step, ...]:
    """The steps as a tuple, or ``ValueError`` unless their levels are exactly 1..N in order."""
    levels = [step.level for step in steps]
    if levels != list(range(1, len(steps) + 1)):
        raise ValueError(f"upgrade steps must be levels 1..N in order, got {levels}")
    return tuple(steps)

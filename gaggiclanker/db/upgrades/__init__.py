"""The upgrade steps that carry a database made by an older version forward.

``db/schema.sql`` is the schema as it is now, and it ends with ``PRAGMA user_version = N``,
where N is the number of steps registered here. A database starts at the level of the file
that built it. When the app starts on a database below the latest level it takes a backup and
runs the steps from that level up in one transaction (``db/upgrade.py``).

**Adding a step** (a schema change ships its step in the same commit):

1. Copy the current ``db/schema.sql`` to ``schemas/level_<N>.sql``, where N is the level it
   has now, if that file is not there yet. It is the schema at that level for ever.
2. Write ``level_<N+1>.py``: a module with one ``STEP = Step(...)`` (see ``step.py``). It
   imports only from this package, copies into itself whatever it needs from live code, and
   uses raw SQL on the connection it is given.
3. Register it at the end of ``STEPS`` below, edit ``schema.sql`` and raise its
   ``PRAGMA user_version`` to N+1.
4. Set the step's ``expects`` to the fingerprint of ``schemas/level_<N>.sql``
   (``tests/test_upgrade_registry.py`` prints the value it wants), and test the step by
   building a database from the frozen file, filling it, and running the upgrade.

Frozen schemas are pinned by checksum, so an edit to one fails a test.
"""

from __future__ import annotations

from pathlib import Path

from gaggiclanker.db.upgrades.level_001 import STEP as _LEVEL_1
from gaggiclanker.db.upgrades.level_002 import STEP as _LEVEL_2
from gaggiclanker.db.upgrades.level_003 import STEP as _LEVEL_3
from gaggiclanker.db.upgrades.level_004 import STEP as _LEVEL_4
from gaggiclanker.db.upgrades.level_005 import STEP as _LEVEL_5
from gaggiclanker.db.upgrades.step import Step, validate_steps

__all__ = [
    "LATEST",
    "SCHEMAS_DIR",
    "STEPS",
    "Step",
    "frozen_schema_path",
    "frozen_schema_sql",
    "validate_steps",
]

#: Registered in order; the step at index N-1 produces level N.
STEPS: tuple[Step, ...] = validate_steps((_LEVEL_1, _LEVEL_2, _LEVEL_3, _LEVEL_4, _LEVEL_5))

#: The level a database ends at: what ``schema.sql`` says, which a test ties to ``STEPS``.
LATEST = len(STEPS)

SCHEMAS_DIR = Path(__file__).parent / "schemas"


def frozen_schema_path(level: int) -> Path:
    """The file holding ``schema.sql`` as it was at ``level``."""
    return SCHEMAS_DIR / f"level_{level:03d}.sql"


def frozen_schema_sql(level: int) -> str:
    return frozen_schema_path(level).read_text(encoding="utf-8")

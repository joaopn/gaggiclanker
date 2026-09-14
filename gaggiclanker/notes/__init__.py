"""Writing the archive's verdict back onto the machine's own notes card.

The read direction has existed since the sync engine: the device's `/h/<id>.json` is
mirrored into `device_shot_notes` and seeds a judgement exactly once. This is the
other direction, and it never happens on its own: a person selects judgements on
the Sync page and sends them, behind `deviceWritesEnabled`. Saving a judgement
does not contact the machine.
"""

from __future__ import annotations

__all__: list[str] = []

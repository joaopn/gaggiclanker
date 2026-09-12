"""Writing the archive's verdict back onto the machine's own notes card.

The read direction has existed since the sync engine: the device's `/h/<id>.json` is
mirrored into `device_shot_notes` and seeds a judgement exactly once. This is the
other direction, and it is opt-in twice over — `deviceWritesEnabled` for writing
to a machine at all, `notesWritebackEnabled` for this in particular.
"""

from __future__ import annotations

__all__: list[str] = []

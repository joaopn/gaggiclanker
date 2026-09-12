"""The per-shot analyzer: one structured LLM call, and what surrounds it.

Four modules, read in the order the work happens:

* :mod:`.style` — what kind of shot this profile brews (classic, turbo, lever…).
* :mod:`.context` — everything the model is told, assembled deterministically.
* :mod:`.models` — the shape the model must answer in.
* :mod:`.service` — the run itself: a row, a call, an outcome, and the
  suggestions that come out of it.

There is no agent loop here on purpose:
the diagnostics are already deterministic, so the model is asked one question
with everything in front of it rather than given tools to go and look.
"""

from __future__ import annotations

__all__: list[str] = []

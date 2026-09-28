"""Review: a passive model reading of one shot, started by a person.

Four modules, read in the order the work happens:

* :mod:`.style` — what kind of shot this profile brews (classic, turbo, lever…).
* :mod:`.context` — everything the model is told, assembled deterministically
  from the shot alone: never the person's judgement, the Set or another shot.
* :mod:`.models` — the shape the model must answer in: a blind taste
  prediction, a description and a one-sentence summary.
* :mod:`.service` — the run itself: a row, a call, an outcome.

A review writes those three things to the shot and does nothing else. It
proposes no change and asks nothing; what it writes is shot information that
the chat reads like any other, and everything active is the chat's and the
person's. There is no agent loop here on purpose: the diagnostics are already
deterministic, so the model is asked one question with everything in front of
it rather than given tools to go and look.
"""

from __future__ import annotations

__all__: list[str] = []

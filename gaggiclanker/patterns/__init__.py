"""Find patterns across Sets: one pressed call that proposes general insights.

Three modules, in the order the work happens:

* :mod:`.context`: everything the model is told, assembled deterministically from the
  confirmed insights of every Set, the general insights and the proposals a person
  declined.
* :mod:`.models`: the shape the model must answer in, and the post-filter that decides which
  of its proposals may be shown.
* :mod:`.service`: the run itself: a row, a call, an outcome.

It is closer to Review than to a conversation: no tools, one structured answer, and only a
person's button starts it. What it writes are proposals; an insight is written (and the Set
insights it was derived from are deleted) only when a person approves one.
"""

from __future__ import annotations

__all__: list[str] = []

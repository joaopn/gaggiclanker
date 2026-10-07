"""Review: a model's review of one shot, started by a person; its claims are kept unless rejected.

Seven modules, read in the order the work happens:

* :mod:`.style` — what kind of shot this profile brews (classic, turbo, lever…).
* :mod:`.context` — everything the model is told, assembled deterministically: the shot
  with its checks first, the Set version's recipe and prediction, the confirmed signature's
  free-text expectations, the profile, the style, the rules and excerpts. Never the person's
  judgement, the machine's note, the label, another shot or an earlier review.
* :mod:`.models` — the shape the model must answer in, built per call so that its enums are
  this shot's phase names and this review's expectation ids: claims tied to windows, an
  answer to each free-text expectation, a stance on the prediction when there is one. No taste,
  no advice, no proposal.
* :mod:`.evidence` — the numbers behind every claim, worked out by the server with the
  metric language's own evaluator, and the window of each claim resolved to seconds.
* :mod:`.service` — the run itself: a row, a call, the evidence, an outcome; and a person's
  answers to the claims.
* :mod:`.reading` — the Curve check and the review blocks, worked out whenever a shot
  is read, the same for the person and for the chat (everything not rejected).

A review writes its claims and does nothing else. Every claim is kept (`confirmed`) until a
person rejects it, and what a chat is told is every claim not rejected. It proposes no change and
asks nothing, and everything active is the chat's and the person's. There is no agent loop here on
purpose: the diagnostics are already deterministic and the evaluator is the server's, so the model
is asked one question with everything in front of it rather than given tools to go and look.
"""

from __future__ import annotations

__all__: list[str] = []

"""Profile drafts: propose a profile, validate it four ways, put it on the machine.

The four layers are described in `docs/safety-layers.md` and they are
spread across three packages on purpose — a layer that lives next to the thing it
guards is a layer somebody can turn off by editing one file:

1. **schema** — :class:`gaggiclanker.domain.models.Profile`, strict pydantic;
2. **policy** — :mod:`gaggiclanker.domain.profile_policy`, narrower than the
   firmware and tunable from Settings;
3. **round trip** — :meth:`.service.ProfileDraftService.push`, which reads back
   what it wrote and compares canonical JSON;
4. **simulator** — `tests/simulator/test_profile_push.py`, in CI.

Plus the switch: :class:`.gate.SettingsWriteGate`, which is what makes all five
of the device client's write methods refuse by default.
"""

from __future__ import annotations

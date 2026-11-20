"""Cup flow: how fast coffee reached the cup, as the scale saw it.

The firmware logs the scale's flow per sample as ``vf`` (g/s), averaged from the
same weight it logs as ``v``. A scale cannot lose coffee into the machine, so a
negative reading is never cup flow: it is the old tare glitch that older
firmware logged as a clamp at -20 g/s. Every reader of ``vf`` for an analysis
takes it through :func:`cup_flow`, so the floor is one rule.

Puck flow (``pf``) is a different thing: the pump model's estimate of water
through the puck. Nothing here reads it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "FIRST_DRIP_RISE_G",
    "cup_first_drip_index",
    "cup_flow",
    "mean_cup_flow",
    "shot_has_scale",
]

#: The weight that has to arrive in the cup, above the first reading, before it counts as
#: coffee: the scale's noise and a settling cup stay under it.
FIRST_DRIP_RISE_G = 0.5


def cup_flow(sample: Mapping[str, float]) -> float | None:
    """The sample's cup flow in g/s, never below zero; ``None`` when it was not recorded."""
    value = sample.get("vf")
    if value is None:
        return None
    return max(float(value), 0.0)


def shot_has_scale(weights: Iterable[float | None]) -> bool:
    """Whether a shot had a scale: any brew-phase weight above zero.

    A board without a scale logs ``v`` and ``vf`` as zeros, so up to version 7 the flag has to be
    read from the weights themselves. A version 8 shot records a weight only where a scale
    answered and has none (``None``) elsewhere, which reads as no weight.
    """
    return any((weight or 0.0) > 0 for weight in weights)


def mean_cup_flow(samples: Iterable[Mapping[str, float]]) -> float | None:
    """The mean cup flow over the samples that recorded it; ``None`` when none did."""
    flows = [flow for sample in samples if (flow := cup_flow(sample)) is not None]
    return sum(flows) / len(flows) if flows else None


def cup_first_drip_index(samples: Sequence[Mapping[str, float]]) -> int | None:
    """The first sample whose weight is at least the first reading plus half a gram.

    That is when coffee first reached the cup. The caller decides whether the shot had a scale;
    without weights, or when the weight never rose that far, ``None``. So is it when the first
    recorded weight is no baseline: a reading that is not the first sample's and is already at or
    above :data:`FIRST_DRIP_RISE_G` says the scale came up (only firmware 1.10 can log a shot that
    way) after coffee was in the cup, and a drip counted from it would be wrong, not just late.
    A first reading under that (the cup still empty) is a baseline whenever the scale answered.
    """
    first: float | None = None
    for index, sample in enumerate(samples):
        weight = sample.get("v")
        if weight is None:
            continue
        if first is None:
            if index > 0 and weight >= FIRST_DRIP_RISE_G:
                return None
            first = float(weight)
        if weight >= first + FIRST_DRIP_RISE_G:
            return index
    return None

"""Which of a shot's weights are a scale's reading.

Up to version 7 the firmware logged ``v`` and ``vf`` only from a scale: with none
connected they are zeros, and "had a scale" is read from the weights themselves (some
brew-phase weight above zero, ``cup_flow.shot_has_scale``). Firmware 1.10 (version 8)
logs the *active* weight instead. With no physical scale that is the flow estimator's
guess, and a scale that stops reporting mid-shot hands over to the estimator, which
carries on from the last real reading (``Controller::onVolumetricMeasurement``). The
flow column follows the same source: the puck flow when the weight is the estimate
(``ShotHistoryPlugin::selectWeightFlow``). Such a weight is the machine's estimate and
not the cup's, and an analysis built on it would be built on a model.

The firmware says which is which, per sample, in ``si`` bit 0x0100 ("the active
hardware or Bluetooth scale is healthy", ``getSystemInfo``); its own shot analyzer reads
the bit the same way. So for version 8 a sample's ``v`` and ``vf`` are a scale reading
only when that sample has the bit, and are **not recorded** otherwise: this module reads
them as absent (``None``, never a zero, which would be a reading), so a shot with no bit has no
weights, which keeps every reader of "has a scale" (the brew-phase rule, its SQL twin, cup flow,
the warnings, the spread, the curves) right without a second rule. The estimated weight ``ev``
is its own channel and stays.

The header's ``finalWeight`` (the highest spike-filtered active weight) and the machine's index
entry, which copies it into ``volume`` (a dose out typed on the machine's notes card replaces
it), are the estimate too when no scale answered, or when the scale was lost before the end and
the estimator carried on past its last reading. :func:`header_weight` says whether the header
counts, and what of the index volume may be believed.

The bytes are never edited: the codec and the importer's re-encoding keep what the machine
wrote, and this is applied where a parsed shot becomes the samples the app stores and reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from gaggiclanker.domain.models import (
    SI_ACTIVE_SCALE_CONNECTED,
    SI_BLUETOOTH_SCALE_CONNECTED,
    SI_EXTENDED_RECORDING,
    Sample,
)

__all__ = [
    "ACTIVE_SCALE_VERSION",
    "HeaderWeight",
    "header_weight",
    "kept_samples",
    "recorded_samples",
    "scale_answered",
    "scale_connected_flag",
    "scale_lost_before_the_end",
    "scale_reading",
    "weights_follow_active_scale",
]

#: The first version that logs the active weight (firmware 1.10).
ACTIVE_SCALE_VERSION = 8

#: Tenths of a gram: the firmware stores every weight as an integer of these, in the shot's
#: header and in the index entry alike, and two weights are the same weight when those integers
#: are (a float round trip is not a comparison).
WEIGHT_UNITS_PER_G = 10


class _HasSystemInfo(Protocol):
    """A parsed sample or a stored row: all the rules below read of it is its `si`."""

    @property
    def si(self) -> int | None: ...


def _brew_samples[T: _HasSystemInfo](samples: Sequence[T]) -> list[T]:
    """The samples of the brew: those without the extended-recording bit (``si`` 0x0010).

    The recording that follows the stop runs with the pump off, so what a scale does there (comes
    up, drops out) says nothing about the weights the brew was made of.
    """
    return [sample for sample in samples if not (sample.si or 0) & SI_EXTENDED_RECORDING]


def weights_follow_active_scale(version: int) -> bool:
    """Whether this version's weights may be the machine's estimate (so the ``si`` bit decides)."""
    return version >= ACTIVE_SCALE_VERSION


def scale_reading(version: int, si: int | None) -> bool:
    """Whether a sample's ``v`` and ``vf`` are a scale's reading, by its ``si``.

    Before version 8 they always were (or zeros). From version 8 only when the sample
    carries the active-scale bit; a sample whose ``si`` was not recorded cannot say so.
    """
    if not weights_follow_active_scale(version):
        return True
    return si is not None and bool(si & SI_ACTIVE_SCALE_CONNECTED)


def kept_samples(samples: Sequence[Sample]) -> list[Sample]:
    """The samples a shot keeps, in time order: a sample with no `t` cannot be stored, and of two
    samples at one moment (a torn write, or two recordings inside one tick) the last wins, which
    is how the firmware's own parser walks the file. The one filter behind the stored rows, the
    scale flag, whether a scale answered and whether the header's weight counts, so those cannot
    disagree about which samples there are."""
    by_time: dict[int, Sample] = {}
    for sample in samples:
        if sample.t is not None:
            by_time[sample.t] = sample
    return [by_time[t] for t in sorted(by_time)]


def scale_answered(version: int, samples: Sequence[_HasSystemInfo]) -> bool:
    """Whether a scale answered during the brew: some brew sample carries the active-scale bit.

    Version 8 only (older versions have no such bit; a scale there is read from the weights).
    A scale that comes up only in the extended recording after the stop did not weigh the brew,
    so the shot had none.
    """
    return weights_follow_active_scale(version) and any(
        scale_reading(version, sample.si) for sample in _brew_samples(samples)
    )


def recorded_samples(version: int, samples: Sequence[Sample]) -> list[Sample]:
    """The samples as the app reads them: a weight that is not a scale's reading is absent.

    Version 7 and below come back as they are (the same objects). For version 8 a sample with
    no active-scale bit has ``v`` and ``vf`` set to ``None``, "not recorded", and never to 0:
    a zero is a reading (an empty scale, a cup lifted), and a scale lost mid-shot would show as
    the weight falling to nothing. Every other field is untouched. A shot whose scale never
    answered during the brew has no weights at all, whatever the extended recording after it
    shows, which is what a shot without a scale has always been here.
    """
    if not weights_follow_active_scale(version):
        return list(samples)
    answered = scale_answered(version, kept_samples(samples))
    return [
        sample
        if answered and scale_reading(version, sample.si)
        else sample.model_copy(update={"v": None, "vf": None})
        for sample in samples
    ]


def scale_connected_flag(version: int, samples: Sequence[_HasSystemInfo]) -> bool:
    """The shot's ``scale_connected`` column: some sample says a scale was connected.

    Version 8 reads the active-scale bit (0x0100) over the brew (:func:`scale_answered`). Older
    versions read the Bluetooth bit (0x0004), the only scale connection they recorded. It is the
    firmware's word for the connection, not the answer to "had a scale" (the weights are). Given
    the stored rows, it is what the rows say.
    """
    if weights_follow_active_scale(version):
        return scale_answered(version, samples)
    return any(
        sample.si is not None and sample.si & SI_BLUETOOTH_SCALE_CONNECTED for sample in samples
    )


def scale_lost_before_the_end(version: int, samples: Sequence[Sample]) -> bool:
    """Whether a scale answered at some point and the brew's last weighed sample is not its.

    Version 8 only, and over the brew's samples: the extended recording that follows the stop
    (``si`` bit 0x0010) runs with the pump off and the estimator barely moves there, so a scale
    lost in that tail leaves the header's weight a real one. Some brew sample carries the
    active-scale bit, but the last brew sample that holds a weight above zero (as the machine
    wrote it) does not: the scale stopped reporting and the estimator carried on from the last
    reading. A bit that is set throughout, or that only appears near the end, is not lost; a
    shot whose scale never answered is not "lost", it had none; weights of zero after the last
    weighed sample say nothing either way.
    """
    if not weights_follow_active_scale(version):
        return False
    kept = kept_samples(samples)
    if not scale_answered(version, kept):
        return False
    brew = _brew_samples(kept)
    last_weighed = next((sample for sample in reversed(brew) if (sample.v or 0.0) > 0), None)
    return last_weighed is not None and not scale_reading(version, last_weighed.si)


@dataclass(frozen=True, slots=True)
class HeaderWeight:
    """What a shot's header weight is, and so what of the machine's index volume is believable."""

    #: The header's ``finalWeight`` is a scale's reading (always for version 7 and below).
    counts: bool
    #: The header's ``finalWeight`` as the file states it, in grams.
    final_weight_g: float | None

    def index_volume(self, volume_g: float | None) -> float | None:
        """The index entry's volume as the app keeps it.

        The machine copies the header's weight into the index entry, so when the header is the
        estimate the entry's volume is too, unless a person typed a dose out on the machine's
        notes card (the firmware then writes that figure into the entry). The two are told apart
        by comparing them as the firmware's integers: a volume that is the header's weight is
        absent; any other is the person's and is kept. A typed figure that happens to equal the
        header's cannot be told apart from it and reads as absent. When the header's weight is
        itself not a scale's and cannot be read (an incomplete copy, or a header of zero), nothing
        can be told apart and the volume is absent too; a figure someone typed still reaches the
        app through the notes' dose out and the judgement.
        """
        if volume_g is None or self.counts:
            return volume_g
        if self.final_weight_g is None:
            return None
        if _units(volume_g) == _units(self.final_weight_g):
            return None
        return volume_g


def _units(weight_g: float) -> int:
    return round(weight_g * WEIGHT_UNITS_PER_G)


def header_weight(
    version: int, samples: Sequence[Sample], final_weight_g: float | None
) -> HeaderWeight:
    """Whether the header's final weight is a scale's: see the module note and the two rules.

    Version 8 counts it only when a scale answered during the brew and was not lost before the
    end. Version 7 and below always count it.
    """
    if not weights_follow_active_scale(version):
        return HeaderWeight(counts=True, final_weight_g=final_weight_g)
    answered = scale_answered(version, kept_samples(samples))
    counts = answered and not scale_lost_before_the_end(version, samples)
    return HeaderWeight(counts=counts, final_weight_g=final_weight_g)

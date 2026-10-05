"""The one brew-ratio rule that the catalogue, the trends and the starting point share."""

from __future__ import annotations

from gaggiclanker.domain.ratio import brew_ratio


def test_the_typed_dose_wins_over_the_versions_and_the_typed_yield_over_the_scale() -> None:
    assert (
        brew_ratio(judged_dose_g=18.0, version_dose_g=20.0, judged_yield_g=45.0, scale_yield_g=40.0)
        == 2.5
    )


def test_the_version_dose_and_the_scale_yield_are_the_fallbacks() -> None:
    assert (
        brew_ratio(judged_dose_g=None, version_dose_g=20.0, judged_yield_g=None, scale_yield_g=40.0)
        == 2.0
    )


def test_no_dose_or_no_yield_is_no_ratio() -> None:
    assert (
        brew_ratio(judged_dose_g=None, version_dose_g=None, judged_yield_g=45.0, scale_yield_g=40.0)
        is None
    )
    assert (
        brew_ratio(judged_dose_g=18.0, version_dose_g=None, judged_yield_g=None, scale_yield_g=0.0)
        is None
    )
    assert (
        brew_ratio(judged_dose_g=18.0, version_dose_g=None, judged_yield_g=None, scale_yield_g=-1.0)
        is None
    )

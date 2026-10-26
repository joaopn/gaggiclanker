"""The derived machine name and how a machine's name is read back."""

from __future__ import annotations

import pytest

from gaggiclanker.db.repos.shots import brewed_as
from gaggiclanker.domain.machine_names import (
    app_suffix,
    is_app_name,
    is_app_name_in_shot_header,
    machine_name,
    name_taken_from,
)
from gaggiclanker.domain.slog import shot_header_name


def test_the_machine_name_is_the_label_and_the_versions_label_when_there_is_one() -> None:
    assert machine_name("Alma Lever", "v1.3") == "Alma Lever v1.3"
    assert machine_name("Alma Lever", "v2") == "Alma Lever v2"
    assert machine_name("Alma Lever", None) == "Alma Lever"


@pytest.mark.parametrize("version", ["v1", "v1.3", "v12.40"])
def test_the_profiles_own_name_with_a_well_formed_version_is_what_the_app_wrote(
    version: str,
) -> None:
    assert is_app_name(f"Alma {version}", "Alma") and app_suffix(f"Alma {version}", "Alma")
    assert is_app_name("Alma", "Alma") and app_suffix("Alma", "Alma") == ""


@pytest.mark.parametrize(
    "seen", ["Alma v", "Alma v1.", "Alma 1.3", "Alma v1.3 x", "Almav1", "alma v1", "Alma  v1"]
)
def test_anything_else_is_not_the_apps_name(seen: str) -> None:
    assert not is_app_name(seen, "Alma")


def test_only_the_suffix_the_app_expected_or_recorded_is_cut_from_a_rename() -> None:
    assert name_taken_from("Y v1.4", "X", "X v1.4", "X v1.3") == "Y"
    assert name_taken_from("Y v1.3", "X", "X v1.4", "X v1.3") == "Y"
    assert name_taken_from("Y", "X", "X v1.4", "X v1.4") == "Y"
    # a suffix the app never wrote is the person's: never a generic pattern
    assert name_taken_from("Y v1.9", "X", "X v1.4", "X v1.3") == "Y v1.9"
    assert name_taken_from("Foo v1.3", "X", "X", "X") == "Foo v1.3"
    # nothing is left of the name: it stays as it is
    assert name_taken_from(" v1.4", "X", "X v1.4", None) == " v1.4"
    # a profile with no Recipe has no suffix to cut
    assert name_taken_from("Y v1", "X", "X", "X") == "Y v1"


def test_a_shot_filed_under_a_recipe_and_brewed_under_the_name_and_a_version_is_not_brewed_as() -> (
    None
):
    def seen(label: str, on_device: str) -> str | None:
        return brewed_as(label, on_device, filed_under_recipe=True)

    assert seen("Alma Lever", "Alma Lever v1.3") is None
    assert seen("Alma Lever", "Alma Lever") is None
    assert seen("Alma Lever", "Alma Lever v9") is None
    assert seen("Alma Lever", "Other v1.3") == "Other v1.3"
    assert seen("Alma Lever", "Alma Lever v1.3 x") == "Alma Lever v1.3 x"
    assert seen("Alma Lever", "Alma Lever 1.3") == "Alma Lever 1.3"


def test_a_shot_in_no_recipe_keeps_the_rule_it_always_had() -> None:
    """The app adds no version to a profile no Recipe brews, so a suffix on a shot's name is a
    different name there ("Adaptive v2" is the profile; "Adaptive v2 v2" is something else)."""
    assert brewed_as("Alma Lever", "Alma Lever v1.3") == "Alma Lever v1.3"
    assert brewed_as("Adaptive v2", "Adaptive v2 v2") == "Adaptive v2 v2"
    assert brewed_as("Alma Lever", "Alma Lever") is None
    assert brewed_as("a" * 52, shot_header_name("a" * 52)) is None
    assert brewed_as("Alma Lever", "Alma Lever v1.3", filed_under_recipe=False) == "Alma Lever v1.3"


@pytest.mark.parametrize("version", ["v1", "v1.4", "v10.12", "v123.456"])
@pytest.mark.parametrize("length", [30, 38, 41, 42, 43, 44, 45, 46, 47, 52])
def test_the_suffix_cut_at_47_bytes_by_the_shot_header_is_still_the_same_profile(
    length: int, version: str
) -> None:
    label = "n" * length
    on_device = shot_header_name(f"{label} {version}")
    assert brewed_as(label, on_device, filed_under_recipe=True) is None
    # (a name over 47 bytes is cut inside itself, which the plain name rule already covers)
    assert is_app_name_in_shot_header(on_device, label) == (length <= 47)


def test_a_cut_through_a_character_is_the_same_profile_too() -> None:
    label = "a" * 43 + "é"  # 45 bytes
    on_device = f"{label} v1.4".encode()[:47].decode("utf-8", errors="replace")
    assert brewed_as(label, on_device, filed_under_recipe=True) is None
    label = "é" * 22  # 44 bytes; "é" cut in half by the 47-byte limit
    on_device = f"{label} v1.4".encode()[:47].decode("utf-8", errors="replace")
    assert brewed_as(label, on_device, filed_under_recipe=True) is None


def test_a_short_name_that_only_starts_like_a_version_is_a_different_name() -> None:
    assert brewed_as("Alma", "Alma v", filed_under_recipe=True) == "Alma v"
    assert brewed_as("Alma", "Alma v1.", filed_under_recipe=True) == "Alma v1."

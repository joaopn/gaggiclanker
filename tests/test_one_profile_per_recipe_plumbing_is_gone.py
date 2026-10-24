"""The plumbing that existed only because several Recipes could share a profile is gone.

A Recipe has one profile and a profile has at most one Recipe, so nothing needs to decide whether
two profile versions are "the same profile", to refuse a shot as ambiguous, to record a Recipe
version when the sync finds a file, or to remember which file a Recipe's version was pushed as. The
names below belonged to those pieces; a new use of one is the piece coming back. The upgrade steps
are frozen code and keep the old schema's names (they describe the database they read).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The names that went, by where they lived.
GONE = [
    r"\bprofile_identity\b",
    r"\bprofile_entry_(id|sql)\b",
    r"\bsame_profile\b",
    r"\bsameProfile\b",
    r"\bProfileRef\b",
    r"\bchange_is_major\b",
    r"\bVersionPath\b",
    r"\bdefault_major\b",
    r"\bmajor_by_default\b",
    r"\bnew_profile_only\b",
    r"\breusable_version_ids\b",
    r"\balready_a_profiles_brew\b",
    r"\bfind_version_named_by_a_recipe\b",
    r"\bfind_version_named_by_a_set\b",
    r"\bsets_currently_using\b",
    r"\bdevice_profile_of\b",
    r"\bcurrent_device_profile\b",
    r"\bclear_pushed_device_profile\b",
    r"\brestore_pushed_device_profile\b",
    r"\bset_versions\.pushed_device_profile_id\b",
    r"\bpending_recipe_id\b",
    r"\bpending_set_id\b",
    r"\bpending_major\b",
    r"\bback_from_recipe_version_id\b",
    r"\bback_from_set_version_id\b",
    r"\bcleared_recipe_version_ids\b",
    r"\bcleared_set_version_ids\b",
    r"\battach_to_recipe\b",
    r"\battach_to_set\b",
    r"\b_record_on_recipe\b",
    r"\b_record_on_set\b",
    r"\bcurrent_brews\b",
    r"\bsets_brewing\b",
    r"\bversions_naming_profile\b",
    r"\bProfilePut\b|\bBoardPut\.recipe_id\b",
    r"\bProfilePut\b|\bBoardPut\.set_id\b",
    r">Any profile<",
    r"takes every shot the machine pulls",
    r"approve-proposal-without-set",
    r"Approve without recording",
]

#: Where "Any profile" is still the right words: the Shots page's filter by profile (it filters
#: shots, it does not give a Recipe a profile).
ALLOWED = {("web/src/components/shots/ShotFilters.tsx", r">Any profile<")}

SUFFIXES = {".py", ".ts", ".tsx", ".yaml", ".sql", ".md"}


def _files() -> list[Path]:
    # The README and the architecture and safety notes describe the same pieces.
    found: list[Path] = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    for base in ("gaggiclanker", "web/src", "tests"):
        for path in sorted((ROOT / base).rglob("*")):
            if (
                path.suffix not in SUFFIXES
                or "node_modules" in path.parts
                or "upgrades" in path.parts
                or path.name in {"schema.d.ts", Path(__file__).name}
                or "test_upgrade_level" in path.name
            ):
                continue
            found.append(path)
    return found


def test_the_names_are_gone() -> None:
    """Every file is read once and checked against every name; the hits are listed per name."""
    compiled = [(pattern, re.compile(pattern)) for pattern in GONE]
    hits: dict[str, list[str]] = {pattern: [] for pattern in GONE}
    for path in _files():
        rel = str(path.relative_to(ROOT))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, rx in compiled:
                if rx.search(line) and (rel, pattern) not in ALLOWED:
                    hits[pattern].append(f"{rel}:{number}: {line.strip()[:100]}")
    assert not {pattern: found for pattern, found in hits.items() if found}

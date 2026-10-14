"""Every document built from a stored version goes through ``document_for``.

A version's stored document carries the name it was first stored with, which may be an older name
of its profile (or another profile's), so a document built from it for the machine, the editor, an
export or the agent must be given the owner's current name. The importer is the other place a
document becomes a ``Profile``: a file's own label is the name.
"""

from __future__ import annotations

import ast
from pathlib import Path

from gaggiclanker.db.repos.profiles import ProfileVersionRow, document_for, stored_document

ROOT = Path(__file__).resolve().parents[1] / "gaggiclanker"
#: Where a Profile may be validated from a version's ``profile`` field without the helper.
ALLOWED = {ROOT / "db" / "repos" / "profiles.py"}


def version(label: str) -> ProfileVersionRow:
    return ProfileVersionRow.model_validate(
        {
            "id": 1,
            "content_hash": "x",
            "label": label,
            "type": "pro",
            "json": {
                "label": label,
                "type": "pro",
                "temperature": 93.0,
                "phases": [
                    {
                        "name": "Extraction",
                        "phase": "brew",
                        "valve": 1,
                        "duration": 30,
                        "pump": {"target": "pressure", "pressure": 9, "flow": 0},
                        "targets": [],
                    }
                ],
            },
            "source": "device",
            "created_at": "2026-10-13T00:00:00.000Z",
        }
    )


def test_document_for_is_the_stored_document_under_the_name_given() -> None:
    stored = version("First stored")

    built = document_for(stored, "Its profile's name")

    assert built.label == "Its profile's name"
    assert stored_document(stored, "Its profile's name")["label"] == "Its profile's name"
    assert stored.profile is not None and stored.profile["label"] == "First stored"
    assert built.phases[0].name == "Extraction"


def builds_a_profile_from_a_version(node: ast.AST) -> bool:
    """``Profile.model_validate(...)``, ``Profile(...)`` or ``Profile.model_construct(...)`` whose
    argument reads some object's ``.profile`` (the stored document of a version), spread or not."""
    if not isinstance(node, ast.Call):
        return False
    target = node.func
    named = (isinstance(target, ast.Name) and target.id == "Profile") or (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "Profile"
        and target.attr in {"model_validate", "model_construct", "model_validate_json"}
    )
    if not named:
        return False
    parts = [*node.args, *(k.value for k in node.keywords)]
    return any(
        isinstance(inner, ast.Attribute) and inner.attr == "profile"
        for part in parts
        for inner in ast.walk(part)
    )


def test_nothing_else_builds_a_profile_from_a_stored_version() -> None:
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if path in ALLOWED or "upgrades" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and builds_a_profile_from_a_version(node):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], "build the document with document_for(version, name):\n" + "\n".join(
        offenders
    )


def test_the_scan_sees_each_way_of_building_one() -> None:
    for source in (
        "Profile.model_validate(version.profile)",
        "Profile.model_validate({**version.profile, 'label': 'x'})",
        "Profile(**version.profile)",
        "Profile.model_validate(dict(row.profile or {}))",
    ):
        call = ast.parse(source).body[0].value  # type: ignore[attr-defined]
        assert builds_a_profile_from_a_version(call), source
    assert not builds_a_profile_from_a_version(
        ast.parse("Profile.model_validate(body)").body[0].value  # type: ignore[attr-defined]
    )


def test_the_old_hash_names_are_gone() -> None:
    names = ("profile_content_hash", "canonical_profile_json")
    hits = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*.py")
        if any(name in path.read_text() for name in names)
    ]
    assert hits == []

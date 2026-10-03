"""A version's name is an identifier: no ordinal is served, in any shape, anywhere.

The ordinal ("the Nth version") ordered versions and meant "the current one is
the highest". Names say what a reader needs, the Set's pointer says which one is
current and `created_at` says what was made first, so a field that counts
versions can only mislead (v1.2 may be the third version, or the ninth). Four
places can serve one and each is walked here: every API response model, every
tool's input and output, the MCP resource's rows, and the curated SQL views.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from gaggiclanker.db.repos.sets import SetRow, SetVersionRow
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, registry

ROOT = Path(__file__).resolve().parent.parent / "gaggiclanker"


def _ordinal_names(schema: Any, path: str = "") -> list[str]:
    """Every property name in a JSON Schema that is, or ends in, `version_no`."""
    found: list[str] = []
    if isinstance(schema, dict):
        for name, sub in (schema.get("properties") or {}).items():
            if name == "version_no" or name.endswith("_version_no"):
                found.append(f"{path}.{name}")
            found += _ordinal_names(sub, f"{path}.{name}")
        for key, value in schema.items():
            if key != "properties":
                found += _ordinal_names(value, f"{path}/{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            found += _ordinal_names(value, f"{path}[{index}]")
    return found


def test_no_api_response_or_request_model_has_an_ordinal(app: FastAPI) -> None:
    components = app.openapi()["components"]["schemas"]
    assert len(components) > 50, "the walk found the app's models"
    assert {n for name, s in components.items() for n in _ordinal_names(s, name)} == set()


def test_no_tool_input_or_output_has_an_ordinal() -> None:
    specs = registry.specs(CHAT_PERMISSIONS)
    assert specs
    found = [
        name
        for spec in specs
        for model in (spec.input_model, spec.output_model)
        for name in _ordinal_names(model.model_json_schema(mode="serialization"), spec.name)
    ]
    assert found == []


def test_the_rows_the_mcp_resource_serves_have_no_ordinal() -> None:
    """The stdio `sets/{id}` resource dumps these two models, nothing else."""
    for model in (SetRow, SetVersionRow):
        assert _ordinal_names(model.model_json_schema(mode="serialization"), model.__name__) == []


def test_no_python_file_names_an_ordinal() -> None:
    """Only the migrations that created and dropped it still say `version_no`."""
    hits = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*.py")
        if re.search(r"version_no", path.read_text(encoding="utf-8"))
    ]
    assert hits == []


def test_the_walk_finds_an_ordinal_when_there_is_one() -> None:
    """The control: a walk that finds nothing anywhere proves nothing."""
    schema = {
        "properties": {
            "ok": {"type": "integer"},
            "row": {"properties": {"compares_to_version_no": {}}, "type": "object"},
            "list": {"anyOf": [{"properties": {"version_no": {}}}]},
        }
    }
    assert sorted(_ordinal_names(schema)) == [
        ".list/anyOf[0].version_no",
        ".row.compares_to_version_no",
    ]

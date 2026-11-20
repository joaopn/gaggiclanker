"""The draft call's output schema is the profile model's, so what the model gains the schema
shows. The agent is not asked for a dose: a strict schema requires every property it lists, so
a `dose` there would be one more thing every drafted profile has to answer."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from gaggiclanker.domain.models import Profile
from gaggiclanker.drafts.models import DraftedProfile
from gaggiclanker.llm.schema import strict_json_schema
from tests.drafts.conftest import profile_fixture


def _profile_schema(inline_refs: bool) -> dict[str, Any]:
    schema = strict_json_schema(DraftedProfile, inline_refs=inline_refs)
    node = schema["properties"]["profile"]
    if "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[1]]
    return dict(node)


def test_the_drafting_schema_does_not_ask_for_a_dose() -> None:
    for inline_refs in (True, False):
        profile = _profile_schema(inline_refs)
        assert "dose" not in profile["properties"]
        assert "dose" not in profile["required"]
        # The one this keeps out for the same reason, so the pair is read as a pair.
        assert "annotations" not in profile["properties"]


def test_without_the_marker_the_dose_would_be_in_the_schema_and_required() -> None:
    """The first half: a model that is the profile with a plain `dose` field puts it in the
    strict schema as a required property, which is what the marker keeps out."""

    class PlainDose(Profile):
        dose: float | None = Field(default=None, ge=0, le=100)

    class Drafted(BaseModel):
        profile: PlainDose

    schema = strict_json_schema(Drafted)["properties"]["profile"]
    assert "dose" in schema["properties"] and "dose" in schema["required"]


def test_a_reply_without_a_dose_is_a_valid_draft_and_one_with_it_is_kept() -> None:
    document = profile_fixture("firmware-9bar")
    drafted = DraftedProfile.model_validate({"profile": document, "change_summary": ""})
    assert drafted.profile.dose is None
    with_dose = DraftedProfile.model_validate({"profile": {**document, "dose": 18}})
    assert with_dose.profile.dose == 18
    assert "dose" not in json.dumps(drafted.profile.to_device())

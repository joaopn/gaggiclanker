"""Turning a pydantic model into a JSON schema providers will actually accept.

``model_json_schema()`` is correct JSON Schema and is rejected by half the
structured-output implementations in the field, for two reasons that have
nothing to do with correctness:

* **OpenAI's strict mode** requires ``additionalProperties: false`` on every
  object and every property listed in ``required`` — optional fields are
  expressed as a nullable type, not by omission from ``required``. A schema
  that leaves a field out of ``required`` is refused outright, so a model with
  one ``str | None = None`` field fails every call until this runs over it.
* **Some gateways reject ``$ref``/``$defs``** even though the spec allows them,
  and pydantic emits them for any nested model or enum.

So the schema gets walked once: definitions inlined, every object closed, every
property required. The pydantic model stays the authority on what is *actually*
optional — the reply is validated against the model, not against the schema we
sent — so making a field "required but nullable" costs nothing and buys a
schema every provider takes.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel

__all__ = ["schema_name", "strict_json_schema"]

#: How deep the inliner will follow ``$ref``. A recursive model would otherwise
#: expand for ever; ten levels is far past anything this app models and the
#: failure at the limit is a ``$ref`` left in place, not a hang.
_MAX_INLINE_DEPTH = 10


def schema_name(model: type[BaseModel]) -> str:
    """The name providers label the schema with. The class name, as authored."""
    return model.__name__


def strict_json_schema(model: type[BaseModel], *, inline_refs: bool = True) -> dict[str, Any]:
    """A closed, fully-required JSON schema for ``model``.

    ``inline_refs=False`` keeps ``$defs``/``$ref`` for providers that handle
    them (the Anthropic SDK transforms the schema itself and is happy with
    refs), which matters for a model that refers to the same nested type twice:
    inlining duplicates it, refs do not.
    """
    root = model.model_json_schema()
    defs: dict[str, Any] = root.pop("$defs", {}) if inline_refs else root.get("$defs", {})
    walked = cast("dict[str, Any]", _walk(root, defs, depth=0, inline_refs=inline_refs))
    if not inline_refs and defs:
        walked["$defs"] = {
            name: _walk(schema, defs, depth=0, inline_refs=False) for name, schema in defs.items()
        }
    return walked


def _walk(node: Any, defs: dict[str, Any], *, depth: int, inline_refs: bool) -> Any:
    if isinstance(node, list):
        return [_walk(item, defs, depth=depth, inline_refs=inline_refs) for item in node]
    if not isinstance(node, dict):
        return node

    schema: dict[str, Any] = dict(node)

    ref = schema.get("$ref")
    if inline_refs and isinstance(ref, str) and depth < _MAX_INLINE_DEPTH:
        target = _resolve(ref, defs)
        if target is not None:
            # Sibling keys (a description on the property, say) survive the
            # inlining and win over the definition's own.
            merged = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
            return _walk(merged, defs, depth=depth + 1, inline_refs=True)

    for key in ("properties", "$defs", "patternProperties"):
        value = schema.get(key)
        if isinstance(value, dict):
            schema[key] = {
                name: _walk(child, defs, depth=depth + 1, inline_refs=inline_refs)
                for name, child in value.items()
            }

    for key in ("items", "additionalItems", "contains", "not"):
        if key in schema:
            schema[key] = _walk(schema[key], defs, depth=depth + 1, inline_refs=inline_refs)

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        value = schema.get(key)
        if isinstance(value, list):
            schema[key] = [
                _walk(child, defs, depth=depth + 1, inline_refs=inline_refs) for child in value
            ]

    properties = schema.get("properties")
    if isinstance(properties, dict):
        # Closed and fully required: the two things strict mode insists on.
        # `required` is rewritten rather than extended so a model that already
        # listed some fields does not end up with duplicates.
        schema["additionalProperties"] = False
        schema["required"] = list(properties.keys())
        schema.setdefault("type", "object")

    # `default` is advisory in JSON Schema and OpenAI's strict validator
    # rejects it outright; the pydantic model still applies its defaults when
    # it validates the reply, so dropping it here changes nothing we rely on.
    schema.pop("default", None)
    return schema


def _resolve(ref: str, defs: dict[str, Any]) -> dict[str, Any] | None:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        return None
    target = defs.get(ref[len(prefix) :])
    return target if isinstance(target, dict) else None

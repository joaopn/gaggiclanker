"""Getting a JSON object out of whatever the model actually sent.

In ``json_schema`` mode the body is already JSON and this is a no-op. The
function exists for the two weaker modes, where a model that was merely *asked*
for JSON tends to wrap it in a ``` fence, or introduce it ("Here is the
analysis:"), or add a closing remark. Both habits are cheap to undo and
expensive to debug if they reach ``model_validate`` untouched, where the error
is "Invalid JSON: expected value at line 1 column 1" and says nothing about the
prose that caused it.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["parse_json_content", "strip_code_fences"]

# ```json … ``` or ``` … ```, with the fence on its own line. Non-greedy so the
# first fenced block wins when a chatty model emits two.
_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Return the contents of the first fenced block, or the text unchanged."""
    match = _FENCE.search(text)
    if match is None:
        return text.strip()
    return match.group(1).strip()


def parse_json_content(text: str) -> Any:
    """Parse the JSON document inside ``text``.

    Three attempts, in order: the text as-is, the first fenced block, and the
    slice from the first ``{`` (or ``[``) to the matching last bracket. Raises
    ``ValueError`` mentioning "parse" when none of them is JSON — the word
    matters, because the retry policy keys on it.
    """
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    unfenced = strip_code_fences(text)
    if unfenced and unfenced not in candidates:
        candidates.append(unfenced)

    for source in list(candidates):
        sliced = _slice_to_brackets(source)
        if sliced and sliced not in candidates:
            candidates.append(sliced)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except ValueError:
            continue

    preview = stripped[:200]
    raise ValueError(f"could not parse JSON from the model's reply: {preview!r}")


def _slice_to_brackets(text: str) -> str | None:
    """The span from the first opening bracket to the last matching closer.

    Objects are tried before arrays because every output model this app uses is
    an object, and a prose reply containing a bracketed aside would otherwise
    win over the real payload.
    """
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            return text[start : end + 1]
    return None

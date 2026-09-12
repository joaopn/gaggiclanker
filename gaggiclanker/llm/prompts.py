"""Prompts as editable data: YAML files seeded into a table, rendered on demand.

A prompt is the part of an LLM feature most likely to need changing, and the
part least likely to need a code review. Keeping it in a string constant means
every wording tweak is a rebuild; keeping it only in the database means an
upgrade cannot ship a better one. So both: the files under
``gaggiclanker/prompts/`` are the seed, the ``prompts`` table is the live copy,
and the seeding rules (below) let the two coexist.

**Seeding, per file, on every boot:**

* no row → insert it, live text and default identical;
* the file is unchanged → do nothing, so a steady-state boot touches nothing;
* the file changed and the row is *unedited* → take the new text for both, so an
  upgrade reaches every prompt nobody has touched;
* the file changed and the row is *edited* → update the default only. The user's
  text stands, and "reset to default" now converges on the new wording rather
  than on the version they forked from.

A row whose file has vanished is left alone: an image that dropped a prompt
should not delete text the user may have written.

**Rendering** is deliberately not Jinja. Two substitutions, ``{{var}}`` and
``{{> fragment}}``, no logic, no loops, no filters, no autoescaping — a prompt
is prose with holes in it, and a template language in the hands of a text box
that anyone can edit is a sandbox to escape from. Fragments expand one level
only, for the same reason recursion is not offered.

An undefined variable **raises**. Rendering ``{{shotSummary}}`` to an empty
string produces a call that costs real money and answers confidently about
nothing, which is far worse than a 500 naming the variable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gaggiclanker import __version__
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.llm.types import LlmMessage

__all__ = [
    "DEFAULT_PROMPTS_DIR",
    "FRAGMENT_PREFIX",
    "PromptError",
    "PromptFile",
    "PromptService",
    "RenderedPrompt",
    "default_prompt_vars",
    "seed_prompts",
]

log = structlog.get_logger(__name__)

#: Shipped inside the package, next to the migrations, because the wheel and
#: the container image carry ``gaggiclanker/`` and nothing else — a top-level
#: ``prompts/`` directory would exist in a checkout and be missing in the image.
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

#: Where fragments live, and therefore the prefix their names carry.
FRAGMENT_PREFIX = "fragments/"

#: ``{{ name }}``. Dots and dashes are allowed so a variable can be namespaced.
_VARIABLE = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")
#: ``{{> name}}``.
_PARTIAL = re.compile(r"\{\{>\s*([\w.-]+)\s*\}\}")

#: A prompt body cannot be bigger than this. Not a security boundary — the API
#: is authenticated — but a 10 MB paste into the editor would otherwise be
#: rendered into every subsequent call.
MAX_PROMPT_CHARS = 200_000


class PromptError(Exception):
    """A prompt could not be loaded, validated or rendered."""


class PromptVariable(BaseModel):
    """A declared variable. Documentation for the editor, not a constraint.

    The renderer's requirement is that every ``{{var}}`` *used* is provided;
    this list is what the UI shows and what a missing-variable error quotes as
    "declared", which is how a typo in either direction becomes obvious.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""


class PromptFile(BaseModel):
    """The YAML schema. Strict: an unknown key is a typo, not an extension."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    variables: list[PromptVariable] = Field(default_factory=list)
    system: str = ""
    user: str = ""
    #: Fragments carry this instead of system/user.
    template: str = ""


@dataclass(slots=True)
class RenderedPrompt:
    """A prompt with its holes filled, ready to become messages."""

    name: str
    description: str
    system: str
    user: str
    #: The row's ``updated_at``, recorded on the usage row so an analysis can
    #: be traced to the exact prompt text that produced it.
    version: str = ""
    declared: list[str] = field(default_factory=list)

    def messages(self) -> list[LlmMessage]:
        """The rendered prompt as messages, omitting an empty system prompt.

        Typed rather than dicts so a caller can hand the result straight to
        ``LlmRequest(messages=...)``; a dict would type-check there and then
        fail inside the provider, which is the worst place to find out.
        """
        out: list[LlmMessage] = []
        if self.system.strip():
            out.append(LlmMessage(role="system", content=self.system))
        out.append(LlmMessage(role="user", content=self.user))
        return out

    def as_dicts(self) -> list[dict[str, str]]:
        """The same thing as ``[{role, content}]``, for a caller that wants JSON."""
        return [{"role": message.role, "content": message.content} for message in self.messages()]


def default_prompt_vars() -> dict[str, str]:
    """Variables every prompt can use without the caller passing them."""
    return {"app_version": __version__}


def _parse(name: str, content: str) -> PromptFile:
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise PromptError(f"prompt {name!r} is not valid YAML: {exc}") from None
    if not isinstance(document, dict):
        raise PromptError(f"prompt {name!r} must be a YAML mapping")
    try:
        return PromptFile.model_validate(document)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or '(root)'}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        raise PromptError(f"prompt {name!r} does not match the prompt schema: {details}") from None


async def seed_prompts(repo: PromptsRepository, directory: Path | None = None) -> int:
    """Upsert every ``*.yaml`` under ``directory``. Returns how many changed.

    A missing directory is not an error: the tests build an app without one,
    and a deployment that has dropped the prompts should serve the archive
    rather than refuse to boot.
    """
    root = directory or DEFAULT_PROMPTS_DIR
    if not root.is_dir():
        log.info("prompts_dir_missing", path=str(root))
        return 0

    changed = 0
    for path in sorted(root.rglob("*.yaml")):
        name = path.relative_to(root).with_suffix("").as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - unreadable seed file
            log.warning("prompt_seed_unreadable", prompt=name, error=str(exc))
            continue
        try:
            _parse(name, content)
        except PromptError as exc:
            # A broken seed must not take the app down, but it must be loud:
            # the feature that uses it will fail at the first call otherwise,
            # far from the file that caused it.
            log.error("prompt_seed_invalid", prompt=name, error=str(exc))
            continue

        row = await repo.get(name)
        if row is None:
            await repo.insert(name, content)
            changed += 1
            continue
        if row.default_content == content:
            continue
        if row.edited:
            await repo.refresh_default(name, content)
        else:
            await repo.refresh_both(name, content)
        changed += 1

    if changed:
        log.info("prompts_seeded", changed=changed, path=str(root))
    return changed


class PromptService:
    """Loads, renders, validates and resets the prompts in the table."""

    def __init__(self, repo: PromptsRepository) -> None:
        self.repo = repo
        # Keyed on (name, updated_at), so an edit through the API invalidates
        # the entry by changing the key rather than by anyone remembering to
        # clear a cache. The row itself is read every time — that read is the
        # source of truth; only the YAML parse is worth memoising.
        self._parsed: dict[tuple[str, str], PromptFile] = {}

    async def _file(self, name: str) -> tuple[PromptFile, str]:
        row = await self.repo.get(name)
        if row is None:
            raise PromptError(f"no prompt named {name!r}")
        key = (name, row.updated_at)
        parsed = self._parsed.get(key)
        if parsed is None:
            parsed = _parse(name, row.content)
            self._parsed = {key: parsed}
        return parsed, row.updated_at

    async def load(self, name: str, variables: dict[str, Any] | None = None) -> RenderedPrompt:
        """Render ``name`` with ``variables`` on top of the defaults."""
        parsed, version = await self._file(name)
        merged = {**default_prompt_vars(), **_normalize(variables or {})}
        declared = [variable.name for variable in parsed.variables]
        system = await self._render(parsed.system, name, merged, declared)
        user = await self._render(parsed.user, name, merged, declared)
        return RenderedPrompt(
            name=name,
            description=parsed.description,
            system=system,
            user=user,
            version=version,
            declared=declared,
        )

    async def render_fragment(self, name: str, variables: dict[str, Any] | None = None) -> str:
        """Render a fragment on its own, for a caller that wants just the note."""
        full = name if name.startswith(FRAGMENT_PREFIX) else f"{FRAGMENT_PREFIX}{name}"
        parsed, _ = await self._file(full)
        if not parsed.template:
            raise PromptError(f"fragment {name!r} has no `template` field")
        merged = {**default_prompt_vars(), **_normalize(variables or {})}
        return await self._render(parsed.template, full, merged, [v.name for v in parsed.variables])

    async def _render(
        self, template: str, context: str, variables: dict[str, str], declared: list[str]
    ) -> str:
        if not template:
            return ""
        expanded = await self._expand_partials(template, context)
        return _interpolate(expanded, variables, context=context, declared=declared)

    async def _expand_partials(self, template: str, context: str) -> str:
        """Replace every ``{{> name}}`` with its fragment's template. One level.

        Each distinct fragment is loaded once however often it appears, and a
        fragment that itself contains a partial is rejected rather than
        expanded: nesting turns an editable text box into something that can
        recurse, and nobody has ever needed it.
        """
        names = {match.group(1) for match in _PARTIAL.finditer(template)}
        if not names:
            return template

        bodies: dict[str, str] = {}
        for name in names:
            fragment, _ = await self._file(f"{FRAGMENT_PREFIX}{name}")
            if not fragment.template:
                raise PromptError(
                    f"fragment {name!r}, referenced from {context!r}, has no `template` field"
                )
            if _PARTIAL.search(fragment.template):
                raise PromptError(
                    f"fragment {name!r} references another partial; "
                    "expansion is one level deep and nesting is not supported"
                )
            bodies[name] = fragment.template

        return _PARTIAL.sub(lambda match: bodies[match.group(1)], template)

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Every prompt, with enough to render the editor's list.

        Never raises on a bad row: one prompt someone broke in the editor must
        not make the list of prompts unreachable, which is where they would go
        to fix it.
        """
        out: list[dict[str, Any]] = []
        for row in await self.repo.list_all():
            entry: dict[str, Any] = {
                "name": row.name,
                "updated_at": row.updated_at,
                "edited": row.edited,
                "fragment": row.name.startswith(FRAGMENT_PREFIX),
                "description": "",
                "variables": [],
                "valid": True,
            }
            try:
                parsed = _parse(row.name, row.content)
            except PromptError as exc:
                entry["valid"] = False
                entry["description"] = str(exc)
            else:
                entry["description"] = parsed.description
                entry["variables"] = [v.model_dump() for v in parsed.variables]
            out.append(entry)
        return out

    async def get(self, name: str) -> dict[str, Any]:
        row = await self.repo.get(name)
        if row is None:
            raise PromptError(f"no prompt named {name!r}")
        return {
            "name": row.name,
            "content": row.content,
            "default_content": row.default_content,
            "edited": row.edited,
            "updated_at": row.updated_at,
            "fragment": row.name.startswith(FRAGMENT_PREFIX),
        }

    async def validate_content(self, name: str, content: str) -> None:
        """Structural check for a PUT: YAML, schema, and every fragment exists.

        Deliberately does **not** check variable names. The variables a prompt
        uses are decided by the caller that loads it, and a save-time check
        would make adding a variable a two-commit dance: you could not save the
        prompt that uses it until the code that provides it had shipped.
        """
        if len(content) > MAX_PROMPT_CHARS:
            raise PromptError(f"a prompt may not exceed {MAX_PROMPT_CHARS} characters")
        parsed = _parse(name, content)
        is_fragment = name.startswith(FRAGMENT_PREFIX)
        bodies = [parsed.system, parsed.user, parsed.template]
        for body in bodies:
            for match in _PARTIAL.finditer(body or ""):
                referenced = match.group(1)
                if is_fragment:
                    raise PromptError(
                        f"fragment {name!r} references {referenced!r}; fragments cannot "
                        "include {{> ...}} because expansion is one level deep"
                    )
                if await self.repo.get(f"{FRAGMENT_PREFIX}{referenced}") is None:
                    raise PromptError(
                        f"prompt {name!r} references fragment {referenced!r}, which does not exist"
                    )

    async def save(self, name: str, content: str) -> dict[str, Any]:
        await self.validate_content(name, content)
        if not await self.repo.set_content(name, content):
            raise PromptError(f"no prompt named {name!r}")
        self._parsed.clear()
        log.info("prompt_saved", prompt=name)
        return await self.get(name)

    async def reset(self, name: str) -> dict[str, Any]:
        if not await self.repo.reset(name):
            raise PromptError(f"no prompt named {name!r}")
        self._parsed.clear()
        log.info("prompt_reset", prompt=name)
        return await self.get(name)


def _normalize(variables: dict[str, Any]) -> dict[str, str]:
    """Coerce values to text, dropping ``None``.

    Dropping rather than rendering "None" is on purpose: a variable that is
    absent raises, which is the behaviour we want for "the caller had no shot
    summary", while the string ``None`` would sail into the prompt and be
    answered about.
    """
    return {key: str(value) for key, value in variables.items() if value is not None}


def _interpolate(
    template: str, variables: dict[str, str], *, context: str, declared: list[str]
) -> str:
    """Substitute every ``{{var}}``; raise naming what was missing.

    An empty string is a perfectly good value — "no tasting notes yet" renders
    as nothing on purpose — so presence is tested, not truthiness.
    """
    missing: set[str] = set()

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return variables[key]
        missing.add(key)
        return ""

    rendered = _VARIABLE.sub(substitute, template)
    if missing:
        provided = ", ".join(sorted(variables)) or "(none)"
        raise PromptError(
            f"prompt {context!r} is missing variables: {', '.join(sorted(missing))}. "
            f"Declared: {', '.join(sorted(declared)) or '(none)'}. Provided: {provided}."
        )
    return rendered

"""The tool's scope rule against the picker's scope-options, over random archives, and
scope-reach against an independent count. Real app, real routes, real dispatcher.

Each seed builds beans (some archived, some with blank or oddly spelled origins), grinders and
Recipes (some archived, some being designed), then asks: in a Recipe or Design conversation,
is every option the picker offers accepted, stored as the option's value, and nothing else; in a
General conversation, is every select option accepted, and is whatever the tool accepts one that
a select shows, in its spelling."""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatMessageWrite, ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.knowledge_insights import scope_matches
from gaggiclanker.db.repos.recipes import (
    DesignBrief,
    RecipesRepository,
    RecipeVersionWrite,
    RecipeWrite,
)
from gaggiclanker.domain.vocab import PROCESSES, ROAST_LEVELS
from gaggiclanker.tools.builtin import SCOPE_NOT_THIS_RECIPE
from gaggiclanker.tools.registry import registry
from gaggiclanker.tools.scope import ToolScope
from tests.knowledge.chat_tools import ctx_for

ORIGINS = ["Ethiopia", "ethiopia ", "Colombia", "", None, "Kenya", "  ", "KENYA"]
SAID = "remember this everywhere please"  # at least a phrase


def norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return bool(norm(a) == norm(b))
    return bool(a == b)


def my_attributes(bean: Any, grinder_id: Any) -> dict[str, Any]:
    def blank(v: Any) -> Any:
        return None if (v is None or (isinstance(v, str) and not v.strip())) else v

    return {
        "bean_id": bean.id if bean else None,
        "grinder_id": grinder_id,
        "roast_level": blank(getattr(bean, "roast_level", None)),
        "process": blank(getattr(bean, "process", None)),
        "origin": blank(getattr(bean, "origin", None)),
    }


async def build(app: FastAPI, rng: random.Random) -> dict[str, Any]:
    db = app.state.db
    beans = BeansRepository(db)
    made_beans = []
    for i in range(rng.randint(2, 6)):
        bean = await beans.create(
            BeanWrite(
                name=f"Bean {i} {rng.random():.3f}",
                origin=rng.choice(ORIGINS),
                roast_level=rng.choice([*ROAST_LEVELS, None]),  # type: ignore[list-item]
                process=rng.choice([*PROCESSES, None]),  # type: ignore[list-item]
            )
        )
        made_beans.append(bean)
    archived_beans = set()
    for bean in made_beans:
        if rng.random() < 0.25:
            await beans.set_archived(bean.id, archived=True)
            archived_beans.add(bean.id)
    grinders = GrindersRepository(db)
    made_grinders = [
        (await grinders.create(GrinderWrite(name=f"Grinder {i}"))).id
        for i in range(rng.randint(1, 3))
    ]
    recipes = RecipesRepository(db)
    made = []
    for i in range(rng.randint(3, 8)):
        bean = rng.choice(made_beans)
        grinder = rng.choice([*made_grinders, None])
        kind = rng.choices(["plain", "archived", "designing"], [6, 2, 1])[0]
        if kind == "designing":
            row = await recipes.create_design(
                RecipeWrite(name=f"R{i}", bean_id=bean.id, grinder_id=grinder), DesignBrief()
            )
        else:
            row = await recipes.create(
                RecipeWrite(name=f"R{i}", bean_id=bean.id, grinder_id=grinder),
                RecipeVersionWrite(drink="espresso", dose_g=18, target_yield_g=36),
            )
            if kind == "archived":
                await recipes.archive(row.id)
        made.append((row.id, bean.id, grinder, kind))
    chat = ChatRepository(db)
    thread = (await chat.create_thread(ChatThreadWrite())).thread
    assert thread is not None
    await chat.add_message(ChatMessageWrite(thread_id=thread.id, role="user", content=SAID))
    return {
        "beans": made_beans,
        "archived_beans": archived_beans,
        "grinders": made_grinders,
        "recipes": made,
        "thread": thread.id,
    }


async def ok(r: httpx.Response) -> Any:
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.mark.parametrize("seed", range(12))
async def test_twin_and_reach(app: FastAPI, client: httpx.AsyncClient, seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - a seeded fixture, not a secret
    world = await build(app, rng)
    db = app.state.db
    findings: list[str] = []
    counter = 0

    universe: list[tuple[str, Any]] = []
    for bean in world["beans"]:
        universe.append(("bean_id", bean.id))
    universe.append(("bean_id", 9999))
    for g in world["grinders"]:
        universe.append(("grinder_id", g))
    for r in ROAST_LEVELS:
        universe.append(("roast_level", r))
        universe.append(("roast_level", r.upper()))
    for p in PROCESSES:
        universe.append(("process", p))
    for o in ORIGINS:
        if o is not None and o.strip():
            universe.append(("origin", o))
    universe.append(("origin", "Narnia"))

    # ── Recipe and Design conversations ──
    for recipe_id, _bean_id, _grinder_id, kind in world["recipes"]:
        options = (
            await ok(await client.get(f"/api/knowledge/scope-options?recipe_id={recipe_id}"))
        )["recipe"]
        ctx = ctx_for(app, await ToolScope.resolve(db, recipe_id), world["thread"])
        # every single option accepted, stored as the option's value
        for option in options:
            counter += 1
            out = await registry.dispatch(
                ctx,
                "request_general_insight",
                {"text": f"t{counter}", "said": SAID, "scope": {option["key"]: option["value"]}},
            )
            if not out.ok:
                findings.append(f"option refused r{recipe_id}/{kind} {option}: {out.data}")
                continue
            if out.data["scope"] != {option["key"]: option["value"]}:
                findings.append(f"stored {out.data['scope']} != option {option}")
            if out.data["scope_labels"] != [option["label"]]:
                findings.append(f"labels {out.data['scope_labels']} != {option['label']}")
        # all options together
        if options:
            counter += 1
            out = await registry.dispatch(
                ctx,
                "request_general_insight",
                {
                    "text": f"t{counter}",
                    "said": SAID,
                    "scope": {o["key"]: o["value"] for o in options},
                },
            )
            if not out.ok:
                findings.append(f"all options refused r{recipe_id}: {out.data}")
        # nothing else accepted
        for key, value in universe:
            offered = any(o["key"] == key and same(o["value"], value) for o in options)
            counter += 1
            out = await registry.dispatch(
                ctx,
                "request_general_insight",
                {"text": f"t{counter}", "said": SAID, "scope": {key: value}},
            )
            if offered and not out.ok:
                findings.append(f"offered {key}={value!r} refused r{recipe_id}: {out.data}")
            if not offered:
                if out.ok:
                    findings.append(
                        f"NOT offered {key}={value!r} accepted r{recipe_id}/{kind}: {options}"
                    )
                elif str(out.data.get("detail")) != SCOPE_NOT_THIS_RECIPE:
                    findings.append(f"wrong sentence {key}={value!r}: {out.data}")

    # ── General conversation: the selects ──
    general = await ok(await client.get("/api/knowledge/scope-options"))
    gctx = ctx_for(app, ToolScope(), world["thread"])
    select_values = {
        "bean_id": [b["id"] for b in general["beans"]],
        "grinder_id": [g["id"] for g in general["grinders"]],
        "roast_level": [t["value"] for t in general["roast_levels"]],
        "process": [t["value"] for t in general["processes"]],
        "origin": [t["value"] for t in general["origins"]],
    }
    for key, values in select_values.items():
        for value in values:
            counter += 1
            out = await registry.dispatch(
                gctx,
                "request_general_insight",
                {"text": f"t{counter}", "said": SAID, "scope": {key: value}},
            )
            if not out.ok:
                findings.append(f"GENERAL picker option refused {key}={value!r}: {out.data}")
    for key, value in universe:
        counter += 1
        out = await registry.dispatch(
            gctx,
            "request_general_insight",
            {"text": f"t{counter}", "said": SAID, "scope": {key: value}},
        )
        offered = any(same(o, value) for o in select_values[key])
        if offered and not out.ok:
            findings.append(f"GENERAL offered {key}={value!r} refused: {out.data}")
        if out.ok:
            stored = out.data["scope"][key]
            if stored not in select_values[key]:
                findings.append(f"GENERAL stored {key}={stored!r}: no select option shows it")
            if not offered:
                findings.append(f"GENERAL accepted {key}={value!r}, which no select offers")
        elif not offered and not str(out.data.get("detail")):
            findings.append(f"GENERAL refused {key}={value!r} with no sentence")

    # ── reach ──
    bean_rows = {b.id: await BeansRepository(db).get(b.id) for b in world["beans"]}
    live = [(r, b, g) for r, b, g, kind in world["recipes"] if kind == "plain"]
    for _ in range(30):
        wanted: dict[str, Any] = {}
        for key, value in rng.sample(universe, rng.randint(0, 2)):
            wanted.setdefault(key, value)
        params = dict(wanted)
        got = (await ok(await client.get("/api/knowledge/scope-reach", params=params)))["recipes"]
        mine = sum(1 for r, b, g in live if scope_matches(wanted, my_attributes(bean_rows[b], g)))
        if got != mine:
            findings.append(f"reach {wanted}: route {got} mine {mine}")

    assert not findings, "\n".join(findings[:30])

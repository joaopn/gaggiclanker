"""A tool context over the running app's database, for the tests that ask tools through it."""

from __future__ import annotations

from fastapi import FastAPI

from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext
from gaggiclanker.tools.scope import ToolScope


def ctx_for(app: FastAPI, scope: ToolScope, thread: int) -> ToolContext:
    db = app.state.db
    ctx = ToolContext(
        db=db,
        settings=SettingsService(SettingsRepository(db)),
        knowledge=KnowledgeService(db),
        scope=scope,
        caller="test",
        permissions=CHAT_PERMISSIONS,
    )
    ctx.thread_id = thread
    return ctx

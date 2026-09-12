"""structlog configuration: one JSON object per line on stdout.

The container's log driver is the only consumer, so there is no file handler
and no rotation. Every event carries the request id when there is one, which is
what makes a failure traceable from the client's ``x-request-id`` header to the
device call that caused it.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

__all__ = ["configure_logging", "get_logger"]

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

_configured = False


def _add_request_id(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Merge the ambient request id into every event."""
    # Imported here rather than at module scope so the logging module stays
    # importable from scripts that never touch the web layer.
    from gaggiclanker.infra.request_context import get_request_id

    request_id = get_request_id()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def _drop_uvicorn_color_message(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Strip uvicorn's ``color_message``.

    uvicorn passes a second, ANSI-escaped copy of each message as an extra, for
    its own coloured console formatter. Rendered as JSON it is a duplicate of
    ``event`` with escape codes in it — noise in the log and in anything that
    indexes it.
    """
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(level: str = "info", *, json_output: bool = True) -> None:
    """Configure structlog and the stdlib root logger.

    Idempotent in effect: calling it again re-applies the configuration, which
    is what tests want when they build a second app with a different level.
    ``json_output=False`` switches to structlog's console renderer for local
    development, where a human is reading.
    """
    global _configured

    resolved = _LEVELS.get(level.strip().lower(), logging.INFO)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    # uvicorn, asyncio and aiosqlite log through the stdlib. Routing their
    # records through structlog's ProcessorFormatter means a container log has
    # one format end to end, rather than JSON from us and "INFO: Started
    # server process" from uvicorn, which no log shipper can parse together.
    foreign_pre_chain: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        _drop_uvicorn_color_message,
        _add_request_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=foreign_pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    logging.basicConfig(handlers=[handler], level=resolved, force=True)
    for noisy in ("uvicorn", "uvicorn.error", "aiosqlite"):
        stdlib_logger = logging.getLogger(noisy)
        stdlib_logger.setLevel(resolved)
        # Let the root handler above do the rendering. The `uvicorn` CLI
        # installs its own plain-text handlers and sets propagate=False, so
        # without this its startup lines are the only non-JSON output in an
        # otherwise machine-readable log.
        stdlib_logger.handlers.clear()
        stdlib_logger.propagate = True

    # uvicorn's access log is silenced rather than reformatted: the request
    # middleware already logs one line per request, with the request id and the
    # duration, and two lines per request is one too many. Set explicitly
    # because this function runs when uvicorn imports the app — after uvicorn
    # has applied its own `access_log=False`, which a blanket propagate=True
    # above would otherwise undo.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(resolved),
        # No `file=`, on purpose: the factory then resolves `sys.stdout` when a
        # line is written rather than when logging is configured. In the
        # container the two are the same stream; under pytest they are not, and
        # a logger holding the stdout of the test that configured it writes to a
        # closed file in the next one. Configuration is global, so one test
        # calling `gaggiclanker import` would otherwise break every later test
        # that logs.
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """A bound logger; configures logging with defaults if nobody has yet."""
    if not _configured:
        configure_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger

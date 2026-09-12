"""``python -m gaggiclanker`` / the ``gaggiclanker`` console script.

A thin uvicorn launcher that reads the same environment the app does, so
``docker run -e PORT=9000`` works without a separate uvicorn command line.
"""

from __future__ import annotations

import uvicorn

from gaggiclanker.infra.logging import configure_logging
from gaggiclanker.settings import EnvSettings

__all__ = ["main"]


def main() -> None:
    env = EnvSettings()
    configure_logging(env.log_level, json_output=env.log_json)
    uvicorn.run(
        "gaggiclanker.main:app",
        host=env.host,
        port=env.port,
        # log_config=None keeps uvicorn from installing its own plain-text
        # handlers over the structlog ones configure_logging() set up, so the
        # container log is JSON end to end.
        log_config=None,
        log_level=env.log_level.lower(),
        # Our own middleware logs one line per request with the request id;
        # uvicorn's access log would duplicate it without one.
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()

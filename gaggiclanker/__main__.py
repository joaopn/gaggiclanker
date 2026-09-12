"""``python -m gaggiclanker`` / the ``gaggiclanker`` console script.

Three commands. ``serve`` — the default, and what a bare ``gaggiclanker`` still
does — is a thin uvicorn launcher reading the same environment the app does, so
``docker run -e PORT=9000`` works without a separate uvicorn command line.
``import`` loads exported shots and profiles straight into the database file,
without a server in the way (see :mod:`gaggiclanker.imports.cli`). ``mcp``
speaks the Model Context Protocol on stdin/stdout, for Claude Desktop and for
``claude -p --mcp-config`` (see :mod:`gaggiclanker.mcp.stdio`).

The bare form matters: it is the container's entry point, and adding a
subcommand must not change what ``CMD ["gaggiclanker"]`` does.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import uvicorn

from gaggiclanker import __version__
from gaggiclanker.imports.cli import add_import_parser, import_command
from gaggiclanker.infra.logging import configure_logging
from gaggiclanker.mcp.stdio import add_mcp_parser, mcp_command
from gaggiclanker.settings import EnvSettings

__all__ = ["build_parser", "main", "serve"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaggiclanker",
        description="Archive, diagnose and analyse espresso shots from a GaggiMate machine.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="run the web server (the default)")
    add_import_parser(subparsers)
    add_mcp_parser(subparsers)
    return parser


def serve() -> None:
    """Run the API and the SPA on the configured host and port."""
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "import":
        env = EnvSettings()
        # The import prints its own per-file lines; a JSON log line between them
        # would make the output unreadable, so only warnings and worse show.
        configure_logging("WARNING", json_output=env.log_json)
        return import_command(args)
    if args.command == "mcp":
        # stdout is the protocol channel: a single log line on it is a parse
        # error at the client. structlog writes to stdout, so it is turned down
        # to CRITICAL and the transport gets the stream to itself.
        configure_logging("CRITICAL", json_output=True)
        return mcp_command(args)
    serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

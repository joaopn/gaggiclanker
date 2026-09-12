"""Serving the built React bundle with an SPA fallback.

In production the Vite build lands in ``web/dist`` and FastAPI serves it from
the same origin as the API, which is what keeps the front end free of CORS and
lets the browser send credentials without ceremony. In development the Vite dev
server serves the app on its own port and proxies ``/api`` here, so ``web/dist``
does not exist and this module mounts nothing.

The fallback is what makes deep links work: ``/shots/123`` is a client-side
route, so a hard refresh must return ``index.html`` and let the router sort it
out — but only for requests that actually want HTML, and never for ``/api``,
which must keep returning a JSON 404 in the envelope.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.routing import Match

from gaggiclanker.infra.errors import MethodNotAllowed, NotFound

__all__ = ["DEFAULT_WEB_DIST", "mount_spa"]

log = structlog.get_logger(__name__)

# Repository layout: <repo>/web/dist next to <repo>/gaggiclanker. In the
# container the app is installed at /app with the same relative shape.
DEFAULT_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def mount_spa(app: FastAPI, dist_dir: Path | None = None) -> bool:
    """Serve ``dist_dir`` at ``/`` with an SPA fallback. Returns whether it mounted.

    Must be called after every API router is registered: the catch-all route
    added here would otherwise shadow them.
    """
    dist = dist_dir or DEFAULT_WEB_DIST
    index = dist / "index.html"
    if not index.is_file():
        log.info("spa_not_mounted", dist=str(dist))
        return False

    # Snapshot the real routes before the catch-all joins them, so the handler
    # can tell "no such path" from "that path exists, wrong verb".
    real_routes = list(app.router.routes)

    assets = dist / "assets"
    if assets.is_dir():
        # Hashed filenames, so they are safe to cache hard. Mounted separately
        # from the catch-all so a missing asset 404s as a missing asset rather
        # than silently returning index.html (which shows up as the browser
        # complaining that HTML is not valid JavaScript).
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    # Every method, not just GET: a catch-all registered for GET alone makes
    # Starlette answer 405 to a POST at an unknown path, which tells a client
    # the path exists. It does not. Non-GET verbs get the same JSON 404 they
    # get with no SPA build present.
    @app.api_route(
        "/{spa_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def spa_fallback(request: Request, spa_path: str) -> Response:
        # A catch-all that matches every method also swallows Starlette's own
        # 405: without this check, POST /health would answer 404 with the SPA
        # built and 405 without it. Match.PARTIAL means the path matched a real
        # route and only the method did not.
        for route in real_routes:
            if route.matches(request.scope)[0] is Match.PARTIAL:
                raise MethodNotAllowed(f"Method not allowed: {request.method} /{spa_path}")

        if spa_path.startswith("api/") or spa_path == "api":
            raise NotFound(f"Route not found: {request.method} /{spa_path}")

        if request.method not in {"GET", "HEAD"}:
            raise NotFound(f"Route not found: {request.method} /{spa_path}")

        # A real file (favicon, manifest, robots.txt) wins over the fallback.
        if spa_path:
            candidate = (dist / spa_path).resolve()
            # resolve() + is_relative_to() stops ../ traversal out of dist.
            if candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)

        accept = request.headers.get("accept", "")
        if "text/html" not in accept and "*/*" not in accept and accept:
            raise NotFound(f"Route not found: {request.method} /{spa_path}")

        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    log.info("spa_mounted", dist=str(dist))
    return True

"""Static serving of the built SPA, and the fallback rules around it.

The front-end build produces ``web/dist``. Until it exists the app must still start (it does — the
mount is conditional), and once the build exists the deep-link fallback must
not swallow ``/api`` or asset requests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app


@pytest.fixture
def web_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


async def test_no_dist_means_no_mount(env: EnvSettings, tmp_path: Path) -> None:
    async with running_app(env, web_dist=tmp_path / "absent") as (app, client):
        assert app.state.spa_mounted is False
        assert (await client.get("/")).status_code == 404


async def test_index_is_served_at_the_root(env: EnvSettings, web_dist: Path) -> None:
    async with running_app(env, web_dist=web_dist) as (app, client):
        assert app.state.spa_mounted is True
        response = await client.get("/")
        assert response.status_code == 200
        assert "<div id=root>" in response.text


async def test_deep_link_falls_back_to_index(env: EnvSettings, web_dist: Path) -> None:
    """A hard refresh on a client-side route must not 404."""
    async with running_app(env, web_dist=web_dist) as (_app, client):
        response = await client.get("/shots/000123", headers={"accept": "text/html"})
        assert response.status_code == 200
        assert "<div id=root>" in response.text


async def test_real_files_win_over_the_fallback(env: EnvSettings, web_dist: Path) -> None:
    async with running_app(env, web_dist=web_dist) as (_app, client):
        assert (await client.get("/favicon.svg")).text == "<svg/>"
        assert (await client.get("/assets/index-abc123.js")).text == "console.log(1)"


async def test_missing_asset_is_a_404_not_index_html(env: EnvSettings, web_dist: Path) -> None:
    """Returning index.html for a missing .js shows up as a syntax error, not a 404."""
    async with running_app(env, web_dist=web_dist) as (_app, client):
        assert (await client.get("/assets/gone.js")).status_code == 404


async def test_api_404_stays_json_with_the_spa_mounted(env: EnvSettings, web_dist: Path) -> None:
    """The catch-all must never hand HTML to an API client."""
    async with running_app(env, web_dist=web_dist) as (_app, client):
        response = await client.get("/api/nope", headers={"accept": "text/html"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_health_still_works_with_the_spa_mounted(env: EnvSettings, web_dist: Path) -> None:
    async with running_app(env, web_dist=web_dist) as (_app, client):
        assert (await client.get("/health")).json()["data"]["status"] == "ok"


async def test_path_traversal_is_refused(env: EnvSettings, web_dist: Path) -> None:
    async with running_app(env, web_dist=web_dist) as (_app, client):
        response = await client.get("/../../etc/passwd", headers={"accept": "text/html"})
        assert "root:" not in response.text


async def test_unknown_path_is_404_for_every_method(env: EnvSettings, web_dist: Path) -> None:
    """A GET-only catch-all makes Starlette answer 405 to a POST, which leaks
    that the path is routable. It is not: every verb gets the same JSON 404."""
    async with running_app(env, web_dist=web_dist) as (_app, client):
        for method in ("post", "put", "patch", "delete"):
            response = await getattr(client, method)("/shots/000123")
            assert response.status_code == 404, method
            body = response.json()
            assert body["ok"] is False
            assert body["error"]["code"] == "NOT_FOUND"
            assert body["meta"]["request_id"]


async def test_api_path_is_404_for_every_method_with_the_spa_mounted(
    env: EnvSettings, web_dist: Path
) -> None:
    async with running_app(env, web_dist=web_dist) as (_app, client):
        for method in ("get", "post", "delete"):
            response = await getattr(client, method)("/api/nope")
            assert response.status_code == 404, method
            assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_wrong_method_on_a_real_route_is_405_not_404(
    env: EnvSettings, web_dist: Path
) -> None:
    """The catch-all matches every method, so it also swallowed Starlette's 405.

    Without a partial-match check, POST /health answered 404 with the SPA built
    and 405 without it — the same request, two answers, depending on whether the
    front end happened to be compiled.
    """
    async with running_app(env, web_dist=web_dist) as (_app, client):
        for method, path in (
            ("post", "/health"),
            ("delete", "/api/settings"),
            ("get", "/api/backup"),
        ):
            response = await getattr(client, method)(path)
            assert response.status_code == 405, f"{method} {path}"
            body = response.json()
            assert body["ok"] is False
            assert body["error"]["code"] == "METHOD_NOT_ALLOWED"
            assert body["meta"]["request_id"]


async def test_405_matches_with_and_without_the_spa(
    env: EnvSettings, web_dist: Path, tmp_path: Path
) -> None:
    """Same request, same answer, whether or not the front end is built."""
    async with running_app(env, web_dist=tmp_path / "absent") as (_app, client):
        without = (await client.post("/health")).status_code
    async with running_app(env, web_dist=web_dist) as (_app, client):
        with_spa = (await client.post("/health")).status_code
    assert without == with_spa == 405

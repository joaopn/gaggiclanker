"""The backup and restore routes need a sign-in when it is on, every one of them.

(`test_guard.py` enumerates the whole router; this names the ones that can read the
archive, replace it or empty it, with the method each answers, so a loosened guard says which.)
"""

from __future__ import annotations

import httpx
import pytest

TOKEN = "0" * 32


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/backup"),
        ("get", "/api/backup?include_keys=true"),
        ("post", "/api/backup/restore"),
        ("delete", f"/api/backup/restore/{TOKEN}"),
        ("post", f"/api/backup/restore/{TOKEN}/apply"),
        ("post", "/api/reset"),
    ],
)
async def test_the_backup_routes_are_guarded(
    secured_client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await secured_client.request(method, path, content=b"x")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_with_a_token_the_download_works(
    secured_client: httpx.AsyncClient, bearer: dict[str, str]
) -> None:
    response = await secured_client.get("/api/backup", headers=bearer)

    assert response.status_code == 200
    assert response.content[:15] == b"SQLite format 3"

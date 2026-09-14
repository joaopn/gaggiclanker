"""Outbound HTTP to external services: credentials come from the database and nowhere else.

Every credential this app spends on an external service — an LLM provider's key
or token — is a secret setting, entered in the Settings page and stored in the
database. Removing the environment variables that used to carry them is not
enough on its own, because the libraries underneath have fallbacks of their
own: an HTTP client reads ``~/.netrc`` and mounts whatever proxies the
environment names (credentials included), and the SDKs read keys, extra
headers, organisation ids and base URLs from variables. Any one of those can
add a credential this app never stored, or send a stored one to a server the
person never chose.

So outbound clients are built here, from nothing the environment can smuggle a
credential through:

* ``trust_env=False`` — no ``.netrc``, and no proxy picked up behind our back;
* proxies from the environment are honoured only when they carry no userinfo
  (``user:pass@``). A proxy is a route, not a credential; one with credentials
  in it is refused at boot (see :func:`gaggiclanker.settings.retired_auth_env_keys`)
  and skipped here as well, so the rule holds even for a process that skipped
  the boot check;
* CA bundle variables (``SSL_CERT_FILE``, ``SSL_CERT_DIR``) are still honoured:
  they decide whom to trust, not what to present;
* redirects are not followed. An API does not redirect a request, and a
  redirect to another host would carry headers such as ``x-api-key`` with it.

The SDK clients built on top pass every credential, header and base URL
explicitly (``gaggiclanker/llm/providers/``). A future outbound connection
follows the same rule: its credential is a secret setting, and its client is
built from this module.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx2
import structlog
from httpx2._utils import get_environment_proxies

__all__ = [
    "PROXY_ENV_KEYS",
    "environment_proxy_mounts",
    "outbound_http_client",
    "url_carries_userinfo",
]

log = structlog.get_logger(__name__)

#: The proxy variables the HTTP stack and the Claude Code CLI read, compared
#: without regard to case (both spellings are honoured by curl, urllib and Node).
PROXY_ENV_KEYS: tuple[str, ...] = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")

#: Generous but finite: the providers pass their own per-request deadline, so
#: this only bounds a request made without one.
_DEFAULT_TIMEOUT = httpx2.Timeout(600.0, connect=10.0)
_DEFAULT_LIMITS = httpx2.Limits(max_connections=100, max_keepalive_connections=20)


def url_carries_userinfo(value: str) -> bool:
    """Whether a URL (or a bare ``host:port``) names a user or a password."""
    text = value.strip()
    if not text:
        return False
    netloc = urlsplit(text if "://" in text else f"http://{text}").netloc
    return "@" in netloc


def environment_proxy_mounts() -> dict[str, str | None]:
    """The environment's proxies as mount pattern -> proxy URL, credentials removed.

    ``None`` values are ``NO_PROXY`` exclusions. A proxy that carries userinfo is
    left out entirely — the name is logged, never the value — rather than used
    with its credentials stripped, because a proxy that needs them would fail in
    a way that looks like the provider being down.
    """
    mounts: dict[str, str | None] = {}
    for pattern, url in get_environment_proxies().items():
        if url is not None and url_carries_userinfo(url):
            log.warning("outbound_proxy_with_credentials_ignored", pattern=pattern)
            continue
        mounts[pattern] = url
    return mounts


def outbound_http_client(
    *, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    """An HTTP client for an external service that the environment cannot authenticate.

    ``transport`` replaces the network for a test; proxy mounts still apply to
    the URLs they match, exactly as they would in production.
    """
    verify = httpx2.create_ssl_context(trust_env=True)
    mounts: dict[str, httpx2.AsyncBaseTransport | None] = {
        pattern: None if url is None else httpx2.AsyncHTTPTransport(proxy=url, verify=verify)
        for pattern, url in environment_proxy_mounts().items()
    }
    return httpx2.AsyncClient(
        trust_env=False,
        verify=verify,
        mounts=mounts,
        transport=transport if transport is not None else httpx2.AsyncHTTPTransport(verify=verify),
        timeout=_DEFAULT_TIMEOUT,
        limits=_DEFAULT_LIMITS,
        follow_redirects=False,
    )

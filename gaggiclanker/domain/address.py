"""The machine's address, as a person types it and as the client needs it.

The setting holds a bare host (``192.168.1.50``, ``gaggimate.local``, or
``host:port`` for a simulator), and the client builds ``http://{host}`` and
``ws://{host}/ws`` from it. What a person copies, though, is the address bar of
the machine's own web UI: ``http://192.168.1.50/``. Put into those templates
unchanged, that is ``http://http://192.168.1.50/``, which aiohttp reads as a
host named ``http`` on port 80 — a name-resolution failure that says nothing
about the setting. So the forms that plainly mean the machine are reduced to
the bare host, and the ones that cannot work are refused when saved.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Schemes that name the machine's plain-HTTP server. ``https``/``wss`` are not
#: here: the firmware never terminates TLS, so they only make sense behind a
#: proxy, and that is what ``gaggimateProtocol`` says — not the host.
PLAIN_SCHEMES: tuple[str, ...] = ("http", "ws")


def _split(value: str) -> tuple[str, str, str]:
    """``(scheme, netloc, rest)`` of a typed address; ``scheme`` is lowercased."""
    text = value.strip()
    scheme, found, _ = text.partition("://")
    parts = urlsplit(text if found else f"//{text}")
    rest = parts.path + (f"?{parts.query}" if parts.query else "")
    rest += f"#{parts.fragment}" if parts.fragment else ""
    return (scheme.lower() if found else "", parts.netloc, rest)


def machine_host(value: str) -> str:
    """The bare ``host[:port]`` in a typed address; ``""`` for an empty one.

    Idempotent, and never raises: a stored value that :func:`host_problem`
    would refuse (from the environment, or saved before the check existed) is
    reduced the same way rather than failing the connection build.
    """
    if not value.strip():
        return ""
    _, netloc, _ = _split(value)
    return netloc


def host_problem(value: str) -> str | None:
    """Why a typed address cannot be used, or ``None``.

    Messages are fixed strings: they reach the client and never quote the value.
    """
    if not value.strip():
        return None
    scheme, netloc, rest = _split(value)
    if scheme in ("https", "wss"):
        return (
            "the machine serves plain HTTP: give the host without https:// or wss://, and set "
            "gaggimateProtocol to wss only when a TLS proxy sits in front of it"
        )
    if scheme and scheme not in PLAIN_SCHEMES:
        return "give the machine's hostname or IP, optionally with :port"
    if not netloc or "@" in netloc or rest not in ("", "/"):
        return "give the machine's hostname or IP, optionally with :port, and nothing after it"
    return None

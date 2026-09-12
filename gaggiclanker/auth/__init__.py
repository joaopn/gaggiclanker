"""Optional single-user authentication.

Off unless configured, which is the right default for a box on a home LAN that
nothing else can reach, and one environment pair away from on for a box that is
port-forwarded or shared with a flatmate.

Three modules:

``passwords``  argon2id hashing and a constant-time verify.
``service``    the whole policy — is auth on, issue a token, verify one, revoke
               one, and the login throttle.
``guard``      the pure-ASGI middleware that applies it to every ``/api/*``
               request, SSE streams and the OpenAPI document included.

Ported from cvclanker, with the
four things that assessment said were missing: a hashed password with a
constant-time compare, a login throttle, a public status probe so the UI can
show a sign-in page before the first 401, and a sign-out control.
"""

from __future__ import annotations

from gaggiclanker.auth.service import (
    AuthConfig,
    AuthService,
    AuthStatus,
    IssuedToken,
    LoginThrottle,
)

__all__ = ["AuthConfig", "AuthService", "AuthStatus", "IssuedToken", "LoginThrottle"]

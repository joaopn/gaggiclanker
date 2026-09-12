"""``/api/auth`` — sign in, sign out, and the public "is auth on?" probe.

Two of the four routes are reachable without a token (see
``gaggiclanker/auth/guard.py``). ``logout`` and ``password`` are not: revoking a
session, and replacing the credential that issues them, are things only somebody
already through the door should be able to ask for.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.api.deps import AuthServiceDep
from gaggiclanker.auth.guard import bearer_token
from gaggiclanker.auth.passwords import verify_password
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import Unauthorized

__all__ = ["client_address", "router"]

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    """Credentials. Never logged, and never echoed in a validation error."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=200)
    # The upper bound is not a policy on how long a password may be; it is a
    # bound on how much argon2 work one unauthenticated request can buy.
    password: str = Field(min_length=1, max_length=1024)


class LoginData(BaseModel):
    """What a successful login returns. The token is the whole credential."""

    token: str
    expires_in: int
    user: str


class LogoutData(BaseModel):
    revoked: bool


class PasswordBody(BaseModel):
    """A password change. The plain values never leave this request."""

    model_config = ConfigDict(extra="forbid")

    #: Required once a password is configured, ignored when none is. It is
    #: checked even though the guard has already authenticated the caller: a
    #: borrowed tab should not be able to lock its owner out, and this is the
    #: one request where that is cheap to prevent.
    current_password: str | None = Field(default=None, max_length=1024)
    #: Twelve is not a policy anybody will love, but a four-character password
    #: on a box somebody thought was worth locking is worse than no lock.
    new_password: str = Field(min_length=12, max_length=1024)


class PasswordData(BaseModel):
    """What changed, and what it cost."""

    #: Whether auth is now actually on. Setting a password with no `authUser`
    #: configured stores the password and leaves auth off, which is the order
    #: the Settings page uses.
    auth_required: bool
    sessions_revoked: bool


class AuthStatusData(BaseModel):
    """``auth_required`` is what the SPA reads before it has any token at all."""

    auth_required: bool
    authenticated: bool
    user: str | None = None


def client_address(request: Request) -> str:
    """The address the login throttle counts against.

    ``request.client``, not ``X-Forwarded-For``: a header any client can set is
    not an identity, and trusting one would let an attacker have a fresh five
    attempts per forged address. Behind a reverse proxy this collapses to the
    proxy's address, which means the throttle becomes global — on a single-user
    appliance that is the safe direction to be wrong in.
    """
    return request.client.host if request.client else "unknown"


@router.post(
    "/login",
    response_model=ApiResponse[LoginData],
    summary="Exchange credentials for a bearer token",
)
async def login(body: LoginBody, request: Request, auth: AuthServiceDep) -> JSONResponse:
    issued = await auth.login(
        body.username,
        body.password,
        client=client_address(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return envelope_response(
        LoginData(
            token=issued.token, expires_in=issued.expires_in, user=issued.subject
        ).model_dump()
    )


@router.post(
    "/logout",
    response_model=ApiResponse[LogoutData],
    summary="Revoke the session this token names",
)
async def logout(request: Request, auth: AuthServiceDep) -> JSONResponse:
    revoked = await auth.logout(bearer_token(request.headers))
    return envelope_response(LogoutData(revoked=revoked).model_dump())


@router.post(
    "/password",
    response_model=ApiResponse[PasswordData],
    summary="Set the sign-in password, hashing it on the server",
)
async def set_password(body: PasswordBody, request: Request, auth: AuthServiceDep) -> JSONResponse:
    """The only way the password is set from a browser.

    The plain password is hashed here and the hash is stored; the browser never
    sees a hash and the settings API refuses to take one
    (``authPasswordHash`` is read-only there). That is the whole reason this
    route exists: a masked text box on the Settings page labelled "password
    hash" invites somebody to type the *password* into it, and a stored value
    that is not a hash cannot authenticate anybody.

    Every open session is revoked, including the caller's own. A password is
    changed because the old one may be known to somebody else, and the tokens
    already issued are the credential now.
    """
    config = await auth.config()
    if config.enabled and config.usable:
        # Not `Forbidden`: to a client this is the same class of answer as a
        # failed sign-in, and the throttle on that route is the one that
        # matters. Here the caller already holds a valid token.
        if not body.current_password or not verify_password(
            config.password_hash, body.current_password
        ):
            raise Unauthorized("The current password is wrong")

    await auth.set_password(body.new_password)
    updated = await auth.config()
    return envelope_response(
        PasswordData(auth_required=updated.enabled, sessions_revoked=True).model_dump()
    )


@router.get(
    "/status",
    response_model=ApiResponse[AuthStatusData],
    summary="Whether this server needs a token, and whether you have a valid one",
)
async def status(request: Request, auth: AuthServiceDep) -> JSONResponse:
    result = await auth.status(bearer_token(request.headers))
    return envelope_response(result.to_api())

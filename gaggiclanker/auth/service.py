"""The whole auth policy in one object: is it on, issue, verify, revoke.

Design, and why each piece is the way it is:

**Off unless configured.** Auth is enabled exactly when ``authUser`` and
``authPasswordHash`` both resolve to something non-empty. Both are ordinary
registry settings, so either the environment or the Settings page can turn auth
on, and the switch is re-read on **every** request: flipping it must not need a
container restart, because the person flipping it is often locked out of the
container.

**Bearer JWT, but revocable.** The token is HS256 with a ``jti``, and a row in
``auth_sessions`` is written for that ``jti`` at login. The guard looks the row
up on every request, so ``POST /api/auth/logout`` ends the session now rather
than in thirty days. A JWT alone cannot do that; a session table alone would
need a second lookup anyway. The pair is cvclanker's auth design and it is the right one here.

**The signing secret lives in the database.** Generated once into
``runtime_secrets`` and cached in memory. A secret derived per boot would sign
every tab out on every restart; a secret in the environment would mean one more
thing to set, and one more thing to lose. ``AUTH_JWT_SECRET`` overrides it for
an operator who wants to share sessions between two processes.

**The password is never stored.** ``AUTH_PASSWORD_HASH`` takes an argon2id PHC
string. ``AUTH_PASSWORD`` takes the plain password, which
:meth:`AuthService.bootstrap` hashes once at boot into the settings table — the
plain value is deliberately *not* a registry key, so it never reaches
``GET /api/settings``, not even as a four-character hint.

**The throttle is in memory, per client IP.** Five failures buy a sixty-second
lock. In memory because this is one process on an appliance, and because a
throttle that writes to SQLite on every failed guess is itself the amplifier;
per IP because there is only one user, so per-account locking would hand any
passer-by a denial of service against the owner.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
import structlog

from gaggiclanker.auth.passwords import (
    hash_password,
    is_argon2_hash,
    verify_password,
    verify_user,
)
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.auth import AuthSessionsRepository, RuntimeSecretsRepository
from gaggiclanker.infra.errors import TooManyRequests, Unauthorized
from gaggiclanker.settings_service import SettingsService

__all__ = [
    "JWT_ALGORITHM",
    "JWT_SECRET_KEY",
    "LOCKOUT_SECONDS",
    "MAX_LOGIN_FAILURES",
    "MIN_ENV_SECRET_LENGTH",
    "AuthConfig",
    "AuthService",
    "AuthStatus",
    "IssuedToken",
    "LoginThrottle",
    "validate_env_secret",
]

log = structlog.get_logger(__name__)

#: HS256, not RS256: one process signs and the same process verifies, so an
#: asymmetric key buys nothing and costs a key pair to manage.
JWT_ALGORITHM = "HS256"

#: The row in ``runtime_secrets`` holding the signing key. (The name of a row,
#: not a secret — S105 cannot tell the difference.)
JWT_SECRET_KEY = "jwt_secret"  # noqa: S105

#: 48 bytes of urandom, base64url — 384 bits into an HMAC-SHA256, which is well
#: past the point where the hash is the weaker half.
_SECRET_BYTES = 48

MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 60

#: Shorter than this and ``AUTH_JWT_SECRET`` is somebody's idea of a password,
#: not a signing key. 32 characters is the width of the HMAC's own block.
MIN_ENV_SECRET_LENGTH = 32


def validate_env_secret(secret: str) -> None:
    """Raise if ``AUTH_JWT_SECRET`` is set and too short. Empty is fine.

    A free function, and called from two places on purpose:
    :meth:`AuthService.__init__` (so no instance can exist that cannot sign) and
    :func:`gaggiclanker.main.check_configuration`, which runs it **before the
    database is opened**. A check that only fires after `connect()` leaves a
    live aiosqlite worker thread behind when it raises, and that thread is not a
    daemon — the interpreter then waits for it for ever and the container never
    restarts. Pure-configuration checks belong before anything is opened.
    """
    value = secret.strip()
    if value and len(value) < MIN_ENV_SECRET_LENGTH:
        raise RuntimeError(
            f"AUTH_JWT_SECRET is {len(value)} characters; it must be at "
            f"least {MIN_ENV_SECRET_LENGTH}. Leave it unset to have one generated "
            "and kept in the database, which is what almost every install wants."
        )


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """The resolved switch, read fresh on every request.

    Three states, not two, and the third is the one that matters:

    ``user`` empty, or no password at all
        Auth is **off**. Nothing is configured; this is the default and it is
        the right one for a box only your own LAN can reach.
    ``user`` set and the hash usable
        Auth is **on** and works.
    ``user`` set and the hash unusable
        Auth is **on and broken** — enabled, and refusing every sign-in.

    That third state used to collapse into the first, and it was a hole: a
    password typed into the hash field left ``password_hash`` empty, which read
    as "not configured", which opened every route to everybody. A configuration
    error must never be the thing that takes the lock off the door.
    """

    user: str
    password_hash: str
    ttl_seconds: int
    #: Whether the stored hash is one argon2 could verify against.
    usable: bool = True

    @property
    def enabled(self) -> bool:
        """Whether a token is required. True even when the hash is unusable."""
        return bool(self.user) and bool(self.password_hash)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """What a successful login hands back."""

    token: str
    expires_in: int
    subject: str


@dataclass(frozen=True, slots=True)
class AuthStatus:
    """``GET /api/auth/status``. Public, so it says as little as it can."""

    auth_required: bool
    authenticated: bool
    user: str | None

    def to_api(self) -> dict[str, Any]:
        return {
            "auth_required": self.auth_required,
            "authenticated": self.authenticated,
            "user": self.user,
        }


@dataclass
class LoginThrottle:
    """Per-client-IP failure counting with a fixed lockout.

    Deliberately tiny. The bound on memory is the number of distinct client
    addresses that have failed a login inside the lockout window, and entries
    are dropped as soon as they expire, so a spray from a botnet costs a few
    hundred bytes per address for a minute.
    """

    max_failures: int = MAX_LOGIN_FAILURES
    lockout_seconds: int = LOCKOUT_SECONDS
    _failures: dict[str, tuple[int, float]] = field(default_factory=dict, repr=False)

    def _prune(self, now: float) -> None:
        stale = [
            key for key, (_, last) in self._failures.items() if now - last > self.lockout_seconds
        ]
        for key in stale:
            del self._failures[key]

    def retry_after(self, client: str, now: float | None = None) -> int:
        """Seconds the client must wait, or 0 when it may try."""
        moment = time.monotonic() if now is None else now
        self._prune(moment)
        entry = self._failures.get(client)
        if entry is None:
            return 0
        count, last = entry
        if count < self.max_failures:
            return 0
        remaining = self.lockout_seconds - (moment - last)
        return max(1, int(remaining + 0.999)) if remaining > 0 else 0

    def record_failure(self, client: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        self._prune(moment)
        count, _ = self._failures.get(client, (0, moment))
        # The timestamp moves on every failure, so a client that keeps guessing
        # keeps extending its own lockout rather than getting a fresh five
        # attempts a minute after the first one.
        self._failures[client] = (count + 1, moment)

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)


class AuthService:
    """Everything the guard and the auth routes need, and nothing else."""

    def __init__(
        self,
        db: Database,
        settings: SettingsService,
        *,
        throttle: LoginThrottle | None = None,
        env_secret: str = "",
    ) -> None:
        self.db = db
        self.settings = settings
        self.sessions = AuthSessionsRepository(db)
        self.secrets = RuntimeSecretsRepository(db)
        self.throttle = throttle or LoginThrottle()
        self._env_secret = env_secret.strip()
        # Belt and braces: the lifespan checks this before it opens anything
        # (see `validate_env_secret`), and this stops any *other* caller from
        # building a service that cannot sign. Deferring it to `secret()` turned
        # a typo in the compose file into a 500 on the first sign-in, hours
        # later.
        validate_env_secret(self._env_secret)
        self._secret: str | None = None

    # -- configuration ----------------------------------------------------

    async def config(self) -> AuthConfig:
        """The switch, resolved now.

        Short-circuits on an empty user so the common case — auth off — costs
        one point query rather than three.
        """
        user = str(await self.settings.get("authUser") or "").strip()
        if not user:
            return AuthConfig(user="", password_hash="", ttl_seconds=0)
        password_hash = str(await self.settings.get("authPasswordHash") or "").strip()
        usable = is_argon2_hash(password_hash) if password_hash else True
        if not usable:
            # Loud, and once per request is fine: it means nobody can sign in
            # and the operator needs to see why. The hash is deliberately NOT
            # blanked — blanking it would read as "auth not configured" and
            # open every route to everybody, which is the opposite of what a
            # broken credential should do.
            log.error(
                "auth_password_hash_unusable",
                expected="an argon2id hash, not a password",
                fix=(
                    "POST /api/auth/password with the new password, or set AUTH_PASSWORD "
                    "in the environment and restart"
                ),
            )
        ttl = int(await self.settings.get("authTokenTtlSeconds") or 0)
        return AuthConfig(user=user, password_hash=password_hash, ttl_seconds=ttl, usable=usable)

    async def enabled(self) -> bool:
        return (await self.config()).enabled

    async def secret(self) -> str:
        """The HS256 signing key: the environment's, or one generated once.

        Cached on the instance after the first read. The instance lives for the
        life of the process, and the row it caches is only written once.
        """
        if self._secret is not None:
            return self._secret
        if self._env_secret:
            # Its length was checked at construction, so a misconfigured box
            # never gets this far.
            self._secret = self._env_secret
            return self._secret
        self._secret = await self.secrets.get_or_create(
            JWT_SECRET_KEY, secrets.token_urlsafe(_SECRET_BYTES)
        )
        return self._secret

    async def bootstrap(self, plain_password: str) -> bool:
        """Seed the first password from ``AUTH_PASSWORD``. Returns whether it wrote.

        **Seeding, not syncing.** It writes only when nothing is configured at
        all, and never touches a password that is already set. The environment
        variable is how you get in on a fresh install; after that the password
        belongs to ``POST /api/auth/password``, and a change made there has to
        survive the next restart. A boot that re-hashed whatever
        ``AUTH_PASSWORD`` said would silently undo it — the operator would
        change the password, and it would revert at three in the morning when
        the container restarted, with nothing in the log to explain why.

        So an install that has a password and still carries ``AUTH_PASSWORD`` in
        its compose file is not a conflict: the file is the seed that was used
        once, and it is inert now. ``.env.example`` says so.

        The plain value is never stored and is not a registry key, so it cannot
        come back out of ``GET /api/settings``.
        """
        password = plain_password.strip()
        if not password:
            return False
        stored = str(await self.settings.get("authPasswordHash") or "").strip()
        if stored:
            if not is_argon2_hash(stored):
                # Not seeding over it either. A stored value that is not a hash
                # is a mistake somebody made deliberately, and overwriting it
                # from the environment would hide the mistake rather than fix
                # it; config() has already said what to do about it.
                log.warning("auth_password_env_ignored", reason="a password is already configured")
            return False
        await self.settings.store("authPasswordHash", hash_password(password))
        log.info("auth_password_seeded_from_env", env_key="AUTH_PASSWORD")
        return True

    async def set_password(self, new_password: str) -> None:
        """Hash and store a new password, then end every open session.

        Revoking is the point as much as the write is. A password is changed
        because the old one may be known to somebody else, and a change that
        left their thirty-day token working would be theatre — the tokens are
        the credential once they are issued, not the password.
        """
        await self.settings.store("authPasswordHash", hash_password(new_password))
        revoked = await self.sessions.revoke_all()
        log.info("auth_password_changed", sessions_revoked=revoked)

    async def cleanup(self) -> int:
        """Drop expired and revoked session rows. Runs at boot."""
        return await self.sessions.delete_stale(int(time.time()))

    # -- the token lifecycle ---------------------------------------------

    async def login(
        self,
        username: str,
        password: str,
        *,
        client: str,
        user_agent: str = "",
    ) -> IssuedToken:
        """Check the credentials and issue a token, or raise.

        Every rejection except the throttle is the same ``Unauthorized`` with
        the same message: telling a caller which half of the pair was wrong
        halves the work of guessing the other half.
        """
        config = await self.config()
        if not config.enabled:
            raise Unauthorized("Authentication is not enabled on this server")
        if not config.usable:
            # Enabled but broken. Refused before the throttle, because this is
            # not a wrong guess and should not spend the operator's five
            # attempts; and with a message that names the fix, because the
            # person hitting it is the person who has to make it.
            raise Unauthorized(
                "Sign-in is unavailable: the stored password hash is not an argon2 hash. "
                "Set a password with POST /api/auth/password, or put one in AUTH_PASSWORD "
                "and restart."
            )

        wait = self.throttle.retry_after(client)
        if wait:
            log.warning("auth_login_locked", client=client, retry_after=wait)
            raise TooManyRequests(
                "Too many failed sign-in attempts; try again shortly",
                retry_after=wait,
                details={"retry_after_seconds": wait},
            )

        # Both halves are checked even when the username is already wrong, so a
        # bad username and a bad password cost the same ~50 ms of argon2. A
        # short-circuit here is a username oracle with a stopwatch.
        user_ok = verify_user(config.user, username)
        password_ok = verify_password(config.password_hash, password)
        if not (user_ok and password_ok):
            self.throttle.record_failure(client)
            log.warning("auth_login_failed", client=client)
            raise Unauthorized("Invalid username or password")

        self.throttle.record_success(client)

        jti = uuid.uuid4().hex
        issued_at = int(time.time())
        expires_at = issued_at + config.ttl_seconds
        await self.sessions.create(
            jti=jti, subject=config.user, expires_at=expires_at, user_agent=user_agent
        )
        token = jwt.encode(
            {"sub": config.user, "jti": jti, "iat": issued_at, "exp": expires_at},
            await self.secret(),
            algorithm=JWT_ALGORITHM,
        )
        log.info("auth_login_ok", subject=config.user, expires_in=config.ttl_seconds)
        return IssuedToken(token=token, expires_in=config.ttl_seconds, subject=config.user)

    async def verify(self, token: str) -> str | None:
        """The subject a token authenticates, or ``None``. Never raises.

        Four things have to hold: the signature verifies, the claims are
        present, the session row exists and is neither revoked nor expired, and
        the subject still matches the configured user. The last one matters —
        renaming the user has to invalidate tokens issued to the old name, or
        the rename is cosmetic.

        A session issued before the stored hash became unusable stays valid.
        That is deliberate: it was authenticated properly, and it is the tab the
        operator fixes the mistake from. What an unusable hash stops is issuing
        a *new* one (see :meth:`login`).
        """
        if not token:
            return None
        try:
            payload = jwt.decode(
                token,
                await self.secret(),
                algorithms=[JWT_ALGORITHM],
                options={"require": ["exp", "sub", "jti"]},
            )
        except jwt.PyJWTError:
            return None

        subject = str(payload.get("sub") or "")
        jti = str(payload.get("jti") or "")
        if not subject or not jti:
            return None

        config = await self.config()
        if not config.enabled or not verify_user(config.user, subject):
            return None

        session = await self.sessions.get(jti)
        if session is None or not session.is_live(int(time.time())):
            return None
        return subject

    async def logout(self, token: str) -> bool:
        """Revoke the session a token names. Idempotent, and quiet about misses.

        A logout with a junk token is a 200 with ``revoked: false``: the caller
        is trying to end a session and the session is already over, which is the
        outcome they asked for. Reporting 401 there just makes sign-out fail on
        the one page where the token has already expired.
        """
        if not token:
            return False
        try:
            payload = jwt.decode(
                token,
                await self.secret(),
                algorithms=[JWT_ALGORITHM],
                # An expired token can still name a session worth revoking, and
                # this path only ever *removes* access.
                options={"require": ["jti"], "verify_exp": False},
            )
        except jwt.PyJWTError:
            return False
        jti = str(payload.get("jti") or "")
        if not jti:
            return False
        revoked = await self.sessions.revoke(jti)
        if revoked:
            log.info("auth_logout", jti=jti)
        return revoked

    async def status(self, token: str) -> AuthStatus:
        """The public probe: is auth on, and is this caller already through it."""
        config = await self.config()
        if not config.enabled:
            return AuthStatus(auth_required=False, authenticated=True, user=None)
        subject = await self.verify(token)
        return AuthStatus(auth_required=True, authenticated=subject is not None, user=subject)

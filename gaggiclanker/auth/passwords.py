"""Password hashing: argon2id, and a verify that cannot leak by timing.

argon2id rather than bcrypt: bcrypt silently truncates the password at 72
bytes (so a long passphrase is weaker than it looks) and has no memory cost,
which is the parameter that matters against the GPU somebody would actually
point at a hash they lifted from a backup of the SQLite file.

The parameters are argon2-cffi's own defaults deliberately. They are chosen to
land around 50 ms on ordinary hardware, and this hash is computed exactly once
per login attempt on a single-user appliance — there is no throughput argument
for weakening them, and a hand-tuned set here would be a number nobody could
justify in a year's time.
"""

from __future__ import annotations

import hmac

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerificationError

__all__ = ["hash_password", "is_argon2_hash", "verify_password", "verify_user"]

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """An argon2id PHC string for ``password``. Carries its own salt and parameters."""
    return _hasher.hash(password)


def is_argon2_hash(value: str) -> bool:
    """Whether ``value`` looks like something :func:`verify_password` could use.

    Used to tell a configured hash from a password somebody pasted into the
    hash field by mistake — which would otherwise fail every login with no
    explanation anywhere.
    """
    return value.startswith(("$argon2id$", "$argon2i$", "$argon2d$"))


def verify_password(stored_hash: str, password: str) -> bool:
    """Check ``password`` against ``stored_hash``. Never raises.

    argon2's verify is already constant-time with respect to the password, and
    every failure mode — wrong password, malformed hash, an argon2 backend
    error — returns ``False`` rather than propagating, so a login route cannot
    turn "the hash in the settings table is corrupt" into a 500 that tells an
    attacker something a 401 would not.
    """
    if not stored_hash or not password:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError, Argon2Error):
        return False


def verify_user(expected: str, supplied: str) -> bool:
    """Compare usernames without leaking the real one through timing.

    A plain ``==`` on a string returns at the first differing byte, which over
    enough attempts recovers the username a character at a time. The username is
    not the secret, but it is half of the credential pair and the fix costs one
    function call.
    """
    return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))

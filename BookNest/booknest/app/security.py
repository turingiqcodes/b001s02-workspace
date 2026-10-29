"""Password hashing (scrypt, standard library) and API key generation."""
import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2 ** 14, 8, 1
KEY_PREFIX = "bn_"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Checked when the username doesn't exist, so a failed login takes the same time either way.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    # Keys are long random strings, so a fast hash is enough to keep them out of the DB.
    return hashlib.sha256(key.encode()).hexdigest()

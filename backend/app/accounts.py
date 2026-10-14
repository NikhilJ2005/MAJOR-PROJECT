"""Platform logins: PBKDF2 password hashing (standard library) and signed JWT tokens."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

import jwt

from agent.config import settings

_ROUNDS = 240_000
TOKEN_HOURS = 24 * 7


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ROUNDS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, _, expected = stored.partition("$")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ROUNDS)
    return hmac.compare_digest(digest.hex(), expected)


def _secret() -> str:
    """SECRET_KEY from the environment, else one generated once and kept in the data folder."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = settings.data_dir / "secret.key"
    if not path.exists():
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    return path.read_text().strip()


def create_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": int(time.time()) + TOKEN_HOURS * 3600}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def user_id_from_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        return int(jwt.decode(token, _secret(), algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None

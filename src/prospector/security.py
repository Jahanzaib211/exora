"""Passwords (stdlib scrypt) and signed session tokens (HS256 JWT in an HttpOnly cookie)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt

from .models import utcnow

# scrypt cost: n=2**15, r=8, p=1 needs ~32 MB and ~0.1 s on this laptop - fine for a login form.
_N, _R, _P = 2**15, 8, 1
MIN_PASSWORD = 12


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def dummy_hash() -> str:
    """A real scrypt hash of a random value, used to burn the same CPU on an unknown email.

    `verify_password` costs ~0.1 s. Short-circuiting when the email does not exist made a login
    attempt against a known address measurably slower than an unknown one, which enumerates
    valid accounts from the outside.
    """
    return hash_password(secrets.token_urlsafe(32))


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD:
        problems.append(f"at least {MIN_PASSWORD} characters")
    if password.lower() == password or password.upper() == password:
        problems.append("mix upper and lower case")
    if not any(c.isdigit() for c in password):
        problems.append("include a digit")
    return problems


def make_token(user_id: int, role: str, secret: str, hours: int) -> str:
    now = utcnow()
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + timedelta(hours=hours)}
    return jwt.encode(payload, secret, algorithm="HS256")


def read_token(token: str, secret: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None

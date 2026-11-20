"""Passwords and JWTs."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

import jwt

JWT_SECRET = os.environ.get("VOYAGE_JWT_SECRET", "dev-only-voyage-jwt-secret-0123456789abcdef")
JWT_TTL_S = 3600


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    _, salt, digest = stored.split("$")
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return hmac.compare_digest(test, digest)


def issue_token(user_id: int, agency_id: str, role: str) -> str:
    t = int(time.time())
    claims = {"sub": str(user_id), "agency": agency_id, "role": role, "iat": t, "exp": t + JWT_TTL_S}
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


def read_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])

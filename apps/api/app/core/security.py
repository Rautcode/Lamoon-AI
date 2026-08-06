"""JWT + password primitives used by LocalIdentityProvider. Kept thin and
isolated so the whole auth mechanism swaps behind IdentityProvider (ADR-0008).
"""
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _pwd.verify(raw, hashed)


def encode_token(claims: dict, ttl: timedelta) -> str:
    s = get_settings()
    payload = {**claims, "exp": datetime.now(UTC) + ttl}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_alg)


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_alg])

"""JWT + password primitives used by LocalIdentityProvider. Kept thin and
isolated so the whole auth mechanism swaps behind IdentityProvider (ADR-0008).
"""
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings


def _pw(raw: str) -> bytes:
    # bcrypt caps input at 72 bytes; truncate (standard practice) so long
    # passphrases don't raise. Apply identically on hash and verify.
    return raw.encode("utf-8")[:72]


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(_pw(raw), bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    return bcrypt.checkpw(_pw(raw), hashed.encode("ascii"))


def encode_token(claims: dict, ttl: timedelta) -> str:
    s = get_settings()
    payload = {**claims, "exp": datetime.now(UTC) + ttl}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_alg)


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_alg])

"""Token revocation — a Redis deny-list keyed by jti. Redis is already a hard
dependency (Celery's broker), so this adds zero new infrastructure.

Design: revoke(jti, ttl) sets a key that expires exactly when the token
itself would have — the deny-list never grows unbounded and never needs a
sweep job. is_revoked() is checked once, at the point ALL access-token
verification already converges (IdentityProvider.verify_session), so it
applies everywhere for free; refresh tokens are checked explicitly in the
/auth/refresh route since they don't go through verify_session.

ponytail: this makes Redis a hard dependency for every authenticated
request, not just background jobs — if Redis is down, is_revoked() raises
and every caller (verify_session, /auth/refresh) already wraps auth in a
broad try/except that turns that into a 401. Fail-closed is the right
default for a security check; worth knowing this is now on the hot path.
"""
from datetime import UTC, datetime

import redis

from app.core.config import get_settings

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def revoke(jti: str, ttl_seconds: int) -> None:
    if jti and ttl_seconds > 0:
        _redis().setex(f"revoked:{jti}", ttl_seconds, "1")


def is_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    return bool(_redis().exists(f"revoked:{jti}"))


def ttl_from_exp(exp: int) -> int:
    """Seconds until a token's own `exp` — the deny-list entry should live
    exactly that long, no more (it's moot once the token expires anyway)."""
    return max(0, exp - int(datetime.now(UTC).timestamp()))

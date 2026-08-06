"""Ops feature flags (platform §5) — a table + a helper, deliberately not a
service. Distinct from entitlements (what you paid for); this is kill-switches
and gradual rollout. Reads from a `feature_flags(company_id, key, enabled)` row.
"""
from uuid import UUID


def enabled(key: str, company_id: UUID | str) -> bool:
    # ponytail: DB-backed lookup + short TTL cache when the first real flag lands.
    # Default-off is the safe kill-switch stance.
    return False

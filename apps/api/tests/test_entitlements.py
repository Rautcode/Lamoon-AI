"""The entitlement decision is money/access logic — it gets a check."""
from app.core.billing.entitlements import Entitlement, decide


def test_boolean():
    assert decide(Entitlement("module.ats", "boolean", 1), current=0, amount=1).allowed
    assert not decide(Entitlement("module.ats", "boolean", 0), current=0, amount=1).allowed


def test_limit():
    ent = Entitlement("employees", "limit", 100)
    assert decide(ent, current=99, amount=1).allowed        # 99+1 = 100, ok
    assert not decide(ent, current=100, amount=1).allowed    # would exceed


def test_quota():
    ent = Entitlement("ai_credits", "quota", 500)
    assert decide(ent, current=500, amount=10).allowed       # balance 500 >= 10
    assert not decide(ent, current=5, amount=10).allowed      # balance 5 < 10


def test_unknown_key_denies():
    assert not decide(None, current=0, amount=1).allowed

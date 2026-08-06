"""Payment gateway seam (platform §7). V1 = manual/offline provisioning.
Razorpay (India-first, GST invoices) lands only when self-serve checkout ships.
No payment integration before there are subscriptions to charge.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Receipt:
    id: str
    amount: float


class PaymentGateway(Protocol):
    async def create_subscription(self, company_id: str, plan_key: str) -> str: ...
    async def charge(self, company_id: str, amount: float, meta: dict) -> Receipt: ...


class ManualGateway:
    """V1: ops provisions subscriptions; invoicing is offline."""

    async def create_subscription(self, company_id: str, plan_key: str) -> str:
        raise NotImplementedError  # ponytail: ManualGateway is admin-driven, not code-driven yet

    async def charge(self, company_id: str, amount: float, meta: dict) -> Receipt:
        raise NotImplementedError


def get_payment_gateway() -> PaymentGateway:
    return ManualGateway()

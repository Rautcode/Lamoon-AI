"""Notification seam (platform §4). V1 = email (SMTP/Graph/Gmail per company);
SMS/Slack/Teams/WhatsApp later, same send() call.
"""
from typing import Protocol


class Notifier(Protocol):
    async def send(self, *, to: str, template: str, ctx: dict, channel: str = "email") -> None: ...


class EmailNotifier:
    """V1: templated email; provider (SMTP/Graph/Gmail) chosen per company."""

    async def send(self, *, to: str, template: str, ctx: dict, channel: str = "email") -> None:
        raise NotImplementedError  # ponytail: implement in V1 ATS phase


def get_notifier() -> Notifier:
    return EmailNotifier()

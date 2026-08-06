"""Notification seam (platform §4). V1 = email (SMTP); SMS/Slack/Teams/
WhatsApp later, same send() call. No smtp_host configured → logs instead of
sending, so dev/tests never need real credentials.
"""
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger("lamoon.notify")
_TEMPLATE_DIR = Path(__file__).parent / "templates"

# ponytail: process-local outbox for tests/dev-inspection, not a real mailbox.
outbox: list[dict] = []


class Notifier(Protocol):
    async def send(self, *, to: str, template: str, ctx: dict, channel: str = "email") -> None: ...


def render(template: str, ctx: dict) -> tuple[str, str]:
    """Template's first line is `Subject: ...`; the rest is the body. Both are
    `.format(**ctx)`'d — missing keys raise loudly rather than sending garbage."""
    raw = (_TEMPLATE_DIR / f"{template}.txt").read_text(encoding="utf-8")
    subject_line, _, body = raw.partition("\n")
    subject = subject_line.removeprefix("Subject:").strip()
    return subject.format(**ctx), body.strip().format(**ctx)


class EmailNotifier:
    """V1: templated email over SMTP. ponytail: one SMTP account for now;
    per-company sender (Graph/Gmail API) is a later swap behind this class."""

    async def send(self, *, to: str, template: str, ctx: dict, channel: str = "email") -> None:
        if channel != "email":
            raise NotImplementedError(f"channel '{channel}' not supported yet")
        subject, body = render(template, ctx)
        s = get_settings()
        if not s.smtp_host:
            logger.info("EMAIL (dev, not sent) to=%s subject=%r", to, subject)
            outbox.append({"to": to, "subject": subject, "body": body, "template": template})
            return

        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port) as server:
            server.starttls()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)


def get_notifier() -> Notifier:
    return EmailNotifier()

"""Celery app (ADR-0004). Three priority queues: high / normal / background
(platform §8). Resume AI → high, emails → normal, cleanup/reminders → background.
"""
import asyncio

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

_s = get_settings()

celery_app = Celery("lamoon", broker=_s.redis_url, backend=_s.redis_url)

celery_app.conf.update(
    task_default_queue="normal",
    task_queues={q: {"exchange": q, "routing_key": q} for q in ("high", "normal", "background")},
    task_acks_late=True,          # redeliver on worker crash (idempotent tasks assumed)
    task_reject_on_worker_lost=True,
    task_track_started=True,
    beat_schedule={
        # Workflow 2: Daily Auto Reject — Tier C/D past their 10-day grace window.
        "ats-auto-reject-daily": {
            "task": "app.workers.celery_app.auto_reject_stale",
            "schedule": crontab(hour=2, minute=0),  # low-traffic hour
            "options": {"queue": "background"},
        },
    },
)


@celery_app.task(name="app.workers.celery_app.auto_reject_stale")
def auto_reject_stale() -> dict[str, int]:
    from app.modules.ats.tasks import auto_reject_stale_all

    return asyncio.run(auto_reject_stale_all())


# ponytail: further task modules (interview reminders, follow-up, dedup cleanup —
# Workflows 4-6) register the same way once those modules exist.

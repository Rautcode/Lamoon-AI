"""Celery app (ADR-0004). Three priority queues: high / normal / background
(platform §8). Resume AI → high, emails → normal, cleanup/reminders → background.
"""
from celery import Celery

from app.core.config import get_settings

_s = get_settings()

celery_app = Celery("lamoon", broker=_s.redis_url, backend=_s.redis_url)

celery_app.conf.update(
    task_default_queue="normal",
    task_queues={q: {"exchange": q, "routing_key": q} for q in ("high", "normal", "background")},
    task_acks_late=True,          # redeliver on worker crash (idempotent tasks assumed)
    task_reject_on_worker_lost=True,
    task_track_started=True,
)

# ponytail: task modules (ats pipeline, email, cleanup) autodiscovered here once they exist.
# celery_app.autodiscover_tasks(["app.modules.ats", "app.modules.notifications"])

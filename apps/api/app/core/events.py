"""In-process domain-event dispatcher (ADR-0004, platform §1).

Publishers call publish(); handlers subscribe by event name and run async via
Celery. When a *separate service* must consume these, swap this module's body
for Redis Streams/Kafka — publishers don't change.
"""
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core import context


@dataclass
class DomainEvent:
    name: str
    company_id: str
    payload: dict
    correlation_id: str | None = field(default=None)


_subscribers: dict[str, list[Callable[[DomainEvent], None]]] = defaultdict(list)


def subscribe(event_name: str, handler: Callable[[DomainEvent], None]) -> None:
    _subscribers[event_name].append(handler)


def publish(event: DomainEvent) -> None:
    if event.correlation_id is None:
        event.correlation_id = context.correlation_id.get()
    # audit.record(event)  # ponytail: wire once the audit module exists
    for handler in _subscribers[event.name]:
        # ponytail: sync fan-out for skeleton; switch to celery .delay() for
        # real side effects so a slow handler can't block the request.
        handler(event)

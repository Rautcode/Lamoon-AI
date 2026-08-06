"""The five identifiers that travel with every request/task/event (platform §10).

Stored in ContextVars so they're available to logging, the DB tenant guard,
Celery task headers, and DomainEvents without threading them through every call.
"""
from contextvars import ContextVar
from dataclasses import dataclass

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)  # company_id
user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
job_id: ContextVar[str | None] = ContextVar("job_id", default=None)


@dataclass
class RequestContext:
    request_id: str | None = None
    correlation_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    job_id: str | None = None


def current() -> RequestContext:
    return RequestContext(
        request_id=request_id.get(),
        correlation_id=correlation_id.get(),
        tenant_id=tenant_id.get(),
        user_id=user_id.get(),
        job_id=job_id.get(),
    )

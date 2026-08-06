"""Search seam (ADR-0007, platform §6). V1 = Postgres FTS (tsvector + trigram),
inside the RLS tenant boundary. Meilisearch/ES later if relevance demands it.
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass
class Hit:
    kind: str
    id: str
    score: float


class Search(Protocol):
    async def query(self, *, company_id: UUID, q: str, kind: str) -> list[Hit]: ...


class PgSearch:
    """V1: query generated tsvector columns; index() is a no-op (data is in-row)."""

    async def query(self, *, company_id: UUID, q: str, kind: str) -> list[Hit]:
        raise NotImplementedError  # ponytail: implement alongside the first searchable table


def get_search() -> Search:
    return PgSearch()

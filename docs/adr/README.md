# Architecture Decision Records

Short records of the foundational decisions. Each: context, decision,
consequences, alternatives. Once **Accepted**, a decision is frozen — revisit
only on a real implementation constraint, not to re-debate.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith over microservices | Accepted |
| [0002](0002-shared-schema-rls.md) | Shared-schema multi-tenancy with Postgres RLS | Accepted |
| [0003](0003-fastapi.md) | FastAPI over NestJS / Spring Boot | Accepted |
| [0004](0004-celery-over-kafka.md) | Celery over Kafka for background work | Accepted |
| [0005](0005-gemini-flash.md) | Gemini 2.5 Flash as default model | Accepted |
| [0006](0006-google-drive-storage.md) | Google Drive before S3 | Accepted |
| [0007](0007-postgres-fts.md) | Postgres FTS over Elasticsearch | Accepted |
| [0008](0008-inapp-rbac.md) | In-app RBAC over Keycloak (V1) | Accepted |

Format: context / decision / consequences / alternatives. Supersede with a new
ADR rather than editing an Accepted one.

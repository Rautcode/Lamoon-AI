-- The default POSTGRES_USER (see docker-compose.yml) is created as a
-- SUPERUSER by the official postgres image, and superusers implicitly
-- BYPASSRLS — silently defeating every RLS policy (ADR-0002), even with
-- FORCE ROW LEVEL SECURITY. The application must NEVER connect as that role.
--
-- This role is what the API, workers, and migrations actually use. It owns
-- nothing special and has no bypass — RLS applies to it for real.
CREATE ROLE app WITH LOGIN PASSWORD 'app' NOSUPERUSER NOBYPASSRLS;
GRANT ALL ON SCHEMA public TO app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO app;

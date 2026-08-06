# ADR 0008 — In-app RBAC over Keycloak (V1)

**Status:** Accepted · 2026-08-06

## Context
V1 needs role-based permissions (admin/hr/manager/employee) and login via
password + Google/Microsoft OAuth. Enterprise SSO (SAML/OIDC via an external
IdP) is a Phase-4 concern tied to paying enterprise demand.

## Decision
In-app RBAC: `roles`, `role_permissions` tables and a `require(permission)`
FastAPI dependency. All auth sits behind an `IdentityProvider` interface
(ARCHITECTURE.md §6) so an external IdP can be added without touching business
logic. V1 ships `LocalIdentityProvider` (password + OAuth).

## Consequences
- Permissions are one table and a six-line dependency — trivial to run and
  reason about at SME scale.
- No standing Java service (Keycloak realm, admin, upgrades) to operate pre-
  revenue.
- Adding Keycloak/SAML later = a new `IdentityProvider` implementation
  registered in one place; `users` rows map onto external identities via
  `oauth_provider` / nullable `password_hash`. Zero module changes.
- Row-level scoping (a manager sees only reports) rides on RLS + query filter,
  kept separate from permission strings.

## Alternatives considered
- **Keycloak from day one** — rejected for V1: heavyweight IdP for what is one
  table today; operational cost without a paying SSO customer. **Trigger to
  adopt:** an enterprise deal requires SAML/OIDC SSO or centralized identity.
- **Auth0 / hosted IdP** — rejected for V1: recurring per-MAU cost added before
  it's needed; reconsider alongside Keycloak at the SSO trigger.

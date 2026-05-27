# Backend Architecture

## Current Scope

- Auth module: registration, login, refresh rotation, logout.
- Users module: user model, profile read/update.
- Cabinet module: protected account area.
- Deposits module: pending SBP deposit creation, antifraud blacklist check.
- Webhooks module: SBP payment and chargeback webhooks.
- Integrations package: explicit stubs for future SBP/BingX clients.

## Domain Terms

- `User.id`: internal UUID primary key in our database.
- `User.bingx_uid`: internal account UID on BingX, used later for exchange-side transfer flows.
- JWT `sub`: our `User.id`, not BingX UID.

## Boundaries

- `app/core`: settings, DB connection pool, JWT/password helpers, middleware, common errors.
- `app/modules/auth`: auth routes, refresh token storage, auth dependencies.
- `app/modules/users`: user model, schemas, service, profile routes.
- `app/modules/cabinet`: protected cabinet route.
- `app/modules/deposits`: transaction creation and audit records.
- `app/modules/webhooks`: provider callbacks, KYC capture, chargeback antifraud.
- `app/integrations/bingx`: future BingX API client stub.
- `app/integrations/sbp`: future SBP provider client stub.

## Database

Runtime DB is PostgreSQL through `DATABASE_URL` and direct `asyncpg` queries.

Tests use PostgreSQL and expect migrations to be applied before the test run.

Schema changes go through Alembic migrations. SQLAlchemy metadata is kept only for migration autogenerate.

# MoneyWay Backend

FastAPI backend: auth, JWT sessions, user profile, cabinet boundary.

## Runtime

- Python: `3.12`
- DB: PostgreSQL via `asyncpg` and `DATABASE_URL`
- Migrations: Alembic
- Package/runtime: `uv`

## Endpoints

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/users/me`
- `PATCH /api/v1/users/me`
- `GET /api/v1/cabinet/me`
- `POST /api/v1/deposits`
- `POST /api/v1/webhooks/sbp/payment`
- `POST /api/v1/webhooks/sbp/chargeback`

## Config

Config comes from environment variables. `.env.example` is only a local template.

Required runtime values:

- `DATABASE_URL=postgresql://...`
- `SECRET_KEY=...`
- `JWT_ISSUER=moneyway-api`
- `JWT_AUDIENCE=moneyway-clients`
- `DEPOSIT_MIN_RUB=5000`
- `DEPOSIT_MAX_RUB=50000`
- `DEPOSIT_COMMISSION_PERCENT=2`
- `RUB_USDT_RATE=100`
- `BINGX_API_KEY=...`
- `BINGX_SECRET_KEY=...`
- `WEBHOOK_SECRET=...`

## Local Commands

```bash
uv sync --dev
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

## Checks

```bash
uv run ruff check .
uv run pytest
```

## Notes

- `id` fields are internal UUIDs for our database records.
- `bingx_uid` is the user's internal account UID on BingX.
- SQLAlchemy is used by Alembic migrations only; runtime DB access is direct `asyncpg`.
- `app/integrations/*` contains intentional stubs and small clients for future SBP/BingX implementation.

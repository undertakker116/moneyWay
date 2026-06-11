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

## Docker (release)

One command brings up Postgres, runs migrations, then starts the API and the transfer worker
(two containers from the same image):

```bash
docker compose up --build          # everything
docker compose up -d --scale worker=3   # 3 workers (SKIP LOCKED — safe)
docker compose up api              # API-only demo (pulls postgres + migrate; no BingX keys needed)
```

- `migrate` runs `alembic upgrade head` once and exits; `api` and `worker` wait for it.
- API on `http://localhost:8000` (liveness `/api/v1/health`, readiness `/api/v1/health/ready`).
- Config comes from env. Without a `.env` it boots in `local` mode with safe defaults. For release,
  create `.env` with `ENVIRONMENT=production`, a real `SECRET_KEY` (≥32 chars), `WEBHOOK_SECRET`,
  `BINGX_API_KEY`/`BINGX_SECRET_KEY`, and your domain in `TRUSTED_HOSTS` — the app refuses to start
  on missing secrets outside local/test. The `worker` needs the BingX keys to run.

## Admin panel

A starlette-admin UI (Django-admin style) over the tables, mounted at `/admin`. **Off by default**
(`ADMIN_ENABLED=false`). Enable for dev with `ADMIN_ENABLED=true` and log in with an `admin`-role user.

```bash
# promote a user to admin (no API for this — by design)
psql "$DATABASE_URL" -c "UPDATE users SET role='admin' WHERE email='you@example.com';"
# then open http://localhost:8000/admin
```

Financial/audit tables are read-only; only promo codes, blacklist and fraud alerts are editable.
Login is rate-limited, cookie is `SameSite=strict`. In production keep it disabled unless `/admin` is
network-restricted (VPN/IP-allowlist) and set a distinct `ADMIN_SESSION_SECRET`.

## Local Commands

```bash
uv sync --dev
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

## BingX transfer worker

Pending USDT transfers are sent by a separate worker process (the queue is the `bingx_transfers`
table — no Celery/Redis broker).

```bash
# continuous: LISTEN/NOTIFY wakes it instantly on a new transfer, polls as a fallback
uv run python -m app.workers.bingx_transfers

# one-shot: drain the queue once and exit (for cron / k8s CronJob)
uv run python -m app.workers.bingx_transfers --once
```

It signs requests per the BingX spec (HMAC-SHA256 over the alphabetically sorted query string,
`recvWindow`, `transferClientId` for idempotency) and routes BingX responses: duplicate →
completed, insufficient balance → `on_hold` + alert, recipient not found → `failed`, transient
errors → retry with backoff. USDT is released only after the `BINGX_TRANSFER_HOLD_MINUTES`
chargeback hold (`scheduled_at`).

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

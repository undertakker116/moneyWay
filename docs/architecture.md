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

JSON columns (`audit_log.payload`, `fraud_alerts.metadata`, `bingx_transfers.bingx_response`) are
`jsonb`. FKs into financial/audit tables (`transactions`, `fraud_alerts`, `audit_log`) use
`ON DELETE RESTRICT` on purpose: history must not disappear when a user/transaction is deleted.

## Money-path decisions

These are intentional behaviours, not accidents — change them deliberately.

- **Commission model = "from the sum".** `calculate_deposit_amounts` deducts the platform
  commission before converting to USDT (`amount_usdt = (amount_rub - commission) / rate`), matching
  the TЗ "Расчёт суммы USDT" flowchart. The platform keeps the commission; the user receives the net.
- **Source of truth for the paid amount is the transaction, not the webhook.** A paid webhook whose
  `amount_rub` disagrees with the stored transaction does not move money — it opens an
  `amount_mismatch` fraud alert and is left `pending` for manual review.
- **`amount_usdt` is frozen at deposit-creation time** using the rate then in effect, and that exact
  amount is sent to BingX regardless of later RUB/USDT drift. If a quote TTL / re-quote-at-payment is
  wanted, that is a separate product decision.
- **Status machine.** `pending → paid → completed`; `pending → failed`; a chargeback on an already-paid
  transaction → `chargeback` and cancels any still-`pending`/`processing` BingX transfer. Terminal
  states are never rolled back by late/duplicate webhooks.
- **BingX client** (`app/integrations/bingx/client.py`) is async (httpx). It signs every request per the
  BingX spec: HMAC-SHA256 over the **alphabetically sorted** query string, with `recvWindow` (replay
  window) and `timestamp`; the signed string is the POST body (`x-www-form-urlencoded`). Internal
  transfer uses `coin=USDT, userAccountType=1 (UID), walletType=1 (Fund), transferClientId=<idempotency_key>`.
- **Transfer idempotency + response routing.** `transferClientId = idempotency_key` makes a retry or a
  crash-before-record deduplicated by BingX instead of paying twice. The worker routes the BingX answer:
  duplicate → `completed` (idempotent), insufficient balance → `on_hold` + `low_balance` alert (no retry
  burn; admin tops up the Fund account and resets to `pending`), recipient not found → `failed` (+ txn
  `failed`), any other API/network error → retry up to `MAX_TRANSFER_ATTEMPTS` then `error`. Stuck
  `processing` rows are reclaimed after `BINGX_TRANSFER_STALE_SECONDS`. All `transactions` writes are
  guarded by `WHERE status='paid'`, so a late completion/error never clobbers a chargeback.
  - BingX error categories are matched on the human-readable `msg` (codes aren't fully documented);
    `BingXClient.list_internal_transfers` is available for manual reconciliation.

## Scaling & operations

Built for horizontal scale without extra infrastructure.

- **No Celery, no Redis, no RabbitMQ.** The transfer queue *is* the `bingx_transfers` table (a
  transactional outbox — the transfer row commits atomically with `status='paid'`, so a task can never
  be lost or fired before commit, unlike a naive `broker.delay()` dual-write). The worker pulls work with
  `FOR UPDATE SKIP LOCKED`, so many workers run at once with no double-send and no broker.
- **Worker modes.** Continuous (`python -m app.workers.bingx_transfers`): a dedicated `LISTEN`
  connection on the `bingx_transfers` channel is woken by the webhook's `NOTIFY` for near-real-time
  payout, with `WORKER_POLL_INTERVAL_SECONDS` as a fallback that also picks up expired holds, due
  retries, and any missed notifications. One-shot (`--once`): drain once and exit, for cron / CronJob.
- **Chargeback hold.** `bingx_transfers.scheduled_at = paid_at + BINGX_TRANSFER_HOLD_MINUTES`; the claim
  query ignores a transfer until `scheduled_at <= now()`. This is the 15–30 min window in which a
  reversed/fraudulent SBP payment surfaces (chargeback webhook cancels the still-`pending` transfer)
  before irreversible USDT goes out. The same predicate also enforces retry backoff.
- **Do we need Redis?** Not for the queue. Consider it only for: (a) a *global* rate limit shared across
  instances (the built-in limiter is per-process — see Security), (b) caching, or (c) if you later adopt
  Celery and want a broker. For the current design Postgres is enough.
- **When Celery would help:** scheduled beat jobs, fan-out to many task types, or per-task retry/visibility
  tooling. Until then the Postgres queue + a periodic drain is simpler and has fewer moving parts.
- **Horizontal scaling.** App instances are stateless except the in-process rate limiter; run N replicas
  behind a load balancer (`uvicorn --workers` / gunicorn / k8s replicas). Workers scale independently.
- **DB connections.** Each instance opens `DB_POOL_MAX_SIZE` (+ a small aux pool) connections. Keep
  `max_size * instances + workers < Postgres max_connections`; put PgBouncer in front if you outgrow it.
  Connection-level `statement_timeout` / `lock_timeout` / `idle_in_transaction_session_timeout` stop a
  stuck query from pinning a pooled connection.
- **Event loop stays free.** argon2 hashing/verify run in a thread pool (`asyncio.to_thread`) and the BingX
  client is async (httpx) — no CPU/IO blocks the loop under load.
- **Health.** `GET /api/v1/health` is liveness (no DB); `GET /api/v1/health/ready` pings the DB (503 if
  down) so the LB stops routing to a broken instance.

## Promo codes & admin

- **Promo codes** (`promo_codes` table) give a percentage discount on the *commission*
  (`calculate_deposit_amounts`). Redemption is atomic — `reserve_promo_code` does
  `UPDATE ... WHERE used_count < max_uses RETURNING` inside the request transaction, so the limit
  can't be exceeded under concurrency and an unused redemption rolls back with a failed deposit.
  A use is **released** (`release_promo_code`) when the payment is rejected (`failed`) or reversed
  (`chargeback`). Known limitation: a deposit created but never paid (abandoned `pending`) holds its
  reservation until a deposit-expiry sweep exists — add one if abandoned-deposit promo griefing matters.
- **Device-fingerprint antifraud** flags (does not block) when the same `X-Device-Fingerprint`
  appears on a *paid/completed* transaction of another user → a `device_reuse` fraud alert for manual
  review (indexed lookup via `ix_transactions_device_fingerprint`).
- **Admin RBAC** (`/api/v1/admin/*`, role `admin` via `get_current_admin`): transaction dossier for
  bank disputes (KYC + transfer + fraud alerts + audit — and the access itself is audited as
  `dossier_viewed`), fraud-alert queue, and manual blacklist add/remove.
- **Admin panel** (starlette-admin, mounted at `/admin`, `ADMIN_ENABLED`, **off by default**): a
  Django-admin-style UI over the tables. Security model:
  - Session login, **role=admin re-checked on every request** (not just at login); password via argon2.
  - Financial/audit tables (`transactions`, `bingx_transfers`, `kyc_records`, `audit_log`, `users`) are
    **read-only** (server-side 403, not just hidden buttons) — direct edits would bypass the asyncpg
    money guards. Only `promo_codes`, `blacklist`, `fraud_alerts` are writable.
  - `password_hash` is never exposed (field allow-list). Login is rate-limited (5/min/IP, anti-brute).
  - Session cookie: httponly, `SameSite=strict` (CSRF mitigation — starlette-admin has no CSRF tokens),
    `Secure` in production, signed with a **separate** `ADMIN_SESSION_SECRET`.
  - **Operational requirement:** the panel is a powerful surface — in production keep `ADMIN_ENABLED`
    off unless access to `/admin` is network-restricted (VPN / IP all-list / reverse-proxy auth). The
    login `?next=` param is library-controlled and only acts after valid credentials (low-risk
    open-redirect) — the network restriction is the real control.
- **Behind a reverse proxy** set `TRUSTED_PROXY=true` so the real client IP is read from `X-Real-IP`
  (for rate-limit keys, `payer_ip`, and IP blacklisting) instead of the proxy's address.

## Not yet implemented (external dependencies)

Deliberately stubbed with clear seams — they need a provider/credentials, not more code structure:
SBP payment creation (`payment_url` is `None`), live RUB/USDT rate (`RUB_USDT_RATE` is static, not
"bank rate + 2%"), BingX UID pre-validation at deposit time, and client notifications.

## Security posture

- **Webhook auth is fail-closed.** `WEBHOOK_SECRET` is required outside `local`/`test`; a missing
  secret makes webhooks answer `503`, never accept unauthenticated calls. `SECRET_KEY` must be a long
  random value outside `local`/`test`.
- **Refresh tokens use rotation + reuse detection.** Each session is a token `family`; presenting an
  already-revoked refresh token revokes the whole family (OAuth 2.0 BCP).
- **Chargeback antifraud** auto-blacklists `email` and `uid` only — never the shared `payer_ip`
  (CGNAT collateral). The IP and the full dossier live in the `fraud_alerts` record for manual action.
- **Rate limiting** (`app/core/ratelimit.py`) is a coarse in-process backstop on auth/webhook/deposit
  endpoints; production must also enforce limits at the gateway/WAF. Disable with `RATE_LIMIT_ENABLED=false`.

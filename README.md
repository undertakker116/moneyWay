# MoneyWay Backend

FastAPI backend для базовой аутентификации и личного кабинета.

## Что Уже Есть

- `POST /api/v1/auth/register` - регистрация и выдача JWT-пары.
- `POST /api/v1/auth/login` - вход по email/паролю.
- `POST /api/v1/auth/refresh` - обновление JWT с ротацией refresh-токена.
- `POST /api/v1/auth/logout` - отзыв refresh-токена.
- `GET /api/v1/users/me` / `PATCH /api/v1/users/me` - профиль.
- `GET /api/v1/cabinet/me` - профиль текущего пользователя как старт личного кабинета.


```bash
cp .env.example .env
```

Главные переменные:

- `DATABASE_URL` - строка подключения к базе. Локально можно SQLite, на сервере обычно `postgresql+asyncpg://...`.
- `SECRET_KEY` - ключ подписи JWT. В production должен быть длинным секретом из secret manager.
- `AUTO_CREATE_TABLES` - по умолчанию `false`. Таблицы не создаются приложением автоматически.
- `TRUSTED_HOSTS` - список разрешенных host.
- `CORS_ORIGINS` - список frontend-origin.

## Локальный Запуск

```bash
uv sync --dev
cp .env.example .env
mkdir -p .local
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Swagger локально: `http://127.0.0.1:8000/docs`.

## Тесты И Проверки

```bash
uv run pytest
uv run ruff check .
```

## Архитектура

Подробно: [docs/architecture.md](/Users/user/Desktop/moneyWay/docs/architecture.md).

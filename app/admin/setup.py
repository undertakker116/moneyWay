from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette_admin.contrib.sqla import Admin

from app.admin.auth import AdminAuthProvider
from app.admin.middleware import AdminLoginRateLimitMiddleware
from app.admin.views import (
    AuditLogView,
    BingxTransferView,
    BlacklistView,
    FraudAlertView,
    KycRecordView,
    PromoCodeView,
    TransactionView,
    UserView,
)
from app.core.config import Settings
from app.models import (
    AuditLog,
    BingxTransfer,
    Blacklist,
    FraudAlert,
    KycRecord,
    PromoCode,
    Transaction,
    User,
)


def mount_admin(app: Starlette, engine: AsyncEngine, settings: Settings) -> None:
    """Монтирует starlette-admin на /admin: сессионный логин, role=admin, read-only на финансах."""
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    admin = Admin(
        engine,
        title="MoneyWay Admin",
        base_url="/admin",
        auth_provider=AdminAuthProvider(sessionmaker),
        middlewares=[
            # Троттл логина (брутфорс) — раньше сессии/авторизации.
            Middleware(AdminLoginRateLimitMiddleware, settings=settings),
            Middleware(
                SessionMiddleware,
                # Отдельный секрет от JWT (key separation), fallback на SECRET_KEY для dev.
                secret_key=settings.admin_session_secret or settings.secret_key,
                https_only=settings.is_production,
                # strict: cookie не уходит на кросс-сайт POST → защита от CSRF (у starlette-admin
                # нет CSRF-токенов). Прод дополнительно ограничивает /admin по сети/IP.
                same_site="strict",
            ),
        ],
    )
    admin.add_view(UserView(User, icon="fa fa-user", label="Users"))
    admin.add_view(TransactionView(Transaction, icon="fa fa-money-bill", label="Transactions"))
    admin.add_view(BingxTransferView(BingxTransfer, icon="fa fa-paper-plane", label="Transfers"))
    admin.add_view(KycRecordView(KycRecord, icon="fa fa-id-card", label="KYC"))
    admin.add_view(PromoCodeView(PromoCode, icon="fa fa-tags", label="Promo codes"))
    admin.add_view(BlacklistView(Blacklist, icon="fa fa-ban", label="Blacklist"))
    admin.add_view(FraudAlertView(FraudAlert, icon="fa fa-triangle-exclamation", label="Fraud"))
    admin.add_view(AuditLogView(AuditLog, icon="fa fa-list", label="Audit log"))
    admin.mount_to(app)

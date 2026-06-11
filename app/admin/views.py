from starlette.requests import Request
from starlette_admin.contrib.sqla import ModelView

# В starlette-admin can_create/can_edit/can_delete/can_view_details — это МЕТОДЫ
# (self, request) -> bool, а не атрибуты. Переопределяем методами.


class ReadOnlyView(ModelView):
    """Только просмотр — для финансовых/аудиторских таблиц (правка в обход гардов запрещена)."""

    page_size = 25

    def can_create(self, request: Request) -> bool:
        return False

    def can_edit(self, request: Request) -> bool:
        return False

    def can_delete(self, request: Request) -> bool:
        return False


class UserView(ReadOnlyView):
    # Allow-list полей: password_hash не попадает ни в список, ни в detail, ни в export.
    fields = [
        "id",
        "email",
        "full_name",
        "bingx_uid",
        "role",
        "is_active",
        "is_verified",
        "created_at",
        "last_login_at",
    ]


class TransactionView(ReadOnlyView):
    pass


class KycRecordView(ReadOnlyView):
    pass


class BingxTransferView(ReadOnlyView):
    pass


class AuditLogView(ReadOnlyView):
    pass


class FraudAlertView(ModelView):
    """Алерты можно разбирать (менять только статус), но не создавать/удалять/править улики."""

    page_size = 25
    # Поля-улики неизменяемы: правится только status.
    exclude_fields_from_edit = [
        "user_id",
        "transaction_id",
        "alert_type",
        "alert_metadata",
        "created_at",
    ]

    def can_create(self, request: Request) -> bool:
        return False

    def can_delete(self, request: Request) -> bool:
        return False


class PromoCodeView(ModelView):
    """CRUD промокодов. used_count — системный счётчик, руками не редактируется."""

    page_size = 25
    exclude_fields_from_create = ["used_count"]
    exclude_fields_from_edit = ["used_count"]


class BlacklistView(ModelView):
    """Полный CRUD — ручной бан/разбан."""

    page_size = 25

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


@dataclass(slots=True)
class User:
    id: str
    email: str
    password_hash: str
    full_name: str | None
    bingx_uid: str | None
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None

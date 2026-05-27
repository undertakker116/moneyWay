from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.users.models import UserRole


class UserPublic(BaseModel):
    id: str
    email: EmailStr
    full_name: str | None = None
    bingx_uid: str | None = None
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    bingx_uid: str | None = Field(default=None, min_length=3, max_length=64)

    model_config = ConfigDict(extra="forbid")

    @field_validator("bingx_uid", mode="before")
    @classmethod
    def normalize_bingx_uid(cls, value: object) -> object:
        """Обрезает пробелы и превращает пустой BingX UID в None."""
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            return None
        return value

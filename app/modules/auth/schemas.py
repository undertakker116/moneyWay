from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    bingx_uid: str | None = Field(default=None, min_length=3, max_length=64)

    model_config = ConfigDict(extra="forbid")

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        """Проверяет минимальную сложность пароля при регистрации."""
        if (
            value.lower() == value
            or value.upper() == value
            or not any(ch.isdigit() for ch in value)
        ):
            raise ValueError("Password must include uppercase/lowercase letters and a digit.")
        return value

    @field_validator("bingx_uid", mode="before")
    @classmethod
    def normalize_bingx_uid(cls, value: object) -> object:
        """Обрезает пробелы вокруг BingX UID до основной валидации поля."""
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            return None
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    model_config = ConfigDict(extra="forbid")


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=20)

    model_config = ConfigDict(extra="forbid")


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=20)

    model_config = ConfigDict(extra="forbid")


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthMessage(BaseModel):
    detail: str

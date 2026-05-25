from fastapi import HTTPException, status


class ConflictError(HTTPException):
    def __init__(self, detail: str) -> None:
        """Возвращает 409, когда ресурс уже существует или конфликтует."""
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class UnauthorizedError(HTTPException):
    def __init__(self, detail: str = "Authentication required") -> None:
        """Возвращает 401 и Bearer challenge для ошибок аутентификации."""
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(HTTPException):
    def __init__(self, detail: str = "Forbidden") -> None:
        """Возвращает 403, когда пользователь аутентифицирован, но доступа нет."""
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

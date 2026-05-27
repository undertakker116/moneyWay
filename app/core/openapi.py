from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def is_default_validation_response(response: dict[str, Any]) -> bool:
    schema = (
        response.get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("$ref", "")
    )
    return response.get("description") == "Validation Error" or schema.endswith(
        "/HTTPValidationError"
    )


def remove_default_validation_docs(openapi_schema: dict[str, Any]) -> None:
    for path_item in openapi_schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            if is_default_validation_response(responses.get("422", {})):
                responses.pop("422", None)

    schemas = openapi_schema.get("components", {}).get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)


def install_openapi(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        remove_default_validation_docs(openapi_schema)
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi

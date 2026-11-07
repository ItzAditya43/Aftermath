"""Every error response uses the same shape: {"error": {"code": ..., "message": ...}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_CODES_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


class NotFoundError(AppError):
    def __init__(self, message: str):
        super().__init__(404, "not_found", message)


class ConflictError(AppError):
    def __init__(self, message: str):
        super().__init__(409, "conflict", message)


def error_json(status_code: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` entries so /docs shows the error shape."""
    return {code: {"model": ErrorResponse, "description": _CODES_BY_STATUS.get(code, "error")} for code in status_codes}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        response = error_json(exc.status_code, exc.code, exc.message)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _CODES_BY_STATUS.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        return error_json(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        details = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
            for err in exc.errors()
        ]
        first = details[0] if details else None
        message = "Request validation failed"
        if first:
            field = ".".join(str(part) for part in first["loc"] if part != "body")
            message = f"{field}: {first['msg']}" if field else first["msg"]
        return error_json(422, "validation_error", message, details)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error", exc_info=exc)
        return error_json(500, "internal_error", "Internal server error")

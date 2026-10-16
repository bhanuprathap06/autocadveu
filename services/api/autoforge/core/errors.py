"""Consistent API error schema: {"error": {code, message, details, request_id}}."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, code: str | None = None, status: int | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.details = details


class NotFound(ApiError):
    status = 404
    code = "not_found"


class Forbidden(ApiError):
    status = 403
    code = "forbidden"


class Unauthorized(ApiError):
    status = 401
    code = "unauthorized"


class Conflict(ApiError):
    status = 409
    code = "conflict"


class RateLimited(ApiError):
    status = 429
    code = "rate_limited"


class Unprocessable(ApiError):
    status = 422
    code = "unprocessable"


class ServiceUnavailable(ApiError):
    status = 503
    code = "service_unavailable"


def error_body(code: str, message: str, request_id: str | None, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id}}

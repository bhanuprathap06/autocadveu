"""Per-request context (correlation id, client ip) via contextvars."""

from __future__ import annotations

import contextvars

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
client_ip_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("client_ip", default=None)


def current_request_id() -> str | None:
    return request_id_var.get()


def current_ip() -> str | None:
    return client_ip_var.get()

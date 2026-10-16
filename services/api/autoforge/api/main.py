"""FastAPI application factory."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..core.config import get_settings
from ..core.context import client_ip_var, request_id_var
from ..core.errors import ApiError, error_body

log = logging.getLogger("autoforge.api")
REQUESTS = Counter("af_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("af_http_request_seconds", "HTTP latency", ["method", "route"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60))
RID_RE = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")
API_PREFIX = "/api/v1"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
               "request_id": request_id_var.get()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def configure_logging() -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    from ..kernel_pool import shutdown_pool

    shutdown_pool()


def create_app() -> FastAPI:
    s = get_settings()
    if s.env != "test":
        configure_logging()
    app = FastAPI(
        title="AutoForge Engineering Studio API",
        version="0.1.0",
        description="Parametric CAD, automotive component catalog, simulation and collaboration API. "
                    "All errors use the schema {error: {code, message, details, request_id}}.",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs" if s.env != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    if s.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.allowed_origins, allow_credentials=True,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["content-type", "x-csrf-token", "x-request-id"], expose_headers=["x-request-id",
                                                                                                         "x-content-sha256"])

    @app.middleware("http")
    async def context_mw(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        rid = rid if RID_RE.match(rid) else uuid.uuid4().hex
        request_id_var.set(rid)
        ip = request.client.host if request.client else None
        if s.trust_proxy_headers and request.headers.get("x-forwarded-for"):
            ip = request.headers["x-forwarded-for"].split(",")[0].strip()
        client_ip_var.set(ip)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            response = JSONResponse(error_body("internal_error", "An unexpected error occurred. Quote the request id when "
                                               "reporting it.", rid), status_code=500)
        route = request.scope.get("route")
        rname = getattr(route, "path", "unmatched")
        REQUESTS.labels(request.method, rname, str(response.status_code)).inc()
        LATENCY.labels(request.method, rname).observe(time.perf_counter() - t0)
        response.headers["x-request-id"] = rid
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("referrer-policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("permissions-policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith(f"{API_PREFIX}/docs"):
            pass
        else:
            response.headers.setdefault("content-security-policy", "default-src 'none'; frame-ancestors 'none'")
        if s.cookie_secure:
            response.headers.setdefault("strict-transport-security", "max-age=63072000; includeSubDomains")
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.message, request_id_var.get(), exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()][:50]
        return JSONResponse(error_body("validation_error", "Request validation failed", request_id_var.get(), details),
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized", 403: "forbidden"}.get(exc.status_code, "http_error")
        return JSONResponse(error_body(code, str(exc.detail), request_id_var.get()), status_code=exc.status_code)

    from . import audit_routes, auth, documents, files, projects, system

    for r in (auth.router, projects.router, documents.router, files.router, audit_routes.router, system.router):
        app.include_router(r, prefix=API_PREFIX)
    _optional_routers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        from .system import readiness

        ok, checks = readiness()
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    return app


def _optional_routers(app: FastAPI) -> None:
    """Routers for later phases register here once their modules exist."""
    import importlib

    for mod in ("catalog_routes", "assemblies", "simulation", "drawings", "rooms", "comments", "requirements", "calculators",
                "admin", "ws", "reports"):
        try:
            m = importlib.import_module(f"autoforge.api.{mod}")
        except ModuleNotFoundError as exc:
            if exc.name == f"autoforge.api.{mod}":
                continue
            raise
        app.include_router(m.router, prefix=API_PREFIX)


app = create_app()

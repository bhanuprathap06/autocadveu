"""Runtime configuration. All secrets come from the environment / secret manager."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AF_", env_file=None, extra="ignore")

    env: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "postgresql+psycopg://autoforge:autoforge@localhost:5432/autoforge"
    redis_url: str | None = None

    # Object storage
    storage_backend: Literal["s3", "local"] = "local"
    storage_local_dir: str = ".data/objects"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "autoforge"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    # "path" puts the bucket in the URL path (self-hosted stores); "virtual" in the host name (Railway buckets, AWS).
    # Unset: path style for a custom endpoint, automatic for AWS.
    s3_addressing_style: Literal["auto", "path", "virtual"] | None = None

    # HTTP / security
    public_base_url: str = "http://localhost:3000"
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    cookie_secure: bool = False
    session_ttl_hours: int = 24 * 7
    max_upload_mb: int = 100
    login_rate_limit_per_15min: int = 10
    trust_proxy_headers: bool = False

    # Kernel / workers
    kernel_workers: int = 2
    kernel_timeout_s: float = 60.0
    kernel_memory_mb: int = 4096
    job_poll_interval_s: float = 1.0
    sim_max_runtime_s: int = 900
    sim_memory_mb: int = 6144
    sim_max_mesh_nodes: int = 400_000
    ccx_binary: str = "ccx"

    # Email (transactional outbox), delivered over SMTP or the Resend HTTPS API (for hosts that block SMTP,
    # e.g. Railway's Hobby plan). Without either, messages stay in the outbox (admin console).
    resend_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "AutoForge <no-reply@autoforge.local>"
    # STARTTLS is required for real mail servers; disable only for a local capture sink (e.g. Mailpit in docker compose).
    smtp_starttls: bool = True

    # AI (optional; disabled unless a provider is configured)
    ai_provider: Literal["none", "anthropic"] = "none"
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-5-5"

    # Data retention
    project_retention_days: int = 30
    audit_retention_days: int = 365

    # Seed data / demo accounts (never enabled in production)
    seed_demo: bool = False

    @model_validator(mode="after")
    def _prod_guard(self) -> "Settings":
        if self.env == "production":
            if not self.cookie_secure:
                raise ValueError("AF_COOKIE_SECURE must be true in production")
            if self.seed_demo:
                raise ValueError("Demo seed accounts are not allowed in production")
            if self.storage_backend != "s3":
                raise ValueError("Production requires S3-compatible object storage")
        return self

    @property
    def mail_configured(self) -> bool:
        return bool(self.resend_api_key or self.smtp_host)

    @property
    def is_dev(self) -> bool:
        return self.env in ("development", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()

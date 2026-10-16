"""Relational schema (PostgreSQL). Migrations live in services/api/migrations.

Design notes
------------
* UUID primary keys (stable identifiers, safe to expose; no enumeration).
* CAD revisions are immutable rows; the document row only points at the head.
  Restores create a *new* revision copying old content (history is append-only).
* The ``jobs`` table is the durable queue. Domain rows (e.g. simulation_jobs)
  and their queue entry are inserted in the same transaction, which gives the
  transactional-outbox guarantee without a separate relay.
* Large binaries live in object storage; rows keep keys + SHA-256 checksums.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=now, server_default=text("now()"), nullable=False)


# --------------------------------------------------------------------------- identity
class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = created()
    __table_args__ = (Index("uq_users_email_lower", text("lower(email)"), unique=True),)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    mfa_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    user: Mapped[User] = relationship()


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # verify_email | reset_password
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    id: Mapped[uuid.UUID] = pk()
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = created()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    personal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created()


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[uuid.UUID] = pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # owner | admin | member
    created_at: Mapped[datetime] = created()
    __table_args__ = (UniqueConstraint("org_id", "user_id"), CheckConstraint("role in ('owner','admin','member')"))


# --------------------------------------------------------------------------- projects
class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    domain: Mapped[str | None] = mapped_column(String(64))
    template: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ProjectMember(Base):
    __tablename__ = "project_members"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = created()
    __table_args__ = (UniqueConstraint("project_id", "user_id"),
                      CheckConstraint("role in ('owner','editor','reviewer','viewer')"))


class EngineeringProblem(Base):
    __tablename__ = "engineering_problems"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)  # draft|extracted|approved
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_provider: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    problem_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("engineering_problems.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str | None] = mapped_column(String(120))
    comparator: Mapped[str | None] = mapped_column(String(4))
    target_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16), default="user", nullable=False)  # user | ai | heuristic
    claim_class: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)  # proposed|approved|rejected
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()
    __table_args__ = (
        CheckConstraint("kind in ('functional','constraint','target_metric','assumption','open_question')"),
    )


# --------------------------------------------------------------------------- CAD
class CADDocument(Base):
    __tablename__ = "cad_documents"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # part | assembly
    head_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("kind in ('part','assembly')"),)


class CADRevision(Base):
    __tablename__ = "cad_revisions"
    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cad_documents.id", ondelete="CASCADE"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_number: Mapped[int | None] = mapped_column(Integer)
    restored_from: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kernel_version: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    op_id: Mapped[str] = mapped_column(String(80), nullable=False)
    recompute: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    __table_args__ = (UniqueConstraint("document_id", "number"), UniqueConstraint("document_id", "op_id"))


class Feature(Base):
    """Denormalised index of features per revision (search, status, reporting)."""

    __tablename__ = "features"
    id: Mapped[uuid.UUID] = pk()
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cad_revisions.id", ondelete="CASCADE"), index=True, nullable=False)
    feature_id: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Assembly(Base):
    """Assembly metadata; the assembly definition is the CAD revision content."""

    __tablename__ = "assemblies"
    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cad_documents.id", ondelete="CASCADE"), unique=True, nullable=False)
    dof: Mapped[int | None] = mapped_column(Integer)
    solve_status: Mapped[str | None] = mapped_column(String(24))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)


class AssemblyInstance(Base):
    __tablename__ = "assembly_instances"
    id: Mapped[uuid.UUID] = pk()
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cad_revisions.id", ondelete="CASCADE"), index=True, nullable=False)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cad_documents.id"), index=True)
    source_revision: Mapped[int | None] = mapped_column(Integer)
    catalog_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("component_catalog_items.id"), index=True)
    transform: Mapped[list[float]] = mapped_column(JSONB, nullable=False)


# --------------------------------------------------------------------------- catalog
class AssetLicense(Base):
    __tablename__ = "asset_licenses"
    id: Mapped[uuid.UUID] = pk()
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    spdx_id: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(500))
    redistribution_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    commercial_use_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[uuid.UUID] = pk()
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    standard: Mapped[str | None] = mapped_column(String(120))
    density_kg_m3: Mapped[float] = mapped_column(Float, nullable=False)
    youngs_modulus_mpa: Mapped[float | None] = mapped_column(Float)
    poisson_ratio: Mapped[float | None] = mapped_column(Float)
    yield_strength_mpa: Mapped[float | None] = mapped_column(Float)
    ultimate_strength_mpa: Mapped[float | None] = mapped_column(Float)
    thermal_conductivity_w_mk: Mapped[float | None] = mapped_column(Float)
    specific_heat_j_kgk: Mapped[float | None] = mapped_column(Float)
    thermal_expansion_1_k: Mapped[float | None] = mapped_column(Float)
    value_basis: Mapped[str] = mapped_column(String(40), nullable=False)  # typical | minimum | nominal
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ComponentCatalogItem(Base):
    __tablename__ = "component_catalog_items"
    id: Mapped[uuid.UUID] = pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(64))
    domain: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(24), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(120))
    part_number: Mapped[str | None] = mapped_column(String(120))
    standard: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    specs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    material: Mapped[str | None] = mapped_column(String(120))
    material_source: Mapped[str | None] = mapped_column(String(300))
    mass_kg: Mapped[float | None] = mapped_column(Float)
    mass_source: Mapped[str | None] = mapped_column(String(300))
    mass_confidence: Mapped[str | None] = mapped_column(String(16))
    image_key: Mapped[str | None] = mapped_column(String(500))
    image_attribution: Mapped[str | None] = mapped_column(String(500))
    cad_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cad_documents.id"))
    cad_content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # parametric generator document for generated geometry
    cad_blob_key: Mapped[str | None] = mapped_column(String(500))
    cad_format: Mapped[str | None] = mapped_column(String(32))
    spec_source: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    license_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("asset_licenses.id"))
    units: Mapped[str] = mapped_column(String(16), default="mm", nullable=False)
    measurement_basis: Mapped[str | None] = mapped_column(String(200))
    compatibility_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    revision: Mapped[str | None] = mapped_column(String(40))
    completeness: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    safety_critical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    search: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(name,'') || ' ' || coalesce(manufacturer,'') || ' ' || "
            "coalesce(part_number,'') || ' ' || coalesce(category,'') || ' ' || coalesce(subcategory,'') || ' ' || "
            "coalesce(standard,'') || ' ' || coalesce(description,''))",
            persisted=True,
        ),
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)
    __table_args__ = (
        CheckConstraint("asset_class in ('verified_public','manufacturer','user_upload','generated')"),
        Index("ix_catalog_category", "category", "subcategory"),
        Index("ix_catalog_manufacturer_pn", "manufacturer", "part_number"),
        Index("ix_catalog_tags", "tags", postgresql_using="gin"),
        Index("ix_catalog_specs", "specs", postgresql_using="gin"),
        Index("ix_catalog_search", "search", postgresql_using="gin"),
    )


class Favorite(Base):
    __tablename__ = "favorites"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("component_catalog_items.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = created()
    __table_args__ = (UniqueConstraint("user_id", "item_id"),)


class ProjectComponent(Base):
    __tablename__ = "project_components"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("component_catalog_items.id", ondelete="CASCADE"), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    added_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    __table_args__ = (UniqueConstraint("project_id", "item_id"),)


# --------------------------------------------------------------------------- files
class StoredFile(Base):
    __tablename__ = "stored_files"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # reference | cad_import | export | artifact
    scan_status: Mapped[str] = mapped_column(String(24), default="not_scanned", nullable=False)
    diagnostics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()


# --------------------------------------------------------------------------- jobs & simulation
class Job(Base):
    """Durable job queue row (claimed with SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint("status in ('queued','running','succeeded','failed','cancelled','timed_out')"),
        Index("ix_jobs_claim", "status", "priority", "run_after"),
    )


class SimulationJob(Base):
    __tablename__ = "simulation_jobs"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cad_documents.id"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis: Mapped[str] = mapped_column(String(24), nullable=False)  # static | thermal
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    setup: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    solver: Mapped[str] = mapped_column(String(40), nullable=False)
    solver_version: Mapped[str | None] = mapped_column(String(80))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()


class SimulationResult(Base):
    __tablename__ = "simulation_results"
    id: Mapped[uuid.UUID] = pk()
    simulation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_jobs.id", ondelete="CASCADE"), unique=True,
                                                     nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mesh: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    convergence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # name -> {key, sha256, size}
    created_at: Mapped[datetime] = created()


# --------------------------------------------------------------------------- drawings
class Drawing(Base):
    __tablename__ = "drawings"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cad_documents.id"), nullable=False)
    revision_pin: Mapped[int | None] = mapped_column(Integer)  # None = follow head revision
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)


# --------------------------------------------------------------------------- collaboration
class CollaborationRoom(Base):
    __tablename__ = "collaboration_rooms"
    id: Mapped[uuid.UUID] = pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoomMember(Base):
    __tablename__ = "room_members"
    id: Mapped[uuid.UUID] = pk()
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collaboration_rooms.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = created()
    __table_args__ = (UniqueConstraint("room_id", "user_id"),
                      CheckConstraint("role in ('owner','editor','reviewer','viewer')"))


class RoomProject(Base):
    __tablename__ = "room_projects"
    id: Mapped[uuid.UUID] = pk()
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collaboration_rooms.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    shared_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    __table_args__ = (UniqueConstraint("room_id", "project_id"),)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[uuid.UUID] = pk()
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collaboration_rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("role in ('editor','reviewer','viewer')"),)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    anchor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_comments_target", "project_id", "target_type", "target_id"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = pk()
    at: Mapped[datetime] = created()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    room_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    __table_args__ = (
        Index("ix_audit_project_at", "project_id", "at"),
        Index("ix_audit_actor_at", "actor_id", "at"),
        Index("ix_audit_room_at", "room_id", "at"),
    )

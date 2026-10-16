"""Schema-validated AI outputs. Every claim carries a classification."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClaimClass = Literal["source_supported_fact", "calculation", "engineering_hypothesis", "design_recommendation",
                     "unverified_suggestion"]
RequirementKind = Literal["functional", "constraint", "target_metric", "assumption", "open_question"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedRequirement(_M):
    kind: RequirementKind
    text: str = Field(max_length=1000)
    metric: str | None = Field(default=None, max_length=120)
    comparator: Literal["<=", ">=", "=", "<", ">"] | None = None
    target_value: float | None = None
    unit: str | None = Field(default=None, max_length=32)
    claim_class: ClaimClass
    rationale: str = Field(default="", max_length=1000)


class Extraction(_M):
    summary: str = Field(max_length=2000)
    domain_guess: str | None = None
    requirements: list[ExtractedRequirement] = Field(max_length=100)
    clarifying_questions: list[str] = Field(default_factory=list, max_length=30)


class Claim(_M):
    text: str = Field(max_length=1000)
    claim_class: ClaimClass
    reference: str | None = Field(default=None, max_length=300)  # catalog item id, requirement id, URL, or None


class Concept(_M):
    title: str = Field(max_length=200)
    description: str = Field(max_length=2000)
    template: str | None = None  # starter template key, if one applies
    catalog_queries: list[str] = Field(default_factory=list, max_length=5)
    claims: list[Claim] = Field(default_factory=list, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)


class ConceptSet(_M):
    concepts: list[Concept] = Field(max_length=6)


class ParameterSetting(_M):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    expression: str = Field(max_length=200)
    rationale: str = Field(default="", max_length=500)


class CadProposal(_M):
    """A proposed modelling action expressed only through the validated command registry.

    The AI never emits geometry scripts: it may choose a registered template, set
    named parameters and select a material from the material library. The server
    builds the document and validates it with the CAD kernel before it is shown.
    """

    template: str
    name: str = Field(max_length=160)
    parameters: list[ParameterSetting] = Field(default_factory=list, max_length=50)
    material_key: str | None = None
    claims: list[Claim] = Field(default_factory=list, max_length=20)

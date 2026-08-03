"""Schémas de la méthode SRA et de sa revue humaine."""
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


ReviewStatus = Literal["proposed", "accepted", "rejected", "modified"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskObjective(StrictModel):
    attacker_category: str
    targeted_objectives: str


class Evidence(StrictModel):
    source: str
    quote: str


class ReviewFields(StrictModel):
    review_status: ReviewStatus = "proposed"
    review_comment: str = ""
    reviewed_at: str = ""
    reviewed_by: str = ""


class RiskOriginType(ReviewFields):
    ro_type_id: str
    canonical_type: str
    definition: str
    observed_instances: List[str] = Field(default_factory=list)
    characterization: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)


class TargetObjectiveRelation(ReviewFields):
    to_id: str
    ro_type_id: str
    ro_canonical_type: str
    target_name: str
    target_category: str
    rationale: str
    evidence: List[Evidence] = Field(default_factory=list)


class AmbiguousCandidate(ReviewFields):
    candidate_id: str = ""
    extracted_category: str
    characterization: str
    source: str
    evidence: str
    reason: str


class SraGenerationConfig(StrictModel):
    profile: Literal["quality", "balanced", "fast", "custom"] = "quality"
    extraction_model: str = "qwen3.5:latest"
    adjudication_model: str = "deepseek-r1:32b"
    finalization_model: str = "mistral-small3.2:latest"
    finalization_enabled: bool = False
    map_batch_chars: int = Field(default=24_000, ge=8_000, le=48_000)
    map_batch_overlap: int = Field(default=1_500, ge=0, le=6_000)
    adjudication_batch_chars: int = Field(default=18_000, ge=6_000, le=36_000)
    adjudication_retries: int = Field(default=1, ge=0, le=3)


class RiskObjectivesTable(StrictModel):
    project_id: str
    generation_mode: str = "deep"
    schema_version: Literal["1.0", "2.0"] = "1.0"
    source_document_ids: List[str] = Field(default_factory=list)
    generation_config: SraGenerationConfig = Field(default_factory=SraGenerationConfig)
    risk_origin_types: List[RiskOriginType] = Field(default_factory=list)
    ambiguous_candidates: List[AmbiguousCandidate] = Field(default_factory=list)
    risk_objectives: List[RiskObjective] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    trace: List[dict] = Field(default_factory=list)
    generated_at: str = ""


class TargetObjectivesTable(StrictModel):
    project_id: str
    generation_mode: str = "deep"
    schema_version: Literal["1.0"] = "1.0"
    source_document_ids: List[str] = Field(default_factory=list)
    generation_config: SraGenerationConfig = Field(default_factory=SraGenerationConfig)
    target_objectives: List[TargetObjectiveRelation] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    trace: List[dict] = Field(default_factory=list)
    generated_at: str = ""

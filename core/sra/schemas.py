"""Schémas de la méthode SRA et de sa revue humaine."""
from typing import Dict, List, Literal

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


class TargetAttackScenario(StrictModel):
    scenario_id: str
    attack: str
    objective: str
    target_names: List[str] = Field(default_factory=list)
    target_categories: List[str] = Field(default_factory=list)
    security_properties: List[Literal[
        "confidentiality", "integrity", "availability", "authenticity",
        "non_repudiation",
    ]] = Field(default_factory=list)
    target_domains: List[Literal[
        "ground_segment", "space_segment", "supervision", "communications",
        "data", "user", "supply_chain", "other",
    ]] = Field(default_factory=list)
    motivations: List[Literal[
        "financial_gain", "espionage", "sabotage", "disruption", "influence",
        "strategic_advantage", "ideology", "revenge", "other",
    ]] = Field(default_factory=list)
    rationale: str
    evidence: List[Evidence] = Field(default_factory=list)
    canonical_target_family: str = ""
    canonical_target_key: str = ""
    primary_target_domain: str = "other"
    secondary_target_domains: List[str] = Field(default_factory=list)
    effect_category: str = "other"
    plausibility_score: float = 0.0
    plausibility_level: Literal["low", "medium", "high", "unassessed"] = "unassessed"
    plausibility_method: str = ""
    plausibility_factors: Dict[str, float] = Field(default_factory=dict)


class TargetObjectiveGroup(ReviewFields):
    to_id: str
    ro_type_id: str
    ro_canonical_type: str
    ro_definition: str = ""
    attacks: List[TargetAttackScenario] = Field(default_factory=list)
    ro_evidence: List[Evidence] = Field(default_factory=list)


class AmbiguousCandidate(ReviewFields):
    candidate_id: str = ""
    extracted_category: str
    characterization: str
    source: str
    evidence: str
    reason: str


class RoTaxonomyType(StrictModel):
    ro_type_id: str
    canonical_type: str
    definition: str


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
    taxonomy_id: str = "sra-default"
    taxonomy_version: str = "1.0"
    taxonomy_mode: Literal["closed", "extensible"] = "closed"
    ro_types: List[RoTaxonomyType] = Field(default_factory=list)
    ro_type_aliases: Dict[str, str] = Field(default_factory=dict)


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


class ClassifiedTargetObjective(ReviewFields):
    classification_id: str
    ro_type_id: str
    ro_canonical_type: str
    ro_definition: str = ""
    target_domain: Literal[
        "ground_segment", "space_segment", "supervision", "communications",
        "data", "user", "supply_chain", "other",
    ]
    primary_target_domain: Literal[
        "ground_segment", "space_segment", "supervision", "communications",
        "data", "user", "supply_chain", "other",
    ] = "other"
    secondary_target_domains: List[str] = Field(default_factory=list)
    effect_category: Literal[
        "information_compromise", "integrity_or_control",
        "service_disruption", "supply_chain_compromise", "other",
    ] = "other"
    generic_attack_name: str
    attack_summary: str
    objective_summary: str
    scenario_ids: List[str] = Field(default_factory=list)
    source_scenario_count: int = 0
    target_names: List[str] = Field(default_factory=list)
    security_properties: List[str] = Field(default_factory=list)
    motivations: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)


class ClassifiedTargetObjectivesTable(StrictModel):
    project_id: str
    schema_version: Literal["1.0", "2.0"] = "2.0"
    consolidation_strategy: str = "ro_type_x_security_effect"
    target_objective_budget: int = 20
    source_to_revision_id: str = ""
    source_ro_revision_id: str = ""
    source_to_generated_at: str = ""
    classified_objectives: List[ClassifiedTargetObjective] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    trace: List[dict] = Field(default_factory=list)
    generated_at: str = ""


class TreatmentItem(ReviewFields):
    treatment_id: str
    classification_id: str
    ro_type_id: str
    target_objective: str
    expected_security_outcome: str
    target_names: List[str] = Field(default_factory=list)
    security_properties: List[str] = Field(default_factory=list)
    measures: List[str] = Field(default_factory=list)
    owner: str = ""
    due_date: str = ""
    implementation_status: Literal[
        "to_define", "planned", "in_progress", "implemented", "accepted_risk",
    ] = "to_define"


class TreatmentPlan(StrictModel):
    project_id: str
    schema_version: Literal["1.0"] = "1.0"
    source_to_revision_id: str = ""
    source_decision_generated_at: str = ""
    items: List[TreatmentItem] = Field(default_factory=list)
    generated_at: str = ""


class TargetObjectivesTable(StrictModel):
    project_id: str
    generation_mode: str = "deep"
    schema_version: Literal["1.0", "2.0"] = "1.0"
    source_document_ids: List[str] = Field(default_factory=list)
    source_ro_revision_id: str = ""
    generation_config: SraGenerationConfig = Field(default_factory=SraGenerationConfig)
    target_objectives: List[TargetObjectiveRelation | TargetObjectiveGroup] = Field(default_factory=list)
    adjudication_issues: List[dict] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    trace: List[dict] = Field(default_factory=list)
    generated_at: str = ""
    completion_status: Literal["complete", "partial", "incomplete"] = "complete"
    resume_available: bool = False

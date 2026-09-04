import { API_BASE, getJSON } from "@/lib/api";
import type { TaskMetrics } from "@/lib/types";

export type RiskObjective = {
  attacker_category: string;
  targeted_objectives: string;
};
export type ReviewStatus = "proposed" | "accepted" | "rejected" | "modified";
export type ReviewFields = { review_status?: ReviewStatus; review_comment?: string; reviewed_at?: string | null; reviewed_by?: string | null };
export type RiskOriginType = ReviewFields & {
  ro_type_id: string; canonical_type: string; definition: string;
  observed_instances: string[]; characterization: string[];
  evidence: Array<{ source: string; quote: string }>;
};
export type TargetAttackScenario = {
  scenario_id: string; attack: string; objective: string;
  target_names: string[]; target_categories: string[]; rationale: string;
  security_properties: Array<"confidentiality" | "integrity" | "availability" | "authenticity" | "non_repudiation">;
  target_domains: Array<"ground_segment" | "space_segment" | "supervision" | "communications" | "data" | "user" | "supply_chain" | "other">;
  motivations: Array<"financial_gain" | "espionage" | "sabotage" | "disruption" | "influence" | "strategic_advantage" | "ideology" | "revenge" | "other">;
  evidence: Array<{ source: string; quote: string }>;
  canonical_target_family?: string; canonical_target_key?: string;
  primary_target_domain?: TargetAttackScenario["target_domains"][number];
  secondary_target_domains?: TargetAttackScenario["target_domains"];
  effect_category?: string; plausibility_score?: number;
  plausibility_level?: "low" | "medium" | "high" | "unassessed";
  plausibility_method?: string; plausibility_factors?: Record<string, number>;
};
export type TargetObjectiveRelation = ReviewFields & {
  to_id: string; ro_type_id: string; ro_canonical_type: string;
  ro_definition?: string; attacks?: TargetAttackScenario[];
  ro_evidence?: Array<{ source: string; quote: string }>;
  target_name?: string; target_category?: string; rationale?: string;
  evidence?: Array<{ source: string; quote: string }>;
};
export type TargetObjectivesTable = {
  project_id: string; generation_mode: "deep"; schema_version: "1.0" | "2.0";
  source_document_ids: string[]; source_ro_revision_id?: string; generation_config?: SraGenerationConfig;
  target_objectives: TargetObjectiveRelation[]; warnings: string[];
  coverage?: { documents_total: number; documents_with_evidence: number; ro_types_total: number; ro_types_with_candidates: number; ro_types_retained: number; missing_candidate_ro_type_ids: string[]; missing_retained_ro_type_ids: string[] };
  trace: Array<Record<string, unknown>>; generated_at: string;
  adjudication_issues?: Array<Record<string, unknown>>;
  completion_status?: "complete" | "partial" | "incomplete"; resume_available?: boolean;
};
export type ClassifiedTargetObjective = ReviewFields & {
  classification_id: string; ro_type_id: string; ro_canonical_type: string; ro_definition: string;
  target_domain: TargetAttackScenario["target_domains"][number];
  primary_target_domain: TargetAttackScenario["target_domains"][number];
  secondary_target_domains: TargetAttackScenario["target_domains"];
  effect_category: "information_compromise" | "integrity_or_control" | "service_disruption" | "supply_chain_compromise" | "other";
  generic_attack_name: string; attack_summary: string; objective_summary: string;
  scenario_ids: string[]; source_scenario_count: number; target_names: string[];
  security_properties: string[]; motivations: string[];
  evidence: Array<{ source: string; quote: string }>;
};
export type ClassifiedTargetObjectivesTable = {
  project_id: string; schema_version: "1.0" | "2.0"; source_to_revision_id: string;
  consolidation_strategy?: string; target_objective_budget?: number;
  source_ro_revision_id: string; source_to_generated_at: string;
  classified_objectives: ClassifiedTargetObjective[]; warnings: string[]; trace: Array<Record<string, unknown>>; generated_at: string;
};
export type TreatmentItem = ReviewFields & {
  treatment_id: string; classification_id: string; ro_type_id: string;
  target_objective: string; expected_security_outcome: string;
  target_names: string[]; security_properties: string[]; measures: string[];
  owner: string; due_date: string;
  implementation_status: "to_define" | "planned" | "in_progress" | "implemented" | "accepted_risk";
};
export type TreatmentPlan = {
  project_id: string; schema_version: "1.0"; source_to_revision_id: string;
  source_decision_generated_at: string; items: TreatmentItem[]; generated_at: string;
};

export type AmbiguousCandidate = ReviewFields & {
  candidate_id?: string;
  extracted_category: string; characterization: string; source: string;
  evidence: string; reason: string;
};
export type RiskObjectivesTable = {
  project_id: string;
  generation_mode: "deep";
  schema_version?: "1.0" | "2.0";
  source_document_ids: string[];
  generation_config?: SraGenerationConfig;
  risk_origin_types?: RiskOriginType[];
  ambiguous_candidates?: AmbiguousCandidate[];
  risk_objectives: RiskObjective[];
  warnings: string[];
  trace: Array<Record<string, unknown>>;
  generated_at: string;
};
export type TargetObjectiveEstimate = { estimate: { windows: number; ro_types: number; estimated_map_calls: number; estimated_tokens: number; estimated_wall_s: number; confirmation_required: boolean; resume_available: boolean }; budget: { max_llm_calls: number; max_wall_s: number; max_total_tokens: number } };
export type SraGenerationConfig = {
  profile: "quality" | "balanced" | "fast" | "custom";
  extraction_model: string; adjudication_model: string;
  finalization_model: string; finalization_enabled: boolean;
  map_batch_chars: number; map_batch_overlap: number;
  adjudication_batch_chars: number; adjudication_retries: number;
  taxonomy_id?: string; taxonomy_version?: string;
  taxonomy_mode?: "closed" | "extensible";
  ro_types?: Array<{ ro_type_id: string; canonical_type: string; definition: string }>;
  ro_type_aliases?: Record<string, string>;
};
export type SraProject = {
  project_id: string;
  name: string;
  document_ids: string[];
  generation_config?: SraGenerationConfig;
  status: string;
  created_at: string;
  table: RiskObjectivesTable | null;
  target_objectives_table?: TargetObjectivesTable | null;
  classified_target_objectives_table?: ClassifiedTargetObjectivesTable | null;
  downstream_status?: "stale_after_to_revision" | "decision_current" | "treatment_current";
  treatment_plan?: TreatmentPlan | null;
  archived?: boolean; active_ro_revision_id?: string; active_to_revision_id?: string;
  generation_jobs?: Record<string, Record<string, unknown>>;
};
export type SraArtifact = { revision_id: string; project_id: string; project_name: string; kind: "ro" | "to"; sequence: number; created_at: string; label: string };
export type SraRevision = SraArtifact & { reason: string; parent_revision_id?: string };
export type RevisionComparison = { kind: "ro" | "to"; added: unknown[]; removed: unknown[]; changed: Array<{ before: unknown; after: unknown }> };

export type GenerateEvent =
  | { type: "task"; task_id: string; status: "running" }
  | { type: "task_metrics"; metrics: TaskMetrics }
  | ({ type: "stage"; stage: string; document?: string } & Record<string, unknown>)
  | { type: "table"; table: RiskObjectivesTable }
  | { type: "done" }
  | { type: "stopped"; message: string }
  | { type: "error"; message: string };

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `HTTP ${response.status}`);
  }
  return response.json();
}

export const listProjects = () =>
  getJSON<{ projects: SraProject[] }>("/api/sra/projects");
export const listArtifacts = () => getJSON<{ artifacts: SraArtifact[] }>("/api/sra/artifacts");
export const listRevisions = (id: string, kind?: "ro" | "to") => getJSON<{ revisions: SraRevision[] }>("/api/sra/projects/" + id + "/revisions" + (kind ? "?kind=" + kind : ""));
export const compareRevisions = (left: string, right: string) => getJSON<RevisionComparison>("/api/sra/revisions/compare/" + left + "/" + right);
export const patchProject = (id: string, patch: { name?: string; archived?: boolean }) => send<SraProject>("/api/sra/projects/" + id, "PATCH", patch);
export const duplicateProject = (id: string, name?: string) => send<SraProject>("/api/sra/projects/" + id + "/duplicate", "POST", { name });
export const getProject = (id: string) =>
  getJSON<SraProject>(`/api/sra/projects/${id}`);
export const createProject = (name: string, document_ids: string[]) =>
  send<SraProject>("/api/sra/projects", "POST", { name, document_ids });
export const updateDocuments = (id: string, document_ids: string[]) =>
  send<SraProject>(`/api/sra/projects/${id}/documents`, "PUT", { document_ids });
export const updateGenerationConfig = (id: string, config: SraGenerationConfig) =>
  send<SraProject>(`/api/sra/projects/${id}/generation-config`, "PUT", { config });
export const saveTable = (id: string, table: RiskObjectivesTable) =>
  send<SraProject>(`/api/sra/projects/${id}/risk-objectives`, "PUT", { table });

export async function generateRiskObjectives(
  id: string,
  onEvent: (event: GenerateEvent) => void,
): Promise<void> {
  const response = await fetch(
    `${API_BASE}/api/sra/projects/${id}/risk-objectives/generate`,
    { method: "POST" },
  );
  if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      for (const line of part.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        try { onEvent(JSON.parse(line.slice(6)) as GenerateEvent); } catch { /* ignore */ }
      }
    }
  }
}

export async function exportTable(id: string, format: "csv" | "xlsx"): Promise<void> {
  const response = await fetch(
    `${API_BASE}/api/sra/projects/${id}/risk-objectives/exports`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format }),
    },
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1]
    ?? `risk_objectives.${format}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export const stopGeneration = (id: string, kind: "ro" | "to") =>
  send<{ stopped: boolean; kind: string }>("/api/sra/projects/" + id + "/generations/" + kind + "/stop", "POST");

export const saveTargetObjectivesTable = (id: string, table: TargetObjectivesTable) =>
  send<SraProject>(`/api/sra/projects/${id}/target-objectives`, "PUT", { table });
export const saveDecisionTargetObjectives = (id: string, table: ClassifiedTargetObjectivesTable) =>
  send<SraProject>(`/api/sra/projects/${id}/target-objectives/decision`, "PUT", { table });
export const initializeTreatmentPlan = (id: string) =>
  send<SraProject>(`/api/sra/projects/${id}/treatment-plan/initialize`, "POST");
export const saveTreatmentPlan = (id: string, table: TreatmentPlan) =>
  send<SraProject>(`/api/sra/projects/${id}/treatment-plan`, "PUT", { table });

export const estimateTargetObjectives = (id: string, documentIds: string[]) =>
  send<TargetObjectiveEstimate>("/api/sra/projects/" + id + "/target-objectives/estimate", "POST", { document_ids: documentIds });

export async function generateTargetObjectives(
  id: string,
  documentIds: string[],
  onEvent: (event: GenerateEvent | { type: "target_objectives_table"; table: TargetObjectivesTable }) => void,
  confirmed = false,
): Promise<void> {
  const response = await fetch(API_BASE + "/api/sra/projects/" + id + "/target-objectives/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_ids: documentIds, confirmed, resume: true }),
  });
  if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
  for (;;) {
    const { done, value } = await reader.read(); if (done) break;
    buffer += decoder.decode(value, { stream: true }); const parts = buffer.split("\n\n"); buffer = parts.pop() ?? "";
    for (const part of parts) for (const line of part.split("\n")) if (line.startsWith("data: ")) {
      try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignore malformed SSE */ }
    }
  }
}

export async function classifyTargetObjectives(
  id: string,
  onEvent: (event: { type: string; table?: ClassifiedTargetObjectivesTable; message?: string } & Record<string, unknown>) => void,
): Promise<void> {
  const response = await fetch(API_BASE + "/api/sra/projects/" + id + "/target-objectives/classify", { method: "POST" });
  if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
  for (;;) {
    const { done, value } = await reader.read(); if (done) break;
    buffer += decoder.decode(value, { stream: true }); const parts = buffer.split("\n\n"); buffer = parts.pop() ?? "";
    for (const part of parts) for (const line of part.split("\n")) if (line.startsWith("data: ")) {
      try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignore malformed SSE */ }
    }
  }
}

export async function exportClassifiedTargetObjectives(id: string, format: "csv" | "xlsx"): Promise<void> {
  const response = await fetch(API_BASE + "/api/sra/projects/" + id + "/target-objectives/classified/exports", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ format }),
  });
  if (!response.ok) { const detail = await response.json().catch(() => null); throw new Error(detail?.detail ?? `HTTP ${response.status}`); }
  const blob = await response.blob(); const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? `TO_classees_par_domaine.`;
  const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = name;
  link.click(); URL.revokeObjectURL(url);
}

export async function exportTargetObjectives(id: string, format: "csv" | "xlsx"): Promise<void> {
  const response = await fetch(`${API_BASE}/api/sra/projects/${id}/target-objectives/exports`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ format }),
  });
  if (!response.ok) { const detail = await response.json().catch(() => null); throw new Error(detail?.detail ?? `HTTP ${response.status}`); }
  const blob = await response.blob(); const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? `target_objectives.${format}`;
  const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = name;
  link.click(); URL.revokeObjectURL(url);
}

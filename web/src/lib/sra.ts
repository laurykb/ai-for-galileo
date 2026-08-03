import { API_BASE, getJSON } from "@/lib/api";

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
export type TargetObjectiveRelation = ReviewFields & {
  to_id: string; ro_type_id: string; ro_canonical_type: string;
  target_name: string; target_category: string; rationale: string;
  evidence: Array<{ source: string; quote: string }>;
};
export type TargetObjectivesTable = {
  project_id: string; generation_mode: "deep"; schema_version: "1.0";
  source_document_ids: string[]; generation_config?: SraGenerationConfig;
  target_objectives: TargetObjectiveRelation[]; warnings: string[];
  trace: Array<Record<string, unknown>>; generated_at: string;
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
export type SraGenerationConfig = {
  profile: "quality" | "balanced" | "fast" | "custom";
  extraction_model: string; adjudication_model: string;
  finalization_model: string; finalization_enabled: boolean;
  map_batch_chars: number; map_batch_overlap: number;
  adjudication_batch_chars: number; adjudication_retries: number;
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
  archived?: boolean; active_ro_revision_id?: string; active_to_revision_id?: string;
  generation_jobs?: Record<string, Record<string, unknown>>;
};
export type SraArtifact = { revision_id: string; project_id: string; project_name: string; kind: "ro" | "to"; sequence: number; created_at: string; label: string };
export type SraRevision = SraArtifact & { reason: string; parent_revision_id?: string };
export type RevisionComparison = { kind: "ro" | "to"; added: unknown[]; removed: unknown[]; changed: Array<{ before: unknown; after: unknown }> };

export type GenerateEvent =
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

export async function generateTargetObjectives(
  id: string,
  onEvent: (event: GenerateEvent | { type: "target_objectives_table"; table: TargetObjectivesTable }) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/sra/projects/${id}/target-objectives/generate`, { method: "POST" });
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

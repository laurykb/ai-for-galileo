// web/src/lib/ebios.ts — types et accès API du module EBIOS RM
import { API_BASE, getJSON } from "@/lib/api";

export type Preuve = { document_id: string; source: string; page: number | null; extrait: string };
export type Responsable = { nom: string; type: "INTERNE" | "EXTERNE" };
export type ValeurMetier = {
  id: string; denomination: string; nature: "processus" | "information";
  description: string; responsable: Responsable; preuves: Preuve[];
};
export type BienSupport = {
  id: string; denomination: string; description: string; responsable: Responsable;
  valeurs_metier_associees: string[]; preuves: Preuve[];
};
export type EvenementRedoute = {
  id: string; valeur_metier_id: string; evenement: string; impact: string;
  gravite: number; justification_gravite: string; preuves: Preuve[];
};
export type Ecart = { description: string; justification: string; preuves: Preuve[] };
export type SocleSecurite = {
  id: string; type_referentiel: string; nom_referentiel: string;
  etat_application: string; ecarts: Ecart[];
};
export type Atelier1 = {
  mission: string; valeurs_metier: ValeurMetier[]; biens_supports: BienSupport[];
  evenements_redoutes: EvenementRedoute[]; socle_securite: SocleSecurite[];
};
export type Study = {
  schema_version: string; project_id: string; source_document_ids: string[];
  atelier_1: Atelier1; warnings: string[]; generated_at: string; validated_at: string | null;
};
export type EbiosProject = {
  project_id: string; name: string; document_ids: string[]; status: string;
  created_at: string; exports: { format: string; at: string }[];
};
export type VersionDoc = {
  version: number; origin: string; author: string; created_at: string; study: Study;
};
export type GenerateEvent =
  | { type: "stage"; stage: string; document?: string; domaine?: string }
  | { type: "draft"; version: number; study: Study; warnings: string[] }
  | { type: "done" } | { type: "error"; message: string };

export const GRAVITE_LABELS: Record<number, string> = {
  1: "Mineure", 2: "Significative", 3: "Grave", 4: "Critique",
};
export const ETATS_APPLICATION = ["Appliqué", "Partiellement appliqué", "Non appliqué"];

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export const listProjects = () =>
  getJSON<{ projects: EbiosProject[] }>("/api/ebios/projects");
export const createProject = (name: string, document_ids: string[]) =>
  send<EbiosProject>("/api/ebios/projects", "POST", { name, document_ids });
export const getProject = (id: string) =>
  getJSON<{ project: EbiosProject; latest: VersionDoc | null; warnings: string[] }>(
    `/api/ebios/projects/${id}`);
export const updateDocuments = (id: string, document_ids: string[]) =>
  send<EbiosProject>(`/api/ebios/projects/${id}/documents`, "PUT", { document_ids });
export const putAtelier = (id: string, study: Study, expected_version: number) =>
  send<VersionDoc>(`/api/ebios/projects/${id}/atelier-1`, "PUT",
    { study, expected_version, author: "ingénieur" });
export const validateAtelier = (id: string, expected_version: number, validated_by: string) =>
  send<VersionDoc>(`/api/ebios/projects/${id}/atelier-1/validate`, "POST",
    { expected_version, validated_by });

export async function exportAtelier(id: string, format: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/ebios/projects/${id}/atelier-1/exports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? `ebios_atelier1.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// Même patron que web/src/lib/sse.ts (POST + ReadableStream, EventSource ne fait pas de POST)
export async function streamGenerate(
  id: string, onEvent: (ev: GenerateEvent) => void, signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/ebios/projects/${id}/atelier-1/generate`, {
    method: "POST", signal,
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
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

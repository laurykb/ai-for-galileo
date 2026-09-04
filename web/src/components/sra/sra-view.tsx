"use client";

import { useCallback, useEffect, useState } from "react";

import { Banner, Panel, PanelTitle, Pill, Spinner } from "@/components/ui";
import { AgentsView } from "@/components/agents";
import { useExpert } from "@/components/expert-toggle";
import { TargetObjectivesPanel } from "@/components/sra/target-objectives-panel";
import { getJSON, SourcesResponse } from "@/lib/api";
import type { TaskMetrics } from "@/lib/types";
import {
  RiskObjectivesTable, SraGenerationConfig, SraProject, createProject, exportTable,
  generateRiskObjectives, getProject, listProjects, listRevisions, duplicateProject, patchProject, saveTable, stopGeneration, updateDocuments, updateGenerationConfig,
} from "@/lib/sra";

const QUALITY_CONFIG: SraGenerationConfig = { profile: "quality", extraction_model: "qwen3.5:latest", adjudication_model: "deepseek-r1:32b", finalization_model: "mistral-small3.2:latest", finalization_enabled: false, map_batch_chars: 24000, map_batch_overlap: 1500, adjudication_batch_chars: 18000, adjudication_retries: 1 };
const PROFILE_CONFIGS: Record<"quality" | "balanced" | "fast", SraGenerationConfig> = {
  quality: QUALITY_CONFIG,
  balanced: { ...QUALITY_CONFIG, profile: "balanced", adjudication_model: "qwen3.5:latest", adjudication_batch_chars: 20000 },
  fast: { ...QUALITY_CONFIG, profile: "fast", adjudication_model: "qwen3.5:latest", finalization_model: "qwen3.5:latest", map_batch_chars: 30000, map_batch_overlap: 1000, adjudication_batch_chars: 26000, adjudication_retries: 0 },
};

type SraTab = "ro" | "to" | "sources" | "configuration" | "prompts" | "history";

function traceLabel(event: Record<string, unknown>): string {
  const stage = String(event.stage ?? "analyse");
  if (stage === "sra_ro_budget") {
    const estimate = (event.estimate ?? {}) as Record<string, unknown>;
    const budget = (event.budget ?? {}) as Record<string, unknown>;
    return "Charge estimée RO — " + String(estimate.documents ?? 0) + " document(s), "
      + String(estimate.windows ?? 0) + " fenêtre(s), environ "
      + String(estimate.estimated_map_calls ?? 0) + " appel(s) MAP · garde-fou "
      + String(budget.max_total_tokens ?? 0) + " tokens";
  }
  if (stage === "sra_document_start") return "Démarrage de " + String(event.document ?? "document") + " — " + String(event.batch_count ?? 0) + " fenêtre(s), " + String(event.characters ?? 0) + " caractères";
  if (stage === "sra_batch") return String(event.document ?? "document") + " — fenêtre " + String(event.batch ?? "") + "/" + String(event.batch_count ?? "") + " (" + String(event.characters ?? 0) + " caractères)";
  if (stage === "sra_batch_split") return "Sortie RO trop longue — fenêtre divisée automatiquement";
  if (stage === "sra_adjudication_split") return "Adjudication RO trop longue — lot divisé automatiquement";
  if (stage === "sra_batch_done") return String(event.document ?? "document") + " — fenêtre " + String(event.batch ?? "") + " terminée, " + String(event.new_candidates ?? 0) + " nouveau(x) RO candidat(s)";
  if (stage === "sra_document_done") return String(event.document ?? "document") + " terminé — " + String(event.candidates ?? 0) + " RO candidat(s)";
  if (stage === "prefilter") return "Périmètre documentaire fixé";
  if (stage === "map") return "MAP consolidé : " + String(event.document ?? "document") + " — " + String(event.n_items ?? 0) + " élément(s)";
  if (stage === "map_complete") return "Balayage exhaustif terminé — " + String(event.items ?? 0) + " candidat(s) dans " + String(event.documents ?? 0) + " document(s)";
  if (stage === "sra_consolidation") return "Consolidation RO Types — " + String(event.ro_types_retained ?? event.rows_retained ?? 0) + " type(s), " + String(event.occurrences_merged_into_types ?? event.normalized_duplicates_merged ?? 0) + " occurrence(s) fusionnée(s)";
  return stage;
}

export function SraView() {
  const expert = useExpert();
  const [projects, setProjects] = useState<SraProject[]>([]);
  const [activeTab, setActiveTab] = useState<SraTab>("ro");
  const [sources, setSources] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [generationConfig, setGenerationConfig] = useState<SraGenerationConfig>(QUALITY_CONFIG);
  const [current, setCurrent] = useState<SraProject | null>(null);
  const [table, setTable] = useState<RiskObjectivesTable | null>(null);
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [newName, setNewName] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [trace, setTrace] = useState<string[]>([]);
  const [taskMetrics, setTaskMetrics] = useState<TaskMetrics | null>(null);
  const [revisions, setRevisions] = useState<Array<{ revision_id: string; kind: "ro" | "to"; sequence: number; reason: string; created_at: string }>>([]);

  const refresh = useCallback(async (projectId?: string) => {
    const [projectList, sourceList, modelList] = await Promise.all([
      listProjects(),
      getJSON<SourcesResponse>("/api/sources").catch(
        () => ({ available: false, sources: [] }),
      ),
      getJSON<{ models: string[] }>("/api/models").catch(() => ({ models: [] })),
    ]);
    setProjects(projectList.projects);
    setSources(sourceList.sources.map((source) => source.name));
    setModels(modelList.models.filter((model) => model !== "bge-m3:latest"));
    if (projectId) {
      const project = await getProject(projectId);
      setCurrent(project);
      setTable(project.table);
      setTrace((project.table?.trace ?? []).map(traceLabel));
      setSelectedDocs(project.document_ids);
      setGenerationConfig(project.generation_config ?? QUALITY_CONFIG);
      setDirty(false);
      listRevisions(projectId).then((result) => setRevisions(result.revisions)).catch(() => setRevisions([]));
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      void refresh().catch((reason) => setError(String(reason)));
    }, 0);
    return () => clearTimeout(timer);
  }, [refresh]);

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(null); setStage(""); }
  };

  const create = () => run("create", async () => {
    const project = await createProject(newName, selectedDocs);
    setNewName("");
    await refresh(project.project_id);
  });

  const generate = () => run("generate", async () => {
    if (!current) return;
    setTrace([]);
    setTaskMetrics(null);
    await updateDocuments(current.project_id, selectedDocs);
    await updateGenerationConfig(current.project_id, generationConfig);
    await generateRiskObjectives(current.project_id, (event) => {
      if (event.type === "stage") {
        setStage(``);
        setTrace((currentTrace) => [...currentTrace, traceLabel(event)]);
      }
      if (event.type === "table") setTable(event.table);
      if (event.type === "task_metrics") setTaskMetrics(event.metrics);
      if (event.type === "error") setError(event.message);
    });
    await refresh(current.project_id);
  });

  const stopRo = async () => {
    if (!current) return;
    setStage("arrêt demandé…");
    try { await stopGeneration(current.project_id, "ro"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  const patchRow = (index: number, field: "attacker_category" | "targeted_objectives",
                    value: string) => {
    if (!table) return;
    const rows = table.risk_objectives.map((row, rowIndex) =>
      rowIndex === index ? { ...row, [field]: value } : row);
    setTable({ ...table, risk_objectives: rows });
    setDirty(true);
  };

  const patchRoType = (index: number, patch: Record<string, unknown>) => {
    if (!table?.risk_origin_types) return;
    setTable({ ...table, risk_origin_types: table.risk_origin_types.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row) });
    setDirty(true);
  };

  const removeRow = (index: number) => {
    if (!table) return;
    setTable({
      ...table,
      risk_objectives: table.risk_objectives.filter((_, rowIndex) => rowIndex !== index),
    });
    setDirty(true);
  };

  const addRow = () => {
    if (!table) return;
    setTable({
      ...table,
      risk_objectives: [
        ...table.risk_objectives,
        { attacker_category: "", targeted_objectives: "" },
      ],
    });
    setDirty(true);
  };

  const save = () => run("save", async () => {
    if (!current || !table) return;
    await saveTable(current.project_id, table);
    await refresh(current.project_id);
  });

  const allSelected = sources.length > 0 && selectedDocs.length === sources.length;

  return (
    <div className="space-y-6">
      <Panel>
        <PanelTitle kicker="Analyse SRA" title="Dossiers d&apos;analyse"
          hint="Un dossier conserve les documents, les réglages et les tables RO/TO d&apos;une analyse." />
        <div className="mt-2 flex flex-wrap gap-2">
          {projects.map((project) => (
            <button key={project.project_id}
              className={`rounded border px-3 py-1 text-sm ${
                current?.project_id === project.project_id
                  ? "border-accent-bright" : "border-edge"}`}
              onClick={() => void refresh(project.project_id)}>
              {project.name} <Pill tone="neutral">{project.status}</Pill>
            </button>
          ))}
        </div>
        {current && <div className="mt-3 flex flex-wrap gap-2"><button className="rounded border border-edge px-3 py-1 text-sm" onClick={() => void run("duplicate", async () => { const clone = await duplicateProject(current.project_id); await refresh(clone.project_id); })}>Dupliquer ce dossier</button><button className="rounded border border-edge px-3 py-1 text-sm" onClick={() => void run("archive", async () => { await patchProject(current.project_id, { archived: true }); setCurrent(null); setTable(null); await refresh(); })}>Archiver</button></div>}
        <div className="mt-3 flex gap-2">
          <input className="flex-1 rounded border border-edge bg-transparent px-2 py-1 text-sm"
            placeholder="Nom du nouveau dossier" value={newName}
            onChange={(event) => setNewName(event.target.value)} />
          <button className="rounded border border-edge px-3 py-1 text-sm"
            disabled={!newName.trim() || busy !== null} onClick={create}>Créer</button>
        </div>
      </Panel>

      {current && (
        <>
          <Panel><PanelTitle kicker="Dossier actif" title={current.name} hint={current.document_ids.length + " document(s) dans le corpus · " + (table?.risk_origin_types?.length ?? 0) + " RO Type(s) · " + (current.target_objectives_table?.target_objectives.length ?? 0) + " relation(s) TO"} />
            <nav className="mt-3 flex flex-wrap gap-2" aria-label="Sections du dossier SRA">{([
              ["ro", "Risk Origins (RO)"], ["to", "Target Objectives (TO)"], ["sources", "Sources"], ["configuration", "Configuration"],
              ...(expert ? [["prompts", "Prompts métier"]] : []), ["history", "Historique"],
            ] as Array<[SraTab, string]>).map(([id, label]) => <button key={id} onClick={() => setActiveTab(id)} className={"rounded border px-3 py-2 text-sm " + (activeTab === id ? "border-accent-bright bg-surface-2 text-foreground" : "border-edge text-fg-muted")}>{label}</button>)}</nav>
          </Panel>

          {activeTab === "sources" && <Panel><PanelTitle kicker="Corpus du dossier" title="Documents sources" hint="Ce corpus sert à la génération des RO Types. Un sous-ensemble distinct pourra être choisi pour les Target Objectives." right={<button className="rounded border border-edge px-2 py-1 text-xs" disabled={busy !== null || sources.length === 0} onClick={() => setSelectedDocs(allSelected ? [] : [...sources])}>{allSelected ? "Tout désélectionner" : "Tout sélectionner"}</button>} /><div className="mt-3 grid gap-1 md:grid-cols-2">{sources.map((source) => <label key={source} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selectedDocs.includes(source)} disabled={busy !== null} onChange={(event) => setSelectedDocs(event.target.checked ? [...selectedDocs, source] : selectedDocs.filter((item) => item !== source))} />{source}</label>)}</div><button className="mt-3 rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null || selectedDocs.length === 0} onClick={() => void run("sources", async () => { await updateDocuments(current.project_id, selectedDocs); await refresh(current.project_id); })}>Enregistrer le corpus</button></Panel>}

          {activeTab === "history" && revisions.length > 0 && <Panel><PanelTitle kicker="Traçabilité" title="Historique des versions" hint="Chaque génération ou revue sauvegardée reste consultable et comparable via son identifiant." /><ol className="mt-3 space-y-2 text-sm">{revisions.map((revision) => <li key={revision.revision_id} className="flex justify-between border-b border-edge pb-2"><span>{revision.kind.toUpperCase()} v{revision.sequence} · {revision.reason}</span><span className="text-fg-muted">{new Date(revision.created_at).toLocaleString("fr-FR")}</span></li>)}</ol></Panel>}
          {activeTab === "configuration" && <Panel>
            <PanelTitle kicker="Configuration du dossier SRA" title="Modèles et stratégie de génération" hint="Réglages persistés avec le dossier et réutilisables par les futures tables SRA." />
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <label className="text-sm">Profil<select className="mt-1 w-full rounded border border-edge bg-surface p-2" value={generationConfig.profile} onChange={(event) => { const profile = event.target.value as SraGenerationConfig["profile"]; setGenerationConfig(profile === "custom" ? { ...generationConfig, profile } : { ...generationConfig, ...PROFILE_CONFIGS[profile] }); }}><option value="quality">Qualité maximale — recommandé</option><option value="balanced">Équilibré</option><option value="fast">Rapide</option><option value="custom">Personnalisé</option></select></label>
              {[["extraction_model", "Extraction exhaustive"], ["adjudication_model", "Adjudication métier"], ["finalization_model", "Finalisation rédactionnelle"]].map(([field, label]) => <label key={field} className="text-sm">{label}<select className="mt-1 w-full rounded border border-edge bg-surface p-2" value={String(generationConfig[field as keyof SraGenerationConfig])} onChange={(event) => setGenerationConfig({ ...generationConfig, profile: "custom", [field]: event.target.value })}>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>)}
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={generationConfig.finalization_enabled} onChange={(event) => setGenerationConfig({ ...generationConfig, profile: "custom", finalization_enabled: event.target.checked })} />Activer la finalisation rédactionnelle optionnelle</label>
              <label className="text-sm">Relances d adjudication<input className="mt-1 w-full rounded border border-edge bg-transparent p-2" type="number" min={0} max={3} value={generationConfig.adjudication_retries} onChange={(event) => setGenerationConfig({ ...generationConfig, profile: "custom", adjudication_retries: Number(event.target.value) })} /></label>
              <label className="text-sm">Mode de taxonomie<select className="mt-1 w-full rounded border border-edge bg-surface p-2" value={generationConfig.taxonomy_mode ?? "closed"} onChange={(event) => setGenerationConfig({ ...generationConfig, profile: "custom", taxonomy_mode: event.target.value as "closed" | "extensible" })}><option value="closed">Fermée — catégories inconnues à part</option><option value="extensible">Extensible — nouveaux types proposés</option></select></label>
              <label className="text-sm">Identifiant de taxonomie<input className="mt-1 w-full rounded border border-edge bg-transparent p-2" value={generationConfig.taxonomy_id ?? "sra-default"} onChange={(event) => setGenerationConfig({ ...generationConfig, profile: "custom", taxonomy_id: event.target.value })} /></label>
              <label className="text-sm">Version de taxonomie<input className="mt-1 w-full rounded border border-edge bg-transparent p-2" value={generationConfig.taxonomy_version ?? "1.0"} onChange={(event) => setGenerationConfig({ ...generationConfig, profile: "custom", taxonomy_version: event.target.value })} /></label>
            </div>
            <details className="mt-3 text-sm text-fg-muted"><summary>Paramètres avancés</summary><div className="mt-2 grid gap-3 md:grid-cols-3">
              {[["map_batch_chars", "Taille fenêtre MAP", 8000, 48000], ["map_batch_overlap", "Chevauchement", 0, 6000], ["adjudication_batch_chars", "Taille lot adjudication", 6000, 36000]].map(([field, label, min, max]) => <label key={String(field)}>{label}<input className="mt-1 w-full rounded border border-edge bg-transparent p-2" type="number" min={Number(min)} max={Number(max)} step={500} value={Number(generationConfig[field as keyof SraGenerationConfig])} onChange={(event) => setGenerationConfig({ ...generationConfig, profile: "custom", [field as string]: Number(event.target.value) })} /></label>)}
            </div></details>
            <button className="mt-3 rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null} onClick={() => void run("config", async () => { await updateGenerationConfig(current.project_id, generationConfig); await refresh(current.project_id); })}>Enregistrer la configuration</button>
          </Panel>}

          {activeTab === "prompts" && <Panel>
            <PanelTitle kicker="Mode Expert SRA" title="Prompts métier RO et TO"
              hint="Ces agents appartiennent uniquement au pipeline SRA et sont indépendants des prompts Chat/RAG." />
            <AgentsView groups={["ro", "to"]} />
          </Panel>}

          {activeTab === "to" && <TargetObjectivesPanel key={current.project_id} project={current} sources={sources} onRefresh={() => refresh(current.project_id)} />}

          {activeTab === "ro" && <>
          <Panel>
            <PanelTitle kicker={current.name} title="1. Générer les RO Types"
              hint="Sélectionnez les documents puis lancez la construction du référentiel des Risk Origin Types. Les Target Objectives seront traités séparément."
              right={
                <button className="rounded border border-edge px-2 py-1 text-xs"
                  disabled={busy !== null || sources.length === 0}
                  onClick={() => setSelectedDocs(allSelected ? [] : [...sources])}>
                  {allSelected ? "Tout désélectionner" : "Tout sélectionner"}
                </button>
              } />
            <div className="mt-2 grid gap-1 md:grid-cols-2">
              {sources.map((source) => (
                <label key={source} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={selectedDocs.includes(source)}
                    disabled={busy !== null}
                    onChange={(event) => setSelectedDocs(event.target.checked
                      ? [...selectedDocs, source]
                      : selectedDocs.filter((item) => item !== source))} />
                  {source}
                </label>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <button className="rounded border border-edge px-3 py-1 text-sm"
                disabled={busy !== null || selectedDocs.length === 0} onClick={generate}>
                Générer les RO Types
              </button>
              {busy === "generate" && (<>
                <span className="flex items-center gap-2 text-sm text-fg-muted">
                  <Spinner /> {stage || "génération en cours"}
                </span>
                <button className="rounded border border-red-500 px-3 py-1 text-sm text-red-400"
                  onClick={() => void stopRo()}>Arrêter la génération RO</button>
              </>)}
            </div>
          </Panel>

          {trace.length > 0 && (
            <Panel>
              <PanelTitle kicker="Exécution" title="Trace du traitement"
                hint="Documents, fenêtres, candidats RO, consolidation et contrôles réellement exécutés." />
              <ol className="mt-3 max-h-72 space-y-1 overflow-y-auto text-sm text-fg-muted">
                {trace.map((entry, index) => (
                  <li key={index} className="border-l border-edge pl-3">{entry}</li>
                ))}
              </ol>
            </Panel>
          )}

          {error && <Banner tone="bad">{error}</Banner>}
          {taskMetrics && <Panel><PanelTitle kicker="Métriques de tâche" title={taskMetrics.status === "completed" ? "Génération accomplie" : "Génération " + taskMetrics.status} hint={taskMetrics.trajectory.llm_calls + " appel(s) LLM · " + taskMetrics.efficiency.total_tokens + " tokens · " + taskMetrics.latency_s + "s"} /><p className="mt-2 text-sm text-fg-muted">Temps LLM cumulé : {taskMetrics.trajectory.llm_time_s}s · charge relative {Math.round((taskMetrics.trajectory.llm_share ?? 0) * 100)} % du temps mur · juge sémantique : non exécuté</p></Panel>}
          {table?.warnings.length ? (
            <Banner tone="warn">{table.warnings.join(" · ")}</Banner>
          ) : null}

          {table && (
            <Panel>
              <PanelTitle kicker="Risk Origins (RO)" title="Table des RO Types"
                hint={`${table.risk_origin_types?.length ?? table.risk_objectives.length} ligne(s) détectée(s) avec le profil ${table.generation_config?.profile ?? "hérité"}.`} />
              <details open className="mt-3"><summary className="cursor-pointer text-sm font-medium text-fg-muted">Afficher / masquer la table RO</summary>
              {(table.risk_origin_types?.length ?? 0) > 0 && (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full border-collapse text-sm">
                    <thead><tr>{["ID", "RO Type canonique", "Définition", "Instances observées", "Caractérisation", "Preuves", "Revue métier"].map((label) => <th key={label} className="border border-edge p-2 text-left">{label}</th>)}</tr></thead>
                    <tbody>{table.risk_origin_types?.map((row, index) => (
                      <tr key={row.ro_type_id}>
                        <td className="border border-edge p-2 align-top">{row.ro_type_id}</td>
                        <td className="border border-edge p-2 align-top font-medium"><input className="w-full bg-transparent" value={row.canonical_type} onChange={(event) => patchRoType(index, { canonical_type: event.target.value, review_status: "modified" })} /></td>
                        <td className="border border-edge p-2 align-top"><textarea className="min-h-20 w-full bg-transparent" value={row.definition} onChange={(event) => patchRoType(index, { definition: event.target.value, review_status: "modified" })} /></td>
                        <td className="border border-edge p-2 align-top">{row.observed_instances.join("; ") || "—"}</td>
                        <td className="border border-edge p-2 align-top"><ul className="space-y-1">{row.characterization.map((item, i) => <li key={i}>{item}</li>)}</ul></td>
                        <td className="border border-edge p-2 align-top"><details><summary className="cursor-pointer">{row.evidence.length} preuve(s)</summary>{row.evidence.map((item, evidenceIndex) => <blockquote key={evidenceIndex} className="mt-2 border-l border-edge pl-2 text-xs"><b>{item.source}</b><br />{item.quote}</blockquote>)}</details></td><td className="border border-edge p-2 align-top"><select className="bg-transparent" value={row.review_status ?? "proposed"} onChange={(event) => patchRoType(index, { review_status: event.target.value })}><option value="proposed">À revoir</option><option value="accepted">Accepté</option><option value="rejected">Rejeté</option><option value="modified">Modifié</option></select><textarea className="mt-2 min-h-16 w-full bg-transparent text-xs" placeholder="Commentaire de revue" value={row.review_comment ?? ""} onChange={(event) => patchRoType(index, { review_comment: event.target.value })} /></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
              {table.schema_version !== "2.0" && <div className="mt-3 overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr>
                      <th className="border border-edge p-2 text-left">Catégorie d&apos;attaquant</th>
                      <th className="border border-edge p-2 text-left">Objectifs visés / justification</th>
                      <th className="w-16 border border-edge p-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {table.risk_objectives.map((row, index) => (
                      <tr key={index}>
                        <td className="border border-edge p-2 align-top">
                          <input className="w-full bg-transparent" value={row.attacker_category}
                            onChange={(event) => patchRow(index, "attacker_category", event.target.value)} />
                        </td>
                        <td className="border border-edge p-2">
                          <textarea className="min-h-20 w-full bg-transparent"
                            value={row.targeted_objectives}
                            onChange={(event) => patchRow(index, "targeted_objectives", event.target.value)} />
                        </td>
                        <td className="border border-edge p-2 text-center">
                          <button onClick={() => removeRow(index)} aria-label="Supprimer la ligne">×</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>}
              </details>
              <div className="mt-3 flex flex-wrap gap-2">
                <button className="rounded border border-edge px-3 py-1 text-sm"
                  disabled={table.schema_version === "2.0"} onClick={addRow}>Ajouter une ligne</button>
                <button className="rounded border border-edge px-3 py-1 text-sm"
                  disabled={!dirty || busy !== null} onClick={save}>Sauvegarder</button>
                <span className="ml-auto flex gap-2">
                  <button className="rounded border border-edge px-3 py-1 text-sm"
                    disabled={dirty || busy !== null}
                    onClick={() => void run("export", () => exportTable(current.project_id, "csv"))}>
                    Exporter CSV
                  </button>
                  <button className="rounded border border-edge px-3 py-1 text-sm"
                    disabled={dirty || busy !== null}
                    onClick={() => void run("export", () => exportTable(current.project_id, "xlsx"))}>
                    Exporter XLSX
                  </button>
                </span>
              </div>
            </Panel>
          )}
          </>}
        </>
      )}
    </div>
  );
}

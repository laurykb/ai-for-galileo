"use client";
import { useState } from "react";
import { Banner, Panel, PanelTitle, Spinner } from "@/components/ui";
import type { TaskMetrics } from "@/lib/types";
import { ClassifiedTargetObjectivesTable, SraProject, TargetObjectivesTable, TreatmentPlan, classifyTargetObjectives, estimateTargetObjectives, exportClassifiedTargetObjectives, exportTargetObjectives, generateTargetObjectives, initializeTreatmentPlan, saveDecisionTargetObjectives, saveTargetObjectivesTable, saveTreatmentPlan, stopGeneration } from "@/lib/sra";
type Props = { project: SraProject; sources: string[]; onRefresh: () => Promise<void> };
type ToView = "decision" | "domains" | "scenarios";
const DOMAIN_LABELS: Record<string, string> = {
  ground_segment: "Segment sol", space_segment: "Segment spatial", supervision: "Supervision",
  communications: "Communications", data: "Données", user: "Utilisateurs",
  supply_chain: "Chaîne d’approvisionnement", other: "Autre",
};
function label(event: Record<string, unknown>): string {
  const stage = String(event.stage ?? "analyse");
  if (stage === "sra_to_document_start") return `${String(event.document)} — ${String(event.batch_count)} fenêtre(s)`;
  if (stage === "sra_to_budget") {
    const estimate = (event.estimate ?? {}) as Record<string, unknown>;
    const budget = (event.budget ?? {}) as Record<string, unknown>;
    return "Charge estimée — " + String(estimate.windows ?? 0) + " fenêtre(s), "
      + String(estimate.ro_types ?? 0) + " RO Type(s), environ "
      + String(estimate.estimated_map_calls ?? 0) + " appel(s) MAP avant croisement · garde-fou "
      + String(budget.max_total_tokens ?? 0) + " tokens";
  }
  if (stage === "sra_to_batch_split") return event.split === "text"
    ? "Sortie TO trop longue — fenêtre divisée en sous-fenêtres"
    : "Sortie TO trop longue — groupe de " + String(event.ro_types) + " RO Types divisé";
  if (stage === "sra_to_ro_group") return String(event.document) + " — fenêtre " + String(event.batch) + ", groupe " + String(event.group) + " : " + (Array.isArray(event.ro_types) ? event.ro_types.join(", ") : "RO Types");
  if (stage === "sra_to_batch_done") return `${String(event.document)} — fenêtre ${String(event.batch)} terminée, ${String(event.new_candidates)} cible(s) candidate(s)`;
  if (stage === "sra_to_document_done") return `${String(event.document)} terminé — ${String(event.candidates)} cible(s) candidate(s)`;
  if (stage === "sra_to_adjudication_start") return `Construction des scénarios depuis ${String(event.candidates)} cible(s)`;
  if (stage === "sra_to_adjudication_incomplete") return `Lot ${String(event.batch)} incomplet — ${Array.isArray(event.technical_rejections) ? event.technical_rejections.length : 0} candidat(s) conservé(s) pour reprise`;
  if (stage === "sra_to_classification_prepare") return "Préparation du classement — " + String(event.scenarios) + " scénario(s) source";
  if (stage === "sra_to_classification_start") return "Consolidation lancée — " + String(event.groups) + " objectif(s) RO Type × effet";
  if (stage === "sra_to_classification_split") return "Sous-lots MAP/REDUCE — " + String(event.scenarios) + " scénario(s) répartis en " + String(event.batches) + " lot(s)";
  if (stage === "sra_to_classification_group") return "Objectif " + String(event.index) + "/" + String(event.groups) + " — " + String(event.ro_type_id) + " · " + String(event.effect_category) + " · " + String(event.scenarios) + " scénario(s)";
  if (stage === "sra_to_classification_saved") return "Table décisionnelle sauvegardée — " + String(event.groups) + " TO · Excel prêt";
  if (stage === "sra_to_consolidation") return "Consolidation — " + String(event.groups_retained) + " RO Type(s), " + String(event.scenarios_retained) + " scénario(s), " + String(event.targets) + " cible(s)";
  if (stage === "map_complete") return `Balayage exhaustif terminé — ${String(event.items)} candidat(s)`;
  return stage;
}
export function TargetObjectivesPanel({ project, sources, onRefresh }: Props) {
  const [table, setTable] = useState<TargetObjectivesTable | null>(project.target_objectives_table ?? null);
  const [classified, setClassified] = useState<ClassifiedTargetObjectivesTable | null>(project.classified_target_objectives_table ?? null);
  const [treatment, setTreatment] = useState<TreatmentPlan | null>(project.treatment_plan ?? null);
  const [decisionDirty, setDecisionDirty] = useState(false);
  const [treatmentDirty, setTreatmentDirty] = useState(false);
  const [view, setView] = useState<ToView>(project.classified_target_objectives_table?.schema_version === "2.0" ? "decision" : "scenarios");
  const [selectedDocuments, setSelectedDocuments] = useState<string[]>(project.target_objectives_table?.source_document_ids?.length ? project.target_objectives_table.source_document_ids : project.document_ids);
  const [classificationTrace, setClassificationTrace] = useState<string[]>((project.classified_target_objectives_table?.trace ?? []).map(label));
  const [trace, setTrace] = useState<string[]>((project.target_objectives_table?.trace ?? []).map(label));
  const [taskMetrics, setTaskMetrics] = useState<TaskMetrics | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null); const [error, setError] = useState<string | null>(null);
  const run = async (name: string, action: () => Promise<void>) => { setBusy(name); setError(null); try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(null); } };
  const patchRelation = (index: number, patch: Record<string, unknown>) => {
    if (!table) return;
    setTable({ ...table, target_objectives: table.target_objectives.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row) });
    setDirty(true);
  };
  const patchScenario = (rowIndex: number, scenarioIndex: number, patch: Record<string, unknown>) => {
    if (!table) return;
    setTable({ ...table, target_objectives: table.target_objectives.map((row, index) => index !== rowIndex ? row : {
      ...row, review_status: "modified", attacks: (row.attacks ?? []).map((scenario, attackIndex) => attackIndex === scenarioIndex ? { ...scenario, ...patch } : scenario),
    }) });
    setDirty(true);
  };
  const save = () => run("save-to", async () => { if (!table) return; await saveTargetObjectivesTable(project.project_id, table); setClassified(null); setTreatment(null); setView("scenarios"); setDirty(false); await onRefresh(); });
  const stopTo = async () => {
    try { await stopGeneration(project.project_id, "to"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const generate = () => run("generate-to", async () => {
    const forecast = await estimateTargetObjectives(project.project_id, selectedDocuments);
    const minutes = Math.ceil(forecast.estimate.estimated_wall_s / 60);
    const resume = forecast.estimate.resume_available ? " Un checkpoint compatible sera repris." : "";
    if (forecast.estimate.confirmation_required && !window.confirm("Charge estimée : " + forecast.estimate.windows + " fenêtres MAP, jusqu à " + minutes + " min." + resume + " Continuer ?")) return;
    setTrace([]); setTaskMetrics(null); await generateTargetObjectives(project.project_id, selectedDocuments, (event) => {
      if (event.type === "stage") setTrace((current) => [...current, label(event)]);
      if (event.type === "target_objectives_table") { setTable(event.table); setClassified(null); setTreatment(null); setView("scenarios"); }
      if (event.type === "task_metrics") setTaskMetrics(event.metrics);
      if (event.type === "error") setError(event.message);
    }, true); await onRefresh();
  });
  const classify = () => run("classify-to", async () => {
    setClassificationTrace([]);
    let generationError = "";
    await classifyTargetObjectives(project.project_id, (event) => {
      if (event.type === "stage") setClassificationTrace((current) => [...current, label(event)]);
      if (event.type === "classified_target_objectives_table" && event.table) { setClassified(event.table); setTreatment(null); setDecisionDirty(false); setView("decision"); setClassificationTrace((event.table.trace ?? []).map(label)); }
      if (event.type === "error") generationError = event.message ?? "Échec du classement";
    });
    if (generationError) throw new Error(generationError);
    await onRefresh();
    await exportClassifiedTargetObjectives(project.project_id, "xlsx");
  });
  const patchDecision = (index: number, patch: Record<string, unknown>) => {
    if (!classified) return;
    setClassified({ ...classified, classified_objectives: classified.classified_objectives.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row) });
    setDecisionDirty(true);
  };
  const saveDecision = () => run("save-decision", async () => { if (!classified) return; const saved = await saveDecisionTargetObjectives(project.project_id, classified); setTreatment(saved.treatment_plan ?? null); setDecisionDirty(false); await onRefresh(); });
  const initializeTreatment = () => run("initialize-treatment", async () => { const saved = await initializeTreatmentPlan(project.project_id); setTreatment(saved.treatment_plan ?? null); setTreatmentDirty(false); await onRefresh(); });
  const patchTreatment = (index: number, patch: Record<string, unknown>) => {
    if (!treatment) return;
    setTreatment({ ...treatment, items: treatment.items.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row) });
    setTreatmentDirty(true);
  };
  const persistTreatment = () => run("save-treatment", async () => { if (!treatment) return; await saveTreatmentPlan(project.project_id, treatment); setTreatmentDirty(false); await onRefresh(); });
  const classifiedRoSpans = new Map<string, number>();
  for (const row of classified?.classified_objectives ?? []) classifiedRoSpans.set(row.ro_type_id, (classifiedRoSpans.get(row.ro_type_id) ?? 0) + 1);
  const domainMatrix = new Map<string, { scenarioIds: Set<string>; roTypes: Set<string>; targetNames: Set<string> }>();
  for (const relation of table?.target_objectives ?? []) {
    for (const scenario of relation.attacks ?? []) {
      for (const domain of scenario.target_domains?.length ? scenario.target_domains : ["other"] as const) {
        const row = domainMatrix.get(domain) ?? { scenarioIds: new Set<string>(), roTypes: new Set<string>(), targetNames: new Set<string>() };
        row.scenarioIds.add(scenario.scenario_id); row.roTypes.add(relation.ro_type_id);
        for (const target of scenario.target_names) row.targetNames.add(target);
        domainMatrix.set(domain, row);
      }
    }
  }
  const domainRows = [...domainMatrix.entries()].sort(([left], [right]) => (DOMAIN_LABELS[left] ?? left).localeCompare(DOMAIN_LABELS[right] ?? right, "fr"));
  return <>
    <Panel><PanelTitle kicker="Référentiel source" title={"Table RO utilisée · " + (project.table?.risk_origin_types?.length ?? 0) + " RO Type(s)"} hint={table?.source_ro_revision_id ? "Révision RO " + table.source_ro_revision_id + " · générée le " + (project.table?.generated_at ?? "date inconnue") : "Table RO active · générée le " + (project.table?.generated_at ?? "date inconnue")} /></Panel>
    <Panel><PanelTitle kicker={project.name} title="2. Croiser les RO Types avec les Target Objectives" hint="Choisissez explicitement les documents d architecture sur lesquels fonder les cibles potentielles." right={<button className="rounded border border-edge px-2 py-1 text-xs" disabled={busy !== null || sources.length === 0} onClick={() => setSelectedDocuments(selectedDocuments.length === sources.length ? [] : [...sources])}>{selectedDocuments.length === sources.length ? "Tout désélectionner" : "Tout sélectionner"}</button>} />
      <div className="mt-3 grid gap-1 md:grid-cols-2">{sources.map((source) => <label key={source} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selectedDocuments.includes(source)} disabled={busy !== null} onChange={(event) => setSelectedDocuments(event.target.checked ? [...selectedDocuments, source] : selectedDocuments.filter((item) => item !== source))} />{source}</label>)}</div>
      {table?.source_document_ids?.length ? <p className="mt-3 text-xs text-fg-muted">Dernière table générée à partir de {table.source_document_ids.length} document(s) : {table.source_document_ids.join(" · ")}</p> : null}
      <div className="mt-3 flex items-center gap-3"><button className="rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null || selectedDocuments.length === 0 || !(project.table?.risk_origin_types?.length)} onClick={generate}>Générer les scénarios TO par RO Type</button>
      {busy === "generate-to" && <>
        <span className="flex items-center gap-2 text-sm text-fg-muted"><Spinner /> Analyse croisée en cours</span>
        <button className="rounded border border-red-500 px-3 py-1 text-sm text-red-400" onClick={() => void stopTo()}>Arrêter la génération TO</button>
      </>}</div></Panel>
    {trace.length > 0 && <Panel><PanelTitle kicker="Exécution TO" title="Trace du traitement" hint="Documents, fenêtres, candidats, adjudication et consolidation TO réellement exécutés." />
      <ol className="mt-3 max-h-72 space-y-1 overflow-y-auto text-sm text-fg-muted">{trace.map((entry, index) => <li key={index} className="border-l border-edge pl-3">{entry}</li>)}</ol></Panel>}
    {classificationTrace.length > 0 && <Panel><PanelTitle kicker="Génération décisionnelle" title="Trace de la consolidation TO" hint="Regroupement RO Type × effet de sécurité, avec conservation de chaque scénario source." />
      <ol className="mt-3 max-h-72 space-y-1 overflow-y-auto text-sm text-fg-muted">{classificationTrace.map((entry, index) => <li key={index} className="border-l border-edge pl-3">{entry}</li>)}</ol></Panel>}
    {error && <Banner tone="bad">{error}</Banner>}{taskMetrics && <Panel><PanelTitle kicker="Métriques de tâche TO" title={taskMetrics.status === "completed" ? "Génération accomplie" : "Génération " + taskMetrics.status} hint={taskMetrics.trajectory.llm_calls + " appel(s) LLM · " + taskMetrics.efficiency.total_tokens + " tokens · " + taskMetrics.latency_s + "s"} /><p className="mt-2 text-sm text-fg-muted">Temps LLM cumulé : {taskMetrics.trajectory.llm_time_s}s · charge relative {Math.round((taskMetrics.trajectory.llm_share ?? 0) * 100)} % du temps mur · juge sémantique : non exécuté</p></Panel>}{table?.completion_status === "partial" ? <Banner tone="warn">Table partielle sauvegardée. Relancez avec les mêmes paramètres pour reprendre au dernier checkpoint.</Banner> : null}
    {table?.completion_status === "incomplete" ? <Banner tone="warn">Adjudication techniquement incomplète. Les candidats concernés sont conservés dans l’audit et le lot sera rejoué à la prochaine relance.</Banner> : null}
    {table?.warnings.length ? <Banner tone="warn">{table.warnings.join(" · ")}</Banner> : null}
    {table && <Panel><PanelTitle kicker="Target Objectives (TO)" title="Analyse des scénarios et objectifs" hint={`${table.target_objectives.length} RO Type(s) · ${table.target_objectives.reduce((total, row) => total + (row.attacks?.length ?? 0), 0)} scénario(s) d’attaque.`} />
      <div className="mt-3 flex flex-wrap gap-2" role="tablist" aria-label="Vues Target Objectives">
        <button className={`rounded border px-3 py-1.5 text-sm ${view === "decision" ? "border-accent-bright text-accent-bright" : "border-edge"}`} disabled={!classified || classified.schema_version !== "2.0"} onClick={() => setView("decision")}>TO décisionnels{classified?.schema_version === "2.0" ? ` (${classified.classified_objectives.length})` : ""}</button>
        <button className={`rounded border px-3 py-1.5 text-sm ${view === "domains" ? "border-accent-bright text-accent-bright" : "border-edge"}`} onClick={() => setView("domains")}>Matrice par domaine ({domainRows.length})</button>
        <button className={`rounded border px-3 py-1.5 text-sm ${view === "scenarios" ? "border-accent-bright text-accent-bright" : "border-edge"}`} onClick={() => setView("scenarios")}>Scénarios exhaustifs</button>
      </div>
      {view === "scenarios" && <details open className="mt-3"><summary className="cursor-pointer text-sm font-medium text-fg-muted">Afficher / masquer les scénarios exhaustifs</summary><div className="mt-3 overflow-x-auto"><table className="w-full border-collapse text-sm"><thead><tr>{["TO ID", "RO Type canonique", "Attaques, objectifs et cibles", "Revue métier"].map((item) => <th key={item} className="border border-edge p-2 text-left">{item}</th>)}</tr></thead>
      <tbody>{table.target_objectives.map((row, rowIndex) => <tr key={row.to_id}><td className="border border-edge p-2 align-top">{row.to_id}</td><td className="border border-edge p-2 align-top font-medium">{row.ro_canonical_type}<p className="mt-1 text-xs font-normal text-fg-muted">{row.ro_definition}</p></td><td className="border border-edge p-2 align-top"><div className="space-y-3">{(row.attacks ?? []).map((scenario, scenarioIndex) => <section key={scenario.scenario_id} className="rounded border border-edge p-2"><div className="flex flex-wrap justify-between gap-2 text-xs text-fg-faint"><span>{scenario.scenario_id}</span>{scenario.plausibility_level && scenario.plausibility_level !== "unassessed" ? <span title={scenario.plausibility_method}>Plausibilité {scenario.plausibility_level} · {Math.round((scenario.plausibility_score ?? 0) * 100)} %</span> : null}</div><input className="w-full bg-transparent font-medium" value={scenario.attack} onChange={(event) => patchScenario(rowIndex, scenarioIndex, { attack: event.target.value })} /><textarea className="mt-1 min-h-12 w-full bg-transparent" value={scenario.objective} onChange={(event) => patchScenario(rowIndex, scenarioIndex, { objective: event.target.value })} /><p className="text-xs"><b>Cibles :</b> {scenario.target_names.join(", ")}</p><div className="mt-1 flex flex-wrap gap-1 text-[11px]">{(scenario.security_properties ?? []).map((item) => <span key={item} className="rounded bg-surface-2 px-1.5 py-0.5">Propriété · {item}</span>)}{(scenario.target_domains ?? []).map((item) => <span key={item} className="rounded bg-surface-2 px-1.5 py-0.5">Domaine · {item}</span>)}{(scenario.motivations ?? []).map((item) => <span key={item} className="rounded bg-surface-2 px-1.5 py-0.5">Motivation · {item}</span>)}</div><textarea className="mt-1 min-h-16 w-full bg-transparent text-xs" value={scenario.rationale} onChange={(event) => patchScenario(rowIndex, scenarioIndex, { rationale: event.target.value })} /><details><summary className="cursor-pointer text-xs">{scenario.evidence.length} preuve(s) de cible</summary>{scenario.evidence.map((item, evidenceIndex) => <blockquote key={evidenceIndex} className="mt-2 border-l border-edge pl-2 text-xs"><b>{item.source}</b><br />{item.quote}</blockquote>)}</details></section>)}{!row.attacks?.length && <p className="text-fg-muted">Ancienne relation TO : {row.target_name} — {row.rationale}</p>}</div></td><td className="border border-edge p-2 align-top"><select className="bg-transparent" value={row.review_status ?? "proposed"} onChange={(event) => patchRelation(rowIndex, { review_status: event.target.value })}><option value="proposed">À revoir</option><option value="accepted">Accepté</option><option value="rejected">Rejeté</option><option value="modified">Modifié</option></select><textarea className="mt-2 min-h-16 w-full bg-transparent text-xs" placeholder="Commentaire de revue" value={row.review_comment ?? ""} onChange={(event) => patchRelation(rowIndex, { review_comment: event.target.value })} /></td></tr>)}</tbody></table></div></details>}
      {view === "domains" && <div className="mt-3"><Banner tone="neutral">Matrice de couverture opérationnelle : un scénario peut apparaître dans plusieurs domaines. Les lignes ci-dessous ne sont pas des Target Objectives.</Banner><div className="mt-3 overflow-x-auto"><table className="w-full border-collapse text-sm"><thead><tr>{["Domaine", "RO Types concernés", "Scénarios", "Cibles couvertes"].map((item) => <th key={item} className="border border-edge p-2 text-left">{item}</th>)}</tr></thead><tbody>{domainRows.map(([domain, row]) => <tr key={domain}><td className="border border-edge p-2 align-top font-medium">{DOMAIN_LABELS[domain] ?? domain}</td><td className="border border-edge p-2 align-top">{[...row.roTypes].sort().join(" · ")}</td><td className="border border-edge p-2 align-top"><details><summary className="cursor-pointer">{row.scenarioIds.size} scénario(s)</summary><p className="mt-2 text-xs text-fg-muted">{[...row.scenarioIds].sort().join(" · ")}</p></details></td><td className="border border-edge p-2 align-top">{[...row.targetNames].sort((left, right) => left.localeCompare(right, "fr")).join(", ")}</td></tr>)}</tbody></table></div></div>}
      <div className="mt-3 flex justify-end gap-2"><button className="rounded border border-edge px-3 py-1 text-sm" disabled={!dirty || busy !== null} onClick={save}>Sauvegarder la revue</button><button className="rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null} onClick={() => void run("export-to", () => exportTargetObjectives(project.project_id, "csv"))}>Exporter CSV</button><button className="rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null} onClick={() => void run("export-to", () => exportTargetObjectives(project.project_id, "xlsx"))}>Exporter XLSX</button></div>
      <div className="mt-3 border-t border-edge pt-3"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-medium">Table TO décisionnelle</h3><p className="text-xs text-fg-muted">Vue compacte RO Type × effet de sécurité ; tous les scénarios, domaines et preuves restent traçables.</p></div><button className="rounded border border-accent-bright px-3 py-2 text-sm" disabled={busy !== null || !table} onClick={classify}>{busy === "classify-to" ? "Consolidation en cours…" : "Générer environ 20 TO décisionnels"}</button></div></div>
    </Panel>}
    {classified?.warnings.length ? <Banner tone="warn">{classified.warnings.join(" · ")}</Banner> : null}
    {classified?.schema_version === "2.0" && view === "decision" && <Panel><PanelTitle kicker="TO dérivée" title="Table TO décisionnelle" hint={classified.classified_objectives.length + " objectif(s), budget " + (classified.target_objective_budget ?? 20) + ", depuis la révision TO " + (classified.source_to_revision_id || "active") + "."} />
      <details open className="mt-3"><summary className="cursor-pointer text-sm font-medium text-fg-muted">Afficher / masquer les TO décisionnels</summary><div className="mt-3 overflow-x-auto"><table className="w-full border-collapse text-sm"><thead><tr>{["RO Type", "Effet recherché", "Domaines", "Attaque générique", "Synthèse", "Traçabilité"].map((item) => <th key={item} className="border border-edge p-2 text-left">{item}</th>)}</tr></thead><tbody>{classified.classified_objectives.map((row, rowIndex) => <tr key={row.classification_id}>{(rowIndex === 0 || classified.classified_objectives[rowIndex - 1].ro_type_id !== row.ro_type_id) && <td rowSpan={classifiedRoSpans.get(row.ro_type_id)} className="border border-edge p-2 align-top"><b>{row.ro_type_id}</b><br />{row.ro_canonical_type}</td>}<td className="border border-edge p-2 align-top font-medium">{row.effect_category}</td><td className="border border-edge p-2 align-top"><span className="rounded bg-surface-2 px-2 py-1">Principal · {row.primary_target_domain ?? row.target_domain}</span>{row.secondary_target_domains?.length ? <p className="mt-2 text-xs text-fg-muted">Secondaires · {row.secondary_target_domains.join(", ")}</p> : null}</td><td className="border border-edge p-2 align-top font-medium">{row.generic_attack_name}</td><td className="border border-edge p-2 align-top"><p>{row.attack_summary}</p><p className="mt-2 text-fg-muted"><b>Objectifs :</b> {row.objective_summary}</p><p className="mt-2 text-xs"><b>Cibles :</b> {row.target_names.join(", ")}</p></td><td className="border border-edge p-2 align-top"><details><summary className="cursor-pointer">{row.source_scenario_count} scénario(s) source</summary><p className="mt-2 text-xs">{row.scenario_ids.join(" · ")}</p></details></td></tr>)}</tbody></table></div></details>
      <div className="mt-3 flex justify-end gap-2"><button className="rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null} onClick={() => void run("export-classified-csv", () => exportClassifiedTargetObjectives(project.project_id, "csv"))}>Exporter CSV</button><button className="rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null} onClick={() => void run("export-classified-xlsx", () => exportClassifiedTargetObjectives(project.project_id, "xlsx"))}>Exporter XLSX</button></div>
    </Panel>}
    {classified?.schema_version === "2.0" && view === "decision" && <Panel><PanelTitle kicker="Revue métier" title="Valider les TO avant traitement" hint="Une modification invalide automatiquement le plan de traitement existant." />
      <div className="mt-3 space-y-2">{classified.classified_objectives.map((row, index) => <div key={row.classification_id} className="grid gap-2 rounded border border-edge p-2 md:grid-cols-[9rem_1fr_2fr]"><select className="bg-transparent text-sm" value={row.review_status ?? "proposed"} onChange={(event) => patchDecision(index, { review_status: event.target.value })}><option value="proposed">À revoir</option><option value="accepted">Accepté</option><option value="rejected">Rejeté</option><option value="modified">Modifié</option></select><input className="bg-transparent text-sm font-medium" value={row.generic_attack_name} onChange={(event) => patchDecision(index, { generic_attack_name: event.target.value, review_status: "modified" })} /><textarea className="min-h-12 bg-transparent text-xs" placeholder="Commentaire de revue" value={row.review_comment ?? ""} onChange={(event) => patchDecision(index, { review_comment: event.target.value })} /></div>)}</div>
      <div className="mt-3 flex justify-end"><button className="rounded border border-edge px-3 py-1 text-sm" disabled={!decisionDirty || busy !== null} onClick={saveDecision}>Sauvegarder la revue décisionnelle</button></div>
    </Panel>}
    {classified?.schema_version === "2.0" && <Panel><PanelTitle kicker="Traitement" title={treatment ? "Plan de traitement" : "Initialiser le plan de traitement"} hint="Le plan est dérivé des TO non rejetés. Aucune mesure technique n’est inventée automatiquement." />
      {!treatment ? <button className="mt-3 rounded border border-accent-bright px-3 py-2 text-sm" disabled={busy !== null || decisionDirty} onClick={initializeTreatment}>Créer le plan depuis les TO revus</button> : <><div className="mt-3 overflow-x-auto"><table className="w-full border-collapse text-sm"><thead><tr>{["TO", "Résultat attendu", "Mesures à définir", "Pilotage"].map((item) => <th key={item} className="border border-edge p-2 text-left">{item}</th>)}</tr></thead><tbody>{treatment.items.map((item, index) => <tr key={item.treatment_id}><td className="border border-edge p-2 align-top"><b>{item.ro_type_id}</b><p className="mt-1 text-xs">{item.target_objective}</p></td><td className="border border-edge p-2 align-top">{item.expected_security_outcome}</td><td className="border border-edge p-2 align-top"><textarea className="min-h-20 w-full bg-transparent" placeholder="Une mesure par ligne" value={item.measures.join("\n")} onChange={(event) => patchTreatment(index, { measures: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} /></td><td className="border border-edge p-2 align-top"><input className="w-full bg-transparent" placeholder="Responsable" value={item.owner} onChange={(event) => patchTreatment(index, { owner: event.target.value })} /><input type="date" className="mt-2 w-full bg-transparent" value={item.due_date} onChange={(event) => patchTreatment(index, { due_date: event.target.value })} /><select className="mt-2 w-full bg-transparent" value={item.implementation_status} onChange={(event) => patchTreatment(index, { implementation_status: event.target.value })}><option value="to_define">À définir</option><option value="planned">Planifié</option><option value="in_progress">En cours</option><option value="implemented">Implémenté</option><option value="accepted_risk">Risque accepté</option></select></td></tr>)}</tbody></table></div><div className="mt-3 flex justify-end"><button className="rounded border border-edge px-3 py-1 text-sm" disabled={!treatmentDirty || busy !== null} onClick={persistTreatment}>Sauvegarder le plan</button></div></>}
    </Panel>}
  </>
}

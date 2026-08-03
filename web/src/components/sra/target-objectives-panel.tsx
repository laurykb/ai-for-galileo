"use client";
import { useState } from "react";
import { Banner, Panel, PanelTitle, Spinner } from "@/components/ui";
import { SraProject, TargetObjectivesTable, exportTargetObjectives, generateTargetObjectives, saveTargetObjectivesTable, stopGeneration } from "@/lib/sra";
type Props = { project: SraProject; onRefresh: () => Promise<void> };
function label(event: Record<string, unknown>): string {
  const stage = String(event.stage ?? "analyse");
  if (stage === "sra_to_document_start") return `${String(event.document)} — ${String(event.batch_count)} fenêtre(s)`;
  if (stage === "sra_to_batch_split") return event.split === "text"
    ? "Sortie TO trop longue — fenêtre divisée en sous-fenêtres"
    : "Sortie TO trop longue — groupe de " + String(event.ro_types) + " RO Types divisé";
  if (stage === "sra_to_batch_done") return `${String(event.document)} — fenêtre ${String(event.batch)} terminée, ${String(event.new_candidates)} relation(s) candidate(s)`;
  if (stage === "sra_to_document_done") return `${String(event.document)} terminé — ${String(event.candidates)} relation(s) candidate(s)`;
  if (stage === "sra_to_adjudication_start") return `Adjudication de ${String(event.candidates)} relation(s) candidate(s)`;
  if (stage === "sra_to_consolidation") return "Consolidation — " + String(event.relations_retained) + " relation(s), " + String(event.targets) + " cible(s), " + String(event.ro_types) + " RO Type(s)";
  if (stage === "map_complete") return `Balayage exhaustif terminé — ${String(event.items)} candidat(s)`;
  return stage;
}
export function TargetObjectivesPanel({ project, onRefresh }: Props) {
  const [table, setTable] = useState<TargetObjectivesTable | null>(project.target_objectives_table ?? null);
  const [trace, setTrace] = useState<string[]>((project.target_objectives_table?.trace ?? []).map(label));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null); const [error, setError] = useState<string | null>(null);
  const run = async (name: string, action: () => Promise<void>) => { setBusy(name); setError(null); try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(null); } };
  const patchRelation = (index: number, patch: Record<string, unknown>) => {
    if (!table) return;
    setTable({ ...table, target_objectives: table.target_objectives.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row) });
    setDirty(true);
  };
  const save = () => run("save-to", async () => { if (!table) return; await saveTargetObjectivesTable(project.project_id, table); setDirty(false); await onRefresh(); });
  const stopTo = async () => {
    try { await stopGeneration(project.project_id, "to"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const generate = () => run("generate-to", async () => {
    setTrace([]); await generateTargetObjectives(project.project_id, (event) => {
      if (event.type === "stage") setTrace((current) => [...current, label(event)]);
      if (event.type === "target_objectives_table") setTable(event.table);
      if (event.type === "error") setError(event.message);
    }); await onRefresh();
  });
  return <>
    <Panel><PanelTitle kicker={project.name} title="2. Croiser les RO Types avec les cibles Galileo" hint="Analyse les trois documents d architecture dédiés et recense les cibles potentielles pour chaque RO Type canonique retenu." />
      <div className="mt-3 flex items-center gap-3"><button className="rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null || !(project.table?.risk_origin_types?.length)} onClick={generate}>Générer la table RO × cibles</button>
      {busy === "generate-to" && <>
        <span className="flex items-center gap-2 text-sm text-fg-muted"><Spinner /> Analyse croisée en cours</span>
        <button className="rounded border border-red-500 px-3 py-1 text-sm text-red-400" onClick={() => void stopTo()}>Arrêter la génération TO</button>
      </>}</div></Panel>
    {trace.length > 0 && <Panel><PanelTitle kicker="Exécution TO" title="Trace du traitement" hint="Documents, fenêtres, candidats, adjudication et consolidation TO réellement exécutés." />
      <ol className="mt-3 max-h-72 space-y-1 overflow-y-auto text-sm text-fg-muted">{trace.map((entry, index) => <li key={index} className="border-l border-edge pl-3">{entry}</li>)}</ol></Panel>}
    {error && <Banner tone="bad">{error}</Banner>}{table?.warnings.length ? <Banner tone="warn">{table.warnings.join(" · ")}</Banner> : null}
    {table && <Panel><PanelTitle kicker="Target Objectives (TO)" title="Table des cibles potentielles par RO Type" hint={`${table.target_objectives.length} relation(s) RO–cible retenue(s).`} />
      <div className="mt-3 overflow-x-auto"><table className="w-full border-collapse text-sm"><thead><tr>{["TO ID", "RO Type canonique", "Cible potentielle", "Catégorie", "Justification", "Preuves", "Revue métier"].map((item) => <th key={item} className="border border-edge p-2 text-left">{item}</th>)}</tr></thead>
      <tbody>{table.target_objectives.map((row, rowIndex) => <tr key={row.to_id}><td className="border border-edge p-2 align-top">{row.to_id}</td><td className="border border-edge p-2 align-top font-medium">{row.ro_canonical_type}</td><td className="border border-edge p-2 align-top"><input className="w-full bg-transparent" value={row.target_name} onChange={(event) => patchRelation(rowIndex, { target_name: event.target.value, review_status: "modified" })} /></td><td className="border border-edge p-2 align-top"><ul className="space-y-1">{[row.target_category].map((item, index) => <li key={index}>{item}</li>)}</ul></td><td className="border border-edge p-2 align-top"><textarea className="min-h-20 w-full bg-transparent" value={row.rationale} onChange={(event) => patchRelation(rowIndex, { rationale: event.target.value, review_status: "modified" })} /></td><td className="border border-edge p-2 align-top"><details><summary className="cursor-pointer">{row.evidence.length} preuve(s)</summary>{row.evidence.map((item, evidenceIndex) => <blockquote key={evidenceIndex} className="mt-2 border-l border-edge pl-2 text-xs"><b>{item.source}</b><br />{item.quote}</blockquote>)}</details></td><td className="border border-edge p-2 align-top"><select className="bg-transparent" value={row.review_status ?? "proposed"} onChange={(event) => patchRelation(rowIndex, { review_status: event.target.value })}><option value="proposed">À revoir</option><option value="accepted">Accepté</option><option value="rejected">Rejeté</option><option value="modified">Modifié</option></select><textarea className="mt-2 min-h-16 w-full bg-transparent text-xs" placeholder="Commentaire de revue" value={row.review_comment ?? ""} onChange={(event) => patchRelation(rowIndex, { review_comment: event.target.value })} /></td></tr>)}</tbody></table></div>
      <div className="mt-3 flex justify-end gap-2"><button className="rounded border border-edge px-3 py-1 text-sm" disabled={!dirty || busy !== null} onClick={save}>Sauvegarder la revue</button><button className="rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null} onClick={() => void run("export-to", () => exportTargetObjectives(project.project_id, "csv"))}>Exporter CSV</button><button className="rounded border border-edge px-3 py-1 text-sm" disabled={busy !== null} onClick={() => void run("export-to", () => exportTargetObjectives(project.project_id, "xlsx"))}>Exporter XLSX</button></div>
    </Panel>}</>;
}

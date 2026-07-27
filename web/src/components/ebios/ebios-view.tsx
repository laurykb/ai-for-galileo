// web/src/components/ebios/ebios-view.tsx
"use client";

import { useCallback, useEffect, useState } from "react";

import { Atelier1Tables } from "@/components/ebios/atelier1-tables";
import { Banner, Panel, PanelTitle, Pill, Spinner } from "@/components/ui";
import { getJSON, SourcesResponse } from "@/lib/api";
import {
  Atelier1, EbiosProject, Study, VersionDoc,
  createProject, exportAtelier, getProject, listProjects, putAtelier,
  streamGenerate, updateDocuments, validateAtelier,
} from "@/lib/ebios";

const STATUS_LABELS: Record<string, string> = {
  created: "créé", draft_generated: "brouillon généré", draft_reviewed: "brouillon relu",
  validated: "validé", exported: "exporté",
};
const ATELIERS_FUTURS = ["Atelier 2", "Atelier 3", "Atelier 4", "Atelier 5"];

export function EbiosView() {
  const [projects, setProjects] = useState<EbiosProject[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [current, setCurrent] = useState<EbiosProject | null>(null);
  const [latest, setLatest] = useState<VersionDoc | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [draft, setDraft] = useState<Study | null>(null);
  const [dirty, setDirty] = useState(false);
  const [newName, setNewName] = useState("");
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportFmt, setExportFmt] = useState("xlsx");

  const refresh = useCallback(async (projectId?: string) => {
    const [pl, src] = await Promise.all([
      listProjects(),
      getJSON<SourcesResponse>("/api/sources").catch(() => ({ available: false, sources: [] })),
    ]);
    setProjects(pl.projects);
    setSources(src.sources.map((s) => s.name));
    if (projectId) {
      const detail = await getProject(projectId);
      setCurrent(detail.project);
      setLatest(detail.latest);
      setWarnings(detail.warnings);
      setDraft(detail.latest ? detail.latest.study : null);
      setSelectedDocs(detail.project.document_ids);
      setDirty(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => { void refresh().catch((e) => setError(String(e))); }, 0);
    return () => clearTimeout(t);
  }, [refresh]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError(null);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); setStage(null); }
  };

  const onCreate = () => run("create", async () => {
    const p = await createProject(newName, selectedDocs);
    setNewName("");
    await refresh(p.project_id);
  });

  const onGenerate = () => run("generate", async () => {
    if (!current) return;
    // La génération lit document_ids côté serveur : on persiste d'abord la
    // sélection affichée pour que la re-génération porte toujours dessus.
    await updateDocuments(current.project_id, selectedDocs);
    await streamGenerate(current.project_id, (ev) => {
      if (ev.type === "stage") setStage(
        ev.stage + (ev.document ? ` — ${ev.document} (${ev.domaine})` : ""));
      if (ev.type === "draft") { setDraft(ev.study); setWarnings(ev.warnings); }
      if (ev.type === "error") setError(ev.message);
    });
    await refresh(current.project_id);
  });

  const onSave = () => run("save", async () => {
    if (!current || !draft) return;
    await putAtelier(current.project_id, draft, latest?.version ?? 0);
    await refresh(current.project_id);
  });

  const onValidate = () => run("validate", async () => {
    if (!current || !latest) return;
    await validateAtelier(current.project_id, latest.version, "ingénieur");
    await refresh(current.project_id);
  });

  const onExport = () => run("export", async () => {
    if (!current) return;
    await exportAtelier(current.project_id, exportFmt);
    await refresh(current.project_id);
  });

  const setA1 = (a1: Atelier1) => {
    if (!draft) return;
    setDraft({ ...draft, atelier_1: a1 });
    setDirty(true);
  };

  const validated = Boolean(draft?.validated_at);
  const allSelected = sources.length > 0 && selectedDocs.length === sources.length;

  return (
    <div className="space-y-6">
      <Panel>
        <PanelTitle kicker="EBIOS RM" title="Dossiers"
          hint="Un dossier regroupe des documents ingérés et les versions successives de l'Atelier 1." />
        <div className="mt-2 flex flex-wrap gap-2">
          {projects.map((p) => (
            <button key={p.project_id}
              className={`rounded border px-3 py-1 text-sm ${
                current?.project_id === p.project_id
                  ? "border-accent-bright" : "border-edge"}`}
              onClick={() => refresh(p.project_id).catch((e) => setError(String(e)))}>
              {p.name} <Pill tone="neutral">{STATUS_LABELS[p.status] ?? p.status}</Pill>
            </button>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <input className="flex-1 rounded border border-edge bg-transparent px-2 py-1 text-sm"
            placeholder="Nom du nouveau dossier" value={newName}
            onChange={(e) => setNewName(e.target.value)} />
          <button className="rounded border border-edge px-3 py-1 text-sm"
            disabled={!newName.trim() || busy !== null} onClick={onCreate}>
            Créer
          </button>
        </div>
      </Panel>

      {current && (
        <>
          <Panel>
            <PanelTitle kicker={current.name} title="Documents et génération"
              hint="Sélectionner les documents déjà ingérés puis générer le brouillon sourcé."
              right={
                <button className="rounded border border-edge px-2 py-1 text-xs text-fg-muted"
                  disabled={busy !== null || sources.length === 0}
                  onClick={() => setSelectedDocs(allSelected ? [] : [...sources])}>
                  {allSelected ? "Tout désélectionner" : "Tout sélectionner"}
                </button>
              } />
            <div className="mt-2 grid gap-1 md:grid-cols-2">
              {sources.map((s) => (
                <label key={s} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={selectedDocs.includes(s)}
                    disabled={busy !== null}
                    onChange={(e) => setSelectedDocs(e.target.checked
                      ? [...selectedDocs, s] : selectedDocs.filter((x) => x !== s))} />
                  {s}
                </label>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <button className="rounded border border-edge px-3 py-1 text-sm"
                disabled={busy !== null || selectedDocs.length === 0} onClick={onGenerate}>
                Générer l&apos;Atelier 1
              </button>
              {busy === "generate" && (
                <span className="flex items-center gap-2 text-sm text-fg-muted">
                  <Spinner /> {stage ?? "démarrage"}
                </span>
              )}
              <span className="ml-auto flex gap-1">
                <Pill tone="accent">Atelier 1</Pill>
                {ATELIERS_FUTURS.map((a) => (
                  <Pill key={a} tone="neutral">{a} — à venir</Pill>
                ))}
              </span>
            </div>
          </Panel>

          {error && <Banner tone="bad">{error}</Banner>}
          {warnings.length > 0 && (
            <Banner tone="warn">
              <p className="font-medium">Avertissements ({warnings.length})</p>
              <ul className="mt-1 list-disc pl-5 text-sm">
                {warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </Banner>
          )}

          {draft && (
            <>
              <Atelier1Tables a1={draft.atelier_1} onChange={setA1} disabled={busy !== null} />
              <Panel>
                <PanelTitle kicker="Cycle" title="Sauvegarde, validation, export"
                  hint="La validation fige la version ; l'export n'est permis qu'après validation et ne déclenche jamais de génération." />
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <span className="text-sm text-fg-muted">
                    Version {latest?.version ?? 0} ({latest?.origin ?? "aucune"})
                    {dirty && <Pill tone="warn">modifications non sauvegardées</Pill>}
                    {validated && <Pill tone="good">validé</Pill>}
                  </span>
                  <button className="rounded border border-edge px-3 py-1 text-sm"
                    disabled={busy !== null || !dirty} onClick={onSave}>
                    Sauvegarder le brouillon
                  </button>
                  <button className="rounded border border-edge px-3 py-1 text-sm"
                    disabled={busy !== null || dirty || !latest || validated}
                    onClick={onValidate}>
                    Valider l&apos;Atelier 1
                  </button>
                  <span className="ml-auto flex items-center gap-2">
                    <select className="rounded border border-edge bg-transparent px-2 py-1 text-sm"
                      value={exportFmt} onChange={(e) => setExportFmt(e.target.value)}>
                      <option value="xlsx">XLSX</option>
                      <option value="docx" disabled>DOCX — indisponible (template non validé)</option>
                      <option value="pdf" disabled>PDF — indisponible (template non validé)</option>
                    </select>
                    <button className="rounded border border-edge px-3 py-1 text-sm"
                      disabled={busy !== null || !validated} onClick={onExport}>
                      Exporter
                    </button>
                  </span>
                </div>
                {!validated && (
                  <p className="mt-2 text-xs text-fg-muted">
                    L&apos;export est activé après validation explicite de l&apos;ingénieur.
                  </p>
                )}
              </Panel>
            </>
          )}
        </>
      )}
    </div>
  );
}

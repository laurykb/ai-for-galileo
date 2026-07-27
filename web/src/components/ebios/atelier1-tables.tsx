// web/src/components/ebios/atelier1-tables.tsx
"use client";

import { Panel, PanelTitle, Pill } from "@/components/ui";
import {
  Atelier1, ETATS_APPLICATION, GRAVITE_LABELS, Preuve,
} from "@/lib/ebios";

const cell = "w-full bg-transparent border border-edge rounded px-2 py-1 text-sm";
const subLabel = "mt-4 text-xs font-medium uppercase tracking-wide text-fg-faint";

function Preuves({ preuves }: { preuves: Preuve[] }) {
  if (!preuves.length) return <Pill tone="warn">sans preuve</Pill>;
  return (
    <div className="mt-1 space-y-0.5">
      {preuves.map((p, i) => (
        <p key={i} className="text-xs text-fg-muted">
          {p.source}{p.page != null ? ` p.${p.page}` : " (page non localisée)"} — « {p.extrait} »
        </p>
      ))}
    </div>
  );
}

type Props = { a1: Atelier1; onChange: (next: Atelier1) => void; disabled: boolean };

export function Atelier1Tables({ a1, onChange, disabled }: Props) {
  const patch = (partial: Partial<Atelier1>) => onChange({ ...a1, ...partial });
  const vmName = (id: string) =>
    a1.valeurs_metier.find((v) => v.id === id)?.denomination ?? id;

  return (
    <div className="space-y-6">
      <Panel>
        <PanelTitle kicker="Atelier 1" title="Missions et valeurs métier"
          hint="Mission, valeurs métier (5 max) et biens supports (11 max) du classeur contractuel." />
        <label className="mt-2 block text-xs text-fg-muted">Mission</label>
        <textarea className={cell} rows={2} value={a1.mission} disabled={disabled}
          onChange={(e) => patch({ mission: e.target.value })} />
        <div className="mt-3 space-y-3">
          {a1.valeurs_metier.map((vm, i) => (
            <div key={vm.id} className="rounded border border-edge p-2">
              <div className="grid gap-2 md:grid-cols-4">
                <input className={cell} value={vm.denomination} disabled={disabled}
                  placeholder="Dénomination"
                  onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                    (v, j) => j === i ? { ...v, denomination: e.target.value } : v) })} />
                <select className={cell} value={vm.nature} disabled={disabled}
                  onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                    (v, j) => j === i
                      ? { ...v, nature: e.target.value as "processus" | "information" } : v) })}>
                  <option value="processus">processus</option>
                  <option value="information">information</option>
                </select>
                <input className={cell} value={vm.description} disabled={disabled}
                  placeholder="Description"
                  onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                    (v, j) => j === i ? { ...v, description: e.target.value } : v) })} />
                <div className="flex gap-1">
                  <input className={cell} value={vm.responsable.nom} disabled={disabled}
                    placeholder="Responsable"
                    onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                      (v, j) => j === i
                        ? { ...v, responsable: { ...v.responsable, nom: e.target.value } } : v) })} />
                  <select className={cell} value={vm.responsable.type} disabled={disabled}
                    onChange={(e) => patch({ valeurs_metier: a1.valeurs_metier.map(
                      (v, j) => j === i
                        ? { ...v, responsable: { ...v.responsable,
                            type: e.target.value as "INTERNE" | "EXTERNE" } } : v) })}>
                    <option value="INTERNE">INTERNE</option>
                    <option value="EXTERNE">EXTERNE</option>
                  </select>
                </div>
              </div>
              <Preuves preuves={vm.preuves} />
            </div>
          ))}
        </div>
        <p className={subLabel}>Biens supports associés</p>
        <div className="mt-1 space-y-3">
          {a1.biens_supports.map((bs, i) => (
            <div key={bs.id} className="rounded border border-edge p-2">
              <div className="grid gap-2 md:grid-cols-4">
                <input className={cell} value={bs.denomination} disabled={disabled}
                  placeholder="Dénomination"
                  onChange={(e) => patch({ biens_supports: a1.biens_supports.map(
                    (b, j) => j === i ? { ...b, denomination: e.target.value } : b) })} />
                <input className={cell} value={bs.description} disabled={disabled}
                  placeholder="Description"
                  onChange={(e) => patch({ biens_supports: a1.biens_supports.map(
                    (b, j) => j === i ? { ...b, description: e.target.value } : b) })} />
                <div className="flex gap-1">
                  <input className={cell} value={bs.responsable.nom} disabled={disabled}
                    placeholder="Responsable"
                    onChange={(e) => patch({ biens_supports: a1.biens_supports.map(
                      (b, j) => j === i
                        ? { ...b, responsable: { ...b.responsable, nom: e.target.value } } : b) })} />
                  <select className={cell} value={bs.responsable.type} disabled={disabled}
                    onChange={(e) => patch({ biens_supports: a1.biens_supports.map(
                      (b, j) => j === i
                        ? { ...b, responsable: { ...b.responsable,
                            type: e.target.value as "INTERNE" | "EXTERNE" } } : b) })}>
                    <option value="INTERNE">INTERNE</option>
                    <option value="EXTERNE">EXTERNE</option>
                  </select>
                </div>
                <p className="self-center text-xs text-fg-muted">
                  VM : {bs.valeurs_metier_associees.map(vmName).join(", ") || "aucune"}
                </p>
              </div>
              <Preuves preuves={bs.preuves} />
            </div>
          ))}
        </div>
      </Panel>

      <Panel>
        <PanelTitle kicker="Atelier 1" title="Événements redoutés"
          hint="7 lignes maximum ; gravité sur l'échelle 1 Mineure → 4 Critique." />
        <div className="mt-2 space-y-3">
          {a1.evenements_redoutes.map((er, i) => (
            <div key={er.id} className="rounded border border-edge p-2">
              <div className="grid gap-2 md:grid-cols-4">
                <select className={cell} value={er.valeur_metier_id} disabled={disabled}
                  onChange={(e) => patch({ evenements_redoutes: a1.evenements_redoutes.map(
                    (x, j) => j === i ? { ...x, valeur_metier_id: e.target.value } : x) })}>
                  <option value="">— valeur métier —</option>
                  {a1.valeurs_metier.map((vm) => (
                    <option key={vm.id} value={vm.id}>{vm.denomination}</option>
                  ))}
                </select>
                <input className={cell} value={er.evenement} disabled={disabled}
                  placeholder="Événement redouté"
                  onChange={(e) => patch({ evenements_redoutes: a1.evenements_redoutes.map(
                    (x, j) => j === i ? { ...x, evenement: e.target.value } : x) })} />
                <input className={cell} value={er.impact} disabled={disabled}
                  placeholder="Impact"
                  onChange={(e) => patch({ evenements_redoutes: a1.evenements_redoutes.map(
                    (x, j) => j === i ? { ...x, impact: e.target.value } : x) })} />
                <select className={cell} value={er.gravite} disabled={disabled}
                  onChange={(e) => patch({ evenements_redoutes: a1.evenements_redoutes.map(
                    (x, j) => j === i ? { ...x, gravite: Number(e.target.value) } : x) })}>
                  {[1, 2, 3, 4].map((g) => (
                    <option key={g} value={g}>{g} — {GRAVITE_LABELS[g]}</option>
                  ))}
                </select>
              </div>
              <Preuves preuves={er.preuves} />
            </div>
          ))}
        </div>
      </Panel>

      <Panel>
        <PanelTitle kicker="Atelier 1" title="Socle de sécurité (GAP)"
          hint="3 référentiels maximum ; écarts par bloc : 2, 2 puis 1 (structure du classeur)." />
        <div className="mt-2 space-y-3">
          {a1.socle_securite.map((gap, i) => (
            <div key={gap.id} className="rounded border border-edge p-2">
              <div className="grid gap-2 md:grid-cols-3">
                <input className={cell} value={gap.type_referentiel} disabled={disabled}
                  placeholder="Type de référentiel"
                  onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                    (g, j) => j === i ? { ...g, type_referentiel: e.target.value } : g) })} />
                <input className={cell} value={gap.nom_referentiel} disabled={disabled}
                  placeholder="Nom du référentiel"
                  onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                    (g, j) => j === i ? { ...g, nom_referentiel: e.target.value } : g) })} />
                <select className={cell} value={gap.etat_application} disabled={disabled}
                  onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                    (g, j) => j === i ? { ...g, etat_application: e.target.value } : g) })}>
                  {ETATS_APPLICATION.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              {gap.ecarts.map((ec, k) => (
                <div key={k} className="mt-2 grid gap-2 md:grid-cols-2">
                  <input className={cell} value={ec.description} disabled={disabled}
                    placeholder="Écart"
                    onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                      (g, j) => j === i ? { ...g, ecarts: g.ecarts.map(
                        (x, m) => m === k ? { ...x, description: e.target.value } : x) } : g) })} />
                  <input className={cell} value={ec.justification} disabled={disabled}
                    placeholder="Justification de l'écart"
                    onChange={(e) => patch({ socle_securite: a1.socle_securite.map(
                      (g, j) => j === i ? { ...g, ecarts: g.ecarts.map(
                        (x, m) => m === k ? { ...x, justification: e.target.value } : x) } : g) })} />
                  <Preuves preuves={ec.preuves} />
                </div>
              ))}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

"use client";

import dynamic from "next/dynamic";
import { Panel, PanelTitle, Spinner } from "@/components/ui";

const SraView = dynamic(
  () => import("@/components/sra/sra-view").then((module) => module.SraView),
  { ssr: false, loading: () => <div className="flex items-center gap-2 text-sm text-fg-muted"><Spinner /> Chargement de SRA…</div> },
);

export default function SraPage() {
  return (
    <main className="mx-auto w-full max-w-7xl space-y-6 px-4 py-8">
      <Panel>
        <PanelTitle kicker="Analyse de risques" title="Analyse SRA"
          hint="Construire les types de sources de risque, puis les croiser avec les cibles décrites par le corpus." />
      </Panel>
      <SraView />
    </main>
  );
}

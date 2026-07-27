import { EbiosView } from "@/components/ebios/ebios-view";
import { PanelTitle } from "@/components/ui";

export default function EbiosPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-6xl">
        <PanelTitle kicker="Analyse de risques" title="EBIOS RM"
          hint="Atelier 1 : cadrage et socle de sécurité, généré depuis vos documents puis validé par vos soins." />
        <EbiosView />
      </div>
    </div>
  );
}

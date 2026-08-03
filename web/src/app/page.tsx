import Link from "next/link";

import { Intro } from "@/components/intro";

export default function HomePage() {
  return (
    <div className="home-root relative min-h-[calc(100vh-9rem)] py-8">
      <Intro />

      <section className="mx-auto flex max-w-4xl flex-col items-center pt-4 text-center">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img id="home-thales" src="/thales.svg" alt="Thales" className="h-9 w-auto" />
        <p className="mt-12 text-xs font-medium uppercase tracking-[0.2em] text-accent-bright">Analyse documentaire souveraine</p>
        <h1 className="mt-4 max-w-3xl text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">Du corpus documentaire à l’analyse de risque</h1>
        <p className="mt-5 max-w-2xl text-base leading-7 text-fg-muted">AI for SSH réunit un Chat documentaire sourcé et une méthode SRA structurée, entièrement exécutés sur cette machine.</p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link href="/rag" className="rounded-lg bg-accent px-5 py-3 text-sm font-semibold text-background transition-colors hover:bg-accent-bright">Accéder au Chat</Link>
        </div>
      </section>

      <section className="mx-auto mt-20 grid max-w-5xl gap-4 md:grid-cols-3" aria-label="Espaces de travail">
        <article className="rounded-xl border border-edge bg-surface p-5"><p className="text-xs font-medium uppercase tracking-wider text-accent-bright">Chat documentaire</p><h2 className="mt-2 text-lg font-semibold">Interroger et comparer</h2><p className="mt-2 text-sm leading-6 text-fg-muted">Réponses ancrées dans vos sources, comparaisons multi-documents et exploration des résultats produits.</p></article>
        <article className="rounded-xl border border-accent/30 bg-surface p-5"><p className="text-xs font-medium uppercase tracking-wider text-accent-bright">Méthode SRA</p><h2 className="mt-2 text-lg font-semibold">Structurer l’analyse</h2><p className="mt-2 text-sm leading-6 text-fg-muted">Génération, revue et export des RO Types et de leurs relations avec les Target Objectives.</p></article>
        <article className="rounded-xl border border-edge bg-surface p-5"><p className="text-xs font-medium uppercase tracking-wider text-accent-bright">Souveraineté</p><h2 className="mt-2 text-lg font-semibold">Rester en local</h2><p className="mt-2 text-sm leading-6 text-fg-muted">Documents, modèles, index et résultats restent sur la machine de l’analyste.</p></article>
      </section>

    </div>
  );
}

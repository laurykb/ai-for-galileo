"use client";

import { useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";

import { Intro } from "@/components/intro";

/** Accueil : intro Thales (passée d'un clic), puis entrée directe dans
 * l'outil RAG — la v3 n'embarque que lui. Le perchoir #home-thales reste
 * la cible du docking de l'intro. */
export default function HomePage() {
  const router = useRouter();
  useEffect(() => {
    router.prefetch("/rag");
  }, [router]);
  const enter = useCallback(() => router.replace("/rag"), [router]);

  return (
    <div className="home-root relative flex min-h-[calc(100vh-9rem)] flex-col items-center justify-center py-8">
      <Intro onDone={enter} />
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        id="home-thales"
        src="/thales.svg"
        alt="Thales"
        className="absolute left-1/2 top-12 h-9 w-auto -translate-x-1/2"
      />
    </div>
  );
}

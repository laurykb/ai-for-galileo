"use client";

/** Navigation de l'Outil RAG — seule application de cette version.
 * Observabilité : réservée au mode Expert. Le logo (dans le layout) ramène
 * à l'accueil, qui rejoue l'intro puis retourne au chat. */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ExpertToggle, useExpert } from "@/components/expert-toggle";

// Le chat et ses vues d'opérateur (mêmes onglets que le Streamlit d'origine).
const RAG_LINKS = [
  { href: "/rag", label: "Chat" },
  { href: "/documents", label: "Documents" },
  { href: "/informations", label: "Informations" },
];
const RAG_EXPERT = [{ href: "/observability", label: "Observabilité" }];
const RAG_TAIL = [{ href: "/settings", label: "Paramètres" }];

export function HeaderNav() {
  const pathname = usePathname();
  const expert = useExpert();
  if (pathname === "/") return null; // accueil : l'intro seule

  const links = [...RAG_LINKS, ...(expert ? RAG_EXPERT : []), ...RAG_TAIL];

  return (
    <nav className="flex items-center gap-5 text-sm text-fg-muted">
      {links.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          aria-current={pathname.startsWith(l.href) ? "page" : undefined}
          className={`transition-colors hover:text-foreground ${
            pathname.startsWith(l.href) ? "text-accent-bright" : ""
          }`}
        >
          {l.label}
        </Link>
      ))}
      <ExpertToggle />
    </nav>
  );
}

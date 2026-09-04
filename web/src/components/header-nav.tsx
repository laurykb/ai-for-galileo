"use client";

/** Navigation par espaces métier. */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ExpertToggle, useExpert } from "@/components/expert-toggle";

const PRIMARY_LINKS = [
  { href: "/rag", label: "Chat" },
  { href: "/sra", label: "Méthode SRA" },
  { href: "/documents", label: "Documents" },
];
const EXPERT_LINKS = [
  { href: "/agents", label: "Prompts métier" },
  { href: "/observability", label: "Suivi technique" },
];
const CHAT_LINKS = [
  { href: "/informations", label: "Aide" },
  { href: "/settings", label: "Réglages" },
];
const CHAT_PATHS = ["/rag", "/agents", "/informations", "/settings", "/observability"];

function NavLink({ href, label, pathname }: { href: string; label: string; pathname: string }) {
  const active = pathname.startsWith(href);
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`transition-colors hover:text-foreground ${active ? "text-accent-bright" : ""}`}
    >
      {label}
    </Link>
  );
}

export function HeaderNav() {
  const pathname = usePathname();
  const expert = useExpert();
  if (pathname === "/") return null;

  const isChatWorkspace = CHAT_PATHS.some((path) => pathname.startsWith(path));
  const isSraWorkspace = pathname.startsWith("/sra");
  const chatLinks = [...CHAT_LINKS, ...(expert ? EXPERT_LINKS : [])];

  return (
    <nav className="flex items-center gap-5 text-sm text-fg-muted">
      {PRIMARY_LINKS.map((link) => (
        <NavLink key={link.href} {...link} pathname={pathname} />
      ))}
      {isChatWorkspace && (
        <>
          {chatLinks.map((link) => (
            <NavLink key={link.href} {...link} pathname={pathname} />
          ))}
        </>
      )}
      {(isChatWorkspace || isSraWorkspace) && <ExpertToggle />}
    </nav>
  );
}

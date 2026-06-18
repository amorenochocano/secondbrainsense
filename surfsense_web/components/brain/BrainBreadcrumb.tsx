/**
 * @file BrainBreadcrumb.tsx
 * @module components/brain
 *
 * Breadcrumb contextual para las vistas Brain.
 * Se monta en brain/layout.tsx y muestra la ruta activa dentro de
 * SecondBrainSense: "SecondBrainSense > {sección actual}".
 *
 * Usa usePathname() para determinar la sección activa sin necesidad
 * de props adicionales — el searchSpaceId se usa para generar los hrefs.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Brain } from "lucide-react";
import { cn } from "@/lib/utils";
import { brainLogger } from "@/lib/brain/logger";
import { BRAIN_ROUTES } from "@/lib/brain/constants";

const log = brainLogger("BrainBreadcrumb");

interface BrainBreadcrumbProps {
  searchSpaceId: string;
  className?: string;
}

/**
 * Mapa de segmento de ruta → etiqueta legible.
 * Centralizado aquí para no tener strings hardcodeados en el render.
 */
const BRAIN_SECTION_LABELS: Record<string, string> = {
  home:       "Home",
  chat:       "Chat",
  wiki:       "Wiki",
  graph:      "Grafo",
  ingest:     "Ingestar",
  metrics:    "Métricas",
  admin:      "Admin",
  vocabulary: "Maestros",
};

/**
 * Extrae el segmento de sección Brain de la pathname actual.
 * Ejemplo: "/dashboard/1/brain/wiki/my-doc" → "wiki"
 */
function extractBrainSection(pathname: string): string | null {
  const match = pathname.match(/\/brain\/([^/]+)/);
  return match?.[1] ?? null;
}

/**
 * Breadcrumb visual: icono Brain > "SecondBrainSense" > sección activa.
 * La sección activa se muestra sin enlace (es la página actual).
 */
export function BrainBreadcrumb({ searchSpaceId, className }: BrainBreadcrumbProps) {
  const pathname = usePathname();
  const section = extractBrainSection(pathname ?? "");
  const sectionLabel = section ? (BRAIN_SECTION_LABELS[section] ?? section) : null;

  log.debug("BrainBreadcrumb render", { section, pathname: pathname?.slice(0, 60) });

  return (
    <nav
      className={cn(
        "flex items-center gap-1.5 px-4 py-2 text-sm text-muted-foreground border-b border-border",
        className,
      )}
      aria-label="Breadcrumb SecondBrainSense"
    >
      <Brain className="h-3.5 w-3.5 shrink-0 text-violet-400" aria-hidden />

      <Link
        href={BRAIN_ROUTES.HOME(searchSpaceId)}
        className="hover:text-foreground transition-colors"
      >
        SecondBrainSense
      </Link>

      {sectionLabel && (
        <>
          <span className="select-none" aria-hidden>/</span>
          <span className="text-foreground font-medium">{sectionLabel}</span>
        </>
      )}
    </nav>
  );
}

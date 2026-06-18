/**
 * @file DomainBadge.tsx
 * @module components/brain
 *
 * Badge visual que muestra el dominio de conocimiento de un documento Brain.
 * Los dominios y su configuración visual están centralizados en
 * BRAIN_DOMAIN_CONFIG de lib/brain/constants.ts.
 *
 * Dominios disponibles: engineering | data | business | functional | legal | other
 */

"use client";

import { cn } from "@/lib/utils";
import { BRAIN_DOMAIN_CONFIG, type BrainDomain } from "@/lib/brain/constants";

interface DomainBadgeProps {
  /** Dominio del documento tal como lo devuelve el backend */
  domain: BrainDomain | string | null | undefined;
  className?: string;
}

/**
 * Normaliza el valor del dominio a uno válido del enum, o "other" si es desconocido.
 */
function normalizeDomain(domain: string | null | undefined): BrainDomain {
  if (!domain) return "other";
  return (domain in BRAIN_DOMAIN_CONFIG)
    ? (domain as BrainDomain)
    : "other";
}

/**
 * Renderiza un badge con el nombre del dominio y el color correspondiente.
 */
export function DomainBadge({ domain, className }: DomainBadgeProps) {
  const key = normalizeDomain(domain);
  const cfg = BRAIN_DOMAIN_CONFIG[key];

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        cfg.classes,
        className,
      )}
      aria-label={`Dominio: ${cfg.label}`}
    >
      {cfg.label}
    </span>
  );
}

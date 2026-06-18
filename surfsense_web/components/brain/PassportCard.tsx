"use client";

/**
 * @file PassportCard.tsx
 * @module components/brain
 *
 * Tarjeta visual para un pasaporte Brain en la Wiki semántica.
 *
 * Diseño:
 * - Borde izquierdo coloreado según el dominio del documento
 * - Importancia como indicador visual ●●●○○ (1-5 puntos)
 * - Confianza con color semáforo (verde >70%, amarillo >40%, rojo)
 * - Tags como pills #tag con overflow inteligente (+N más)
 * - Scopes como pills con colores (brain/knowledge/code)
 * - Indicador PII sutil (punto naranja + etiqueta)
 * - Acciones vía menú 3 puntos: ver, editar, re-sintetizar, eliminar
 */

import { BookOpen, Edit3, MoreHorizontal, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  BRAIN_CONNECTOR_DEFAULT_ICON,
  BRAIN_CONNECTOR_ICONS,
  BRAIN_DOMAIN_CONFIG,
  BRAIN_IMPORTANCE_MAX,
  BRAIN_ROUTES,
  BRAIN_SCOPE_COLORS,
  BRAIN_SCORE_HIGH_THRESHOLD,
  BRAIN_SCORE_MEDIUM_THRESHOLD,
  BRAIN_WIKI_MAX_VISIBLE_TAGS,
} from "@/lib/brain/constants";
import type { BrainDomain, BrainScope, PassportMetadata } from "@/contracts/types/brain.types";
import { cn } from "@/lib/utils";

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Devuelve clases Tailwind para el color de confianza */
function confidenceColorClass(confidence: number | null | undefined): string {
  if (confidence == null) return "text-muted-foreground";
  if (confidence > BRAIN_SCORE_HIGH_THRESHOLD) return "text-emerald-400";
  if (confidence > BRAIN_SCORE_MEDIUM_THRESHOLD) return "text-yellow-400";
  return "text-red-400";
}

/** Formatea una fecha ISO como texto relativo */
function formatRelative(dateStr: string | null): string {
  if (!dateStr) return "—";
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60_000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const rtf = new Intl.RelativeTimeFormat("es-ES", { numeric: "auto" });
  if (days > 0) return rtf.format(-days, "day");
  if (hours > 0) return rtf.format(-hours, "hour");
  return rtf.format(-minutes, "minute");
}

/** Devuelve el color CSS para el borde-izquierda según dominio */
function domainBorderColor(domain: string | null | undefined): string {
  if (!domain) return "border-border";
  const cfg = BRAIN_DOMAIN_CONFIG[domain as BrainDomain];
  if (!cfg) return "border-border";
  // Mapeo de domain a clase Tailwind border-left
  const borderMap: Record<string, string> = {
    engineering: "border-violet-500",
    data:        "border-orange-500",
    business:    "border-sky-500",
    functional:  "border-green-500",
    legal:       "border-red-500",
    other:       "border-border",
  };
  return borderMap[domain] ?? "border-border";
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-componentes internos
// ─────────────────────────────────────────────────────────────────────────────

/** Puntos de importancia ●●●○○ */
function ImportanceDots({ value }: { value: number | null | undefined }) {
  const val = value ?? 0;
  return (
    <span className="flex items-center gap-0.5" aria-label={`Importancia ${val} de ${BRAIN_IMPORTANCE_MAX}`}>
      {Array.from({ length: BRAIN_IMPORTANCE_MAX }, (_, i) => (
        <span
          key={i}
          className={cn(
            "size-1.5 rounded-full",
            i < val ? "bg-violet-400" : "bg-border",
          )}
        />
      ))}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────────

interface PassportCardProps {
  passport: PassportMetadata;
  /** search_space_id para construir las rutas de navegación */
  searchSpaceId: string;
  /** Callback al solicitar eliminación — el padre gestiona el AlertDialog */
  onDelete?: (source: string) => void;
  /** Callback al solicitar re-síntesis — el padre gestiona la mutación */
  onResynthesize?: (source: string) => void;
}

// ─────────────────────────────────────────────────────────────────────────────
// Componente principal
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Tarjeta de pasaporte para la Wiki semántica.
 * Muestra toda la metadata relevante con jerarquía visual clara.
 */
export function PassportCard({
  passport,
  searchSpaceId,
  onDelete,
  onResynthesize,
}: PassportCardProps) {
  const {
    source,
    title,
    doc_type,
    domain,
    importance,
    confidence,
    tags,
    scopes,
    connector_type,
    has_pii,
    updated_at,
  } = passport;

  const detailHref = BRAIN_ROUTES.WIKI_ITEM(searchSpaceId, source);
  const visibleTags = tags.slice(0, BRAIN_WIKI_MAX_VISIBLE_TAGS);
  const hiddenTagCount = Math.max(0, tags.length - BRAIN_WIKI_MAX_VISIBLE_TAGS);
  const connectorIcon =
    BRAIN_CONNECTOR_ICONS[connector_type?.toLowerCase() ?? ""] ?? BRAIN_CONNECTOR_DEFAULT_ICON;

  return (
    <div
      className={cn(
        "group relative flex flex-col rounded-xl border border-border bg-card",
        "border-l-[3px]", domainBorderColor(domain),
        "transition-all duration-200",
        "hover:-translate-y-0.5 hover:border-border/80 hover:shadow-lg hover:shadow-black/20",
      )}
    >
      {/* Cabecera: tipo doc + PII + menú */}
      <div className="flex items-start justify-between gap-2 px-4 pt-3.5">
        <div className="flex items-center gap-2 flex-wrap">
          {doc_type && (
            <span className="inline-flex items-center rounded-md bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground ring-1 ring-inset ring-border">
              {doc_type}
            </span>
          )}
          {has_pii && (
            <span
              className="inline-flex items-center gap-1 rounded-md bg-orange-500/10 px-1.5 py-0.5 text-[11px] font-medium text-orange-400 ring-1 ring-inset ring-orange-500/20"
              title="Contiene información de identificación personal"
            >
              <span className="size-1.5 rounded-full bg-orange-400" />
              PII
            </span>
          )}
        </div>

        {/* Menú de acciones — visible con hover o focus */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="rounded-md p-1 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none"
              aria-label="Acciones del pasaporte"
            >
              <MoreHorizontal className="size-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem asChild>
              <Link href={detailHref} className="flex items-center gap-2">
                <BookOpen className="size-3.5" />
                Ver pasaporte
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href={`${detailHref}?edit=1`} className="flex items-center gap-2">
                <Edit3 className="size-3.5" />
                Editar
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={() => onResynthesize?.(source)}
              className="flex items-center gap-2 text-blue-400 focus:text-blue-300"
            >
              <RefreshCw className="size-3.5" />
              Re-sintetizar
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={() => onDelete?.(source)}
              className="flex items-center gap-2 text-red-400 focus:text-red-300"
            >
              <Trash2 className="size-3.5" />
              Eliminar
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Título — clickable */}
      <Link href={detailHref} className="px-4 pt-1.5 pb-0 block">
        <h3 className="line-clamp-2 text-sm font-semibold leading-snug text-foreground hover:text-violet-300 transition-colors">
          {title}
        </h3>
      </Link>

      {/* Tags */}
      {tags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 px-4 pt-2.5">
          {visibleTags.map((tag) => (
            <span
              key={tag}
              className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
            >
              #{tag}
            </span>
          ))}
          {hiddenTagCount > 0 && (
            <span className="text-[11px] text-muted-foreground/50">
              +{hiddenTagCount}
            </span>
          )}
        </div>
      )}

      {/* Scopes + origen conector */}
      <div className="flex flex-wrap items-center gap-1.5 px-4 pt-2">
        {scopes.map((scope) => (
          <span
            key={scope}
            className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
            style={{
              background: `color-mix(in srgb, ${BRAIN_SCOPE_COLORS[scope as BrainScope]} 15%, transparent)`,
              color: BRAIN_SCOPE_COLORS[scope as BrainScope],
              outline: `1px solid color-mix(in srgb, ${BRAIN_SCOPE_COLORS[scope as BrainScope]} 30%, transparent)`,
            }}
          >
            {scope}
          </span>
        ))}
        <span className="ml-auto text-xs text-muted-foreground/60">
          {connectorIcon} {connector_type ?? "local"}
        </span>
      </div>

      {/* Separador */}
      <div className="mx-4 mt-2.5 border-t border-border/40" />

      {/* Footer: importancia + confianza + fecha */}
      <div className="flex items-center justify-between gap-2 px-4 py-2.5">
        <ImportanceDots value={importance} />

        <div className="flex items-center gap-2.5">
          {confidence != null && (
            <span className={cn("text-xs font-medium tabular-nums", confidenceColorClass(confidence))}>
              {Math.round(confidence * 100)}%
            </span>
          )}
          <span className="text-[11px] text-muted-foreground/50">
            {formatRelative(updated_at)}
          </span>
        </div>
      </div>
    </div>
  );
}

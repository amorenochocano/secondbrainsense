/**
 * @file SourceCard.tsx
 * @module components/brain
 *
 * Tarjeta compacta que muestra una fuente citada en una respuesta Brain.
 * El score se coloriza en verde/amarillo/rojo según los umbrales de relevancia
 * definidos en BRAIN_SCORE_*_THRESHOLD de lib/brain/constants.ts.
 *
 * Fuente original: lógica de score_color() de sources.py (Second Brain).
 */

"use client";

import { cn } from "@/lib/utils";
import {
  BRAIN_SCORE_CLASSES,
  BRAIN_SCORE_HIGH_THRESHOLD,
  BRAIN_SCORE_MEDIUM_THRESHOLD,
  type ScoreVariant,
} from "@/lib/brain/constants";
import type { BrainSource } from "@/contracts/types/brain.types";

interface SourceCardProps {
  source: BrainSource;
  className?: string;
}

/**
 * Devuelve la variante visual según el umbral de score.
 * Los umbrales se leen de las constantes — sin valores hardcodeados.
 */
function resolveScoreVariant(score: number): ScoreVariant {
  if (score > BRAIN_SCORE_HIGH_THRESHOLD)   return "high";
  if (score >= BRAIN_SCORE_MEDIUM_THRESHOLD) return "medium";
  return "low";
}

/**
 * Renderiza una fuente citada con badge de score colorizado,
 * nombre de documento (enlazable si hay URL) y número de página opcional.
 */
export function SourceCard({ source, className }: SourceCardProps) {
  const variant = resolveScoreVariant(source.score);
  const scoreLabel = source.score.toFixed(3);

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border px-2 py-1 text-xs",
        className,
      )}
      title={`Fuente: ${source.source} — relevancia ${scoreLabel}`}
    >
      {/* Badge de score */}
      <span
        className={cn(
          "rounded-full border px-1.5 py-0.5 font-mono shrink-0",
          BRAIN_SCORE_CLASSES[variant],
        )}
        aria-label={`Score de relevancia: ${scoreLabel}`}
      >
        {scoreLabel}
      </span>

      {/* Nombre del documento (enlazable si tiene URL) */}
      {source.source_url ? (
        <a
          href={source.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="hover:underline truncate max-w-[200px] text-foreground"
        >
          {source.source}
        </a>
      ) : (
        <span className="truncate max-w-[200px]">{source.source}</span>
      )}

      {/* Página opcional */}
      {source.page !== undefined && source.page !== null && (
        <span className="text-muted-foreground shrink-0">p.{source.page}</span>
      )}
    </div>
  );
}

/**
 * @file LevelBadge.tsx
 * @module components/brain
 *
 * Badge visual que indica el nivel de retrieval usado en una respuesta Brain.
 * Se usa en BrainChat y, opcionalmente, en el chat principal de SurfSense.
 *
 * Niveles:
 *   1 → Brain (pasaportes L1)
 *   2 → Knowledge (semántico L2)
 *   3 → BM25 (léxico L2.b)
 *   4 → Web (SearXNG L2.c)
 *   0 → LLM libre (sin retrieval)
 */

"use client";

import { cn } from "@/lib/utils";
import { BRAIN_LEVEL_CONFIG, type BrainLevel } from "@/lib/brain/constants";

interface LevelBadgeProps {
  /** Nivel numérico devuelto por el backend (0-4) */
  level: BrainLevel;
  /** Tier del modelo usado (p.ej. "fast", "balanced", "quality") */
  modelTier?: string | null;
  className?: string;
}

/**
 * Renderiza un badge con el nivel de retrieval coloreado.
 * La configuración visual (label + clases) se lee de BRAIN_LEVEL_CONFIG
 * en lib/brain/constants.ts — sin valores hardcodeados aquí.
 */
export function LevelBadge({ level, modelTier, className }: LevelBadgeProps) {
  const cfg = BRAIN_LEVEL_CONFIG[level];

  if (!cfg) return null;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
        cfg.classes,
        className,
      )}
      title={`Nivel de retrieval: ${cfg.label}`}
      aria-label={`Respuesta obtenida de: ${cfg.label}`}
    >
      {cfg.label}
      {modelTier && (
        <span className="opacity-60">· {modelTier}</span>
      )}
    </span>
  );
}

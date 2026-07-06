"use client";

/**
 * @file IngestPhaseLog.tsx
 * @module components/brain
 *
 * Componente reutilizable que visualiza el progreso en tiempo real de una
 * ingesta Brain mediante un stream SSE de 4 fases.
 *
 * Responsabilidades:
 * - Abre y gestiona la conexión SSE al recibir un jobId válido.
 * - Representa el estado de cada fase con iconografía animada.
 * - Muestra detalles textuales y recuento de chunks por fase.
 * - Llama a onComplete/onError para coordinar con el padre.
 *
 * Gestión de logs: brainLogger("IngestPhaseLog")
 * ZERO HARDCODE: todos los textos e íconos en BRAIN_INGEST_PHASES
 */

import { useEffect, useRef, useState } from "react";
import { CheckCircle, AlertCircle, SkipForward, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { BRAIN_INGEST_PHASES, type IngestPhaseKey } from "@/lib/brain/constants";
import { brainLogger } from "@/lib/brain/logger";
import type { IngestPhaseEvent, IngestPhaseStatus } from "@/contracts/types/brain.types";

const log = brainLogger("IngestPhaseLog");

// ─── Tipos locales ────────────────────────────────────────────────────────────

interface PhaseState {
  status: IngestPhaseStatus;
  detail: string | null;
  chunks: number | null;
}

interface IngestPhaseLogProps {
  /** ID del job obtenido de brainApiService.ingestUrl(). null = inactivo */
  jobId: string | null;
  /** Callback cuando el pipeline completa con éxito */
  onComplete?: () => void;
  /** Callback cuando el pipeline reporta un error */
  onError?: (err: Error) => void;
}

// ─── Estado inicial de todas las fases ───────────────────────────────────────

const buildInitialPhaseStates = (): Record<IngestPhaseKey, PhaseState> =>
  Object.fromEntries(
    BRAIN_INGEST_PHASES.map((p) => [p.key, { status: "idle" as IngestPhaseStatus, detail: null, chunks: null }])
  ) as Record<IngestPhaseKey, PhaseState>;

// ─── Sub-componente: indicador visual de estado de fase ──────────────────────

interface PhaseStatusIconProps {
  status: IngestPhaseStatus;
}

function PhaseStatusIcon({ status }: PhaseStatusIconProps) {
  switch (status) {
    case "running":
      return <Loader2 className="h-5 w-5 animate-spin text-violet-400" />;
    case "ok":
      return <CheckCircle className="h-5 w-5 text-emerald-400" />;
    case "error":
      return <AlertCircle className="h-5 w-5 text-red-400" />;
    case "warn":
      return <AlertCircle className="h-5 w-5 text-amber-400" />;
    case "skip":
      return <SkipForward className="h-5 w-5 text-muted-foreground" />;
    default:
      // idle
      return (
        <span className="flex h-5 w-5 items-center justify-center rounded-full border-2 border-border bg-transparent" />
      );
  }
}

// ─── Sub-componente: barra de progreso de fase ────────────────────────────────

interface PhaseProgressBarProps {
  status: IngestPhaseStatus;
}

function PhaseProgressBar({ status }: PhaseProgressBarProps) {
  const widthClass =
    status === "idle"    ? "w-0"
    : status === "running" ? "w-1/2 animate-pulse"
    : status === "skip"    ? "w-full bg-slate-500"
    : status === "ok"      ? "w-full bg-emerald-500"
    : status === "warn"    ? "w-full bg-amber-500"
    : /* error */            "w-full bg-red-500";

  return (
    <div className="h-0.5 w-full rounded-full bg-border/50 overflow-hidden mt-1">
      <div
        className={cn(
          "h-full rounded-full transition-all duration-700",
          status === "running" ? "bg-violet-500" : "",
          widthClass,
        )}
      />
    </div>
  );
}

// ─── Componente principal ─────────────────────────────────────────────────────

export function IngestPhaseLog({ jobId, onComplete, onError }: IngestPhaseLogProps) {
  const [phases, setPhases] = useState<Record<IngestPhaseKey, PhaseState>>(buildInitialPhaseStates);
  const [done, setDone] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);

  // Reiniciar estado al recibir un nuevo jobId
  useEffect(() => {
    if (!jobId) {
      setPhases(buildInitialPhaseStates());
      setDone(false);
      return;
    }

    // Reiniciar estado para la nueva ingesta
    setPhases(buildInitialPhaseStates());
    setDone(false);

    log.info("Conectando stream SSE de ingesta", { jobId });

    const cleanup = brainApiService.streamIngestJob(
      jobId,
      // onPhase
      (event: IngestPhaseEvent) => {
        log.debug("Evento de fase recibido", event);
        setPhases((prev) => ({
          ...prev,
          [event.phase]: {
            status: event.status,
            detail: event.detail ?? null,
            chunks: event.chunks ?? null,
          },
        }));
      },
      // onDone
      () => {
        log.info("Pipeline de ingesta completado", { jobId });
        setDone(true);
        onComplete?.();
      },
      // onError
      (err: Error) => {
        log.error("Error en pipeline de ingesta", { jobId, err });
        onError?.(err);
      },
    );

    cleanupRef.current = cleanup;

    return () => {
      cleanup();
      cleanupRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  if (!jobId) return null;

  return (
    <div className="rounded-xl border border-border bg-card p-4 space-y-1">
      {/* Cabecera */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          Pipeline de ingesta
        </span>
        {done && (
          <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-medium">
            <CheckCircle className="h-3.5 w-3.5" />
            Completado
          </span>
        )}
      </div>

      {/* Lista de fases */}
      <ol className="space-y-3">
        {BRAIN_INGEST_PHASES.map((phase, idx) => {
          const state = phases[phase.key as IngestPhaseKey];
          const isActive = state.status === "running";
          const isDone = state.status === "ok" || state.status === "warn" || state.status === "skip";
          const isError = state.status === "error";

          return (
            <li
              key={phase.key}
              className={cn(
                "flex items-start gap-3 rounded-lg px-3 py-2.5 transition-all duration-300",
                isActive && "bg-violet-500/10 border border-violet-500/20",
                isDone && "bg-emerald-500/5",
                isError && "bg-red-500/10 border border-red-500/20",
              )}
            >
              {/* Índice y línea vertical */}
              <div className="flex flex-col items-center gap-1 pt-0.5">
                <PhaseStatusIcon status={state.status} />
                {idx < BRAIN_INGEST_PHASES.length - 1 && (
                  <span
                    className={cn(
                      "w-px flex-1 rounded-full transition-colors duration-500",
                      isDone || isError ? "bg-border" : "bg-border/50",
                    )}
                    style={{ minHeight: "12px" }}
                  />
                )}
              </div>

              {/* Contenido */}
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-semibold text-foreground">
                    {phase.icon} {phase.label}
                  </span>
                  {state.chunks !== null && state.chunks > 0 && (
                    <span className="text-xs text-violet-300 font-mono">
                      {state.chunks} chunks
                    </span>
                  )}
                </div>
                <p
                  className={cn(
                    "text-xs mt-0.5 transition-colors",
                    state.status === "idle"    && "text-muted-foreground",
                    state.status === "running" && "text-violet-300",
                    state.status === "ok"      && "text-emerald-400",
                    state.status === "warn"    && "text-amber-400",
                    state.status === "error"   && "text-red-400",
                    state.status === "skip"    && "text-muted-foreground",
                  )}
                >
                  {state.detail ?? phase.detail}
                </p>
                <PhaseProgressBar status={state.status} />
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

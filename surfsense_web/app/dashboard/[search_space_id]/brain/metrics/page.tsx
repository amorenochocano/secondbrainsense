"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/metrics
 *
 * F6.9 — Métricas Brain: visibilidad operacional del pipeline de conocimiento.
 *
 * Diseño mejorado sobre el panel Streamlit original:
 * - Cards de colecciones con donut visual y ratio brain/total
 * - Distribución de niveles L1/L2/BM25/Web/L0 con barras proporcionales
 * - Categorías A/B/C inferidas del corpus de pasaportes
 * - Top 10 fuentes por importancia
 *
 * Gestión de logs: brainLogger("BrainMetricsPage")
 * ZERO HARDCODE: todas las constantes en lib/brain/constants.ts
 */

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Brain, Database, Code2, TrendingUp, FileText, BarChart3, Layers, Loader2,
  Microscope, Link, FileUp,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { BrainBreadcrumb, DomainBadge, LevelBadge } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import { cacheKeys } from "@/lib/query-client/cache-keys";
import {
  BRAIN_INGEST_EXTENSION_CATEGORY,
  BRAIN_INGEST_CATEGORIES,
  BRAIN_LEVEL_CONFIG,
  BRAIN_LEVELS,
  type IngestCategory,
} from "@/lib/brain/constants";

const log = brainLogger("BrainMetricsPage");

// ─── Utilidades ───────────────────────────────────────────────────────────────

function extractExtension(source: string): string | null {
  const match = source.match(/\.([a-zA-Z0-9]{1,10})(?:[?#]|$)/);
  return match ? `.${match[1].toLowerCase()}` : null;
}

function getCategoryForExt(ext: string | null): IngestCategory {
  return ext ? (BRAIN_INGEST_EXTENSION_CATEGORY[ext] ?? "C") : "C";
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// ─── Sub-componente: colección Qdrant ────────────────────────────────────────

interface CollectionCardProps {
  name: string;
  icon: React.ReactNode;
  vectors: number;
  sources: number;
  dimension: number;
  accent: string;
  accentText: string;
  ratio?: number; // 0-1 para barra de progreso visual
}

function CollectionCard({ name, icon, vectors, sources, dimension, accent, accentText, ratio }: CollectionCardProps) {
  return (
    <div className={cn("rounded-xl border bg-slate-800/40 p-5 space-y-3", accent)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={accentText}>{icon}</span>
          <span className="text-sm font-semibold text-slate-200 font-mono">{name}</span>
        </div>
        {ratio !== undefined && (
          <span className="text-xs text-slate-400">
            {Math.round(ratio * 100)}% del total
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div>
          <p className="text-xs text-slate-500">Vectores</p>
          <p className={cn("text-xl font-bold tabular-nums", accentText)}>{formatNumber(vectors)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Fuentes</p>
          <p className="text-xl font-bold text-slate-200 tabular-nums">{formatNumber(sources)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Dimensión</p>
          <p className="text-xl font-bold text-slate-400 tabular-nums">{dimension}</p>
        </div>
      </div>

      {ratio !== undefined && (
        <div className="h-1.5 w-full rounded-full bg-slate-700/60 overflow-hidden">
          <div
            className={cn("h-full rounded-full transition-all duration-700", accentText.replace("text-", "bg-"))}
            style={{ width: `${Math.round(ratio * 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}

// ─── Sub-componente: barra de nivel ──────────────────────────────────────────

interface LevelBarProps {
  levelKey: string;
  count: number;
  maxCount: number;
}

function LevelBar({ levelKey, count, maxCount }: LevelBarProps) {
  const numericLevel = Number(levelKey);
  const isValidLevel = !Number.isNaN(numericLevel) && numericLevel in BRAIN_LEVEL_CONFIG;
  const pct = maxCount > 0 ? Math.round((count / maxCount) * 100) : 0;

  return (
    <div className="flex items-center gap-3">
      <div className="w-28 shrink-0">
        {isValidLevel ? (
          <LevelBadge level={numericLevel as keyof typeof BRAIN_LEVEL_CONFIG} />
        ) : (
          <span className="text-xs text-slate-400">{levelKey}</span>
        )}
      </div>
      <div className="flex-1 h-2 rounded-full bg-slate-700/60 overflow-hidden">
        <div
          className="h-full rounded-full bg-violet-500/70 transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-10 text-right text-xs text-slate-400 tabular-nums shrink-0">{count}</span>
    </div>
  );
}

// Ícono por tipo de pipeline
const CATEGORY_ICON_METRICS = {
  A: Microscope,
  B: Link,
  C: FileUp,
} as const satisfies Record<IngestCategory, React.ElementType>;

// ─── Página principal ─────────────────────────────────────────────────────────

export default function BrainMetricsPage() {
  const params = useParams<{ search_space_id: string }>();
  const spaceId = params.search_space_id;

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: cacheKeys.brain.stats(Number(spaceId)),
    queryFn: () => {
      log.debug("Cargando stats Brain", { spaceId });
      return brainApiService.getStats(Number(spaceId));
    },
    staleTime: 60_000,
  });

  const { data: levelUsage, isLoading: levelLoading } = useQuery({
    queryKey: cacheKeys.brain.levelUsage(Number(spaceId)),
    queryFn: () => {
      log.debug("Cargando uso de niveles Brain", { spaceId });
      return brainApiService.getLevelUsage(Number(spaceId));
    },
    staleTime: 60_000,
  });

  const { data: passports } = useQuery({
    queryKey: cacheKeys.brain.list(Number(spaceId)),
    queryFn: () => brainApiService.listPassports(Number(spaceId)),
    staleTime: 60_000,
  });

  // ── Derivados ──────────────────────────────────────────────────────────────

  const collections = stats?.collections ?? {};
  const brainCol   = collections.brain   ?? { vectors: 0, sources: 0, dimension: 768 };
  const knowledgeCol = collections.knowledge ?? { vectors: 0, sources: 0, dimension: 768 };
  const codeCol    = collections.code    ?? { vectors: 0, sources: 0, dimension: 768 };
  const totalVectors = (brainCol.vectors ?? 0) + (knowledgeCol.vectors ?? 0) + (codeCol.vectors ?? 0);

  // Distribución de categorías A/B/C desde corpus de pasaportes
  const categoryDist: Record<IngestCategory, number> = { A: 0, B: 0, C: 0 };
  for (const p of passports ?? []) {
    const ext = extractExtension(p.source);
    const cat = getCategoryForExt(ext);
    categoryDist[cat]++;
  }
  const totalDocs = (passports?.length ?? 0) || 1;

  // Top fuentes por importancia
  const topSources = [...(passports ?? [])]
    .sort((a, b) => (b.importance ?? 0) - (a.importance ?? 0))
    .slice(0, 10);

  // Distribución de niveles
  const usageEntries = Object.entries(levelUsage?.usage ?? {}).sort((a, b) => b[1] - a[1]);
  const maxUsage = Math.max(...usageEntries.map(([, v]) => v), 1);
  const totalUsage = usageEntries.reduce((s, [, v]) => s + v, 0);

  const isLoading = statsLoading || levelLoading;

  return (
    <div className="flex flex-col gap-6 p-6 max-w-5xl mx-auto w-full">
      {/* Breadcrumb */}
      <BrainBreadcrumb spaceId={spaceId} current="Métricas" />

      {/* Cabecera */}
      <div>
        <h1 className="text-xl font-bold text-slate-100 flex items-center gap-2">
          <BarChart3 className="h-5 w-5 text-violet-400" />
          Métricas Brain
        </h1>
        <p className="text-sm text-slate-400 mt-1">
          Estado operacional del pipeline de conocimiento · Qdrant + PostgreSQL
        </p>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-violet-400" />
        </div>
      )}

      {!isLoading && (
        <>
          {/* ── Sección 1: Colecciones Qdrant ───────────────────────────── */}
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3 flex items-center gap-2">
              <Database className="h-3.5 w-3.5" />
              Colecciones Qdrant
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <CollectionCard
                name="brain"
                icon={<Brain className="h-4 w-4" />}
                vectors={brainCol.vectors ?? 0}
                sources={brainCol.sources ?? 0}
                dimension={brainCol.dimension ?? 768}
                accent="border-violet-500/30"
                accentText="text-violet-400"
                ratio={totalVectors > 0 ? (brainCol.vectors ?? 0) / totalVectors : 0}
              />
              <CollectionCard
                name="knowledge"
                icon={<Database className="h-4 w-4" />}
                vectors={knowledgeCol.vectors ?? 0}
                sources={knowledgeCol.sources ?? 0}
                dimension={knowledgeCol.dimension ?? 768}
                accent="border-blue-500/30"
                accentText="text-blue-400"
                ratio={totalVectors > 0 ? (knowledgeCol.vectors ?? 0) / totalVectors : 0}
              />
              <CollectionCard
                name="code"
                icon={<Code2 className="h-4 w-4" />}
                vectors={codeCol.vectors ?? 0}
                sources={codeCol.sources ?? 0}
                dimension={codeCol.dimension ?? 768}
                accent="border-emerald-500/30"
                accentText="text-emerald-400"
                ratio={totalVectors > 0 ? (codeCol.vectors ?? 0) / totalVectors : 0}
              />
            </div>

            {/* Totales */}
            <div className="mt-3 flex items-center gap-6 px-1">
              <span className="text-xs text-slate-500">
                Total vectores: <span className="font-semibold text-slate-300">{formatNumber(totalVectors)}</span>
              </span>
              <span className="text-xs text-slate-500">
                Documentos indexados: <span className="font-semibold text-slate-300">{passports?.length ?? 0}</span>
              </span>
              {stats?.last_ingest_at && (
                <span className="text-xs text-slate-500">
                  Última ingesta: <span className="font-semibold text-slate-300">
                    {new Date(stats.last_ingest_at).toLocaleDateString("es-ES")}
                  </span>
                </span>
              )}
            </div>
          </section>

          {/* ── Sección 2: Distribución de niveles ──────────────────────── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-4 flex items-center gap-2">
                <TrendingUp className="h-3.5 w-3.5" />
                Uso de niveles (últimas {levelUsage?.period_hours ?? 24}h)
                {totalUsage > 0 && (
                  <span className="ml-auto text-xs text-slate-400 font-normal normal-case">
                    {totalUsage} consultas
                  </span>
                )}
              </h2>

              {usageEntries.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-6">Sin datos de uso todavía</p>
              ) : (
                <div className="space-y-3">
                  {usageEntries.map(([levelKey, count]) => (
                    <LevelBar key={levelKey} levelKey={levelKey} count={count} maxCount={maxUsage} />
                  ))}
                </div>
              )}
            </div>

            {/* ── Sección 3: Categorías A/B/C ─────────────────────────── */}
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-4 flex items-center gap-2">
                <Layers className="h-3.5 w-3.5" />
                Distribución por categoría del pipeline
              </h2>

              {passports?.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-6">Sin documentos todavía</p>
              ) : (
                <div className="space-y-3">
                  {(["A", "B", "C"] as IngestCategory[]).map((cat) => {
                    const count = categoryDist[cat];
                    const pct = Math.round((count / totalDocs) * 100);
                    const cfg = BRAIN_INGEST_CATEGORIES[cat];
                    return (
                      <div key={cat} className="space-y-1">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span
                              className={cn(
                                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold",
                                cfg.classes,
                              )}
                            >
                              {(() => { const Icon = CATEGORY_ICON_METRICS[cat]; return <Icon className="h-3 w-3 shrink-0" aria-hidden />; })()}
                              {cfg.label}
                            </span>
                            <span className="text-xs text-slate-400 hidden sm:inline">{cfg.desc}</span>
                          </div>
                          <span className="text-xs text-slate-400 tabular-nums">{count} docs</span>
                        </div>
                        <div className="h-1.5 w-full rounded-full bg-slate-700/60 overflow-hidden">
                          <div
                            className="h-full rounded-full bg-violet-500/50 transition-all duration-700"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* ── Sección 4: Top fuentes ──────────────────────────────────── */}
          {topSources.length > 0 && (
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-4 flex items-center gap-2">
                <FileText className="h-3.5 w-3.5" />
                Top fuentes por importancia
              </h2>
              <div className="space-y-2">
                {topSources.map((p, idx) => (
                  <div key={p.source} className="flex items-center gap-3 py-1.5">
                    <span className="w-5 text-xs text-slate-600 tabular-nums text-right shrink-0">
                      {idx + 1}
                    </span>
                    <DomainBadge domain={p.domain} size="sm" />
                    <span
                      className="flex-1 text-sm text-slate-300 truncate"
                      title={p.source}
                    >
                      {p.source.split("/").pop() ?? p.source}
                    </span>
                    {/* Importance dots */}
                    <span className="flex gap-0.5 shrink-0">
                      {Array.from({ length: 5 }, (_, i) => (
                        <span
                          key={i}
                          className={cn(
                            "text-[10px]",
                            i < (p.importance ?? 0) ? "text-violet-400" : "text-slate-700",
                          )}
                        >
                          ●
                        </span>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}


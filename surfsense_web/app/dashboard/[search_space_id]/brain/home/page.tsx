"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/home
 *
 * Dashboard Brain — pantalla de bienvenida y estado del sistema SecondBrainSense.
 *
 * Muestra en tiempo real:
 *  - Estado de salud de los servicios (Qdrant, Ollama, PostgreSQL) — primera sección
 *  - Tarjetas de colección Qdrant (brain / knowledge / code) con color por scope
 *  - Última ingesta: fecha + documento
 *  - Nivel de retrieval más usado en las últimas 24h
 *  - Accesos rápidos a las secciones principales (excluye Home)
 *
 * Fuentes de datos:
 *  - GET /api/v1/brain/stats  → BrainStatsResponse  (stale 60s, refetch on focus)
 *  - GET /api/v1/health       → HealthResponse       (stale 30s, refetch 30s, no retry)
 */

import { type ElementType, use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart2,
  BookMarked,
  BookOpen,
  Brain,
  CheckCircle2,
  CircleAlert,
  Clock,
  Code2,
  Library,
  Loader2,
  MessageCircle,
  Network,
  RefreshCw,
  Settings2,
  Upload,
  XCircle,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { LevelBadge } from "@/components/brain/LevelBadge";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import {
  BRAIN_LEVEL_CONFIG,
  BRAIN_ROUTES,
  BRAIN_SCOPES,
  type BrainLevel,
  type BrainScope,
} from "@/lib/brain/constants";
import { cacheKeys } from "@/lib/query-client/cache-keys";
import { cn } from "@/lib/utils";
import type {
  BrainStatsResponse,
  HealthResponse,
  ServiceHealthStatus,
} from "@/contracts/types/brain.types";

const log = brainLogger("BrainHomePage");

// ─────────────────────────────────────────────────────────────────────────────
// Configuración estática (sin hardcode en JSX)
// ─────────────────────────────────────────────────────────────────────────────

/** Icono, color de borde izquierdo y descripción por scope de colección Qdrant */
const COLLECTION_CONFIG: Record<
  BrainScope,
  { label: string; Icon: ElementType; borderClass: string; description: string }
> = {
  brain:     { label: "Brain",     Icon: Brain,   borderClass: "border-l-violet-500", description: "Pasaportes sintetizados" },
  knowledge: { label: "Knowledge", Icon: Library, borderClass: "border-l-blue-500",   description: "Chunks semánticos"       },
  code:      { label: "Code",      Icon: Code2,   borderClass: "border-l-emerald-500",description: "Fragmentos de código"    },
};

/** Mapeo directo de scope → campo en BrainStatsResponse para evitar ternarios */
const STATS_FIELD_MAP: Record<BrainScope, keyof Pick<BrainStatsResponse, "brain_count" | "knowledge_count" | "code_count">> = {
  brain:     "brain_count",
  knowledge: "knowledge_count",
  code:      "code_count",
};

/** Etiqueta legible por servicio de salud */
const HEALTH_SERVICE_LABELS: Partial<Record<keyof HealthResponse, string>> = {
  qdrant:     "Qdrant",
  ollama:     "Ollama",
  postgresql: "PostgreSQL",
  redis:      "Redis",
};

/** Icono y clases Tailwind por estado de salud */
const HEALTH_STATUS_CONFIG: Record<
  ServiceHealthStatus,
  { Icon: ElementType; iconClass: string; textClass: string; label: string }
> = {
  ok:       { Icon: CheckCircle2, iconClass: "text-emerald-500", textClass: "text-emerald-500", label: "OK"           },
  degraded: { Icon: CircleAlert,  iconClass: "text-yellow-500",  textClass: "text-yellow-600",  label: "Degradado"    },
  error:    { Icon: XCircle,      iconClass: "text-red-500",     textClass: "text-red-500",      label: "Error"        },
  unknown:  { Icon: CircleAlert,  iconClass: "text-muted-foreground", textClass: "text-muted-foreground", label: "Desconocido" },
};

/**
 * Accesos rápidos: excluye HOME (ya estamos aquí).
 * routeKey tipado contra BRAIN_ROUTES para detectar claves inválidas en compilación.
 */
const QUICK_ACCESS_ITEMS: readonly {
  label: string;
  Icon: ElementType;
  routeKey: Exclude<keyof typeof BRAIN_ROUTES, "HOME">;
}[] = [
  { label: "Chat",     Icon: MessageCircle, routeKey: "CHAT"       },
  { label: "Wiki",     Icon: BookOpen,      routeKey: "WIKI"       },
  { label: "Grafo",    Icon: Network,       routeKey: "GRAPH"      },
  { label: "Ingestar", Icon: Upload,        routeKey: "INGEST"     },
  { label: "Métricas", Icon: BarChart2,     routeKey: "METRICS"    },
  { label: "Admin",    Icon: Settings2,     routeKey: "ADMIN"      },
  { label: "Maestros", Icon: BookMarked,    routeKey: "VOCABULARY" },
];

// ─────────────────────────────────────────────────────────────────────────────
// Utilidades
// ─────────────────────────────────────────────────────────────────────────────

/** Devuelve el nivel de retrieval con mayor uso, o null si no hay datos */
function getMostUsedLevel(levelUsage?: Record<string, number>): BrainLevel | null {
  if (!levelUsage || Object.keys(levelUsage).length === 0) return null;
  const [topKey] = Object.entries(levelUsage).sort(([, a], [, b]) => b - a)[0];
  const num = Number(topKey);
  return num in BRAIN_LEVEL_CONFIG ? (num as BrainLevel) : null;
}

/** Formatea una fecha ISO a texto legible */
function formatDate(iso: string | number | null): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("es-ES", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return String(iso);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Subcomponentes
// ─────────────────────────────────────────────────────────────────────────────

/** Tarjeta de colección Qdrant con borde de color e icono por scope */
function CollectionCard({
  scope,
  count,
  isLoading,
}: {
  scope: BrainScope;
  count: number;
  isLoading: boolean;
}) {
  const cfg = COLLECTION_CONFIG[scope];
  const isEmpty = !isLoading && count === 0;

  return (
    <Card className={cn("border-l-4", cfg.borderClass)}>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {cfg.label}
        </CardTitle>
        <cfg.Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <>
            <Skeleton className="h-8 w-20 mb-1" />
            <Skeleton className="h-3 w-28" />
          </>
        ) : (
          <>
            <p className={cn("text-2xl font-bold", isEmpty && "text-muted-foreground")}>
              {count.toLocaleString("es-ES")}
            </p>
            <p className="text-xs text-muted-foreground mt-1">{cfg.description}</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** Indicador de estado de un servicio con semáforo de color */
function ServiceHealthBadge({
  service,
  status,
}: {
  service: string;
  status: ServiceHealthStatus;
}) {
  const cfg = HEALTH_STATUS_CONFIG[status] ?? HEALTH_STATUS_CONFIG.unknown;

  return (
    <div
      className="flex items-center gap-2 text-sm"
      role="status"
      aria-label={`${service}: ${cfg.label}`}
    >
      <cfg.Icon className={cn("h-4 w-4 shrink-0", cfg.iconClass)} aria-hidden />
      <span className="font-medium">{service}</span>
      <span className={cn("text-xs font-medium", cfg.textClass)}>{cfg.label}</span>
    </div>
  );
}

/** Estado vacío cuando el Brain no tiene documentos indexados aún */
function BrainEmptyState({ searchSpaceId }: { searchSpaceId: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border/60 bg-muted/20 py-12 text-center">
      <Brain className="h-10 w-10 text-violet-400/60" aria-hidden />
      <div>
        <p className="text-sm font-medium">Tu Brain está vacío</p>
        <p className="mt-1 text-xs text-muted-foreground max-w-xs mx-auto">
          Ingesta tu primer documento para empezar a construir tu base de conocimiento.
        </p>
      </div>
      <Link
        href={BRAIN_ROUTES.INGEST(searchSpaceId)}
        className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-violet-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-600 transition-colors"
      >
        <Upload className="h-3.5 w-3.5" aria-hidden />
        Ingestar primer documento
      </Link>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Página principal
// ─────────────────────────────────────────────────────────────────────────────

interface BrainHomePageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainHomePage({ params }: BrainHomePageProps) {
  const { search_space_id } = use(params);
  const spaceId = Number(search_space_id);

  // Stats de colecciones Qdrant
  const {
    data: stats,
    isLoading: statsLoading,
    error: statsError,
  } = useQuery({
    queryKey:           cacheKeys.brain.stats(spaceId),
    queryFn:            () => {
      log.debug("Fetching brain stats", { spaceId });
      return brainApiService.getStats(spaceId);
    },
    enabled:            !!spaceId,
    staleTime:          60_000,
    refetchOnWindowFocus: true,
  });

  // Estado de salud de los servicios — se refresca cada 30s automáticamente
  const {
    data: health,
    isLoading: healthLoading,
    dataUpdatedAt: healthUpdatedAt,
    refetch: refetchHealth,
    isFetching: healthFetching,
  } = useQuery({
    queryKey:        cacheKeys.brain.health(),
    queryFn:         () => {
      log.debug("Fetching health status");
      return brainApiService.getHealth();
    },
    staleTime:       30_000,
    refetchInterval: 30_000,
    retry:           false, // no reintentar — si falla, el usuario lo ve inmediatamente
  });

  const mostUsedLevel = getMostUsedLevel(stats?.level_usage);
  const totalDocs = (stats?.brain_count ?? 0) + (stats?.knowledge_count ?? 0) + (stats?.code_count ?? 0);
  const isBrainEmpty = !statsLoading && totalDocs === 0;

  if (statsError) {
    log.error("Error cargando stats Brain", { error: String(statsError) });
    return (
      <div className="p-6 animate-in fade-in duration-300">
        <Alert variant="destructive">
          <AlertDescription>
            No se pudieron cargar las estadísticas del Brain. Comprueba que el backend está activo.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-5xl mx-auto animate-in fade-in duration-300">

      {/* ── Cabecera ─────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-500/10">
          <Brain className="h-5 w-5 text-violet-400" aria-hidden />
        </div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight">SecondBrainSense</h1>
          <p className="text-sm text-muted-foreground">Estado del sistema</p>
        </div>
      </div>

      {/* ── Estado de servicios (información crítica primero) ─────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Estado de servicios
          </CardTitle>
          <div className="flex items-center gap-2">
            {healthUpdatedAt > 0 && (
              <span className="text-[10px] text-muted-foreground/60">
                Actualizado {formatDate(healthUpdatedAt)}
              </span>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={() => refetchHealth()}
              disabled={healthFetching}
              aria-label="Refrescar estado de servicios"
              title="Refrescar"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", healthFetching && "animate-spin")} aria-hidden />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {healthLoading ? (
            <div className="flex gap-6">
              {["Qdrant", "Ollama", "PostgreSQL"].map((s) => (
                <Skeleton key={s} className="h-5 w-28" />
              ))}
            </div>
          ) : health ? (
            <div className="flex flex-wrap gap-6">
              {(Object.keys(HEALTH_SERVICE_LABELS) as (keyof HealthResponse)[])
                .filter((key) => health[key] !== undefined)
                .map((key) => (
                  <ServiceHealthBadge
                    key={key}
                    service={HEALTH_SERVICE_LABELS[key]!}
                    status={health[key] as ServiceHealthStatus}
                  />
                ))}
            </div>
          ) : (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Comprobando servicios…
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Empty state cuando el Brain está vacío ───────────────────────── */}
      {isBrainEmpty && <BrainEmptyState searchSpaceId={search_space_id} />}

      {/* ── Tarjetas de colección Qdrant ─────────────────────────────────── */}
      <section aria-label="Colecciones Qdrant">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Colecciones Qdrant
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {BRAIN_SCOPES.map((scope) => (
            <CollectionCard
              key={scope}
              scope={scope}
              count={stats?.[STATS_FIELD_MAP[scope]] ?? 0}
              isLoading={statsLoading}
            />
          ))}
        </div>
      </section>

      {/* ── Última ingesta + Nivel más usado ─────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Última ingesta
            </CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent>
            {statsLoading ? (
              <>
                <Skeleton className="h-4 w-40 mb-1" />
                <Skeleton className="h-3 w-28" />
              </>
            ) : (
              <>
                <p
                  className="truncate text-sm font-medium"
                  title={stats?.last_ingest_doc ?? undefined}
                >
                  {stats?.last_ingest_doc ?? "Sin datos"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {formatDate(stats?.last_ingest ?? null)}
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Nivel más usado (24h)
            </CardTitle>
            <BarChart2 className="h-4 w-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent>
            {statsLoading ? (
              <Skeleton className="h-6 w-32" />
            ) : mostUsedLevel !== null ? (
              <LevelBadge level={mostUsedLevel} />
            ) : (
              <p className="text-sm text-muted-foreground">Sin actividad reciente</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ── Accesos rápidos (excluye Home — ya estamos aquí) ─────────────── */}
      <section aria-label="Accesos rápidos a secciones Brain">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Accesos rápidos
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {QUICK_ACCESS_ITEMS.map(({ label, Icon, routeKey }) => (
            <Link
              key={routeKey}
              href={BRAIN_ROUTES[routeKey](search_space_id)}
              className={cn(
                "flex flex-col items-center gap-2 rounded-lg border p-4 text-sm font-medium",
                "text-muted-foreground transition-colors",
                "hover:border-violet-500/40 hover:bg-violet-500/5 hover:text-foreground",
              )}
            >
              <Icon className="h-5 w-5" aria-hidden />
              {label}
            </Link>
          ))}
        </div>
      </section>

    </div>
  );
}


import { use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart2,
  BookMarked,
  BookOpen,
  Brain,
  CheckCircle2,
  CircleAlert,
  Clock,
  Database,
  LayoutDashboard,
  Loader2,
  MessageCircle,
  Network,
  Settings2,
  Upload,
  XCircle,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { LevelBadge } from "@/components/brain/LevelBadge";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import {
  BRAIN_LEVEL_CONFIG,
  BRAIN_ROUTES,
  BRAIN_SCOPES,
  type BrainLevel,
  type BrainScope,
} from "@/lib/brain/constants";
import { cacheKeys } from "@/lib/query-client/cache-keys";
import { cn } from "@/lib/utils";
import type { HealthResponse, ServiceHealthStatus } from "@/contracts/types/brain.types";

const log = brainLogger("BrainHomePage");

// ── Configuración visual de colecciones (sin hardcode) ────────────────────────

const COLLECTION_CONFIG: Record<BrainScope, { label: string; icon: string; description: string }> = {
  brain:     { label: "Brain",     icon: "🧠", description: "Pasaportes sintetizados" },
  knowledge: { label: "Knowledge", icon: "📚", description: "Chunks semánticos" },
  code:      { label: "Code",      icon: "💻", description: "Fragmentos de código" },
};

// ── Configuración visual de estado de salud ───────────────────────────────────

const HEALTH_SERVICE_LABELS: Record<keyof HealthResponse, string> = {
  qdrant:     "Qdrant",
  ollama:     "Ollama",
  postgresql: "PostgreSQL",
  redis:      "Redis",
};

const HEALTH_STATUS_CONFIG: Record<ServiceHealthStatus, { icon: React.ElementType; classes: string; label: string }> = {
  ok:       { icon: CheckCircle2, classes: "text-emerald-500", label: "OK" },
  degraded: { icon: CircleAlert,  classes: "text-yellow-500",  label: "Degradado" },
  error:    { icon: XCircle,      classes: "text-red-500",     label: "Error" },
  unknown:  { icon: CircleAlert,  classes: "text-muted-foreground", label: "Desconocido" },
};

// ── Accesos rápidos a secciones Brain ─────────────────────────────────────────

const QUICK_ACCESS_ITEMS = [
  { label: "Home",     icon: LayoutDashboard, routeKey: "HOME"       },
  { label: "Chat",     icon: MessageCircle,   routeKey: "CHAT"       },
  { label: "Wiki",     icon: BookOpen,        routeKey: "WIKI"       },
  { label: "Grafo",    icon: Network,         routeKey: "GRAPH"      },
  { label: "Ingestar", icon: Upload,          routeKey: "INGEST"     },
  { label: "Métricas", icon: BarChart2,       routeKey: "METRICS"    },
  { label: "Admin",    icon: Settings2,       routeKey: "ADMIN"      },
  { label: "Maestros", icon: BookMarked,      routeKey: "VOCABULARY" },
] as const satisfies readonly { label: string; icon: React.ElementType; routeKey: keyof typeof BRAIN_ROUTES }[];

// ── Utilidades ────────────────────────────────────────────────────────────────

/**
 * Devuelve el nivel de retrieval con mayor uso, o null si no hay datos.
 */
function getMostUsedLevel(levelUsage?: Record<string, number>): BrainLevel | null {
  if (!levelUsage || Object.keys(levelUsage).length === 0) return null;
  const [topKey] = Object.entries(levelUsage).sort(([, a], [, b]) => b - a)[0];
  const num = Number(topKey);
  return num in BRAIN_LEVEL_CONFIG ? (num as BrainLevel) : null;
}

/**
 * Formatea una fecha ISO a texto legible en español.
 */
function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("es-ES", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

// ── Subcomponentes ────────────────────────────────────────────────────────────

/** Tarjeta de colección Qdrant con contador de vectores */
function CollectionCard({
  scope,
  count,
  isLoading,
}: {
  scope: BrainScope;
  count: number;
  isLoading: boolean;
}) {
  const cfg = COLLECTION_CONFIG[scope];
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {cfg.icon} {cfg.label}
        </CardTitle>
        <Database className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <p className="text-2xl font-bold">{count.toLocaleString("es-ES")}</p>
        )}
        <p className="text-xs text-muted-foreground mt-1">{cfg.description}</p>
      </CardContent>
    </Card>
  );
}

/** Indicador de estado de un servicio */
function ServiceHealthBadge({
  service,
  status,
}: {
  service: string;
  status: ServiceHealthStatus;
}) {
  const cfg = HEALTH_STATUS_CONFIG[status] ?? HEALTH_STATUS_CONFIG.unknown;
  const Icon = cfg.icon;
  return (
    <div className="flex items-center gap-2 text-sm">
      <Icon className={cn("h-4 w-4 shrink-0", cfg.classes)} aria-hidden />
      <span className="font-medium">{service}</span>
      <span className={cn("text-xs", cfg.classes)}>{cfg.label}</span>
    </div>
  );
}

// ── Página principal ──────────────────────────────────────────────────────────

interface BrainHomePageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainHomePage({ params }: BrainHomePageProps) {
  const { search_space_id } = use(params);
  const spaceId = Number(search_space_id);

  const {
    data: stats,
    isLoading: statsLoading,
    error: statsError,
  } = useQuery({
    queryKey: cacheKeys.brain.stats(spaceId),
    queryFn:  () => {
      log.debug("Fetching brain stats", { spaceId });
      return brainApiService.getStats(spaceId);
    },
    enabled:   !!spaceId,
    staleTime: 60_000,
  });

  const {
    data: health,
    isLoading: healthLoading,
  } = useQuery({
    queryKey: cacheKeys.brain.health(),
    queryFn:  () => {
      log.debug("Fetching health status");
      return brainApiService.getHealth();
    },
    staleTime: 30_000,
    retry: false, // el health check no debe reintentar en error
  });

  const mostUsedLevel = getMostUsedLevel(stats?.level_usage);

  log.debug("BrainHomePage render", {
    statsLoading,
    healthLoading,
    mostUsedLevel,
  });

  // ── Error de carga ─────────────────────────────────────────────────────────
  if (statsError) {
    log.error("Error cargando stats Brain", { error: String(statsError) });
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <AlertDescription>
            No se pudieron cargar las estadísticas del Brain. Comprueba que el backend está activo.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-5xl mx-auto">

      {/* ── Cabecera ───────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <Brain className="h-7 w-7 text-violet-400" />
        <div>
          <h1 className="text-xl font-semibold">SecondBrainSense</h1>
          <p className="text-sm text-muted-foreground">Estado del sistema · space {search_space_id}</p>
        </div>
      </div>

      {/* ── Tarjetas de colección ──────────────────────────────────────────── */}
      <section aria-label="Colecciones Qdrant">
        <h2 className="text-sm font-medium text-muted-foreground mb-3 uppercase tracking-wider">
          Colecciones Qdrant
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {BRAIN_SCOPES.map((scope) => (
            <CollectionCard
              key={scope}
              scope={scope}
              count={
                scope === "brain"     ? (stats?.brain_count     ?? 0) :
                scope === "knowledge" ? (stats?.knowledge_count ?? 0) :
                                        (stats?.code_count      ?? 0)
              }
              isLoading={statsLoading}
            />
          ))}
        </div>
      </section>

      {/* ── Fila: última ingesta + nivel más usado ─────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">

        {/* Última ingesta */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Última ingesta
            </CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {statsLoading ? (
              <Skeleton className="h-5 w-40" />
            ) : (
              <>
                <p className="text-sm font-medium truncate" title={stats?.last_ingest_doc ?? undefined}>
                  {stats?.last_ingest_doc ?? "Sin datos"}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {formatDate(stats?.last_ingest ?? null)}
                </p>
              </>
            )}
          </CardContent>
        </Card>

        {/* Nivel de retrieval más usado */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Nivel más usado (24h)
            </CardTitle>
            <BarChart2 className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {statsLoading ? (
              <Skeleton className="h-6 w-28" />
            ) : mostUsedLevel !== null ? (
              <LevelBadge level={mostUsedLevel} />
            ) : (
              <p className="text-sm text-muted-foreground">Sin actividad reciente</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ── Estado de salud ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
            Estado de servicios
          </CardTitle>
        </CardHeader>
        <CardContent>
          {healthLoading ? (
            <div className="flex gap-6">
              {["Qdrant", "Ollama", "PostgreSQL"].map((s) => (
                <Skeleton key={s} className="h-5 w-24" />
              ))}
            </div>
          ) : health ? (
            <div className="flex flex-wrap gap-6">
              {(Object.entries(HEALTH_SERVICE_LABELS) as [keyof HealthResponse, string][])
                .filter(([key]) => health[key] !== undefined)
                .map(([key, label]) => (
                  <ServiceHealthBadge
                    key={key}
                    service={label}
                    status={health[key] as ServiceHealthStatus}
                  />
                ))}
            </div>
          ) : (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Comprobando servicios…
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Accesos rápidos ────────────────────────────────────────────────── */}
      <section aria-label="Accesos rápidos">
        <h2 className="text-sm font-medium text-muted-foreground mb-3 uppercase tracking-wider">
          Accesos rápidos
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {QUICK_ACCESS_ITEMS.map(({ label, icon: Icon, routeKey }) => (
            <Link
              key={routeKey}
              href={BRAIN_ROUTES[routeKey](search_space_id)}
              className={cn(
                "flex flex-col items-center gap-2 rounded-lg border p-4",
                "text-sm font-medium text-muted-foreground",
                "hover:bg-muted/50 hover:text-foreground transition-colors",
              )}
            >
              <Icon className="h-5 w-5" aria-hidden />
              {label}
            </Link>
          ))}
        </div>
      </section>

    </div>
  );
}


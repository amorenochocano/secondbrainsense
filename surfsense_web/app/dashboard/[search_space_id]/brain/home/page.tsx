"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/home
 *
 * Dashboard Brain â€” pantalla de bienvenida y estado del sistema SecondBrainSense.
 *
 * Muestra en tiempo real:
 *  - Estado de salud de los servicios (Qdrant, Ollama, PostgreSQL) â€” primera secciÃ³n
 *  - Tarjetas de colecciÃ³n Qdrant (brain / knowledge / code) con color por scope
 *  - Ãšltima ingesta: fecha + documento
 *  - Nivel de retrieval mÃ¡s usado en las Ãºltimas 24h
 *  - Accesos rÃ¡pidos a las secciones principales (excluye Home)
 *
 * Fuentes de datos:
 *  - GET /api/v1/brain/stats  â†’ BrainStatsResponse  (stale 60s, refetch on focus)
 *  - GET /api/v1/health       â†’ HealthResponse       (stale 30s, refetch 30s, no retry)
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

// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// ConfiguraciÃ³n estÃ¡tica (sin hardcode en JSX)
// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/** Icono, color de borde izquierdo y descripciÃ³n por scope de colecciÃ³n Qdrant */
const COLLECTION_CONFIG: Record<
  BrainScope,
  { label: string; Icon: ElementType; borderClass: string; description: string }
> = {
  brain:     { label: "Brain",     Icon: Brain,   borderClass: "border-l-violet-500", description: "Pasaportes sintetizados" },
  knowledge: { label: "Knowledge", Icon: Library, borderClass: "border-l-blue-500",   description: "Chunks semÃ¡nticos"       },
  code:      { label: "Code",      Icon: Code2,   borderClass: "border-l-emerald-500",description: "Fragmentos de cÃ³digo"    },
};

/** Mapeo directo de scope â†’ campo en BrainStatsResponse para evitar ternarios */
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
 * Accesos rÃ¡pidos: excluye HOME (ya estamos aquÃ­).
 * routeKey tipado contra BRAIN_ROUTES para detectar claves invÃ¡lidas en compilaciÃ³n.
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
  { label: "MÃ©tricas", Icon: BarChart2,     routeKey: "METRICS"    },
  { label: "Admin",    Icon: Settings2,     routeKey: "ADMIN"      },
  { label: "Maestros", Icon: BookMarked,    routeKey: "VOCABULARY" },
];

// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Utilidades
// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/** Devuelve el nivel de retrieval con mayor uso, o null si no hay datos */
function getMostUsedLevel(levelUsage?: Record<string, number>): BrainLevel | null {
  if (!levelUsage || Object.keys(levelUsage).length === 0) return null;
  const [topKey] = Object.entries(levelUsage).sort(([, a], [, b]) => b - a)[0];
  const num = Number(topKey);
  return num in BRAIN_LEVEL_CONFIG ? (num as BrainLevel) : null;
}

/** Formatea una fecha ISO a texto legible */
function formatDate(iso: string | number | null): string {
  if (!iso) return "â€”";
  try {
    return new Intl.DateTimeFormat("es-ES", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return String(iso);
  }
}

// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Subcomponentes
// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/** Tarjeta de colecciÃ³n Qdrant con borde de color e icono por scope */
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

/** Indicador de estado de un servicio con semÃ¡foro de color */
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

/** Estado vacÃ­o cuando el Brain no tiene documentos indexados aÃºn */
function BrainEmptyState({ searchSpaceId }: { searchSpaceId: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border/60 bg-muted/20 py-12 text-center">
      <Brain className="h-10 w-10 text-violet-400/60" aria-hidden />
      <div>
        <p className="text-sm font-medium">Tu Brain estÃ¡ vacÃ­o</p>
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

// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// PÃ¡gina principal
// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

  // Estado de salud de los servicios â€” se refresca cada 30s automÃ¡ticamente
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
    retry:           false, // no reintentar â€” si falla, el usuario lo ve inmediatamente
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
            No se pudieron cargar las estadÃ­sticas del Brain. Comprueba que el backend estÃ¡ activo.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-5xl mx-auto animate-in fade-in duration-300">

      {/* â”€â”€ Cabecera â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-500/10">
          <Brain className="h-5 w-5 text-violet-400" aria-hidden />
        </div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight">SecondBrainSense</h1>
          <p className="text-sm text-muted-foreground">Estado del sistema</p>
        </div>
      </div>

      {/* â”€â”€ Estado de servicios (informaciÃ³n crÃ­tica primero) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
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
              Comprobando serviciosâ€¦
            </div>
          )}
        </CardContent>
      </Card>

      {/* â”€â”€ Empty state cuando el Brain estÃ¡ vacÃ­o â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      {isBrainEmpty && <BrainEmptyState searchSpaceId={search_space_id} />}

      {/* â”€â”€ Tarjetas de colecciÃ³n Qdrant â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
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

      {/* â”€â”€ Ãšltima ingesta + Nivel mÃ¡s usado â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Ãšltima ingesta
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
              Nivel mÃ¡s usado (24h)
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

      {/* â”€â”€ Accesos rÃ¡pidos (excluye Home â€” ya estamos aquÃ­) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <section aria-label="Accesos rÃ¡pidos a secciones Brain">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Accesos rÃ¡pidos
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

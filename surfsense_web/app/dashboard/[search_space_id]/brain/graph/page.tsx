"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/graph
 *
 * Grafo interactivo de conocimiento — ForceGraph2D con panel de detalle lateral.
 *
 * Mejoras sobre el Streamlit original (vis-network en iframe):
 * - Sin `st.rerun()`: el panel de detalle se actualiza sin rerenderizar el grafo
 * - Nodo seleccionado se resalta visualmente (ring ámbar) sin relayout
 * - Panel de stats siempre visible (hub, docs aislados, dominios activos)
 * - Estado vacío educativo — explica cómo se crean las conexiones
 * - Integración directa: "Abrir en Wiki" y "Preguntar al Brain" son router.push()
 * - Filtro de dominio + toggle de aristas de tag_overlap (ruido vs contexto)
 * - Leyenda de dominio y tipos de arista accesible
 *
 * @note react-force-graph requiere `pnpm install` tras añadirlo a package.json
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  BookOpen,
  MessageCircle,
  Network,
  RefreshCw,
  Share2,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { DomainBadge } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import {
  BRAIN_DOMAIN_CONFIG,
  BRAIN_DOMAIN_GRAPH_COLORS,
  BRAIN_DOMAINS,
  BRAIN_GRAPH_EDGE_COLORS,
  BRAIN_GRAPH_EDGE_WIDTHS,
  BRAIN_GRAPH_NODE_DEFAULT_COLOR,
  BRAIN_GRAPH_NODE_SIZE_MAX,
  BRAIN_GRAPH_NODE_SIZE_MIN,
  BRAIN_GRAPH_TOOLTIP_SUMMARY_LENGTH,
  BRAIN_IMPORTANCE_MAX,
  BRAIN_ROUTES,
} from "@/lib/brain/constants";
import { CACHE_KEYS } from "@/lib/query-client/cache-keys";
import type { BrainDomain, GraphEdgeType, GraphNode } from "@/contracts/types/brain.types";
import { cn } from "@/lib/utils";

const log = brainLogger("BrainGraph");

// ─────────────────────────────────────────────────────────────────────────────
// ForceGraph2D — carga diferida (canvas/DOM-only, incompatible con SSR)
// ─────────────────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ForceGraph2D = dynamic<any>(
  () => import("react-force-graph").then((m) => m.ForceGraph2D),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full w-full items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-3">
          <Network className="size-10 animate-pulse text-muted-foreground/30" />
          <p className="text-xs text-muted-foreground">Calculando layout...</p>
        </div>
      </div>
    ),
  },
);

// ─────────────────────────────────────────────────────────────────────────────
// Types internos
// ─────────────────────────────────────────────────────────────────────────────

/** Nodo del grafo enriquecido con posición interna de react-force-graph */
interface GraphNodeFG extends GraphNode {
  x?: number;
  y?: number;
  fx?: number;
  fy?: number;
}

/** Arista para react-force-graph — source/target pueden ser ID o nodo resuelto */
interface GraphLinkFG {
  source: string | GraphNodeFG;
  target: string | GraphNodeFG;
  type: GraphEdgeType;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Calcula el tamaño de un nodo a partir de su importancia (1-5) */
function nodeSize(importance: number | null | undefined): number {
  const v = importance ?? 1;
  const range = BRAIN_GRAPH_NODE_SIZE_MAX - BRAIN_GRAPH_NODE_SIZE_MIN;
  return BRAIN_GRAPH_NODE_SIZE_MIN + (v / BRAIN_IMPORTANCE_MAX) * range;
}

/** Importancia como puntos visuales */
function ImportanceDots({ value }: { value: number | null | undefined }) {
  const v = value ?? 0;
  return (
    <span className="flex items-center gap-0.5">
      {Array.from({ length: BRAIN_IMPORTANCE_MAX }, (_, i) => (
        <span
          key={i}
          className={cn("size-1.5 rounded-full", i < v ? "bg-violet-400" : "bg-border")}
        />
      ))}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-componentes internos
// ─────────────────────────────────────────────────────────────────────────────

/** Panel lateral de stats cuando no hay nodo seleccionado */
function GraphStatsPanel({
  nodes,
  links,
}: {
  nodes: GraphNodeFG[];
  links: GraphLinkFG[];
}) {
  // Hub: nodo con más conexiones
  const connectionCount = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const link of links) {
      const s = typeof link.source === "string" ? link.source : link.source.id;
      const t = typeof link.target === "string" ? link.target : link.target.id;
      counts[s] = (counts[s] ?? 0) + 1;
      counts[t] = (counts[t] ?? 0) + 1;
    }
    return counts;
  }, [links]);

  const hubNode = useMemo(() => {
    if (!nodes.length) return null;
    return nodes.reduce(
      (max, n) => ((connectionCount[n.id] ?? 0) > (connectionCount[max.id] ?? 0) ? n : max),
      nodes[0],
    );
  }, [nodes, connectionCount]);

  const isolatedCount = nodes.filter((n) => !connectionCount[n.id]).length;
  const domainCount = new Set(nodes.map((n) => n.domain).filter(Boolean)).size;
  const relatedCount = links.filter((l) => l.type === "related").length;

  return (
    <div className="flex flex-col gap-4 p-4 animate-in fade-in duration-300">
      <div className="text-center">
        <Network className="mx-auto size-8 text-muted-foreground/30 mb-2" />
        <p className="text-xs text-muted-foreground">
          Haz clic en un nodo para ver su detalle
        </p>
      </div>

      <Separator />

      {/* Stats del grafo */}
      <div className="space-y-2.5">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Resumen
        </p>
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: "Documentos", value: nodes.length },
            { label: "Conexiones", value: links.filter((l) => l.type !== "tag_overlap").length },
            { label: "Dominios", value: domainCount },
            { label: "Refs explícitas", value: relatedCount },
          ].map(({ label, value }) => (
            <div key={label} className="rounded-lg border border-border bg-muted/20 p-2.5 text-center">
              <p className="text-lg font-bold tabular-nums text-foreground">{value}</p>
              <p className="text-[10px] text-muted-foreground">{label}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Hub del conocimiento */}
      {hubNode && connectionCount[hubNode.id] > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Hub del conocimiento
          </p>
          <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 p-2.5">
            <p className="text-xs font-medium line-clamp-1">{hubNode.label}</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {connectionCount[hubNode.id]} conexiones
            </p>
          </div>
        </div>
      )}

      {/* Documentos aislados */}
      {isolatedCount > 0 && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-2.5">
          <p className="text-xs font-medium text-amber-300">
            {isolatedCount} doc{isolatedCount !== 1 ? "s" : ""} sin conexiones
          </p>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            Añade campos `related`, `serie` o etiquetas compartidas para conectarlos
          </p>
        </div>
      )}
    </div>
  );
}

/** Panel de detalle de un nodo seleccionado */
function NodeDetailPanel({
  node,
  searchSpaceId,
  onClose,
}: {
  node: GraphNodeFG;
  searchSpaceId: string;
  onClose: () => void;
}) {
  const nodeColor =
    BRAIN_DOMAIN_GRAPH_COLORS[node.domain ?? "other"] ?? BRAIN_GRAPH_NODE_DEFAULT_COLOR;

  return (
    <div className="flex flex-col gap-3 p-4 animate-in fade-in slide-in-from-right-2 duration-200">
      {/* Cabecera */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="size-3 shrink-0 rounded-full"
            style={{ background: nodeColor }}
          />
          <p className="text-xs font-semibold line-clamp-2 leading-snug">{node.label}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Cerrar panel de detalle"
        >
          <X className="size-3.5" />
        </button>
      </div>

      {/* Domain badge */}
      {node.domain && <DomainBadge domain={node.domain as BrainDomain} />}

      {/* Importancia */}
      <ImportanceDots value={node.val} />

      {/* Tags */}
      {node.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {node.tags.slice(0, 6).map((tag) => (
            <span
              key={tag}
              className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
            >
              #{tag}
            </span>
          ))}
        </div>
      )}

      {/* Resumen */}
      {node.summary && (
        <div className="rounded-lg border border-border/40 bg-muted/20 p-2.5">
          <p className="text-xs text-muted-foreground leading-relaxed">
            {node.summary.slice(0, BRAIN_GRAPH_TOOLTIP_SUMMARY_LENGTH)}
            {node.summary.length > BRAIN_GRAPH_TOOLTIP_SUMMARY_LENGTH && "…"}
          </p>
        </div>
      )}

      <Separator />

      {/* Acciones */}
      <div className="flex flex-col gap-2">
        <Button variant="outline" size="sm" className="w-full justify-start gap-2 text-xs" asChild>
          <Link href={BRAIN_ROUTES.WIKI_ITEM(searchSpaceId, node.id)}>
            <BookOpen className="size-3.5" />
            Abrir en Wiki
          </Link>
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start gap-2 text-xs border-violet-500/30 text-violet-300 hover:bg-violet-500/10 hover:text-violet-200"
          asChild
        >
          <Link
            href={`${BRAIN_ROUTES.CHAT(searchSpaceId)}?q=${encodeURIComponent(node.label)}`}
          >
            <MessageCircle className="size-3.5" />
            Preguntar al Brain
          </Link>
        </Button>
      </div>
    </div>
  );
}

/** Estado vacío — no hay nodos en el grafo */
function EmptyGraphState({ onIngest }: { onIngest: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 py-12 text-center animate-in fade-in duration-400">
      <div className="flex size-16 items-center justify-center rounded-2xl border border-border bg-muted">
        <Share2 className="size-7 text-muted-foreground/40" />
      </div>
      <div className="max-w-xs">
        <p className="text-sm font-medium">Grafo de conocimiento vacío</p>
        <p className="mt-1.5 text-xs text-muted-foreground leading-relaxed">
          Cuando tus documentos tengan campos <code className="rounded bg-muted px-1 text-violet-300">related</code>,{" "}
          <code className="rounded bg-muted px-1 text-violet-300">serie</code> o{" "}
          tags compartidos, aparecerán aquí como conexiones visuales.
        </p>
      </div>
      <Button
        size="sm"
        onClick={onIngest}
        className="bg-violet-600 text-xs text-white hover:bg-violet-500"
      >
        Ingestar documentos
      </Button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Página principal
// ─────────────────────────────────────────────────────────────────────────────

export default function BrainGraphPage() {
  const params = useParams<{ search_space_id: string }>();
  const router = useRouter();
  const searchSpaceId = parseInt(params.search_space_id, 10);

  // Estado UI
  const [selectedNode, setSelectedNode] = useState<GraphNodeFG | null>(null);
  const [showTagEdges, setShowTagEdges] = useState(false);
  const [filterDomains, setFilterDomains] = useState<Set<BrainDomain>>(new Set());

  // Dimensiones del canvas
  const containerRef = useRef<HTMLDivElement>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const graphRef = useRef<any>(null);

  // Resize observer para el canvas
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) {
        setCanvasSize({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // ── Queries ────────────────────────────────────────────────────────────────

  const {
    data: rawGraph,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: CACHE_KEYS.brain.graph(searchSpaceId),
    queryFn: () => brainApiService.getGraph(searchSpaceId),
    staleTime: 60_000,
  });

  // ── Transformación de datos ────────────────────────────────────────────────

  /** Filtra y adapta al formato que espera react-force-graph */
  const graphData = useMemo(() => {
    if (!rawGraph) return { nodes: [], links: [] };

    const filteredNodes =
      filterDomains.size > 0
        ? rawGraph.nodes.filter((n) => n.domain && filterDomains.has(n.domain as BrainDomain))
        : rawGraph.nodes;

    const nodeIds = new Set(filteredNodes.map((n) => n.id));

    const filteredLinks = rawGraph.edges.filter((e) => {
      if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) return false;
      if (e.type === "tag_overlap" && !showTagEdges) return false;
      return true;
    });

    log.debug("Grafo filtrado", {
      nodes: filteredNodes.length,
      links: filteredLinks.length,
      showTagEdges,
      filterDomains: filterDomains.size,
    });

    return {
      nodes: filteredNodes as GraphNodeFG[],
      links: filteredLinks.map((e) => ({
        source: e.source,
        target: e.target,
        type: e.type,
      })) as GraphLinkFG[],
    };
  }, [rawGraph, filterDomains, showTagEdges]);

  // ── Callbacks para ForceGraph2D ────────────────────────────────────────────

  const nodeColor = useCallback(
    (node: GraphNodeFG) => {
      const base =
        BRAIN_DOMAIN_GRAPH_COLORS[node.domain ?? "other"] ?? BRAIN_GRAPH_NODE_DEFAULT_COLOR;
      // Nodo seleccionado: más brillante / opaco
      if (selectedNode?.id === node.id) return base;
      return `${base}cc`; // ligeramente transparente cuando no está seleccionado
    },
    [selectedNode],
  );

  const nodeVal = useCallback(
    (node: GraphNodeFG) => nodeSize(node.val ?? node.tags?.length ?? 1),
    [],
  );

  const linkColor = useCallback(
    (link: GraphLinkFG) =>
      BRAIN_GRAPH_EDGE_COLORS[link.type as GraphEdgeType] ?? BRAIN_GRAPH_EDGE_COLORS.tag_overlap,
    [],
  );

  const linkWidth = useCallback(
    (link: GraphLinkFG) =>
      BRAIN_GRAPH_EDGE_WIDTHS[link.type as GraphEdgeType] ?? BRAIN_GRAPH_EDGE_WIDTHS.tag_overlap,
    [],
  );

  const handleNodeClick = useCallback((node: GraphNodeFG) => {
    log.debug("Nodo seleccionado", { id: node.id, label: node.label });
    setSelectedNode((prev) => (prev?.id === node.id ? null : node));
  }, []);

  const handleNodeHover = useCallback((node: GraphNodeFG | null) => {
    if (containerRef.current) {
      containerRef.current.style.cursor = node ? "pointer" : "default";
    }
  }, []);

  const handleZoomIn = useCallback(() => {
    graphRef.current?.zoom(1.5, 400);
  }, []);

  const handleZoomOut = useCallback(() => {
    graphRef.current?.zoom(0.7, 400);
  }, []);

  const handleResetView = useCallback(() => {
    graphRef.current?.zoomToFit(400, 40);
  }, []);

  const toggleDomain = useCallback((domain: BrainDomain) => {
    setFilterDomains((prev) => {
      const next = new Set(prev);
      next.has(domain) ? next.delete(domain) : next.add(domain);
      return next;
    });
  }, []);

  // ── Render ─────────────────────────────────────────────────────────────────

  if (isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 animate-in fade-in duration-300">
        <p className="text-sm text-muted-foreground">Error al cargar el grafo</p>
        <Button variant="ghost" size="sm" onClick={() => refetch()} className="gap-1.5">
          <RefreshCw className="size-3.5" />
          Reintentar
        </Button>
      </div>
    );
  }

  const isEmpty = !isLoading && graphData.nodes.length === 0;

  return (
    <div className="flex h-full flex-col overflow-hidden animate-in fade-in duration-300">
      {/* ── Toolbar ─────────────────────────────────────────────────── */}
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border/50 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Network className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">Grafo de conocimiento</span>
          {!isLoading && (
            <div className="flex items-center gap-1.5">
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                {graphData.nodes.length} nodos
              </span>
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                {graphData.links.filter((l) => l.type !== "tag_overlap").length} aristas
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Toggle aristas de tag_overlap */}
          <button
            type="button"
            onClick={() => setShowTagEdges((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
              showTagEdges
                ? "border-gray-500/50 bg-gray-500/10 text-gray-300"
                : "border-border bg-muted text-muted-foreground hover:border-gray-500/30",
            )}
          >
            <span
              className={cn(
                "size-2 rounded-full border",
                showTagEdges ? "border-gray-400 bg-gray-400/30" : "border-border bg-transparent",
              )}
            />
            Tags compartidos
          </button>

          {/* Filtros de dominio */}
          <div className="flex items-center gap-1">
            {BRAIN_DOMAINS.map((domain) => {
              const active = filterDomains.has(domain);
              const color = BRAIN_DOMAIN_GRAPH_COLORS[domain] ?? "#6b7280";
              return (
                <button
                  key={domain}
                  type="button"
                  onClick={() => toggleDomain(domain)}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[11px] transition-all",
                    active
                      ? "text-foreground"
                      : "border-transparent text-muted-foreground hover:border-border/60",
                  )}
                  style={
                    active
                      ? {
                          borderColor: `${color}50`,
                          background: `${color}15`,
                          color,
                        }
                      : {}
                  }
                  title={BRAIN_DOMAIN_CONFIG[domain].label}
                >
                  {BRAIN_DOMAIN_CONFIG[domain].label.slice(0, 3)}
                </button>
              );
            })}
            {filterDomains.size > 0 && (
              <button
                type="button"
                onClick={() => setFilterDomains(new Set())}
                className="rounded p-0.5 text-muted-foreground/60 hover:text-muted-foreground transition-colors"
                aria-label="Limpiar filtro de dominios"
              >
                <X className="size-3" />
              </button>
            )}
          </div>
        </div>
      </header>

      {/* ── Cuerpo: canvas + panel lateral ────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Canvas del grafo */}
        <div ref={containerRef} className="relative flex-1 bg-[#0d0d0f] overflow-hidden">
          {isLoading ? (
            <div className="flex h-full w-full items-center justify-center">
              <div className="flex flex-col items-center gap-3">
                <Network className="size-10 animate-pulse text-muted-foreground/30" />
                <p className="text-xs text-muted-foreground">Cargando grafo...</p>
              </div>
            </div>
          ) : isEmpty ? (
            <EmptyGraphState
              onIngest={() => router.push(BRAIN_ROUTES.INGEST(params.search_space_id))}
            />
          ) : (
            <>
              <ForceGraph2D
                ref={graphRef}
                width={canvasSize.width}
                height={canvasSize.height}
                graphData={graphData}
                nodeId="id"
                nodeLabel={(node: GraphNodeFG) =>
                  `<div style="background:#1f2937;border:1px solid #374151;padding:6px 10px;border-radius:8px;font-size:12px;max-width:220px">
                    <strong style="color:#f9fafb">${node.label}</strong>
                    ${node.domain ? `<br/><span style="color:#9ca3af">${node.domain}</span>` : ""}
                    ${node.summary ? `<br/><span style="color:#6b7280;font-size:11px">${node.summary.slice(0, 80)}…</span>` : ""}
                  </div>`
                }
                nodeColor={nodeColor}
                nodeVal={nodeVal}
                nodeRelSize={1}
                linkColor={linkColor}
                linkWidth={linkWidth}
                linkDirectionalParticles={(link: GraphLinkFG) =>
                  link.type === "related" ? 2 : 0
                }
                linkDirectionalParticleWidth={2}
                onNodeClick={handleNodeClick}
                onNodeHover={handleNodeHover}
                backgroundColor="#0d0d0f"
                warmupTicks={80}
                cooldownTicks={80}
                d3AlphaDecay={0.02}
                d3VelocityDecay={0.3}
              />

              {/* Controles de zoom */}
              <div className="absolute bottom-4 left-4 flex flex-col gap-1.5">
                <button
                  type="button"
                  onClick={handleZoomIn}
                  className="flex size-7 items-center justify-center rounded-lg border border-border/60 bg-background/80 text-muted-foreground backdrop-blur-sm hover:text-foreground transition-colors"
                  aria-label="Acercar"
                >
                  <ZoomIn className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={handleZoomOut}
                  className="flex size-7 items-center justify-center rounded-lg border border-border/60 bg-background/80 text-muted-foreground backdrop-blur-sm hover:text-foreground transition-colors"
                  aria-label="Alejar"
                >
                  <ZoomOut className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={handleResetView}
                  className="flex size-7 items-center justify-center rounded-lg border border-border/60 bg-background/80 text-muted-foreground backdrop-blur-sm hover:text-foreground transition-colors"
                  aria-label="Ajustar a pantalla"
                >
                  <RefreshCw className="size-3" />
                </button>
              </div>

              {/* Leyenda tipos de arista */}
              <div className="absolute bottom-4 right-4 flex flex-col gap-1 rounded-lg border border-border/40 bg-background/80 px-3 py-2.5 backdrop-blur-sm">
                {(
                  [
                    { type: "related", label: "Referencia", solid: true },
                    { type: "serie", label: "Serie", solid: false },
                    ...(showTagEdges
                      ? [{ type: "tag_overlap", label: "Tags comunes", solid: false }]
                      : []),
                  ] as { type: GraphEdgeType; label: string; solid: boolean }[]
                ).map(({ type, label, solid }) => (
                  <div key={type} className="flex items-center gap-2">
                    <svg width="20" height="6" className="shrink-0">
                      <line
                        x1="0"
                        y1="3"
                        x2="20"
                        y2="3"
                        stroke={BRAIN_GRAPH_EDGE_COLORS[type as GraphEdgeType]}
                        strokeWidth={solid ? 2 : 1.5}
                        strokeDasharray={solid ? "none" : "3 2"}
                      />
                    </svg>
                    <span className="text-[10px] text-muted-foreground">{label}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Panel lateral */}
        <aside className="flex w-64 shrink-0 flex-col overflow-y-auto border-l border-border/40 bg-card/50">
          {selectedNode ? (
            <NodeDetailPanel
              node={selectedNode}
              searchSpaceId={params.search_space_id}
              onClose={() => setSelectedNode(null)}
            />
          ) : (
            !isEmpty && (
              <GraphStatsPanel
                nodes={graphData.nodes}
                links={graphData.links}
              />
            )
          )}
        </aside>
      </div>

      {/* ── Leyenda de dominios ────────────────────────────────────────── */}
      {!isEmpty && !isLoading && (
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-t border-border/30 bg-card/30 px-4 py-1.5">
          <span className="text-[10px] text-muted-foreground/60">Dominio:</span>
          {BRAIN_DOMAINS.map((domain) => {
            const color = BRAIN_DOMAIN_GRAPH_COLORS[domain];
            return (
              <div key={domain} className="flex items-center gap-1.5">
                <span
                  className="size-2 rounded-full"
                  style={{ background: color }}
                />
                <span className="text-[10px] text-muted-foreground">
                  {BRAIN_DOMAIN_CONFIG[domain].label}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


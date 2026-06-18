"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/ingest
 *
 * F6.8 — Ingesta avanzada y monitorización del pipeline Brain.
 *
 * Diseño en dos pestañas:
 *   • "Ingestar URL": input de URL con routing preview en tiempo real,
 *     recomendación automática de modelo, y log animado de 4 fases SSE.
 *   • "Monitorización": historial de todos los documentos ingestados con
 *     categoría A/B/C del pipeline F5, scopes, y fecha de actualización.
 *
 * Gestión de logs: brainLogger("BrainIngestPage")
 * ZERO HARDCODE: todas las constantes en lib/brain/constants.ts
 */

import { useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  Link2,
  Sparkles,
  Info,
  ArrowRight,
  FileText,
  Brain,
  Database,
  Code2,
  CheckCircle2,
  Clock,
  ExternalLink,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useToast } from "@/hooks/use-toast";
import { BrainBreadcrumb, DomainBadge, IngestPhaseLog } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import { cacheKeys } from "@/lib/query-client/cache-keys";
import {
  BRAIN_ROUTES,
  BRAIN_MODEL_RECOMMENDATION,
  BRAIN_ROUTING_PREVIEW,
  BRAIN_INGEST_CATEGORIES,
  BRAIN_INGEST_EXTENSION_CATEGORY,
  type IngestCategory,
} from "@/lib/brain/constants";

const log = brainLogger("BrainIngestPage");

// ─── Utilidades locales ───────────────────────────────────────────────────────

/** Extrae la extensión de un path/URL (ej. ".pdf", null si no hay) */
function extractExtension(input: string): string | null {
  try {
    const pathname = input.startsWith("http") ? new URL(input).pathname : input;
    const match = pathname.match(/\.([a-zA-Z0-9]{1,10})(?:[?#]|$)/);
    return match ? `.${match[1].toLowerCase()}` : null;
  } catch {
    return null;
  }
}

/** Devuelve la categoría del pipeline para una extensión dada */
function getCategoryForExtension(ext: string | null): IngestCategory {
  return ext ? (BRAIN_INGEST_EXTENSION_CATEGORY[ext] ?? "C") : "C";
}

/** Valida que un string sea una URL http/https */
function isValidHttpUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

/** Formatea una fecha ISO a dd/mm/yyyy HH:MM */
function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("es-ES", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// ─── Sub-componente: Badge de categoría del pipeline ─────────────────────────

interface CategoryBadgeProps {
  category: IngestCategory;
}

function CategoryBadge({ category }: CategoryBadgeProps) {
  const cfg = BRAIN_INGEST_CATEGORIES[category];
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(
              "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold cursor-default",
              cfg.classes,
            )}
          >
            Cat. {category}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-52 text-center">
          <p className="font-semibold">{cfg.label}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{cfg.desc}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ─── Sub-componente: Panel de routing preview ─────────────────────────────────

interface RoutingPreviewPanelProps {
  url: string;
  selectedModel: string;
}

function RoutingPreviewPanel({ url, selectedModel }: RoutingPreviewPanelProps) {
  const ext = extractExtension(url);
  const category = getCategoryForExtension(ext);
  const catCfg = BRAIN_INGEST_CATEGORIES[category];

  // Determinar colecciones Qdrant según routing
  const preview = BRAIN_ROUTING_PREVIEW;
  const collections: string[] = ["brain"];
  if (preview.knowledge_routing) collections.push("knowledge");
  if (preview.code_routing && (ext === ".py" || ext === ".sql" || ext === ".ipynb")) {
    collections.push("code");
  }

  // Modelo recomendado según extensión
  const recommended = BRAIN_MODEL_RECOMMENDATION.model;
  const isRecommendedSelected = selectedModel === recommended || selectedModel === "";

  return (
    <div className="rounded-xl border border-slate-700/50 bg-slate-800/30 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 text-violet-400" />
        <span className="text-xs font-semibold uppercase tracking-widest text-slate-400">
          Routing preview
        </span>
      </div>

      {/* Extensión detectada + Categoría */}
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-xs text-slate-400">Tipo detectado:</span>
        <span className="font-mono text-xs text-slate-200 bg-slate-700/60 rounded px-1.5 py-0.5">
          {ext ?? "html / sin extensión"}
        </span>
        <CategoryBadge category={category} />
        <span className="text-xs text-slate-500">{catCfg.desc}</span>
      </div>

      {/* Colecciones destino */}
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-xs text-slate-400">Colecciones Qdrant:</span>
        {collections.map((col) => (
          <span
            key={col}
            className="flex items-center gap-1 rounded-full bg-violet-500/15 px-2.5 py-0.5 text-xs font-medium text-violet-300"
          >
            {col === "brain"     && <Brain   className="h-3 w-3" />}
            {col === "knowledge" && <Database className="h-3 w-3" />}
            {col === "code"      && <Code2   className="h-3 w-3" />}
            {col}
          </span>
        ))}
      </div>

      {/* Modelo recomendado */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-400">Modelo recomendado:</span>
        <span className="font-mono text-xs text-violet-300">{recommended}</span>
        {isRecommendedSelected && (
          <span className="flex items-center gap-1 text-xs text-emerald-400">
            <CheckCircle2 className="h-3 w-3" /> activo
          </span>
        )}
        {!isRecommendedSelected && (
          <span className="text-xs text-amber-400">
            (usando {selectedModel})
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Sub-componente: estado vacío del monitor ─────────────────────────────────

interface EmptyMonitorStateProps {
  onGoIngest: () => void;
}

function EmptyMonitorState({ onGoIngest }: EmptyMonitorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-4 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-700 bg-slate-800/60">
        <Database className="h-8 w-8 text-slate-500" />
      </div>
      <div>
        <h3 className="text-base font-semibold text-slate-200">Sin documentos ingestados</h3>
        <p className="text-sm text-slate-400 mt-1 max-w-xs">
          Usa la pestaña &quot;Ingestar URL&quot; para añadir documentos al Brain.
        </p>
      </div>
      <Button variant="outline" size="sm" onClick={onGoIngest} className="gap-2">
        <Link2 className="h-3.5 w-3.5" />
        Ingestar primer documento
      </Button>
    </div>
  );
}

// ─── Sub-componente: tabla del monitor ───────────────────────────────────────

interface MonitorTableProps {
  spaceId: string;
  onOpenWiki: (source: string) => void;
}

function MonitorTable({ spaceId, onOpenWiki }: MonitorTableProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: cacheKeys.brain.list(Number(spaceId)),
    queryFn: () => brainApiService.listPassports(Number(spaceId)),
    staleTime: 30_000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-violet-400" />
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-center py-10 text-sm text-red-400">
        Error al cargar los documentos. Inténtalo de nuevo.
      </p>
    );
  }

  const passports = data ?? [];
  if (passports.length === 0) return null; // El padre muestra EmptyMonitorState

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-700/60 text-xs text-slate-500 uppercase tracking-wider">
            <th className="text-left pb-3 pr-4 font-medium">Documento</th>
            <th className="text-left pb-3 pr-4 font-medium">Dominio</th>
            <th className="text-left pb-3 pr-4 font-medium">Categoría</th>
            <th className="text-left pb-3 pr-4 font-medium">Scopes</th>
            <th className="text-left pb-3 pr-4 font-medium">Actualizado</th>
            <th className="pb-3 font-medium" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-700/30">
          {passports.map((passport) => {
            const ext = extractExtension(passport.source);
            const category = getCategoryForExtension(ext);

            return (
              <tr
                key={passport.source}
                className="group hover:bg-slate-800/30 transition-colors"
              >
                {/* Nombre del documento */}
                <td className="py-3 pr-4">
                  <div className="flex items-center gap-2 max-w-xs">
                    <FileText className="h-3.5 w-3.5 shrink-0 text-slate-500" />
                    <span
                      className="truncate text-slate-200 font-medium"
                      title={passport.source}
                    >
                      {passport.source.split("/").pop() ?? passport.source}
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 truncate max-w-xs mt-0.5 pl-[22px]" title={passport.source}>
                    {passport.source}
                  </p>
                </td>

                {/* Dominio */}
                <td className="py-3 pr-4">
                  <DomainBadge domain={passport.domain} size="sm" />
                </td>

                {/* Categoría pipeline */}
                <td className="py-3 pr-4">
                  <CategoryBadge category={category} />
                </td>

                {/* Scopes */}
                <td className="py-3 pr-4">
                  <div className="flex flex-wrap gap-1">
                    {passport.scopes.map((scope) => (
                      <span
                        key={scope}
                        className="rounded-full bg-slate-700/50 px-2 py-0.5 text-xs text-slate-300"
                      >
                        {scope}
                      </span>
                    ))}
                  </div>
                </td>

                {/* Fecha actualización */}
                <td className="py-3 pr-4">
                  <span className="flex items-center gap-1 text-xs text-slate-400 whitespace-nowrap">
                    <Clock className="h-3 w-3" />
                    {formatDate(passport.updated_at)}
                  </span>
                </td>

                {/* Acción */}
                <td className="py-3">
                  <button
                    type="button"
                    onClick={() => onOpenWiki(passport.source)}
                    className="flex items-center gap-1 text-xs text-violet-400 hover:text-violet-300 transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <ExternalLink className="h-3 w-3" />
                    Wiki
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Página principal ─────────────────────────────────────────────────────────

export default function BrainIngestPage() {
  const params = useParams<{ search_space_id: string }>();
  const spaceId = params.search_space_id;
  const router = useRouter();
  const { toast } = useToast();

  // ── Estado del formulario de ingesta ────────────────────────────────────────
  const [url, setUrl]                 = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [isIngesting, setIsIngesting] = useState(false);
  const [ingestDone, setIngestDone]   = useState(false);
  const [activeTab, setActiveTab]     = useState("ingest");

  // ── Estado derivado ──────────────────────────────────────────────────────────
  const urlValid     = isValidHttpUrl(url);
  const showPreview  = url.length > 10 && urlValid;
  const canIngest    = urlValid && !isIngesting;

  // ── Lista de modelos disponibles (reutiliza modelos Ollama del sistema) ──────
  const { data: passportList } = useQuery({
    queryKey: cacheKeys.brain.list(Number(spaceId)),
    queryFn: () => brainApiService.listPassports(Number(spaceId)),
    staleTime: 30_000,
  });

  // ── Mutación de ingesta ──────────────────────────────────────────────────────
  const ingestMutation = useMutation({
    mutationFn: () =>
      brainApiService.ingestUrl(url, Number(spaceId), selectedModel || undefined),
    onSuccess: (response) => {
      log.info("Job de ingesta creado", { jobId: response.job_id, background: response.background });
      setActiveJobId(response.job_id);
      setIsIngesting(true);
      setIngestDone(false);

      if (response.background) {
        toast({
          title: "Ingesta en segundo plano",
          description: "El documento es grande. Puedes continuar usando Brain mientras se procesa.",
        });
      }
    },
    onError: (err: Error) => {
      log.error("Error al iniciar la ingesta", { err });
      toast({
        variant: "destructive",
        title: "Error al iniciar la ingesta",
        description: err.message,
      });
    },
  });

  // ── Callbacks SSE ────────────────────────────────────────────────────────────
  const handlePipelineComplete = useCallback(() => {
    setIsIngesting(false);
    setIngestDone(true);
    log.info("Pipeline completado, invalidar lista de documentos");
    toast({
      title: "Ingesta completada",
      description: "El documento ha sido procesado y está disponible en el Brain.",
    });
  }, [toast]);

  const handlePipelineError = useCallback(
    (err: Error) => {
      setIsIngesting(false);
      log.error("Error en pipeline SSE", { err });
      toast({
        variant: "destructive",
        title: "Error en el pipeline",
        description: err.message,
      });
    },
    [toast],
  );

  // ── Handlers de UI ───────────────────────────────────────────────────────────
  const handleIngest = () => {
    if (!canIngest) return;
    log.info("Iniciando ingesta", { url, model: selectedModel });
    ingestMutation.mutate();
  };

  const handleOpenWikiPassport = (source: string) => {
    router.push(BRAIN_ROUTES.WIKI_PASSPORT(spaceId, encodeURIComponent(source)));
  };

  const handleGoIngest = () => setActiveTab("ingest");

  const passportCount = passportList?.length ?? 0;

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto w-full">
      {/* Breadcrumb */}
      <BrainBreadcrumb spaceId={spaceId} current="Ingesta" />

      {/* Cabecera de la página */}
      <div>
        <h1 className="text-xl font-bold text-slate-100 flex items-center gap-2">
          <Link2 className="h-5 w-5 text-violet-400" />
          Ingesta avanzada
        </h1>
        <p className="text-sm text-slate-400 mt-1">
          Añade documentos al Brain por URL. Observa el pipeline en tiempo real.
        </p>
      </div>

      {/* Pestañas principales */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid w-full grid-cols-2 max-w-sm">
          <TabsTrigger value="ingest" className="gap-2">
            <Link2 className="h-3.5 w-3.5" />
            Ingestar URL
          </TabsTrigger>
          <TabsTrigger value="monitor" className="gap-2">
            <Database className="h-3.5 w-3.5" />
            Monitorización
            {passportCount > 0 && (
              <Badge variant="secondary" className="ml-1 h-4 text-[10px] px-1.5">
                {passportCount}
              </Badge>
            )}
          </TabsTrigger>
        </TabsList>

        {/* ── TAB 1: Ingestar URL ────────────────────────────────────────────── */}
        <TabsContent value="ingest" className="mt-6 space-y-6">
          {/* Sección: Input de URL */}
          <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-violet-400" />
              <h2 className="text-sm font-semibold text-slate-200">URL del documento</h2>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Info className="h-3.5 w-3.5 text-slate-500 cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent side="right" className="max-w-64">
                    <p className="text-xs">
                      Soporta páginas web (HTML), PDFs, ficheros Markdown, código fuente (.py,
                      .sql, .ipynb) y documentos Office. La URL debe ser accesible públicamente.
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>

            {/* Input */}
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
                <Input
                  type="url"
                  placeholder="https://ejemplo.com/documento.pdf"
                  value={url}
                  onChange={(e) => {
                    setUrl(e.target.value);
                    setIngestDone(false);
                    if (activeJobId) setActiveJobId(null);
                  }}
                  className="pl-9 font-mono text-sm bg-slate-900/50 border-slate-700 focus:border-violet-500"
                  disabled={isIngesting}
                />
              </div>
            </div>

            {/* Routing preview (aparece al escribir una URL válida) */}
            {showPreview && (
              <RoutingPreviewPanel url={url} selectedModel={selectedModel} />
            )}

            {/* Selector de modelo + Botón ingestar */}
            <div className="flex flex-wrap gap-3 items-center">
              <div className="flex items-center gap-2 flex-1 min-w-40">
                <Select
                  value={selectedModel}
                  onValueChange={setSelectedModel}
                  disabled={isIngesting}
                >
                  <SelectTrigger className="bg-slate-900/50 border-slate-700 text-sm">
                    <SelectValue placeholder="Modelo automático (recomendado)" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">
                      <span className="flex items-center gap-2">
                        <Sparkles className="h-3.5 w-3.5 text-violet-400" />
                        Automático — {BRAIN_MODEL_RECOMMENDATION.model}
                      </span>
                    </SelectItem>
                    <SelectItem value="gpt-4o">gpt-4o</SelectItem>
                    <SelectItem value="gpt-4o-mini">gpt-4o-mini</SelectItem>
                    <SelectItem value="claude-3-5-haiku-20241022">claude-3-5-haiku</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <Button
                onClick={handleIngest}
                disabled={!canIngest || ingestMutation.isPending}
                className="gap-2 bg-violet-600 hover:bg-violet-500 text-white"
              >
                {ingestMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Iniciando…
                  </>
                ) : isIngesting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Procesando…
                  </>
                ) : ingestDone ? (
                  <>
                    <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                    Ingestar otro
                  </>
                ) : (
                  <>
                    <ArrowRight className="h-4 w-4" />
                    Ingestar
                  </>
                )}
              </Button>
            </div>
          </div>

          {/* Log de fases SSE */}
          <IngestPhaseLog
            jobId={activeJobId}
            onComplete={handlePipelineComplete}
            onError={handlePipelineError}
          />

          {/* Banner de éxito + CTA a Wiki */}
          {ingestDone && (
            <div className="flex items-center justify-between rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
              <span className="flex items-center gap-2 text-sm text-emerald-300">
                <CheckCircle2 className="h-4 w-4" />
                Documento disponible en el Brain
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setActiveTab("monitor")}
                  className="border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 gap-1.5"
                >
                  <Database className="h-3.5 w-3.5" />
                  Ver en monitor
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => router.push(BRAIN_ROUTES.WIKI(spaceId))}
                  className="border-slate-600 gap-1.5"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Abrir Wiki
                </Button>
              </div>
            </div>
          )}

          {/* Información sobre el pipeline */}
          {!activeJobId && !ingestDone && (
            <div className="rounded-xl border border-slate-700/40 bg-slate-800/20 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
                ¿Cómo funciona el pipeline?
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {[
                  { icon: "📥", title: "Extracción", desc: "Parseo especializado según el tipo de fichero (PDF, código, Markdown…)" },
                  { icon: "🧠", title: "Síntesis L1", desc: "Un LLM genera el pasaporte semántico → colección brain de Qdrant" },
                  { icon: "📦", title: "Chunking L2", desc: "Fragmentación semántica del contenido → colecciones knowledge/code" },
                  { icon: "🔢", title: "Vectorización", desc: "Embeddings de los chunks almacenados en Qdrant para búsqueda RAG" },
                ].map((step) => (
                  <div key={step.title} className="flex gap-3 items-start">
                    <span className="text-xl">{step.icon}</span>
                    <div>
                      <p className="text-sm font-medium text-slate-300">{step.title}</p>
                      <p className="text-xs text-slate-500 mt-0.5">{step.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </TabsContent>

        {/* ── TAB 2: Monitorización ──────────────────────────────────────────── */}
        <TabsContent value="monitor" className="mt-6">
          <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Database className="h-4 w-4 text-violet-400" />
                <h2 className="text-sm font-semibold text-slate-200">
                  Documentos en el Brain
                </h2>
                {passportCount > 0 && (
                  <span className="text-xs text-slate-500">
                    {passportCount} documento{passportCount !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
            </div>

            {passportCount === 0 ? (
              <EmptyMonitorState onGoIngest={handleGoIngest} />
            ) : (
              <MonitorTable spaceId={spaceId} onOpenWiki={handleOpenWikiPassport} />
            )}
          </div>

          {/* Leyenda de categorías */}
          {passportCount > 0 && (
            <div className="mt-4 flex flex-wrap gap-3">
              {(["A", "B", "C"] as IngestCategory[]).map((cat) => (
                <div key={cat} className="flex items-center gap-2">
                  <CategoryBadge category={cat} />
                  <span className="text-xs text-slate-400">
                    {BRAIN_INGEST_CATEGORIES[cat].desc}
                  </span>
                </div>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

      <span className="text-4xl">📥</span>
      <p className="text-sm">Ingesta avanzada (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.8 — implementación pendiente</p>
    </div>
  );
}

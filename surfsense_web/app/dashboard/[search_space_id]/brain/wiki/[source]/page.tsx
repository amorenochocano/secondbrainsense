"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/wiki/[source]
 *
 * Vista de detalle de un pasaporte Brain.
 *
 * Layout: header con metadata + dos paneles (Monaco editor izq / preview Markdown dcha)
 *         + historial de versiones colapsable en la parte inferior.
 *
 * Mejoras sobre el Streamlit original:
 * - Monaco editor con tema dark, wordWrap, sin minimap — mucho mejor que un textarea
 * - Preview Markdown en vivo lado a lado (actualización instantánea al escribir)
 * - Indicador "cambios sin guardar" sutil (punto ámbar en el título)
 * - Re-síntesis con selección de modelo según extensión del fichero
 * - Historial de versiones collapsible con preview + restaurar
 * - Modo edición activado por URL param ?edit=1
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { toast } from "sonner";
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Clock,
  Edit3,
  Eye,
  Loader2,
  MessageCircle,
  RefreshCw,
  Save,
  Trash2,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { DomainBadge } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import {
  BRAIN_DEFAULT_MODEL,
  BRAIN_MODEL_RECOMMENDATION,
  BRAIN_ROUTES,
  BRAIN_SCOPE_COLORS,
} from "@/lib/brain/constants";
import { cacheKeys } from "@/lib/query-client/cache-keys";
import type { BrainScope } from "@/contracts/types/brain.types";
import { cn } from "@/lib/utils";

const log = brainLogger("BrainWikiDetail");

// ─────────────────────────────────────────────────────────────────────────────
// Monaco editor — carga diferida (incompatible con SSR)
// ─────────────────────────────────────────────────────────────────────────────

const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.default),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center bg-[#1e1e1e]">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    ),
  },
);

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Formato de fecha ISO → "dd MMM yyyy, HH:MM" */
function formatDateTime(dateStr: string): string {
  return new Intl.DateTimeFormat("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(dateStr));
}

/** Modelo recomendado según extensión del source */
function getRecommendedModel(source: string): string {
  const match = source.match(/\.[A-Za-z0-9]{1,10}$/);
  const ext = match?.[0]?.toLowerCase() ?? "";
  return BRAIN_MODEL_RECOMMENDATION[ext] ?? BRAIN_DEFAULT_MODEL;
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-componentes internos
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Preview Markdown con estilos prose para el contenido del pasaporte.
 */
function PassportPreview({ content }: { content: string }) {
  return (
    <div
      className={cn(
        "h-full overflow-y-auto px-8 py-6",
        "prose prose-sm max-w-none",
        "[&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-muted/40 [&_pre]:p-4",
        "[&_code:not(pre_code)]:rounded [&_code:not(pre_code)]:bg-primary/8 [&_code:not(pre_code)]:px-1.5 [&_code:not(pre_code)]:py-0.5 [&_code:not(pre_code)]:text-[0.8em] [&_code:not(pre_code)]:text-primary",
        "[&_a]:text-primary [&_a]:underline-offset-4 [&_a:hover]:text-primary/80",
        "[&_h1]:text-xl [&_h1]:font-bold [&_h1]:text-foreground",
        "[&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-foreground [&_h2]:border-b [&_h2]:border-border/50 [&_h2]:pb-1",
        "[&_h3]:text-sm [&_h3]:font-medium [&_h3]:text-foreground",
        "[&_blockquote]:border-primary/30 [&_blockquote]:text-muted-foreground [&_blockquote]:bg-muted/30 [&_blockquote]:rounded-r-lg [&_blockquote]:py-0.5",
        "[&_table]:w-full [&_thead]:bg-muted/50 [&_tbody_tr:hover]:bg-muted/20 [&_th]:text-foreground [&_td]:text-foreground",
        "[&_hr]:border-border/40",
        "[&_li]:text-foreground [&_p]:text-foreground",
      )}
    >
      {content ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      ) : (
        <p className="text-muted-foreground italic">El pasaporte está vacío.</p>
      )}
    </div>
  );
}

/**
 * Historial de versiones collapsible.
 * Muestra la lista de versiones con fecha y botón de restaurar.
 */
function VersionHistory({
  source,
  searchSpaceId,
  onRestore,
}: {
  source: string;
  searchSpaceId: number;
  onRestore: (content: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [expandedVersion, setExpandedVersion] = useState<string | null>(null);

  const { data: history = [], isLoading } = useQuery({
    queryKey: cacheKeys.brain.passportHistory(source, searchSpaceId),
    queryFn: () => brainApiService.getPassportHistory(source, searchSpaceId),
    enabled: isOpen, // Solo carga cuando está abierto
    staleTime: 60_000,
  });

  return (
    <div className="shrink-0 border-t border-border/50">
      {/* Cabecera collapsible */}
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <span className="flex items-center gap-1.5">
          <Clock className="size-3.5" />
          Historial de versiones
          {history.length > 0 && (
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px]">
              {history.length}
            </span>
          )}
        </span>
        {isOpen ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
      </button>

      {/* Lista de versiones */}
      {isOpen && (
        <div className="border-t border-border/30 max-h-52 overflow-y-auto">
          {isLoading ? (
            <div className="space-y-2 p-3">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : history.length === 0 ? (
            <p className="px-4 py-3 text-xs text-muted-foreground">
              Sin versiones anteriores
            </p>
          ) : (
            <div className="divide-y divide-border/30">
              {history.map((version) => (
                <div key={version.version_id} className="flex flex-col">
                  <div className="flex items-center justify-between gap-2 px-4 py-2">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedVersion(
                            expandedVersion === version.version_id ? null : version.version_id,
                          )
                        }
                        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {expandedVersion === version.version_id ? (
                          <ChevronUp className="size-3" />
                        ) : (
                          <ChevronDown className="size-3" />
                        )}
                        <span className="font-mono text-[10px] opacity-60">
                          {version.version_id.slice(0, 8)}
                        </span>
                        <span>{formatDateTime(version.created_at)}</span>
                      </button>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onRestore(version.content)}
                      className="h-6 px-2 text-[11px] text-blue-400 hover:text-blue-300 hover:bg-blue-500/10"
                    >
                      Restaurar
                    </Button>
                  </div>

                  {/* Preview de versión expandida */}
                  {expandedVersion === version.version_id && (
                    <div className="mx-4 mb-2 rounded-lg border border-border/40 bg-muted/20 p-3 max-h-32 overflow-y-auto">
                      <pre className="whitespace-pre-wrap text-[11px] text-muted-foreground font-mono leading-relaxed">
                        {version.content.slice(0, 500)}
                        {version.content.length > 500 && "…"}
                      </pre>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Página principal
// ─────────────────────────────────────────────────────────────────────────────

export default function BrainWikiDetailPage() {
  const params = useParams<{ search_space_id: string; source: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();

  const searchSpaceId = parseInt(params.search_space_id, 10);
  const source = decodeURIComponent(params.source);
  const recommendedModel = getRecommendedModel(source);

  // Modo edición: activado por URL param ?edit=1 o por botón
  const [isEditing, setIsEditing] = useState(searchParams.get("edit") === "1");
  const [content, setContent] = useState("");
  const [selectedModel, setSelectedModel] = useState(recommendedModel);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  // ── Queries ────────────────────────────────────────────────────────────────

  const {
    data: passport,
    isLoading,
    isError,
  } = useQuery({
    queryKey: cacheKeys.brain.passport(source, searchSpaceId),
    queryFn: () => brainApiService.getPassport(source, searchSpaceId),
    staleTime: 30_000,
  });

  // Inicializar content cuando llegan datos
  useEffect(() => {
    if (passport?.content !== undefined) {
      setContent(passport.content);
    }
  }, [passport?.content]);

  const isDirty = content !== (passport?.content ?? "");

  // ── Mutations ──────────────────────────────────────────────────────────────

  const { mutate: savePassport, isPending: isSaving } = useMutation({
    mutationFn: () =>
      brainApiService.updatePassport(source, content, searchSpaceId),
    onSuccess: () => {
      log.info("Pasaporte guardado", { source });
      toast.success("Pasaporte guardado correctamente");
      queryClient.invalidateQueries({
        queryKey: cacheKeys.brain.passport(source, searchSpaceId),
      });
      queryClient.invalidateQueries({
        queryKey: cacheKeys.brain.passportHistory(source, searchSpaceId),
      });
      setIsEditing(false);
    },
    onError: (err) => {
      log.error("Error guardando pasaporte", { error: String(err) });
      toast.error("Error al guardar el pasaporte");
    },
  });

  const { mutate: resynthesizePassport, isPending: isResynthesizing } = useMutation({
    mutationFn: () =>
      brainApiService.resynthesizePassport(source, searchSpaceId, selectedModel),
    onSuccess: () => {
      log.info("Re-síntesis solicitada", { source, model: selectedModel });
      toast.success("Re-síntesis iniciada. El pasaporte se actualizará en breve.");
    },
    onError: (err) => {
      log.error("Error en re-síntesis", { error: String(err) });
      toast.error("Error al iniciar la re-síntesis");
    },
  });

  const { mutate: deleteDocument, isPending: isDeleting } = useMutation({
    mutationFn: () => brainApiService.deleteDocument(source, searchSpaceId),
    onSuccess: () => {
      log.warn("Pasaporte eliminado", { source });
      toast.success("Documento eliminado");
      queryClient.invalidateQueries({
        queryKey: cacheKeys.brain.list(searchSpaceId),
      });
      router.push(BRAIN_ROUTES.WIKI(params.search_space_id));
    },
    onError: (err) => {
      log.error("Error eliminando pasaporte", { error: String(err) });
      toast.error("Error al eliminar el documento");
    },
  });

  // ── Callbacks ──────────────────────────────────────────────────────────────

  const handleDiscard = useCallback(() => {
    setContent(passport?.content ?? "");
    setIsEditing(false);
  }, [passport?.content]);

  const handleRestore = useCallback((restoredContent: string) => {
    setContent(restoredContent);
    setIsEditing(true);
    toast.info("Versión restaurada en el editor — guarda para confirmar");
  }, []);

  // ── Modelos disponibles para selector re-síntesis ──────────────────────────

  const availableModels = useMemo(() => {
    const models = new Set(Object.values(BRAIN_MODEL_RECOMMENDATION));
    models.add(BRAIN_DEFAULT_MODEL);
    return [...models].sort();
  }, []);

  // ── Render ─────────────────────────────────────────────────────────────────

  if (isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 animate-in fade-in duration-300">
        <p className="text-sm text-muted-foreground">No se pudo cargar el pasaporte</p>
        <Button variant="ghost" size="sm" asChild>
          <Link href={BRAIN_ROUTES.WIKI(params.search_space_id)}>
            <ArrowLeft className="size-3.5 mr-1" />
            Volver a la Wiki
          </Link>
        </Button>
      </div>
    );
  }

  const metadata = passport?.metadata;

  return (
    <>
      <div className="flex h-full flex-col overflow-hidden animate-in fade-in duration-300">
        {/* ── Header ─────────────────────────────────────────────────── */}
        <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border/50 px-4 py-2.5">
          <div className="flex items-center gap-2 min-w-0">
            {/* Breadcrumb */}
            <Link
              href={BRAIN_ROUTES.WIKI(params.search_space_id)}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors shrink-0"
            >
              <ArrowLeft className="size-3.5" />
              Wiki
            </Link>
            <span className="text-muted-foreground/40">/</span>

            {/* Nombre del source */}
            <span className="truncate text-sm font-medium text-foreground">
              {source}
            </span>

            {/* Indicador de cambios no guardados */}
            {isDirty && (
              <span
                className="size-2 shrink-0 rounded-full bg-amber-400"
                title="Cambios sin guardar"
              />
            )}
          </div>

          <div className="flex items-center gap-2">
            {/* Badges de metadata */}
            {!isLoading && metadata && (
              <div className="hidden items-center gap-1.5 sm:flex">
                {metadata.domain && <DomainBadge domain={metadata.domain} />}
                {metadata.scopes.map((scope) => (
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
                {metadata.confidence != null && (
                  <span className="text-xs text-muted-foreground">
                    {Math.round(metadata.confidence * 100)}% conf.
                  </span>
                )}
              </div>
            )}

            <Separator orientation="vertical" className="h-5 hidden sm:block" />

            {/* Acciones */}
            {isEditing ? (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleDiscard}
                  disabled={isSaving}
                  className="h-8 gap-1.5 text-xs"
                >
                  <X className="size-3.5" />
                  Descartar
                </Button>
                <Button
                  size="sm"
                  onClick={() => savePassport()}
                  disabled={isSaving || !isDirty}
                  className="h-8 gap-1.5 bg-violet-600 text-xs text-white hover:bg-violet-500 disabled:bg-muted disabled:text-muted-foreground"
                >
                  {isSaving ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Save className="size-3.5" />
                  )}
                  Guardar
                </Button>
              </>
            ) : (
              <>
                {/* Re-sintetizar con selector de modelo */}
                <div className="flex items-center rounded-lg border border-border overflow-hidden">
                  <Select value={selectedModel} onValueChange={setSelectedModel}>
                    <SelectTrigger className="h-8 w-36 rounded-none border-0 text-xs bg-muted/30 focus:ring-0">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {availableModels.map((model) => (
                        <SelectItem key={model} value={model} className="text-xs">
                          {model}
                          {model === recommendedModel && (
                            <span className="ml-1 text-violet-400">✓</span>
                          )}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => resynthesizePassport()}
                    disabled={isResynthesizing}
                    className="h-8 rounded-none border-l border-border px-2.5 text-xs text-blue-400 hover:text-blue-300 hover:bg-blue-500/10"
                  >
                    {isResynthesizing ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="size-3.5" />
                    )}
                  </Button>
                </div>

                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setIsEditing(true)}
                  className="h-8 gap-1.5 text-xs"
                >
                  <Edit3 className="size-3.5" />
                  Editar
                </Button>

                <Link
                  href={`${BRAIN_ROUTES.CHAT(params.search_space_id)}?q=${encodeURIComponent(`¿Qué sabes sobre ${source}?`)}`}
                  title="Preguntar al Brain sobre este documento"
                  className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs text-violet-400 hover:text-violet-300 hover:bg-violet-500/10 transition-colors"
                >
                  <MessageCircle className="size-3.5" />
                  <span className="hidden sm:inline">Preguntar</span>
                </Link>

                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowDeleteDialog(true)}
                  className="h-8 gap-1.5 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10"
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </>
            )}
          </div>
        </header>

        {/* ── Contenido ───────────────────────────────────────────────── */}
        {isLoading ? (
          <div className="flex-1 p-8 max-w-3xl mx-auto w-full space-y-4">
            <Skeleton className="h-8 w-2/3 rounded-lg" />
            <Skeleton className="h-4 w-full rounded" />
            <Skeleton className="h-4 w-5/6 rounded" />
            <Skeleton className="h-4 w-4/5 rounded" />
            <Skeleton className="h-32 w-full rounded-lg mt-4" />
          </div>
        ) : isEditing ? (
          /* ── Modo edición: split editor + preview ── */
          <div className="flex flex-1 overflow-hidden">
            <div className="flex w-1/2 flex-col border-r border-border/40">
              <div className="flex shrink-0 items-center gap-1.5 border-b border-border/30 px-3 py-1.5 bg-muted/30">
                <Edit3 className="size-3 text-muted-foreground" />
                <span className="text-[11px] text-muted-foreground font-medium">Markdown</span>
                {isDirty && (
                  <span className="ml-auto flex items-center gap-1 text-[10px] text-amber-500">
                    <span className="size-1.5 rounded-full bg-amber-400" />
                    sin guardar
                  </span>
                )}
              </div>
              <div className="flex-1">
                <MonacoEditor
                  defaultLanguage="markdown"
                  theme="vs-dark"
                  value={content}
                  onChange={(value) => setContent(value ?? "")}
                  options={{
                    minimap: { enabled: false },
                    wordWrap: "on",
                    lineNumbers: "on",
                    scrollBeyondLastLine: false,
                    fontSize: 13,
                    fontFamily: "'JetBrains Mono', 'Cascadia Code', 'Fira Code', monospace",
                    padding: { top: 12, bottom: 12 },
                    scrollbar: { verticalScrollbarSize: 6 },
                  }}
                />
              </div>
            </div>
            <div className="flex w-1/2 flex-col">
              <div className="flex shrink-0 items-center gap-1.5 border-b border-border/30 px-3 py-1.5 bg-muted/10">
                <Eye className="size-3 text-muted-foreground" />
                <span className="text-[11px] text-muted-foreground font-medium">Preview en vivo</span>
              </div>
              <PassportPreview content={content} />
            </div>
          </div>
        ) : (
          /* ── Modo vista: preview a pantalla completa ── */
          <div className="flex-1 overflow-hidden">
            <PassportPreview content={content} />
          </div>
        )}

        {/* ── Historial de versiones ──────────────────────────────────── */}
        <VersionHistory
          source={source}
          searchSpaceId={searchSpaceId}
          onRestore={handleRestore}
        />
      </div>

      {/* AlertDialog de eliminación */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Eliminar este documento?</AlertDialogTitle>
            <AlertDialogDescription>
              Se eliminará <strong className="text-foreground">{source}</strong> y su
              pasaporte Brain. Esta acción no se puede deshacer.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteDocument()}
              disabled={isDeleting}
              className="bg-red-600 hover:bg-red-500 focus:ring-red-500"
            >
              {isDeleting ? "Eliminando..." : "Eliminar"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}


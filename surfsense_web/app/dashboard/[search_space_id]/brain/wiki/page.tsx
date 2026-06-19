"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/wiki
 *
 * Wiki semántica — lista de pasaportes Brain con filtros, búsqueda,
 * vista grid/lista y acciones CRUD.
 *
 * Mejoras de diseño sobre el Streamlit original:
 * - Sidebar de filtros estructurado (dominio, tipo doc, tags)
 * - Búsqueda full-text cliente sobre título + tags
 * - Grid de PassportCards con borde por dominio, hover elevado
 * - Toggle grid/lista
 * - Ordenación por importancia / fecha / título
 * - Confirmación antes de eliminar (AlertDialog)
 * - Skeleton mientras carga, empty state con CTA
 */

import { useCallback, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  BookOpen,
  Filter,
  LayoutGrid,
  List,
  Plus,
  RefreshCw,
  Search,
  X,
} from "lucide-react";

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
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { PassportCard } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import {
  BRAIN_DEFAULT_MODEL,
  BRAIN_DOMAIN_CONFIG,
  BRAIN_DOMAINS,
  BRAIN_MODEL_RECOMMENDATION,
  BRAIN_ROUTES,
  BRAIN_WIKI_SORT_OPTIONS,
  type WikiSortOption,
  type WikiViewMode,
} from "@/lib/brain/constants";
import { cacheKeys } from "@/lib/query-client/cache-keys";
import type { BrainDomain, PassportMetadata } from "@/contracts/types/brain.types";
import { cn } from "@/lib/utils";

const log = brainLogger("BrainWiki");

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Extrae la extensión de un source para recomendar modelo */
function getModelForSource(source: string): string {
  const match = source.match(/\.[A-Za-z0-9]{1,10}$/);
  const ext = match?.[0]?.toLowerCase() ?? "";
  return BRAIN_MODEL_RECOMMENDATION[ext] ?? BRAIN_DEFAULT_MODEL;
}

/** Extrae tipos de documento únicos de la lista de pasaportes */
function uniqueDocTypes(passports: PassportMetadata[]): string[] {
  const types = passports.map((p) => p.doc_type).filter(Boolean) as string[];
  return [...new Set(types)].sort();
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-componentes internos
// ─────────────────────────────────────────────────────────────────────────────

/** Skeleton de tarjeta para el estado de carga */
function CardSkeleton() {
  return (
    <div className="flex flex-col rounded-xl border border-border bg-card p-4 space-y-3">
      <div className="flex justify-between">
        <Skeleton className="h-5 w-20 rounded-md" />
        <Skeleton className="h-4 w-4 rounded" />
      </div>
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-3 w-full" />
      <div className="flex gap-1">
        <Skeleton className="h-5 w-12 rounded-full" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <Separator />
      <div className="flex justify-between">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-3 w-14" />
      </div>
    </div>
  );
}

/** Estado vacío cuando no hay pasaportes o no hay resultados de filtro */
function EmptyWikiState({
  isFiltered,
  onClearFilters,
  onIngest,
}: {
  isFiltered: boolean;
  onClearFilters: () => void;
  onIngest: () => void;
}) {
  if (isFiltered) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-20 text-center animate-in fade-in duration-300">
        <Search className="size-10 text-muted-foreground/30" />
        <p className="text-sm font-medium">Sin resultados</p>
        <p className="text-xs text-muted-foreground">
          Ningún pasaporte coincide con los filtros activos
        </p>
        <Button variant="ghost" size="sm" onClick={onClearFilters} className="gap-1.5 mt-1">
          <X className="size-3.5" />
          Limpiar filtros
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 py-20 text-center animate-in fade-in duration-300">
      <div className="flex size-16 items-center justify-center rounded-2xl border border-border bg-muted">
        <BookOpen className="size-7 text-muted-foreground/50" />
      </div>
      <div>
        <p className="text-sm font-medium">La Wiki está vacía</p>
        <p className="text-xs text-muted-foreground mt-1">
          Ingesta documentos para generar sus pasaportes semánticos
        </p>
      </div>
      <Button size="sm" onClick={onIngest} className="gap-1.5 bg-violet-600 hover:bg-violet-500 text-white">
        <Plus className="size-3.5" />
        Ingestar documentos
      </Button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Página principal
// ─────────────────────────────────────────────────────────────────────────────

export default function BrainWikiPage() {
  const params = useParams<{ search_space_id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const searchSpaceId = parseInt(params.search_space_id, 10);

  // Filtros y vista
  const [search, setSearch] = useState("");
  const [selectedDomains, setSelectedDomains] = useState<Set<BrainDomain>>(new Set());
  const [selectedDocTypes, setSelectedDocTypes] = useState<Set<string>>(new Set());
  const [sortBy, setSortBy] = useState<WikiSortOption>("importance");
  const [viewMode, setViewMode] = useState<WikiViewMode>("grid");
  const [showFilters, setShowFilters] = useState(true);

  // Estado para confirmación de eliminación
  const [deleteSource, setDeleteSource] = useState<string | null>(null);

  // ── Queries ────────────────────────────────────────────────────────────────

  const {
    data: passports = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: cacheKeys.brain.list(searchSpaceId),
    queryFn: () => brainApiService.listPassports(searchSpaceId),
    staleTime: 30_000,
  });

  // ── Mutations ──────────────────────────────────────────────────────────────

  const { mutate: deletePassport, isPending: isDeleting } = useMutation({
    mutationFn: (source: string) =>
      brainApiService.deleteDocument(source, searchSpaceId),
    onSuccess: (_, source) => {
      log.info("Pasaporte eliminado", { source });
      toast.success("Documento eliminado correctamente");
      queryClient.invalidateQueries({ queryKey: cacheKeys.brain.list(searchSpaceId) });
      setDeleteSource(null);
    },
    onError: (err, source) => {
      log.error("Error eliminando pasaporte", { source, error: String(err) });
      toast.error("Error al eliminar el documento");
    },
  });

  const { mutate: resynthesizePassport } = useMutation({
    mutationFn: (source: string) =>
      brainApiService.resynthesizePassport(source, searchSpaceId, getModelForSource(source)),
    onSuccess: (_, source) => {
      log.info("Re-síntesis solicitada", { source });
      toast.success("Re-síntesis iniciada. El pasaporte se actualizará en breve.");
    },
    onError: (err, source) => {
      log.error("Error en re-síntesis", { source, error: String(err) });
      toast.error("Error al iniciar la re-síntesis");
    },
  });

  // ── Filtrado y ordenación cliente ──────────────────────────────────────────

  const docTypes = useMemo(() => uniqueDocTypes(passports), [passports]);
  const isFiltered =
    search.trim().length > 0 ||
    selectedDomains.size > 0 ||
    selectedDocTypes.size > 0;

  const filteredPassports = useMemo(() => {
    const q = search.toLowerCase().trim();

    return passports
      .filter((p) => {
        if (q && !p.title.toLowerCase().includes(q) && !p.tags.some((t) => t.toLowerCase().includes(q)))
          return false;
        if (selectedDomains.size > 0 && (!p.domain || !selectedDomains.has(p.domain as BrainDomain)))
          return false;
        if (selectedDocTypes.size > 0 && (!p.doc_type || !selectedDocTypes.has(p.doc_type)))
          return false;
        return true;
      })
      .sort((a, b) => {
        if (sortBy === "importance") return (b.importance ?? 0) - (a.importance ?? 0);
        if (sortBy === "date")
          return new Date(b.updated_at ?? 0).getTime() - new Date(a.updated_at ?? 0).getTime();
        if (sortBy === "title") return a.title.localeCompare(b.title, "es");
        return 0;
      });
  }, [passports, search, selectedDomains, selectedDocTypes, sortBy]);

  // ── Callbacks ──────────────────────────────────────────────────────────────

  const handleClearFilters = useCallback(() => {
    setSearch("");
    setSelectedDomains(new Set());
    setSelectedDocTypes(new Set());
  }, []);

  const toggleDomain = useCallback((domain: BrainDomain) => {
    setSelectedDomains((prev) => {
      const next = new Set(prev);
      next.has(domain) ? next.delete(domain) : next.add(domain);
      return next;
    });
  }, []);

  const toggleDocType = useCallback((type: string) => {
    setSelectedDocTypes((prev) => {
      const next = new Set(prev);
      next.has(type) ? next.delete(type) : next.add(type);
      return next;
    });
  }, []);

  // ── Render ─────────────────────────────────────────────────────────────────

  if (isError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-20 animate-in fade-in duration-300">
        <p className="text-sm text-muted-foreground">Error al cargar los pasaportes</p>
        <Button variant="ghost" size="sm" onClick={() => refetch()} className="gap-1.5">
          <RefreshCw className="size-3.5" />
          Reintentar
        </Button>
      </div>
    );
  }

  return (
    <>
      <div className="flex h-full flex-col overflow-hidden animate-in fade-in duration-300">
        {/* ── Toolbar ────────────────────────────────────────────────── */}
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border/50 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <BookOpen className="size-4 text-muted-foreground" />
            <h1 className="text-sm font-medium">Wiki semántica</h1>
            {!isLoading && (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                {passports.length}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {/* Buscador */}
            <div className="relative w-52">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground pointer-events-none" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Buscar..."
                className="h-8 pl-8 text-xs bg-muted/30"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>

            {/* Ordenación */}
            <Select value={sortBy} onValueChange={(v) => setSortBy(v as WikiSortOption)}>
              <SelectTrigger className="h-8 w-36 text-xs bg-muted/30">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BRAIN_WIKI_SORT_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value} className="text-xs">
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Toggle grid / lista */}
            <div className="flex items-center rounded-lg border border-border bg-muted/30 p-0.5">
              <button
                type="button"
                onClick={() => setViewMode("grid")}
                aria-pressed={viewMode === "grid"}
                className={cn(
                  "rounded-md p-1.5 transition-colors",
                  viewMode === "grid" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                )}
                aria-label="Vista cuadrícula"
              >
                <LayoutGrid className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setViewMode("list")}
                aria-pressed={viewMode === "list"}
                className={cn(
                  "rounded-md p-1.5 transition-colors",
                  viewMode === "list" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                )}
                aria-label="Vista lista"
              >
                <List className="size-3.5" />
              </button>
            </div>

            {/* Toggle filtros */}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowFilters((v) => !v)}
              className={cn(
                "h-8 gap-1.5 px-2.5 text-xs",
                showFilters ? "bg-violet-500/10 text-violet-300 hover:bg-violet-500/15" : "",
                isFiltered && !showFilters ? "text-amber-300" : "",
              )}
            >
              <Filter className="size-3.5" />
              Filtros
              {isFiltered && <span className="size-1.5 rounded-full bg-amber-400" />}
            </Button>

            {/* Ingestar */}
            <Button
              size="sm"
              onClick={() => router.push(BRAIN_ROUTES.INGEST(params.search_space_id))}
              className="h-8 gap-1.5 bg-violet-600 text-xs text-white hover:bg-violet-500"
            >
              <Plus className="size-3.5" />
              Ingestar
            </Button>
          </div>
        </div>

        {/* ── Cuerpo: sidebar filtros + grid cards ───────────────────── */}
        <div className="flex flex-1 overflow-hidden">
          {/* Sidebar de filtros */}
          {showFilters && (
            <aside className="hidden w-52 shrink-0 flex-col gap-4 overflow-y-auto border-r border-border/40 px-3 py-4 lg:flex">
              {/* Filtro dominio */}
              <div className="space-y-2">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Dominio
                </p>
                <div className="space-y-1">
                  {BRAIN_DOMAINS.map((domain) => {
                    const cfg = BRAIN_DOMAIN_CONFIG[domain];
                    const checked = selectedDomains.has(domain);
                    return (
                      <button
                        key={domain}
                        type="button"
                        onClick={() => toggleDomain(domain)}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors",
                          checked
                            ? "bg-muted text-foreground"
                            : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                        )}
                      >
                        <span
                          className={cn(
                            "flex size-3.5 shrink-0 items-center justify-center rounded border transition-colors",
                            checked ? "border-violet-500 bg-violet-500/20" : "border-border",
                          )}
                        >
                          {checked && <span className="size-1.5 rounded-sm bg-violet-400" />}
                        </span>
                        <span
                          className={cn(
                            "inline-block size-2 shrink-0 rounded-full",
                            cfg.classes.split(" ").find((c) => c.startsWith("bg-")),
                          )}
                        />
                        {cfg.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Filtro tipo de documento */}
              {docTypes.length > 0 && (
                <div className="space-y-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Tipo de doc
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {docTypes.map((type) => {
                      const active = selectedDocTypes.has(type);
                      return (
                        <button
                          key={type}
                          type="button"
                          onClick={() => toggleDocType(type)}
                          className={cn(
                            "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                            active
                              ? "border-violet-500/50 bg-violet-500/15 text-violet-300"
                              : "border-border bg-muted text-muted-foreground hover:border-violet-500/30 hover:bg-violet-500/5",
                          )}
                        >
                          {type}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Limpiar filtros */}
              {isFiltered && (
                <button
                  type="button"
                  onClick={handleClearFilters}
                  className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <X className="size-3" />
                  Limpiar filtros
                </button>
              )}
            </aside>
          )}

          {/* Área principal de cards */}
          <main className="flex-1 overflow-y-auto p-4">
            {isLoading ? (
              <div
                className={cn(
                  "grid gap-4",
                  viewMode === "grid"
                    ? "grid-cols-1 sm:grid-cols-2 xl:grid-cols-3"
                    : "grid-cols-1",
                )}
              >
                {Array.from({ length: 6 }, (_, i) => <CardSkeleton key={i} />)}
              </div>
            ) : filteredPassports.length === 0 ? (
              <EmptyWikiState
                isFiltered={isFiltered}
                onClearFilters={handleClearFilters}
                onIngest={() => router.push(BRAIN_ROUTES.INGEST(params.search_space_id))}
              />
            ) : (
              <>
                {/* Contador de resultados cuando hay filtro activo */}
                {isFiltered && (
                  <p className="mb-3 text-xs text-muted-foreground">
                    {filteredPassports.length} resultado{filteredPassports.length !== 1 ? "s" : ""}
                  </p>
                )}
                <div
                  className={cn(
                    "grid gap-4",
                    viewMode === "grid"
                      ? "grid-cols-1 sm:grid-cols-2 xl:grid-cols-3"
                      : "grid-cols-1 max-w-3xl",
                  )}
                >
                  {filteredPassports.map((passport) => (
                    <PassportCard
                      key={passport.source}
                      passport={passport}
                      searchSpaceId={params.search_space_id}
                      onDelete={setDeleteSource}
                      onResynthesize={(source) => resynthesizePassport(source)}
                    />
                  ))}
                </div>
              </>
            )}
          </main>
        </div>
      </div>

      {/* ── AlertDialog de confirmación de eliminación ──────────────── */}
      <AlertDialog
        open={deleteSource !== null}
        onOpenChange={(open) => !open && setDeleteSource(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Eliminar este documento?</AlertDialogTitle>
            <AlertDialogDescription>
              Se eliminará <strong className="text-foreground">{deleteSource}</strong> y su
              pasaporte Brain. Esta acción no se puede deshacer.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteSource && deletePassport(deleteSource)}
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


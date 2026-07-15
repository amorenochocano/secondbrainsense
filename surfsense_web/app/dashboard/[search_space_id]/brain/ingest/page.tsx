"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/ingest
 *
 * F6.8 — Ingesta avanzada: URL · Fichero · Ruta local.
 * Selector de modelos cargado desde Ollama en tiempo real.
 */

import { useState, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Link2,
  Sparkles,
  ArrowRight,
  FileText,
  Brain,
  Database,
  CheckCircle2,
  Clock,
  ExternalLink,
  Loader2,
  FolderOpen,
  Upload,
  Server,
  Plug,
  RefreshCw,
  AlertCircle,
  BookMarked,
  Ticket,
  Settings,
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
import { toast } from "sonner";
import { BrainBreadcrumb, DomainBadge, IngestPhaseLog } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import { cacheKeys } from "@/lib/query-client/cache-keys";
import {
  BRAIN_ROUTES,
  BRAIN_MODEL_RECOMMENDATION_BY_EXT,
  BRAIN_DEFAULT_MODEL,
  BRAIN_INGEST_CATEGORIES,
  BRAIN_INGEST_EXTENSION_CATEGORY,
  type IngestCategory,
} from "@/lib/brain/constants";
import type { AvailableConnector, NativeConnectorStatus } from "@/contracts/types/brain.types";
import {
  ConnectorIndicator,
  type ConnectorIndicatorHandle,
} from "@/components/assistant-ui/connector-popup";

const log = brainLogger("BrainIngestPage");

// ─── Utilidades ───────────────────────────────────────────────────────────────

function extractExtension(input: string): string | null {
  try {
    const pathname = input.startsWith("http") ? new URL(input).pathname : input;
    const match = pathname.match(/\.([a-zA-Z0-9]{1,10})(?:[?#]|$)/);
    return match ? `.${match[1].toLowerCase()}` : null;
  } catch {
    return null;
  }
}

function getCategoryForExtension(ext: string | null, isUrl = false): IngestCategory {
  if (ext) return BRAIN_INGEST_EXTENSION_CATEGORY[ext] ?? "A";
  if (isUrl) return "B";
  return "C";
}

function isValidHttpUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("es-ES", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function getModelForExt(ext: string | null): string {
  if (!ext) return BRAIN_DEFAULT_MODEL;
  return BRAIN_MODEL_RECOMMENDATION_BY_EXT[ext] ?? BRAIN_DEFAULT_MODEL;
}

// ─── Sub-componente: selector de modelo desde Ollama ─────────────────────────

interface ModelSelectorProps {
  value: string;
  onValueChange: (v: string) => void;
  disabled?: boolean;
  recommendedModel?: string;
}

function ModelSelector({ value, onValueChange, disabled, recommendedModel }: ModelSelectorProps) {
  const { data: ollamaData, isLoading } = useQuery({
    queryKey: ["ollama-models"],
    queryFn: () => brainApiService.getOllamaModels(),
    staleTime: 60_000,
  });

  const models = ollamaData?.models ?? [];
  const recommended = recommendedModel ?? BRAIN_DEFAULT_MODEL;

  return (
    <div className="flex items-center gap-2 flex-1 min-w-48">
      <Select value={value} onValueChange={onValueChange} disabled={disabled || isLoading}>
        <SelectTrigger className="bg-muted/50 border-input text-sm text-foreground">
          <SelectValue placeholder="Selecciona modelo…" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="auto">
            <span className="flex items-center gap-2">
              <Sparkles className="h-3.5 w-3.5 text-violet-400" />
              Auto — {recommended}
            </span>
          </SelectItem>
          {isLoading && (
            <SelectItem value="__loading__" disabled>
              <span className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Cargando modelos…
              </span>
            </SelectItem>
          )}
          {models.map((m) => (
            <SelectItem key={m.name} value={m.name}>
              <span className="flex items-center gap-2">
                <Brain className="h-3.5 w-3.5 text-violet-400" />
                {m.name}
                {m.name === recommended && (
                  <span className="text-xs text-violet-400 ml-1">recomendado</span>
                )}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

// ─── Sub-componente: badge de categoría pipeline ──────────────────────────────

function CategoryBadge({ category }: { category: IngestCategory }) {
  const cfg = BRAIN_INGEST_CATEGORIES[category];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold",
        cfg.classes,
      )}
    >
      <span className="opacity-60 font-mono">{category}</span>
      {cfg.shortLabel}
    </span>
  );
}

// ─── Sub-componente: banner de éxito ─────────────────────────────────────────

function SuccessBanner({ spaceId, onNewIngest, onGoMonitor }: {
  spaceId: string; onNewIngest: () => void; onGoMonitor: () => void;
}) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
      <span className="flex items-center gap-2 text-sm text-emerald-300">
        <CheckCircle2 className="h-4 w-4" />
        Documento disponible en el Brain
      </span>
      <div className="flex gap-2">
        <Button
          variant="outline" size="sm" onClick={onGoMonitor}
          className="border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 gap-1.5"
        >
          <Database className="h-3.5 w-3.5" /> Ver en monitor
        </Button>
        <Button
          variant="outline" size="sm" onClick={onNewIngest}
          className="border-border00 text-foreground/80 gap-1.5"
        >
          <ArrowRight className="h-3.5 w-3.5" /> Ingestar otro
        </Button>
      </div>
    </div>
  );
}

// ─── Sub-componente: tabla del monitor ───────────────────────────────────────

function MonitorTable({ spaceId, onOpenWiki }: {
  spaceId: string; onOpenWiki: (source: string) => void;
}) {
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
    return <p className="text-center py-10 text-sm text-red-400">Error al cargar los documentos.</p>;
  }

  const passports = data ?? [];
  if (passports.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-4 text-center">
        <Database className="h-10 w-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Sin documentos ingestados aún.</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-muted-foreground uppercase tracking-wider">
            <th className="text-left pb-3 pr-4 font-medium">Documento</th>
            <th className="text-left pb-3 pr-4 font-medium">Dominio</th>
            <th className="text-left pb-3 pr-4 font-medium">Categoría</th>
            <th className="text-left pb-3 pr-4 font-medium">Actualizado</th>
            <th className="pb-3 font-medium" />
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {passports.map((passport) => {
            const ext = extractExtension(passport.source);
            const isUrl = passport.source.startsWith("http");
            const category = getCategoryForExtension(ext, isUrl);
            return (
              <tr key={passport.source} className="group hover:bg-muted/40 transition-colors">
                <td className="py-3 pr-4">
                  <div className="flex items-center gap-2 max-w-xs">
                    <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate text-foreground font-medium" title={passport.source}>
                      {passport.source.split("/").pop() ?? passport.source}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground truncate max-w-xs mt-0.5 pl-[22px]" title={passport.source}>
                    {passport.source}
                  </p>
                </td>
                <td className="py-3 pr-4"><DomainBadge domain={passport.domain} size="sm" /></td>
                <td className="py-3 pr-4"><CategoryBadge category={category} /></td>
                <td className="py-3 pr-4">
                  <span className="flex items-center gap-1 text-xs text-muted-foreground whitespace-nowrap">
                    <Clock className="h-3 w-3" />
                    {formatDate(passport.updated_at)}
                  </span>
                </td>
                <td className="py-3">
                  <button
                    type="button"
                    onClick={() => onOpenWiki(passport.source)}
                    className="flex items-center gap-1 text-xs text-violet-400 hover:text-violet-300 transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <ExternalLink className="h-3 w-3" /> Wiki
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

// ─── Sub-componente: badge de formato ────────────────────────────────────────

function FormatBadge({ isKnown }: { isKnown: boolean }) {
  return isKnown ? (
    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">
      <CheckCircle2 className="h-2.5 w-2.5" /> Extractor nativo
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-400">
      <AlertCircle className="h-2.5 w-2.5" /> Fallback
    </span>
  );
}

// ─── Sub-componente: formulario de ítem nativo ───────────────────────────────

interface NativeItemFormProps {
  connectorType: string;
  status: NativeConnectorStatus;
  spaceId: string;
  selectedModel: string;
  isIngesting: boolean;
  onStartJob: (jobId: string) => void;
  icon: React.ReactNode;
  placeholder: string;
  filenameHint: string;
}

function NativeItemForm({
  connectorType, status, spaceId, selectedModel, isIngesting,
  onStartJob, icon, placeholder, filenameHint,
}: NativeItemFormProps) {
  const [itemId, setItemId] = useState("");
  const [filename, setFilename] = useState("");

  const ingestMutation = useMutation({
    mutationFn: () => {
      if (!itemId.trim())   throw new Error("Introduce el ID del ítem");
      if (!filename.trim()) throw new Error("Introduce el nombre del fichero");
      return brainApiService.ingestNative(connectorType, {
        item_id:         itemId.trim(),
        filename:        filename.trim(),
        search_space_id: Number(spaceId),
        model:           selectedModel === "auto" ? undefined : selectedModel,
      });
    },
    onSuccess: (res) => { onStartJob(res.job_id); },
    onError: (err: Error) => toast.error(`Error al iniciar ingesta de ${status.label}`, { description: err.message }),
  });

  const canIngest = status.configured && !!itemId.trim() && !!filename.trim() && !isIngesting && !ingestMutation.isPending;

  return (
    <div className="rounded-xl border border-border bg-card p-5 space-y-4">
      {/* Cabecera con estado */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {icon}
          <h3 className="text-sm font-semibold text-foreground">{status.label}</h3>
        </div>
        {status.configured ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">
            <CheckCircle2 className="h-2.5 w-2.5" /> Configurado
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-400">
            <AlertCircle className="h-2.5 w-2.5" /> No configurado
          </span>
        )}
      </div>

      {/* Aviso si no está configurado */}
      {!status.configured && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 space-y-1">
          <p className="text-xs text-amber-300 font-medium flex items-center gap-1.5">
            <Settings className="h-3 w-3 shrink-0" />
            El administrador debe configurar las siguientes variables en <code className="font-mono">.env</code>:
          </p>
          <ul className="ml-4 space-y-0.5">
            {status.missing_env_vars.map((v) => (
              <li key={v} className="text-[11px] font-mono text-amber-400">{v}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Formulario — solo visible si está configurado */}
      {status.configured && (
        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{status.item_label}</label>
            <Input
              placeholder={placeholder}
              value={itemId}
              onChange={(e) => setItemId(e.target.value)}
              className="font-mono text-sm bg-muted/50 border-input focus:border-violet-500 text-foreground placeholder:text-muted-foreground/60"
              disabled={isIngesting}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Nombre de fichero destino</label>
            <Input
              placeholder={filenameHint}
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              className="font-mono text-sm bg-muted/50 border-input focus:border-violet-500 text-foreground placeholder:text-muted-foreground/60"
              disabled={isIngesting}
            />
          </div>
          <Button
            onClick={() => ingestMutation.mutate()}
            disabled={!canIngest}
            className="gap-2 bg-violet-600 hover:bg-violet-500 text-white"
          >
            {ingestMutation.isPending ? <><Loader2 className="h-4 w-4 animate-spin" /> Iniciando…</> :
             isIngesting ? <><Loader2 className="h-4 w-4 animate-spin" /> Procesando…</> :
             <><ArrowRight className="h-4 w-4" /> Ingestar</>}
          </Button>
        </div>
      )}
    </div>
  );
}

// ─── Sub-componente: tab Fuentes estructuradas (Jira / Confluence) ────────────

interface NativeSourcesTabProps {
  spaceId: string;
  selectedModel: string;
  isIngesting: boolean;
  onStartJob: (jobId: string) => void;
}

function NativeSourcesTab({ spaceId, selectedModel, isIngesting, onStartJob }: NativeSourcesTabProps) {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["brain", "native-connectors-status"],
    queryFn: () => brainApiService.getNativeConnectorsStatus(),
    staleTime: 120_000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 gap-2 text-muted-foreground text-sm">
        <Loader2 className="h-4 w-4 animate-spin" /> Comprobando configuración…
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-3 text-center">
        <AlertCircle className="h-6 w-6 text-red-400" />
        <p className="text-sm text-red-400">Error al obtener el estado de las fuentes.</p>
        <Button variant="outline" size="sm" onClick={() => refetch()} className="gap-1.5">
          <RefreshCw className="h-3.5 w-3.5" /> Reintentar
        </Button>
      </div>
    );
  }

  const connectors = data?.connectors ?? {};

  return (
    <div className="space-y-4">
      {/* Descripción */}
      <div className="rounded-xl border border-border/60 bg-muted/20 px-4 py-3">
        <p className="text-xs text-muted-foreground leading-relaxed">
          <span className="font-medium text-foreground/80">Fuentes estructuradas</span> — ingesta contenido de Jira y Confluence directamente al Brain.
          El contenido se descarga, sintetiza y queda disponible para búsqueda semántica en el chat.
          <br />
          <span className="text-[10px] mt-1 block text-muted-foreground/70">
            Estas fuentes usan credenciales REST API configuradas por el administrador. Son independientes de los conectores de chat (que usan OAuth MCP).
          </span>
        </p>
        <div className="flex justify-end mt-2">
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground/80 transition-colors"
          >
            <RefreshCw className={cn("h-2.5 w-2.5", isFetching && "animate-spin")} />
            Actualizar estado
          </button>
        </div>
      </div>

      {/* Jira */}
      {connectors["JIRA_CONNECTOR"] && (
        <NativeItemForm
          connectorType="JIRA_CONNECTOR"
          status={connectors["JIRA_CONNECTOR"]}
          spaceId={spaceId}
          selectedModel={selectedModel}
          isIngesting={isIngesting}
          onStartJob={onStartJob}
          icon={<Ticket className="h-4 w-4 text-blue-400" />}
          placeholder="TEC-123"
          filenameHint="tec-123-descripcion.md"
        />
      )}

      {/* Confluence */}
      {connectors["CONFLUENCE_CONNECTOR"] && (
        <NativeItemForm
          connectorType="CONFLUENCE_CONNECTOR"
          status={connectors["CONFLUENCE_CONNECTOR"]}
          spaceId={spaceId}
          selectedModel={selectedModel}
          isIngesting={isIngesting}
          onStartJob={onStartJob}
          icon={<BookMarked className="h-4 w-4 text-blue-400" />}
          placeholder="123456  o  Título de la página"
          filenameHint="nombre-pagina.md"
        />
      )}
    </div>
  );
}

// ─── Sub-componente: tab Conector ────────────────────────────────────────────

interface ConnectorTabProps {
  spaceId: string;
  selectedModel: string;
  isIngesting: boolean;
  onStartJob: (jobId: string) => void;
}

function ConnectorTab({ spaceId, selectedModel, isIngesting, onStartJob }: ConnectorTabProps) {
  const [selectedConnector, setSelectedConnector] = useState<AvailableConnector | null>(null);
  const [itemId, setItemId] = useState("");
  const [filename, setFilename] = useState("");
  const [isKnownFormat, setIsKnownFormat] = useState<boolean | null>(null);
  const connectorDialogRef = useRef<ConnectorIndicatorHandle>(null);

  const { data: connectors, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["brain", "connectors-available", Number(spaceId)],
    queryFn: () => brainApiService.getAvailableConnectors(Number(spaceId)),
    staleTime: 60_000,
  });

  // Inferir is_known del nombre de fichero introducido
  const handleFilenameChange = (value: string) => {
    setFilename(value);
    const ext = extractExtension(value);
    if (ext) {
      // Estimamos conocido si tiene extractor nativo (PDF, DOCX, MD, etc.)
      // La lista de extensiones conocidas la inferimos por las más comunes.
      // El backend devolverá el estado real — aquí solo es indicativo.
      const KNOWN_EXTS = new Set([
        ".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm", ".py", ".sql",
        ".ipynb", ".xml", ".drawio", ".json", ".md", ".markdown", ".csv", ".txt",
        ".confluence", ".jira_ticket", ".github_file",
      ]);
      setIsKnownFormat(KNOWN_EXTS.has(ext));
    } else {
      setIsKnownFormat(null);
    }
  };

  const ingestMutation = useMutation({
    mutationFn: () => {
      if (!selectedConnector) throw new Error("Selecciona un conector");
      if (!itemId.trim())     throw new Error("Introduce el ID del ítem");
      if (!filename.trim())   throw new Error("Introduce el nombre del fichero");
      return brainApiService.ingestConnector(selectedConnector.connector_type, {
        connector_id:    selectedConnector.connector_id,
        item_id:         itemId.trim(),
        filename:        filename.trim(),
        search_space_id: Number(spaceId),
        model:           selectedModel === "auto" ? undefined : selectedModel,
      });
    },
    onSuccess: (res) => { onStartJob(res.job_id); },
    onError: (err: Error) => toast.error("Error al iniciar ingesta de conector", { description: err.message }),
  });

  const canIngest = !!selectedConnector && !!itemId.trim() && !!filename.trim() && !isIngesting && !ingestMutation.isPending;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 gap-2 text-muted-foreground text-sm">
        <Loader2 className="h-4 w-4 animate-spin" /> Cargando conectores…
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-3 text-center">
        <AlertCircle className="h-6 w-6 text-red-400" />
        <p className="text-sm text-red-400">Error al cargar los conectores.</p>
        <Button variant="outline" size="sm" onClick={() => refetch()} className="gap-1.5 border-border00 text-foreground/80">
          <RefreshCw className="h-3.5 w-3.5" /> Reintentar
        </Button>
      </div>
    );
  }

  // Solo conectores de almacenamiento (OneDrive, Drive, Dropbox).
  // Jira y Confluence tienen su propia pestaña "Fuentes" con credenciales nativas.
  const storageConnectors = (connectors?.connectors ?? []).filter((c) => c.family === "storage");
  const available = storageConnectors.filter((c) => c.ok);
  const unavailable = storageConnectors.filter((c) => !c.ok);

  return (
    <div className="space-y-4">
      {/* ConnectorIndicator montado aquí — invisible, solo expone open() vía ref */}
      <ConnectorIndicator ref={connectorDialogRef} />

      {/* Selector de conector */}
      <div className="rounded-xl border border-border bg-card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Plug className="h-4 w-4 text-violet-400" />
            <h2 className="text-sm font-semibold text-foreground">Almacenamiento en la nube</h2>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => refetch()}
              disabled={isFetching}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground/80 transition-colors"
            >
              <RefreshCw className={cn("h-3 w-3", isFetching && "animate-spin")} />
              Actualizar
            </button>
            <button
              type="button"
              onClick={() => connectorDialogRef.current?.open()}
              className="flex items-center gap-1 text-xs text-violet-400 hover:text-violet-300 transition-colors"
            >
              <Plug className="h-3 w-3" />
              Gestionar
            </button>
          </div>
        </div>

        {available.length === 0 && unavailable.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-6 text-center">
            <Plug className="h-8 w-8 text-muted-foreground/40" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground/70">Sin conectores de almacenamiento</p>
              <p className="text-xs text-muted-foreground">Conecta OneDrive, Google Drive o Dropbox para ingestar ficheros desde la nube.</p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => connectorDialogRef.current?.open()}
              className="gap-1.5 border-violet-500/40 text-violet-400 hover:text-violet-300 hover:border-violet-400/60 hover:bg-violet-500/10"
            >
              <Plug className="h-3.5 w-3.5" />
              Configurar conector
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            {available.map((c) => (
              <button
                key={c.connector_id}
                type="button"
                onClick={() => { setSelectedConnector(c); setItemId(""); setFilename(""); setIsKnownFormat(null); }}
                className={cn(
                  "w-full flex items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors",
                  selectedConnector?.connector_id === c.connector_id
                    ? "border-violet-500/60 bg-violet-500/10 text-foreground"
                    : "border-border/60 bg-muted/30 text-foreground/80 hover:border-border hover:bg-muted/40",
                )}
              >
                <div className="flex items-center gap-2">
                  <Plug className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  <span className="font-medium">{c.name}</span>
                  <span className="text-[10px] text-muted-foreground font-mono">{c.connector_type}</span>
                </div>
                <Badge variant="outline" className="text-[10px] px-1.5 border-border00 text-muted-foreground">
                  {c.family}
                </Badge>
              </button>
            ))}
            {unavailable.map((c) => (
              <div
                key={c.connector_id}
                className="w-full flex items-center justify-between rounded-lg border border-border/40 bg-muted/20 px-3 py-2 text-sm opacity-50"
              >
                <div className="flex items-center gap-2">
                  <Plug className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  <span className="text-muted-foreground">{c.name}</span>
                </div>
                <span className="text-[10px] text-red-400">requiere reautenticación</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Formulario de ítem — visible solo cuando hay conector seleccionado */}
      {selectedConnector && (
        <div className="rounded-xl border border-border bg-card p-5 space-y-4">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-violet-400" />
            <h2 className="text-sm font-semibold text-foreground">
              Ítem a ingestar
              <span className="ml-2 text-xs font-normal text-muted-foreground font-mono">{selectedConnector.name}</span>
            </h2>
          </div>

          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">ID del ítem (file_id, item_id, thread_id…)</label>
              <Input
                placeholder="abc123-def456"
                value={itemId}
                onChange={(e) => setItemId(e.target.value)}
                className="font-mono text-sm bg-muted/50 border-input focus:border-violet-500 text-foreground placeholder:text-muted-foreground/60"
                disabled={isIngesting}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Nombre de fichero (con extensión)</label>
              <div className="flex items-center gap-2">
                <Input
                  placeholder="documento.pdf"
                  value={filename}
                  onChange={(e) => handleFilenameChange(e.target.value)}
                  className="font-mono text-sm bg-muted/50 border-input focus:border-violet-500 text-foreground placeholder:text-muted-foreground/60"
                  disabled={isIngesting}
                />
                {isKnownFormat !== null && <FormatBadge isKnown={isKnownFormat} />}
              </div>
              <p className="text-[10px] text-muted-foreground mt-1">
                Badge verde: extractor nativo. Badge amarillo: fallback (se procesará como texto o metadata binaria).
              </p>
            </div>
          </div>

          <Button
            onClick={() => ingestMutation.mutate()}
            disabled={!canIngest}
            className="gap-2 bg-violet-600 hover:bg-violet-500 text-white"
          >
            {ingestMutation.isPending ? <><Loader2 className="h-4 w-4 animate-spin" /> Iniciando…</> :
             isIngesting ? <><Loader2 className="h-4 w-4 animate-spin" /> Procesando…</> :
             <><ArrowRight className="h-4 w-4" /> Ingestar ítem</>}
          </Button>
        </div>
      )}
    </div>
  );
}

// ─── Página principal ─────────────────────────────────────────────────────────

export default function BrainIngestPage() {
  const params       = useParams<{ search_space_id: string }>();
  const spaceId      = params.search_space_id;
  const router       = useRouter();
  const queryClient  = useQueryClient();

  // ── Estado compartido ────────────────────────────────────────────────────────
  const [selectedModel, setSelectedModel] = useState("auto");
  const [activeJobId,   setActiveJobId]   = useState<string | null>(null);
  const [isIngesting,   setIsIngesting]   = useState(false);
  const [ingestDone,    setIngestDone]    = useState(false);
  const [activeTab,     setActiveTab]     = useState("url");
  const [mainTab,       setMainTab]       = useState("ingest");

  // ── URL tab ──────────────────────────────────────────────────────────────────
  const [url, setUrl] = useState("");
  const urlValid   = isValidHttpUrl(url);
  const urlExt     = extractExtension(url);
  const urlRecoMod = getModelForExt(urlExt);

  // ── Fichero tab ──────────────────────────────────────────────────────────────
  const [dragOver,   setDragOver]   = useState(false);
  const [droppedFile, setDroppedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Ruta local tab ───────────────────────────────────────────────────────────
  const [localPath, setLocalPath] = useState("");
  const pathExt     = extractExtension(localPath);
  const pathRecoMod = getModelForExt(pathExt);

  // ── Lista de documentos ──────────────────────────────────────────────────────
  const { data: passportList } = useQuery({
    queryKey: cacheKeys.brain.list(Number(spaceId)),
    queryFn: () => brainApiService.listPassports(Number(spaceId)),
    staleTime: 30_000,
  });
  const passportCount = passportList?.length ?? 0;

  // ── Helpers de estado compartido ─────────────────────────────────────────────
  const startJob = (jobId: string) => {
    setActiveJobId(jobId);
    setIsIngesting(true);
    setIngestDone(false);
  };

  const handlePipelineComplete = useCallback(() => {
    setIsIngesting(false);
    setIngestDone(true);
    queryClient.invalidateQueries({ queryKey: cacheKeys.brain.list(Number(spaceId)) });
    toast("Ingesta completada", { description: "Documento disponible en el Brain." });
  }, [queryClient, spaceId]);

  const handlePipelineError = useCallback((err: Error) => {
    setIsIngesting(false);
    toast.error("Error en el pipeline", { description: err.message });
  }, []);

  const resetIngest = () => {
    setActiveJobId(null);
    setIngestDone(false);
    setIsIngesting(false);
  };

  // ── Mutación URL ─────────────────────────────────────────────────────────────
  const urlMutation = useMutation({
    mutationFn: () =>
      brainApiService.ingestUrl(
        url,
        Number(spaceId),
        selectedModel === "auto" ? undefined : selectedModel,
      ),
    onSuccess: (res) => { startJob(res.job_id); },
    onError: (err: Error) => toast.error("Error al iniciar ingesta URL", { description: err.message }),
  });

  // ── Mutación Fichero ─────────────────────────────────────────────────────────
  const fileMutation = useMutation({
    mutationFn: () => {
      if (!droppedFile) throw new Error("No hay fichero seleccionado");
      return brainApiService.ingestFile(
        droppedFile,
        Number(spaceId),
        selectedModel === "auto" ? undefined : selectedModel,
      );
    },
    onSuccess: (res) => { startJob(res.job_id); },
    onError: (err: Error) => toast.error("Error al iniciar ingesta fichero", { description: err.message }),
  });

  // ── Mutación Ruta local ──────────────────────────────────────────────────────
  const pathMutation = useMutation({
    mutationFn: () =>
      brainApiService.ingestPath(
        localPath,
        Number(spaceId),
        selectedModel === "auto" ? undefined : selectedModel,
      ),
    onSuccess: (res) => { startJob(res.job_id); },
    onError: (err: Error) => toast.error("Error al iniciar ingesta ruta", { description: err.message }),
  });

  const isPending = urlMutation.isPending || fileMutation.isPending || pathMutation.isPending;

  // ── Drag & Drop handlers ─────────────────────────────────────────────────────
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) { setDroppedFile(f); resetIngest(); }
  };

  const recommendedModel =
    activeTab === "url"  ? urlRecoMod :
    activeTab === "file" ? getModelForExt(droppedFile ? `.${droppedFile.name.split(".").pop()?.toLowerCase()}` : null) :
    pathRecoMod;

  // ── Info pipeline ─────────────────────────────────────────────────────────────
  const pipelineSteps = [
    { icon: "📥", title: "Extracción",         desc: "Parseo del fichero y generación de chunks según formato (PDF, código, Markdown…)" },
    { icon: "🧠", title: "Síntesis semántica", desc: "LLM genera el pasaporte semántico del documento" },
    { icon: "🗄️", title: "Indexación",         desc: "Chunks + embeddings → Qdrant (brain / knowledge / code) + PostgreSQL BM25" },
  ];

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto w-full">
      <BrainBreadcrumb spaceId={spaceId} current="Ingesta" />

      {/* Cabecera */}
      <div>
        <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
          <Upload className="h-5 w-5 text-violet-400" />
          Ingesta avanzada
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Añade documentos al Brain por URL, fichero subido o ruta local del servidor.
        </p>
      </div>

      {/* Pestañas principales */}
      <Tabs value={mainTab} onValueChange={setMainTab} className="w-full">
        <TabsList className="grid w-full grid-cols-2 max-w-sm">
          <TabsTrigger value="ingest" className="gap-2">
            <Upload className="h-3.5 w-3.5" /> Ingestar
          </TabsTrigger>
          <TabsTrigger value="monitor" className="gap-2">
            <Database className="h-3.5 w-3.5" /> Monitorización
            {passportCount > 0 && (
              <Badge variant="secondary" className="ml-1 h-4 text-[10px] px-1.5">{passportCount}</Badge>
            )}
          </TabsTrigger>
        </TabsList>

        {/* ── TAB: Ingestar ──────────────────────────────────────────────────── */}
        <TabsContent value="ingest" className="mt-6 space-y-6">

          {/* Sub-tabs de modo de ingesta */}
          <Tabs value={activeTab} onValueChange={(v) => { setActiveTab(v); resetIngest(); }} className="w-full">
            <TabsList className="grid w-full grid-cols-5 max-w-2xl">
              <TabsTrigger value="url"       className="gap-1.5 text-xs">
                <Link2 className="h-3.5 w-3.5" /> URL
              </TabsTrigger>
              <TabsTrigger value="file"      className="gap-1.5 text-xs">
                <FileText className="h-3.5 w-3.5" /> Fichero
              </TabsTrigger>
              <TabsTrigger value="path"      className="gap-1.5 text-xs">
                <Server className="h-3.5 w-3.5" /> Ruta local
              </TabsTrigger>
              <TabsTrigger value="native"    className="gap-1.5 text-xs">
                <Ticket className="h-3.5 w-3.5" /> Fuentes
              </TabsTrigger>
              <TabsTrigger value="connector" className="gap-1.5 text-xs">
                <Plug className="h-3.5 w-3.5" /> Nube
              </TabsTrigger>
            </TabsList>

            {/* ── URL ────────────────────────────────────────────────────────── */}
            <TabsContent value="url" className="mt-4">
              <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <div className="flex items-center gap-2">
                  <Link2 className="h-4 w-4 text-violet-400" />
                  <h2 className="text-sm font-semibold text-foreground">URL del documento</h2>
                </div>
                <p className="text-xs text-muted-foreground">
                  Soporta páginas web (HTML), PDFs, Markdown, código fuente y documentos Office.
                  La URL debe ser accesible desde el servidor.
                </p>
                <div className="relative">
                  <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    type="url"
                    placeholder="https://ejemplo.com/documento.pdf"
                    value={url}
                    onChange={(e) => { setUrl(e.target.value); resetIngest(); }}
                    className="pl-9 font-mono text-sm bg-muted/50 border-input focus:border-violet-500 text-foreground placeholder:text-muted-foreground/60"
                    disabled={isIngesting}
                  />
                </div>
                <div className="flex flex-wrap gap-3 items-center">
                  <ModelSelector
                    value={selectedModel}
                    onValueChange={setSelectedModel}
                    disabled={isIngesting}
                    recommendedModel={urlRecoMod}
                  />
                  <Button
                    onClick={() => urlMutation.mutate()}
                    disabled={!urlValid || isIngesting || isPending}
                    className="gap-2 bg-violet-600 hover:bg-violet-500 text-white"
                  >
                    {isPending ? <><Loader2 className="h-4 w-4 animate-spin" /> Iniciando…</> :
                     isIngesting ? <><Loader2 className="h-4 w-4 animate-spin" /> Procesando…</> :
                     <><ArrowRight className="h-4 w-4" /> Ingestar</>}
                  </Button>
                </div>
              </div>
            </TabsContent>

            {/* ── Fichero ─────────────────────────────────────────────────────── */}
            <TabsContent value="file" className="mt-4">
              <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-violet-400" />
                  <h2 className="text-sm font-semibold text-foreground">Subir fichero</h2>
                </div>
                <p className="text-xs text-muted-foreground">
                  PDF, Word, Excel, PowerPoint, Markdown, código fuente, CSV, JSON, XML, DrawIO, Jupyter…
                </p>

                {/* Zona drag & drop */}
                <div
                  className={cn(
                    "relative rounded-lg border-2 border-dashed p-8 text-center cursor-pointer transition-colors",
                    dragOver
                      ? "border-violet-400 bg-violet-500/10"
                      : "border-border hover:border-primary/40 bg-muted/30",
                  )}
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) { setDroppedFile(f); resetIngest(); }
                    }}
                  />
                  {droppedFile ? (
                    <div className="flex flex-col items-center gap-2">
                      <FileText className="h-8 w-8 text-violet-400" />
                      <p className="text-sm font-medium text-foreground">{droppedFile.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {(droppedFile.size / 1024).toFixed(1)} KB
                        {" · "}
                        <button
                          type="button"
                          className="text-violet-400 underline"
                          onClick={(e) => { e.stopPropagation(); setDroppedFile(null); resetIngest(); }}
                        >
                          Cambiar
                        </button>
                      </p>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-2">
                      <Upload className="h-8 w-8 text-muted-foreground" />
                      <p className="text-sm text-muted-foreground">
                        Arrastra un fichero aquí o <span className="text-violet-400 underline">selecciona uno</span>
                      </p>
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap gap-3 items-center">
                  <ModelSelector
                    value={selectedModel}
                    onValueChange={setSelectedModel}
                    disabled={isIngesting}
                    recommendedModel={recommendedModel}
                  />
                  <Button
                    onClick={() => fileMutation.mutate()}
                    disabled={!droppedFile || isIngesting || isPending}
                    className="gap-2 bg-violet-600 hover:bg-violet-500 text-white"
                  >
                    {isPending ? <><Loader2 className="h-4 w-4 animate-spin" /> Subiendo…</> :
                     isIngesting ? <><Loader2 className="h-4 w-4 animate-spin" /> Procesando…</> :
                     <><ArrowRight className="h-4 w-4" /> Ingestar</>}
                  </Button>
                </div>
              </div>
            </TabsContent>

            {/* ── Ruta local ──────────────────────────────────────────────────── */}
            <TabsContent value="path" className="mt-4">
              <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <div className="flex items-center gap-2">
                  <Server className="h-4 w-4 text-violet-400" />
                  <h2 className="text-sm font-semibold text-foreground">Ruta local del servidor</h2>
                </div>
                <p className="text-xs text-muted-foreground">
                  Ruta absoluta de un fichero accesible desde el servidor backend.
                  Útil para volúmenes montados o directorios compartidos en red.
                </p>
                <div className="relative">
                  <FolderOpen className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="/data/documentos/informe.pdf"
                    value={localPath}
                    onChange={(e) => { setLocalPath(e.target.value); resetIngest(); }}
                    className="pl-9 font-mono text-sm bg-muted/50 border-input focus:border-violet-500 text-foreground placeholder:text-muted-foreground/60"
                    disabled={isIngesting}
                  />
                </div>
                <div className="flex flex-wrap gap-3 items-center">
                  <ModelSelector
                    value={selectedModel}
                    onValueChange={setSelectedModel}
                    disabled={isIngesting}
                    recommendedModel={pathRecoMod}
                  />
                  <Button
                    onClick={() => pathMutation.mutate()}
                    disabled={!localPath.trim() || isIngesting || isPending}
                    className="gap-2 bg-violet-600 hover:bg-violet-500 text-white"
                  >
                    {isPending ? <><Loader2 className="h-4 w-4 animate-spin" /> Iniciando…</> :
                     isIngesting ? <><Loader2 className="h-4 w-4 animate-spin" /> Procesando…</> :
                     <><ArrowRight className="h-4 w-4" /> Ingestar</>}
                  </Button>
                </div>
              </div>
            </TabsContent>

            {/* ── Fuentes estructuradas (Jira / Confluence) ──────────────────── */}
            <TabsContent value="native" className="mt-4">
              <NativeSourcesTab
                spaceId={spaceId}
                selectedModel={selectedModel}
                isIngesting={isIngesting}
                onStartJob={startJob}
              />
            </TabsContent>

            {/* ── Nube (OneDrive, Drive, Dropbox) ──────────────────────────────── */}
            <TabsContent value="connector" className="mt-4">
              <ConnectorTab
                spaceId={spaceId}
                selectedModel={selectedModel}
                isIngesting={isIngesting}
                onStartJob={startJob}
              />
            </TabsContent>
          </Tabs>

          {/* Log de fases SSE */}
          <IngestPhaseLog
            jobId={activeJobId}
            onComplete={handlePipelineComplete}
            onError={handlePipelineError}
          />

          {/* Banner de éxito */}
          {ingestDone && (
            <SuccessBanner
              spaceId={spaceId}
              onNewIngest={resetIngest}
              onGoMonitor={() => setMainTab("monitor")}
            />
          )}

          {/* Info pipeline */}
          {!activeJobId && !ingestDone && (
            <div className="rounded-xl border border-border/60 bg-muted/20 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
                ¿Cómo funciona el pipeline?
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {pipelineSteps.map((step) => (
                  <div key={step.title} className="flex gap-3 items-start">
                    <span className="text-xl">{step.icon}</span>
                    <div>
                      <p className="text-sm font-medium text-foreground/80">{step.title}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">{step.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </TabsContent>

        {/* ── TAB: Monitorización ────────────────────────────────────────────── */}
        <TabsContent value="monitor" className="mt-6">
          <div className="rounded-xl border border-border bg-card p-5">
            <div className="flex items-center gap-2 mb-4">
              <Database className="h-4 w-4 text-violet-400" />
              <h2 className="text-sm font-semibold text-foreground">Documentos en el Brain</h2>
              {passportCount > 0 && (
                <span className="text-xs text-muted-foreground">
                  {passportCount} documento{passportCount !== 1 ? "s" : ""}
                </span>
              )}
            </div>
            <MonitorTable spaceId={spaceId} onOpenWiki={(src) => router.push(BRAIN_ROUTES.WIKI_PASSPORT(spaceId, src))} />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

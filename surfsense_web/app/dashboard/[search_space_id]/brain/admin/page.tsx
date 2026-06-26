"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/admin
 *
 * F6.10 — Admin Brain: configuración en caliente del pipeline de conocimiento.
 *
 * 5 tabs (del brain_admin.py original, mejorados):
 *   • ✂️ Chunking — strategy, chunk_size, chunk_overlap
 *   • 🔍 Retrieval — scores, top_k, reranking, ingesta toggle
 *   • 🤖 LLM — provider, modelo, temperatura + CRAG Evaluador inteligente
 *   • 📦 Colecciones — estado Qdrant con visualización de vectores
 *   • 🖥️ Sistema — health check + fallback SurfSense (F5 unificada)
 *
 * Mejoras vs. Streamlit:
 * - Guardado selectivo (PATCH semántico) con indicador de campos modificados
 * - Warning contextual de latencia CRAG según provider
 * - Perfiles CRAG autodetectados visibles sin hardcode en componente
 * - Health check con estados visuales semáforo
 *
 * Gestión de logs: brainLogger("BrainAdminPage")
 * ZERO HARDCODE: todas las constantes en lib/brain/constants.ts
 */

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Scissors, Search, Bot, Package, Monitor, Save, RefreshCw,
  AlertTriangle, Info, CheckCircle, XCircle, AlertCircle, Loader2,
  ToggleLeft, ToggleRight, ChevronDown, ChevronUp,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Slider } from "@/components/ui/slider";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";
import { BrainBreadcrumb } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import { cacheKeys } from "@/lib/query-client/cache-keys";
import type { AdminConfigResponse } from "@/contracts/types/brain.types";
import {
  BRAIN_ADMIN_TABS,
  BRAIN_CHUNK_STRATEGIES,
  BRAIN_CHUNK_SIZE_MIN, BRAIN_CHUNK_SIZE_MAX,
  BRAIN_CHUNK_OVERLAP_MIN, BRAIN_CHUNK_OVERLAP_MAX,
  BRAIN_ROUTER_SCORE_MIN, BRAIN_ROUTER_SCORE_MAX, BRAIN_ROUTER_SCORE_STEP,
  BRAIN_CRAG_TIMEOUT_MIN, BRAIN_CRAG_TIMEOUT_MAX,
  BRAIN_QUALITY_THRESHOLD_MIN, BRAIN_QUALITY_THRESHOLD_MAX, BRAIN_QUALITY_THRESHOLD_STEP,
  BRAIN_LLM_PROVIDERS,
  BRAIN_CRAG_PROFILES,
  BRAIN_CRAG_LATENCY,
  type BrainLlmProvider,
} from "@/lib/brain/constants";

const log = brainLogger("BrainAdminPage");

// ─── Sub-componente: indicador de salud ──────────────────────────────────────

type HealthStatus = "ok" | "degraded" | "error" | "unknown";

interface HealthIndicatorProps {
  name: string;
  status: HealthStatus;
}

function HealthIndicator({ name, status }: HealthIndicatorProps) {
  const statusConfig: Record<HealthStatus, { icon: React.ReactNode; label: string; cls: string }> = {
    ok:       { icon: <CheckCircle className="h-4 w-4" />,  label: "Operativo",  cls: "text-emerald-400" },
    degraded: { icon: <AlertCircle className="h-4 w-4" />, label: "Degradado",  cls: "text-amber-400" },
    error:    { icon: <XCircle     className="h-4 w-4" />, label: "Error",      cls: "text-red-400" },
    unknown:  { icon: <AlertCircle className="h-4 w-4" />, label: "Desconocido", cls: "text-slate-500" },
  };
  const cfg = statusConfig[status];

  return (
    <div className="flex items-center justify-between rounded-lg border border-slate-700/60 bg-slate-800/30 px-4 py-3">
      <div className="flex items-center gap-3">
        <span className={cfg.cls}>{cfg.icon}</span>
        <span className="text-sm text-slate-200">{name}</span>
      </div>
      <span className={cn("text-xs font-medium", cfg.cls)}>{cfg.label}</span>
    </div>
  );
}

// ─── Sub-componente: campo de configuración numérico ──────────────────────────

interface NumberFieldProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  hint?: string;
}

function NumberField({ label, value, min, max, step = 1, onChange, hint }: NumberFieldProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-sm text-slate-300">{label}</Label>
        <span className="text-sm font-mono text-violet-300">{value}</span>
      </div>
      <Slider
        min={min} max={max} step={step}
        value={[value]}
        onValueChange={([v]) => onChange(v)}
        className="w-full"
      />
      <div className="flex justify-between text-xs text-slate-600">
        <span>{min}</span>
        {hint && <span className="text-slate-500">{hint}</span>}
        <span>{max}</span>
      </div>
    </div>
  );
}

// ─── Página principal ─────────────────────────────────────────────────────────

export default function BrainAdminPage() {
  const params = useParams<{ search_space_id: string }>();
  const spaceId = params.search_space_id;
  const queryClient = useQueryClient();

  // ── Cargar configuración activa ─────────────────────────────────────────────
  const { data: config, isLoading } = useQuery({
    queryKey: cacheKeys.brain.adminConfig(),
    queryFn: () => {
      log.debug("Cargando configuración Admin Brain");
      return brainApiService.getAdminConfig();
    },
    staleTime: 30_000,
  });

  const { data: ollamaData } = useQuery({
    queryKey: cacheKeys.brain.ollamaModels(),
    queryFn: () => brainApiService.getOllamaModels(),
    staleTime: 60_000,
  });

  const { data: health, refetch: refetchHealth } = useQuery({
    queryKey: ["brain", "health"],
    queryFn: () => brainApiService.getHealth(),
    staleTime: 10_000,
  });

  // ── Estado del formulario (draft sobre la config cargada) ───────────────────
  const [draft, setDraft] = useState<Partial<AdminConfigResponse>>({});
  const [showCragProfile, setShowCragProfile] = useState(false);

  // Sincronizar draft con la config cargada (solo al montar o si no hay draft)
  useEffect(() => {
    if (config && Object.keys(draft).length === 0) {
      setDraft({ ...config });
    }
  }, [config]); // eslint-disable-line react-hooks/exhaustive-deps

  // Helper para actualizar un campo del draft
  const set = <K extends keyof AdminConfigResponse>(key: K, value: AdminConfigResponse[K]) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
  };

  // Calcular cuántos campos han sido modificados respecto a la config original
  const modifiedCount = Object.keys(draft).filter(
    (k) => config && draft[k as keyof AdminConfigResponse] !== config[k as keyof AdminConfigResponse]
  ).length;

  // ── Mutación de guardado ─────────────────────────────────────────────────────
  const saveMutation = useMutation({
    mutationFn: () => {
      // Enviar SOLO los campos modificados (PATCH semántico)
      const patch: Partial<AdminConfigResponse> = {};
      for (const [k, v] of Object.entries(draft)) {
        const key = k as keyof AdminConfigResponse;
        if (config?.[key] !== v) {
          (patch as Record<string, unknown>)[k] = v;
        }
      }
      log.info("Guardando configuración Admin Brain", { patchKeys: Object.keys(patch) });
      return brainApiService.updateAdminConfig(patch);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cacheKeys.brain.adminConfig() });
      toast("Configuración guardada", { description: "Los cambios se aplican inmediatamente." });
    },
    onError: (err: Error) => {
      log.error("Error guardando configuración", { err });
      toast.error("Error al guardar", { description: err.message });
    },
  });

  const ollamaModels = ollamaData?.models ?? [];
  const cragProvider = (draft.CRAG_EVALUATOR_PROVIDER ?? draft.BRAIN_LLM_PROVIDER ?? "ollama") as BrainLlmProvider;
  const cragEnabled  = draft.CRAG_EVALUATOR_ENABLED ?? false;
  const showLatencyWarning = cragEnabled && cragProvider === "ollama";
  const showCostWarning    = cragEnabled && (cragProvider === "anthropic" || cragProvider === "openai");
  const cragProfile = BRAIN_CRAG_PROFILES[cragProvider as keyof typeof BRAIN_CRAG_PROFILES];

  return (
    <div className="flex flex-col gap-6 p-6 max-w-3xl mx-auto w-full">
      {/* Breadcrumb */}
      <BrainBreadcrumb spaceId={spaceId} current="Admin Brain" />

      {/* Cabecera */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-100 flex items-center gap-2">
            <Monitor className="h-5 w-5 text-violet-400" />
            Admin Brain
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Configuración en caliente del pipeline · sin restart de contenedor
          </p>
        </div>

        <Button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending || modifiedCount === 0 || isLoading}
          className="gap-2 bg-violet-600 hover:bg-violet-500 text-white"
        >
          {saveMutation.isPending ? (
            <><Loader2 className="h-4 w-4 animate-spin" /> Guardando…</>
          ) : (
            <>
              <Save className="h-4 w-4" />
              Guardar{modifiedCount > 0 ? ` (${modifiedCount})` : ""}
            </>
          )}
        </Button>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-violet-400" />
        </div>
      )}

      {!isLoading && (
        <Tabs defaultValue="chunking" className="w-full">
          <TabsList className="grid w-full grid-cols-5 text-xs">
            {BRAIN_ADMIN_TABS.map((tab) => (
              <TabsTrigger key={tab.key} value={tab.key} className="gap-1">
                <span>{tab.icon}</span>
                <span className="hidden sm:inline">{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>

          {/* ── TAB: Chunking ──────────────────────────────────────────── */}
          <TabsContent value="chunking" className="mt-6 space-y-5">
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5 space-y-5">
              {/* Estrategia */}
              <div className="space-y-2">
                <Label className="text-sm text-slate-300">Estrategia de chunking</Label>
                <Select
                  value={draft.BRAIN_CHUNK_STRATEGY ?? "paragraph"}
                  onValueChange={(v) => set("BRAIN_CHUNK_STRATEGY", v)}
                >
                  <SelectTrigger className="bg-slate-900/50 border-slate-700">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BRAIN_CHUNK_STRATEGIES.map((s) => (
                      <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <NumberField
                label="Chunk size (tokens)"
                value={draft.BRAIN_CHUNK_SIZE ?? 512}
                min={BRAIN_CHUNK_SIZE_MIN} max={BRAIN_CHUNK_SIZE_MAX} step={64}
                onChange={(v) => set("BRAIN_CHUNK_SIZE", v)}
                hint="tokens por fragmento"
              />
              <NumberField
                label="Chunk overlap (tokens)"
                value={draft.BRAIN_CHUNK_OVERLAP ?? 64}
                min={BRAIN_CHUNK_OVERLAP_MIN} max={BRAIN_CHUNK_OVERLAP_MAX} step={16}
                onChange={(v) => set("BRAIN_CHUNK_OVERLAP", v)}
                hint="solapamiento entre chunks"
              />
            </div>
          </TabsContent>

          {/* ── TAB: Retrieval ──────────────────────────────────────────── */}
          <TabsContent value="retrieval" className="mt-6 space-y-5">
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5 space-y-5">
              <NumberField
                label="Score mínimo L1 (pasaportes)"
                value={draft.ROUTER_L1_HIGH_SCORE ?? 0.72}
                min={BRAIN_ROUTER_SCORE_MIN} max={BRAIN_ROUTER_SCORE_MAX} step={BRAIN_ROUTER_SCORE_STEP}
                onChange={(v) => set("ROUTER_L1_HIGH_SCORE", v)}
                hint="umbral para respuesta directa L1"
              />
              <NumberField
                label="Score umbral fallback L2"
                value={draft.ROUTER_L1_MIN_SCORE ?? 0.45}
                min={BRAIN_ROUTER_SCORE_MIN} max={BRAIN_ROUTER_SCORE_MAX} step={BRAIN_ROUTER_SCORE_STEP}
                onChange={(v) => set("ROUTER_L1_MIN_SCORE", v)}
                hint="por debajo → fallback a L2 Hybrid (Qdrant+BM25)"
              />
              <NumberField
                label="Top K documentos"
                value={draft.BRAIN_TOP_K ?? 5}
                min={1} max={20} step={1}
                onChange={(v) => set("BRAIN_TOP_K", v)}
                hint="documentos por nivel"
              />

              <div className="flex items-center justify-between py-1">
                <div>
                  <Label className="text-sm text-slate-300">Reranking semántico</Label>
                  <p className="text-xs text-slate-500 mt-0.5">Reordena chunks por relevancia antes de sintetizar</p>
                </div>
                <Switch
                  checked={draft.BRAIN_RERANKING_ENABLED ?? true}
                  onCheckedChange={(v) => set("BRAIN_RERANKING_ENABLED", v)}
                />
              </div>

              <Separator className="border-slate-700/60" />

              <div className="space-y-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Ingesta Brain (F5 unificada)
                </p>
                <div className="flex items-center justify-between py-1">
                  <div>
                    <Label className="text-sm text-slate-300">Pipeline Brain activo</Label>
                    <p className="text-xs text-slate-500 mt-0.5">Genera pasaportes y vectoriza en Qdrant</p>
                  </div>
                  <Switch
                    checked={draft.BRAIN_INGESTION_ENABLED ?? true}
                    onCheckedChange={(v) => set("BRAIN_INGESTION_ENABLED", v)}
                  />
                </div>
                <div className="flex items-center justify-between py-1">
                  <div>
                    <Label className="text-sm text-slate-300">Síntesis de pasaportes</Label>
                    <p className="text-xs text-slate-500 mt-0.5">On/off sin afectar al chunking/vectorización</p>
                  </div>
                  <Switch
                    checked={draft.BRAIN_SYNTHESIS_ENABLED ?? true}
                    onCheckedChange={(v) => set("BRAIN_SYNTHESIS_ENABLED", v)}
                  />
                </div>
                <NumberField
                  label="Umbral de calidad mínima"
                  value={draft.BRAIN_QUALITY_THRESHOLD ?? 0.3}
                  min={BRAIN_QUALITY_THRESHOLD_MIN} max={BRAIN_QUALITY_THRESHOLD_MAX} step={BRAIN_QUALITY_THRESHOLD_STEP}
                  onChange={(v) => set("BRAIN_QUALITY_THRESHOLD", v)}
                  hint="documentos por debajo son descartados"
                />
              </div>
            </div>
          </TabsContent>

          {/* ── TAB: LLM ──────────────────────────────────────────────── */}
          <TabsContent value="llm" className="mt-6 space-y-5">
            {/* Modelo de síntesis */}
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5 space-y-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Modelo de síntesis
              </p>

              <div className="space-y-2">
                <Label className="text-sm text-slate-300">Provider</Label>
                <Select
                  value={draft.BRAIN_LLM_PROVIDER ?? "ollama"}
                  onValueChange={(v) => set("BRAIN_LLM_PROVIDER", v)}
                >
                  <SelectTrigger className="bg-slate-900/50 border-slate-700">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BRAIN_LLM_PROVIDERS.map((p) => (
                      <SelectItem key={p} value={p}>{p}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Modelo síntesis */}
              <div className="space-y-2">
                <Label className="text-sm text-slate-300">Modelo</Label>
                {ollamaModels.length > 0 ? (
                  <Select
                    value={draft.BRAIN_LLM_MODEL ?? ""}
                    onValueChange={(v) => set("BRAIN_LLM_MODEL", v)}
                  >
                    <SelectTrigger className="bg-slate-900/50 border-slate-700">
                      <SelectValue placeholder="Seleccionar modelo…" />
                    </SelectTrigger>
                    <SelectContent>
                      {ollamaModels.map((m) => (
                        <SelectItem key={m} value={m}>{m}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    value={draft.BRAIN_LLM_MODEL ?? ""}
                    onChange={(e) => set("BRAIN_LLM_MODEL", e.target.value)}
                    placeholder="claude-3-5-haiku-20241022"
                    className="bg-slate-900/50 border-slate-700 font-mono text-sm"
                  />
                )}
              </div>

              <NumberField
                label="Temperatura"
                value={draft.BRAIN_LLM_TEMPERATURE ?? 0.1}
                min={0.0} max={1.0} step={0.05}
                onChange={(v) => set("BRAIN_LLM_TEMPERATURE", v)}
                hint="precisión vs creatividad"
              />
              <NumberField
                label="Max tokens síntesis"
                value={draft.BRAIN_LLM_MAX_TOKENS ?? 4096}
                min={512} max={8192} step={256}
                onChange={(v) => set("BRAIN_LLM_MAX_TOKENS", v)}
              />

              {/* Modelo embedding */}
              <div className="space-y-2">
                <Label className="text-sm text-slate-300">Modelo de embedding</Label>
                <Input
                  value={draft.BRAIN_EMBEDDING_MODEL ?? "nomic-embed-text"}
                  onChange={(e) => set("BRAIN_EMBEDDING_MODEL", e.target.value)}
                  className="bg-slate-900/50 border-slate-700 font-mono text-sm"
                />
                <p className="text-xs text-slate-500">
                  Cambiar el modelo requiere re-vectorización de todos los chunks
                </p>
              </div>
            </div>

            {/* Bloque CRAG Evaluador */}
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                    Agente Evaluador CRAG
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Evalúa la relevancia de cada chunk antes de sintetizar (F4.6)
                  </p>
                </div>
                <Switch
                  checked={cragEnabled}
                  onCheckedChange={(v) => set("CRAG_EVALUATOR_ENABLED", v)}
                />
              </div>

              <div className={cn("space-y-4 transition-opacity", !cragEnabled && "opacity-40 pointer-events-none")}>
                <div className="space-y-2">
                  <Label className="text-sm text-slate-300">Provider evaluador</Label>
                  <Select
                    value={draft.CRAG_EVALUATOR_PROVIDER ?? ""}
                    onValueChange={(v) => set("CRAG_EVALUATOR_PROVIDER", v)}
                    disabled={!cragEnabled}
                  >
                    <SelectTrigger className="bg-slate-900/50 border-slate-700">
                      <SelectValue placeholder="Mismo que síntesis" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="">Mismo que síntesis (auto)</SelectItem>
                      {BRAIN_LLM_PROVIDERS.map((p) => (
                        <SelectItem key={p} value={p}>{p}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label className="text-sm text-slate-300">Modelo evaluador</Label>
                  {ollamaModels.length > 0 ? (
                    <Select
                      value={draft.CRAG_EVALUATOR_MODEL ?? ""}
                      onValueChange={(v) => set("CRAG_EVALUATOR_MODEL", v)}
                      disabled={!cragEnabled}
                    >
                      <SelectTrigger className="bg-slate-900/50 border-slate-700">
                        <SelectValue placeholder="Seleccionar modelo…" />
                      </SelectTrigger>
                      <SelectContent>
                        {ollamaModels.map((m) => (
                          <SelectItem key={m} value={m}>{m}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      value={draft.CRAG_EVALUATOR_MODEL ?? ""}
                      onChange={(e) => set("CRAG_EVALUATOR_MODEL", e.target.value)}
                      disabled={!cragEnabled}
                      placeholder="qwen2.5-coder:3b"
                      className="bg-slate-900/50 border-slate-700 font-mono text-sm"
                    />
                  )}
                </div>

                <NumberField
                  label="Timeout evaluador (segundos)"
                  value={draft.CRAG_EVAL_TIMEOUT ?? 15}
                  min={BRAIN_CRAG_TIMEOUT_MIN} max={BRAIN_CRAG_TIMEOUT_MAX} step={1}
                  onChange={(v) => set("CRAG_EVAL_TIMEOUT", v)}
                />

                {/* Perfil autodetectado */}
                {cragProfile && (
                  <button
                    type="button"
                    className="w-full text-left"
                    onClick={() => setShowCragProfile((p) => !p)}
                  >
                    <div className="flex items-center justify-between rounded-lg border border-slate-700/40 bg-slate-900/40 px-3 py-2">
                      <span className="text-xs text-slate-400">Perfil autodetectado: {cragProvider}</span>
                      {showCragProfile ? (
                        <ChevronUp className="h-3.5 w-3.5 text-slate-500" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5 text-slate-500" />
                      )}
                    </div>
                  </button>
                )}
                {showCragProfile && cragProfile && (
                  <div className="rounded-lg border border-slate-700/40 bg-slate-900/30 px-4 py-3 space-y-2">
                    <p className="text-xs text-slate-400 font-mono">{cragProfile.desc}</p>
                    <div className="flex gap-4">
                      <span className="text-xs text-slate-500">
                        Max chunks: <span className="text-slate-300">{cragProfile.maxChunks}</span>
                      </span>
                      <span className="text-xs text-slate-500">
                        Batch: <span className="text-slate-300">{cragProfile.batch}</span>
                      </span>
                    </div>
                    <p className="text-xs text-slate-500">
                      Latencia estimada: <span className="text-amber-400">{BRAIN_CRAG_LATENCY[cragProvider as keyof typeof BRAIN_CRAG_LATENCY]}</span>
                    </p>
                  </div>
                )}

                {/* Rewriter */}
                <div className="space-y-2">
                  <Label className="text-sm text-slate-300">Modelo rewriter (siempre activo)</Label>
                  <Input
                    value={draft.CRAG_REWRITER_MODEL ?? "qwen2.5-coder:3b"}
                    onChange={(e) => set("CRAG_REWRITER_MODEL", e.target.value)}
                    className="bg-slate-900/50 border-slate-700 font-mono text-sm"
                  />
                  <p className="text-xs text-slate-500">
                    Convierte la pregunta a keywords antes de buscar en SearXNG (L2.c)
                  </p>
                </div>
              </div>

              {/* Warnings contextuales */}
              {showLatencyWarning && (
                <Alert className="border-amber-500/30 bg-amber-500/10">
                  <AlertTriangle className="h-4 w-4 text-amber-400" />
                  <AlertDescription className="text-amber-300 text-xs">
                    Con Ollama local, el evaluador CRAG añade ~15s por consulta (3 llamadas LLM × 5s).
                    Considera usar Claude o OpenAI para el evaluador.
                  </AlertDescription>
                </Alert>
              )}
              {showCostWarning && (
                <Alert className="border-blue-500/30 bg-blue-500/10">
                  <Info className="h-4 w-4 text-blue-400" />
                  <AlertDescription className="text-blue-300 text-xs">
                    El evaluador usa {cragProvider} API — latencia mínima (~1s) pero
                    con coste por llamada (~$0.003-0.005/evaluación).
                  </AlertDescription>
                </Alert>
              )}
            </div>
          </TabsContent>

          {/* ── TAB: Colecciones ──────────────────────────────────────── */}
          <TabsContent value="collections" className="mt-6 space-y-4">
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-4">
                Estado de colecciones Qdrant
              </p>
              <p className="text-sm text-slate-400">
                Las colecciones Qdrant (<span className="font-mono text-violet-300">brain</span>,{" "}
                <span className="font-mono text-blue-300">knowledge</span>,{" "}
                <span className="font-mono text-emerald-300">code</span>) se gestionan desde
                la página de{" "}
                <a
                  href={`/dashboard/${spaceId}/brain/metrics`}
                  className="underline text-violet-400 hover:text-violet-300"
                >
                  Métricas Brain
                </a>.
              </p>
              <div className="mt-4 rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-3">
                <p className="text-xs text-amber-400 flex items-center gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  La recreación de una colección elimina todos sus vectores. Usa solo si el
                  índice está corrupto o necesitas cambiar el modelo de embedding.
                </p>
              </div>
            </div>
          </TabsContent>

          {/* ── TAB: Sistema ──────────────────────────────────────────── */}
          <TabsContent value="system" className="mt-6 space-y-4">
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5 space-y-4">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Health check de servicios
                </p>
                <Button
                  variant="ghost" size="sm"
                  onClick={() => {
                    log.debug("Refrescando health check");
                    refetchHealth();
                  }}
                  className="gap-1.5 text-xs text-slate-400"
                >
                  <RefreshCw className="h-3 w-3" />
                  Actualizar
                </Button>
              </div>

              <div className="space-y-2">
                <HealthIndicator name="Qdrant" status={(health?.qdrant ?? "unknown") as HealthStatus} />
                <HealthIndicator name="Ollama" status={(health?.ollama ?? "unknown") as HealthStatus} />
                <HealthIndicator name="PostgreSQL" status={(health?.postgresql ?? "unknown") as HealthStatus} />
                {health?.redis && (
                  <HealthIndicator name="Redis" status={(health.redis ?? "unknown") as HealthStatus} />
                )}
              </div>
            </div>

            {/* Sección F5: Fallback SurfSense */}
            <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 p-5 space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Fallback SurfSense (F5 unificada)
              </p>
              <p className="text-xs text-slate-400">
                Si el pipeline Brain falla (Ollama no disponible, Qdrant error), el sistema
                cae automáticamente al pipeline SurfSense original (MiniLM + búsqueda directa).
                Este comportamiento es automático y no configurable.
              </p>
              <div className="flex items-center gap-2">
                {(draft.BRAIN_INGESTION_ENABLED ?? true) ? (
                  <>
                    <ToggleRight className="h-4 w-4 text-emerald-400" />
                    <span className="text-xs text-emerald-400">Pipeline Brain activo — fallback disponible</span>
                  </>
                ) : (
                  <>
                    <ToggleLeft className="h-4 w-4 text-amber-400" />
                    <span className="text-xs text-amber-400">Pipeline Brain desactivado — usando SurfSense directo</span>
                  </>
                )}
              </div>
            </div>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}


"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/chat
 *
 * Brain Chat — interfaz de conversación conectada a la cascada multinivel
 * L1→L2→BM25→Web→L0 del backend (brain_chat.py).
 *
 * Diferencia con el chat SurfSense: sin herramientas LangGraph.
 * Ofrece transparencia completa del nivel de retrieval + fuentes citadas.
 *
 * @features
 * - Detección automática de retrieve_mode ("auto" | "full_source")
 * - Toggle Force LLM Libre (force_l0, evita retrieval)
 * - LevelBadge visible en cada respuesta del asistente
 * - Hints Perplexity-style en la pantalla inicial (sin mensajes)
 * - session_id para continuidad de conversación
 * - Drill-down condicional (drillDownAvailable && level === L1)
 * - Renderizado Markdown con remark-gfm (tablas, código, listas)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Brain,
  Check,
  Copy,
  Loader2,
  RefreshCw,
  Send,
  X,
  Zap,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { LevelBadge, SourceCard } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import { BRAIN_CHAT_HINTS, BRAIN_LEVELS } from "@/lib/brain/constants";
import { detectRetrieveMode } from "@/lib/brain/retrieve-mode";
import type {
  BrainLevelValue,
  BrainSource,
  RetrieveMode,
} from "@/contracts/types/brain.types";
import { cn } from "@/lib/utils";

const log = brainLogger("BrainChat");

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface BrainChatMessage {
  /** ID único del mensaje */
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Solo en mensajes del asistente */
  levelUsed?: BrainLevelValue;
  modelTier?: string | null;
  sources?: BrainSource[];
  drillDownAvailable?: boolean;
  /** Pregunta original para reenvío drill-down */
  originalQuestion?: string;
  timestamp: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Formatea un timestamp en hora HH:MM */
function formatTime(ts: number): string {
  return new Intl.DateTimeFormat("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ts));
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components (internos — no se exportan)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Pantalla inicial sin mensajes — hints estilo Perplexity.
 * Al hacer clic en un hint, se pre-rellena el input.
 */
function HintsScreen({ onHintClick }: { onHintClick: (text: string) => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-8 px-4 py-12 animate-in fade-in duration-500">
      <div className="flex flex-col items-center gap-3 text-center">
        <div className="flex size-16 items-center justify-center rounded-2xl border border-violet-500/20 bg-violet-500/10">
          <Brain className="size-8 text-violet-400" />
        </div>
        <div>
          <h2 className="text-xl font-semibold">Brain Chat</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Consulta tu base de conocimiento con cascada multinivel
            L1&nbsp;→&nbsp;L2&nbsp;→&nbsp;BM25&nbsp;→&nbsp;Web
          </p>
        </div>
      </div>

      <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {BRAIN_CHAT_HINTS.map((hint) => (
          <button
            key={hint.text}
            type="button"
            onClick={() => onHintClick(hint.text)}
            className={cn(
              "group flex items-start gap-3 rounded-xl border border-border bg-card p-3.5",
              "text-left text-sm transition-all duration-150",
              "hover:border-violet-500/40 hover:bg-violet-500/5",
              "active:scale-[0.98]",
            )}
          >
            <span className="mt-0.5 shrink-0 text-lg leading-none">{hint.icon}</span>
            <span className="line-clamp-2 text-muted-foreground transition-colors group-hover:text-foreground">
              {hint.text}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Mensaje del usuario — burbuja derecha con acento violeta.
 */
function UserMessage({ message }: { message: BrainChatMessage }) {
  return (
    <div className="flex justify-end px-4 animate-in slide-in-from-bottom-2 fade-in duration-200">
      <div className="max-w-[75%] space-y-1">
        <div className="rounded-2xl rounded-br-sm border border-violet-500/20 bg-violet-500/10 px-4 py-2.5">
          <p className="whitespace-pre-wrap break-words text-sm text-foreground">
            {message.content}
          </p>
        </div>
        <p className="pr-1 text-right text-[11px] text-muted-foreground/50">
          {formatTime(message.timestamp)}
        </p>
      </div>
    </div>
  );
}

/**
 * Renderizador Markdown para el asistente.
 * Usa prose-invert de Tailwind Typography + overrides para código y enlaces.
 */
function BrainMarkdownContent({ content }: { content: string }) {
  return (
    <div
      className={cn(
        "prose prose-sm prose-invert max-w-none",
        "[&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-muted/40 [&_pre]:p-4",
        "[&_code:not(pre_code)]:rounded [&_code:not(pre_code)]:bg-violet-500/10 [&_code:not(pre_code)]:px-1.5 [&_code:not(pre_code)]:py-0.5 [&_code:not(pre_code)]:text-[0.8em] [&_code:not(pre_code)]:text-violet-300",
        "[&_a]:text-violet-400 [&_a]:underline-offset-4 [&_a:hover]:text-violet-300",
        "[&_table]:overflow-x-auto [&_thead]:border-border [&_tbody_tr:hover]:bg-muted/30",
        "[&_blockquote]:border-violet-500/30 [&_blockquote]:text-muted-foreground",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

/**
 * Botón de copiar con feedback visual (icono cambia 2 s tras copiar).
 */
function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [content]);

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      aria-label="Copiar respuesta"
    >
      {copied ? (
        <Check className="size-3.5 text-emerald-400" />
      ) : (
        <Copy className="size-3.5" />
      )}
    </button>
  );
}

/**
 * Mensaje del asistente Brain:
 * avatar → LevelBadge → contenido Markdown → fuentes → botón drill-down.
 */
function AssistantMessage({
  message,
  onDrillDown,
}: {
  message: BrainChatMessage;
  onDrillDown: (question: string) => void;
}) {
  const showDrillDown =
    message.drillDownAvailable === true &&
    message.levelUsed === BRAIN_LEVELS.L1 &&
    !!message.originalQuestion;

  return (
    <div className="flex gap-3 px-4 animate-in slide-in-from-bottom-2 fade-in duration-200">
      {/* Avatar */}
      <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-violet-500/30 bg-violet-500/15">
        <Brain className="size-3.5 text-violet-400" />
      </div>

      <div className="min-w-0 flex-1 space-y-3">
        {/* Fila de cabecera: LevelBadge + timestamp + copiar */}
        <div className="flex items-center justify-between gap-2">
          <div>
            {message.levelUsed !== undefined && (
              <LevelBadge
                level={message.levelUsed}
                modelTier={message.modelTier ?? undefined}
              />
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-muted-foreground/50">
              {formatTime(message.timestamp)}
            </span>
            <CopyButton content={message.content} />
          </div>
        </div>

        {/* Contenido Markdown */}
        <BrainMarkdownContent content={message.content} />

        {/* Fuentes citadas */}
        {message.sources && message.sources.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Fuentes ({message.sources.length})
            </p>
            <div className="space-y-1">
              {message.sources.map((src, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: fuentes sin ID único
                <SourceCard key={`${src.source}-${i}`} source={src} />
              ))}
            </div>
          </div>
        )}

        {/* Drill-down — solo L1 con disponibilidad confirmada */}
        {showDrillDown && (
          <button
            type="button"
            onClick={() => onDrillDown(message.originalQuestion!)}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg border border-blue-500/30",
              "bg-blue-500/10 px-3 py-1.5 text-xs text-blue-300",
              "transition-all hover:border-blue-400/50 hover:bg-blue-500/20 hover:text-blue-200",
              "active:scale-[0.98]",
            )}
          >
            <RefreshCw className="size-3" />
            Ampliar con documentos originales (Nivel 2)
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Indicador de "pensando" — tres puntos animados mientras el backend responde.
 */
function ThinkingIndicator() {
  return (
    <div className="flex gap-3 px-4 animate-in fade-in duration-200">
      <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-violet-500/30 bg-violet-500/15">
        <Brain className="size-3.5 animate-pulse text-violet-400" />
      </div>
      <div className="flex items-center gap-1 pt-2">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 animate-bounce rounded-full bg-violet-400/60"
            style={{ animationDelay: `${i * 150}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Página principal
// ─────────────────────────────────────────────────────────────────────────────

export default function BrainChatPage() {
  const params = useParams<{ search_space_id: string }>();
  const searchSpaceId = parseInt(params.search_space_id, 10);

  // Estado de conversación
  const [messages, setMessages] = useState<BrainChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Controles de retrieval
  const [retrieveMode, setRetrieveMode] = useState<RetrieveMode>("auto");
  const [modeManuallySet, setModeManuallySet] = useState(false);
  const [forceL0, setForceL0] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Detección automática del modo según el texto escrito
  const detectedMode = detectRetrieveMode(input);
  const effectiveMode: RetrieveMode = modeManuallySet ? retrieveMode : detectedMode;

  // Scroll al final cuando hay mensajes nuevos
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-resize del textarea (máx 160 px)
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  // Mutación TanStack Query para enviar pregunta al Brain
  const { mutate: sendQuery, isPending } = useMutation({
    mutationFn: (req: { question: string; mode: RetrieveMode }) =>
      brainApiService.query({
        question: req.question,
        search_space_id: searchSpaceId,
        retrieve_mode: req.mode,
        force_l0: forceL0,
        ...(sessionId ? { session_id: sessionId } : {}),
      }),
    onSuccess: (response, variables) => {
      log.info("Respuesta Brain recibida", {
        level: response.level_used,
        sources: response.sources.length,
        drill_down: response.drill_down_available,
      });

      if (response.session_id) setSessionId(response.session_id);

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: response.answer,
          levelUsed: response.level_used,
          modelTier: response.model_tier,
          sources: response.sources,
          drillDownAvailable: response.drill_down_available,
          originalQuestion: variables.question,
          timestamp: Date.now(),
        },
      ]);
    },
    onError: (err) => {
      log.error("Error en query Brain", { error: String(err) });
      toast.error("Error al consultar el Brain. Revisa la conexión con el backend.");
    },
  });

  /** Envía la pregunta actual o una pregunta dada con modo override opcional */
  const handleSend = useCallback(
    (questionOverride?: string, modeOverride?: RetrieveMode) => {
      const text = (questionOverride ?? input).trim();
      if (!text || isPending) return;

      const mode = modeOverride ?? effectiveMode;
      log.debug("Enviando mensaje Brain", { question: text.slice(0, 60), mode });

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "user",
          content: text,
          timestamp: Date.now(),
        },
      ]);
      setInput("");
      sendQuery({ question: text, mode });
    },
    [input, isPending, effectiveMode, sendQuery],
  );

  /** Re-envía la misma pregunta forzando retrieval full_source (drill-down L2) */
  const handleDrillDown = useCallback(
    (question: string) => {
      log.debug("Drill-down solicitado", { question: question.slice(0, 60) });
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "user",
          content: "🔍 Ampliar con documentos originales...",
          timestamp: Date.now(),
        },
      ]);
      sendQuery({ question, mode: "full_source" });
    },
    [sendQuery],
  );

  /** Enter → enviar / Shift+Enter → nueva línea */
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  /** Limpia la sesión completa (mensajes + session_id) */
  const handleClearSession = useCallback(() => {
    setMessages([]);
    setSessionId(null);
    log.info("Sesión Brain borrada");
  }, []);

  /** Rellena el input con el texto del hint y enfoca */
  const handleHintClick = useCallback((text: string) => {
    setInput(text);
    textareaRef.current?.focus();
  }, []);

  // Tier del modelo tomado del último mensaje del asistente
  const lastModelTier = messages
    .filter((m) => m.role === "assistant" && m.modelTier)
    .at(-1)?.modelTier;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Header ────────────────────────────────────────────────── */}
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border/50 px-4 py-2.5">
        {/* Título + tier del modelo */}
        <div className="flex items-center gap-2">
          <Brain className="size-4 shrink-0 text-violet-400" />
          <span className="text-sm font-medium">Brain Chat</span>
          {lastModelTier && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
              {lastModelTier}
            </span>
          )}
        </div>

        <div className="flex items-center gap-4">
          {/* Selector retrieve_mode */}
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Modo:</span>
            {modeManuallySet ? (
              <div className="flex items-center gap-1">
                {/* Pill toggle auto | full_source */}
                <button
                  type="button"
                  onClick={() => setRetrieveMode("auto")}
                  aria-pressed={retrieveMode === "auto"}
                  className={cn(
                    "rounded-l-full border border-r-0 px-2.5 py-0.5 text-xs transition-colors",
                    retrieveMode === "auto"
                      ? "border-violet-500/50 bg-violet-500/15 text-violet-300"
                      : "border-border bg-muted text-muted-foreground hover:bg-muted/70",
                  )}
                >
                  auto
                </button>
                <button
                  type="button"
                  onClick={() => setRetrieveMode("full_source")}
                  aria-pressed={retrieveMode === "full_source"}
                  className={cn(
                    "rounded-r-full border px-2.5 py-0.5 text-xs transition-colors",
                    retrieveMode === "full_source"
                      ? "border-amber-500/50 bg-amber-500/15 text-amber-300"
                      : "border-border bg-muted text-muted-foreground hover:bg-muted/70",
                  )}
                >
                  full_source
                </button>
                {/* Volver a detección automática */}
                <button
                  type="button"
                  onClick={() => {
                    setModeManuallySet(false);
                    setRetrieveMode("auto");
                  }}
                  aria-label="Restablecer detección automática"
                  className="ml-1 rounded p-0.5 text-muted-foreground/60 transition-colors hover:text-muted-foreground"
                >
                  <X className="size-3" />
                </button>
              </div>
            ) : (
              /* Indicador de auto-detección — click para activar modo manual */
              <button
                type="button"
                onClick={() => setModeManuallySet(true)}
                className="flex items-center gap-1.5 rounded-full border border-border bg-muted px-2.5 py-0.5 text-xs text-muted-foreground transition-colors hover:border-violet-500/30 hover:bg-violet-500/5"
                title="Haz clic para fijar el modo manualmente"
              >
                <span className="size-1.5 animate-pulse rounded-full bg-emerald-400" />
                auto-detect
              </button>
            )}
          </div>

          {/* Toggle Force LLM libre */}
          <div className="flex items-center gap-1.5">
            <Switch
              id="force-l0"
              checked={forceL0}
              onCheckedChange={setForceL0}
              className="scale-[0.8] data-[state=checked]:bg-amber-500"
            />
            <Label
              htmlFor="force-l0"
              className={cn(
                "cursor-pointer select-none text-xs",
                forceL0 ? "text-amber-300" : "text-muted-foreground",
              )}
            >
              <Zap className="-mt-0.5 mr-0.5 inline size-3" />
              LLM libre
            </Label>
          </div>

          {/* Limpiar sesión */}
          {messages.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClearSession}
              className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
            >
              <X className="size-3" />
              Limpiar
            </Button>
          )}
        </div>
      </header>

      {/* Banner de advertencia Force L0 */}
      {forceL0 && (
        <div className="shrink-0 border-b border-amber-500/20 bg-amber-500/10 px-4 py-1.5">
          <p className="text-xs text-amber-300">
            ⚡ <strong>LLM libre activo</strong> — las respuestas no usarán tus
            documentos
          </p>
        </div>
      )}

      {/* ── Área de mensajes ──────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto scroll-smooth">
        {messages.length === 0 && !isPending ? (
          <HintsScreen onHintClick={handleHintClick} />
        ) : (
          <div className="flex flex-col gap-6 py-6">
            {messages.map((msg) =>
              msg.role === "user" ? (
                <UserMessage key={msg.id} message={msg} />
              ) : (
                <AssistantMessage
                  key={msg.id}
                  message={msg}
                  onDrillDown={handleDrillDown}
                />
              ),
            )}
            {isPending && <ThinkingIndicator />}
            {/* Centinela para auto-scroll */}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* ── Área de input ─────────────────────────────────────────── */}
      <div className="shrink-0 border-t border-border/50 p-3">
        {/* Indicador de modo auto-detectado (solo si es full_source y hay texto) */}
        {!modeManuallySet && detectedMode === "full_source" && input.trim() && (
          <div className="mb-2 flex items-center gap-1.5 px-1">
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-300">
              📄 full_source
            </span>
            <span className="text-[11px] text-muted-foreground">
              Solicitud de fichero detectada automáticamente
            </span>
          </div>
        )}

        {/* Caja de texto + botón enviar */}
        <div
          className={cn(
            "relative flex items-end gap-2 rounded-xl border bg-muted/30 px-3 py-2 transition-colors",
            forceL0
              ? "border-amber-500/30 focus-within:border-amber-500/50"
              : "border-border/60 focus-within:border-violet-500/40",
          )}
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              forceL0
                ? "Pregunta sin retrieval de documentos..."
                : "Pregunta a tu base de conocimiento..."
            }
            disabled={isPending}
            rows={1}
            className="min-h-[28px] max-h-[160px] flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground/50 disabled:cursor-not-allowed disabled:opacity-50"
          />
          <Button
            type="button"
            size="sm"
            disabled={!input.trim() || isPending}
            onClick={() => handleSend()}
            className={cn(
              "h-8 w-8 shrink-0 rounded-lg p-0 transition-all duration-150",
              "bg-violet-600 text-white hover:bg-violet-500",
              "disabled:bg-muted disabled:text-muted-foreground",
            )}
            aria-label="Enviar mensaje"
          >
            {isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Send className="size-3.5" />
            )}
          </Button>
        </div>

        <p className="mt-1.5 text-center text-[10px] text-muted-foreground/40">
          Enter para enviar&nbsp;·&nbsp;Shift+Enter para nueva línea
        </p>
      </div>
    </div>
  );
}


# F9 — Reingeniería UI: Second Brain → Next.js/SurfSense
**Duración:** 3 semanas  
**Equipo:** Frontend Senior (1) + Frontend Mid (1) + UX (0.5)  
**Dependencias:** F6 completada (estructura base Brain en Next.js)  
**Entregable:** UI unificada con todas las capacidades de Second Brain Streamlit reimaginadas en Next.js

---

## Análisis previo: Streamlit vs Next.js — qué migrar y cómo

### Lo que Second Brain hace en Streamlit que SurfSense NO cubre en absoluto

Después de leer todo el código Streamlit, hay **5 capacidades diferenciales** que F6 solo esbozó pero que el código real revela con mucho más detalle:

**1. Recomendación automática de modelo por extensión de fichero**
`brain_ingest.py` tiene un sistema completo (`_MODEL_RECOMMENDATION`, `_best_model_for_ext`, `_show_model_recommendation`) que detecta la extensión del fichero subido y recomienda el modelo Ollama óptimo en tiempo real — `qwen2.5-coder:3b` para `.py`/`.sql`, `deepseek-r1` para `.drawio`/`.md`/`.xlsx`, `llama3.2:3b` para `.pdf`/`.docx`. Esto no existe en SurfSense en absoluto.

**2. Routing preview antes de ingestar**
`_show_routing_preview()` muestra al usuario las colecciones destino (`brain`, `knowledge`, `code`) según la extensión del fichero, **antes** de ingestar. El usuario sabe exactamente a dónde va el documento.

**3. Log de fases de ingesta con estados granulares**
`_build_phase_log()` con 4 fases (Extracción, Síntesis L1, Chunking L2, Vectorización) con estados `ok/warn/error/skip/processing`. SurfSense solo muestra un spinner genérico.

**4. Drill-down inteligente en el chat**
`_drill_down()` en `brain_chat.py` — el usuario puede forzar Nivel 2 desde cualquier respuesta de Nivel 1. El botón "🔍 Ampliar con documentos originales" aparece condicionalmente según `drill_down_available`. Esto es única de tu sistema.

**5. Grafo vis.js con panel de detalle dual**
`brain_graph.py` usa `vis.js` embebido via `components.html` con física forceAtlas2, tooltips HTML enriquecidos, toggle de tag-edges, y panel de detalle lateral con acciones. No es un grafo genérico — tiene 3 tipos de aristas (referencias, serie, tag_overlap) y colores por dominio.

### Lo que Streamlit limita estructuralmente y Next.js resuelve

**Rerender completo en cada interacción.** En Streamlit, cada click en el sidebar de la Wiki recarga todo el árbol de componentes. En Next.js, el panel de detalle se actualiza sin tocar el grafo.

**Sin WebSockets reales.** El log de fases en `brain_ingest.py` usa polling (`time.sleep` en thread separado) porque Streamlit no tiene push real. En Next.js usamos Server-Sent Events para actualización en tiempo real fase a fase.

**Admin hardcodeado en `_DEFAULTS`.** `brain_admin.py` define defaults en código Python. En Next.js los controles están ligados directamente a la API sin doble fuente de verdad.

**Grafo en iframe.** `brain_graph.py` embebe vis.js via `components.html` — un iframe dentro de Streamlit. En Next.js el grafo es un componente React de primera clase con estado compartido.

---

## F9.1 — Brain Ingest reimaginado (Semana 1, Día 1-3)

El componente más diferenciado de tu UI. La reingeniería añade lo que Streamlit no puede dar:

```tsx
// app/(app)/brain/ingest/page.tsx — versión completa

"use client";
import { useState, useCallback, useEffect } from "react";
import { useDropzone } from "react-dropzone";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";

// ── Mapa modelo por extensión (migrado de _MODEL_RECOMMENDATION) ────────────
const MODEL_RECOMMENDATION: Record<string, {
  keyword: string; label: string; reason: string; color: string;
}> = {
  py:       { keyword: "coder:3b",  label: "qwen2.5-coder:3b", reason: "Código fuente",          color: "#34d399" },
  sql:      { keyword: "coder:3b",  label: "qwen2.5-coder:3b", reason: "SQL / procedures",        color: "#34d399" },
  ipynb:    { keyword: "coder:3b",  label: "qwen2.5-coder:3b", reason: "Notebook",                color: "#34d399" },
  json:     { keyword: "coder:3b",  label: "qwen2.5-coder:3b", reason: "JSON estructurado",       color: "#34d399" },
  drawio:   { keyword: "deepseek",  label: "deepseek-r1",      reason: "Diagrama — razonamiento", color: "#a78bfa" },
  pdf:      { keyword: "3.2:3b",    label: "llama3.2:3b",      reason: "Documento general",       color: "#60a5fa" },
  docx:     { keyword: "3.2:3b",    label: "llama3.2:3b",      reason: "Documento Word",          color: "#60a5fa" },
  pptx:     { keyword: "3.2:3b",    label: "llama3.2:3b",      reason: "Presentación",            color: "#60a5fa" },
  xlsx:     { keyword: "deepseek",  label: "deepseek-r1",      reason: "Datos tabulares",         color: "#a78bfa" },
  md:       { keyword: "deepseek",  label: "deepseek-r1",      reason: "Markdown técnico",        color: "#a78bfa" },
  xml:      { keyword: "coder:3b",  label: "qwen2.5-coder:3b", reason: "XML / configuración",     color: "#34d399" },
};

// ── Routing preview (migrado de _show_routing_preview) ─────────────────────
const ROUTING_PREVIEW: Record<string, Array<{ col: string; color: string }>> = {
  py:    [{ col: "brain", color: "#a78bfa" }, { col: "code",      color: "#34d399" }],
  sql:   [{ col: "brain", color: "#a78bfa" }, { col: "code",      color: "#34d399" }],
  json:  [{ col: "brain", color: "#a78bfa" }, { col: "knowledge", color: "#60a5fa" }, { col: "code", color: "#34d399" }],
  ipynb: [{ col: "brain", color: "#a78bfa" }, { col: "knowledge", color: "#60a5fa" }, { col: "code", color: "#34d399" }],
};
const DEFAULT_ROUTING = [{ col: "brain", color: "#a78bfa" }, { col: "knowledge", color: "#60a5fa" }];

// ── Fases de ingesta (migrado de _PHASE_DEFS) ───────────────────────────────
const PHASES = [
  { key: "extraction",    icon: "📥", name: "Extracción",    desc: "Parseo y extracción de bloques" },
  { key: "synthesis",     icon: "🧠", name: "Síntesis L1",   desc: "Pasaporte semántico → brain" },
  { key: "chunking",      icon: "📦", name: "Chunking L2",   desc: "Segmentación → knowledge / code" },
  { key: "vectorization", icon: "🔢", name: "Vectorización", desc: "Embeddings → Qdrant" },
];

type PhaseStatus = "idle" | "running" | "ok" | "warn" | "error" | "skip";

interface PhaseState {
  status: PhaseStatus;
  detail: string;
}

interface IngestResult {
  source: string;
  chunks: { brain: number; knowledge: number; code: number };
  phases: Record<string, PhaseState>;
}

export default function BrainIngestPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [selectedModel, setSelectedModel] = useState("auto");
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [results, setResults] = useState<IngestResult[]>([]);
  const [currentPhases, setCurrentPhases] = useState<Record<string, PhaseState>>({});
  const [ingesting, setIngesting] = useState(false);

  // Cargar modelos disponibles
  useEffect(() => {
    fetch("/api/admin/ollama-models")
      .then(r => r.json())
      .then(d => setAvailableModels(d.models?.filter((m: string) => !m.includes("embed")) ?? []));
  }, []);

  const onDrop = useCallback((accepted: File[]) => {
    setFiles(prev => [...prev, ...accepted]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop });

  // Extensión del primer fichero seleccionado
  const firstExt = files[0]?.name.split(".").pop()?.toLowerCase() ?? "";
  const recommendation = MODEL_RECOMMENDATION[firstExt];
  const routing = ROUTING_PREVIEW[firstExt] ?? DEFAULT_ROUTING;

  // ── SSE: log de fases en tiempo real ────────────────────────────────────
  const handleIngest = async () => {
    if (!files.length) return;
    setIngesting(true);
    setCurrentPhases({});

    for (const file of files) {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("synthesis_model", selectedModel);

      // Abrir SSE stream para el log de fases
      // El backend emite eventos: phase_start, phase_ok, phase_warn, phase_error
      const eventSource = new EventSource(`/api/v1/brain/ingest/stream`);

      eventSource.onmessage = (e) => {
        const event = JSON.parse(e.data);
        setCurrentPhases(prev => ({
          ...prev,
          [event.phase]: { status: event.status, detail: event.detail }
        }));
        if (event.phase === "vectorization" && event.status === "ok") {
          eventSource.close();
        }
      };

      await fetch("/api/v1/documents/fileupload", {
        method: "POST", body: formData,
      });
    }
    setIngesting(false);
  };

  return (
    <div className="p-6 max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-slate-100">Ingestar Documentos</h1>
        <p className="text-sm text-slate-500 mt-1">
          Pipeline 3 fases: Extracción → Síntesis L1 → Chunking L2 → Vectorización
        </p>
      </div>

      {/* Dropzone */}
      <div {...getRootProps()} className={`border-2 border-dashed rounded-xl p-8 text-center
           cursor-pointer transition-colors ${isDragActive ? "border-violet-500 bg-violet-500/5" : "border-slate-700 hover:border-slate-600"}`}>
        <input {...getInputProps()} />
        <div className="text-3xl mb-3">📁</div>
        <p className="text-slate-400 text-sm">
          Arrastra ficheros o haz click · .py .sql .pdf .md .docx .xlsx .pptx .json .ipynb .xml .csv .txt .drawio
        </p>
      </div>

      {/* Modelo recomendado + routing preview — solo si hay fichero */}
      {firstExt && (
        <div className="grid grid-cols-2 gap-4">
          {/* Model recommendation */}
          <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-2">Modelo recomendado</p>
            {recommendation ? (
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold px-2 py-1 rounded"
                      style={{ color: recommendation.color, background: `${recommendation.color}15` }}>
                  ✦ Auto: {recommendation.label}
                </span>
                <span className="text-xs text-slate-500">{recommendation.reason}</span>
              </div>
            ) : (
              <span className="text-xs text-slate-500">Modelo por defecto del servidor</span>
            )}
            <select
              value={selectedModel}
              onChange={e => setSelectedModel(e.target.value)}
              className="mt-3 w-full text-xs bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-300"
            >
              <option value="auto">🤖 Auto (recomendado)</option>
              {availableModels.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>

          {/* Routing preview */}
          <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-2">Destino en Qdrant</p>
            <div className="flex items-center gap-2 flex-wrap mt-1">
              {routing.map(r => (
                <span key={r.col} className="text-xs font-semibold px-3 py-1 rounded-full border"
                      style={{ color: r.color, borderColor: `${r.color}44`, background: `${r.color}10` }}>
                  → {r.col}
                </span>
              ))}
            </div>
            <p className="text-xs text-slate-600 mt-3">
              Ficheros con código van a <code>code</code> (2560d) · Resto a <code>knowledge</code> (768d)
            </p>
          </div>
        </div>
      )}

      {/* Lista de ficheros */}
      {files.length > 0 && (
        <div className="space-y-2">
          {files.map((f, i) => {
            const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
            const FORMAT_ICONS: Record<string, string> = {
              py: "🐍", sql: "🗃️", pdf: "📄", docx: "📝", xlsx: "📊",
              pptx: "📊", md: "📋", json: "⚙️", ipynb: "📓", drawio: "🗺️",
            };
            return (
              <div key={i} className="flex items-center gap-3 bg-slate-900 border border-slate-800 rounded-lg px-4 py-2">
                <span>{FORMAT_ICONS[ext] ?? "📄"}</span>
                <span className="text-sm text-slate-300 flex-1 truncate">{f.name}</span>
                <span className="text-xs text-slate-600">{(f.size / 1024).toFixed(1)} KB</span>
                <button onClick={() => setFiles(fs => fs.filter((_, j) => j !== i))}
                        className="text-slate-600 hover:text-red-400 transition-colors">✕</button>
              </div>
            );
          })}
        </div>
      )}

      <button
        onClick={handleIngest}
        disabled={!files.length || ingesting}
        className="w-full py-3 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white font-semibold rounded-lg transition-colors"
      >
        {ingesting ? "Procesando..." : `Ingestar ${files.length} fichero(s)`}
      </button>

      {/* Log de fases en tiempo real */}
      {Object.keys(currentPhases).length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
          <p className="text-xs text-slate-500 uppercase tracking-wider">Pipeline de ingesta</p>
          {PHASES.map(phase => {
            const state = currentPhases[phase.key];
            const STATUS_STYLES: Record<PhaseStatus, { color: string; icon: string }> = {
              idle:       { color: "#374151", icon: "○" },
              running:    { color: "#f59e0b", icon: "⟳" },
              ok:         { color: "#34d399", icon: "✓" },
              warn:       { color: "#f59e0b", icon: "⚠" },
              error:      { color: "#ef4444", icon: "✗" },
              skip:       { color: "#374151", icon: "—" },
            };
            const style = STATUS_STYLES[state?.status ?? "idle"];
            return (
              <div key={phase.key} className="flex items-start gap-3">
                <span className="text-lg w-6 text-center">{phase.icon}</span>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-300">{phase.name}</span>
                    <span className="text-xs font-bold" style={{ color: style.color }}>
                      {style.icon} {state?.status ?? "esperando"}
                    </span>
                  </div>
                  {state?.detail && (
                    <p className="text-xs text-slate-500 mt-0.5">{state.detail}</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

---

## F9.2 — Brain Chat con drill-down (Semana 1, Día 3-5)

La mejora crítica sobre F6: el botón de drill-down condicional y el `retrieve_mode` automático.

```tsx
// Añadir a app/(app)/brain/chat/page.tsx

// ── Detección retrieve_mode (migrado de _retrieve_mode_from_question) ────────
const FILE_REQUEST_PATTERNS = ["fichero","archivo","documento","completo","entero","full file","raw file"];
const FILENAME_RE = /([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,10})/;

function detectRetrieveMode(question: string): "auto" | "full_source" {
  const q = question.toLowerCase();
  const hasFilename = FILENAME_RE.test(question);
  const asksFile = FILE_REQUEST_PATTERNS.some(p => q.includes(p));
  return hasFilename && asksFile ? "full_source" : "auto";
}

// ── Hint cards estilo Perplexity (migrado de _HINTS) ──────────────────────
const HINTS = [
  { icon: "💬", text: "¿De qué tratan mis documentos?" },
  { icon: "📝", text: "Resume el plan RAG" },
  { icon: "🔍", text: "¿Qué docs tengo sobre IA?" },
  { icon: "✂️", text: "Cita exacta sobre chunking" },
  { icon: "💻", text: "¿Qué funciones implementa chunking.py?" },
  { icon: "📄", text: "Muestra el código del extractor PDF" },
];

// ── Level badges (migrado de _LEVEL_BADGE) ────────────────────────────────
const LEVEL_BADGES: Record<string | number, { label: string; color: string }> = {
  0:       { label: "🤖 LLM",       color: "#f59e0b" },
  1:       { label: "🧠 brain",      color: "#a78bfa" },
  2:       { label: "🔍 knowledge",  color: "#60a5fa" },
  3:       { label: "🌐 Web",        color: "#34d399" },  // CRAG F8
  "code":  { label: "💻 code",       color: "#34d399" },
};

// ── Mensaje con drill-down ─────────────────────────────────────────────────
interface AssistantMessage {
  content: string;
  level: number;
  collection: string;
  sources: Array<{ source: string; score: number; section?: string; page?: string }>;
  drillDownAvailable: boolean;
  model: string;
  question: string;  // para el drill-down
}

function AssistantBubble({
  msg, onDrillDown
}: {
  msg: AssistantMessage;
  onDrillDown: (question: string) => void;
}) {
  const badge = LEVEL_BADGES[msg.collection === "code" ? "code" : msg.level];

  return (
    <div className="flex gap-3">
      <span className="text-2xl mt-1">🧠</span>
      <div className="flex-1 space-y-2">
        {/* Badge de nivel */}
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold px-2 py-0.5 rounded border"
                style={{ color: badge.color, borderColor: `${badge.color}33`, background: `${badge.color}10` }}>
            {badge.label}
          </span>
          {msg.model && (
            <span className="text-xs text-slate-600">{msg.model}</span>
          )}
        </div>

        {/* Respuesta */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-3">
          <ReactMarkdown>{msg.content}</ReactMarkdown>
        </div>

        {/* Fuentes con score dots */}
        {msg.sources.length > 0 && (
          <details className="text-xs">
            <summary className="text-slate-500 cursor-pointer hover:text-slate-400">
              📎 {msg.sources.length} fuente(s) consultadas
            </summary>
            <div className="mt-2 space-y-1 pl-2">
              {msg.sources.map((s, i) => {
                const dotColor = s.score > 0.75 ? "#16a34a" : s.score >= 0.55 ? "#ca8a04" : "#dc2626";
                return (
                  <div key={i} className="flex items-center gap-2">
                    <span style={{ color: dotColor }}>●</span>
                    <span className="text-slate-400 font-medium">{s.source}</span>
                    {s.section && <span className="text-slate-600">§{s.section}</span>}
                    {s.page && <span className="text-slate-600">p.{s.page}</span>}
                    <span className="text-slate-600 ml-auto">{s.score.toFixed(3)}</span>
                  </div>
                );
              })}
            </div>
          </details>
        )}

        {/* Drill-down — solo si disponible y nivel es 1 (brain) */}
        {msg.drillDownAvailable && msg.level === 1 && (
          <button
            onClick={() => onDrillDown(msg.question)}
            className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1 transition-colors"
          >
            🔍 Ampliar con documentos originales (Nivel 2)
          </button>
        )}
      </div>
    </div>
  );
}
```

---

## F9.3 — Brain Wiki con cards enriquecidas (Semana 2, Día 1-3)

La migración más compleja por la riqueza de metadata. Las cards de Streamlit usan HTML inline — las reescribimos como componentes React reutilizables.

```tsx
// components/brain/passport-card.tsx

interface PassportCardProps {
  source: string;
  title: string;
  docType: string;
  domain: string;
  subdomain?: string;
  tags: string[];
  importance: number;        // 1-5
  confidence: number;        // 0-1
  updatedAt: string;
  embeddingScope: string[];
  hasPii: boolean;
  ingestOrigin?: string;
  ingestPath?: string;
  onOpen: () => void;
}

const DOMAIN_COLORS: Record<string, string> = {
  engineering: "#818cf8",
  business:    "#38bdf8",
  functional:  "#4ade80",
  legal:       "#f87171",
  data:        "#fb923c",
  other:       "#6b7280",
};

const SCOPE_COLORS: Record<string, string> = {
  brain:     "#a78bfa",
  knowledge: "#60a5fa",
  code:      "#34d399",
};

const ORIGIN_META: Record<string, { icon: string; label: string }> = {
  file_upload: { icon: "📁", label: "Subida" },
  local_path:  { icon: "📂", label: "Ruta local" },
  sharepoint:  { icon: "☁️", label: "SharePoint" },
  url:         { icon: "🌐", label: "URL" },
};

export function PassportCard({
  source, title, docType, domain, subdomain, tags,
  importance, confidence, updatedAt, embeddingScope,
  hasPii, ingestOrigin, ingestPath, onOpen
}: PassportCardProps) {
  const domainColor = DOMAIN_COLORS[domain] ?? DOMAIN_COLORS.other;
  const origin = ORIGIN_META[ingestOrigin ?? ""] ?? { icon: "📄", label: ingestOrigin };

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 hover:shadow-md
                    transition-all cursor-pointer group"
         onClick={onOpen}
         style={{ borderLeft: `3px solid ${domainColor}` }}>

      {/* Header: tipo + dominio */}
      <div className="flex items-start justify-between mb-2">
        <span className="text-xs font-bold px-2 py-0.5 rounded uppercase tracking-wider"
              style={{ color: domainColor, background: `${domainColor}12` }}>
          {docType}
        </span>
        {hasPii && (
          <span className="text-xs bg-red-100 text-red-600 border border-red-200 px-2 py-0.5 rounded">
            ⚠️ PII
          </span>
        )}
      </div>

      {/* Título */}
      <h3 className="font-bold text-sm text-slate-800 leading-snug mb-2 line-clamp-2">
        {title}
      </h3>

      {/* Tags (máx 5) */}
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {tags.slice(0, 5).map(tag => (
            <span key={tag} className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded">
              #{tag}
            </span>
          ))}
        </div>
      )}

      {/* Embedding scope */}
      <div className="flex gap-1 mb-3">
        {embeddingScope.map(scope => (
          <span key={scope} className="text-xs font-semibold px-2 py-0.5 rounded-full border"
                style={{ color: SCOPE_COLORS[scope] ?? "#6b7280",
                         borderColor: `${SCOPE_COLORS[scope] ?? "#6b7280"}44`,
                         background: `${SCOPE_COLORS[scope] ?? "#6b7280"}10` }}>
            {scope}
          </span>
        ))}
      </div>

      {/* Origen */}
      {ingestOrigin && (
        <div className="text-xs text-slate-500 mb-2">
          {origin.icon} {origin.label}
          {ingestPath && (
            <span className="text-slate-400 ml-1 font-mono truncate block max-w-full">
              {ingestPath.length > 40 ? `...${ingestPath.slice(-40)}` : ingestPath}
            </span>
          )}
        </div>
      )}

      {/* Footer: importancia + confianza + fecha */}
      <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
        {/* Importancia como dots */}
        <span className="text-xs text-slate-400 tracking-tighter">
          {"●".repeat(importance)}{"○".repeat(5 - importance)}
        </span>
        {/* Confianza */}
        <span className="text-xs" style={{
          color: confidence > 0.7 ? "#16a34a" : confidence > 0.4 ? "#ca8a04" : "#dc2626"
        }}>
          ● {(confidence * 100).toFixed(0)}%
        </span>
        <span className="text-xs text-slate-400 ml-auto">
          🗓 {updatedAt.slice(0, 10)}
        </span>
      </div>
    </div>
  );
}
```

**Vista detalle del pasaporte (reimaginada de `_render_doc_detail`):**

```tsx
// app/(app)/brain/wiki/[source]/page.tsx

// La vista de detalle añade lo que Streamlit no puede:
// - Editor Monaco (en lugar del textarea de Streamlit)
// - Preview Markdown en tiempo real lado a lado con el editor
// - Selector de modelo para re-síntesis integrado en el botón
// - Historial de versiones del pasaporte (nuevo — no está en Streamlit)
```

---

## F9.4 — Brain Graph reimaginado con react-force-graph (Semana 2, Día 3-5)

El grafo actual en Streamlit usa vis.js embebido en un iframe. En Next.js es un componente React de primera clase.

**Las mejoras sobre el Streamlit actual:**

El panel de detalle en Streamlit es un `st.columns([6,4])` que se recarga con cada selección. En Next.js el panel de detalle se actualiza sin rerenderizar el grafo — el grafo mantiene su estado de zoom y posición.

El toggle de tag-edges en Streamlit requiere `st.rerun()`. En Next.js es estado local que filtra las aristas sin tocar el grafo.

Los botones "Abrir en Wiki" y "Preguntar al Brain" en Streamlit cambian `st.session_state["sb_section"]` y hacen `st.rerun()`. En Next.js son `router.push()` instantáneos.

```tsx
// app/(app)/brain/graph/page.tsx — mejoras sobre brain_graph.py

"use client";
import dynamic from "next/dynamic";
import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";

const ForceGraph2D = dynamic(() => import("react-force-graph").then(m => m.ForceGraph2D), {
  ssr: false
});

// Paleta de dominios (migrada de _DOMAIN_PALETTE)
const DOMAIN_COLORS: Record<string, string> = {
  engineering: "#818cf8",
  business:    "#38bdf8",
  functional:  "#4ade80",
  legal:       "#f87171",
  data:        "#fb923c",
  other:       "#6b7280",
};

// Tipos de aristas (migrados de _build_vis_edges)
const EDGE_STYLES = {
  related:     { color: "#6366f1", dashed: false, label: "Referencia explícita" },
  serie:       { color: "#0891b2", dashed: true,  label: "Misma serie" },
  tag_overlap: { color: "#374151", dashed: true,  label: "Tags compartidos" },
};

export default function BrainGraphPage() {
  const router = useRouter();
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [selected, setSelected] = useState<any>(null);
  const [showTagEdges, setShowTagEdges] = useState(false);
  const [filterDomains, setFilterDomains] = useState<string[]>([]);
  const [filterTags, setFilterTags] = useState<string[]>([]);

  useEffect(() => {
    fetch("/api/brain/graph")
      .then(r => r.json())
      .then(data => {
        // Transformar formato vis.js → react-force-graph
        const nodes = data.nodes.map((n: any) => ({
          id: n.id,
          label: n.label,
          summary: n.summary,
          tags: n.tags ?? [],
          domain: n.domain ?? "other",
          importance: n.importance ?? 3,
          docType: n.type ?? "",
          color: DOMAIN_COLORS[n.domain ?? "other"] ?? "#6b7280",
          val: 3 + (n.importance ?? 3),
        }));

        const links = data.edges
          .filter((e: any) => showTagEdges || e.type !== "tag_overlap")
          .map((e: any) => ({
            source: e.source,
            target: e.target,
            type: e.type,
            color: EDGE_STYLES[e.type as keyof typeof EDGE_STYLES]?.color ?? "#374151",
          }));

        setGraphData({ nodes, links });
      });
  }, [showTagEdges]);

  return (
    <div className="flex h-full">
      {/* Controles */}
      <div className="w-64 border-r border-slate-800 p-4 space-y-4">
        <div>
          <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer">
            <input type="checkbox" checked={showTagEdges}
                   onChange={e => setShowTagEdges(e.target.checked)}
                   className="rounded" />
            Mostrar tag-links
          </label>
        </div>
        {/* Filtros por dominio */}
        <div>
          <p className="text-xs text-slate-600 uppercase tracking-wider mb-2">Filtrar dominios</p>
          {Object.entries(DOMAIN_COLORS).map(([domain, color]) => (
            <label key={domain} className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer mb-1">
              <input type="checkbox"
                     checked={filterDomains.length === 0 || filterDomains.includes(domain)}
                     onChange={e => {
                       if (e.target.checked) setFilterDomains(prev => [...prev, domain]);
                       else setFilterDomains(prev => prev.filter(d => d !== domain));
                     }} />
              <span style={{ color }}>{domain}</span>
            </label>
          ))}
        </div>
      </div>

      {/* Grafo */}
      <div className="flex-1 relative">
        <ForceGraph2D
          graphData={graphData}
          nodeLabel={(node: any) => `${node.label}\n${node.summary?.slice(0, 80) ?? ""}`}
          nodeColor={(node: any) => node.color}
          nodeVal={(node: any) => node.val}
          linkColor={(link: any) => link.color}
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={1}
          onNodeClick={(node: any) => setSelected(node)}
          onNodeDoubleClick={(node: any) => router.push(`/brain/wiki/${node.id}`)}
          cooldownTicks={100}
        />
      </div>

      {/* Panel de detalle */}
      {selected && (
        <div className="w-80 border-l border-slate-800 p-4 overflow-y-auto">
          <div className="flex items-start justify-between mb-3">
            <h3 className="font-bold text-slate-100 text-sm leading-snug flex-1">{selected.label}</h3>
            <button onClick={() => setSelected(null)} className="text-slate-600 hover:text-slate-400 ml-2">✕</button>
          </div>

          <div className="flex flex-wrap gap-1 mb-3">
            <span className="text-xs px-2 py-0.5 rounded"
                  style={{ color: DOMAIN_COLORS[selected.domain], background: `${DOMAIN_COLORS[selected.domain]}15` }}>
              🗂 {selected.domain}
            </span>
            {selected.docType && (
              <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                {selected.docType}
              </span>
            )}
          </div>

          {selected.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-3">
              {selected.tags.slice(0, 6).map((t: string) => (
                <span key={t} className="text-xs bg-slate-800 text-slate-400 px-2 py-0.5 rounded">#{t}</span>
              ))}
            </div>
          )}

          {selected.summary && (
            <p className="text-xs text-slate-500 leading-relaxed mb-4 bg-slate-900 rounded-lg p-3">
              {selected.summary}
            </p>
          )}

          <div className="space-y-2">
            <button
              onClick={() => router.push(`/brain/wiki/${selected.id}`)}
              className="w-full text-sm py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition-colors"
            >
              📖 Abrir en Wiki
            </button>
            <button
              onClick={() => router.push(`/brain/chat?q=${encodeURIComponent(`¿Qué información hay sobre ${selected.label}?`)}`)}
              className="w-full text-sm py-2 bg-violet-900/50 hover:bg-violet-900 text-violet-300 border border-violet-800 rounded-lg transition-colors"
            >
              💬 Preguntar al Brain
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
```

---

## F9.5 — Brain Admin reimaginado (Semana 3, Día 1-2)

`brain_admin.py` tiene 5 tabs con configuración en caliente. En Next.js la ventaja es que los controles se actualizan sin `st.rerun()`.

**Mejoras sobre el Streamlit:**

El banner de estado en Streamlit se renderiza con HTML inline en Python. En Next.js es un componente tipado con estados claros.

Los sliders y selectboxes en Streamlit usan `session_state` como intermediario. En Next.js el formulario gestiona su propio estado con `useForm` y envía el payload de una vez.

La sección de variables de entorno en Streamlit genera HTML con f-strings Python. En Next.js es un componente de tabla limpio con copy-to-clipboard.

```tsx
// app/(app)/admin/brain/page.tsx — estructura de tabs

const ADMIN_TABS = [
  { id: "chunking",    label: "✂️ Chunking",    component: ChunkingConfig },
  { id: "retrieval",  label: "🔍 Retrieval",   component: RetrievalConfig },
  { id: "llm",        label: "🤖 LLM",         component: LLMConfig },
  { id: "collections",label: "🗄️ Colecciones", component: CollectionsConfig },
  { id: "system",     label: "🖥️ Sistema",     component: SystemPanel },
];

// ChunkingConfig: strategy (paragraph/fixed/semantic), chunk_size, overlap
// RetrievalConfig: min_score, top_k, reranking, router thresholds L1_HIGH/L1_MIN
// LLMConfig: provider, ollama_model, temperature, synthesis_max_tokens
// CollectionsConfig: stats brain/knowledge/code/md_files + zona peligrosa recrear
// SystemPanel: health check API/Qdrant/Ollama + env vars + links rápidos
```

---

## Dependencias adicionales para F9

```bash
# package.json additions
"react-force-graph": "^1.44.0",    # grafo — sustituye vis.js embebido
"@monaco-editor/react": "^4.6.0",  # editor para pasaportes wiki
"react-markdown": "^9.0.0",        # render markdown en chat y wiki
"remark-gfm": "^4.0.0",            # GitHub Flavored Markdown
"react-dropzone": "^14.3.0",       # dropzone ingest
"eventsource": "^3.0.0",           # SSE para log de fases
```

---

## Mapa completo de migración Streamlit → Next.js

| Componente Streamlit | Vista Next.js | Cambio principal |
|---|---|---|
| `brain_ingest.py` → `render_brain_ingest()` | `/brain/ingest` | SSE real para log de fases; model rec. sin rerun |
| `brain_chat.py` → `render_brain_chat()` | `/brain/chat` | drill-down sin rerun; hints sin rerun |
| `brain_wiki.py` → `render_brain_wiki()` | `/brain/wiki` | cards React; Monaco editor; preview lado a lado |
| `brain_wiki.py` → `_render_doc_detail()` | `/brain/wiki/[source]` | Monaco + historial de versiones |
| `brain_graph.py` → `render_brain_graph()` | `/brain/graph` | react-force-graph nativo; panel sin rerun |
| `brain_admin.py` → `render_brain_admin()` | `/admin/brain` | form unificado; copy env vars; live config |
| `sidebar.py` → `render_sidebar()` | Sidebar global Next.js | Biblioteca docs con metadata rica |
| `chat.py` → `render_chat()` | Integrado en `/brain/chat` | Unificado con Brain Chat |
| `upload.py` → `render_upload()` | Integrado en `/brain/ingest` | Dropzone + URL + URL ingest unificados |
| `sources.py` → `render_sources()` | Componente `SourcesList` | Score dots inline sin expander |

---

## Checklist F9

- [ ] `/brain/ingest`: dropzone + model recommendation + routing preview + SSE log de fases
- [ ] `/brain/chat`: hints estilo Perplexity + badge nivel + drill-down condicional + retrieve_mode auto
- [ ] `/brain/wiki`: cards con importance/confidence/origin/scope + filtros multiselect
- [ ] `/brain/wiki/[source]`: Monaco editor + preview MD + re-síntesis con selector de modelo
- [ ] `/brain/graph`: react-force-graph + panel detalle sin rerender + toggle tag-edges
- [ ] `/admin/brain`: 5 tabs config en caliente + health check + env vars con copy + links rápidos
- [ ] Sidebar global: biblioteca de documentos con metadata rich (date, origin, chunks)
- [ ] SSE endpoint en FastAPI para streaming del log de fases
- [ ] Monaco editor para edición de pasaportes .md
- [ ] Historial de versiones de pasaportes (nuevo — no estaba en Streamlit)

---

**Anterior:** [F8 — CRAG Agente Evaluador](./F8-crag-agente-evaluador.md)  
**Volver al plan maestro:** [00-plan-maestro.md](./00-plan-maestro.md)

/**
 * @file constants.ts
 * @module lib/brain
 *
 * Constantes centralizadas del módulo SecondBrainSense.
 *
 * REGLA: ningún componente ni servicio del módulo Brain debe tener
 * valores literales hardcodeados. Toda constante va aquí y se importa
 * desde este fichero.
 */

// ─────────────────────────────────────────────────────────────────────────────
// API — rutas de endpoints Brain
// ─────────────────────────────────────────────────────────────────────────────

/** Prefijo base para todos los endpoints del módulo Brain */
export const BRAIN_API_PREFIX = "/api/v1/brain" as const;

/** Prefijo para endpoints de administración Brain */
export const BRAIN_ADMIN_API_PREFIX = "/api/v1/admin" as const;

/** Endpoints completos */
export const BRAIN_ENDPOINTS = {
  /** Consulta Brain con cascada multinivel L1→L2→BM25→Web→L0 */
  QUERY:            `${BRAIN_API_PREFIX}/query`,
  /** Lista de pasaportes con metadata */
  LIST:             `${BRAIN_API_PREFIX}/list`,
  /** Stats de colecciones Qdrant + última ingesta */
  STATS:            `${BRAIN_API_PREFIX}/stats`,
  /** Grafo de conocimiento: nodos y aristas */
  GRAPH:            `${BRAIN_API_PREFIX}/graph`,
  /** Distribución de niveles de respuesta */
  LEVEL_USAGE:      `${BRAIN_API_PREFIX}/level-usage`,
  /** Pasaporte de un documento (GET/PUT) */
  PASSPORT:         (source: string) => `${BRAIN_API_PREFIX}/passport/${encodeURIComponent(source)}`,
  /** Historial de versiones de un pasaporte */
  PASSPORT_HISTORY: (source: string) => `${BRAIN_API_PREFIX}/passport/${encodeURIComponent(source)}/history`,
  /** Re-sintetizar pasaporte */
  RESYNTHESIZE:     (source: string) => `${BRAIN_API_PREFIX}/document/${encodeURIComponent(source)}/resynthesize`,
  /** Eliminar documento Brain */
  DELETE_DOCUMENT:  (source: string) => `${BRAIN_API_PREFIX}/document/${encodeURIComponent(source)}`,
  /** Ingestión de URL */
  INGEST_URL:       `${BRAIN_API_PREFIX}/ingest/url`,
  /** Stream SSE de fases de ingesta */
  INGEST_STREAM:    (jobId: string) => `${BRAIN_API_PREFIX}/ingest/stream?job_id=${encodeURIComponent(jobId)}`,
  /** Configuración activa del admin Brain */
  ADMIN_CONFIG:     `${BRAIN_ADMIN_API_PREFIX}/config`,
  /** Modelos Ollama disponibles */
  OLLAMA_MODELS:    `${BRAIN_ADMIN_API_PREFIX}/ollama-models`,
  /** Health check de servicios */
  HEALTH:           "/api/v1/health",
  /** CRUD dominios */
  ADMIN_DOMAINS:    `${BRAIN_API_PREFIX}/admin/domains`,
  /** CRUD tipos de documento */
  ADMIN_DOC_TYPES:  `${BRAIN_API_PREFIX}/admin/doc-types`,
  /** CRUD entity hints */
  ADMIN_ENTITY_HINTS: `${BRAIN_API_PREFIX}/admin/entity-hints`,
  /** CRUD vocabulario canónico */
  ADMIN_VOCABULARY: `${BRAIN_API_PREFIX}/admin/vocabulary`,
  /** Clasificar un formato de fichero */
  ADMIN_CLASSIFY:   `${BRAIN_API_PREFIX}/admin/classify`,
} as const;

// ─────────────────────────────────────────────────────────────────────────────
// Rutas de navegación Brain (Next.js)
// ─────────────────────────────────────────────────────────────────────────────

/** Genera rutas Brain relativas a un search_space_id dado */
export const BRAIN_ROUTES = {
  HOME:       (spaceId: string) => `/dashboard/${spaceId}/brain/home`,
  CHAT:       (spaceId: string) => `/dashboard/${spaceId}/brain/chat`,
  WIKI:       (spaceId: string) => `/dashboard/${spaceId}/brain/wiki`,
  WIKI_ITEM:  (spaceId: string, source: string) => `/dashboard/${spaceId}/brain/wiki/${encodeURIComponent(source)}`,
  GRAPH:      (spaceId: string) => `/dashboard/${spaceId}/brain/graph`,
  INGEST:     (spaceId: string) => `/dashboard/${spaceId}/brain/ingest`,
  METRICS:    (spaceId: string) => `/dashboard/${spaceId}/brain/metrics`,
  ADMIN:      (spaceId: string) => `/dashboard/${spaceId}/brain/admin`,
  VOCABULARY: (spaceId: string) => `/dashboard/${spaceId}/brain/vocabulary`,
} as const;

// ─────────────────────────────────────────────────────────────────────────────
// Niveles de retrieval
// ─────────────────────────────────────────────────────────────────────────────

/** Identificadores numéricos de nivel de retrieval */
export const BRAIN_LEVELS = {
  L1:   1,  // Brain — pasaportes (colección brain)
  L2:   2,  // Knowledge semántico (colección knowledge)
  BM25: 3,  // BM25 léxico (PostgreSQL tsvector)
  WEB:  4,  // Búsqueda web (SearXNG)
  L0:   0,  // LLM libre (sin retrieval)
} as const;

export type BrainLevel = (typeof BRAIN_LEVELS)[keyof typeof BRAIN_LEVELS];

/** Configuración visual de cada nivel (label + clases Tailwind) */
export const BRAIN_LEVEL_CONFIG: Record<BrainLevel, { label: string; classes: string }> = {
  [BRAIN_LEVELS.L1]:   { label: "🧠 Brain",     classes: "bg-violet-500/15 text-violet-300 border-violet-500/30" },
  [BRAIN_LEVELS.L2]:   { label: "📚 Knowledge", classes: "bg-blue-500/15 text-blue-300 border-blue-500/30" },
  [BRAIN_LEVELS.BM25]: { label: "🔍 BM25",      classes: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30" },
  [BRAIN_LEVELS.WEB]:  { label: "🌐 Web",       classes: "bg-green-500/15 text-green-300 border-green-500/30" },
  [BRAIN_LEVELS.L0]:   { label: "🤖 LLM",       classes: "bg-muted text-muted-foreground border-border" },
};

// ─────────────────────────────────────────────────────────────────────────────
// Dominios de conocimiento
// ─────────────────────────────────────────────────────────────────────────────

export const BRAIN_DOMAINS = ["engineering", "data", "business", "functional", "legal", "other"] as const;
export type BrainDomain = (typeof BRAIN_DOMAINS)[number];

/** Configuración visual de cada dominio */
export const BRAIN_DOMAIN_CONFIG: Record<BrainDomain, { label: string; classes: string }> = {
  engineering: { label: "Engineering", classes: "bg-violet-500/15 text-violet-300" },
  data:        { label: "Data",        classes: "bg-orange-500/15 text-orange-300" },
  business:    { label: "Business",    classes: "bg-sky-500/15 text-sky-300"       },
  functional:  { label: "Functional",  classes: "bg-green-500/15 text-green-300"   },
  legal:       { label: "Legal",       classes: "bg-red-500/15 text-red-300"       },
  other:       { label: "Other",       classes: "bg-muted text-muted-foreground"   },
};

// ─────────────────────────────────────────────────────────────────────────────
// Score RAG — umbrales de relevancia
// ─────────────────────────────────────────────────────────────────────────────

/** Umbral por encima del cual un score se considera alta relevancia */
export const BRAIN_SCORE_HIGH_THRESHOLD   = 0.70;
/** Umbral por encima del cual un score se considera relevancia media */
export const BRAIN_SCORE_MEDIUM_THRESHOLD = 0.55;

export type ScoreVariant = "high" | "medium" | "low";

/** Clases Tailwind para cada variante de score */
export const BRAIN_SCORE_CLASSES: Record<ScoreVariant, string> = {
  high:   "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  medium: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  low:    "bg-red-500/20 text-red-300 border-red-500/30",
};

// ─────────────────────────────────────────────────────────────────────────────
// Scopes de indexación Qdrant
// ─────────────────────────────────────────────────────────────────────────────

export const BRAIN_SCOPES = ["brain", "knowledge", "code"] as const;
export type BrainScope = (typeof BRAIN_SCOPES)[number];

/** Color CSS var por scope (referencia a design tokens de globals.css) */
export const BRAIN_SCOPE_COLORS: Record<BrainScope, string> = {
  brain:     "var(--brain-scope-brain)",
  knowledge: "var(--brain-scope-knowledge)",
  code:      "var(--brain-scope-code)",
};

// ─────────────────────────────────────────────────────────────────────────────
// Modelos recomendados por extensión de fichero
// Fuente: _MODEL_RECOMMENDATION de brain_ingest.py
// ─────────────────────────────────────────────────────────────────────────────

export const BRAIN_MODEL_RECOMMENDATION: Record<string, string> = {
  ".py":    "qwen2.5-coder:3b",
  ".sql":   "qwen2.5-coder:3b",
  ".ipynb": "qwen2.5-coder:3b",
  ".json":  "qwen2.5-coder:3b",
  ".xml":   "qwen2.5-coder:3b",
  ".drawio":"deepseek-r1",
  ".xlsx":  "deepseek-r1",
  ".md":    "deepseek-r1",
  ".pdf":   "llama3.2:3b",
  ".docx":  "llama3.2:3b",
  ".pptx":  "llama3.2:3b",
  ".txt":   "llama3.2:3b",
};

/** Modelo de síntesis por defecto cuando la extensión no está en el mapa */
export const BRAIN_DEFAULT_MODEL = "llama3.2:3b" as const;

// ─────────────────────────────────────────────────────────────────────────────
// Routing preview — colecciones destino por extensión
// ─────────────────────────────────────────────────────────────────────────────

export const BRAIN_ROUTING_PREVIEW: Record<string, BrainScope[]> = {
  ".py":    ["brain", "code"],
  ".sql":   ["brain", "code"],
  ".ipynb": ["brain", "code"],
  ".json":  ["brain", "code"],
  ".xml":   ["brain", "knowledge"],
  ".drawio":["brain", "knowledge"],
  ".xlsx":  ["brain", "knowledge"],
  ".md":    ["brain", "knowledge"],
  ".pdf":   ["brain", "knowledge"],
  ".docx":  ["brain", "knowledge"],
  ".pptx":  ["brain", "knowledge"],
  ".txt":   ["brain", "knowledge"],
};

// ─────────────────────────────────────────────────────────────────────────────
// Ingesta — patrones de detección de retrieve_mode
// Fuente: brain_chat.py → detectRetrieveMode()
// ─────────────────────────────────────────────────────────────────────────────

/** Palabras clave que, combinadas con un nombre de fichero, activan retrieve_mode=full_source */
export const BRAIN_FILE_REQUEST_PATTERNS = [
  "fichero", "archivo", "documento", "completo", "entero",
  "full file", "raw file",
] as const;

/** Regex para detectar nombres de fichero en la pregunta */
export const BRAIN_FILENAME_REGEX = /([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,10})/;

// ─────────────────────────────────────────────────────────────────────────────
// Chat Brain — hints de pantalla inicial
// Fuente: brain_chat.py → HINTS
// ─────────────────────────────────────────────────────────────────────────────

export const BRAIN_CHAT_HINTS = [
  { icon: "💬", text: "¿De qué tratan mis documentos?" },
  { icon: "📝", text: "Resume el plan RAG" },
  { icon: "🔍", text: "¿Qué docs tengo sobre IA?" },
  { icon: "✂️", text: "Cita exacta sobre chunking" },
  { icon: "💻", text: "¿Qué funciones implementa chunking.py?" },
  { icon: "📄", text: "Muestra el código del extractor PDF" },
] as const;

// ─────────────────────────────────────────────────────────────────────────────
// Iconos de origen de conector (para PassportCard y Wiki)
// ─────────────────────────────────────────────────────────────────────────────

export const BRAIN_CONNECTOR_ICONS: Record<string, string> = {
  github:       "🐙",
  confluence:   "📘",
  notion:       "✍️",
  jira:         "🎯",
  slack:        "💬",
  google_drive: "📁",
  onedrive:     "☁️",
  local:        "💾",
  upload:       "📁",
  sharepoint:   "☁️",
  url:          "🌐",
};

/** Icono de conector por defecto cuando el tipo no está mapeado */
export const BRAIN_CONNECTOR_DEFAULT_ICON = "📄" as const;

// ─────────────────────────────────────────────────────────────────────────────
// Ingesta — límites operacionales
// ─────────────────────────────────────────────────────────────────────────────

/** Tamaño en bytes a partir del cual la ingesta pasa a modo background */
export const BRAIN_INGEST_BACKGROUND_THRESHOLD_BYTES = 50 * 1024; // 50 KB

/** Número máximo de ingestas recientes mostradas en el historial */
export const BRAIN_INGEST_HISTORY_LIMIT = 10;

// ─────────────────────────────────────────────────────────────────────────────
// Admin CRAG — límites de configuración UI
// ─────────────────────────────────────────────────────────────────────────────

export const BRAIN_CRAG_TIMEOUT_MIN = 5;
export const BRAIN_CRAG_TIMEOUT_MAX = 30;
export const BRAIN_CRAG_TIMEOUT_DEFAULT = 15;

# F6 — UI Unificada: SecondBrainSense
**Duración:** 2 semanas  
**Equipo:** Frontend Senior (1) + UX (0.5)  
**Dependencias:** F5 completada  
**Entregable:** UI única, personalizada y coherente que fusiona lo mejor de SurfSense (conectores, multi-tenant, chat con herramientas) con lo mejor de Second Brain (wiki semántica, grafo de conocimiento, pasaportes, panel de ingesta avanzada, admin Brain).

---

## Objetivo: una sola UI, no dos

SurfSense tiene una UI muy completa pero orientada a un producto SaaS genérico — neutral, corporativa, sin personalidad.  
Second Brain tiene 6 vistas ricas en funcionalidades Brain que no existen en SurfSense.

**No se trata de añadir páginas a SurfSense tal como está.** El objetivo es una UI con identidad propia: **SecondBrainSense** — una herramienta de conocimiento personal técnico, no un chat corporativo, sin perder que puede convertirse un una aplicacion de caracter corporativo.

---

## F6.0 — Inventario comparativo (Día 0)

### Lo que SurfSense tiene y hay que conservar

| Funcionalidad | Dónde está | Valor |
|--------------|------------|-------|
| Chat multi-herramienta con SSE streaming | `new-chat/[[...chat_id]]` | Core — no tocar |
| Sidebar con lista de chats + colapso + resize | `layout/ui/sidebar/` | Conservar base estructural |
| IconRail multi search-space | `layout/ui/icon-rail/` | Conservar — identidad de espacios |
| Panel derecho deslizante (editor/report) | `layout/ui/right-panel/` | Conservar |
| TabBar de documentos abiertos | `layout/ui/tabs/` | Conservar |
| 25+ conectores + log de indexación | `connectors/` | Conservar |
| Configuración LLM por search-space | `new-chat/` → onboard | Conservar |
| Auth + RBAC + multi-tenant | `auth/` + `users.py` | Conservar |
| Dashboard de documentos | `documents/` | Conservar |
| i18n (next-intl) | `messages/` | Conservar |
| Observabilidad (OpenTelemetry) | `instrumentation.ts` | Conservar |

### Lo que Second Brain tiene y hay que integrar

| Vista/Funcionalidad | Componente origen | Valor clave |
|--------------------|-------------------|-------------|
| **Brain Chat** con badge L1/L2/BM25/Web/L0, `retrieve_mode`, historial | `brain_chat.py` | Transparencia del retrieval |
| **Wiki semántica** con CRUD de pasaportes, filtros tags/dominio/origen, re-síntesis | `brain_wiki.py` | Gestión del conocimiento |
| **Grafo interactivo** vis.js con tooltip enriquecido, panel de detalle, colores por dominio | `brain_graph.py` | Exploración visual |
| **Ingesta avanzada** con selección de modelo por extensión, log por 4 fases, historial | `brain_ingest.py` | Control del pipeline |
| **Métricas Brain** — estado 3 colecciones Qdrant, uso de niveles en sesión | `second_brain.py` | Visibilidad operacional |
| **Admin Brain** — configuración chunking/retrieval/LLM en caliente, health check | `brain_admin.py` | Operaciones sin restart |

### Lo que NO existe en ninguna de las dos y hay que construir

| Funcionalidad nueva | Valor |
|--------------------|-------|
| Dashboard home con estado del Brain (docs indexados, última ingestión, nivel más usado) | Primera pantalla significativa |
| Selector de modelo Brain en la barra de chat (no solo en settings) | Acceso rápido sin salir del chat |
| Indicador visual del nivel de respuesta en el chat principal de SurfSense | Diferenciación de contexto |

---

## F6.1 — Sistema de diseño: identidad SecondBrainSense (Día 1)

SurfSense usa Tailwind + shadcn/ui + tema claro/oscuro. Se mantiene esa base pero con una paleta y tipografía con personalidad propia.

### Sistema de diseño: variables exactas del Second Brain

El `app.py` de Second Brain define un sistema de diseño dark completo con jerarquía de contraste WCAG AA. Se traduce directamente a CSS variables en Next.js:

```css
/* Añadir en globals.css — dark theme SecondBrainSense */
/* Fuente: app.py de Second Brain — paleta original probada */
[data-theme="dark"], .dark {
  /* Fondos — 3 niveles de profundidad */
  --bg-deep:    #0e1117;   /* fondo principal de página */
  --bg-surface: #161b27;   /* tarjetas, containers */
  --bg-overlay: #1c2133;   /* controles interactivos, inputs */
  --bg-border:  #2c3152;   /* bordes suaves */

  /* Texto — jerarquía clara (contraste WCAG AA garantizado) */
  --text-hi:    #eaecf4;   /* texto principal     ~13:1 */
  --text-mid:   #b4b9d4;   /* texto secundario    ~8:1  */
  --text-lo:    #7880a8;   /* captions/labels     ~4.5:1 */
  --text-faint: #4a5280;   /* SOLO decorativo — no usar para texto */

  /* Acento principal */
  --accent:      #a78bfa;  /* púrpura violeta — identidad Brain */
  --accent-glow: #4c1d95;  /* glow / fondo de badges accent */
  --accent-dim:  #7c5cbf;  /* hover/active */

  /* Semáforo de score (fuentes RAG) */
  --score-high:   #34d399;  /* >0.70 — alta relevancia */
  --score-medium: #fbbf24;  /* 0.55-0.70 — relevancia media */
  --score-low:    #f87171;  /* <0.55 — baja relevancia */
}
```

### Paleta Brain

/* Dominio engineering — violeta */
--brain-engineering: 139 92 246;      /* #8b5cf6 */
--brain-engineering-subtle: 45 24 100; /* fondo oscuro */

/* Dominio data — naranja */
--brain-data: 251 146 60;             /* #fb923c */

/* Dominio business — azul */
--brain-business: 56 189 248;         /* #38bdf8 */

/* Dominio functional — verde */
--brain-functional: 74 222 128;       /* #4ade80 */

/* Dominio legal — rojo */
--brain-legal: 248 113 113;           /* #f87171 */

/* Niveles de retrieval */
--level-brain:     139 92 246;   /* L1 — violeta */
--level-knowledge:  59 130 246;  /* L2 semántico — azul */
--level-bm25:      234 179 8;    /* L2.b BM25 — amarillo */
--level-web:        34 197 94;   /* L2.c Web — verde */
--level-llm:       107 114 128;  /* L0 — gris */

/* Acento principal de la app */
--accent-brain: 139 92 246;
```

### Componente: `LevelBadge`

Badge reutilizable que indica el nivel de retrieval usado. Se usa en Brain Chat y en el chat principal de SurfSense.

```tsx
// components/brain/LevelBadge.tsx
import { cn } from "@/lib/utils"

export type Level = 1 | 2 | 3 | 4 | 0

const LEVEL_CONFIG: Record<Level, { label: string; classes: string }> = {
  1: { label: "🧠 Brain",     classes: "bg-violet-500/15 text-violet-300 border-violet-500/30" },
  2: { label: "📚 Knowledge", classes: "bg-blue-500/15 text-blue-300 border-blue-500/30" },
  3: { label: "🔍 BM25",      classes: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30" },
  4: { label: "🌐 Web",       classes: "bg-green-500/15 text-green-300 border-green-500/30" },
  0: { label: "🤖 LLM",       classes: "bg-muted text-muted-foreground border-border" },
}

export function LevelBadge({ level, modelTier }: { level: Level; modelTier?: string }) {
  const cfg = LEVEL_CONFIG[level]
  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
      cfg.classes
    )}>
      {cfg.label}
      {modelTier && (
        <span className="opacity-60">· {modelTier}</span>
      )}
    </span>
  )
}
```

### Componente: `SourceCard`

El `sources.py` de Second Brain muestra fuentes con score colorizado. Se traduce a:

```tsx
// components/brain/SourceCard.tsx
// Fuente: sources.py de Second Brain — lógica de score_color()

function scoreVariant(score: number): "high" | "medium" | "low" {
  if (score > 0.70) return "high"    // 🟢 verde
  if (score >= 0.55) return "medium" // 🟡 amarillo
  return "low"                       // 🔴 rojo
}

const SCORE_CLASSES = {
  high:   "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  medium: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  low:    "bg-red-500/20 text-red-300 border-red-500/30",
}

export function SourceCard({ source }: { source: {
  source: string; page?: string | number; score: number; source_url?: string
}}) {
  const variant = scoreVariant(source.score)
  return (
    <div className="flex items-center gap-2 rounded-md border px-2 py-1 text-xs">
      <span className={cn("rounded-full border px-1.5 py-0.5 font-mono", SCORE_CLASSES[variant])}>
        {source.score.toFixed(3)}
      </span>
      {source.source_url
        ? <a href={source.source_url} target="_blank" className="hover:underline truncate max-w-[200px]">
            {source.source}
          </a>
        : <span className="truncate max-w-[200px]">{source.source}</span>
      }
      {source.page && <span className="text-muted-foreground">p.{source.page}</span>}
    </div>
  )
}
```

### Componente: `DomainBadge`

```tsx
// components/brain/DomainBadge.tsx
const DOMAIN_CONFIG = {
  engineering: { label: "Engineering", classes: "bg-violet-500/15 text-violet-300" },
  data:        { label: "Data",        classes: "bg-orange-500/15 text-orange-300" },
  business:    { label: "Business",    classes: "bg-sky-500/15 text-sky-300" },
  functional:  { label: "Functional",  classes: "bg-green-500/15 text-green-300" },
  legal:       { label: "Legal",       classes: "bg-red-500/15 text-red-300" },
  other:       { label: "Other",       classes: "bg-muted text-muted-foreground" },
}
```

---

## F6.2 — Estructura de rutas (Día 1-2)

Las vistas Brain se añaden dentro del espacio de trabajo existente: `dashboard/[search_space_id]/brain/`.
Esto garantiza que `search_space_id` está siempre disponible y el layout del workspace (sidebar, icon-rail) se mantiene.

```
surfsense_web/app/dashboard/[search_space_id]/
├── new-chat/                 ← SurfSense chat — SIN CAMBIOS
├── connectors/               ← SurfSense conectores — SIN CAMBIOS
├── logs/                     ← SurfSense logs — SIN CAMBIOS
├── documents/                ← SurfSense docs — SIN CAMBIOS
│
└── brain/                    ← NUEVO — SecondBrainSense
    ├── layout.tsx            ← NUEVO — sidebar Brain + header con breadcrumb
    ├── page.tsx              ← redirect a brain/home
    ├── home/
    │   └── page.tsx          ← NUEVO — Dashboard Brain (estado del sistema)
    ├── chat/
    │   └── page.tsx          ← NUEVO — Brain Chat con badges de nivel
    ├── wiki/
    │   ├── page.tsx          ← NUEVO — Wiki semántica (lista + filtros)
    │   └── [source]/
    │       └── page.tsx      ← NUEVO — Pasaporte detalle (vista + edición)
    ├── graph/
    │   └── page.tsx          ← NUEVO — Grafo interactivo vis.js
    ├── ingest/
    │   └── page.tsx          ← NUEVO — Ingesta avanzada con log de fases
    ├── metrics/
    │   └── page.tsx          ← NUEVO — Métricas de colecciones Qdrant
    ├── admin/
    │   └── page.tsx          ← NUEVO — Admin Brain (chunking/retrieval/LLM)
    └── vocabulary/
        ├── page.tsx          ← NUEVO — Gestión de maestros y vocabulario (F1.0)
        ├── domains/
        │   └── page.tsx      ← NUEVO — CRUD brain_domains
        ├── doc-types/
        │   └── page.tsx      ← NUEVO — CRUD brain_doc_types
        ├── entity-hints/
        │   └── page.tsx      ← NUEVO — CRUD brain_entity_hints
        └── vocabulary/
            └── page.tsx      ← NUEVO — CRUD brain_vocabulary (canónica + aliases)
```

### Modificación en el sidebar existente

En `Sidebar.tsx` añadir una sección "SecondBrainSense" bajo las secciones existentes:

```tsx
// En el array de navItems que construye client-layout.tsx, añadir:
{
  title: "SecondBrainSense",
  icon: Brain,             // lucide-react 'Brain' icon
  url: `/dashboard/${search_space_id}/brain/home`,
  isSection: true,         // separador visual
  children: [
    { title: "Home",     icon: LayoutDashboard, url: `brain/home`    },
    { title: "Chat",     icon: MessageCircle,   url: `brain/chat`    },
    { title: "Wiki",     icon: BookOpen,        url: `brain/wiki`    },
    { title: "Grafo",    icon: Network,         url: `brain/graph`   },
    { title: "Ingestar", icon: Upload,          url: `brain/ingest`  },
    { title: "Métricas", icon: BarChart2,   url: `brain/metrics`    },
    { title: "Admin",    icon: Settings2,   url: `brain/admin`      },
    { title: "Maestros", icon: BookMarked,  url: `brain/vocabulary` },
  ]
}
```

---

## F6.3 — `brain/layout.tsx`: layout compartido Brain (Día 2)

```tsx
// surfsense_web/app/dashboard/[search_space_id]/brain/layout.tsx

import { use } from "react"
import type { ReactNode } from "react"
import { BrainBreadcrumb } from "@/components/brain/BrainBreadcrumb"

// El layout del workspace (sidebar, icon-rail) ya viene del layout padre.
// Este layout solo añade: breadcrumb Brain + posible header contextual.
export default function BrainLayout({
  children,
  params,
}: {
  children: ReactNode
  params: Promise<{ search_space_id: string }>
}) {
  const { search_space_id } = use(params)

  return (
    <div className="flex flex-col h-full">
      <BrainBreadcrumb searchSpaceId={search_space_id} />
      <div className="flex-1 overflow-auto">{children}</div>
    </div>
  )
}
```

---

## F6.4 — `brain/home/page.tsx`: Dashboard Brain (Día 2-3)

Pantalla de bienvenida con estado del sistema. Equivalente a los banners de stats de `second_brain.py`.

**Datos mostrados:**
- Tarjetas: nº documentos en `brain` / `knowledge` / `code`
- Última ingesta: fecha + documento
- Nivel de respuesta más usado (L1/L2/BM25/Web/L0) en las últimas 24h
- Estado de salud: Qdrant ✓, Ollama ✓, PostgreSQL ✓
- Accesos rápidos a las secciones principales

**Fuentes de datos:**
- `GET /api/v1/brain/stats` → `{ brain_count, knowledge_count, code_count, last_ingest, level_usage }`
- `GET /api/v1/health` → servicios activos

---

## F6.5 — `brain/chat/page.tsx`: Brain Chat (Día 3-4)

Vista de chat específicamente conectada al endpoint Brain F4. **Distinta al chat de SurfSense** — sin herramientas de agente, centrada en el retrieval Brain.

> **Distinción importante**: Second Brain tiene DOS modos de chat:  
> - `chat.py` — chat RAG básico, llama a `/query` sin multilevel routing (pre-Brain)  
> - `brain_chat.py` — chat Brain con cascada L1→L2→BM25→Web→L0, badges de nivel, retrieve_mode  
> En SecondBrainSense, el **chat principal de SurfSense** hereda el rol del `chat.py` original.  
> La **vista Brain Chat** (`brain/chat/`) implementa `brain_chat.py`.

**Funcionalidades clave (del `brain_chat.py` real):**
- `retrieve_mode: "auto" | "full_source"` — detección automática si se pide un fichero completo
- Toggle "Forzar LLM libre" (force_l0)
- `LevelBadge` en cada respuesta — siempre visible
- Hints de preguntas frecuentes en la pantalla inicial (estilo Perplexity)
- Historial persistente por search_space (local storage o BD)
- Visualización de fuentes citadas con score dots y link al pasaporte
- **Drill-down condicional** — botón "🔍 Ampliar con documentos originales (Nivel 2)" visible solo cuando `drillDownAvailable=true` y `level === 1`

**Diferencia con el chat SurfSense:** el chat SurfSense usa el agente LangGraph con herramientas (web search, code execution...). Brain Chat es más simple: pregunta → cascada Brain → respuesta con transparencia de nivel.

**Detección automática de `retrieve_mode`** — del `brain_chat.py` real:

```typescript
// lib/brain/retrieve-mode.ts
const FILE_REQUEST_PATTERNS = [
  "fichero", "archivo", "documento", "completo", "entero", "full file", "raw file"
]
const FILENAME_RE = /([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,10})/

export function detectRetrieveMode(question: string): "auto" | "full_source" {
  const q = question.toLowerCase()
  const hasFilename = FILENAME_RE.test(question)
  const asksFile = FILE_REQUEST_PATTERNS.some(p => q.includes(p))
  return hasFilename && asksFile ? "full_source" : "auto"
}
// Ejemplos:
// "Muéstrame el fichero chunking.py completo"  → "full_source"
// "¿Cómo funciona el chunker?"                → "auto"
```

```tsx
// Estructura de la página
<BrainChatPage>
  <BrainChatHeader>
    <RetrieveModeSelector />   // auto | full_source
    <ForceL0Toggle />
    <ModelDisplay />           // modelo activo (tier)
  </BrainChatHeader>
  
  <BrainChatMessages>
    // Cada mensaje asistente muestra:
    <LevelBadge level={msg.level} modelTier={msg.model_tier} />
    <MessageContent />                        // ReactMarkdown con remark-gfm
    <SourcesList sources={msg.sources} />     // score dots colorados
    {msg.drillDownAvailable && msg.level === 1 && (
      <DrillDownButton onClick={() => forceDrillDown(msg.question)} />
      // → "🔍 Ampliar con documentos originales (Nivel 2)"
    )}
  </BrainChatMessages>
  
  <BrainChatHints hints={HINTS} />   // visible cuando messages.length === 0
  <BrainChatInput />
</BrainChatPage>
```

**Hints predefinidos** (del `brain_chat.py` real):
```typescript
const HINTS = [
  { icon: "💬", text: "¿De qué tratan mis documentos?" },
  { icon: "📝", text: "Resume el plan RAG" },
  { icon: "🔍", text: "¿Qué docs tengo sobre IA?" },
  { icon: "✂️", text: "Cita exacta sobre chunking" },
  { icon: "💻", text: "¿Qué funciones implementa chunking.py?" },
  { icon: "📄", text: "Muestra el código del extractor PDF" },
]
```

---

## F6.6 — `brain/wiki/page.tsx`: Wiki semántica (Día 4-6)

Equivalente directo de `brain_wiki.py`. Vista de tarjetas de pasaportes con CRUD completo.

**Funcionalidades clave (del `brain_wiki.py` real):**
- Lista de pasaportes como grid de cards (`PassportCard`)
- Cada card muestra: título, tipo doc, dominio (`DomainBadge`), importancia (dots ●●●○○), confianza (%), scope badges brain/knowledge/code, origen del conector, indicador PII si aplica
- Filtros: por tags (multiselect), dominio (select), tipo de fichero, origen del conector
- Ordenar por: importancia / fecha / título
- Acciones por card: Ver .md | Editar | Re-sintetizar (con selector de modelo) | Eliminar
- Vista detalle `[source]/page.tsx`: **Monaco editor** (no textarea) con preview Markdown lado a lado
- **Historial de versiones**: cada guardado crea una versión recuperable (nuevo — no está en Streamlit)

**Filtros de origen** — mapeo de connectors SurfSense a iconos/labels:
```
GitHub → 🐙  Confluence → 📘  Notion → ✍️  Jira → 🎯
Slack  → 💬  Google Drive → 📁  OneDrive → ☁️  Local → 💾
```

**Componente `PassportCard`** — campos clave que muestra:
```typescript
// components/brain/PassportCard.tsx
// Borde izquierdo del color del dominio
// Header:   tipo doc (badge coloreado) + indicador PII (⚠️ si hasPii)
// Título:   line-clamp-2
// Tags:     primeros 5, estilo #tag
// Scopes:   brain / knowledge / code como pills con colores
//           brain=#a78bfa  knowledge=#60a5fa  code=#34d399
// Origen:   📁 Subida | 📂 Ruta local | ☁️ SharePoint | 🌐 URL
// Footer:   importancia como dots ●●●○○ | confianza 85% (verde>70%, amarillo>40%, rojo)
//           | fecha actualización
```

**API calls:**
- `GET /api/v1/brain/list` → lista de pasaportes con metadata
- `GET /api/v1/brain/passport/{source}` → contenido .md
- `PUT /api/v1/brain/passport/{source}` → actualizar .md
- `GET /api/v1/brain/passport/{source}/history` → historial de versiones
- `POST /api/v1/brain/document/{source}/resynthesize` → re-sintetizar
- `DELETE /api/v1/brain/document/{source}?search_space_id=X` → eliminar

---

## F6.7 — `brain/graph/page.tsx`: Grafo interactivo (Día 6-8)

Equivalente de `brain_graph.py`. Usa **`react-force-graph`** (no vis-network embebido en iframe) — componente React de primera clase con estado compartido con el resto de la UI.

**Ventaja sobre el Streamlit original:** el panel de detalle se actualiza sin rerenderizar el grafo. El toggle de aristas filtra sin `st.rerun()`. Los botones "Abrir en Wiki" y "Preguntar al Brain" son `router.push()` instantáneos, no cambios de `session_state`.

**Funcionalidades clave (del `brain_graph.py` real):**
- Layout de dos paneles: grafo (izq 70%) + ficha detalle (dcha 30%) — **sin rerender del grafo al cambiar selección**
- Nodos coloreados por dominio (paleta de F6.1)
- Tamaño de nodo proporcional a importancia (1-5)
- **3 tipos de aristas** (del `_build_vis_edges` original):
  - `related` — referencia explícita en frontmatter (sólida, violeta)
  - `serie` — misma serie documental (punteada, azul)
  - `tag_overlap` — tags compartidos (punteada, gris) — toggle on/off
- Tooltip al hover: título, dominio, importancia, tags, resumen (160 chars)
- Panel de detalle al click: resumen completo, tags, botones "📖 Abrir en Wiki" y "💬 Preguntar al Brain"
- Filtros: por dominio (multiselect), toggle tag-edges

**Implementación:**

```tsx
// app/dashboard/[search_space_id]/brain/graph/page.tsx
"use client"
import dynamic from "next/dynamic"
const ForceGraph2D = dynamic(
  () => import("react-force-graph").then(m => m.ForceGraph2D),
  { ssr: false }    // grafo solo en cliente
)

// nodes: { id, label, color(dominio), val(importancia), domain, tags, summary }
// links: { source, target, type, color } — type: "related"|"serie"|"tag_overlap"
// Panel detalle: estado React local — NO rerenderiza el grafo al hacer click
// "Preguntar al Brain" → router.push(`brain/chat?q=...`) instantáneo
```

**API call:** `GET /api/v1/brain/graph?search_space_id=X` → `{ nodes: [...], edges: [...] }`

---

## F6.8 — `brain/ingest/page.tsx`: Ingesta avanzada (Día 8-10)

Equivalente de `brain_ingest.py`. Esta es la vista más diferenciadora respecto a SurfSense.

**Funcionalidades clave (del `brain_ingest.py` + `upload.py` de Second Brain):**
- **Ingesta de URL** (del `upload.py`): campo de texto + botón → `POST /api/v1/brain/ingest/url`
  - Devuelve chunks generados por colección (brain/knowledge/code)
  - El crawler extrae el HTML y lo pasa por el pipeline igual que un fichero
- Dropzone multi-fichero con validación por extensión
- **Recomendación automática de modelo por extensión** (tabla `_MODEL_RECOMMENDATION`):
  - `.py`, `.sql`, `.ipynb`, `.xml` → `qwen2.5-coder:3b` (especializado en código)
  - `.drawio`, `.xlsx` → `deepseek-r1` (razonamiento estructural)
  - `.pdf`, `.docx`, `.pptx`, `.txt` → `llama3.2:3b` (documentos generales)
  - `.md` → `deepseek-r1` (markdown técnico profundo)
- Selector de modelo dinámico con modelos Ollama disponibles (`GET /admin/ollama-models`)
- **Routing preview antes de ingestar** — el usuario ve a qué colecciones irá el documento según su extensión, **antes** de subir:
  ```
  .py  → brain (violeta) + code (verde)
  .md  → brain (violeta) + knowledge (azul)
  .pdf → brain (violeta) + knowledge (azul)
  ```
- **Log de 4 fases por documento** via **SSE** (Server-Sent Events) — actualización en tiempo real, no polling:
  1. 📥 Extracción — formato parseado
  2. 🧠 Síntesis L1 — pasaporte generado → colección brain
  3. 📦 Chunking L2 — nº chunks → knowledge / code
  4. 🔢 Vectorización — embeddings almacenados
- Historial de ingestas recientes (últimas 10 con estado ok/error)
- Modo "background" para documentos grandes (>50KB)

**Implementación del log de fases (SSE real):**

```tsx
// components/brain/IngestPhaseLog.tsx
type PhaseStatus = "idle" | "running" | "ok" | "warn" | "error" | "skip"

// El backend emite eventos SSE: { phase, status, detail }
// phase: "extraction" | "synthesis" | "chunking" | "vectorization"
// El componente muestra un stepper vertical animado con estado por fase

// Backend (FastAPI) necesita un endpoint SSE:
// GET /api/v1/brain/ingest/stream?job_id=X
// → text/event-stream con eventos por fase
```

**Tabla de recomendación de modelo** (del `_MODEL_RECOMMENDATION` real de `brain_ingest.py`):

| Extensión | Modelo recomendado | Razón |
|-----------|-------------------|-------|
| `.py` `.sql` `.ipynb` `.json` `.xml` | `qwen2.5-coder:3b` | Código fuente |
| `.drawio` `.xlsx` `.md` | `deepseek-r1` | Razonamiento estructural |
| `.pdf` `.docx` `.pptx` `.txt` | `llama3.2:3b` | Documentos generales |

La recomendación aparece en tiempo real al soltar el fichero en la dropzone, antes de confirmar la ingesta.

---

## F6.9 — `brain/metrics/page.tsx`: Métricas Brain (Día 10-11)

Equivalente de la sección Métricas de `second_brain.py`.

**Datos mostrados:**
- 3 tarjetas de colección Qdrant: nº vectores, nº fuentes únicas, dimensión
- Gráfico de barras: distribución de chunks por colección (brain/knowledge/code)
- Tabla: top 10 fuentes por nº de chunks
- Uso de niveles de respuesta en las últimas 24h (donut chart)
- Estado de índices PostgreSQL (tsvector GIN)

**API calls:**
- `GET /api/v1/brain/stats` → stats de colecciones
- `GET /api/v1/brain/level-usage` → distribución de niveles L1/L2/BM25/Web/L0

---

## F6.10 — `brain/admin/page.tsx`: Admin Brain (Día 11-13)

Equivalente de `brain_admin.py`. Panel de configuración en caliente con tabs.

**Tabs (del `brain_admin.py` real):**
1. **✂️ Chunking** — strategy (paragraph/token/hybrid), chunk_size, chunk_overlap
2. **🔍 Retrieval** — `ROUTER_L1_HIGH_SCORE`, `ROUTER_L1_MIN_SCORE`, top_k, reranking on/off
3. **🤖 LLM** — provider (ollama/anthropic/openai), modelo, temperatura, max_tokens síntesis, **bloque CRAG Evaluador** (toggle + modelo + chunks)
4. **📦 Colecciones** — estado Qdrant (brain/knowledge/code), recrear colección, ver vectores
5. **🖥️ Sistema** — health check (Qdrant/Ollama/PostgreSQL/Redis), logs recientes

**Diferencia con SurfSense settings:** las settings de SurfSense configuran el LLM del chat. Admin Brain configura el pipeline de retrieval e indexación — parámetros distintos.

### Tab 🤖 LLM — bloque CRAG Evaluador

El tab LLM tiene dos bloques diferenciados:

**Bloque superior — Modelo de síntesis** (parámetros básicos actuales):
- Provider: `[ollama] [anthropic] [openai]`
- Modelo síntesis: select dinámico (modelos Ollama o input libre para API)
- Temperatura, max_tokens

**Bloque inferior — Agente Evaluador CRAG** (configuración F4.6):  
Separado visualmente con `<Separator />` y encabezado de sección.

```
── Agente Evaluador CRAG ────────────────────────────────────────────────────────

  [●] Activar evaluador CRAG   ← Switch (desactivado por defecto)

  ⚠️  Solo recomendado con Claude o GPT-4.
      Con Ollama local añade ~15 segundos de latencia por consulta.
      Con modelos API (Anthropic/OpenAI) la latencia es < 300ms.

  ─────────────────────────────────────────────────────────────────
  [Solo visible si el switch está ON]

  Modelo evaluador:      [qwen2.5-coder:3b ▾]   ← select dinámico (Ollama) o input libre (API)
  Chunks a evaluar:      [  3  ] (1-5)           ← NumberInput con min/max
  Timeout (segundos):    [ 15  ] (5-30)          ← NumberInput

  ─────────────────────────────────────────────────────────────────
  Query Rewriting (siempre activo)
  ℹ️  El rewriter de consultas web (L2.c) está siempre activado.
      Convierte la pregunta a keywords antes de buscar en SearXNG.
  Modelo rewriter:       [qwen2.5-coder:3b ▾]   ← select dinámico
──────────────────────────────────────────────────────────────────────────────────
```

**Comportamiento del toggle:**
- `OFF` (default): campos del bloque CRAG con `opacity-50 pointer-events-none` — visibles pero no editables
- `ON`: campos activados; si el provider activo es `ollama`, mostrar un `Alert` amarillo con el warning de latencia
- Al guardar con `POST /api/v1/admin/config`, el frontend envía SOLO los campos modificados (PATCH semántico)

**Lógica del warning contextual:**
```tsx
// En el componente CRAGBlock:
const showLatencyWarning = cragEnabled && provider === "ollama"

{showLatencyWarning && (
  <Alert variant="warning">
    <AlertTriangle className="h-4 w-4" />
    <AlertDescription>
      Con Ollama local, el evaluador CRAG añade ~15s por consulta
      (3 llamadas LLM × 5s). Considera desactivarlo o usar Claude/GPT-4.
    </AlertDescription>
  </Alert>
)}
```

**Persistencia:**
- Los valores se guardan en memoria en `brain_admin.py` y persisten hasta restart del contenedor
- Opcional (fase posterior): persitir en tabla `brain_config` de PostgreSQL para sobrevivir restarts
- El backend lee las variables de entorno al arranque como defaults; el Admin Brain puede sobrescribirlas en runtime sin restart

**Acceso rápido:** en el tab **🤖 LLM**, encima del bloque CRAG, un enlace inline:
> 📖 _¿Cuándo usar el evaluador CRAG? → Ver documentación en [F4.6](../F4-router-multinivel.md#f46--agente-evaluador-crag-opcional--activar-solo-con-claudegpt-4)_

**API calls:**
- `GET /api/v1/admin/config` → configuración activa (incluye `CRAG_EVALUATOR_ENABLED`, `CRAG_EVALUATOR_MODEL`, `CRAG_MAX_EVAL_CHUNKS`, `CRAG_EVAL_TIMEOUT`, `CRAG_REWRITER_MODEL`)
- `POST /api/v1/admin/config` → actualizar parámetros
- `GET /api/v1/health` → health check
- `GET /api/v1/admin/ollama-models` → modelos disponibles (usados en ambos selects del bloque CRAG)

---

## F6.11 — `brain/vocabulary/page.tsx`: Gestión de Maestros y Vocabulario (Día 11-13)

Las cinco tablas definidas en F1.0 (`brain_domains`, `brain_subdomains`, `brain_doc_types`, `brain_entity_hints`, `brain_vocabulary`) necesitan CRUD completo en la UI. Sin esta vista, cualquier cambio al vocabulario o a los dominios requiere acceso directo a la BD.

**Regla de multi-tenancy visible en la UI:** cada tabla tiene registros globales (`search_space_id = NULL`, fondo ligeramente diferente) y registros específicos del space activo. Los globales no se pueden eliminar desde la UI — solo desactivar (`is_active = false`).

### Estructura de la página: tabs por entidad

```
brain/vocabulary/
└── page.tsx   ← tabs: Dominios | Tipos de doc | Entity Hints | Vocabulario

                                 ┌─ Tab Dominios ─────────────────────────┐
                                 │  Tabla: brain_domains                  │
                                 │  Cols: domain_key | label | signal_tags│
                                 │        signal_kw | scope | activo      │
                                 │  Acciones: + Añadir | ✏️ Editar | 🗑️  │
                                 └─────────────────────────────────────────┘
```

### Tab 1 — Dominios (`brain_domains`)

- Tabla con columnas: `domain_key`, `label`, `signal_tags` (chips), `signal_kw` (chips truncados), scope (Global / Space), is_active (toggle)
- Filtro: Global vs solo este space
- Botón "+ Añadir dominio" → drawer lateral con formulario
- Formulario campos: `domain_key` (slug), `label`, `signal_tags` (input de chips), `signal_kw` (input de chips), `description`
- Dominios globales (seed): fondo `bg-muted/40` — botón eliminar deshabilitado, solo toggle is_active
- Dominios propios del space: CRUD completo
- **Consulta rápida**: buscador para ver si un tag/keyword ya está asociado a un dominio

```
API calls:
GET  /api/v1/brain/admin/domains?search_space_id=X       → lista (global + space)
POST /api/v1/brain/admin/domains                         → crear
PUT  /api/v1/brain/admin/domains/{id}                    → editar
DELETE /api/v1/brain/admin/domains/{id}                  → eliminar (solo propios del space)
PATCH /api/v1/brain/admin/domains/{id}/toggle-active     → activar/desactivar
```

### Tab 2 — Tipos de Documento (`brain_doc_types`)

- Misma estructura que dominios
- Columna extra: `signal_formats` → chips con extensiones (`.py`, `.sql`, `.ipynb`...)
- Consulta: "¿Qué tipo se asignaría a un fichero `.drawio`?" → botón de simulación que llama a `GET /api/v1/brain/admin/classify?format=.drawio&search_space_id=X`

### Tab 3 — Entity Hints (`brain_entity_hints`)

- Tabla: `hint_key`, `label`, `domain_key` (o "Todos"), `doc_type_key` (o "Todos"), nº de `patterns`, activo
- Expandir fila → muestra los patterns regex y los examples
- Formulario: `hint_key`, `label`, `domain_key` (select nullable), `doc_type_key` (select nullable), `patterns` (array de strings con validación regex), `examples`
- **Preview de regex**: campo de texto donde probar un fragmento de texto — el UI muestra qué hints matchearían

### Tab 4 — Vocabulario (`brain_vocabulary`)

Esta es la tabla más usada en el día a día — normaliza tags antes de vectorizar.

- Tabla: `canonical_tag` | `aliases` (chips) | scope | activo | acciones
- Buscador: buscar por canonical o por alias — muestra la entrada canónica si el alias existe
- Formulario añadir/editar: `canonical_tag` (slug), `aliases` (input de chips con validación de formato)
- Merge de aliases: si introduces un alias que ya es canónica en otra entrada → warning visual
- **Vista de consulta rápida** (la más útil para el día a día):

```
┌─ Buscar en vocabulario ───────────────────────────────────────────────┐
│  Input: "powerbi"                                                     │
│  ✅ Alias de: power-bi                                                │
│     Otros aliases: ["pbi", "powerbi"]                                 │
│                                                                       │
│  Input: "hdfs"                                                        │
│  ❌ No registrado — ¿Añadir como alias de...?  [Seleccionar canónica] │
└───────────────────────────────────────────────────────────────────────┘
```

```
API calls:
GET    /api/v1/brain/admin/vocabulary?search_space_id=X&q=powerbi  → buscar
POST   /api/v1/brain/admin/vocabulary                              → crear entrada
PUT    /api/v1/brain/admin/vocabulary/{id}                         → actualizar aliases
DELETE /api/v1/brain/admin/vocabulary/{id}                         → eliminar
GET    /api/v1/brain/admin/vocabulary/lookup?tag=powerbi           → buscar canónica de un alias
```

### Componente reutilizable: `ChipArrayInput`

Para `signal_tags`, `signal_kw`, `aliases` — un input que acepta valores separados por coma o Enter y los muestra como chips eliminables:

```tsx
// components/brain/ChipArrayInput.tsx
// Recibe: value: string[], onChange: (v: string[]) => void
// Comportamiento:
//   - Enter o coma → añade chip
//   - Click × en chip → elimina
//   - Validación opcional (regex para aliases: solo [a-z0-9-])
```

---

## F6.12 — Integrar `LevelBadge` en el chat principal SurfSense (Día 14)

El chat principal de SurfSense (`new-chat/`) también puede mostrar el nivel de retrieval cuando la respuesta viene del Brain Router (F4). Es un cambio mínimo pero de alto valor visual.

**Cambio:** en el componente de mensaje del asistente, si la respuesta incluye `metadata.brain_level`, renderizar el `LevelBadge` bajo el avatar.

```tsx
// components/assistant-ui/thread.tsx — añadir en AssistantMessage
{message.metadata?.brain_level !== undefined && (
  <LevelBadge level={message.metadata.brain_level} />
)}
```

---

## F6.13 — Tests y validación (Día 14)

```
tests/e2e/brain-ui/
├── brain-home.spec.ts        — cards de stats cargan, health check visible
├── brain-chat.spec.ts        — LevelBadge aparece en respuesta, force_l0 toggle funciona
├── brain-wiki.spec.ts        — filtros aplican, CRUD de pasaporte funciona
├── brain-graph.spec.ts       — grafo carga, click en nodo abre panel detalle
├── brain-ingest.spec.ts      — dropzone acepta .py, recomendación de modelo aparece, log fases completa
├── brain-metrics.spec.ts     — stats de colecciones visibles
├── brain-admin.spec.ts       — tabs cargan, config se guarda
└── brain-vocabulary.spec.ts  — CRUD dominios, búsqueda de alias en vocabulario
```

---

## F6.14 — Dependencias npm nuevas (Día 14)

Dependencias que no están en el `package.json` actual de SurfSense y son necesarias para las vistas Brain:

```bash
# Instalar desde surfsense_web/
pnpm add react-force-graph         # grafo — React nativo, sin iframe
pnpm add @monaco-editor/react      # editor Monaco para pasaportes wiki
pnpm add react-markdown remark-gfm # render Markdown en chat y wiki
pnpm add react-dropzone            # dropzone de ingesta
```

| Paquete | Versión recomendada | Usado en |
|---------|:-------------------:|----------|
| `react-force-graph` | ^1.44 | `/brain/graph` |
| `@monaco-editor/react` | ^4.6 | `/brain/wiki/[source]` (editor pasaporte) |
| `react-markdown` + `remark-gfm` | ^9 + ^4 | Chat Brain (respuestas .md) + Wiki preview |
| `react-dropzone` | ^14 | `/brain/ingest` (dropzone multi-fichero) |

**Nota:** `react-force-graph` usa WebGL vía `three.js`. Añadir `{ ssr: false }` en `dynamic()` — solo carga en el cliente.

---

## Checklist F6

### F6.0 — Prerrequisitos
- [ ] F4 operativo: `POST /api/v1/brain/query` responde con `level_used`, `level_label`, `model_tier`
- [ ] F5 operativo: documentos indexados aparecen en `/api/v1/brain/list`
- [ ] `GET /api/v1/brain/stats` implementado en backend
- [ ] `GET /api/v1/brain/graph` implementado en backend

### F6.1 — Sistema de diseño
- [ ] Variables CSS dark theme añadidas en `globals.css` (`--bg-deep`, `--bg-surface`, `--text-hi/mid/lo`, `--accent`, `--score-high/medium/low`)
- [ ] `LevelBadge` creado y exportado desde `components/brain/`
- [ ] `DomainBadge` creado y exportado desde `components/brain/`
- [ ] `SourceCard` creado con score colorizado (>0.70 verde, 0.55-0.70 amarillo, <0.55 rojo)
- [ ] Iconos lucide (`Brain`, `Network`, `Upload`) importables

### F6.2 — Rutas
- [ ] Estructura `dashboard/[search_space_id]/brain/**` creada
- [ ] Sección "SecondBrainSense" añadida al sidebar en `client-layout.tsx`
- [ ] `brain/page.tsx` redirige a `brain/home`
- [ ] `search_space_id` disponible en todas las páginas Brain via `useParams()`

### F6.3 — Layout Brain
- [ ] `brain/layout.tsx` con `BrainBreadcrumb`
- [ ] El layout padre (sidebar, icon-rail) no se rompe

### F6.4 — Brain Home
- [ ] 3 tarjetas de colección Qdrant con nº de vectores
- [ ] Estado de salud de servicios (Qdrant/Ollama/PostgreSQL)
- [ ] Accesos rápidos a Chat, Wiki, Ingestar

### F6.5 — Brain Chat
- [ ] Distinción clara con el chat principal SurfSense (`chat.py` básico vs `brain_chat.py` multinivel)
- [ ] `LevelBadge` visible en cada respuesta
- [ ] Toggle "Forzar LLM libre" funcional
- [ ] `retrieve_mode` selector (auto/full_source) con `detectRetrieveMode()` automático
- [ ] Hints visibles cuando no hay mensajes (6 sugerencias predefinidas)
- [ ] Fuentes citadas con score dots colorados y link al pasaporte Wiki
- [ ] Botón drill-down "🔍 Ampliar con documentos originales" — visible solo si `drillDownAvailable && level === 1`

### F6.6 — Wiki
- [ ] `PassportCard` con borde color dominio, tipo doc badge, importance dots (●●●○○), confianza %, scope pills, icono origen, badge PII si aplica
- [ ] Filtros tags/dominio/tipo/origen funcionales
- [ ] Re-síntesis con selector de modelo (modelos Ollama dinámicos)
- [ ] Vista detalle `[source]`: **Monaco editor** con preview Markdown lado a lado
- [ ] Edición del pasaporte .md con historial de versiones recuperables
- [ ] Eliminar pasaporte con confirmación + `search_space_id`

### F6.7 — Grafo
- [ ] `react-force-graph` instalado (`pnpm add react-force-graph`) con `dynamic({ ssr: false })`
- [ ] Nodos coloreados por dominio (paleta F6.1)
- [ ] Tamaño de nodo proporcional a importancia (1-5)
- [ ] Tooltip enriquecido al hover (título, dominio, tags, resumen 160 chars)
- [ ] Panel de detalle al click — **sin rerender del grafo** (estado React local)
- [ ] 3 tipos de aristas: `related` (sólida violeta) | `serie` (punteada azul) | `tag_overlap` (punteada gris)
- [ ] Toggle `tag_overlap` on/off — filtra aristas sin tocar el grafo
- [ ] Botones panel: "📖 Abrir en Wiki" y "💬 Preguntar al Brain" (router.push instantáneo)

### F6.8 — Ingestar
- [ ] Dropzone multi-fichero con validación por extensión
- [ ] **Campo de ingesta por URL** — `POST /api/v1/brain/ingest/url` (del `upload.py` original)
- [ ] Resultado de ingesta muestra chunks por colección: `brain:N / knowledge:N / code:N`
- [ ] **Routing preview** visible antes de subir: `ext → [brain] [knowledge]` según tipo de fichero
- [ ] Recomendación de modelo automática por extensión (tabla `_MODEL_RECOMMENDATION` del Streamlit original)
- [ ] Selector de modelos Ollama cargado dinámicamente
- [ ] Log de 4 fases via **SSE** (no polling) — backend necesita `GET /api/v1/brain/ingest/stream?job_id=X`
- [ ] Historial de ingestas recientes

### F6.9 — Métricas
- [ ] Stats de 3 colecciones Qdrant visibles
- [ ] Distribución de chunks (gráfico)
- [ ] Uso de niveles de respuesta

### F6.10 — Admin Brain
- [ ] 5 tabs implementados: Chunking, Retrieval, LLM, Colecciones, Sistema
- [ ] Configuración se guarda via `POST /api/v1/admin/config`
- [ ] Health check con estado visual de cada servicio
- [ ] Modelos Ollama cargados dinámicamente
- [ ] Tab LLM: bloque CRAG separado con `<Separator />` y encabezado visual
- [ ] Toggle CRAG desactivado por defecto — campos con `opacity-50 pointer-events-none` cuando OFF
- [ ] `Alert` warning de latencia visible cuando toggle ON + provider = `ollama`
- [ ] Select modelo evaluador carga dinámicamente desde `GET /api/v1/admin/ollama-models`
- [ ] `NumberInput` chunks (1-5) y timeout (5-30) con validación min/max
- [ ] Bloque Query Rewriting siempre visible (no toggleable) con select modelo rewriter
- [ ] `GET /api/v1/admin/config` devuelve los 5 vars CRAG (`ENABLED`, `EVALUATOR_MODEL`, `REWRITER_MODEL`, `MAX_EVAL_CHUNKS`, `EVAL_TIMEOUT`)
- [ ] Al guardar, `POST /api/v1/admin/config` acepta y persiste los 5 vars CRAG en runtime

### F6.11 — Maestros y Vocabulario
- [ ] `brain/vocabulary/page.tsx` con 4 tabs: Dominios | Tipos de doc | Entity Hints | Vocabulario
- [ ] Sidebar item "Maestros" con icono `BookMarked` añadido
- [ ] Registros globales (`search_space_id=NULL`) con fondo diferenciado — solo toggle is_active
- [ ] Registros propios del space: CRUD completo (crear, editar, eliminar)
- [ ] `ChipArrayInput` reutilizable para `signal_tags`, `aliases`, `signal_kw`
- [ ] Tab Dominios: buscador "¿qué dominio tiene este tag?"
- [ ] Tab Tipos de doc: simulación de clasificación por extensión (`?format=.drawio`)
- [ ] Tab Entity Hints: preview de regex — probar fragmento y ver qué hints matchean
- [ ] Tab Vocabulario: búsqueda por alias → devuelve la canónica
- [ ] Tab Vocabulario: warning si alias ya existe como canónica en otra entrada
- [ ] APIs backend: `GET/POST/PUT/DELETE /api/v1/brain/admin/{domains|doc-types|entity-hints|vocabulary}`
- [ ] `GET /api/v1/brain/admin/vocabulary/lookup?tag=X` implementado en backend

### F6.12 — LevelBadge en chat SurfSense
- [ ] `AssistantMessage` muestra `LevelBadge` si `metadata.brain_level` presente

### F6.14 — Dependencias npm
- [ ] `react-force-graph` instalado con `dynamic({ ssr: false })` en grafo
- [ ] `@monaco-editor/react` instalado y funcional en wiki detail
- [ ] `react-markdown` + `remark-gfm` instalados para chat y wiki preview
- [ ] `react-dropzone` instalado para ingest

### F6.13 — Tests
- [ ] 8 specs e2e cubren flujos críticos de cada sección Brain
- [ ] Test drill-down: botón aparece en respuesta L1 con `drillDownAvailable=true`
- [ ] Test grafo: panel detalle abre sin rerender del grafo
- [ ] Test ingest: routing preview aparece al seleccionar `.py`, desaparece al eliminar el fichero
- [ ] El chat SurfSense original (`new-chat/`) pasa sus tests sin cambios

### Criterio de aceptación global F6
- [ ] SurfSense original funciona sin regresiones
- [ ] Todas las secciones Brain accesibles desde el sidebar
- [ ] En Brain Chat: cada respuesta muestra qué nivel del router respondió
- [ ] En Wiki: CRUD completo de pasaportes con re-síntesis funcional
- [ ] En Grafo: click en nodo navega al pasaporte en Wiki
- [ ] En Ingestar: subir un .py sugiere `qwen2.5-coder:3b` automáticamente
- [ ] En Ingestar: pegar una URL dispara el crawler y muestra `brain:N / knowledge:N / code:N`
- [ ] `SourceCard` muestra score con color semáforo en Brain Chat y chat SurfSense
- [ ] Admin Brain: cambio de `ROUTER_L1_MIN_SCORE` persiste hasta restart
- [ ] En Maestros: añadir un alias en Vocabulario → la próxima ingesta lo normaliza correctamente
- [ ] En Maestros: registros globales no se pueden eliminar — solo desactivar

---

**Anterior:** [F5 — Conectores SurfSense → Pipeline Brain](./F5-conectores-pipeline.md)  
**Siguiente:** [F7 — Hardening, Deuda Técnica y Producción](./F7-hardening.md)
# SecondBrainSense — Hoja de ruta de desarrollo unificada

**Fecha:** 2026-07-03  
**Rama base:** `integration/f0-setup`  
**Documentos origen:** `analisis-ui-ux-pendientes.md` · `plan-conectores-chat-unificado.md`

---



## Resumen ejecutivo

Este documento unifica todos los cambios pendientes en un único plan secuencial. El trabajo se organiza en **dos tracks paralelos** que convergen en la Fase 4 (chat unificado):

- **Track A — Infraestructura Brain** (F0 → F1 → F2): FallbackExtractor, transporte de conectores hacia ingesta, UI de ingesta con conectores.
- **Track B — Agente y chat** (F3 → F4 → F5): Brain retrieval como Tool LangGraph, chat unificado, conectores en chat.

Además, hay un **bloque previo de saneamiento** (bugs críticos + UX/UI) que debe resolverse antes de empezar los tracks, ya que uno de los bugs (BUG-1) afecta al vocabulario del Brain, que es infraestructura base.

**Estimación total:** 21–33 días de desarrollo (tracks A y B en paralelo: ~15–20 días calendario).

---

## Tabla de ítems

| ID | Tipo | Título | Impacto | Esfuerzo | Bloque |
|----|------|--------|---------|----------|--------|
| BUG-1 | CRÍTICO | Backend vocabulary CRUD endpoints — 404 | Crítico | Medio | Saneamiento |
| BUG-2 | MEDIO | ZodError en `/api/v1/admin/ollama-models` | Bajo | Mínimo | Saneamiento |
| UX-1 | UX | Unificar sidebar — top nav + footer nav en bloque único | Alto | Medio | Saneamiento |
| UX-2 | UX | Eliminar sección Credits del sidebar | Alto | Bajo | Saneamiento |
| UI-3 | UI | Corregir contraste y tamaño en páginas `/brain/*` | Alto | Bajo | Saneamiento |
| F0 | Backend | FallbackExtractor | Medio | Bajo | Track A |
| F1 | Backend | Capa de transporte de conectores hacia ingesta | Alto | Medio | Track A |
| F2 | Frontend | UI de ingesta con conectores | Alto | Medio | Track A |
| F3 | Backend | Brain retrieval como LangGraph Tool | Alto | Medio | Track B |
| F4 | Frontend | Chat unificado (UI) | Alto | Alto | Convergencia |
| F5 | Frontend + Backend | Conectores en chat unificado | Medio | Bajo | Convergencia |

---

## Diagrama de dependencias

```
SANEAMIENTO (bugs + UX/UI)
    │
    ├── BUG-1 (vocabulary CRUD) ──────────────────────────────┐
    ├── BUG-2 (ZodError ollama-models)                        │
    ├── UX-1 (sidebar unificado)                              │ depende de infra Brain
    ├── UX-2 (eliminar Credits)                              │ estabilizada
    └── UI-3 (contraste /brain/*)                             │
                                                              │
          Track A                            Track B          │
    ┌─────────────────────────┐    ┌──────────────────────┐  │
    │ F0 (FallbackExtractor)  │    │ F3 (Brain como Tool) │◄─┘
    │      [1-2 días]         │    │      [5-7 días]       │
    └────────────┬────────────┘    └──────────┬───────────┘
                 │                             │
    ┌────────────▼────────────┐    ┌──────────▼───────────┐
    │ F1 (Transporte          │    │ F4 (Chat unificado)   │
    │     conectores)         │    │      [5-7 días]       │
    │      [3-5 días]         │    └──────────┬───────────┘
    └────────────┬────────────┘               │
                 │                             │
    ┌────────────▼────────────┐    ┌──────────▼───────────┐
    │ F2 (UI ingesta          │    │ F5 (Conectores        │
    │     conectores)         │    │     en chat)          │
    │      [3-5 días]         │    │      [2-3 días]       │
    └─────────────────────────┘    └──────────────────────┘
```

---

## Contexto y motivación (Tracks A y B)

SecondBrainSense tiene dos sistemas de chat independientes:

- **SurfSense new-chat** (`/dashboard/{id}/new-chat`): agente multi-herramienta con LangGraph, ~14 conectores externos, streaming SSE, persistencia en BD, @mentions, HITL.
- **Brain chat** (`/dashboard/{id}/brain/chat`): RAG cascading de 4 niveles (L1→L2→Web→L0), RRF hybrid fusion, CRAG, reranking, chunk budget dinámico por modelo.

Además, la ingesta de SecondBrainSense solo acepta URL, fichero local y ruta del servidor — no aprovecha los ~14 conectores de SurfSense (OneDrive, Google Drive, Dropbox, Slack, Teams, etc.).

**Objetivo**: (1) que los conectores SurfSense alimenten la ingesta del brain; (2) un único chat que combine lo mejor de ambos.

**Principio arquitectónico clave — Conector ≠ Extractor:**

- **Conector** = transporte. Da acceso a bytes/contenido desde una fuente. No le importa el formato.
- **Extractor** = procesamiento por formato. Parsea un tipo de fichero. No le importa de dónde vino.

```
Conector descarga fichero
       ↓
¿Extensión conocida en ExtractorFactory._MAP?
  ├─ SÍ (.pdf, .docx, .xlsx, .py...)  → Extractor específico existente
  └─ NO (.vsdx, .msg, .dwg...)        → FallbackExtractor (NUEVO en F0)
       ↓
Pipeline compartido: UniversalCleaner → Synthesizer → Writer → IngestRouter → Qdrant
```

---

## Orden secuencial recomendado:

BUG-1 — vocabulary CRUD (bloqueante para todo lo del Brain)
BUG-2 — ZodError ollama-models (10 minutos, limpieza de cache)
UI-3 — contraste /brain/* (visual, bajo riesgo)
UX-2 — eliminar Credits del sidebar (bajo esfuerzo)
UX-1 — unificar sidebar (depende de que UX-2 ya esté limpio)
F0 — FallbackExtractor (backend puro, sin dependencias)
F1 — transporte de conectores (depende de F0)
F3 — Brain como LangGraph Tool (depende de BUG-1, paralelizable con F1/F2 pero en secuencial va aquí)
F2 — UI ingesta con conectores (depende de F1)
F4 — chat unificado UI (depende de F3)
F5 — botón "Ingestar en Brain" desde chat (depende de F1 + F4)
---

## Bloque 0 — Saneamiento (bugs + UX/UI)

**Ejecutar antes de los tracks A y B. BUG-1 es bloqueante para cualquier trabajo sobre el Brain.**

---

### BUG-1 · Vocabulary CRUD endpoints — 404 🔴 CRÍTICO

La página `/brain/vocabulary` llama a 8 endpoints REST que no existen en el backend. El frontend y la BD están completamente implementados — solo falta la capa de rutas.

**Error en consola:**
```
{"status":404,"statusText":"Not Found","code":"NOT_FOUND"}
BaseApiService.request — brain-api.service.ts:1634
```

#### Estado por capa

| Capa | Fichero | Estado |
|------|---------|--------|
| DB models | `db.py` — `BrainDomain`, `BrainDocType`, `BrainEntityHint`, `BrainVocabulary` | ✅ OK |
| Migraciones | `160_brain_metadata_tables.py` | ✅ APLICADA |
| Frontend API | `brain-api.service.ts:383–511` | ✅ OK |
| Frontend UI | `brain/vocabulary/page.tsx` | ✅ OK |
| Backend routes | `brain_routes.py` | ❌ NO IMPLEMENTADO |

#### Endpoints a implementar en `surfsense_backend/app/brain/router.py`

| Método | Endpoint | Propósito |
|--------|----------|-----------|
| GET | `/api/v1/brain/admin/vocabulary` | Listar vocabulario (soporta `?q=` para búsqueda) |
| POST | `/api/v1/brain/admin/vocabulary` | Crear entrada |
| DELETE | `/api/v1/brain/admin/vocabulary/{id}` | Eliminar entrada |
| GET | `/api/v1/brain/admin/vocabulary/lookup` | Lookup alias → tag canónica |
| GET / POST | `/api/v1/brain/admin/domains` | CRUD dominios |
| GET / POST | `/api/v1/brain/admin/doc-types` | CRUD tipos de documento |
| GET / POST | `/api/v1/brain/admin/entity-hints` | CRUD entity hints |

> **Aviso:** El endpoint `/vocabulary/lookup` debe registrarse **antes** de `/{id}` en el router para que FastAPI no lo interprete como un ID con valor "lookup".

---

### BUG-2 · ZodError en `/api/v1/admin/ollama-models` 🟡 MEDIO

El schema Zod del frontend y la respuesta del backend son compatibles. El error indica que en runtime se aplica un schema con `z.array(z.string())` (versión anterior) en lugar del schema actual `z.array(z.object({ name: z.string() }))`.

**Error en consola:**
```
"Invalid API response schema - /api/v1/admin/ollama-models"
expected: "string", path: ["models", 0]
```

**Impacto:** No bloqueante. `BaseApiService.safeParse` logea el error y retorna los datos sin validar. La página funciona.

#### Diagnóstico

| Capa | Estado | Detalle |
|------|--------|---------|
| `admin_routes.py:62` | ✅ OK | Devuelve `{"models": [{"name": "modelo"}, ...]}` |
| `brain.types.ts:233–235` | ✅ OK | `z.array(z.object({ name: z.string() }))` — compatible |
| Runtime (build compilado) | ⚠️ STALE | Build cacheado usa schema antiguo con `z.string()` |

#### Corrección

```powershell
# Limpiar cache Next.js y reiniciar
Remove-Item -Recurse -Force surfsense_web/.next
npm run dev
```

Si persiste tras limpiar cache:
```bash
grep -r "z\.array.*z\.string" surfsense_web/app --include="*.ts"
```

#### Ficheros de referencia

| Fichero | Líneas | Nota |
|---------|--------|------|
| `surfsense_web/app/lib/brain.types.ts` | 233–235 | Schema correcto, no necesita cambio |
| `surfsense_web/app/lib/base-api.service.ts` | 250–260 | `safeParse` — no rompe la UI |
| `surfsense_backend/app/routes/admin_routes.py` | 62 | Respuesta correcta |

---

### UX-1 · Sidebar — Unificar bloques de navegación

El sidebar tiene dos bloques de nav separados sin justificación UX. Items como Inbox/Automations están en el *top* y los items Brain en el *footer*, lo que fragmenta la navegación principal.

#### Estado actual vs propuesta

```
Estado actual                    Propuesta
─────────────────────────────    ─────────────────────────────
┌─────────────────────────┐      ┌─────────────────────────┐
│ Header (search space)   │      │ Header (search space)   │
├─────────────────────────┤      ├─────────────────────────┤
│ New Chat                │ ←top │ New Chat                │ ← bloque
│ Inbox                   │      │  ── Workspace ──        │   único
│ Automations             │      │ Inbox                   │   scrollable
│ Documents (mobile)      │      │ Automations             │
├─────────────────────────┤      │ Documents               │
│ Recents (chats)         │      │  ── Brain ──            │
│  · Chat 1               │      │ Home · Chat · Wiki      │
├─────────────────────────┤      │ Grafo · Ingestar        │
│ SecondBrainSense        │ ←ftr │ Vocabulary              │
│  · Home, Chat, Wiki     │      ├─────────────────────────┤
│ Credits + Earn/Buy      │      │ Recents (chats)         │
│ User profile            │      ├─────────────────────────┤
└─────────────────────────┘      │ User profile            │
                                 └─────────────────────────┘
```

> **Nota:** La sección *Recents* (historial de chats) debe mantenerse separada visualmente del nav principal — entre el bloque de nav y el profile.

#### Ficheros afectados

| Fichero | Líneas | Cambio |
|---------|--------|--------|
| `surfsense_web/app/components/sidebar/Sidebar.tsx` | 211–255, 317–325 | Eliminar separación top/footer. Fusionar en una sección `NavSection` scrollable entre header y profile. |
| `surfsense_web/app/components/layout/LayoutDataProvider.tsx` | 354–401 | Reordenar `navItems` en un único array con separadores `isSectionHeader`. |
| `surfsense_web/app/components/sidebar/NavSection.tsx` | — | Sin cambios. Ya soporta `isSectionHeader`. |

---

### UX-2 · Sidebar — Eliminar sección Credits

El componente `SidebarUsageFooter` expone balance en USD, enlaces "Earn credits" y "Buy credits", y para usuarios anónimos una barra de progreso de tokens con CTA de registro. No aplica en despliegue on-premise.

#### Componente a eliminar

`SidebarUsageFooter` — `Sidebar.tsx:351–434`

Renderiza: `CreditBalanceDisplay`, link "Earn credits" (badge FREE), link "Buy credits", y para anónimos: progress bar tokens + botón "Create Free Account".

#### Ficheros afectados

| Fichero | Líneas | Cambio |
|---------|--------|--------|
| `surfsense_web/app/components/sidebar/Sidebar.tsx` | 327–332, 351–434 | Eliminar render de `<SidebarUsageFooter>` y la función completa. Eliminar imports `CreditCard`, `Zap`, `Badge`, `Progress`. |
| `surfsense_web/app/components/sidebar/CreditBalanceDisplay.tsx` | — | Eliminar fichero completo. |
| `surfsense_web/app/components/layout/LayoutDataProvider.tsx` | 208–244 | Eliminar lógica `insufficient_credits` toast y referencia a "Buy credits". Eliminar prop `pageUsage` de `SidebarProps`. |

#### Rutas candidatas a eliminar

| Ruta | Acción |
|------|--------|
| `/dashboard/[space_id]/buy-tokens` | Eliminar |
| `/dashboard/[space_id]/buy-more` | Eliminar |
| `/dashboard/[space_id]/earn-credits` | Eliminar |
| `/dashboard/[space_id]/more-pages` | Revisar — puede tener contenido no relacionado con credits |
| `/dashboard/[space_id]/purchase-success` | Eliminar |
| `/dashboard/[space_id]/purchase-cancel` | Eliminar |

> **Aviso:** Verificar referencias antes de eliminar:
> ```
> grep -r "buy-tokens\|earn-credits\|purchase-success" surfsense_web/app
> ```

---

### UI-3 · Contraste visual en páginas `/brain/*`

Las páginas Brain usan fondos con opacidad (`bg-slate-800/30`, `bg-slate-800/40`) sobre un fondo de página en light mode. El color efectivo resultante es gris medio (~#a5b2c7), haciendo que textos diseñados para fondos oscuros sean prácticamente invisibles.

#### Ratios de contraste — estado actual

| Elemento | Color texto | Fondo efectivo (light) | Ratio | WCAG |
|----------|-------------|------------------------|-------|------|
| Número vectores "brain" | `text-violet-400` #a78bfa | bg-slate-800/40 on white ≈ #a5b2c7 | 1.25:1 | ❌ FAIL |
| Número vectores "knowledge" | `text-blue-400` #60a5fa | ≈ #a5b2c7 | 1.15:1 | ❌ FAIL |
| Labels "Vectores / Fuentes" | `text-xs text-slate-500` | ≈ #a5b2c7 | 1.40:1 | ❌ FAIL |
| Número fuentes | `text-slate-200` #e2e8f0 | ≈ #a5b2c7 | 1.80:1 | ❌ FAIL |

#### Fix rápido — máximo impacto (recomendado)

Añadir clase `dark` al wrapper de todas las páginas `/brain/*`. Los colores hardcodeados `slate-*` funcionan como fueron diseñados sin modificar cada componente.

```tsx
// Antes
<div className="flex flex-col ...">

// Después
<div className="dark flex flex-col ...">
```

#### Fix quirúrgico — componente a componente

Eliminar el modificador de opacidad en todos los fondos afectados:

```diff
- "rounded-xl border bg-slate-800/40 p-5 space-y-3"
+ "rounded-xl border bg-slate-800 p-5 space-y-3"

- "... bg-slate-800/30 ..."
+ "... bg-slate-800 ..."
```

#### Ficheros con bg-opacity afectados

| Fichero | Líneas | Detalle |
|---------|--------|---------|
| `brain/metrics/page.tsx` | 72, 294 | `bg-slate-800/40` y `bg-slate-800/30` en CollectionCard |
| `brain/admin/page.tsx` | 87, 264, 302, 376, 454, 597, 625 | 7 ocurrencias de `bg-slate-800/30` |

#### Problema secundario — tamaño de texto

| Elemento | Clase actual | Fix |
|----------|-------------|-----|
| Labels de sección (metrics/admin) | `text-xs` + `tracking-wider` | → `text-sm` |
| Labels min/max del slider | `text-xs text-slate-600` | → `text-xs text-slate-400` |
| Valor numérico slider | `text-violet-300` | → `text-violet-200` o variable semántica |

---

## Track A — Infraestructura de ingesta con conectores

### Fase 0 — FallbackExtractor

**Duración:** 1–2 días  
**Dependencias:** ninguna — paralelizable con BUG-2, UX-1, UX-2, UI-3  
**Impacto:** solo `brain/extractors/`

**Entregables:**
- `brain/extractors/fallback.py`: extractor genérico
  - Intenta leer como texto plano (UTF-8, luego chardet/charset-normalizer)
  - Para binarios puros: emite un bloque con metadata del fichero (nombre, tamaño, extensión, fecha)
  - Reutiliza lógica de secciones del TxtExtractor para ficheros que resulten texto
- Modificar `ExtractorFactory.get()`: en vez de `raise ValueError` → devolver `FallbackExtractor()`
- Añadir `ExtractorFactory.is_known(ext) -> bool` para que la UI pueda mostrar si un formato tiene extractor específico
- Tests: `.vsdx`, `.msg`, `.unknown`, fichero binario sin extensión

**Criterio de éxito:** cualquier fichero pasa por el pipeline sin error. Los formatos desconocidos producen pasaporte con calidad menor pero válido.

#### Ficheros afectados

- `brain/extractors/fallback.py` — **NUEVO**
- `brain/extractors/factory.py` — modificar `get()` para fallback
- `tests/test_fallback_extractor.py` — **NUEVO**

#### Componentes existentes del pipeline (sin cambios)

| Componente | Ubicación | Función |
|---|---|---|
| ExtractorFactory | `brain/extractors/factory.py` | Registro `extensión → Extractor`. API: `extract(filename) → list[dict]` |
| BaseExtractor | `brain/extractors/base.py` | Contrato: `extract(source) → blocks[]`, `_clean_blocks()` |
| 17 extractores | `brain/extractors/*.py` | pdf, docx, xlsx, pptx, html, py, sql, ipynb, xml, drawio, json, md, csv, txt + 3 virtuales |
| UniversalCleaner | `brain/rag_lib/layer1_universal/` | encoding, unicode, PII, quality_score, language detection |
| DocumentSynthesizer | `brain/synthesizer.py` | LLM → pasaporte semántico .md |
| BrainWriter | `brain/writer.py` | Escribe pasaporte .md a BRAIN_DIR |
| IngestRouter | `brain/ingest_router.py` | Distribuye blocks a colecciones Qdrant (brain/knowledge/code) |

#### Contrato ExtractorFactory.extract()

```python
# Input
filename: str  # ruta a fichero o nombre virtual ("page.confluence")

# Output: cada dict contiene
{
    "content": str,                    # texto extraído
    "content_type": str,               # "text" | "code" | "table" | "callout"
    "page": int,                       # número de página/sección
    "metadata": dict,                  # section, sheet, slide_title, etc.
    "quality_score": float,            # 0.0-1.0 (de UniversalCleaner)
    "language_detected": str,          # "es", "en", etc.
    "sensitive_data_detected": list,   # PII encontrado
}
```

---

### Fase 1 — Capa de transporte de conectores

**Duración:** 3–5 días  
**Dependencias:** Fase 0  
**Impacto:** `brain/connectors/`, `brain_routes.py`

**Entregables:**
- `brain/connectors/surfsense_adapter.py`:
  - Clase `SurfSenseStorageAdapter`: wrapper para conectores tipo storage
    - Usa API de SurfSense upstream para descargar fichero → tmp → `ExtractorFactory.extract(tmp)`
    - Accede a tokens OAuth del usuario desde tabla `ConnectorCredential` existente
  - Clase `SurfSenseRecordAdapter`: wrapper para conectores tipo record
    - Serializa `{title, body, url, labels}` → Markdown → MarkdownExtractor
  - Clase `SurfSenseChatAdapter`: wrapper para conectores tipo chat
    - Agrupa mensajes por hilo/canal → Markdown con timestamps
- `POST /api/v1/brain/ingest/connector/{connector_type}`:
  - Input: `connector_id`, `item_id` o `batch_params`, `search_space_id`, `model`
  - Output: SSE con 4 fases (extraction → synthesis → chunking → vectorization)
  - Verificación: usuario tiene el conector configurado antes de llamar
- `GET /api/v1/brain/connectors/available`:
  - Lista conectores del usuario que tienen tokens válidos
  - Incluye tipo (storage/record/chat) y formatos que soporta

**Riesgo:** tokens OAuth expirados. Mitigación: verificar token antes de ingestar, informar al usuario si necesita re-autenticar.

#### Ficheros afectados

- `brain/connectors/surfsense_adapter.py` — **NUEVO**
- `brain/connectors/factory.py` — registrar adapters
- `app/routes/brain_routes.py` — endpoints nuevos
- `tests/test_surfsense_adapter.py` — **NUEVO**

#### Inventario de conectores y estrategia por familia

| Familia | Conectores | Estrategia |
|---|---|---|
| **Storage** (ficheros binarios) | Google Drive, OneDrive, Dropbox | Descargar bytes → tmp → `ExtractorFactory.extract(tmp)`. Extensión real del fichero → extractor específico existente o FallbackExtractor. **Cero extractores nuevos.** |
| **Records** (entidades estructuradas) | Airtable, ClickUp, Linear, BookStack, Calendar, Luma, Elasticsearch | `GenericRecordAdapter`: serializa `{title, body, url, labels, status}` a Markdown → MarkdownExtractor existente. |
| **Chat/Mensajes** | Slack, Teams, Discord, Gmail | `GenericChatAdapter`: agrupa mensajes por hilo/canal como Markdown con timestamps → MarkdownExtractor existente. |

#### Estado por conector

| Conector | Tipo | Bridge a Brain | Estado |
|---|---|---|---|
| Confluence | API/wiki | ✓ BrainConnector + Extractor (.confluence) | **Implementado** |
| Jira | API/tickets | ✓ BrainConnector + Extractor (.jira_ticket) | **Implementado** |
| GitHub | API/repos | ✓ BrainConnector + Extractor (.github_file) | **Implementado** |
| Google Drive | Storage/ficheros | ✗ | **Falta — Fase 1** |
| OneDrive | Storage/ficheros | ✗ | **Falta — Fase 1** |
| Dropbox | Storage/ficheros | ✗ | **Falta — Fase 1** |
| Slack | Chat/mensajes | ✗ | **Falta — Fase 1** |
| Teams | Chat/mensajes | ✗ | **Falta — Fase 1** |
| Discord | Chat/mensajes | ✗ | **Falta — Fase 1** |
| Airtable | Records/datos | ✗ | **Falta — Fase 1** |
| ClickUp | Records/tareas | ✗ | **Falta — Fase 1** |
| Linear | Records/issues | ✗ | **Falta — Fase 1** |
| BookStack | API/wiki | ✗ | **Falta — Fase 1** |
| Gmail | Mensajes/email | ✗ | **Falta — Fase 1** |
| Google Calendar | Records/eventos | ✗ | **Falta — Fase 1** |
| Luma | Records/eventos | ✗ | **Falta — Fase 1** |
| WebCrawler | Web/scraping | ✗ | **Falta — Fase 1** |
| Elasticsearch | Datos/índices | ✗ | **Falta — Fase 1** |

#### Contratos técnicos

```python
# BrainConnector (brain/connectors/base.py)
class BaseConnector(ABC):
    def fetch_item(self, item_id: str) -> FetchedItem
    def fetch_batch(self, source_id: str, params: dict) -> BatchResult

@dataclass
class FetchedItem:
    item_id: str
    slug: str              # "{connector}-{sanitized_title}"
    title: str
    text: str              # texto canónico serializado
    metadata: dict
    virtual_ext: str       # ".confluence", ".jira_ticket", etc.

@dataclass
class BatchResult:
    items: list[FetchedItem]
    total: int
    fetched: int
    skipped: int
    errors: list[str]
```

```python
# IngestRouter.route()
# Input
md_content: str           # markdown con frontmatter (del synthesizer)
blocks: list[dict]        # de ExtractorFactory.extract()
source: str               # slug canónico
search_space_id: str      # aislamiento multi-tenant

# Output
{"brain": {"chunks_created": N}, "knowledge": {"chunks_created": N}, "code": {"chunks_created": N}}
```

---

### Fase 2 — UI de ingesta con conectores

**Duración:** 3–5 días  
**Dependencias:** Fase 1  
**Impacto:** `surfsense_web/` (solo ingest page)

**Entregables:**
- Cuarto tab "Conector" en `ingest/page.tsx`:
  - Dropdown con conectores disponibles del usuario
  - Para storage: navegador de ficheros/carpetas del conector (tree view simple)
  - Para record: lista de ítems con filtros (proyecto, espacio, etc.)
  - Para chat: selector de canal/hilo
  - Indicador de formato: badge verde si tiene extractor específico, amarillo si irá por fallback
- Botón "Ingestar seleccionados" → llama endpoint de Fase 1
- Mismo monitor SSE de 4 fases existente
- Batch: permitir seleccionar múltiples ficheros/ítems y ingestar en secuencia

#### Ficheros afectados

- `surfsense_web/app/dashboard/[id]/brain/ingest/page.tsx` — tab Conector
- `surfsense_web/lib/apis/brain-api.service.ts` — métodos nuevos
- `surfsense_web/contracts/types/brain.types.ts` — schemas nuevos

---

## Track B — Agente y chat unificado

### Fase 3 — Brain Retrieval como LangGraph Tool

**Duración:** 5–7 días  
**Dependencias:** BUG-1 resuelto — paralelizable con Track A  
**Impacto:** `brain/tools/` (nuevo), registro en agente SurfSense

**Entregables:**
- `brain/tools/brain_search_tool.py`:
  - LangChain `Tool` que encapsula `BrainRouter`
  - Input: `query: str, search_space_id: int, force_level: int | None`
  - Output: `{answer, level_used, level_label, sources, context_chunks}`
  - Internamente ejecuta toda la cascada: L1→L2(RRF+reranking)→CRAG→Web→L0
- Registrar `brain_search` como tool disponible en el agente multi-agente de SurfSense
  - Fichero de configuración del agente (registro de tools/subagentes)
  - System prompt adicional: "Usa brain_search como primera opción para preguntas sobre documentación interna del knowledge base"
- Metadata en respuesta del tool: `level_used`, `sources`, `level_label` → para que la UI pueda renderizar level badges

**Decisión de diseño:** Brain retrieval es un tool, no un subagente completo. El agente principal decide cuándo invocarlo. Esto mantiene la arquitectura de SurfSense intacta.

#### Ficheros afectados

- `brain/tools/brain_search_tool.py` — **NUEVO**
- `brain/tools/__init__.py` — **NUEVO**
- Registro en agente SurfSense (fichero de config del agente)
- System prompt parcial para brain_search

#### Componentes Brain existentes sin cambios

| Componente | Ubicación | Función |
|---|---|---|
| BrainRouter | `brain/router.py` | Retrieval L1→L2, RRF, reranking |
| QdrantManager | `brain/qdrant_manager.py` | Gestión colecciones Qdrant |
| LLMClient | `brain/llm_client.py` | Ollama/Claude/Hybrid unificado |
| ModelProfile | `brain/model_profiles.py` | Chunk budget dinámico por modelo |
| CRAGEvaluator | `brain/crag_evaluator.py` | Evaluación post-retrieval (opcional) |

#### Contrato Brain Query

```python
# POST /api/v1/brain/query
# Request
{
    "question": str,
    "search_space_id": int,
    "chat_history": list[dict],    # [{"role": "user"|"assistant", "content": str}]
    "top_k": int,                  # default 4
    "force_level": int | None,     # forzar L1/L2
    "force_l0": bool,              # saltar RAG
}

# Response
{
    "answer": str,
    "level_used": int,             # 0|1|2|4
    "level_label": str,            # "🧠 Brain" | "📚 Knowledge" | "🌐 Web" | "🤖 LLM"
    "sources": list[str],
    "context_chunks": int,
    "model_tier": str,             # "small"|"medium"|"claude"
}
```

---

### Fase 4 — Chat unificado (UI)

**Duración:** 5–7 días  
**Dependencias:** Fase 3  
**Impacto:** `surfsense_web/` (rutas de chat, componentes)

**Entregables:**
- Ruta única: `/dashboard/{id}/chat` (redirect desde `/new-chat` y `/brain/chat`)
- Componentes nuevos integrados en el chat SurfSense:
  - `LevelBadge`: cuando la respuesta incluye metadata de `brain_search`
  - Panel de fuentes con scores: integrado en `AssistantMessage`
  - Toggle "Brain mode": deshabilita otros tools, fuerza uso de `brain_search`
  - Toggle "LLM libre": equivale a `force_l0=true`
  - Botón drill-down: re-query con `force_level=2` (detalle documental)
- Mantener TODO lo existente de SurfSense: @mentions, file upload, HITL, historial, threading, sharing, action log
- Eliminar `/brain/chat` como página separada (redirect permanente)

**Riesgo:** regresión en funcionalidad de new-chat. Mitigación: `brain_search` es un tool aditivo — si no se invoca, el agente funciona exactamente igual que antes.

#### Ficheros afectados

- `surfsense_web/app/dashboard/[id]/chat/` — ruta unificada
- `surfsense_web/components/brain/LevelBadge.tsx` — reutilizar
- `surfsense_web/app/dashboard/[id]/new-chat/` — redirect
- `surfsense_web/app/dashboard/[id]/brain/chat/` — redirect o eliminar

#### Matriz de funcionalidades del chat unificado

| Funcionalidad | SurfSense new-chat | Brain chat | Chat Unificado |
|---|:---:|:---:|:---:|
| **RETRIEVAL & RAG** | | | |
| Retrieval multi-nivel (L1→L2→Web→L0) | ✗ single-pass | **✓** cascading 4 niveles | ✓ de Brain |
| RRF Hybrid (Qdrant + BM25 PostgreSQL) | ✗ | **✓** RRF k=60 | ✓ de Brain |
| Cross-encoder reranking (ms-marco) | ✗ | **✓** opcional | ✓ de Brain |
| CRAG (evaluación post-retrieval) | ✗ | **✓** opcional | ✓ de Brain |
| Web search fallback | ~ Tavily | **✓** SearXNG | ✓ ambos |
| Drill-down L1→L2 | ✗ | **✓** | ✓ de Brain |
| Chunk budget dinámico por modelo | ✗ | **✓** ModelProfile | ✓ de Brain |
| **CONECTORES & FUENTES** | | | |
| Conectores externos (~14 fuentes) | **✓** 14+ | ~ 3 (Confl/Jira/GH) | ✓ de SurfSense |
| @ Mention docs/folders/connectors | **✓** | ✗ | ✓ de SurfSense |
| File/image upload en chat | **✓** imágenes | ✗ | ✓ de SurfSense |
| **CONVERSACIÓN** | | | |
| Multi-turn con persistencia en BD | **✓** LangGraph checkpoint | ~ client-side only | ✓ de SurfSense |
| Streaming SSE | **✓** Vercel AI SDK | ✗ respuesta completa | ✓ de SurfSense |
| Edit/regenerate cualquier turno | **✓** checkpoint rewind | ✗ | ✓ de SurfSense |
| HITL (aprobación humana) | **✓** | ✗ | ✓ de SurfSense |
| Thread history sidebar | **✓** | ✗ | ✓ de SurfSense |
| Auto-generated titles | **✓** | ✗ | ✓ de SurfSense |
| Turn cancellation (busy mutex) | **✓** | ✗ | ✓ de SurfSense |
| **UX & PRESENTACIÓN** | | | |
| Citación de fuentes con score | ~ inline citations | **✓** + level badge | ✓ merge ambos |
| Level badges (L0/L1/L2/Web) | ✗ | **✓** | ✓ de Brain |
| Model selector | **✓** | ✗ auto | ✓ de SurfSense |
| Tool toggle per session | **✓** | ✗ | ✓ de SurfSense |
| Token usage tracking | **✓** | ✗ | ✓ de SurfSense |
| Modo auto/manual retrieval | ✗ | **✓** auto + force_l0 | ✓ de Brain |
| Action log con revert | **✓** | ✗ | ✓ de SurfSense |
| Sharing/visibility/snapshots | **✓** | ✗ | ✓ de SurfSense |

---

### Fase 5 — Conectores en chat unificado

**Duración:** 2–3 días  
**Dependencias:** Fase 1 + Fase 4  
**Impacto:** UI del chat + endpoint bridge

**Entregables:**
- Cuando el agente usa un conector para traer un documento en el chat, añadir botón "Ingestar en Brain" en el action log
- Click en ese botón → llama endpoint de Fase 1 → ingesta el contenido traído por el conector
- Feedback inline: "Documento ingestado en Brain (3 chunks creados)"

#### Ficheros afectados

- UI del chat: botón "Ingestar en Brain" en action log
- Bridge endpoint o reutilización del de Fase 1

---

## Criterios de éxito

| Fase | Criterio |
|---|---|
| BUG-1 | Los 8 endpoints de vocabulario responden correctamente. La página `/brain/vocabulary` funciona sin errores 404 |
| BUG-2 | No aparece el ZodError en consola tras limpiar cache de Next.js |
| UX-1 | El sidebar muestra un único bloque de navegación scrollable con separadores de sección |
| UX-2 | No existe ninguna referencia a credits, earn, buy-tokens ni `SidebarUsageFooter` en el código |
| UI-3 | Todos los elementos en páginas `/brain/*` superan ratio WCAG AA (4.5:1) |
| F0 | Cualquier fichero con cualquier extensión pasa por el pipeline sin error |
| F1 | `POST /brain/ingest/connector/onedrive` ingesta un .docx de OneDrive correctamente |
| F2 | Usuario navega ficheros de OneDrive en la UI y los ingesta con un click |
| F3 | En el chat unificado, preguntar "¿qué dice el documento X?" invoca brain_search y devuelve respuesta con fuentes y nivel |
| F4 | Un único chat en `/dashboard/{id}/chat` con todas las funcionalidades de ambos sistemas |
| F5 | Tras traer un documento por conector en el chat, click en "Ingestar" lo añade al brain |

---

## Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| OAuth tokens expirados al ingestar desde conector | Alta | Media | Verificar token antes de ingestar. Si expirado, informar al usuario con link a re-auth |
| FallbackExtractor produce bloques de baja calidad | Media | Baja | Aceptable por diseño. Marcar `quality_score` bajo. A futuro se añaden extractores específicos |
| System prompt del agente crece mucho con brain_search | Media | Media | Inyectar instrucciones de brain solo cuando search_space tiene brain activo |
| Latencia de brain retrieval (L1→L2→CRAG) dentro del agente SSE | Media | Media | Emitir thinking steps mientras brain_search ejecuta. Timeout configurable |
| Regresión en new-chat al integrar componentes Brain | Baja | Alta | brain_search es aditivo. Tests E2E antes de merge. Feature flag para activar/desactivar |
| Conectores storage descargan ficheros grandes | Media | Media | Límite configurable (BRAIN_INGEST_MAX_FILE_SIZE). Ingesta en background para ficheros grandes |

---

## Decisiones arquitectónicas

| Decisión | Justificación |
|---|---|
| Brain retrieval como Tool de LangGraph, no como subagente | Menor complejidad. El agente principal decide cuándo usarlo. No requiere coordinación entre subagentes |
| Adapter genérico por familia (storage/record/chat), no un bridge por conector | 3 adapters cubren 14+ conectores. Esfuerzo O(familias) en vez de O(conectores) |
| FallbackExtractor en vez de rechazar formatos desconocidos | Todo documento entra en el pipeline. Calidad menor pero presente. Extensible a futuro |
| Ruta única `/chat` con redirect desde las antiguas | Transición suave. No rompe bookmarks ni links existentes |
| Feature flag para brain_search en el agente | Permite rollback sin despliegue. Activar por search_space |
| Fix de contraste con clase `dark` en wrapper (no quirúrgico) | Máximo impacto con mínimo riesgo de regresión. Los colores ya están diseñados para dark mode |

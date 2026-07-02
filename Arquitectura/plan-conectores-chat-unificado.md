# Plan: Conectores en Ingesta + Chat Unificado

**Fecha**: 2026-07-02  
**Autor**: amorenochocano  
**Estado**: Borrador para revisión  

---

## 1. Contexto y motivación

SecondBrainSense tiene dos sistemas de chat independientes:

- **SurfSense new-chat** (`/dashboard/{id}/new-chat`): agente multi-herramienta con LangGraph, ~14 conectores externos, streaming SSE, persistencia en BD, @mentions, HITL.
- **Brain chat** (`/dashboard/{id}/brain/chat`): RAG cascading de 4 niveles (L1→L2→Web→L0), RRF hybrid fusion, CRAG, reranking, chunk budget dinámico por modelo.

Además, la ingesta de SecondBrainSense solo acepta URL, fichero local y ruta del servidor — no aprovecha los ~14 conectores de SurfSense (OneDrive, Google Drive, Dropbox, Slack, Teams, etc.).

**Objetivo**: 
1. Que los conectores SurfSense alimenten la ingesta del brain.
2. Un único chat que combine lo mejor de ambos.

---

## 2. Principio arquitectónico clave

**Conector ≠ Extractor.**

- **Conector** = transporte. Da acceso a bytes/contenido desde una fuente (OneDrive, Confluence, etc.). No le importa el formato.
- **Extractor** = procesamiento por formato. Parsea un tipo de fichero (.pdf, .docx, .py). No le importa de dónde vino.

### Flujo de decisión

```
Conector descarga fichero
       ↓
¿Extensión conocida en ExtractorFactory._MAP?
  ├─ SÍ (.pdf, .docx, .xlsx, .py...)  → Extractor específico existente
  └─ NO (.vsdx, .msg, .dwg...)        → FallbackExtractor (NUEVO)
       ↓
Pipeline compartido: UniversalCleaner → Synthesizer → Writer → IngestRouter → Qdrant
```

Para conectores tipo API (Confluence, Jira, GitHub): ya tienen BrainConnector + BrainExtractor con virtual extension (.confluence, .jira_ticket, .github_file).

Para conectores tipo storage (OneDrive, Drive, Dropbox): descargan ficheros reales con extensión real → entran por el extractor que corresponda.

---

## 3. Matriz de funcionalidades — SurfSense vs Brain vs Chat Unificado

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

**Conclusión**: SurfSense aporta la infraestructura (agente, streaming, UI, persistencia). Brain aporta el RAG (cascading, RRF, CRAG, reranking). Se componen, no se reescriben.

---

## 4. Inventario actual de conectores

### Conectores SurfSense (upstream, `app/connectors/`)

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

### Familias de conectores (para adapter genérico)

| Familia | Conectores | Estrategia |
|---|---|---|
| **Storage** (ficheros binarios) | Google Drive, OneDrive, Dropbox | Descargar bytes → tmp → `ExtractorFactory.extract(tmp)`. Extensión real del fichero → extractor específico existente o FallbackExtractor. **Cero extractores nuevos.** |
| **Records** (entidades estructuradas) | Airtable, ClickUp, Linear, BookStack, Calendar, Luma, Elasticsearch | `GenericRecordAdapter`: serializa `{title, body, url, labels, status}` a Markdown → MarkdownExtractor existente. |
| **Chat/Mensajes** | Slack, Teams, Discord, Gmail | `GenericChatAdapter`: agrupa mensajes por hilo/canal como Markdown con timestamps → MarkdownExtractor existente. |

---

## 5. Componentes existentes del pipeline (sin cambios)

| Componente | Ubicación | Función |
|---|---|---|
| ExtractorFactory | `brain/extractors/factory.py` | Registro `extensión → Extractor`. API: `extract(filename) → list[dict]` |
| BaseExtractor | `brain/extractors/base.py` | Contrato: `extract(source) → blocks[]`, `_clean_blocks()` |
| 17 extractores | `brain/extractors/*.py` | pdf, docx, xlsx, pptx, html, py, sql, ipynb, xml, drawio, json, md, csv, txt + 3 virtuales |
| UniversalCleaner | `brain/rag_lib/layer1_universal/` | encoding, unicode, PII, quality_score, language detection |
| DocumentSynthesizer | `brain/synthesizer.py` | LLM → pasaporte semántico .md |
| BrainWriter | `brain/writer.py` | Escribe pasaporte .md a BRAIN_DIR |
| IngestRouter | `brain/ingest_router.py` | Distribuye blocks a colecciones Qdrant (brain/knowledge/code) |
| BrainRouter | `brain/router.py` | Retrieval L1→L2, RRF, reranking |
| QdrantManager | `brain/qdrant_manager.py` | Gestión colecciones Qdrant |
| LLMClient | `brain/llm_client.py` | Ollama/Claude/Hybrid unificado |
| ModelProfile | `brain/model_profiles.py` | Chunk budget dinámico por modelo |
| CRAGEvaluator | `brain/crag_evaluator.py` | Evaluación post-retrieval (opcional) |

---

## 6. Contratos técnicos relevantes

### ExtractorFactory.extract(filename) → list[dict]

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

### BrainConnector (brain/connectors/base.py)

```python
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

### IngestRouter.route()

```python
# Input
md_content: str           # markdown con frontmatter (del synthesizer)
blocks: list[dict]        # de ExtractorFactory.extract()
source: str               # slug canónico
search_space_id: str      # aislamiento multi-tenant

# Output
{"brain": {"chunks_created": N}, "knowledge": {"chunks_created": N}, "code": {"chunks_created": N}}
```

### Brain Query (POST /api/v1/brain/query)

```python
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

## 7. Plan de fases

### Fase 0 — FallbackExtractor

**Duración**: 1-2 días  
**Dependencias**: ninguna  
**Impacto**: solo `brain/extractors/`

**Entregables**:
- `brain/extractors/fallback.py`: extractor genérico
  - Intenta leer como texto plano (UTF-8, luego chardet/charset-normalizer)
  - Para binarios puros: emite un bloque con metadata del fichero (nombre, tamaño, extensión, fecha)
  - Reutiliza lógica de secciones del TxtExtractor para ficheros que resulten texto
- Modificar `ExtractorFactory.get()`: en vez de `raise ValueError` → devolver `FallbackExtractor()`
- Añadir `ExtractorFactory.is_known(ext) -> bool` para que la UI pueda mostrar si un formato tiene extractor específico
- Tests: `.vsdx`, `.msg`, `.unknown`, fichero binario sin extensión

**Criterio de éxito**: cualquier fichero pasa por el pipeline sin error. Los formatos desconocidos producen pasaporte con calidad menor pero válido.

### Fase 1 — Capa de transporte de conectores

**Duración**: 3-5 días  
**Dependencias**: Fase 0  
**Impacto**: `brain/connectors/`, `brain_routes.py`

**Entregables**:
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

**Riesgo**: tokens OAuth expirados. Mitigación: verificar token antes de ingestar, informar al usuario si necesita re-autenticar.

### Fase 2 — UI de ingesta con conectores

**Duración**: 3-5 días  
**Dependencias**: Fase 1  
**Impacto**: `surfsense_web/` (solo ingest page)

**Entregables**:
- Cuarto tab "Conector" en `ingest/page.tsx`:
  - Dropdown con conectores disponibles del usuario
  - Para storage: navegador de ficheros/carpetas del conector (tree view simple)
  - Para record: lista de ítems con filtros (proyecto, espacio, etc.)
  - Para chat: selector de canal/hilo
  - Indicador de formato: badge verde si tiene extractor específico, amarillo si irá por fallback
- Botón "Ingestar seleccionados" → llama endpoint de Fase 1
- Mismo monitor SSE de 4 fases existente
- Batch: permitir seleccionar múltiples ficheros/ítems y ingestar en secuencia

### Fase 3 — Brain Retrieval como LangGraph Tool

**Duración**: 5-7 días  
**Dependencias**: ninguna (paralela con F0-F2)  
**Impacto**: `brain/tools/` (nuevo), registro en agente SurfSense

**Entregables**:
- `brain/tools/brain_search_tool.py`:
  - LangChain `Tool` que encapsula `BrainRouter`
  - Input: `query: str, search_space_id: int, force_level: int | None`
  - Output: `{answer, level_used, level_label, sources, context_chunks}`
  - Internamente ejecuta toda la cascada: L1→L2(RRF+reranking)→CRAG→Web→L0
- Registrar `brain_search` como tool disponible en el agente multi-agente de SurfSense
  - Fichero de configuración del agente (registro de tools/subagentes)
  - System prompt adicional: "Usa brain_search como primera opción para preguntas sobre documentación interna del knowledge base"
- Metadata en respuesta del tool: `level_used`, `sources`, `level_label` → para que la UI pueda renderizar level badges

**Decisión de diseño**: Brain retrieval es un tool, no un subagente completo. El agente principal decide cuándo invocarlo. Esto mantiene la arquitectura de SurfSense intacta.

### Fase 4 — Chat unificado (UI)

**Duración**: 5-7 días  
**Dependencias**: Fase 3  
**Impacto**: `surfsense_web/` (rutas de chat, componentes)

**Entregables**:
- Ruta única: `/dashboard/{id}/chat` (redirect desde `/new-chat` y `/brain/chat`)
- Componentes nuevos integrados en el chat SurfSense:
  - `LevelBadge`: cuando la respuesta incluye metadata de `brain_search`
  - Panel de fuentes con scores: integrado en `AssistantMessage`
  - Toggle "Brain mode": deshabilita otros tools, fuerza uso de `brain_search`
  - Toggle "LLM libre": equivale a `force_l0=true`
  - Botón drill-down: re-query con `force_level=2` (detalle documental)
- Mantener TODO lo existente de SurfSense: @mentions, file upload, HITL, historial, threading, sharing, action log
- Eliminar `/brain/chat` como página separada (redirect permanente)

**Riesgo**: regresión en funcionalidad de new-chat. Mitigación: `brain_search` es un tool aditivo — si no se invoca, el agente funciona exactamente igual que antes.

### Fase 5 — Conectores en chat unificado

**Duración**: 2-3 días  
**Dependencias**: Fase 1 + Fase 4  
**Impacto**: UI del chat + endpoint bridge

**Entregables**:
- Cuando el agente usa un conector para traer un documento en el chat, añadir botón "Ingestar en Brain" en el action log
- Click en ese botón → llama endpoint de Fase 1 → ingesta el contenido traído por el conector
- Feedback inline: "Documento ingestado en Brain (3 chunks creados)"

---

## 8. Diagrama de dependencias

```
F0 (FallbackExtractor) ──→ F1 (Transporte conectores) ──→ F2 (UI ingesta conectores)
       [1-2 días]               [3-5 días]                     [3-5 días]

F3 (Brain como Tool) ──→ F4 (Chat unificado UI) ──→ F5 (Conectores en chat)
       [5-7 días]            [5-7 días]                 [2-3 días]
```

**F0-F2 y F3 son independientes** y pueden ejecutarse en paralelo.  
**Estimación total**: 19-29 días de desarrollo (2 tracks paralelos: ~15-19 días calendar).

---

## 9. Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| OAuth tokens expirados al ingestar desde conector | Alta | Media | Verificar token antes de ingestar. Si expirado, informar al usuario con link a re-auth |
| FallbackExtractor produce bloques de baja calidad | Media | Baja | Aceptable por diseño. Marcar `quality_score` bajo. A futuro se añaden extractores específicos |
| System prompt del agente crece mucho con brain_search | Media | Media | Inyectar instrucciones de brain solo cuando search_space tiene brain activo |
| Latencia de brain retrieval (L1→L2→CRAG) dentro del agente SSE | Media | Media | Emitir thinking steps mientras brain_search ejecuta. Timeout configurable |
| Regresión en new-chat al integrar componentes Brain | Baja | Alta | brain_search es aditivo. Tests E2E antes de merge. Feature flag para activar/desactivar |
| Conectores storage descargan ficheros grandes | Media | Media | Límite configurable (BRAIN_INGEST_MAX_FILE_SIZE). Ingesta en background para ficheros grandes |

---

## 10. Ficheros afectados por fase

### Fase 0
- `brain/extractors/fallback.py` — **NUEVO**
- `brain/extractors/factory.py` — modificar `get()` para fallback
- `tests/test_fallback_extractor.py` — **NUEVO**

### Fase 1
- `brain/connectors/surfsense_adapter.py` — **NUEVO**
- `brain/connectors/factory.py` — registrar adapters
- `app/routes/brain_routes.py` — endpoints nuevos
- `tests/test_surfsense_adapter.py` — **NUEVO**

### Fase 2
- `surfsense_web/app/dashboard/[id]/brain/ingest/page.tsx` — tab Conector
- `surfsense_web/lib/apis/brain-api.service.ts` — métodos nuevos
- `surfsense_web/contracts/types/brain.types.ts` — schemas nuevos

### Fase 3
- `brain/tools/brain_search_tool.py` — **NUEVO**
- `brain/tools/__init__.py` — **NUEVO**
- Registro en agente SurfSense (fichero de config del agente)
- System prompt parcial para brain_search

### Fase 4
- `surfsense_web/app/dashboard/[id]/chat/` — ruta unificada
- `surfsense_web/components/brain/LevelBadge.tsx` — reutilizar
- `surfsense_web/app/dashboard/[id]/new-chat/` — redirect
- `surfsense_web/app/dashboard/[id]/brain/chat/` — redirect o eliminar

### Fase 5
- UI del chat: botón "Ingestar en Brain" en action log
- Bridge endpoint o reutilización del de Fase 1

---

## 11. Criterios de éxito

| Fase | Criterio |
|---|---|
| F0 | Cualquier fichero con cualquier extensión pasa por el pipeline sin error |
| F1 | `POST /brain/ingest/connector/onedrive` ingesta un .docx de OneDrive correctamente |
| F2 | Usuario navega ficheros de OneDrive en la UI y los ingesta con un click |
| F3 | En el chat unificado, preguntar "¿qué dice el documento X?" invoca brain_search y devuelve respuesta con fuentes y nivel |
| F4 | Un único chat en `/dashboard/{id}/chat` con todas las funcionalidades de ambos sistemas |
| F5 | Tras traer un documento por conector en el chat, click en "Ingestar" lo añade al brain |

---

## 12. Decisiones arquitectónicas

| Decisión | Justificación |
|---|---|
| Brain retrieval como Tool de LangGraph, no como subagente | Menor complejidad. El agente principal decide cuándo usarlo. No requiere coordinación entre subagentes |
| Adapter genérico por familia (storage/record/chat), no un bridge por conector | 3 adapters cubren 14+ conectores. Esfuerzo O(familias) en vez de O(conectores) |
| FallbackExtractor en vez de rechazar formatos desconocidos | Todo documento entra en el pipeline. Calidad menor pero presente. Extensible a futuro |
| Ruta única `/chat` con redirect desde las antiguas | Transición suave. No rompe bookmarks ni links existentes |
| Feature flag para brain_search en el agente | Permite rollback sin despliegue. Activar por search_space |

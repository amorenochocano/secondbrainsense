# F5 — Pipeline Unificado: Second Brain como Motor de Ingesta
**Duración:** 2 semanas
**Equipo:** Backend Senior (1) + DevOps (0.5)
**Dependencias:** F4 completada
**Entregable:** Un solo pipeline de ingesta que usa extractores y limpieza de Second Brain para TODOS los documentos de SurfSense. pgvector eliminado — Qdrant como único motor de vectores. PostgreSQL solo texto (CRUD + BM25).

---

## Contexto: por qué F5 cambia respecto al plan original

El F5 original era un hook que duplicaba procesamiento: SurfSense hacía chunking+embedding en pgvector, y el hook repetía en Qdrant. Tras analizar el código real:

1. **L2 knowledge (Qdrant) y chunks SurfSense (pgvector) son lo mismo** — mismo texto, vectorizado dos veces.
2. **Second Brain tiene pipeline superior**: extractores por tipo, UniversalCleaner (quality_score, idioma, PII), nomic-embed-text (768d) vs all-MiniLM-L6-v2 (384d).
3. **Un único punto de consumo de pgvector** — `hybrid_search()` en `chunks_hybrid_search.py`.
4. **30+ tipos de documento en SurfSense** pero extractores para ~15 → necesitamos path genérico.

**Decisión arquitectónica:**
- Second Brain → motor de procesamiento (extracción, limpieza, embedding)
- SurfSense → plataforma (conectores, UI, auth, Celery, multi-tenant)
- Qdrant → búsqueda vectorial (único motor)
- PostgreSQL → CRUD + BM25 keyword search (sin embeddings)

---

## F5.0 — Estado real del codebase (Día 0)

### Tres categorías de ingesta

| Cat | Cuándo aplica | Ejemplos | Pipeline |
|-----|--------------|----------|----------|
| **A** | Fichero binario con extensión conocida | .docx .pdf .py .sql .xlsx .csv .pptx .ipynb .md .txt .html .xml .json .drawio | Extractor especializado → Cleaner → nomic → Qdrant + Síntesis |
| **B** | Conector con extractor virtual | Confluence, Jira, GitHub, Webcrawler | Extractor virtual (text→blocks) → Cleaner → nomic → Qdrant + Síntesis |
| **C** | Todo lo demás — "Saco" genérico | Slack, Discord, Gmail, Calendar, Airtable, Linear, ClickUp, YouTube, Luma, Teams, Bookstack, Notion, Obsidian, Elasticsearch, Circleback, Dropbox, NOTE | source_markdown → Cleaner → chunking semántico → nomic → Qdrant |

### DocumentTypes de SurfSense → Categoría

| DocumentType | Cat | Extractor Brain |
|-------------|-----|-----------------|
| FILE / LOCAL_FOLDER_FILE | A | Según extensión del fichero |
| GOOGLE_DRIVE_FILE / ONEDRIVE_FILE / DROPBOX_FILE | A | Según extensión |
| CONFLUENCE_CONNECTOR | B | confluence.py |
| JIRA_CONNECTOR | B | jira_ticket.py |
| GITHUB_CONNECTOR | B | github_file.py / python_file.py |
| WEBCRAWLER_CONNECTOR / CRAWLED_URL | B | web.py |
| NOTION_CONNECTOR | C | markdown.py (genérico) |
| SLACK_CONNECTOR | C | Saco + efímero (skip síntesis) |
| DISCORD_CONNECTOR | C | Saco + efímero |
| GOOGLE_GMAIL_CONNECTOR | C | Saco + efímero |
| GOOGLE_CALENDAR_CONNECTOR | C | Saco + efímero |
| TEAMS_CONNECTOR | C | Saco + efímero |
| LINEAR_CONNECTOR | C | Saco |
| CLICKUP_CONNECTOR | C | Saco |
| AIRTABLE_CONNECTOR | C | Saco |
| YOUTUBE_VIDEO | C | Saco (transcripción texto) |
| BOOKSTACK_CONNECTOR | C | Saco |
| LUMA_CONNECTOR | C | Saco |
| ELASTICSEARCH_CONNECTOR | C | Saco |
| OBSIDIAN_CONNECTOR | C | markdown.py (genérico) |
| CIRCLEBACK | C | Saco |
| NOTE | C | Saco |
| EXTENSION | C | Saco |

### Lo que F5 debe CREAR

| Componente | Fichero | Descripción |
|-----------|---------|-------------|
| Adaptador de ingesta | `app/indexing_pipeline/brain_ingestion_adapter.py` | Categoriza A/B/C → pipeline adecuado |
| Embedding unificado | `app/indexing_pipeline/unified_embedder.py` | nomic-embed-text centralizado |
| Migración Alembic | `alembic/versions/XXX_remove_chunk_embedding.py` | Elimina Chunk.embedding |
| Script migración datos | `scripts/migrate_pgvector_to_qdrant.py` | Re-embed documentos existentes |
| Tests | `tests/brain/test_unified_pipeline_f5.py` | Tests completos |

### Lo que F5 debe MODIFICAR

| Fichero | Cambio |
|---------|--------|
| `indexing_pipeline_service.py` | chunk_text_hybrid+embed_texts → process_document() |
| `chunks_hybrid_search.py` | vector_search() → Qdrant en vez de pgvector |
| `knowledge_search.py` | embed_texts([query]) → nomic vía unified_embedder |
| `db.py` | Eliminar Chunk.embedding y Document.embedding |
| `config/__init__.py` | EMBEDDING_MODEL → nomic-embed-text |
| `docker/.env` | Variables del pipeline unificado |

### Lo que NO cambia

- 20 connector indexers — siguen produciendo ConnectorDocument
- Brain F1-F4 — ya usa Qdrant
- BM25 — sigue en PostgreSQL sobre chunks.content
- UI, auth, Celery, multi-tenant

### Contratos reales verificados

```python
# IndexingPipelineService.index() — punto de extensión
async def index(self, document: Document, connector_doc: ConnectorDocument) -> Document:
    # Línea 389-398: chunk_text / chunk_text_hybrid ← REEMPLAZAR
    # Línea 403: embed_texts(texts_to_embed) ← REEMPLAZAR
    # Línea 407-408: Chunk(content=text, embedding=emb) ← embedding ya no
    # Línea 418: document.embedding = summary_embedding ← ELIMINAR
    # Línea 421: document.status = DocumentStatus.ready()
    # Línea 429: log_index_success(ctx, chunk_count=len(chunks))

# ChucksHybridSearchRetriever — único consumidor de pgvector
class ChucksHybridSearchRetriever:
    async def vector_search(query_text, top_k, search_space_id) → list[Chunk]
    async def full_text_search(query_text, top_k, search_space_id) → list[Chunk]  # BM25
    async def hybrid_search(query_text, top_k, search_space_id, ...) → list[dict]

# knowledge_search.search_knowledge_base() — el chat agent llama aquí
async def search_knowledge_base(query, search_space_id, ...) → list[dict]:
    [embedding] = await asyncio.to_thread(embed_texts, [query])  # ← MiniLM
    retriever = ChucksHybridSearchRetriever(session)
    results = await retriever.hybrid_search(..., query_embedding=embedding.tolist())

# ConnectorDocument — lo que producen los conectores (NO cambia)
class ConnectorDocument(BaseModel):
    title: str
    source_markdown: str           # texto ya extraído
    document_type: DocumentType
    search_space_id: int
    should_use_code_chunker: bool = False
    metadata: dict = {}
```

---

## F5.1 — `brain_ingestion_adapter.py`: Adaptador de ingesta unificado (Día 1-3)

**Fichero:** `surfsense_backend/app/indexing_pipeline/brain_ingestion_adapter.py` ← **CREAR**

Módulo central que reemplaza `chunk_text_hybrid() + embed_texts()` dentro de `index()`.

### API pública:

```python
"""
brain_ingestion_adapter.py
--------------------------
Adaptador que conecta el pipeline de ingesta de SurfSense con el motor
de procesamiento de Second Brain.

FLUJO:
  ConnectorDocument → categorizar(A/B/C)
      A: fichero binario → Extractor especializado → Cleaner → chunks
      B: conector conocido → Extractor virtual → Cleaner → chunks
      C: genérico ("saco") → Cleaner → chunking semántico → chunks

  chunks → embed(nomic-embed-text) → Qdrant colección knowledge
  chunks → PostgreSQL Chunk(content) — SIN embedding

DISEÑO:
  - Nunca lanza excepción al llamador (captura interna con fallback al saco)
  - Si el extractor especializado falla, cae al "saco" automáticamente
  - La síntesis de pasaporte es OPCIONAL y asíncrona (no bloquea ingesta)
  - Cero hardcode: categorías, modelos y umbrales desde os.getenv()

Variables de entorno:
  BRAIN_INGESTION_ENABLED   — activar pipeline Brain en ingesta (default: true)
  BRAIN_SYNTHESIS_ENABLED   — activar síntesis de pasaportes (default: true)
  BRAIN_EMBEDDING_MODEL     — modelo de embedding (default: nomic-embed-text)
  BRAIN_QUALITY_THRESHOLD   — umbral quality_score para filtrar bloques (default: 0.30)
"""

async def process_document(
    document: Document,
    connector_doc: ConnectorDocument,
) -> tuple[list[str], list[list[float]]]:
    """
    Procesa documento con pipeline Brain.
    Returns: (chunk_texts, chunk_embeddings)
    - chunk_texts → PostgreSQL Chunk.content (BM25)
    - chunk_embeddings → Qdrant knowledge (vectores)

    Nunca lanza excepción — fallback al saco si extractor falla.
    """
```

### Lógica interna:

```
process_document(document, connector_doc)
    │
    ├─ _categorize() → A / B / C
    │
    ├─ A → _process_category_a()
    │       → DocumentProcessor(extractor).process_file(temp_path)
    │       → [bloques con quality_score, idioma, PII]
    │
    ├─ B → _process_category_b()
    │       → DocumentProcessor(extractor).process_text(source_markdown)
    │       → [bloques con metadata del conector]
    │
    └─ C → _process_category_c()
            → UniversalCleaner(source_markdown)
            → _semantic_chunk_text(cleaned_text)
            → [bloques con quality_score]
    │
    ├─ _filter_low_quality(blocks, threshold=BRAIN_QUALITY_THRESHOLD)
    │
    ├─ _embed_chunks(texts) → nomic-embed-text 768d
    │
    ├─ _upsert_to_qdrant(texts, embeddings, search_space_id)
    │
    └─ return (chunk_texts, chunk_embeddings)
```

### El "saco" genérico (Categoría C) — detalle:

```python
async def _process_category_c(connector_doc: ConnectorDocument) -> list[dict]:
    """
    Path genérico para contenido sin extractor especializado.

    1. UniversalCleaner → quality_score, idioma, PII
    2. Chunking semántico:
       - Si hay headings (##/###) → cortar por headings
       - Si no → cortar por párrafos (~1000 chars por chunk)
       - Tablas markdown → chunk indivisible (igual que SurfSense)
    3. Cada chunk = {"content": str, "metadata": {quality_score, language, ...}}

    El "saco" no es inferior — aplica la misma limpieza y el mismo modelo
    de embedding que las categorías A y B. La única diferencia es que no
    tiene extracción estructural especializada (headings, tablas, AST).
    """
```

### Variables de entorno:

```bash
BRAIN_INGESTION_ENABLED=true      # false = fallback completo a SurfSense original
BRAIN_SYNTHESIS_ENABLED=true      # false = sin pasaportes, solo chunks en Qdrant
BRAIN_EMBEDDING_MODEL=nomic-embed-text
BRAIN_QUALITY_THRESHOLD=0.30      # bloques < 0.30 se descartan
```

### Tipos efímeros (skip síntesis pero SÍ indexar):

```python
_SKIP_SYNTHESIS_TYPES = {
    DocumentType.SLACK_CONNECTOR,
    DocumentType.DISCORD_CONNECTOR,
    DocumentType.GOOGLE_GMAIL_CONNECTOR,
    DocumentType.GOOGLE_CALENDAR_CONNECTOR,
    DocumentType.TEAMS_CONNECTOR,
    DocumentType.COMPOSIO_GMAIL_CONNECTOR,
    DocumentType.COMPOSIO_GOOGLE_CALENDAR_CONNECTOR,
}
# Estos tipos SÍ van a Qdrant knowledge (para buscar mensajes/emails)
# pero NO generan pasaporte .md (contenido demasiado efímero)
```

---

## F5.2 — Modificar `IndexingPipelineService.index()` (Día 3-4)

**Fichero:** `surfsense_backend/app/indexing_pipeline/indexing_pipeline_service.py` ← **MODIFICAR**

Reemplazar líneas 389-418 del método `index()`:

```python
# ANTES (líneas 389-418):
if connector_doc.should_use_code_chunker:
    chunk_texts = await asyncio.to_thread(chunk_text, ...)
else:
    chunk_texts = await asyncio.to_thread(chunk_text_hybrid, ...)
texts_to_embed = [content, *chunk_texts]
embeddings = await asyncio.to_thread(embed_texts, texts_to_embed)
summary_embedding, *chunk_embeddings = embeddings
chunks = [Chunk(content=text, embedding=emb) for ...]
document.embedding = summary_embedding

# DESPUÉS:
from app.indexing_pipeline.brain_ingestion_adapter import (
    process_document, BRAIN_INGESTION_ENABLED,
)

if BRAIN_INGESTION_ENABLED:
    chunk_texts, _ = await process_document(document, connector_doc)
    # Embeddings ya están en Qdrant — solo guardar texto en PG para BM25
    chunks = [Chunk(content=text) for text in chunk_texts]
    # document.embedding ya no se usa — vectores en Qdrant
else:
    # Fallback SurfSense original — para rollback de emergencia
    chunk_texts = await asyncio.to_thread(chunk_text_hybrid, content)
    embeddings = await asyncio.to_thread(embed_texts, [content, *chunk_texts])
    summary_embedding, *chunk_embeddings = embeddings
    chunks = [Chunk(content=text, embedding=emb)
              for text, emb in zip(chunk_texts, chunk_embeddings)]
    document.embedding = summary_embedding
```

**Posición exacta:** reemplaza líneas 389-418 del `index()` actual. El `try/except` de SurfSense se mantiene intacto — `process_document()` nunca lanza excepción.

**Regla crítica:** `BRAIN_INGESTION_ENABLED=false` → SurfSense funciona exactamente como antes. Permite rollback instantáneo sin redespliegue.

---

## F5.3 — Adaptar `ChucksHybridSearchRetriever` (Día 4-6)

**Fichero:** `surfsense_backend/app/retriever/chunks_hybrid_search.py` ← **MODIFICAR**

El cambio clave: `vector_search()` consulta Qdrant en vez de pgvector. `full_text_search()` (BM25) no cambia.

### `vector_search()` → Qdrant

```python
# ANTES — pgvector:
query = query.order_by(Chunk.embedding.op("<=>")(query_embedding)).limit(top_k)

# DESPUÉS — Qdrant:
async def vector_search(self, query_text, top_k, search_space_id, ...):
    from app.brain.qdrant_manager import QdrantManager
    from app.indexing_pipeline.unified_embedder import embed_query
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    # Embed la query con nomic-embed-text (mismo modelo que la ingesta)
    embedding = await asyncio.to_thread(embed_query, query_text)

    qdrant = QdrantManager.get_instance().client
    results = qdrant.search(
        collection_name="knowledge",
        query_vector=embedding,
        query_filter=Filter(must=[
            FieldCondition(key="search_space_id",
                           match=MatchValue(value=str(search_space_id)))
        ]),
        limit=top_k,
        with_payload=True,
    )

    # Convertir ScoredPoints a formato compatible con SurfSense
    return _scored_points_to_chunk_results(results, self.db_session)
```

### `full_text_search()` — SIN CAMBIOS (sigue en PostgreSQL BM25)

### `hybrid_search()` — combina Qdrant (vectores) + PostgreSQL (BM25) con RRF

La combinación de resultados de ambas fuentes se hace con Reciprocal Rank Fusion, exactamente como lo hace SurfSense hoy pero con vectores de Qdrant en vez de pgvector.

---

## F5.4 — `unified_embedder.py`: Un solo modelo de embedding (Día 2)

**Fichero:** `surfsense_backend/app/indexing_pipeline/unified_embedder.py` ← **CREAR**

```python
"""
unified_embedder.py
-------------------
Módulo centralizado de embedding para toda la plataforma SecondBrainSense.

UN SOLO MODELO para ingesta y consulta — garantiza que los vectores de
chunks y los vectores de queries usan el mismo espacio semántico.

Modelo: nomic-embed-text (768 dimensiones) vía Ollama local.
Ventaja sobre all-MiniLM-L6-v2 (384d): mayor capacidad semántica,
mejor rendimiento en retrieval multilingüe.

API:
  embed_query(text: str) -> list[float]       # para búsqueda
  embed_chunks(texts: list[str]) -> list[list[float]]  # para indexación

Variables de entorno:
  BRAIN_EMBEDDING_MODEL — nombre del modelo (default: nomic-embed-text)
  OLLAMA_HOST           — URL del servidor Ollama
"""
```

---

## F5.5 — Adaptar `knowledge_search.py` (Día 6-7)

**Fichero:** `app/agents/chat/multi_agent_chat/shared/middleware/knowledge_search.py` ← **MODIFICAR**

```python
# ANTES:
[embedding] = await asyncio.to_thread(embed_texts, [query])
# all-MiniLM-L6-v2 (384d)

# DESPUÉS:
from app.indexing_pipeline.unified_embedder import embed_query
embedding = await asyncio.to_thread(embed_query, query)
# nomic-embed-text (768d) — mismo modelo que la ingesta
```

Con este cambio, el chat de SurfSense automáticamente busca en Qdrant con el mismo modelo de embedding que se usó para indexar.

---

## F5.6 — Migración Alembic: eliminar `Chunk.embedding` (Día 8-9)

**Fichero:** `alembic/versions/XXX_remove_chunk_embedding_pgvector.py` ← **CREAR**

```python
"""Remove pgvector embedding columns from chunks and documents tables.

After F5, all vector search goes through Qdrant. The Chunk.embedding
column and its HNSW index are no longer needed. This migration:
1. Drops the chucks_vector_index (HNSW pgvector)
2. Drops the embedding column from chunks table
3. Drops the embedding column from documents table (summary vector)
4. Does NOT touch the BM25 index (chucks_search_index) — stays in PostgreSQL

REVERSIBLE: down() recreates the columns (empty) but does NOT re-embed.
Re-embedding requires running the full ingestion pipeline again.

EJECUTAR SOLO después de verificar que Qdrant tiene todos los vectores.
"""
```

**IMPORTANTE:** Esta migración se ejecuta DESPUÉS de verificar que Qdrant tiene todos los vectores. Se hace en una subfase separada con un script de verificación que compara el count de chunks en PostgreSQL con el count de vectores en Qdrant.

---

## F5.7 — Variables de entorno (Día 1)

```bash
# docker/.env — Nuevas variables F5

# ── Pipeline Unificado F5 ──────────────────────────────────────────────────
BRAIN_INGESTION_ENABLED=true      # Activar pipeline Brain en ingesta SurfSense
                                   # false = fallback al chunking básico original
BRAIN_SYNTHESIS_ENABLED=true       # Generar pasaportes .md (desactivable sin romper ingesta)
BRAIN_EMBEDDING_MODEL=nomic-embed-text  # Modelo único para ingesta Y consulta
BRAIN_QUALITY_THRESHOLD=0.30       # Bloques con quality_score < 0.30 se descartan

# NOTA: EMBEDDING_MODEL (SurfSense original) se mantiene como fallback
# durante la migración. Una vez verificado F5, se puede eliminar.
```

---

## F5.8 — Tests (Día 8-10)

**Fichero:** `tests/brain/test_unified_pipeline_f5.py`

| Clase | Tests | Qué cubre |
|-------|-------|-----------|
| `TestCategorizer` | 8 | Categorización A/B/C correcta por DocumentType y extensión |
| `TestCategoryA` | 6 | Ficheros binarios → extractor especializado → chunks limpios |
| `TestCategoryB` | 5 | Conectores con extractor virtual → chunks limpios |
| `TestCategoryC_Saco` | 7 | "Saco" genérico → Cleaner + chunking semántico |
| `TestUnifiedEmbedder` | 5 | nomic-embed-text produce 768d, misma query/doc space |
| `TestQdrantUpsert` | 6 | Chunks van a Qdrant knowledge con search_space_id correcto |
| `TestHybridSearchQdrant` | 8 | vector_search() consulta Qdrant, BM25 sigue en PG, hybrid combina |
| `TestFallbacks` | 5 | BRAIN_INGESTION_ENABLED=false, extractor falla → saco, etc. |
| `TestQualityFilter` | 4 | Bloques < 0.30 se descartan, log de auditoría |
| `TestSkipSynthesis` | 4 | Tipos efímeros sin pasaporte pero sí en Qdrant |
| `TestEndToEnd` | 4 | Documento completo: ingesta → Qdrant → consulta chat |
| **Total** | **62** | |

---

## F5.9 — Plan de migración de datos existentes (Día 10-12)

Para documentos ya indexados en pgvector que necesitan re-vectorizarse en Qdrant:

```bash
# Script de migración: re-embed todos los chunks existentes

# 1. Verificar Qdrant disponible
curl -s http://localhost:6333/health

# 2. Ejecutar migración batch
docker compose exec backend python -m scripts.migrate_pgvector_to_qdrant \
    --batch-size 100 \
    --model nomic-embed-text

# 3. Verificar counts
python -c "
from app.brain.qdrant_manager import QdrantManager
mgr = QdrantManager.get_instance()
print(f'knowledge: {mgr.client.count(\"knowledge\").count} vectores')
print(f'brain: {mgr.client.count(\"brain\").count} vectores')
"

# 4. Solo después de verificar → ejecutar migración Alembic
alembic upgrade head  # elimina Chunk.embedding
```

---

## F5.10 — Orden de implementación

| Día | Tarea | Riesgo |
|-----|-------|--------|
| 1 | F5.0 verificación + F5.7 variables .env | Bajo |
| 2 | F5.4 unified_embedder.py | Bajo |
| 1-3 | F5.1 brain_ingestion_adapter.py | Medio |
| 3-4 | F5.2 modificar index() con fallback | Medio |
| 4-6 | F5.3 adaptar hybrid_search → Qdrant | **Alto** |
| 6-7 | F5.5 adaptar knowledge_search.py | Medio |
| 8-10 | F5.8 tests | Bajo |
| 10-12 | F5.9 migración datos + F5.6 Alembic | **Alto** |

---

## Checklist F5

### F5.0 — Prerrequisitos
- [ ] F4 completada y tests pasando
- [ ] Qdrant operativo con colecciones brain/knowledge/code
- [ ] nomic-embed-text descargado en Ollama (`ollama pull nomic-embed-text`)

### F5.1 — Adaptador de ingesta
- [ ] `brain_ingestion_adapter.py` creado
- [ ] Categorización A/B/C correcta para los 30+ DocumentTypes
- [ ] "Saco" genérico con UniversalCleaner + chunking semántico
- [ ] Fallback automático si extractor falla → cae al saco
- [ ] Nunca propaga excepciones al pipeline SurfSense
- [ ] `_SKIP_SYNTHESIS_TYPES` excluye tipos efímeros de la síntesis

### F5.2 — `IndexingPipelineService.index()` modificado
- [ ] Llama a `process_document()` en vez de `chunk_text_hybrid+embed_texts`
- [ ] `BRAIN_INGESTION_ENABLED=false` → fallback al pipeline SurfSense original
- [ ] `Chunk(content=text)` — sin embedding (vectores en Qdrant)

### F5.3 — `ChucksHybridSearchRetriever` adaptado
- [ ] `vector_search()` consulta Qdrant colección knowledge
- [ ] `full_text_search()` sigue en PostgreSQL (BM25, sin cambios)
- [ ] `hybrid_search()` combina Qdrant vectores + PostgreSQL BM25

### F5.4 — `unified_embedder.py`
- [ ] `embed_query()` y `embed_chunks()` centralizados
- [ ] nomic-embed-text vía Ollama, 768 dimensiones
- [ ] Thread-safe (lock global como el original)

### F5.5 — `knowledge_search.py` adaptado
- [ ] `embed_texts([query])` → `embed_query(query)` con nomic-embed-text
- [ ] El chat de SurfSense usa Qdrant automáticamente

### F5.6 — Migración Alembic
- [ ] `Chunk.embedding` eliminado de `db.py`
- [ ] `Document.embedding` eliminado de `db.py`
- [ ] Índice HNSW pgvector eliminado
- [ ] Índice BM25 tsvector mantenido

### F5.7 — Variables de entorno
- [ ] `BRAIN_INGESTION_ENABLED=true`
- [ ] `BRAIN_SYNTHESIS_ENABLED=true`
- [ ] `BRAIN_EMBEDDING_MODEL=nomic-embed-text`
- [ ] `BRAIN_QUALITY_THRESHOLD=0.30`

### F5.8 — Tests
- [ ] 62+ tests unitarios pasando
- [ ] Tests de integración con Qdrant real

### Criterio de aceptación global F5
- [ ] Indexar un documento Confluence → aparece en PostgreSQL (texto) Y en Qdrant (vectores)
- [ ] El chat de SurfSense (search_knowledge_base) busca en Qdrant — NO en pgvector
- [ ] Un mensaje de Slack se indexa con el "saco" genérico → Qdrant con quality_score
- [ ] Con `BRAIN_INGESTION_ENABLED=false`: el pipeline funciona exactamente como antes de F5
- [ ] `ollama` caído → la ingesta SurfSense no se rompe (fallback o cola)
- [ ] Un mismo documento buscado desde el chat SurfSense y desde Brain Chat F4 devuelve los mismos chunks
- [ ] `Chunk.embedding` eliminado de PostgreSQL
- [ ] BM25 sigue funcionando en PostgreSQL

---

**Anterior:** [F4 — Router Multinivel](./F4-router-multinivel.md)
**Siguiente:** [F6 — UI Integrada](./F6-ui-integracion.md)

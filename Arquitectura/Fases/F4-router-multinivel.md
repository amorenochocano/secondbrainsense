# F4 — Router Multinivel L1→L2→BM25→Web→L0
**Duración:** 1 semana  
**Equipo:** Backend Senior (1) + IA Engineer (1)  
**Dependencias:** F3 completada  
**Entregable:** Cascada de retrieval con 5 niveles. El router devuelve el contexto más rico posible antes de llegar al LLM libre. Configurable por modelo (3B vs 7B vs Claude). `search_space_id` en todas las búsquedas.

---

## Objetivo

Sustituir la búsqueda híbrida plana de SurfSense por el router multinivel de Second Brain, **extendiendo L0 con BM25 y búsqueda web** — capacidades que SurfSense ya tiene operativas y que son críticas cuando el modelo LLM es pequeño (3B/7B local).

**Antes (SurfSense):**
```
query → hybrid search (pgvector + BM25) → top-K chunks → LLM → respuesta
```

**Después (BrainSense F4) — cascada extendida:**
```
query → L1: brain semántico (pasaportes Qdrant)
           │ relevante (score ≥ L1_MIN_SCORE)
           ├──→ L2: knowledge+code (Qdrant + cross-encoder reranking)
           │        → contexto semántico rico → LLM
           │
           └ no relevante
              → L2.b: BM25 PostgreSQL (tsvector — <100ms, sin coste extra)
                       │ hits → contexto keyword preciso → LLM
                       │
                       └ sin hits (o BRAIN_WEB_ENABLED=true)
                          → L2.c: SearXNG / Tavily (web — ya en SurfSense)
                                   │ resultados → contexto web → LLM
                                   │
                                   └ sin resultados o BRAIN_L0_ENABLED=false
                                      → L0: LLM libre (último recurso)
```

**Por qué esta cascada importa especialmente con modelos pequeños:**

| Modelo | Context tokens | L0 sin contexto | Con BM25/Web |
|--------|----------------|-----------------|--------------|
| qwen2.5-coder:3b | 6K | Alta alucinación | Respuesta útil con 1-2 chunks |
| qwen2.5-coder:7b | 28K | Alucinación moderada | Buena con 5-8 chunks |
| llama3.1:8b | 30K | Moderada | Buena |
| Claude / GPT-4 | 180K | Aceptable | Excelente |

Para modelos ≤7B en CPU, **L0 libre es casi inutilizable en contextos técnicos**. Un chunk BM25 de 300 tokens sobre la pregunta exacta es más valioso que la capacidad de razonamiento del modelo sin contexto. **La cascada convierte L0 en verdadero último recurso.**

---

## F4.0 — Estado del codebase: qué existe y qué adaptar (Día 0)

| Fichero | Estado | Acción F4 |
|---------|--------|-----------|
| `app/brain/router.py` | ✅ Existe — `BrainRouter()` sin args, síncrono, devuelve `dict` | Añadir `search_space_id`, BM25 hook, web hook |
| `app/retriever/chunks_hybrid_search.py` | ✅ Existe — `full_text_search()` con `search_space_id` | Reutilizar directamente en L2.b |
| `app/agents/chat/shared/tools/web_search.py` | ✅ Existe — `web_search_service.search()` async | Reutilizar directamente en L2.c |
| `app/brain/model_profiles.py` | ✅ Existe — `ModelProfile` con `context_tokens`, `prompt_tier` | Leer para presupuesto de contexto |
| `app/routes/brain_routes.py` | ❌ No existe | **CREAR** |

**Tres correcciones críticas en `router.py` antes de extender:**

| Gap | Problema actual | Fix |
|----|----------------|-----|
| G1 | Constructor sin args — no soporta multi-tenant | Añadir `search_space_id` al constructor |
| G7 | `_search()` y `_search_filtered()` no filtran por `search_space_id` | Añadir filtro `FieldCondition` en ambos métodos |
| G3 | `_build_response()` devuelve `ScoredPoint` de Qdrant | El router devuelve `dict` — la ruta FastAPI extrae `payload` para el LLM |

> **Nota importante**: el `BrainRouter` es **retrieval-only** — devuelve contexto, no respuesta. La llamada al LLM ocurre en la ruta FastAPI (`brain_routes.py`), no dentro del router. Esto permite reutilizar el router desde LangGraph (chat de SurfSense) sin duplicar lógica.

---

## F4.1 — Cambios en `router.py`: `search_space_id` + L2.b + L2.c (Día 1-2)

**Fichero:** `surfsense_backend/app/brain/router.py` ← extender

### Constructor: añadir `search_space_id`

```python
# ANTES:
class BrainRouter:
    def __init__(self):
        self._qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

# DESPUÉS:
class BrainRouter:
    def __init__(self, search_space_id: str = ""):
        self._qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        self.search_space_id = search_space_id
```

### Añadir filtro `search_space_id` en `_search()`

```python
# ANTES:
def _search(self, collection: str, query: str, top_k: int) -> list:
    vector = _embed_for_collection(query, collection)
    try:
        return self._qdrant.search(
            collection_name=collection,
            query_vector=vector,
            limit=top_k,
            with_payload=True,
        )

# DESPUÉS:
def _search(self, collection: str, query: str, top_k: int) -> list:
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    vector = _embed_for_collection(query, collection)
    qdrant_filter = None
    if self.search_space_id:
        qdrant_filter = Filter(must=[
            FieldCondition(key="search_space_id", match=MatchValue(value=self.search_space_id))
        ])
    try:
        return self._qdrant.search(
            collection_name=collection,
            query_vector=vector,
            query_filter=qdrant_filter,
            limit=top_k,
            with_payload=True,
        )
```

> Aplicar el mismo patrón en `_search_filtered()` — añadir `search_space_id` como condición extra en `must`.

### Nuevo método `_search_bm25()` — reutiliza `ChucksHybridSearchRetriever`

```python
def _search_bm25(
    self,
    question: str,
    search_space_id_int: int,   # ChucksHybridSearchRetriever usa int, no UUID str
    db_session,
    top_k: int = 5,
) -> list[dict]:
    """
    Búsqueda keyword (tsvector PostgreSQL) sobre la tabla chunks de SurfSense.
    Reutiliza ChucksHybridSearchRetriever.full_text_search() que ya existe.

    Retorna lista de dicts con: text, source, score (ts_rank).
    """
    import asyncio
    from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever

    retriever = ChucksHybridSearchRetriever(db_session)

    # full_text_search es async — ejecutar en el event loop del caller
    # (la ruta FastAPI pasa db_session como parámetro)
    chunks = asyncio.get_event_loop().run_until_complete(
        retriever.full_text_search(
            query_text=question,
            top_k=top_k,
            search_space_id=search_space_id_int,
        )
    )
    # Normalizar al formato dict del router
    return [
        {
            "text":   c.content if hasattr(c, "content") else str(c),
            "source": getattr(getattr(c, "document", None), "title", ""),
            "score":  0.0,  # BM25 no devuelve score numérico comparable — usar flag
            "level":  "bm25",
        }
        for c in (chunks or [])
    ]
```

> **Nota**: `ChucksHybridSearchRetriever` es **async**. El router actual es **síncrono**. Para evitar romper el router, la llamada BM25 se hace desde la ruta FastAPI (que sí es async) — ver F4.2. No envolver con `run_until_complete` dentro del router síncrono.

### Nuevo método `_search_web()` — reutiliza `web_search_service`

```python
# No se añade al router síncrono — se llama directamente desde brain_routes.py
# web_search_service.search() es async y ya tiene su propia abstracción
```

### Extensión del método `route()`: hook para BM25/Web

El método `route()` existente devuelve el dict de respuesta. Se añade un campo extra para indicar si el resultado viene de L1/L2 o si debe intentarse BM25/Web:

```python
def route(self, question: str, top_k: int = 4, ...) -> dict:
    # ... lógica existente sin cambios ...

    # Si L1 y L2 no tienen resultados:
    if self.needs_level2(question, l1_results) and l1_max < L1_MIN_SCORE:
        log.info("[brain_router] L1+L2 sin resultados relevantes → señal para L2.b/L2.c")
        return self._build_response(
            level=0,
            collection=BRAIN,
            results=[],
            fallback_needed=True,   # ← señal para brain_routes.py
        )

def _build_response(self, level, collection, results, level1=None, fallback_needed=False) -> dict:
    return {
        "level_used":           level,
        "collection_used":      collection,
        "results":              results,
        "sources_consulted":    self._sources_from(results),
        "drill_down_available": level == 1 and len(results) > 0,
        "level1_results":       level1,
        "fallback_needed":      fallback_needed,  # ← NUEVO — trigger BM25/Web en la ruta
    }
```

---

## F4.2 — `brain_routes.py`: cascada completa + presupuesto de contexto (Día 2-3)

**Fichero:** `surfsense_backend/app/routes/brain_routes.py` ← **NUEVO**

La ruta FastAPI implementa la cascada completa porque los niveles L2.b y L2.c son **async** y requieren DB session y web service — dependencias que no pertenecen al router síncrono.

```python
# surfsense_backend/app/routes/brain_routes.py
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.router import BrainRouter
from app.brain.llm_client import LLMClient, SYNTHESIS_MODEL, DEFAULT_PROVIDER
from app.brain.model_profiles import get_profile
from app.db import get_async_session
from app.users import current_active_user, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/brain", tags=["brain"])

# ── Configuración de la cascada ─────────────────────────────────────────────
BRAIN_BM25_ENABLED    = os.getenv("BRAIN_BM25_ENABLED",    "true").lower() == "true"
BRAIN_WEB_ENABLED     = os.getenv("BRAIN_WEB_ENABLED",     "true").lower() == "true"
BRAIN_L0_ENABLED      = os.getenv("BRAIN_L0_ENABLED",      "true").lower() == "true"
BRAIN_WEB_MAX_RESULTS = int(os.getenv("BRAIN_WEB_MAX_RESULTS", "5"))

# ── Presupuesto de contexto por tier de modelo ──────────────────────────────
# Cantidad máxima de chunks a incluir en el prompt por tier
_CHUNK_BUDGET = {
    "small":  2,   # 3B-4B: 6K tokens — máximo 2 chunks de ~500 tokens
    "medium": 6,   # 7B-14B: 28K tokens — hasta 6 chunks
    "claude": 15,  # API: 180K tokens — sin restricción práctica
}

# Longitud máxima de cada chunk en chars para el prompt por tier
_CHUNK_MAX_CHARS = {
    "small":  400,   # chunks cortos para no saturar contexto
    "medium": 800,
    "claude": 2000,
}


class BrainQueryRequest(BaseModel):
    question: str
    search_space_id: str
    chat_history: list[dict] = []
    top_k: int = 4
    force_level: int | None = None     # 1 ó 2 — forzar nivel del router
    force_l0: bool = False             # saltar todo el retrieval directamente a L0


class BrainQueryResponse(BaseModel):
    answer: str
    level_used: int                    # 1=brain, 2=knowledge/code, 3=bm25, 4=web, 0=libre
    level_label: str                   # "🧠 Brain", "📚 Knowledge", "🔍 BM25", "🌐 Web", "🤖 LLM"
    sources: list[str]
    context_chunks: int                # nº de chunks usados en el prompt
    model_tier: str                    # "small" | "medium" | "claude"


@router.post("/query", response_model=BrainQueryResponse)
async def brain_query(
    req: BrainQueryRequest,
    current_user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Router de consulta multinivel L1→L2→BM25→Web→L0.
    La cascada se detiene en el primer nivel que produce contexto relevante.
    El presupuesto de contexto (nº y longitud de chunks) se adapta al modelo activo.
    """
    # ── Perfil del modelo activo ──────────────────────────────────────────
    profile = get_profile(SYNTHESIS_MODEL)
    tier    = profile.prompt_tier if profile else "medium"
    chunk_budget    = _CHUNK_BUDGET.get(tier, 6)
    chunk_max_chars = _CHUNK_MAX_CHARS.get(tier, 800)

    logger.info(
        "[brain_query] question=%r space=%s model=%s tier=%s budget=%d",
        req.question[:60], req.search_space_id, SYNTHESIS_MODEL, tier, chunk_budget
    )

    # ── L0 forzado ────────────────────────────────────────────────────────
    if req.force_l0:
        answer = _call_llm(req.question, context="", tier=tier, history=req.chat_history)
        return BrainQueryResponse(answer=answer, level_used=0, level_label="🤖 LLM",
                                   sources=[], context_chunks=0, model_tier=tier)

    # ── L1 + L2: router síncrono Brain (Qdrant) ───────────────────────────
    brain_router = BrainRouter(search_space_id=req.search_space_id)
    route_result = brain_router.route(
        question=req.question,
        top_k=req.top_k,
        force_level=req.force_level,
    )

    if not route_result.get("fallback_needed") and route_result.get("results"):
        # L1 o L2 devolvieron resultados
        level  = route_result["level_used"]
        chunks = _extract_chunks(route_result["results"], chunk_budget, chunk_max_chars)
        context = _build_context(chunks)
        sources = route_result["sources_consulted"]
        label   = "🧠 Brain" if level == 1 else "📚 Knowledge"

        answer = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
        return BrainQueryResponse(answer=answer, level_used=level, level_label=label,
                                   sources=sources, context_chunks=len(chunks), model_tier=tier)

    # ── L2.b: BM25 PostgreSQL ─────────────────────────────────────────────
    if BRAIN_BM25_ENABLED:
        logger.info("[brain_query] L1/L2 sin resultados → intentando BM25")
        try:
            from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever
            # search_space_id en SurfSense es int en el retriever — obtener de la sesión
            space_int = await _resolve_search_space_int(req.search_space_id, db)
            if space_int is not None:
                retriever  = ChucksHybridSearchRetriever(db)
                bm25_chunks = await retriever.full_text_search(
                    query_text=req.question,
                    top_k=chunk_budget * 2,
                    search_space_id=space_int,
                )
                if bm25_chunks:
                    chunks  = _extract_chunks_from_orm(bm25_chunks, chunk_budget, chunk_max_chars)
                    context = _build_context(chunks)
                    sources = list({getattr(c, "document", None) and c.document.title or "" for c in bm25_chunks[:chunk_budget]})
                    logger.info("[brain_query] BM25 → %d chunks", len(chunks))

                    answer = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
                    return BrainQueryResponse(answer=answer, level_used=3, level_label="🔍 BM25",
                                               sources=sources, context_chunks=len(chunks), model_tier=tier)
        except Exception as exc:
            logger.warning("[brain_query] BM25 falló: %s — continuando cascada", exc)

    # ── L2.c: SearXNG web search con query rewriting ─────────────────────
    if BRAIN_WEB_ENABLED:
        logger.info("[brain_query] BM25 sin resultados → intentando web (SearXNG)")
        try:
            from app.services import web_search_service
            if web_search_service.is_available():
                # Query rewriting: la pregunta en lenguaje natural → keywords
                # Mejora significativa de la calidad de resultados en SearXNG
                search_query = await _rewrite_query_for_web(req.question)
                logger.info(
                    "[brain_query] QueryRewrite: %r → %r",
                    req.question[:50], search_query
                )

                _, web_docs = await web_search_service.search(
                    query=search_query,    # ← query optimizado, no la pregunta cruda
                    search_space_id=None,  # web search no filtra por space
                    top_k=BRAIN_WEB_MAX_RESULTS,
                )
                if web_docs:
                    chunks = []
                    for d in web_docs[:chunk_budget]:
                        content = (d.get("content") or "")[:chunk_max_chars]
                        title   = (d.get("document", {}) or {}).get("title", "web")
                        if content.strip():
                            chunks.append({"text": content, "source": title})

                    if chunks:
                        context = _build_context(chunks)
                        sources = [c["source"] for c in chunks]
                        logger.info("[brain_query] Web → %d snippets", len(chunks))

                        answer = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
                        return BrainQueryResponse(answer=answer, level_used=4, level_label="🌐 Web",
                                                   sources=sources, context_chunks=len(chunks), model_tier=tier)
        except Exception as exc:
            logger.warning("[brain_query] Web search falló: %s — cayendo a L0", exc)

    # ── L0: LLM libre — último recurso ────────────────────────────────────
    if not BRAIN_L0_ENABLED:
        raise HTTPException(
            status_code=404,
            detail="No se encontró contexto relevante y L0 está deshabilitado (BRAIN_L0_ENABLED=false)"
        )

    logger.info(
        "[brain_query] Cascada agotada → L0 libre (tier=%s). "
        "Para modelos ≤7B considerar activar BRAIN_BM25_ENABLED/BRAIN_WEB_ENABLED.",
        tier,
    )
    answer = _call_llm(req.question, context="", tier=tier, history=req.chat_history)
    return BrainQueryResponse(answer=answer, level_used=0, level_label="🤖 LLM",
                               sources=[], context_chunks=0, model_tier=tier)


# ── Endpoints CRUD de pasaportes ─────────────────────────────────────────────

@router.get("/passport/{source}")
async def get_passport(
    source: str,
    current_user: User = Depends(current_active_user),
):
    """Lee el pasaporte .md de un documento."""
    from app.brain.writer import BrainWriter
    writer = BrainWriter()
    content = writer.read(source)   # devuelve None si no existe (no lanza excepción)
    if content is None:
        raise HTTPException(404, detail=f"Pasaporte no encontrado: {source}")
    return {"source": source, "passport_md": content}


@router.delete("/document/{source}")
async def delete_brain_document(
    source: str,
    search_space_id: str,           # requerido — aislamiento multi-tenant
    current_user: User = Depends(current_active_user),
):
    """Elimina pasaporte .md + vectores Qdrant. Requiere search_space_id."""
    from app.brain.writer import BrainWriter
    from app.brain.qdrant_manager import QdrantManager
    BrainWriter().delete(source)
    QdrantManager.get_instance().delete_by_source(source=source, search_space_id=search_space_id)
    return {"status": "deleted", "source": source}


@router.post("/document/{source}/resynthesize")
async def resynthesize_document(
    source: str,
    current_user: User = Depends(current_active_user),
):
    """Re-sintetizar pasaporte desde la fuente original (vía tarea Celery)."""
    from app.tasks.document.resynthesize_task import resynthesize_task
    resynthesize_task.delay(source=source, user_id=str(current_user.id))
    return {"status": "queued", "source": source}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _extract_chunks(qdrant_results: list, budget: int, max_chars: int) -> list[dict]:
    """Extrae y trunca chunks de resultados Qdrant (ScoredPoint)."""
    chunks = []
    for r in qdrant_results[:budget]:
        payload = r.payload if hasattr(r, "payload") else {}
        text    = (payload.get("text", "") or "")[:max_chars]
        source  = payload.get("source", "")
        if text.strip():
            chunks.append({"text": text, "source": source})
    return chunks


def _extract_chunks_from_orm(orm_chunks, budget: int, max_chars: int) -> list[dict]:
    """Extrae y trunca chunks de ORM Chunk objects."""
    result = []
    for c in orm_chunks[:budget]:
        text = (getattr(c, "content", "") or "")[:max_chars]
        if text.strip():
            result.append({"text": text, "source": getattr(getattr(c, "document", None), "title", "")})
    return result


def _build_context(chunks: list[dict]) -> str:
    """Construye el string de contexto para el prompt LLM."""
    parts = []
    for i, c in enumerate(chunks):
        source_label = f"[{c['source']}] " if c.get("source") else ""
        parts.append(f"{source_label}{c['text']}")
    return "\n\n---\n\n".join(parts)


def _call_llm(question: str, context: str, tier: str, history: list[dict]) -> str:
    """
    Llamada al LLM con el contexto construido por la cascada.
    Ajusta el system prompt y la longitud del contexto al tier del modelo.
    """
    client = LLMClient()

    if context:
        system = (
            "Eres un asistente técnico experto. Responde basándote EXCLUSIVAMENTE en el "
            "contexto proporcionado. Cita la fuente cuando sea relevante. "
            "Si la respuesta no está en el contexto, dilo explícitamente."
        )
        # Para modelos small: prompt más corto y directivo
        if tier == "small":
            prompt = f"Contexto:\n{context}\n\nPregunta: {question}\nRespuesta:"
        else:
            history_text = "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-4:])
            prompt = f"{history_text}\n\nContexto:\n{context}\n\nPregunta: {question}"
    else:
        system = (
            "Eres un asistente técnico. No tienes información específica sobre esta pregunta. "
            "Responde desde tu conocimiento general e indica que no tienes documentación de referencia."
        )
        prompt = question

    return client.generate(
        prompt=prompt,
        system=system,
        provider=DEFAULT_PROVIDER,
        model=SYNTHESIS_MODEL,
    )


async def _resolve_search_space_int(search_space_id_str: str, db: AsyncSession) -> int | None:
    """Resuelve el UUID string de search_space a su ID entero para el retriever."""
    from sqlalchemy import select
    from app.db import SearchSpace
    try:
        result = await db.execute(
            select(SearchSpace.id).where(SearchSpace.uuid == search_space_id_str)
        )
        return result.scalar_one_or_none()
    except Exception:
        return None


async def _rewrite_query_for_web(pregunta: str) -> str:
    """
    Convierte una pregunta en lenguaje natural en keywords optimizadas para SearXNG.
    Del módulo CRAG F8: migrado como utilidad de L2.c.

    Ejemplos:
        '¿Por qué mi pipeline falla con Heading 4 en docx?' → 'docx heading4 pipeline fail extraction'
        '¿Cómo conectar Qdrant con Python?' → 'Qdrant Python client connection'

    Fallback: si el LLM falla, usa los primeros 6 tokens de la pregunta original.
    """
    _REWRITE_PROMPT = (
        "Convierte la siguiente pregunta en 4-6 palabras clave para buscar en Google. "
        "Devuelve SOLO las palabras clave separadas por espacios, sin explicación ni puntuación.\n\n"
        f"Pregunta: {pregunta.strip()}"
    )
    try:
        client = LLMClient()
        raw = client.generate(
            prompt=_REWRITE_PROMPT,
            system="Optimizador de queries. Responde solo con palabras clave.",
            provider=DEFAULT_PROVIDER,
            model=SYNTHESIS_MODEL,
        )
        keywords = raw.strip().replace("\n", " ")
        # Sanear: eliminar signos de puntuación y truncar a 80 chars
        keywords = " ".join(keywords.split())[:80]
        return keywords if keywords else " ".join(pregunta.split()[:6])
    except Exception as exc:
        logger.warning("[QueryRewrite] LLM falló: %s → usando truncado de pregunta", exc)
        return " ".join(pregunta.split()[:6])
```

---

## F4.3 — Variables de entorno para la cascada (Día 3)

```bash
# .env.dev — configuración de la cascada por entorno

# ── Cascada de retrieval ──────────────────────────────────────
BRAIN_BM25_ENABLED=true          # Activar BM25 PostgreSQL como L2.b
BRAIN_WEB_ENABLED=true           # Activar SearXNG como L2.c
BRAIN_L0_ENABLED=true            # Permitir L0 como último recurso
BRAIN_WEB_MAX_RESULTS=5          # Limitar resultados web (importante para 3B)

# ── Router Qdrant ─────────────────────────────────────────────
ROUTER_L1_HIGH_SCORE=0.75        # Score mínimo para considerar L1 "completo"
ROUTER_L1_MIN_SCORE=0.60         # Score mínimo para activar L2 semántico
RERANKING_ENABLED=true           # Cross-encoder en L2 (desactivar si GPU no disponible)
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
L2_CANDIDATE_MULTIPLIER=2        # Candidatos L2 = top_k × este factor

# ── CRAG — Agente Evaluador (multi-provider) ─────────────────────
# El evaluador CRAG se adapta automáticamente al provider:
#   ollama → 1 chunk/llamada, parsing robusto, early exit, max 3 chunks
#   claude → 3 chunks/llamada batch, XML wrapping, confianza, max 8 chunks
#   openai → 3 chunks/llamada batch, JSON mode nativo, max 6 chunks
#
CRAG_EVALUATOR_ENABLED=false     # false=desactivado | true=activo
CRAG_EVALUATOR_PROVIDER=ollama   # ollama | claude | openai (autodetecta perfil)
CRAG_EVALUATOR_MODEL=qwen2.5-coder:3b   # modelo para evaluación
CRAG_REWRITER_MODEL=qwen2.5-coder:3b   # modelo para query rewriting web (siempre activo)
CRAG_MAX_EVAL_CHUNKS=3           # chunks a evaluar máximo (override del perfil)
CRAG_EVAL_TIMEOUT=15             # timeout evaluador en segundos

# ── Configuración recomendada CRAG por provider ──────────────────
#
# Para Ollama local (qwen2.5-coder:3b/7b):
#   CRAG_EVALUATOR_ENABLED=false     ← NO recomendado: +15s latencia
#   # Si igualmente quieres activarlo:
#   CRAG_EVALUATOR_PROVIDER=ollama
#   CRAG_EVALUATOR_MODEL=qwen2.5-coder:3b  ← modelo rápido, no el de síntesis
#   CRAG_MAX_EVAL_CHUNKS=2                  ← solo 2 chunks para limitar latencia
#
# Para Claude (Anthropic o Azure AI Foundry):
#   CRAG_EVALUATOR_ENABLED=true      ← RECOMENDADO: ~1s latencia total
#   CRAG_EVALUATOR_PROVIDER=claude
#   CRAG_EVALUATOR_MODEL=claude-3-haiku-20240307  ← haiku: rápido + barato
#   CRAG_MAX_EVAL_CHUNKS=8                        ← puede evaluar más
#
# Para OpenAI (GPT-4o / GPT-4o-mini):
#   CRAG_EVALUATOR_ENABLED=true      ← RECOMENDADO: ~1.5s latencia total
#   CRAG_EVALUATOR_PROVIDER=openai
#   CRAG_EVALUATOR_MODEL=gpt-4o-mini             ← rápido + JSON mode nativo
#   CRAG_MAX_EVAL_CHUNKS=6
#
# Para modo híbrido (síntesis=Ollama, evaluación=Claude):
#   # Usa Ollama para síntesis (gratis) pero Claude para evaluación (preciso)
#   LLM_PROVIDER=ollama              ← provider principal
#   CRAG_EVALUATOR_ENABLED=true
#   CRAG_EVALUATOR_PROVIDER=claude   ← override: evaluador usa Claude
#   CRAG_EVALUATOR_MODEL=claude-3-haiku-20240307

# ── Recomendación por modelo ──────────────────────────────────
# Para qwen2.5-coder:3b (3B local):
#   BRAIN_BM25_ENABLED=true
#   BRAIN_WEB_ENABLED=true
#   BRAIN_L0_ENABLED=false   ← forzar que siempre haya contexto
#   BRAIN_WEB_MAX_RESULTS=3  ← 3 snippets caben en 6K tokens
#   RERANKING_ENABLED=false  ← sin GPU, reranking añade latencia sin ganancia
#   CRAG_EVALUATOR_ENABLED=false

# Para qwen2.5-coder:7b (7B local — modelo recomendado):
#   BRAIN_BM25_ENABLED=true
#   BRAIN_WEB_ENABLED=true
#   BRAIN_L0_ENABLED=true
#   BRAIN_WEB_MAX_RESULTS=5
#   RERANKING_ENABLED=true   ← con 28K context, reranking mejora calidad
#   CRAG_EVALUATOR_ENABLED=false

# Para Claude / GPT-4 (modelos API):
#   BRAIN_L0_ENABLED=true    ← L0 es aceptable con modelos de 180K+ contexto
#   BRAIN_WEB_MAX_RESULTS=10
#   RERANKING_ENABLED=true
#   CRAG_EVALUATOR_ENABLED=true   ← activar evaluador — latencia ~200ms, no 5s
#   CRAG_EVALUATOR_MODEL=claude-3-haiku-20240307  ← modelo rápido de evaluación
```

---

## F4.4 — Registrar el router en `app.py` (Día 3)

**Fichero:** `surfsense_backend/app/app.py` — añadir junto a los demás `include_router`

```python
# En app.py, cerca de la línea donde está:
# app.include_router(crud_router, prefix="/api/v1", tags=["crud"])

from app.routes.brain_routes import router as brain_router
app.include_router(brain_router)
# Monta en /api/v1/brain/* (el prefijo ya está definido dentro del router)
```

> **Nota**: el fichero de la app es `app.py`, no `main.py`. SurfSense no tiene `main.py`.

---

## F4.5 — Tests (Día 4-5)

**Fichero:** `tests/brain/test_router_f4.py`

```python
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.brain.router import BrainRouter
from app.brain.collections import BRAIN, KNOWLEDGE


class TestBrainRouterMultiTenant:

    def test_constructor_acepta_search_space_id(self):
        router = BrainRouter(search_space_id="space-ABC")
        assert router.search_space_id == "space-ABC"

    def test_search_incluye_filtro_search_space(self):
        """_search() debe incluir FieldCondition con search_space_id."""
        router = BrainRouter(search_space_id="space-XYZ")
        mock_qdrant = MagicMock()
        mock_qdrant.search.return_value = []
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1]*768):
            router._search(BRAIN, "test query", top_k=4)

        call_kwargs = mock_qdrant.search.call_args.kwargs
        assert "query_filter" in call_kwargs
        filter_obj = call_kwargs["query_filter"]
        keys = [c.key for c in filter_obj.must]
        assert "search_space_id" in keys

    def test_dos_spaces_no_comparten_resultados(self):
        """Búsquedas de distintos search_space_id deben usar filtros distintos."""
        router_a = BrainRouter(search_space_id="space-A")
        router_b = BrainRouter(search_space_id="space-B")
        mock_qdrant = MagicMock()
        mock_qdrant.search.return_value = []

        with patch("app.brain.router._embed_for_collection", return_value=[0.1]*768):
            router_a._qdrant = mock_qdrant
            router_b._qdrant = mock_qdrant
            router_a._search(BRAIN, "query", 4)
            router_b._search(BRAIN, "query", 4)

        calls = mock_qdrant.search.call_args_list
        filter_a = calls[0].kwargs["query_filter"].must[0].match.value
        filter_b = calls[1].kwargs["query_filter"].must[0].match.value
        assert filter_a == "space-A"
        assert filter_b == "space-B"


class TestBrainRouterCascade:

    def test_route_sin_resultados_devuelve_fallback_needed(self):
        """Si L1 score < L1_MIN_SCORE, fallback_needed=True en la respuesta."""
        router = BrainRouter(search_space_id="space-test")
        mock_qdrant = MagicMock()
        # L1 resultados con score bajo
        mock_point = MagicMock()
        mock_point.score = 0.20   # por debajo de L1_MIN_SCORE=0.60
        mock_point.payload = {"text": "poco relevante", "source": "doc"}
        mock_qdrant.search.return_value = [mock_point]
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1]*768):
            result = router.route("pregunta sin contexto relevante")

        assert result["fallback_needed"] is True

    def test_route_con_score_alto_no_necesita_fallback(self):
        """Si L1 score ≥ L1_MIN_SCORE, fallback_needed=False."""
        router = BrainRouter(search_space_id="space-test")
        mock_qdrant = MagicMock()
        mock_point = MagicMock()
        mock_point.score = 0.85
        mock_point.payload = {"text": "contenido muy relevante " * 20, "source": "doc"}
        mock_qdrant.search.return_value = [mock_point]
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1]*768):
            result = router.route("pregunta con contexto")

        assert result.get("fallback_needed") is not True
        assert result["level_used"] in [1, 2]


class TestBrainRouterContextBudget:

    def test_small_tier_limita_chunks(self):
        """Tier 'small' no debe incluir más de 2 chunks en el contexto."""
        from app.routes.brain_routes import _extract_chunks, _CHUNK_BUDGET
        mock_results = [
            MagicMock(payload={"text": "chunk " + str(i) * 100, "source": f"doc-{i}"})
            for i in range(10)
        ]
        budget = _CHUNK_BUDGET["small"]
        chunks = _extract_chunks(mock_results, budget=budget, max_chars=400)
        assert len(chunks) <= 2

    def test_small_tier_trunca_chars(self):
        """Tier 'small' trunca chunks a 400 chars."""
        from app.routes.brain_routes import _extract_chunks
        mock_result = MagicMock(payload={"text": "x" * 1000, "source": "doc"})
        chunks = _extract_chunks([mock_result], budget=2, max_chars=400)
        assert len(chunks[0]["text"]) <= 400


class TestBrainRouterHelpers:

    def test_needs_level2_con_trigger_de_detalle(self):
        router = BrainRouter()
        assert router.needs_level2("exactamente qué comando se usa", []) is True

    def test_is_code_question(self):
        router = BrainRouter()
        assert router.is_code_question("cómo se implementa la función load_data") is True
        assert router.is_code_question("qué es el Data Lakehouse") is False
```

---

## F4.6 — Agente Evaluador CRAG (multi-provider: Ollama / Claude / OpenAI)

### Por qué existe y cuándo tiene sentido

El router de F4 decide relevancia por **score threshold** — es una heurística ciega. Un chunk con score 0.72 puede no contener la respuesta real a la pregunta. El evaluador CRAG añade una dimensión cualitativa: en lugar de "¿supera el umbral numérico?", pregunta "¿este texto realmente responde mi pregunta?".

### El problema del one-size-fits-all

Cada proveedor LLM tiene capacidades radicalmente distintas. Un evaluador con un único set de prompts/config está **suboptimizado para todos**:

| Aspecto | Ollama ≤14B (local) | Claude (API) | OpenAI GPT-4 (API) |
|---------|---------------------|--------------|---------------------|
| **Latencia por eval** | 3-5s en CPU | ~200ms | ~300ms |
| **Fiabilidad JSON** | Baja — necesita parsing robusto con fallback regex | Alta — `response_format` no nativo pero XML excelente | Alta — `response_format=json_object` nativo |
| **Context window** | 6K-64K (práctico: 2K-8K para eval) | 200K | 128K |
| **Fragmento máximo** | 2000 chars (más = timeout) | 8000 chars | 6000 chars |
| **Prompt ideal** | Few-shot telegráfico, 10 palabras razonamiento | XML wrapping, razonamiento profundo | System instructions + JSON mode |
| **Batch evaluation** | NO (1 chunk por llamada) | SÍ (3 chunks en 1 llamada) | SÍ (3 chunks en 1 llamada) |
| **Chunks evaluables** | Max 3 (coste: +15s) | Max 8 (coste: +1.5s) | Max 6 (coste: +2s) |
| **Early exit** | Crítico (ahorra segundos) | Opcional (ahorra centésimas) | Opcional |
| **Coste** | Electricidad (fijo) | ~$0.003 por eval (3 input + 1 output) | ~$0.005 por eval |

**Conclusión:** el evaluador necesita **perfiles por provider** que se seleccionan automáticamente según `DEFAULT_PROVIDER` + `CRAG_EVALUATOR_MODEL`.

### Posición en la cascada (sin cambios)

```
L2 (Qdrant) → resultados con score ≥ threshold
                  ↓
              CRAG_EVALUATOR_ENABLED?
                  ├── false → responder directamente (default)
                  └── true  → Evaluador LLM (perfil según provider)
                                   ├── es_relevante: true  → responder
                                   └── es_relevante: false → continuar a L2.b
```

### Fichero: `surfsense_backend/app/brain/crag_evaluator.py` ← NUEVO

```python
# crag_evaluator.py — Agente Evaluador CRAG Multi-Provider
# Solo se instancia cuando CRAG_EVALUATOR_ENABLED=true
# LLMClient.generate() es SYNC — no async
#
# Tres perfiles de evaluación:
#   - ollama:  prompts cortos, parsing robusto, 1 chunk/llamada, early exit
#   - claude:  XML wrapping, razonamiento profundo, batch 3 chunks/llamada
#   - openai:  JSON mode nativo, razonamiento medio, batch 3 chunks/llamada

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Literal

from app.brain.llm_client import LLMClient, SYNTHESIS_MODEL, DEFAULT_PROVIDER

logger = logging.getLogger(__name__)

CRAG_EVALUATOR_ENABLED = os.getenv("CRAG_EVALUATOR_ENABLED", "false").lower() == "true"
CRAG_EVALUATOR_MODEL   = os.getenv("CRAG_EVALUATOR_MODEL", SYNTHESIS_MODEL)
CRAG_EVALUATOR_PROVIDER = os.getenv("CRAG_EVALUATOR_PROVIDER", DEFAULT_PROVIDER)


# ── Resultado de evaluación ──────────────────────────────────────────────────

@dataclass
class EvaluationResult:
    es_relevante: bool
    razonamiento: str       # siempre logueado — auditoría de decisiones
    source: str = ""
    confianza: float = 0.0  # 0.0-1.0 — solo significativo en Claude/OpenAI


# ══════════════════════════════════════════════════════════════════════════════
# PERFILES POR PROVIDER
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CRAGProfile:
    """
    Perfil de configuración del evaluador CRAG según el provider LLM.

    Cada provider tiene capacidades distintas que afectan:
    - Cómo se construye el prompt (formato, longitud, ejemplos)
    - Cuánto texto del fragmento puede ver (truncado)
    - Cuántos chunks evalúa por llamada (batch vs individual)
    - Cuántos chunks evalúa en total antes de rendirse
    - Cómo se parsea la respuesta (JSON nativo vs regex fallback)
    - Si aplica early exit (importante para latencia local)
    """
    name: str
    # ── Límites de texto ──────────────────────────────────────────────────
    max_fragment_chars: int     # máximo de chars del fragmento en el prompt
    max_question_chars: int     # máximo de chars de la pregunta
    # ── Estrategia de evaluación ─────────────────────────────────────────
    max_eval_chunks: int        # chunks totales a evaluar (override env)
    batch_size: int             # chunks por llamada LLM (1=individual, 3=batch)
    early_exit: bool            # si True, para al primer chunk relevante
    # ── Prompt ───────────────────────────────────────────────────────────
    system_prompt: str
    user_prompt_template: str   # template con {pregunta} y {fragmento} o {fragmentos}
    # ── Parsing ──────────────────────────────────────────────────────────
    needs_robust_parsing: bool  # True=usar _parse_eval_response con regex fallback
    # ── LLM params ───────────────────────────────────────────────────────
    temperature: float = 0.0
    extra_params: dict = field(default_factory=dict)  # e.g. response_format para OpenAI


# ── Perfil OLLAMA ────────────────────────────────────────────────────────────
# Diseño: prompts telegráficos, few-shot, parsing robusto, 1 chunk/llamada
# Target: qwen2.5-coder:3b/7b, llama3.1:8b, deepseek-r1:14b en CPU

_OLLAMA_SYSTEM = """Evalúa si el FRAGMENTO responde la PREGUNTA.
Responde SOLO con JSON. Sin texto antes ni después.

Ejemplo respuesta correcta cuando SÍ responde:
{"es_relevante": true, "razonamiento": "Explica directamente el proceso de autenticación JWT"}

Ejemplo respuesta correcta cuando NO responde:
{"es_relevante": false, "razonamiento": "Habla de otro tema, no menciona la pregunta"}

Reglas:
- "razonamiento": máximo 10 palabras
- En duda: false"""

_OLLAMA_USER = """PREGUNTA: {pregunta}

FRAGMENTO (puede estar truncado):
---
{fragmento}
---

¿El fragmento responde la pregunta? JSON:"""

PROFILE_OLLAMA = CRAGProfile(
    name="ollama",
    max_fragment_chars=2000,
    max_question_chars=500,
    max_eval_chunks=int(os.getenv("CRAG_MAX_EVAL_CHUNKS", "3")),
    batch_size=1,               # 1 chunk por llamada — contexto limitado
    early_exit=True,            # crítico: ahorra 3-5s por chunk evitado
    system_prompt=_OLLAMA_SYSTEM,
    user_prompt_template=_OLLAMA_USER,
    needs_robust_parsing=True,  # regex fallback imprescindible
    temperature=0.0,
)


# ── Perfil CLAUDE ────────────────────────────────────────────────────────────
# Diseño: XML wrapping (Claude es excelente con XML), razonamiento profundo,
#         batch 3 chunks/llamada, confianza numérica, sin few-shot (innecesario)
# Target: claude-3-haiku, claude-sonnet, claude-opus via Anthropic o Azure

_CLAUDE_SYSTEM = """Eres un evaluador experto de relevancia documental para un sistema RAG.

Tu tarea: dado un conjunto de fragmentos recuperados de una base de conocimiento,
determinar cuáles responden la pregunta del usuario.

Para CADA fragmento, evalúa:
1. ¿Contiene información que responde directa o parcialmente la pregunta?
2. ¿La información es específica y factual, no genérica?
3. ¿Cuál es tu nivel de confianza? (0.0 = irrelevante, 1.0 = respuesta completa)

Responde con un array JSON. Cada elemento:
{"source": "identificador", "es_relevante": true|false, "confianza": 0.0-1.0, "razonamiento": "análisis en 1-3 frases"}

En caso de duda: false con confianza < 0.3 (es más seguro buscar más contexto)."""

_CLAUDE_USER = """<pregunta>
{pregunta}
</pregunta>

<fragmentos_recuperados>
{fragmentos}
</fragmentos_recuperados>

Evalúa la relevancia de cada fragmento respecto a la pregunta. Responde con JSON array:"""

PROFILE_CLAUDE = CRAGProfile(
    name="claude",
    max_fragment_chars=8000,    # Claude maneja fragmentos largos sin problema
    max_question_chars=2000,    # preguntas complejas bienvenidas
    max_eval_chunks=int(os.getenv("CRAG_MAX_EVAL_CHUNKS", "8")),
    batch_size=3,               # evalúa 3 chunks en 1 sola llamada
    early_exit=False,           # no merece la pena — latencia ~200ms por llamada
    system_prompt=_CLAUDE_SYSTEM,
    user_prompt_template=_CLAUDE_USER,
    needs_robust_parsing=False, # Claude es fiable con JSON
    temperature=0.0,
)


# ── Perfil OPENAI ────────────────────────────────────────────────────────────
# Diseño: JSON mode nativo (response_format), razonamiento medio,
#         batch 3 chunks/llamada, sin XML (OpenAI prefiere instrucciones directas)
# Target: gpt-4o, gpt-4o-mini, gpt-4-turbo via OpenAI o Azure OpenAI

_OPENAI_SYSTEM = """You are a document relevance evaluator for a RAG system.

Given retrieved fragments and a user question, determine which fragments
contain information sufficient to answer the question.

For EACH fragment, respond with:
- source: the fragment identifier
- es_relevante: true if it answers the question, false otherwise
- confianza: 0.0 to 1.0 confidence score
- razonamiento: brief analysis (1-2 sentences, in the user's language)

When uncertain: es_relevante=false, confianza < 0.3

Respond with a JSON object: {"evaluaciones": [...]}"""

_OPENAI_USER = """Question: {pregunta}

Retrieved fragments:
{fragmentos}

Evaluate each fragment's relevance. JSON response:"""

PROFILE_OPENAI = CRAGProfile(
    name="openai",
    max_fragment_chars=6000,
    max_question_chars=1500,
    max_eval_chunks=int(os.getenv("CRAG_MAX_EVAL_CHUNKS", "6")),
    batch_size=3,
    early_exit=False,
    system_prompt=_OPENAI_SYSTEM,
    user_prompt_template=_OPENAI_USER,
    needs_robust_parsing=False,
    temperature=0.0,
    extra_params={"response_format": {"type": "json_object"}},  # JSON mode nativo
)


# ── Selección automática de perfil ───────────────────────────────────────────

_PROFILE_MAP: dict[str, CRAGProfile] = {
    "ollama":  PROFILE_OLLAMA,
    "claude":  PROFILE_CLAUDE,
    "openai":  PROFILE_OPENAI,
    "hybrid":  PROFILE_CLAUDE,  # hybrid usa Claude para operaciones largas
    "azure":   PROFILE_OPENAI,  # Azure OpenAI usa el mismo perfil que OpenAI
}


def get_crag_profile(provider: str | None = None) -> CRAGProfile:
    """
    Selecciona el perfil CRAG según el provider.
    Autodetecta desde CRAG_EVALUATOR_PROVIDER si no se especifica.
    Fallback seguro a ollama si el provider es desconocido.
    """
    p = (provider or CRAG_EVALUATOR_PROVIDER).lower()

    # Detectar provider desde el nombre del modelo si es "auto"
    if p not in _PROFILE_MAP:
        model = CRAG_EVALUATOR_MODEL.lower()
        if "claude" in model:
            p = "claude"
        elif "gpt" in model or "o1" in model or "o3" in model:
            p = "openai"
        else:
            p = "ollama"
        logger.info("[CRAG] Provider %r no reconocido → autodetectado como %r desde modelo %r",
                     provider, p, CRAG_EVALUATOR_MODEL)

    profile = _PROFILE_MAP[p]
    logger.debug("[CRAG] Perfil seleccionado: %s (provider=%s, max_chunks=%d, batch=%d)",
                 profile.name, p, profile.max_eval_chunks, profile.batch_size)
    return profile


# ══════════════════════════════════════════════════════════════════════════════
# PARSING (robusto para Ollama, directo para API)
# ══════════════════════════════════════════════════════════════════════════════

# Regex fallback para rescatar respuesta de modelos que envuelven JSON
_JSON_EXTRACT_RE = re.compile(r'\{[^{}]*"es_relevante"\s*:\s*(true|false)[^{}]*\}', re.IGNORECASE)
_RELEVANTE_FALLBACK_RE = re.compile(r'"?es_relevante"?\s*:\s*(true|false)', re.IGNORECASE)


def _parse_eval_response(raw: str, robust: bool = True) -> dict | list[dict]:
    """
    Parsea la respuesta del evaluador.

    Si robust=True (Ollama): 3 niveles de tolerancia con regex fallback.
    Si robust=False (Claude/OpenAI): json.loads directo, falla rápido.

    Para respuestas batch (Claude/OpenAI), retorna list[dict].
    Para respuestas individuales (Ollama), retorna dict.

    Lanza ValueError si no se puede parsear.
    """
    cleaned = raw.strip()

    # Quitar markdown fences (```json ... ```) — todos los providers pueden hacerlo
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```\w*\s*', '', cleaned)
        cleaned = re.sub(r'\s*```\s*$', '', cleaned)

    # Intento 1: JSON directo
    try:
        parsed = json.loads(cleaned)
        # Batch response: {"evaluaciones": [...]} o directamente [...]
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            if "evaluaciones" in parsed:
                return parsed["evaluaciones"]
            if "es_relevante" in parsed:
                return parsed
        # Si parsed es un dict sin claves conocidas, seguir intentando
    except (json.JSONDecodeError, TypeError):
        pass

    if not robust:
        raise ValueError(f"JSON inválido del provider API: {raw[:300]}")

    # ── Solo para Ollama: fallbacks progresivos ──────────────────────────

    # Intento 2: regex extrae primer {...} con "es_relevante"
    match = _JSON_EXTRACT_RE.search(raw)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Intento 3: solo extraer el booleano
    match = _RELEVANTE_FALLBACK_RE.search(raw)
    if match:
        value = match.group(1).lower() == "true"
        return {"es_relevante": value, "razonamiento": "(extraído por regex fallback)"}

    raise ValueError(f"No se pudo extraer es_relevante de: {raw[:200]}")


# ══════════════════════════════════════════════════════════════════════════════
# EVALUACIÓN: individual (Ollama) y batch (Claude/OpenAI)
# ══════════════════════════════════════════════════════════════════════════════

def _format_batch_fragments(chunks: list[dict], max_chars: int) -> str:
    """Formatea N chunks para evaluación batch (Claude/OpenAI)."""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        text = (chunk.get("text", "") or "")[:max_chars]
        source = chunk.get("source", f"fragmento-{i}")
        # Escapar separadores
        text_safe = text.replace("---", "—-—")
        parts.append(f"[Fragmento {i}: source={source}]\n{text_safe}")
    return "\n\n---\n\n".join(parts)


def _evaluar_individual(
    pregunta: str,
    fragmento: str,
    source: str,
    profile: CRAGProfile,
    client: LLMClient,
) -> EvaluationResult:
    """
    Evalúa UN chunk (modo Ollama). 1 llamada LLM por chunk.
    Siempre retorna EvaluationResult, nunca lanza excepción.
    """
    fragmento_safe = fragmento.strip()[:profile.max_fragment_chars].replace("---", "—-—")
    prompt = profile.user_prompt_template.format(
        pregunta=pregunta.strip()[:profile.max_question_chars],
        fragmento=fragmento_safe,
    )
    try:
        raw = client.generate(
            prompt=prompt,
            system=profile.system_prompt,
            provider=CRAG_EVALUATOR_PROVIDER,
            model=CRAG_EVALUATOR_MODEL,
            temperature=profile.temperature,
        )
        parsed = _parse_eval_response(raw, robust=profile.needs_robust_parsing)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}

        result = EvaluationResult(
            es_relevante=bool(parsed.get("es_relevante", False)),
            razonamiento=str(parsed.get("razonamiento", "sin razonamiento"))[:200],
            source=source,
            confianza=float(parsed.get("confianza", 0.0)),
        )
        logger.info(
            "[CRAG:%s] source=%r es_relevante=%s confianza=%.2f razonamiento=%r",
            profile.name, source, result.es_relevante, result.confianza, result.razonamiento,
        )
        return result

    except (ValueError, KeyError) as exc:
        logger.warning("[CRAG:%s] Parse fallido source=%r: %s → fallback False",
                       profile.name, source, exc)
        return EvaluationResult(es_relevante=False,
                                razonamiento=f"Parse fallido: {exc}", source=source)
    except Exception as exc:
        logger.error("[CRAG:%s] Error source=%r: %s → fallback False",
                     profile.name, source, exc)
        return EvaluationResult(es_relevante=False,
                                razonamiento=f"Error: {type(exc).__name__}", source=source)


def _evaluar_batch(
    pregunta: str,
    chunks: list[dict],
    profile: CRAGProfile,
    client: LLMClient,
) -> list[EvaluationResult]:
    """
    Evalúa N chunks en UNA llamada LLM (modo Claude/OpenAI).
    Más eficiente: 3 chunks en 1 llamada vs 3 llamadas separadas.
    Retorna lista de EvaluationResult, nunca lanza excepción.
    """
    fragmentos_str = _format_batch_fragments(chunks, profile.max_fragment_chars)
    prompt = profile.user_prompt_template.format(
        pregunta=pregunta.strip()[:profile.max_question_chars],
        fragmentos=fragmentos_str,
    )
    try:
        raw = client.generate(
            prompt=prompt,
            system=profile.system_prompt,
            provider=CRAG_EVALUATOR_PROVIDER,
            model=CRAG_EVALUATOR_MODEL,
            temperature=profile.temperature,
        )
        parsed = _parse_eval_response(raw, robust=False)

        # Normalizar: si es un dict individual, convertir a lista
        if isinstance(parsed, dict):
            parsed = [parsed]

        results = []
        for i, chunk in enumerate(chunks):
            # Emparejar resultado con chunk por posición
            eval_data = parsed[i] if i < len(parsed) else {}
            source = chunk.get("source", f"chunk-{i}")
            results.append(EvaluationResult(
                es_relevante=bool(eval_data.get("es_relevante", False)),
                razonamiento=str(eval_data.get("razonamiento", "sin evaluación"))[:200],
                source=source,
                confianza=float(eval_data.get("confianza", 0.0)),
            ))
            logger.info(
                "[CRAG:%s:batch] source=%r es_relevante=%s confianza=%.2f",
                profile.name, source, results[-1].es_relevante, results[-1].confianza,
            )

        return results

    except (ValueError, json.JSONDecodeError) as exc:
        # Batch falló — fallback: evaluar cada chunk individualmente
        logger.warning(
            "[CRAG:%s] Batch parse falló (%s) — fallback a evaluación individual",
            profile.name, exc,
        )
        return [
            _evaluar_individual(pregunta, c.get("text", ""), c.get("source", ""), profile, client)
            for c in chunks
        ]
    except Exception as exc:
        logger.error("[CRAG:%s] Batch error (%s) → todos False", profile.name, exc)
        return [
            EvaluationResult(
                es_relevante=False,
                razonamiento=f"Batch error: {type(exc).__name__}",
                source=c.get("source", ""),
            )
            for c in chunks
        ]


# ══════════════════════════════════════════════════════════════════════════════
# API PÚBLICA
# ══════════════════════════════════════════════════════════════════════════════

def evaluar_chunks(
    pregunta: str,
    chunks: list[dict],
    provider: str | None = None,
) -> tuple[bool, list[EvaluationResult]]:
    """
    Evalúa chunks usando el perfil CRAG apropiado para el provider.

    Estrategia según provider:
    - Ollama:  1 chunk/llamada, early exit al primer relevante, max 3 chunks
    - Claude:  3 chunks/llamada batch, sin early exit, max 8 chunks
    - OpenAI:  3 chunks/llamada batch, sin early exit, max 6 chunks

    Returns:
        (hay_al_menos_uno_relevante, lista_de_evaluaciones)
    """
    profile = get_crag_profile(provider)
    client = LLMClient()
    chunks_to_eval = chunks[:profile.max_eval_chunks]

    logger.info(
        "[CRAG] Evaluando %d chunks (profile=%s, batch=%d, early_exit=%s, provider=%s)",
        len(chunks_to_eval), profile.name, profile.batch_size,
        profile.early_exit, CRAG_EVALUATOR_PROVIDER,
    )

    evaluaciones: list[EvaluationResult] = []

    if profile.batch_size > 1:
        # ── Modo batch (Claude/OpenAI) ────────────────────────────────────
        # Evaluar en lotes de batch_size
        for i in range(0, len(chunks_to_eval), profile.batch_size):
            batch = chunks_to_eval[i : i + profile.batch_size]
            batch_results = _evaluar_batch(pregunta, batch, profile, client)
            evaluaciones.extend(batch_results)
    else:
        # ── Modo individual (Ollama) ──────────────────────────────────────
        for chunk in chunks_to_eval:
            result = _evaluar_individual(
                pregunta=pregunta,
                fragmento=chunk.get("text", ""),
                source=chunk.get("source", ""),
                profile=profile,
                client=client,
            )
            evaluaciones.append(result)
            # Early exit: para al primer relevante (ahorra 3-5s por chunk)
            if profile.early_exit and result.es_relevante:
                logger.info("[CRAG:%s] Early exit — chunk relevante encontrado", profile.name)
                return True, evaluaciones

    hay_relevante = any(ev.es_relevante for ev in evaluaciones)
    return hay_relevante, evaluaciones


# ── Compatibilidad: evaluar_chunk individual (usado en tests y F8) ───────────

def evaluar_chunk(pregunta: str, fragmento: str, source: str = "") -> EvaluationResult:
    """
    Evalúa un único chunk. Wrapper de conveniencia.
    Usa el perfil autodetectado del provider configurado.
    """
    profile = get_crag_profile()
    client = LLMClient()
    return _evaluar_individual(pregunta, fragmento, source, profile, client)
```

### Integración en `brain_routes.py` — bloque condicional entre L2 y L2.b

```python
# brain_routes.py — añadir estas importaciones al inicio del fichero:
from app.brain.crag_evaluator import (
    CRAG_EVALUATOR_ENABLED, evaluar_chunks, get_crag_profile,
)

# En brain_query(), DESPUÉS del bloque L1+L2 que devuelve resultados,
# ANTES de hacer la llamada LLM — insertar la evaluación condicional:

    if not route_result.get("fallback_needed") and route_result.get("results"):
        level  = route_result["level_used"]
        chunks = _extract_chunks(route_result["results"], chunk_budget, chunk_max_chars)
        sources = route_result["sources_consulted"]
        label   = "🧠 Brain" if level == 1 else "📚 Knowledge"

        # ── CRAG Evaluador (solo si está activado) ──────────────────────
        if CRAG_EVALUATOR_ENABLED and chunks:
            crag_profile = get_crag_profile()
            logger.info(
                "[CRAG] Activado con perfil=%s (batch=%d, max_chunks=%d, early_exit=%s)",
                crag_profile.name, crag_profile.batch_size,
                crag_profile.max_eval_chunks, crag_profile.early_exit,
            )

            hay_relevante, evaluaciones = evaluar_chunks(req.question, chunks)
            razonamientos = [
                f"{ev.source}: {ev.es_relevante} (conf={ev.confianza:.2f}) — {ev.razonamiento}"
                for ev in evaluaciones
            ]
            logger.info("[CRAG] Evaluaciones: %s", razonamientos)

            if not hay_relevante:
                # Agente decidió que ningún chunk responde la pregunta
                # → continuar cascada hacia L2.b
                logger.info(
                    "[CRAG] Ningún chunk relevante según el evaluador %s "
                    "→ continuando a L2.b/BM25", crag_profile.name,
                )
                # Caer directamente al bloque L2.b (no retornar aquí)
                pass
            else:
                # Agente confirmó al menos un chunk relevante → responder
                # Para Claude/OpenAI: usar confianza para filtrar mejor
                if crag_profile.name in ("claude", "openai"):
                    # Solo incluir chunks con confianza >= 0.5
                    chunks_ok = [
                        chunks[i] for i, ev in enumerate(evaluaciones)
                        if ev.es_relevante and ev.confianza >= 0.5
                    ]
                else:
                    chunks_ok = [
                        chunks[i] for i, ev in enumerate(evaluaciones)
                        if ev.es_relevante
                    ]

                context = _build_context(chunks_ok or chunks)
                answer = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
                return BrainQueryResponse(
                    answer=answer, level_used=level, level_label=label,
                    sources=sources, context_chunks=len(chunks_ok or chunks),
                    model_tier=tier
                )
        else:
            # Sin CRAG: comportamiento original F4 — responder directamente
            context = _build_context(chunks)
            answer = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
            return BrainQueryResponse(answer=answer, level_used=level, level_label=label,
                                       sources=sources, context_chunks=len(chunks), model_tier=tier)
```

### Control desde UI: Admin Brain → ver [F6.10](./F6-ui-integracion.md#f610--brainadminpagetsx-admin-brain-día-11-13)

La configuración del evaluador CRAG (toggle, provider, modelo, chunks) se gestiona desde el **tab 🤖 LLM** del Admin Brain. El diseño completo del panel, comportamiento del toggle, warnings contextuales y API calls están documentados en **F6.10**. F4 solo define la lógica backend (`crag_evaluator.py`); la UI pertenece a F6.

### Tests del evaluador multi-provider

```python
# tests/brain/test_crag_evaluator.py

import pytest
from unittest.mock import patch, MagicMock
from app.brain.crag_evaluator import (
    evaluar_chunk, evaluar_chunks, _parse_eval_response,
    get_crag_profile, EvaluationResult,
    PROFILE_OLLAMA, PROFILE_CLAUDE, PROFILE_OPENAI,
)


# ══════════════════════════════════════════════════════════════════════════════
# Tests del parser (robusto vs directo)
# ══════════════════════════════════════════════════════════════════════════════

class TestParseEvalResponse:
    """Tests para el parser robusto de respuestas LLM."""

    def test_json_limpio(self):
        """Caso ideal: JSON puro sin basura."""
        parsed = _parse_eval_response('{"es_relevante": true, "razonamiento": "Responde directamente"}')
        assert parsed["es_relevante"] is True

    def test_json_envuelto_en_markdown_fences(self):
        """Modelos qwen frecuentemente envuelven JSON en ```json ... ```."""
        raw = '```json\n{"es_relevante": false, "razonamiento": "No relacionado"}\n```'
        parsed = _parse_eval_response(raw)
        assert parsed["es_relevante"] is False

    def test_json_con_texto_extra_antes_y_despues(self):
        """Modelo emite texto + JSON + texto (llama3.1 hace esto)."""
        raw = 'Aquí está mi evaluación:\n{"es_relevante": true, "razonamiento": "OK"}\nEspero que ayude.'
        parsed = _parse_eval_response(raw)
        assert parsed["es_relevante"] is True

    def test_solo_booleano_extraible(self):
        """Último recurso: modelo emite texto libre pero menciona es_relevante: false."""
        raw = 'El fragmento no es relevante. es_relevante: false porque no contiene la respuesta.'
        parsed = _parse_eval_response(raw)
        assert parsed["es_relevante"] is False
        assert "regex" in parsed["razonamiento"]

    def test_basura_total_con_robust_lanza_valueerror(self):
        """Si no se puede extraer nada con robust=True → ValueError."""
        with pytest.raises(ValueError):
            _parse_eval_response("completamente irrelevante sin json ni keywords", robust=True)

    def test_basura_con_robust_false_falla_rapido(self):
        """Sin robust (Claude/OpenAI), no intenta regex — falla directamente."""
        with pytest.raises(ValueError, match="JSON inválido del provider API"):
            _parse_eval_response("texto sin json", robust=False)

    def test_json_con_comillas_simples(self):
        """Algunos modelos usan comillas simples — regex lo rescata."""
        raw = "{'es_relevante': true, 'razonamiento': 'Responde'}"
        parsed = _parse_eval_response(raw)
        assert parsed["es_relevante"] is True

    def test_batch_response_array(self):
        """Claude/OpenAI devuelven array JSON para batch evaluation."""
        raw = '[{"es_relevante": true, "razonamiento": "OK", "source": "doc-1"}, ' \
              '{"es_relevante": false, "razonamiento": "No", "source": "doc-2"}]'
        parsed = _parse_eval_response(raw, robust=False)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["es_relevante"] is True
        assert parsed[1]["es_relevante"] is False

    def test_batch_response_wrapped(self):
        """OpenAI con JSON mode envuelve en {"evaluaciones": [...]}."""
        raw = '{"evaluaciones": [{"es_relevante": true, "razonamiento": "OK"}]}'
        parsed = _parse_eval_response(raw, robust=False)
        assert isinstance(parsed, list)
        assert parsed[0]["es_relevante"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Tests de selección de perfil
# ══════════════════════════════════════════════════════════════════════════════

class TestProfileSelection:

    def test_provider_ollama(self):
        profile = get_crag_profile("ollama")
        assert profile.name == "ollama"
        assert profile.batch_size == 1
        assert profile.early_exit is True
        assert profile.needs_robust_parsing is True

    def test_provider_claude(self):
        profile = get_crag_profile("claude")
        assert profile.name == "claude"
        assert profile.batch_size == 3
        assert profile.early_exit is False
        assert profile.max_fragment_chars == 8000

    def test_provider_openai(self):
        profile = get_crag_profile("openai")
        assert profile.name == "openai"
        assert profile.batch_size == 3
        assert "response_format" in profile.extra_params

    def test_provider_hybrid_maps_to_claude(self):
        profile = get_crag_profile("hybrid")
        assert profile.name == "claude"

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_MODEL", "claude-3-haiku-20240307")
    def test_autodetect_from_model_name_claude(self):
        """Provider desconocido → autodetecta desde nombre de modelo."""
        profile = get_crag_profile("custom_provider")
        assert profile.name == "claude"

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_MODEL", "gpt-4o-mini")
    def test_autodetect_from_model_name_openai(self):
        profile = get_crag_profile("litellm_custom")
        assert profile.name == "openai"

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_MODEL", "qwen2.5-coder:7b")
    def test_autodetect_unknown_defaults_ollama(self):
        profile = get_crag_profile("unknown_provider")
        assert profile.name == "ollama"

    def test_ollama_max_chunks_less_than_claude(self):
        """Ollama evalúa menos chunks que Claude (latencia vs calidad)."""
        assert PROFILE_OLLAMA.max_eval_chunks <= PROFILE_CLAUDE.max_eval_chunks

    def test_ollama_fragment_shorter_than_claude(self):
        """Ollama trunca fragmentos más que Claude (context window)."""
        assert PROFILE_OLLAMA.max_fragment_chars < PROFILE_CLAUDE.max_fragment_chars


# ══════════════════════════════════════════════════════════════════════════════
# Tests de evaluación individual (Ollama)
# ══════════════════════════════════════════════════════════════════════════════

class TestCRAGEvaluadorOllama:

    def test_json_valido_relevante(self):
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = \
                '{"es_relevante": true, "razonamiento": "Explica JWT directamente"}'
            result = evaluar_chunk("¿Qué es JWT?", "JWT es un token de autenticación...")
        assert result.es_relevante is True
        assert "JWT" in result.razonamiento

    def test_json_envuelto_markdown_rescatado(self):
        """JSON envuelto en ```json → rescatado, NO fallback False."""
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = \
                '```json\n{"es_relevante": true, "razonamiento": "Relevante"}\n```'
            result = evaluar_chunk("pregunta", "fragmento relevante")
        assert result.es_relevante is True

    def test_basura_total_fallback_false(self):
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = "no sé qué decir, hola mundo"
            result = evaluar_chunk("pregunta", "fragmento")
        assert result.es_relevante is False
        assert "Parse fallido" in result.razonamiento

    def test_llm_error_fallback_false(self):
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.side_effect = ConnectionError("Ollama caído")
            result = evaluar_chunk("pregunta", "fragmento")
        assert result.es_relevante is False

    def test_razonamiento_truncado_a_200_chars(self):
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = \
                '{"es_relevante": false, "razonamiento": "' + 'x' * 500 + '"}'
            result = evaluar_chunk("pregunta", "fragmento")
        assert len(result.razonamiento) <= 200

    def test_fragmento_con_separadores_no_rompe_prompt(self):
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = \
                '{"es_relevante": true, "razonamiento": "OK"}'
            result = evaluar_chunk("pregunta", "texto con --- separador --- aquí")
        assert result.es_relevante is True


# ══════════════════════════════════════════════════════════════════════════════
# Tests de evaluación por lotes (Claude/OpenAI)
# ══════════════════════════════════════════════════════════════════════════════

class TestCRAGEvaluadorBatch:

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_PROVIDER", "claude")
    def test_claude_batch_3_chunks_1_llamada(self):
        """Claude evalúa 3 chunks en 1 sola llamada LLM."""
        batch_response = json.dumps([
            {"es_relevante": True, "confianza": 0.9, "razonamiento": "Responde directamente", "source": "doc-a"},
            {"es_relevante": False, "confianza": 0.1, "razonamiento": "No relacionado", "source": "doc-b"},
            {"es_relevante": True, "confianza": 0.7, "razonamiento": "Parcialmente útil", "source": "doc-c"},
        ])
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = batch_response
            chunks = [
                {"text": f"chunk {i}", "source": f"doc-{chr(97+i)}"}
                for i in range(3)
            ]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks, provider="claude")

        assert hay is True
        assert len(evaluaciones) == 3
        assert evaluaciones[0].es_relevante is True
        assert evaluaciones[0].confianza == 0.9
        assert evaluaciones[1].es_relevante is False
        # Solo 1 llamada LLM para 3 chunks
        assert MockLLM.return_value.generate.call_count == 1

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_PROVIDER", "claude")
    def test_claude_batch_fallback_a_individual(self):
        """Si batch falla, Claude cae a evaluación individual."""
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.side_effect = [
                "respuesta JSON corrupta del batch",  # batch falla
                '{"es_relevante": true, "razonamiento": "OK"}',   # individual 1
                '{"es_relevante": false, "razonamiento": "No"}',  # individual 2
            ]
            chunks = [
                {"text": "chunk 1", "source": "doc-a"},
                {"text": "chunk 2", "source": "doc-b"},
            ]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks, provider="claude")

        assert hay is True
        assert len(evaluaciones) == 2
        # 1 batch fallido + 2 individuales = 3 llamadas
        assert MockLLM.return_value.generate.call_count == 3

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_PROVIDER", "openai")
    def test_openai_wrapped_evaluaciones(self):
        """OpenAI con JSON mode devuelve {"evaluaciones": [...]}."""
        response = json.dumps({
            "evaluaciones": [
                {"es_relevante": True, "confianza": 0.8, "razonamiento": "OK"},
            ]
        })
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = response
            chunks = [{"text": "chunk 1", "source": "doc-a"}]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks, provider="openai")

        assert hay is True
        assert evaluaciones[0].confianza == 0.8


# ══════════════════════════════════════════════════════════════════════════════
# Tests de estrategia (early exit vs no early exit)
# ══════════════════════════════════════════════════════════════════════════════

class TestCRAGEstrategia:

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_PROVIDER", "ollama")
    def test_ollama_early_exit(self):
        """Ollama para al primer chunk relevante (ahorra latencia)."""
        responses = [
            '{"es_relevante": true, "razonamiento": "Relevante"}',
            '{"es_relevante": true, "razonamiento": "También relevante"}',
        ]
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.side_effect = responses
            chunks = [
                {"text": "chunk 1", "source": "doc-a"},
                {"text": "chunk 2", "source": "doc-b"},
            ]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks, provider="ollama")

        assert hay is True
        assert len(evaluaciones) == 1   # early exit — solo evaluó 1
        assert MockLLM.return_value.generate.call_count == 1

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_PROVIDER", "claude")
    def test_claude_no_early_exit_evalua_todo(self):
        """Claude evalúa todos los chunks (batch, sin early exit)."""
        batch_response = json.dumps([
            {"es_relevante": True, "confianza": 0.9, "razonamiento": "R1"},
            {"es_relevante": True, "confianza": 0.8, "razonamiento": "R2"},
            {"es_relevante": False, "confianza": 0.1, "razonamiento": "R3"},
        ])
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = batch_response
            chunks = [{"text": f"chunk {i}", "source": f"doc-{i}"} for i in range(3)]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks, provider="claude")

        assert hay is True
        assert len(evaluaciones) == 3   # evaluó TODOS, no paró al primero

    @patch("app.brain.crag_evaluator.CRAG_EVALUATOR_PROVIDER", "ollama")
    def test_ollama_todos_irrelevantes(self):
        """Si todos irrelevantes en Ollama → evalúa todos (no hay early exit que aplicar)."""
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = \
                '{"es_relevante": false, "razonamiento": "No responde"}'
            chunks = [{"text": f"chunk {i}", "source": f"doc-{i}"} for i in range(3)]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks, provider="ollama")

        assert hay is False
        assert len(evaluaciones) == 3

# Necesario para test_openai_wrapped_evaluaciones
import json
```

---

### F4.0 — Prerrequisitos
- [ ] `search_space_id` ya presente en payloads Qdrant (brain, knowledge, code) — resultado de F2
- [ ] `ChucksHybridSearchRetriever.full_text_search()` funcional con `search_space_id` int
- [ ] `web_search_service` disponible en el contenedor backend

### F4.1 — `router.py`
- [ ] Constructor `BrainRouter(search_space_id: str = "")` — acepta multi-tenant
- [ ] `_search()` incluye `FieldCondition(key="search_space_id")` en filter
- [ ] `_search_filtered()` incluye `search_space_id` como condición adicional
- [ ] `_build_response()` incluye campo `fallback_needed: bool`
- [ ] `route()` devuelve `fallback_needed=True` cuando L1+L2 no tienen resultados relevantes
- [ ] Tests `TestBrainRouterMultiTenant` pasando

### F4.2 — `brain_routes.py`
- [ ] `POST /api/v1/brain/query` implementado con cascada L1→L2→BM25→Web→L0
- [ ] `BrainQueryResponse` incluye `level_used` (0-4), `level_label`, `model_tier`
- [ ] `_CHUNK_BUDGET` y `_CHUNK_MAX_CHARS` por tier aplicados correctamente
- [ ] `BRAIN_L0_ENABLED=false` devuelve 404 en lugar de LLM libre
- [ ] BM25 usa `ChucksHybridSearchRetriever` — no reinventa la rueda
- [ ] Web usa `web_search_service` — no reinventa la rueda
- [ ] `GET /api/v1/brain/passport/{source}` — `writer.read()` devuelve `None` (no excepción)
- [ ] `DELETE /api/v1/brain/document/{source}` requiere `search_space_id` como query param

### F4.3 — Variables de entorno
- [ ] `BRAIN_BM25_ENABLED`, `BRAIN_WEB_ENABLED`, `BRAIN_L0_ENABLED` en `.env.dev`
- [ ] `BRAIN_WEB_MAX_RESULTS=3` para modelos 3B, `=5` para 7B, `=10` para Claude
- [ ] `CRAG_EVALUATOR_ENABLED=false` por defecto en `.env.dev` y `.env.example`
- [ ] Documentar configuración recomendada por tier en `.env.example` — incluyendo CRAG para Claude

### F4.4 — Registro en `app.py`
- [ ] `from app.routes.brain_routes import router as brain_router`
- [ ] `app.include_router(brain_router)` añadido en `app.py` (no `main.py`)
- [ ] Ruta accesible: `curl /api/v1/brain/query` responde 422 (schema validation) — no 404

### F4.5 — Tests
- [ ] `TestBrainRouterMultiTenant` — filtro `search_space_id` verificado en llamadas Qdrant
- [ ] `TestBrainRouterCascade` — `fallback_needed` correcto según score L1
- [ ] `TestBrainRouterContextBudget` — tier small ≤2 chunks, ≤400 chars
- [ ] Test de integración: `BRAIN_BM25_ENABLED=false, BRAIN_WEB_ENABLED=false` → L0 directo
- [ ] `_rewrite_query_for_web()` — fallback a truncado si LLM falla

### F4.6 — Agente Evaluador CRAG (multi-provider)
- [ ] `crag_evaluator.py` creado con `CRAGProfile` dataclass + 3 perfiles (ollama/claude/openai)
- [ ] `get_crag_profile()` autodetecta provider desde `CRAG_EVALUATOR_PROVIDER` o nombre del modelo
- [ ] `evaluar_chunk()` y `evaluar_chunks()` SYNC — API pública estable
- [ ] `_evaluar_individual()` — modo Ollama: 1 chunk/llamada, parsing robusto, early exit
- [ ] `_evaluar_batch()` — modo Claude/OpenAI: N chunks/llamada, fallback a individual si falla
- [ ] `_parse_eval_response(raw, robust)` — 3 niveles con regex para Ollama, directo para APIs
- [ ] `CRAG_EVALUATOR_ENABLED=false` en `.env.dev` y `.env.example`
- [ ] `CRAG_EVALUATOR_PROVIDER` env var documentada con valores: ollama/claude/openai
- [ ] Integración en `brain_routes.py` — import de `get_crag_profile`, log del perfil seleccionado
- [ ] Claude/OpenAI: filtrado por confianza >= 0.5 en chunks relevantes
- [ ] Si `CRAG_EVALUATOR_ENABLED=false` → comportamiento idéntico al F4 sin CRAG
- [ ] Si `CRAG_EVALUATOR_ENABLED=true` y chunks irrelevantes → continúa a L2.b
- [ ] Log auditado de `razonamiento` + `confianza` en cada decisión del evaluador
- [ ] Toggle y selector de provider en Admin Brain → documentado en [F6.10](./F6-ui-integracion.md)
- [ ] Tests: `TestParseEvalResponse` (9 cases), `TestProfileSelection` (9 cases),
      `TestCRAGEvaluadorOllama` (6 cases), `TestCRAGEvaluadorBatch` (3 cases),
      `TestCRAGEstrategia` (3 cases) — total 30 tests

### Criterio de aceptación global F4
- [ ] Query sobre documento indexado → nivel 1 ó 2, `context_chunks ≥ 1`
- [ ] Query sin documentos relevantes + `BRAIN_BM25_ENABLED=true` → nivel 3 (BM25)
- [ ] Query completamente fuera de corpus + SearXNG activo → nivel 4 (Web)
- [ ] La query web usa `_rewrite_query_for_web()` — no la pregunta original cruda
- [ ] `BRAIN_L0_ENABLED=false` + sin contexto → 404, no alucinación
- [ ] Con `qwen2.5-coder:3b`: chunks ≤2, chars ≤400 — no timeout por contexto largo
- [ ] `CRAG_EVALUATOR_ENABLED=false` → cascada igual que antes, sin diferencia de latencia
- [ ] `CRAG_EVALUATOR_ENABLED=true` + provider=ollama → early exit, 1 chunk/llamada
- [ ] `CRAG_EVALUATOR_ENABLED=true` + provider=claude → batch 3 chunks/llamada, confianza >= 0.5
- [ ] `CRAG_EVALUATOR_ENABLED=true` + provider=openai → JSON mode nativo, batch evaluation
- [ ] `CRAG_EVALUATOR_ENABLED=true` + chunk irrelevante → cascada continúa a L2.b
- [ ] `CRAG_EVALUATOR_PROVIDER` puede diferir de `DEFAULT_PROVIDER` (modo híbrido)
- [ ] La ruta original de SurfSense (`/api/v1/chat`) sigue funcionando sin cambios

---

**Anterior:** [F3 — Síntesis Multi-call y Pasaporte Semántico](./F3-sintesis-pasaporte.md)  
**Siguiente:** [F5 — Conectores SurfSense → Pipeline Brain](./F5-conectores-pipeline.md)
